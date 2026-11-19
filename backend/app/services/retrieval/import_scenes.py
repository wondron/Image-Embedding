"""通过场景管理用例导入场景配置文件：按 code 新增或更新。

读取的内容：管理页下载的文件（{schema_version: 1, kind: "scene_config", scenes: [...]}），
或者 GET /api/scenes 的响应体。每个场景带着它的标签和规则，和 API 返回的一样；文件可以
手工编辑，也可以删减到只剩要修改的部分。

    场景   按 code 匹配              创建，或者更新不同的 name / description / include_all /
                                     outputs_heating / is_active
    标签   在场景内按 code 匹配      创建，或者更新 name / sort_order / is_active
    规则   在场景内按对象匹配（类别的 code，或菜系）：创建，或者修改它的标签
                                     （`label: null` 清空标签）

文件里没写的字段，或者整个 `labels` / `rules` 列表，都不动。已存但没有列出的保持不变，
除非设置了 prune：这时在文件列出的场景里，文件没有列出的规则（场景有 `rules` 列表时）
和标签（场景有 `labels` 列表时）会被删除。这里从不删除场景（那会丢掉它的归属）：
文件没有列出的场景只在报告里列出（not_in_file）。

文件里的 id 不读取，所以文件可以把配置恢复到另一个数据库：规则用 code 指明它的类别和
标签；类别必须已经存在（先导入类别树）。code 就是身份，所以不能通过文件修改。

在一个场景内的顺序是：场景、它的标签、它的规则，然后（prune 时）要删除的规则和标签。
每次写入是一个单独的事务（catalog 存储的事务）。sync_required 说明已存图片是否要在
POST /api/scenes/sync 之后才会跟随新配置，和单次写入一样；dry run 时就是这些写入
会返回的值。
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
MAX_ITEMS_PER_SCENE = 5000  # 一个场景的标签或规则
_SCENE_VALUE_KEYS = ("name", "description", "include_all", "outputs_heating", "is_active")
_LABEL_VALUE_KEYS = ("name", "sort_order", "is_active")

# 规则的对象：("category", 类别的小写 code) 或 ("cuisine", 菜系)
Target = tuple[str, str]


@dataclass
class _Scene:
    """导入此刻看到的一个场景：已存的内容，加上（dry run 时）将要存的内容。"""

    code: str
    scene_id: int | None  # None：即将创建（dry run）
    is_active: bool
    include_all: bool
    labels: dict[str, dict[str, Any]]  # code -> {id | None, name, sort_order, is_active}
    rules: dict[Target, dict[str, Any]]  # 对象 -> {id | None, label: code | None}

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
    total: int = 0  # 文件列出的场景、标签和规则
    sync_required: bool = False
    # 小写 code -> 已存类别；在第一条类别规则时读取
    category_index: dict[str, Mapping[str, Any]] | None = field(default=None, repr=False)


def _target_text(target: Target) -> str:
    return f"{target[0]}:{target[1]}"


class ImportScenesUseCase:
    def __init__(self, scenes: ManageScenesUseCase, categories: ManageCategoriesUseCase) -> None:
        self._scenes = scenes
        self._categories = categories

    def execute(self, document: Any, *, dry_run: bool = True, prune: bool = False) -> dict[str, Any]:
        """{dry_run, prune, summary, rows, rows_truncated, not_in_file, sync_required}。

        完全不是场景配置的文件抛出 VALIDATION_ERROR；写入时数据库失败会抛出（之前写入的行
        保留；再导入一次同一个文件即可继续）。
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

    # -- 场景 -------------------------------------------------------------------------------

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
            try:  # 要创建的场景：它的 code 必须是合法的新 code
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
            return  # 没有创建：它的标签和规则没有地方挂
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
        """更新后的场景。被拒绝的更新会写进报告，场景保持原样：场景存在，
        所以它的标签和规则照样导入。
        """
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

    # -- 标签 -------------------------------------------------------------------------------

    def _import_labels(self, run: _Run, raw: Mapping[str, Any], scene: _Scene, where: str) -> set[str] | None:
        """文件为这个场景列出的标签 code；场景没有 `labels` 列表时为 None（这时场景的标签
        不动，也不清理）。
        """
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
                    listed.add(code)  # 它被列出了：不会因为它的行有问题而被清理
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

    # -- 规则 -------------------------------------------------------------------------------

    def _import_rules(self, run: _Run, raw: Mapping[str, Any], scene: _Scene, where: str) -> set[Target] | None:
        """文件为这个场景列出的规则对象；场景没有 `rules` 列表时为 None。"""
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
                    listed.add(target)  # 它被列出了：不会因为它的行有问题而被清理
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
        """（对象, 类别规则对应的已存类别）。"""
        category_ref, cuisine = item.get("category"), item.get("cuisine")
        if (category_ref is None) == (cuisine is None):
            raise err.validation_error("a rule needs exactly one of category and cuisine")
        if category_ref is None:
            return ("cuisine", normalize_rule_fields({"cuisine": cuisine}, partial=False)["cuisine"]), None
        code = ref_code(category_ref, "category")
        assert code is not None
        if run.category_index is None:  # 只读一次，而且只在文件里有类别规则时读
            run.category_index = {
                str(node["code"]).lower(): node for node in self._categories.tree()["categories"]
            }
        category = run.category_index.get(code.lower())
        if category is None:
            raise err.validation_error(f"category {code!r} is not stored; import the category tree first")
        return ("category", code.lower()), category

    # -- 清理 -------------------------------------------------------------------------------

    def _prune(
        self, run: _Run, scene: _Scene, where: str, listed_labels: set[str] | None, listed_rules: set[Target] | None
    ) -> None:
        """删除场景已存、但文件里它的列表没有列出的内容：先删规则，再删标签（剩下的规则
        仍然映射到的标签会被拒绝，和 DELETE 一样）。
        """
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

    # -- 辅助函数 ---------------------------------------------------------------------------

    @staticmethod
    def _scene_id(scene: _Scene) -> int:
        if scene.scene_id is None:  # 只有 dry run 才有没有 id 的场景，而 dry run 不写入
            raise err.internal_error(f"scene {scene.code!r} has no id")
        return scene.scene_id

    @staticmethod
    def _refresh(scene: _Scene, stored: Mapping[str, Any]) -> None:
        """接收一次写入返回的内容（整个场景），这样后面的行能看到新的 id。"""
        fresh = _scene_of(stored)
        scene.scene_id = fresh.scene_id
        scene.is_active, scene.include_all = fresh.is_active, fresh.include_all
        scene.labels, scene.rules = fresh.labels, fresh.rules
