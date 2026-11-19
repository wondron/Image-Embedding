"""通过类别管理用例导入类别树文件：按 code 新增或更新。

读取的内容：管理页下载的文件（{schema_version: 1, categories: [通过 `children` 嵌套的节点]}），
或者 GET /api/categories 的响应体（{categories: [带 id / parent_id 的扁平节点]}）。
两者都可以手工编辑，也可以删减到只剩要修改的节点。

    code 没有存过的节点          创建在它所嵌套的节点下
    code 已经存过的节点          比较文件里给出的字段；不同的字段会更新
                                 （names、cuisines、heating_group、sort_order、is_active）；
                                 文件里没写的字段不动
    文件里没有列出的已存节点     不动，并在报告里列出（not_in_file）

类别按 code 识别（不区分大小写），所以同一个文件可以把类别树恢复到 id 不同的另一个
数据库；节点的 id、path、name、各种计数和时间戳都不读取。
这里不移动、不改名、不删除任何东西：移动会改写节点下所有东西的 code，所以文件把已存
节点嵌套到另一个父节点下时会被拒绝，节点保持原位（请在页面上移动）。节点的父节点按以下
顺序确定：它所嵌套的节点；它的 `parent_code`（根节点为 null）；`id` 等于它的 `parent_id`
的文件节点；否则它是根节点。

每个节点在自己的事务里写入（catalog 存储的事务），父节点先于子节点。修改菜系会像 PATCH
一样重算该节点下图片的场景：场景变化的 id 会返回，由 HTTP 层推送（scene_membership）。
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
    """文件里的一个节点。"""

    where: str  # 它在文件里的位置，例如 categories[0].children[2]
    raw: Mapping[str, Any]
    nested_in: "_FileNode | None" = None
    code: str | None = None  # None：不可用，problem 说明原因
    parent_code: str | None = None  # 小写；根节点为 None
    problem: str | None = None
    level: int = 0  # 它上面的文件祖先节点数：节点逐层导入
    # 导入过程中：节点存好后（或在 dry run 里会存好时）为 "ok"；否则为 "failed"
    state: str = "pending"
    stored_id: int | None = None  # dry run 里即将创建的节点为 None

    @property
    def key(self) -> str:
        return self.code or self.where


@dataclass
class _Run:
    report: ImportReport
    dry_run: bool
    stored: dict[str, Mapping[str, Any]]  # 小写 code -> 已存节点
    stored_by_id: dict[int, Mapping[str, Any]]
    groups: dict[str, Mapping[str, Any]]  # 加热组 code -> {id, code, name}
    by_code: dict[str, _FileNode]
    roots: int
    recomputed: bool = False
    images: int = 0
    changed_ids: list[Any] = field(default_factory=list)


def _read_nodes(categories: list[Any]) -> list[_FileNode]:
    """文件里的每个节点，父节点排在嵌套在它下面的节点之前。"""
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
    """设置每个节点的 parent_code（顺序见模块文档字符串）。"""
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
    """小写 code -> 列出它的节点；重复列出的 code 是那个节点的问题。"""
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
    """每个节点上面有多少个文件节点，这样不管文件里的顺序如何，父节点都先导入
    （扁平列表里的父节点可能出现在后面）。
    """
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
    """名称作为集合（不保存它们的顺序）；值不是 {lang, name, kind} 列表时为 None，
    这时交给规范化函数报错。
    """
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
    """文件节点的 heating_group（null、一个 code，或 {code, ...}）-> heating_groups.id。"""
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
    """已存节点的（修改内容, 报告行）：只包含文件给出且值不同的字段。和已存值相同的值
    不再校验，所以早于某条规则的节点（没有 zh 主名称）往返一次也保持不变。
    """
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
    """没有存过的节点的（不含 parent_id 的创建字段, 报告行）。
    只有 `name` 也足够作为名称：它会成为 zh 主名称。
    """
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
    new.pop("parent_id", None)  # 在写入节点时解析
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
        """{dry_run, summary, rows, rows_truncated, not_in_file, scene_membership}。

        dry_run：什么都不写；报告说明会发生什么。scene_membership：None，或者菜系修改重算了
        已存图片时为 {images, changed_ids}（dry run 时没有）。
        完全不是类别树的文件抛出 VALIDATION_ERROR；写入时数据库失败会抛出（之前写入的行
        保留；再导入一次同一个文件即可继续）。
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
        for node in sorted(nodes, key=lambda entry: entry.level):  # 稳定排序：同一层内保持文件里的顺序
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

        # 它的父节点：这个文件里的节点（先于它导入），否则是一个已存类别
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
        # 不管怎样都已存好：嵌套在它下面的节点都会导入，不管这个节点结果如何
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
