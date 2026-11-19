"""场景管理页读取和编辑的 scenes / scene_labels / scene_rules。

读取返回所有场景及其标签和规则：场景很少（几十个），规则几百条，页面全部显示。
每次写入是目录锁（app.repositories.catalog_lock）下的一个事务，返回整个场景，这样页面
替换列表里的一项即可，另外返回 sync_required（见下文）。

写入不做的事：重算已存图片属于哪些场景。规则、场景的 is_active / include_all 和类别树都
影响归属；修改通过 POST /api/scenes/sync（services.retrieval.sync_scenes）作用到已存图片
及其向量 payload。每次写入都说明是否需要同步（sync_required，在
core.rules.scene_fields 里决定）：标签、名称、描述和 outputs_heating 在请求时读取，
所以修改后立刻生效。

删除场景会连带删除它的标签、规则和 image_scenes 行（ON DELETE CASCADE）；默认场景
不能删除。仍被规则使用的标签不会删除（SCENE_IN_USE）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core import errors as err
from app.core.category_rules import CATEGORY_DISPLAY_LANG
from app.core.enums import NameKind
from app.core.scene_rules import DEFAULT_SCENE
from app.db.common import _row_to_dict
from app.repositories.catalog_lock import lock_catalog_for_write
from app.core.rules.scene_fields import (
    check_default_scene_change,
    check_rule_label,
    normalize_label_fields,
    normalize_row_id,
    normalize_rule_fields,
    normalize_scene_fields,
    rule_write_needs_sync,
    scene_flags_need_sync,
)

# member_count：生效成员数（停用的场景没有成员；include_all 的场景包含所有图片）
_SCENE_SELECT = (
    "SELECT s.id, s.code, s.name, s.description, s.include_all, s.outputs_heating, s.is_active, s.created_at, "
    "CASE WHEN NOT s.is_active THEN 0 "
    "     WHEN s.include_all THEN (SELECT count(*) FROM images) "
    "     ELSE (SELECT count(*) FROM image_scenes m WHERE m.scene_id = s.id AND m.is_member) END AS member_count "
    "FROM scenes s "
)
_LABEL_SELECT = (
    "SELECT l.id, l.scene_id, l.code, l.name, l.sort_order, l.is_active, "
    "(SELECT count(*) FROM scene_rules r WHERE r.label_id = l.id) AS rule_count "
    "FROM scene_labels l "
)
# 先是类别规则，按树的顺序；然后是菜系规则
_RULE_SELECT = (
    "SELECT r.id, r.scene_id, r.category_id, c.code AS category_code, cn.name AS category_name, "
    "r.cuisine, r.label_id, l.code AS label_code, l.name AS label_name "
    "FROM scene_rules r "
    "LEFT JOIN categories c ON c.id = r.category_id "
    "LEFT JOIN category_names cn ON cn.category_id = c.id AND cn.lang = :display_lang AND cn.kind = :primary_kind "
    "LEFT JOIN scene_labels l ON l.id = r.label_id "
)
_NAME_PARAMS = {"display_lang": CATEGORY_DISPLAY_LANG, "primary_kind": NameKind.PRIMARY.value}

# 约束 -> 错误，给绕过检查的写入方（本存储之外）
_CONFLICTS = {
    "uq_scenes_code": lambda: err.scene_code_exists("another scene already uses this code"),
    "uq_scene_labels_scene_id_code": lambda: err.scene_code_exists("another label of this scene already uses this code"),
    "uq_scene_rules_scene_id_category_id": lambda: err.scene_rule_exists("the scene already has a rule for this category"),
    "uq_scene_rules_scene_id_cuisine": lambda: err.scene_rule_exists("the scene already has a rule for this cuisine"),
}


def _label_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "is_active": row["is_active"],
        "rule_count": int(row["rule_count"]),
    }


def _rule_item(row: Mapping[str, Any]) -> dict[str, Any]:
    category = None
    if row["category_id"] is not None:
        category = {"id": row["category_id"], "code": row["category_code"], "name": row["category_name"]}
    label = None
    if row["label_id"] is not None:
        label = {"id": row["label_id"], "code": row["label_code"], "name": row["label_name"]}
    return {"id": row["id"], "category": category, "cuisine": row["cuisine"], "label": label}


def _read_scenes(conn: Any, scene_id: int | None = None) -> list[dict[str, Any]]:
    where, params = ("", {}) if scene_id is None else ("WHERE s.id = :sid ", {"sid": scene_id})
    scenes = conn.execute(text(_SCENE_SELECT + where + "ORDER BY s.id"), params).mappings().fetchall()
    label_where = "" if scene_id is None else "WHERE l.scene_id = :sid "
    labels = conn.execute(
        text(_LABEL_SELECT + label_where + "ORDER BY l.scene_id, l.sort_order, l.id"), params
    ).mappings().fetchall()
    rule_where = "" if scene_id is None else "WHERE r.scene_id = :sid "
    rules = conn.execute(
        text(_RULE_SELECT + rule_where + "ORDER BY r.scene_id, (r.category_id IS NULL), c.path, r.cuisine, r.id"),
        {**params, **_NAME_PARAMS},
    ).mappings().fetchall()
    labels_by: dict[int, list[dict[str, Any]]] = {}
    for row in labels:
        labels_by.setdefault(int(row["scene_id"]), []).append(_label_item(row))
    rules_by: dict[int, list[dict[str, Any]]] = {}
    for row in rules:
        rules_by.setdefault(int(row["scene_id"]), []).append(_rule_item(row))
    out = []
    for row in scenes:
        scene = _row_to_dict(row)
        scene["member_count"] = int(scene["member_count"])
        scene["labels"] = labels_by.get(int(scene["id"]), [])
        scene["rules"] = rules_by.get(int(scene["id"]), [])
        out.append(scene)
    return out


def _read_scene(conn: Any, scene_id: int) -> dict[str, Any]:
    (scene,) = _read_scenes(conn, scene_id)
    return scene


def _scene_or_404(conn: Any, scene_id: int) -> Mapping[str, Any]:
    row = conn.execute(
        text("SELECT id, code, include_all, is_active FROM scenes WHERE id = :id FOR UPDATE"), {"id": scene_id}
    ).mappings().fetchone()
    if row is None:
        raise err.scene_not_found(f"scene {scene_id} does not exist")
    return row


def _label_or_404(conn: Any, scene_id: int, label_id: int) -> Mapping[str, Any]:
    row = conn.execute(
        text("SELECT id, code FROM scene_labels WHERE id = :lid AND scene_id = :sid FOR UPDATE"),
        {"lid": label_id, "sid": scene_id},
    ).mappings().fetchone()
    if row is None:
        raise err.scene_not_found(f"scene {scene_id} has no label {label_id}")
    return row


def _rule_or_404(conn: Any, scene_id: int, rule_id: int) -> Mapping[str, Any]:
    row = conn.execute(
        text(
            "SELECT id, category_id, cuisine, label_id FROM scene_rules "
            "WHERE id = :rid AND scene_id = :sid FOR UPDATE"
        ),
        {"rid": rule_id, "sid": scene_id},
    ).mappings().fetchone()
    if row is None:
        raise err.scene_not_found(f"scene {scene_id} has no rule {rule_id}")
    return row


def _check_rule_target(conn: Any, scene_id: int, target: Mapping[str, Any], *, except_id: int | None = None) -> None:
    """类别存在，并且场景里没有其他规则以它为对象。"""
    if target["category_id"] is not None:
        if conn.execute(text("SELECT 1 FROM categories WHERE id = :cid"), {"cid": target["category_id"]}).fetchone() is None:
            raise err.validation_error(f"category_id {target['category_id']} does not exist")
        taken = conn.execute(
            text("SELECT id FROM scene_rules WHERE scene_id = :sid AND category_id = :cid"),
            {"sid": scene_id, "cid": target["category_id"]},
        ).fetchone()
        if taken is not None and taken[0] != except_id:
            raise err.scene_rule_exists(f"scene {scene_id} already has a rule for category {target['category_id']}")
        return
    taken = conn.execute(
        text("SELECT id FROM scene_rules WHERE scene_id = :sid AND cuisine = :cuisine"),
        {"sid": scene_id, "cuisine": target["cuisine"]},
    ).fetchone()
    if taken is not None and taken[0] != except_id:
        raise err.scene_rule_exists(f"scene {scene_id} already has a rule for cuisine {target['cuisine']!r}")


def _had_members(conn: Any, scene_id: int) -> bool:
    """场景现在是否有生效成员（有的话它的 code 就在这些成员的向量 payload 里）。"""
    return bool(
        conn.execute(
            text(
                "SELECT CASE WHEN NOT s.is_active THEN false "
                "            WHEN s.include_all THEN EXISTS (SELECT 1 FROM images) "
                "            ELSE EXISTS (SELECT 1 FROM image_scenes m WHERE m.scene_id = s.id AND m.is_member) END "
                "FROM scenes s WHERE s.id = :sid"
            ),
            {"sid": scene_id},
        ).scalar_one()
    )


def _written(scene: dict[str, Any], sync_required: bool) -> dict[str, Any]:
    """写入的返回值：场景现在存储的样子，加上已存图片是否需要同步。"""
    return {**scene, "sync_required": sync_required}


def _check_label_in_scene(conn: Any, scene_id: int, label_id: int | None) -> None:
    if label_id is None:
        return
    found = conn.execute(
        text("SELECT 1 FROM scene_labels WHERE id = :lid AND scene_id = :sid"), {"lid": label_id, "sid": scene_id}
    ).fetchone()
    if found is None:
        raise err.validation_error(f"label_id {label_id} is not a label of scene {scene_id}")


def _check_label_code_free(conn: Any, scene_id: int, code: str, *, except_id: int | None = None) -> None:
    taken = conn.execute(
        text("SELECT id FROM scene_labels WHERE scene_id = :sid AND code = :code"), {"sid": scene_id, "code": code}
    ).fetchone()
    if taken is not None and taken[0] != except_id:
        raise err.scene_code_exists(f"scene {scene_id} already has a label with the code {code!r}")


def _constraint_error(op: str, exc: IntegrityError) -> err.AppError:
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or "unknown"
    if name in _CONFLICTS:
        return _CONFLICTS[name]()
    return err.validation_error(f"{op} breaks the database constraint {name}")


class PgSceneConfigMixin:
    """场景配置：场景及其标签和规则。"""

    def _scene_write(self, op: str, work: Any) -> Any:
        """在目录写锁下的一个事务里执行 work(conn)；映射失败。"""
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_write(conn)
                return work(conn)
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _constraint_error(op, exc) from exc
        except Exception as exc:
            raise err.scene_postgres_failed(f"{op}: {type(exc).__name__}: {exc}") from exc

    def list_scene_configs(self) -> list[dict[str, Any]]:
        """按 id 顺序的所有场景，每个带着它的标签（按显示顺序）和规则。"""
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                return _read_scenes(conn)
        except Exception as exc:
            raise err.scene_postgres_failed(f"list_scene_configs: {type(exc).__name__}: {exc}") from exc

    # -- 场景 -----------------------------------------------------------------------------

    def create_scene(self, fields: dict[str, Any]) -> dict[str, Any]:
        new = normalize_scene_fields(fields, partial=False)

        def work(conn: Any) -> dict[str, Any]:
            if conn.execute(text("SELECT 1 FROM scenes WHERE code = :code"), {"code": new["code"]}).fetchone():
                raise err.scene_code_exists(f"another scene already uses the code {new['code']!r}")
            scene_id = conn.execute(
                text(
                    "INSERT INTO scenes (code, name, description, include_all, outputs_heating, is_active) "
                    "VALUES (:code, :name, :description, :include_all, :outputs_heating, :is_active) RETURNING id"
                ),
                new,
            ).scalar_one()
            return _written(_read_scene(conn, scene_id), scene_flags_need_sync(None, new))

        return self._scene_write("create_scene", work)

    def update_scene(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        changes = normalize_scene_fields(fields, partial=True)

        def work(conn: Any) -> dict[str, Any]:
            current = _scene_or_404(conn, sid)
            refusal = check_default_scene_change(str(current["code"]), changes)
            if refusal:
                raise err.scene_protected(refusal)
            assignments = ", ".join(f"{key} = :{key}" for key in changes)
            conn.execute(text(f"UPDATE scenes SET {assignments} WHERE id = :sid"), {"sid": sid, **changes})
            return _written(_read_scene(conn, sid), scene_flags_need_sync(current, {**current, **changes}))

        return self._scene_write("update_scene", work)

    def delete_scene(self, scene_id: int) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")

        def work(conn: Any) -> dict[str, Any]:
            current = _scene_or_404(conn, sid)
            if current["code"] == DEFAULT_SCENE:
                raise err.scene_protected(f"the default scene {DEFAULT_SCENE!r} cannot be deleted")
            # 它的 code 会留在成员的向量 payload 里，直到这些 payload 被重写
            sync_required = _had_members(conn, sid)
            conn.execute(text("DELETE FROM scenes WHERE id = :sid"), {"sid": sid})
            return {"id": sid, "code": current["code"], "sync_required": sync_required}

        return self._scene_write("delete_scene", work)

    # -- 标签 -----------------------------------------------------------------------------

    def create_scene_label(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        new = normalize_label_fields(fields, partial=False)

        def work(conn: Any) -> dict[str, Any]:
            _scene_or_404(conn, sid)
            _check_label_code_free(conn, sid, new["code"])
            conn.execute(
                text(
                    "INSERT INTO scene_labels (scene_id, code, name, sort_order, is_active) "
                    "VALUES (:sid, :code, :name, :sort_order, :is_active)"
                ),
                {"sid": sid, **new},
            )
            return _written(_read_scene(conn, sid), False)

        return self._scene_write("create_scene_label", work)

    def update_scene_label(self, scene_id: int, label_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        lid = normalize_row_id(label_id, "label_id")
        changes = normalize_label_fields(fields, partial=True)

        def work(conn: Any) -> dict[str, Any]:
            _scene_or_404(conn, sid)
            _label_or_404(conn, sid, lid)
            if "code" in changes:
                _check_label_code_free(conn, sid, changes["code"], except_id=lid)
            assignments = ", ".join(f"{key} = :{key}" for key in changes)
            conn.execute(text(f"UPDATE scene_labels SET {assignments} WHERE id = :lid"), {"lid": lid, **changes})
            return _written(_read_scene(conn, sid), False)

        return self._scene_write("update_scene_label", work)

    def delete_scene_label(self, scene_id: int, label_id: int) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        lid = normalize_row_id(label_id, "label_id")

        def work(conn: Any) -> dict[str, Any]:
            _scene_or_404(conn, sid)
            label = _label_or_404(conn, sid, lid)
            used = conn.execute(text("SELECT count(*) FROM scene_rules WHERE label_id = :lid"), {"lid": lid}).scalar_one()
            if used:
                raise err.scene_in_use(f"label {label['code']!r} is used by {used} rule(s); change or delete them first")
            conn.execute(text("DELETE FROM scene_labels WHERE id = :lid"), {"lid": lid})
            return _written(_read_scene(conn, sid), False)

        return self._scene_write("delete_scene_label", work)

    # -- 规则 -----------------------------------------------------------------------------

    def create_scene_rule(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        new = normalize_rule_fields(fields, partial=False)

        def work(conn: Any) -> dict[str, Any]:
            scene = _scene_or_404(conn, sid)
            _check_rule_target(conn, sid, new)
            _check_label_in_scene(conn, sid, new["label_id"])
            conn.execute(
                text(
                    "INSERT INTO scene_rules (scene_id, category_id, cuisine, label_id) "
                    "VALUES (:sid, :category_id, :cuisine, :label_id)"
                ),
                {"sid": sid, **new},
            )
            return _written(_read_scene(conn, sid), rule_write_needs_sync(scene))

        return self._scene_write("create_scene_rule", work)

    def update_scene_rule(self, scene_id: int, rule_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """原地修改规则的标签和 / 或替换它的对象（一个事务）。"""
        sid = normalize_row_id(scene_id, "scene_id")
        rid = normalize_row_id(rule_id, "rule_id")
        changes = normalize_rule_fields(fields, partial=True)

        def work(conn: Any) -> dict[str, Any]:
            scene = _scene_or_404(conn, sid)
            rule = _rule_or_404(conn, sid, rid)
            merged = {
                "category_id": rule["category_id"], "cuisine": rule["cuisine"], "label_id": rule["label_id"], **changes,
            }
            check_rule_label(merged, label_sent="label_id" in changes)
            target_changed = (merged["category_id"], merged["cuisine"]) != (rule["category_id"], rule["cuisine"])
            if target_changed:
                _check_rule_target(conn, sid, merged, except_id=rid)
            if merged["label_id"] != rule["label_id"]:
                _check_label_in_scene(conn, sid, merged["label_id"])
            conn.execute(
                text(
                    "UPDATE scene_rules SET category_id = :category_id, cuisine = :cuisine, label_id = :label_id "
                    "WHERE id = :rid"
                ),
                {"rid": rid, **merged},
            )
            return _written(_read_scene(conn, sid), rule_write_needs_sync(scene, target_changed=target_changed))

        return self._scene_write("update_scene_rule", work)

    def delete_scene_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        sid = normalize_row_id(scene_id, "scene_id")
        rid = normalize_row_id(rule_id, "rule_id")

        def work(conn: Any) -> dict[str, Any]:
            scene = _scene_or_404(conn, sid)
            _rule_or_404(conn, sid, rid)
            conn.execute(text("DELETE FROM scene_rules WHERE id = :rid"), {"rid": rid})
            return _written(_read_scene(conn, sid), rule_write_needs_sync(scene))

        return self._scene_write("delete_scene_rule", work)
