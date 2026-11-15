"""Import a scene configuration file through the scene admin use case: upsert by code.

What it reads: the file the admin page downloads ({schema_version: 1, kind: "scene_config",
scenes: [...]}), or the body of GET /api/scenes. Each scene carries its labels and rules, as the
API returns them; a file may be edited by hand, or cut down to what should change.

    scene   matched by its code              created, or name / description / include_all /
                                             outputs_heating / is_active updated where they differ
    label   matched by its code in a scene   created, or name / sort_order / is_active updated
    rule    matched by its target in a scene (the category's code, or the cuisine): created, or
                                             its label changed (`label: null` clears it)

A field, or a whole `labels` / `rules` list, that the file leaves out is not touched. What is stored
but not listed is left alone, unless prune is set: then, in the scenes the file lists, the rules
(when the scene has a `rules` list) and the labels (when it has a `labels` list) that the file does
not list are deleted. A scene is never deleted here (that drops its memberships): scenes the file
does not list are only reported (not_in_file).

Ids in the file are not read, so a file restores a configuration into another database: a rule
names its category and its label by code; the category must already be stored (import the category
tree first). A code is the identity, so it cannot be changed through a file.

Within a scene the order is: the scene, its labels, its rules, then (prune) rules and labels to
delete. Each write is its own transaction (the catalog store's). sync_required says whether stored
images follow the new configuration only after POST /api/scenes/sync, as for single writes; in a
dry run it is what the writes would answer.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core import errors as err
from app.core.rules.scene_fields import (
    LABEL_FIELDS,
    check_default_scene_change,
    normalize_label_fields,
    normalize_rule_fields,
    normalize_scene_code,
    normalize_scene_fields,
    rule_write_needs_sync,
    scene_flags_need_sync,
)
from app.services.retrieval.import_report import ImportReport, change, check_envelope, ref_code, text_field
from app.services.retrieval.manage_categories import ManageCategoriesUseCase
from app.services.retrieval.manage_scenes import ManageScenesUseCase

logger = logging.getLogger("retrieval.import_scenes")

KIND = "scene_config"
MAX_SCENES = 200
MAX_ITEMS_PER_SCENE = 5000  # labels, or rules, of one scene
_SCENE_VALUE_KEYS = ("name", "description", "include_all", "outputs_heating", "is_active")
_LABEL_VALUE_KEYS = ("name", "sort_order", "is_active")

# A rule's target: ("category", the category's lower-case code) or ("cuisine", the cuisine)
Target = tuple[str, str]


@dataclass
class _Scene:
    """One scene as the import sees it now: what is stored, plus (in a dry run) what would be."""

    code: str
    scene_id: int | None  # None: about to be created (dry run)
    is_active: bool
    include_all: bool
    labels: dict[str, dict[str, Any]]  # code -> {id | None, name, sort_order, is_active}
    rules: dict[Target, dict[str, Any]]  # target -> {id | None, label: code | None}

    @property
    def flags(self) -> dict[str, bool]:
        return {"is_active": self.is_active, "include_all": self.include_all}


def _scene_of(stored: Mapping[str, Any]) -> _Scene:
    rules: dict[Target, dict[str, Any]] = {}
    for rule in stored.get("rules") or []:
        category, label = rule.get("category"), rule.get("label")
        target: Target = (
            ("category", str(category["code"]).lower()) if category is not None else ("cuisine", str(rule["cuisine"]))
        )
        rules[target] = {"id": int(rule["id"]), "label": None if label is None else str(label["code"])}
    return _Scene(
        code=str(stored["code"]),
        scene_id=int(stored["id"]),
        is_active=bool(stored["is_active"]),
        include_all=bool(stored["include_all"]),
        labels={str(label["code"]): dict(label) for label in stored.get("labels") or []},
        rules=rules,
    )


@dataclass
class _Run:
    report: ImportReport
    dry_run: bool
    prune: bool
    total: int = 0  # scenes, labels and rules the file lists
    sync_required: bool = False
    # lower-case code -> stored category; read on the first category rule
    category_index: dict[str, Mapping[str, Any]] | None = field(default=None, repr=False)


def _target_text(target: Target) -> str:
    return f"{target[0]}:{target[1]}"


class ImportScenesUseCase:
    def __init__(self, scenes: ManageScenesUseCase, categories: ManageCategoriesUseCase) -> None:
        self._scenes = scenes
        self._categories = categories

    def execute(self, document: Any, *, dry_run: bool = True, prune: bool = False) -> dict[str, Any]:
        """{dry_run, prune, summary, rows, rows_truncated, not_in_file, sync_required}.

        A file that is not a scene configuration at all raises VALIDATION_ERROR; a database failure
        while writing raises (rows written before it stay; import the file again to continue).
        """
        items = check_envelope(document, kind=KIND, items_key="scenes")
        if len(items) > MAX_SCENES:
            raise err.validation_error(f"the file lists more than {MAX_SCENES} scenes")
        stored = {str(scene["code"]): scene for scene in self._scenes.list()["scenes"]}
        run = _Run(report=ImportReport(dry_run=dry_run), dry_run=dry_run, prune=prune)
        listed: set[str] = set()
        for index, raw in enumerate(items):
            run.total += 1
            self._import_scene(run, raw, f"scenes[{index}]", stored, listed)
        leftovers = [
            {"id": scene["id"], "code": code, "name": scene.get("name")}
            for code, scene in stored.items()
            if code not in listed
        ]
        body = run.report.body(total=run.total, not_in_file=leftovers)
        body.update(prune=prune, sync_required=run.sync_required)
        return body

    # -- scenes ---------------------------------------------------------------------------

    def _import_scene(
        self, run: _Run, raw: Any, where: str, stored: Mapping[str, Mapping[str, Any]], listed: set[str]
    ) -> None:
        report = run.report
        if not isinstance(raw, Mapping):
            report.fail("scene", where, "a scene must be an object", where=where)
            return
        context: dict[str, Any] = {"where": where, "name": text_field(raw, "name")}
        code = text_field(raw, "code")
        current = stored.get(code) if code is not None else None
        if current is None:
            try:  # a scene to create: its code has to be a valid new one
                code = normalize_scene_code(raw.get("code"))
            except err.AppError as exc:
                report.fail("scene", code or where, exc, **context)
                return
            current = stored.get(code)
        assert code is not None
        context["scene"] = code
        if code in listed:
            report.fail("scene", code, f"scene {code!r} is listed twice in the file", **context)
            return
        listed.add(code)

        scene = self._create_scene(run, raw, code, context) if current is None else self._update_scene(run, raw, current, context)
        if scene is None:
            return  # not created: nothing to hang its labels and rules on
        listed_labels = self._import_labels(run, raw, scene, where)
        listed_rules = self._import_rules(run, raw, scene, where)
        if run.prune:
            self._prune(run, scene, where, listed_labels, listed_rules)

    def _create_scene(self, run: _Run, raw: Mapping[str, Any], code: str, context: dict[str, Any]) -> _Scene | None:
        report = run.report
        try:
            new = normalize_scene_fields(
                {"code": code, **{key: raw[key] for key in _SCENE_VALUE_KEYS if key in raw}}, partial=False
            )
        except err.AppError as exc:
            report.fail("scene", code, exc, **context)
            return None
        sync_required = scene_flags_need_sync(None, new)
        if run.dry_run:
            scene = _Scene(
                code=code, scene_id=None, is_active=new["is_active"], include_all=new["include_all"], labels={}, rules={}
            )
        else:
            try:
                result = self._scenes.create(new)
            except err.AppError as exc:
                if err.is_internal(exc):
                    raise
                report.fail("scene", code, exc, **context)
                return None
            scene, sync_required = _scene_of(result["scene"]), bool(result["sync_required"])
            logger.info("scene import: created scene %s id=%s", code, scene.scene_id)
        run.sync_required = run.sync_required or sync_required
        changes = [change("name", None, new["name"])]
        changes += [change(key, None, new[key]) for key in ("include_all", "outputs_heating") if new[key]]
        if not new["is_active"]:
            changes.append(change("is_active", None, False))
        report.add("scene", code, "create", changes=changes, **{**context, "name": new["name"]})
        return scene

    def _update_scene(self, run: _Run, raw: Mapping[str, Any], current: Mapping[str, Any], context: dict[str, Any]) -> _Scene:
        """The scene after the update. A refused update is reported and the scene stays as it is:
        it exists, so its labels and rules are imported all the same."""
        report = run.report
        scene = _scene_of(current)
        context = {**context, "name": context["name"] or current.get("name")}
        fields: dict[str, Any] = {}
        changes: list[dict[str, Any]] = []
        try:
            for key in _SCENE_VALUE_KEYS:
                if key not in raw:
                    continue
                value = normalize_scene_fields({key: raw[key]}, partial=True)[key]
                if value != current.get(key):
                    fields[key] = value
                    changes.append(change(key, current.get(key), value))
            refusal = check_default_scene_change(scene.code, fields)
            if refusal:
                raise err.scene_protected(refusal)
        except err.AppError as exc:
            report.fail("scene", scene.code, exc, **context)
            return scene
        if not fields:
            report.unchanged()
            return scene
        after = {**current, **fields}
        sync_required = scene_flags_need_sync(current, after)
        if run.dry_run:
            scene.is_active, scene.include_all = bool(after["is_active"]), bool(after["include_all"])
        else:
            try:
                result = self._scenes.update(int(current["id"]), fields)
            except err.AppError as exc:
                if err.is_internal(exc):
                    raise
                report.fail("scene", scene.code, exc, **context)
                return scene
            scene, sync_required = _scene_of(result["scene"]), bool(result["sync_required"])
            logger.info("scene import: updated scene %s fields=%s", scene.code, ",".join(sorted(fields)))
        run.sync_required = run.sync_required or sync_required
        report.add("scene", scene.code, "update", changes=changes, **context)
        return scene

    # -- labels ---------------------------------------------------------------------------

    def _import_labels(self, run: _Run, raw: Mapping[str, Any], scene: _Scene, where: str) -> set[str] | None:
        """The label codes the file lists for the scene; None when it has no `labels` list (the
        scene's labels are then not touched, and not pruned)."""
        report = run.report
        if "labels" not in raw:
            return None
        items = raw["labels"]
        if not isinstance(items, list) or len(items) > MAX_ITEMS_PER_SCENE:
            report.fail(
                "label", f"{scene.code}/labels", f"labels must be an array of at most {MAX_ITEMS_PER_SCENE} labels",
                scene=scene.code, where=f"{where}.labels",
            )
            return None
        listed: set[str] = set()
        for index, item in enumerate(items):
            run.total += 1
            item_where = f"{where}.labels[{index}]"
            if not isinstance(item, Mapping):
                report.fail("label", f"{scene.code}/{item_where}", "a label must be an object", scene=scene.code, where=item_where)
                continue
            code = text_field(item, "code")
            key = f"{scene.code}/{code or item_where}"
            context = {"scene": scene.code, "where": item_where, "name": text_field(item, "name")}
            current = scene.labels.get(code) if code is not None else None
            try:
                if code is not None and code in listed:
                    raise err.validation_error(f"label {code!r} is listed twice in this scene")
                if current is None:
                    new = normalize_label_fields({name: item[name] for name in LABEL_FIELDS if name in item}, partial=False)
                    code = str(new["code"])
                    key = f"{scene.code}/{code}"
                    fields: dict[str, Any] = {}
                    changes = [change("name", None, new["name"])]
                else:
                    fields, changes = {}, []
                    for name in _LABEL_VALUE_KEYS:
                        if name not in item:
                            continue
                        value = normalize_label_fields({name: item[name]}, partial=True)[name]
                        if value != current.get(name):
                            fields[name] = value
                            changes.append(change(name, current.get(name), value))
            except err.AppError as exc:
                if code is not None:
                    listed.add(code)  # it is listed: never pruned because its row is wrong
                report.fail("label", key, exc, **context)
                continue
            assert code is not None
            listed.add(code)

            if current is None:
                if run.dry_run:
                    scene.labels[code] = {"id": None, **new}
                else:
                    try:
                        result = self._scenes.create_label(self._scene_id(scene), new)
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("label", key, exc, **context)
                        continue
                    self._refresh(scene, result["scene"])
                report.add("label", key, "create", changes=changes, **{**context, "name": new["name"]})
            elif fields:
                context["name"] = context["name"] or current.get("name")
                if run.dry_run:
                    current.update(fields)
                else:
                    try:
                        result = self._scenes.update_label(self._scene_id(scene), int(current["id"]), fields)
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("label", key, exc, **context)
                        continue
                    self._refresh(scene, result["scene"])
                report.add("label", key, "update", changes=changes, **context)
            else:
                report.unchanged()
        return listed

    # -- rules ----------------------------------------------------------------------------

    def _import_rules(self, run: _Run, raw: Mapping[str, Any], scene: _Scene, where: str) -> set[Target] | None:
        """The rule targets the file lists for the scene; None when it has no `rules` list."""
        report = run.report
        if "rules" not in raw:
            return None
        items = raw["rules"]
        if not isinstance(items, list) or len(items) > MAX_ITEMS_PER_SCENE:
            report.fail(
                "rule", f"{scene.code}/rules", f"rules must be an array of at most {MAX_ITEMS_PER_SCENE} rules",
                scene=scene.code, where=f"{where}.rules",
            )
            return None
        listed: set[Target] = set()
        for index, item in enumerate(items):
            run.total += 1
            item_where = f"{where}.rules[{index}]"
            context: dict[str, Any] = {"scene": scene.code, "where": item_where}
            if not isinstance(item, Mapping):
                report.fail("rule", f"{scene.code}/{item_where}", "a rule must be an object", **context)
                continue
            key = f"{scene.code}/{item_where}"
            target: Target | None = None
            try:
                target, category = self._rule_target(run, item)
                key = f"{scene.code}/{_target_text(target)}"
                if category is not None:
                    context["name"] = category.get("name")
                if target in listed:
                    raise err.validation_error(f"the rule for {_target_text(target)} is listed twice in this scene")
                label = ref_code(item.get("label"), "label")
                if label is not None:
                    if target[0] != "category":
                        raise err.validation_error("only a category rule can map to a label")
                    if label not in scene.labels:
                        raise err.validation_error(
                            f"label {label!r} is not a label of scene {scene.code!r}; list it under labels"
                        )
            except err.AppError as exc:
                if target is not None:
                    listed.add(target)  # it is listed: never pruned because its row is wrong
                report.fail("rule", key, exc, **context)
                continue
            assert target is not None
            listed.add(target)

            current = scene.rules.get(target)
            if current is None:
                sync_required = rule_write_needs_sync(scene.flags)
                if run.dry_run:
                    scene.rules[target] = {"id": None, "label": label}
                else:
                    fields: dict[str, Any] = (
                        {"category_id": int(category["id"])} if category is not None else {"cuisine": target[1]}
                    )
                    fields["label_id"] = None if label is None else int(scene.labels[label]["id"])
                    try:
                        result = self._scenes.create_rule(self._scene_id(scene), fields)
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("rule", key, exc, **context)
                        continue
                    sync_required = bool(result["sync_required"])
                    self._refresh(scene, result["scene"])
                run.sync_required = run.sync_required or sync_required
                report.add("rule", key, "create", changes=[change("label", None, label)] if label else [], **context)
            elif "label" in item and label != current["label"]:
                before = current["label"]
                if run.dry_run:
                    current["label"] = label
                else:
                    label_id = None if label is None else int(scene.labels[label]["id"])
                    try:
                        result = self._scenes.update_rule(self._scene_id(scene), int(current["id"]), {"label_id": label_id})
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("rule", key, exc, **context)
                        continue
                    run.sync_required = run.sync_required or bool(result["sync_required"])
                    self._refresh(scene, result["scene"])
                report.add("rule", key, "update", changes=[change("label", before, label)], **context)
            else:
                report.unchanged()
        return listed

    def _rule_target(self, run: _Run, item: Mapping[str, Any]) -> tuple[Target, Mapping[str, Any] | None]:
        """(the target, the stored category for a category rule)."""
        category_ref, cuisine = item.get("category"), item.get("cuisine")
        if (category_ref is None) == (cuisine is None):
            raise err.validation_error("a rule needs exactly one of category and cuisine")
        if category_ref is None:
            return ("cuisine", normalize_rule_fields({"cuisine": cuisine}, partial=False)["cuisine"]), None
        code = ref_code(category_ref, "category")
        assert code is not None
        if run.category_index is None:  # read once, and only when a file has category rules
            run.category_index = {
                str(node["code"]).lower(): node for node in self._categories.tree()["categories"]
            }
        category = run.category_index.get(code.lower())
        if category is None:
            raise err.validation_error(f"category {code!r} is not stored; import the category tree first")
        return ("category", code.lower()), category

    # -- prune ----------------------------------------------------------------------------

    def _prune(
        self, run: _Run, scene: _Scene, where: str, listed_labels: set[str] | None, listed_rules: set[Target] | None
    ) -> None:
        """Delete what the scene has stored but its lists in the file do not name: rules first,
        then labels (a label a remaining rule still maps to is refused, as on DELETE)."""
        report = run.report
        if listed_rules is not None:
            for target, rule in list(scene.rules.items()):
                if target in listed_rules or rule["id"] is None:
                    continue
                key = f"{scene.code}/{_target_text(target)}"
                context = {"scene": scene.code, "where": f"{where}.rules"}
                sync_required = rule_write_needs_sync(scene.flags)
                if run.dry_run:
                    del scene.rules[target]
                else:
                    try:
                        result = self._scenes.delete_rule(self._scene_id(scene), int(rule["id"]))
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("rule", key, exc, **context)
                        continue
                    sync_required = bool(result["sync_required"])
                    self._refresh(scene, result["scene"])
                run.sync_required = run.sync_required or sync_required
                report.add("rule", key, "delete", **context)
        if listed_labels is not None:
            for code, label in list(scene.labels.items()):
                if code in listed_labels or label.get("id") is None:
                    continue
                key = f"{scene.code}/{code}"
                context = {"scene": scene.code, "where": f"{where}.labels", "name": label.get("name")}
                if any(rule["label"] == code for rule in scene.rules.values()):
                    report.fail(
                        "label", key,
                        err.scene_in_use(f"label {code!r} is still used by a rule; list it in the file, or change the rule"),
                        **context,
                    )
                    continue
                if run.dry_run:
                    del scene.labels[code]
                else:
                    try:
                        result = self._scenes.delete_label(self._scene_id(scene), int(label["id"]))
                    except err.AppError as exc:
                        if err.is_internal(exc):
                            raise
                        report.fail("label", key, exc, **context)
                        continue
                    self._refresh(scene, result["scene"])
                report.add("label", key, "delete", **context)

    # -- helpers --------------------------------------------------------------------------

    @staticmethod
    def _scene_id(scene: _Scene) -> int:
        if scene.scene_id is None:  # only a dry run has scenes without an id, and it does not write
            raise err.internal_error(f"scene {scene.code!r} has no id")
        return scene.scene_id

    @staticmethod
    def _refresh(scene: _Scene, stored: Mapping[str, Any]) -> None:
        """Take over what a write answered (the whole scene), so later rows see the new ids."""
        fresh = _scene_of(stored)
        scene.scene_id = fresh.scene_id
        scene.is_active, scene.include_all = fresh.is_active, fresh.include_all
        scene.labels, scene.rules = fresh.labels, fresh.rules
