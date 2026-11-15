"""Import a category tree file through the category admin use case: upsert by code.

What it reads: the file the admin page downloads ({schema_version: 1, categories: [nodes nested by
`children`]}), or the body of GET /api/categories ({categories: [flat nodes with id / parent_id]}).
Either may be edited by hand, or cut down to the nodes to change.

    a node whose code is not stored       created under the node it is nested in
    a node whose code is stored           the fields the file gives are compared; those that differ
                                          are updated (names, cuisines, heating_group, sort_order,
                                          is_active); a field the file leaves out is not touched
    a stored node the file does not list  left alone, and reported (not_in_file)

A category is identified by its code (ignoring case), so the same file restores a tree into another
database, whose ids differ; id, path, name, the counts and the timestamps of a node are not read.
Nothing is moved, renamed or deleted here: a move rewrites the codes of everything below the node,
so a stored node the file nests under another parent is refused and stays where it is (move it on
the page). A node's parent is, in this order: the node it is nested in; its `parent_code` (null
for a root); the file node whose `id` is its `parent_id`; otherwise it is a root.

Each node is written in its own transaction (the catalog store's), parents before children. A
cuisines change recomputes the scenes of the images below the node, as on PATCH: the ids whose
scenes changed are returned for the HTTP layer to push (scene_membership).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core import errors as err
from app.core.category_rules import CATEGORY_DISPLAY_LANG, CATEGORY_MAX_ROOTS
from app.core.enums import NameKind
from app.core.rules.category_fields import (
    check_root_code,
    normalize_category_code,
    normalize_category_fields,
    normalize_category_names,
)
from app.services.retrieval.import_report import ImportReport, change, check_envelope, ref_code, text_field
from app.services.retrieval.manage_categories import ManageCategoriesUseCase

logger = logging.getLogger("retrieval.import_categories")

KIND = "category_tree"
MAX_NODES = 5000
MAX_DEPTH = 32
_ROW = "category"


@dataclass
class _FileNode:
    """One node of the file."""

    where: str  # its place in the file, e.g. categories[0].children[2]
    raw: Mapping[str, Any]
    nested_in: "_FileNode | None" = None
    code: str | None = None  # None: unusable, problem says why
    parent_code: str | None = None  # lower case; None for a root
    problem: str | None = None
    level: int = 0  # file ancestors above it: nodes are imported level by level
    # While importing: "ok" once the node is stored (or, in a dry run, would be); "failed" otherwise
    state: str = "pending"
    stored_id: int | None = None  # None in a dry run for a node about to be created

    @property
    def key(self) -> str:
        return self.code or self.where


@dataclass
class _Run:
    report: ImportReport
    dry_run: bool
    stored: dict[str, Mapping[str, Any]]  # lower-case code -> stored node
    stored_by_id: dict[int, Mapping[str, Any]]
    groups: dict[str, Mapping[str, Any]]  # heating group code -> {id, code, name}
    by_code: dict[str, _FileNode]
    roots: int
    recomputed: bool = False
    images: int = 0
    changed_ids: list[Any] = field(default_factory=list)


def _read_nodes(categories: list[Any]) -> list[_FileNode]:
    """Every node of the file, a parent before the nodes nested in it."""
    nodes: list[_FileNode] = []
    pending: list[tuple[Any, str, _FileNode | None, int]] = [
        (raw, f"categories[{index}]", None, 0) for index, raw in reversed(list(enumerate(categories)))
    ]
    while pending:
        raw, where, nested_in, depth = pending.pop()
        if len(nodes) >= MAX_NODES:
            raise err.validation_error(f"the file lists more than {MAX_NODES} categories")
        if depth > MAX_DEPTH:
            raise err.validation_error(f"{where}: nested deeper than {MAX_DEPTH} levels")
        if not isinstance(raw, Mapping):
            nodes.append(_FileNode(where=where, raw={}, nested_in=nested_in, problem="a category must be an object"))
            continue
        node = _FileNode(where=where, raw=raw, nested_in=nested_in)
        try:
            node.code = normalize_category_code(raw.get("code"))
        except err.AppError as exc:
            node.problem = exc.detail
        nodes.append(node)
        children = raw.get("children")
        if children is None:
            continue
        if not isinstance(children, list):
            node.problem = node.problem or "children must be an array"
            continue
        for index, child in reversed(list(enumerate(children))):
            pending.append((child, f"{where}.children[{index}]", node, depth + 1))
    return nodes


def _link_parents(nodes: list[_FileNode]) -> None:
    """Set each node's parent_code (see the module docstring for the order)."""
    by_file_id: dict[int, _FileNode] = {}
    for node in nodes:
        file_id = node.raw.get("id")
        if node.code is not None and isinstance(file_id, int) and not isinstance(file_id, bool):
            by_file_id.setdefault(file_id, node)
    for node in nodes:
        if node.problem is not None or node.code is None:
            continue
        raw = node.raw
        if node.nested_in is not None:
            if node.nested_in.code is None:
                node.problem = "the node it is nested in cannot be imported"
                continue
            node.parent_code = node.nested_in.code.lower()
            explicit = raw.get("parent_code")
            if isinstance(explicit, str) and explicit.strip().lower() != node.parent_code:
                node.problem = (
                    f"parent_code {explicit!r} contradicts the node it is nested in ({node.nested_in.code!r})"
                )
            continue
        if "parent_code" in raw:
            if raw["parent_code"] is not None:
                try:
                    node.parent_code = normalize_category_code(raw["parent_code"]).lower()
                except err.AppError as exc:
                    node.problem = f"parent_code: {exc.detail}"
            continue
        parent_id = raw.get("parent_id")
        if parent_id is None:
            continue
        parent = by_file_id.get(parent_id) if isinstance(parent_id, int) and not isinstance(parent_id, bool) else None
        if parent is None or parent is node or parent.code is None:
            node.problem = (
                f"parent_id {parent_id!r} is not the id of a node in this file; "
                "nest the node under its parent, or give parent_code"
            )
        else:
            node.parent_code = parent.code.lower()


def _index_codes(nodes: list[_FileNode]) -> dict[str, _FileNode]:
    """lower-case code -> the node that lists it; a code listed again is that node's problem."""
    by_code: dict[str, _FileNode] = {}
    for node in nodes:
        if node.code is None:
            continue
        key = node.code.lower()
        first = by_code.get(key)
        if first is None:
            by_code[key] = node
        elif node.problem is None:
            node.problem = f"code {node.code!r} is listed twice in the file (first at {first.where})"
    return by_code


def _set_levels(nodes: list[_FileNode], by_code: Mapping[str, _FileNode]) -> None:
    """How many file nodes sit above each node, so parents are imported first whatever the file's
    order (a flat list may name a parent that comes later)."""
    for node in nodes:
        if node.problem is not None or node.code is None:
            continue
        seen = {node.code.lower()}
        cursor = node.parent_code
        while cursor is not None and cursor in by_code:
            if cursor in seen:
                node.problem = "its parents form a cycle in the file"
                break
            seen.add(cursor)
            node.level += 1
            if node.level > MAX_DEPTH:
                node.problem = f"nested deeper than {MAX_DEPTH} levels"
                break
            cursor = by_code[cursor].parent_code


def _name_keys(value: Any) -> frozenset[tuple[str, str, str]] | None:
    """Names as a set (their order is not stored); None when the value is not a list of
    {lang, name, kind}, which then goes to the normalizer for its error."""
    if not isinstance(value, (list, tuple)):
        return None
    keys: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        lang, name, kind = item.get("lang"), item.get("name"), item.get("kind")
        if not (isinstance(lang, str) and isinstance(name, str) and isinstance(kind, str)):
            return None
        keys.add((lang.strip(), name.strip(), kind.strip()))
    return frozenset(keys)


def _names_text(names: Any) -> str | None:
    """披萨[zh], Pizza[en], 薄饼[zh·alias]"""
    parts = []
    for entry in names or []:
        suffix = "" if entry.get("kind") == NameKind.PRIMARY.value else "·alias"
        parts.append(f"{entry.get('name')}[{entry.get('lang')}{suffix}]")
    return ", ".join(parts) or None


def _group_id(value: Any, groups: Mapping[str, Mapping[str, Any]]) -> int | None:
    """heating_group of a file node (null, a code, or {code, ...}) -> heating_groups.id."""
    code = ref_code(value, "heating_group")
    if code is None:
        return None
    group = groups.get(code)
    if group is None:
        raise err.validation_error(
            f"heating_group {code!r} does not exist; known codes: {', '.join(sorted(groups)) or 'none'}"
        )
    return int(group["id"])


def _group_code(group_id: int | None, groups: Mapping[str, Mapping[str, Any]]) -> str | None:
    if group_id is None:
        return None
    return next((code for code, group in groups.items() if int(group["id"]) == group_id), str(group_id))


def _diff(
    raw: Mapping[str, Any], current: Mapping[str, Any], groups: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the patch, its report lines) for a stored node: only fields the file gives, and only those
    whose value differs. A value equal to what is stored is not validated again, so a node that
    predates a rule (no zh primary name) round-trips unchanged."""
    fields: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    if "names" in raw:
        before = _name_keys(current.get("names"))
        if _name_keys(raw["names"]) != before:
            names = normalize_category_names(raw["names"])
            if _name_keys(names) != before:
                fields["names"] = names
                changes.append(change("names", _names_text(current.get("names")), _names_text(names)))
    if "cuisines" in raw:
        before_cuisines = list(current.get("cuisines") or [])
        if raw["cuisines"] != before_cuisines:
            cuisines = normalize_category_fields({"cuisines": raw["cuisines"]}, partial=True)["cuisines"]
            if cuisines != before_cuisines:
                fields["cuisines"] = cuisines
                changes.append(change("cuisines", before_cuisines, cuisines))
    if "heating_group" in raw:
        group = current.get("heating_group")
        before_id = None if group is None else int(group["id"])
        after_id = _group_id(raw["heating_group"], groups)
        if after_id != before_id:
            fields["heating_group_id"] = after_id
            changes.append(change(
                "heating_group", None if group is None else group.get("code"), _group_code(after_id, groups)
            ))
    for key in ("sort_order", "is_active"):
        if key in raw and raw[key] != current.get(key):
            value = normalize_category_fields({key: raw[key]}, partial=True)[key]
            if value != current.get(key):
                fields[key] = value
                changes.append(change(key, current.get(key), value))
    return fields, changes


def _new_fields(
    node: _FileNode, groups: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the create fields without parent_id, the report lines) for a node that is not stored.
    `name` alone is enough for the names: it becomes the zh primary name."""
    raw = node.raw
    fields: dict[str, Any] = {"code": node.code}
    if "names" in raw:
        fields["names"] = raw["names"]
    elif text_field(raw, "name") is not None:
        fields["names"] = [{"lang": CATEGORY_DISPLAY_LANG, "name": raw["name"], "kind": NameKind.PRIMARY.value}]
    for key in ("cuisines", "sort_order", "is_active"):
        if key in raw:
            fields[key] = raw[key]
    if "heating_group" in raw:
        fields["heating_group_id"] = _group_id(raw["heating_group"], groups)
    new = normalize_category_fields(fields, partial=False)
    new.pop("parent_id", None)  # resolved when the node is written
    changes = [change("names", None, _names_text(new["names"]))]
    if node.parent_code is not None:
        changes.append(change("parent", None, node.parent_code))
    if new["cuisines"]:
        changes.append(change("cuisines", None, new["cuisines"]))
    if new["heating_group_id"] is not None:
        changes.append(change("heating_group", None, _group_code(new["heating_group_id"], groups)))
    if new["sort_order"]:
        changes.append(change("sort_order", None, new["sort_order"]))
    if not new["is_active"]:
        changes.append(change("is_active", None, False))
    return new, changes


class ImportCategoriesUseCase:
    def __init__(self, categories: ManageCategoriesUseCase) -> None:
        self._categories = categories

    def execute(self, document: Any, *, dry_run: bool = True) -> dict[str, Any]:
        """{dry_run, summary, rows, rows_truncated, not_in_file, scene_membership}.

        dry_run: nothing is written; the report says what would happen. scene_membership: None, or
        {images, changed_ids} when a cuisines change recomputed stored images (not in a dry run).
        A file that is not a category tree at all raises VALIDATION_ERROR; a database failure
        while writing raises (rows written before it stay; import the file again to continue).
        """
        nodes = _read_nodes(check_envelope(document, kind=KIND, items_key="categories"))
        _link_parents(nodes)
        by_code = _index_codes(nodes)
        _set_levels(nodes, by_code)

        tree = self._categories.tree()
        stored_nodes = tree["categories"]
        run = _Run(
            report=ImportReport(dry_run=dry_run),
            dry_run=dry_run,
            stored={str(node["code"]).lower(): node for node in stored_nodes},
            stored_by_id={int(node["id"]): node for node in stored_nodes},
            groups={str(group["code"]): group for group in tree["heating_groups"]},
            by_code=by_code,
            roots=sum(1 for node in stored_nodes if node.get("parent_id") is None),
        )
        for node in sorted(nodes, key=lambda entry: entry.level):  # stable: file order within a level
            self._import_node(run, node)

        listed = {node.code.lower() for node in nodes if node.code is not None}
        leftovers = [
            {"id": node["id"], "code": node["code"], "name": node.get("name")}
            for node in stored_nodes
            if str(node["code"]).lower() not in listed
        ]
        body = run.report.body(total=len(nodes), not_in_file=leftovers)
        body["scene_membership"] = (
            {"images": run.images, "changed_ids": run.changed_ids} if run.recomputed else None
        )
        return body

    def _import_node(self, run: _Run, node: _FileNode) -> None:
        report = run.report
        context = {"where": node.where, "name": text_field(node.raw, "name")}
        if node.problem is not None or node.code is None:
            node.state = "failed"
            report.fail(_ROW, node.key, node.problem or "code is required", **context)
            return

        # Its parent: a node of this file (imported before it), else a stored category
        parent_id: int | None = None
        if node.parent_code is not None:
            parent = node.nested_in or run.by_code.get(node.parent_code)
            if parent is not None and parent is not node:
                if parent.state != "ok":
                    node.state = "failed"
                    report.fail(_ROW, node.key, f"its parent {parent.key!r} was not imported", **context)
                    return
                parent_id = parent.stored_id
            else:
                stored_parent = run.stored.get(node.parent_code)
                if stored_parent is None:
                    node.state = "failed"
                    report.fail(
                        _ROW, node.key,
                        f"its parent {node.parent_code!r} is neither in the file nor stored", **context,
                    )
                    return
                parent_id = int(stored_parent["id"])

        current = run.stored.get(node.code.lower())
        if current is not None:
            self._update(run, node, current, context)
        else:
            self._create(run, node, parent_id, context)

    def _update(self, run: _Run, node: _FileNode, current: Mapping[str, Any], context: dict[str, Any]) -> None:
        report = run.report
        # Stored either way: the nodes nested in it are imported whatever happens to this one
        node.state, node.stored_id = "ok", int(current["id"])
        context = {**context, "name": context["name"] or current.get("name")}
        stored_parent = run.stored_by_id.get(int(current["parent_id"])) if current.get("parent_id") is not None else None
        stored_parent_code = None if stored_parent is None else str(stored_parent["code"]).lower()
        if stored_parent_code != node.parent_code:
            report.fail(
                _ROW, node.key,
                f"it is stored under {stored_parent_code or 'the root'!r} but the file puts it under "
                f"{node.parent_code or 'the root'!r}; a file does not move categories (move it on the page)",
                **context,
            )
            return
        try:
            fields, changes = _diff(node.raw, current, run.groups)
        except err.AppError as exc:
            report.fail(_ROW, node.key, exc, **context)
            return
        if not fields:
            report.unchanged()
            return
        if not run.dry_run:
            try:
                result = self._categories.update(int(current["id"]), fields)
            except err.AppError as exc:
                if err.is_internal(exc):
                    raise
                report.fail(_ROW, node.key, exc, **context)
                return
            membership = result.get("scene_membership")
            if membership is not None:
                run.recomputed = True
                run.images += int(membership["images"])
                run.changed_ids.extend(membership["changed_ids"])
            logger.info("category import: updated %s fields=%s", node.code, ",".join(sorted(fields)))
        report.add(_ROW, node.key, "update", changes=changes, **context)

    def _create(self, run: _Run, node: _FileNode, parent_id: int | None, context: dict[str, Any]) -> None:
        report = run.report
        try:
            new, changes = _new_fields(node, run.groups)
            if node.parent_code is None:
                check_root_code(new["code"])
                if run.roots >= CATEGORY_MAX_ROOTS:
                    raise err.validation_error(f"at most {CATEGORY_MAX_ROOTS} root categories are allowed")
        except err.AppError as exc:
            node.state = "failed"
            report.fail(_ROW, node.key, exc, **context)
            return
        if not run.dry_run:
            try:
                created = self._categories.create({**new, "parent_id": parent_id})
            except err.AppError as exc:
                if err.is_internal(exc):
                    raise
                node.state = "failed"
                report.fail(_ROW, node.key, exc, **context)
                return
            node.stored_id = int(created["id"])
            run.stored[str(created["code"]).lower()] = created
            run.stored_by_id[node.stored_id] = created
            logger.info("category import: created %s id=%s parent_id=%s", node.code, node.stored_id, parent_id)
        node.state = "ok"
        if node.parent_code is None:
            run.roots += 1
        display = context["name"] or next(
            (entry["name"] for entry in new["names"]
             if entry["lang"] == CATEGORY_DISPLAY_LANG and entry["kind"] == NameKind.PRIMARY.value),
            None,
        )
        report.add(_ROW, node.key, "create", changes=changes, **{**context, "name": display})
