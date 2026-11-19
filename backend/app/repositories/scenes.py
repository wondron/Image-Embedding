"""scenes / image_scenes：生效的归属（写入时物化）和场景标签映射。

生效的归属（契约 §2.9）：对每个不是 include_all 的启用场景，是它的规则匹配到的图片，
再被 origin='manual' 的行覆盖；另外加上每个启用的 include_all 场景，它不保存任何行。
停用的场景在任何地方都没有成员（行会保留，所以重新启用后手工覆盖会恢复）。

每次重算图片时，规则行都按当前规则重写：

- recompute_image_scenes：一张图（插入、手工覆盖、元数据修改之后）；
- recompute_scene_memberships：按 id 顺序的一批图片，在场景或其规则变化之后
  （POST /api/scenes/sync、app.workers.sync_scenes）；
- _recompute_subtree：一个类别下的图片，在移动它或修改它菜系的那个事务里
  （app.repositories.categories.update_category）。

它们都用 app.core.rules.scene_membership 里的纯规则做规划，只对生效场景集合变化的图片
递增 images.payload_version，并在共享的目录锁（app.repositories.catalog_lock）下读取目录，
所以管理写入永远不会落在规划的中途。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core import errors as err
from app.core.enums import SceneOrigin
from app.db.common import _as_uuid, _row_to_dict, _uuid_in_clause
from app.repositories.catalog_lock import lock_catalog_for_read
from app.core.rules.scene_fields import normalize_row_id
from app.core.rules.scene_membership import (
    apply_manual_overrides,
    compute_rule_member_scene_ids,
    effective_cuisines,
)

# 每张图片生效的场景 code：启用的 include_all 场景（隐式，没有行）加上其他启用场景的
# 成员行。按场景 id 排序。
_SCENE_CODES_SQL = (
    "SELECT x.image_id, array_agg(x.code ORDER BY x.scene_id) AS codes FROM ("
    "  SELECT isc.image_id::text AS image_id, s.id AS scene_id, s.code "
    "  FROM image_scenes isc JOIN scenes s ON s.id = isc.scene_id "
    "  WHERE isc.is_member AND s.is_active AND NOT s.include_all AND isc.image_id IN {ids} "
    "  UNION ALL "
    "  SELECT i.id::text, s.id, s.code "
    "  FROM images i CROSS JOIN scenes s "
    "  WHERE s.include_all AND s.is_active AND i.id IN {ids}"
    ") x GROUP BY x.image_id"
)

# 对每个类别：场景里覆盖它（类别本身或某个祖先）、并且映射到启用标签的最深的规则
# （契约 §2.8：最具体的规则胜出）。只有类别规则带标签（ck_scene_rules_label_needs_category）。
_LABELS_SQL = (
    "SELECT DISTINCT ON (c.id) c.id AS category_id, l.id, l.code, l.name "
    "FROM categories c "
    "JOIN scene_rules r ON r.scene_id = :sid AND r.label_id IS NOT NULL "
    "JOIN categories rc ON rc.id = r.category_id AND c.path <@ rc.path "
    "JOIN scene_labels l ON l.id = r.label_id AND l.is_active "
    "WHERE c.id = ANY(:ids) "
    "ORDER BY c.id, nlevel(rc.path) DESC"
)


@dataclass(frozen=True)
class _Catalog:
    """每张图片都要拿来匹配的内容：启用的场景和所有规则。"""

    scenes: list[Mapping[str, Any]]
    rules_by_scene: dict[int, list[Mapping[str, Any]]]
    rule_cat_paths: dict[int, str]
    code_by_id: dict[int, str]
    include_all_ids: frozenset[int]


@dataclass
class _Plan:
    image_id: str
    payload_version: int
    rule_members: set[int]
    desired_rule_rows: set[int]
    current_rule_rows: set[int]
    old_effective: set[int]
    new_effective: set[int]
    changed: bool = field(init=False)

    def __post_init__(self) -> None:
        self.changed = self.old_effective != self.new_effective


def _load_catalog(conn: Any) -> _Catalog:
    scenes = list(
        conn.execute(
            text("SELECT id, code, include_all FROM scenes WHERE is_active ORDER BY id")
        ).mappings().fetchall()
    )
    rules_by_scene: dict[int, list[Mapping[str, Any]]] = {}
    rule_cat_paths: dict[int, str] = {}
    for rule in conn.execute(
        text(
            "SELECT r.id, r.scene_id, r.category_id, r.cuisine, r.label_id, c.path::text AS category_path "
            "FROM scene_rules r LEFT JOIN categories c ON c.id = r.category_id ORDER BY r.id"
        )
    ).mappings():
        rules_by_scene.setdefault(int(rule["scene_id"]), []).append(rule)
        if rule["category_id"] is not None and rule["category_path"] is not None:
            rule_cat_paths[int(rule["category_id"])] = rule["category_path"]
    return _Catalog(
        scenes=scenes,
        rules_by_scene=rules_by_scene,
        rule_cat_paths=rule_cat_paths,
        code_by_id={int(s["id"]): str(s["code"]) for s in scenes},
        include_all_ids=frozenset(int(s["id"]) for s in scenes if s["include_all"]),
    )


def _load_image_categories(conn: Any, category_ids: Iterable[int]) -> dict[int, tuple[str, list[str]]]:
    """{category_id: (path, 生效的菜系)}；菜系来自类别本身，或者最近一个有菜系的祖先
    （scene_membership.effective_cuisines）。
    """
    ids = sorted(set(category_ids))
    if not ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT c.id AS for_id, c.path::text AS for_path, anc.id, anc.path::text AS path, anc.cuisines "
            "FROM categories c JOIN categories anc ON anc.path @> c.path "
            "WHERE c.id = ANY(:ids) ORDER BY c.id, nlevel(anc.path)"
        ),
        {"ids": ids},
    ).mappings().fetchall()
    chains: dict[int, list[Mapping[str, Any]]] = {}
    paths: dict[int, str] = {}
    for row in rows:
        chains.setdefault(int(row["for_id"]), []).append(row)
        paths[int(row["for_id"])] = row["for_path"]
    out: dict[int, tuple[str, list[str]]] = {}
    for category_id, chain in chains.items():
        own = next((row for row in chain if int(row["id"]) == category_id), None)
        ancestors = [row for row in chain if int(row["id"]) != category_id]  # 根在前
        out[category_id] = (paths[category_id], effective_cuisines(own, ancestors))
    return out


def _load_rows(conn: Any, image_ids: Sequence[UUID]) -> dict[str, list[Mapping[str, Any]]]:
    rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in conn.execute(
        text(
            "SELECT image_id::text AS image_id, scene_id, origin, is_member FROM image_scenes "
            "WHERE image_id = ANY(:ids)"
        ),
        {"ids": list(image_ids)},
    ).mappings():
        rows.setdefault(row["image_id"], []).append(row)
    return rows


def _effective_from_rows(rows: Sequence[Mapping[str, Any]], catalog: _Catalog) -> set[int]:
    """已存行给出的生效场景 id（_SCENE_CODES_SQL 读取的内容）。"""
    active, include_all = catalog.code_by_id, catalog.include_all_ids
    members = {
        int(r["scene_id"])
        for r in rows
        if r["is_member"] and int(r["scene_id"]) in active and int(r["scene_id"]) not in include_all
    }
    return members | set(include_all)


def _plan_image(
    image: Mapping[str, Any],
    catalog: _Catalog,
    categories: Mapping[int, tuple[str, list[str]]],
    rows: Sequence[Mapping[str, Any]],
    before: set[int] | None = None,
) -> _Plan:
    """before：调用方先改了行时（手工覆盖）用来比较的生效集合；None 时和现在的行比较。"""
    category_id = image["category_id"]
    path, cuisines = (None, [])
    if category_id is not None:
        path, cuisines = categories.get(int(category_id), (None, []))
    rule_members = compute_rule_member_scene_ids(
        scenes=catalog.scenes,
        rules_by_scene=catalog.rules_by_scene,
        rule_cat_paths=catalog.rule_cat_paths,
        image_category_id=int(category_id) if category_id is not None else None,
        image_path=path,
        image_cuisines=cuisines,
    )
    active = catalog.code_by_id
    include_all = set(catalog.include_all_ids)
    manual = [r for r in rows if r["origin"] == SceneOrigin.MANUAL.value]
    counted_manual = [r for r in manual if int(r["scene_id"]) in active and int(r["scene_id"]) not in include_all]
    return _Plan(
        image_id=str(image["id"]),
        payload_version=int(image["payload_version"] or 1),
        rule_members=set(rule_members),
        # 手工的行决定那个场景；规则行只为其他匹配保留
        desired_rule_rows=set(rule_members) - {int(r["scene_id"]) for r in manual},
        current_rule_rows={int(r["scene_id"]) for r in rows if r["origin"] == SceneOrigin.RULE.value},
        old_effective=_effective_from_rows(rows, catalog) if before is None else set(before),
        new_effective=apply_manual_overrides(set(rule_members), counted_manual) | include_all,
    )


def _apply_plans(conn: Any, plans: Sequence[_Plan], *, bump: bool) -> None:
    """写入规则行的差异；递增集合变化的图片的 payload_version。"""
    drop = [(p.image_id, sid) for p in plans for sid in sorted(p.current_rule_rows - p.desired_rule_rows)]
    add = [(p.image_id, sid) for p in plans for sid in sorted(p.desired_rule_rows - p.current_rule_rows)]
    if drop:
        conn.execute(
            text(
                "DELETE FROM image_scenes s USING unnest(CAST(:img AS uuid[]), CAST(:sid AS integer[])) AS d(image_id, scene_id) "
                "WHERE s.image_id = d.image_id AND s.scene_id = d.scene_id AND s.origin = :rule"
            ),
            {"img": [_as_uuid(i) for i, _ in drop], "sid": [s for _, s in drop], "rule": SceneOrigin.RULE.value},
        )
    if add:
        conn.execute(
            text(
                "INSERT INTO image_scenes (image_id, scene_id, origin, is_member, updated_at) "
                "SELECT d.image_id, d.scene_id, :rule, true, now() "
                "FROM unnest(CAST(:img AS uuid[]), CAST(:sid AS integer[])) AS d(image_id, scene_id) "
                "ON CONFLICT (image_id, scene_id) DO NOTHING"
            ),
            {"img": [_as_uuid(i) for i, _ in add], "sid": [s for _, s in add], "rule": SceneOrigin.RULE.value},
        )
    changed = [p for p in plans if p.changed]
    if bump and changed:
        conn.execute(
            text(
                "UPDATE images SET payload_version = payload_version + 1, updated_at = now() "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [_as_uuid(p.image_id) for p in changed]},
        )
        for plan in changed:
            plan.payload_version += 1


def _recompute(
    conn: Any,
    images: Sequence[Mapping[str, Any]],
    *,
    bump: bool,
    before: Mapping[str, set[int]] | None = None,
) -> tuple[_Catalog, list[_Plan]]:
    """对已经 FOR UPDATE 锁住的图片做规划并应用，在目录读锁下进行。"""
    catalog = _load_catalog(conn)
    categories = _load_image_categories(
        conn, (int(i["category_id"]) for i in images if i["category_id"] is not None)
    )
    rows = _load_rows(conn, [_as_uuid(i["id"]) for i in images])
    plans = [
        _plan_image(image, catalog, categories, rows.get(str(image["id"]), []), (before or {}).get(str(image["id"])))
        for image in images
    ]
    _apply_plans(conn, plans, bump=bump)
    return catalog, plans


def _recompute_subtree(conn: Any, category_id: int, *, chunk: int = 500) -> tuple[int, list[str]]:
    """重算标注为这个类别或其下类别的所有图片，按 id 顺序，在调用方的事务里（它持有目录锁）
    一块一块进行。类别树必须已经是将要提交的样子：移动已经重建了子树的路径（触发器）。

    返回（重算的图片数, 生效场景集合变化的 id——已递增 payload_version）。
    """
    total = 0
    changed: list[str] = []
    after: UUID | None = None
    while True:
        images = conn.execute(
            text(
                "SELECT i.id, i.category_id, i.payload_version FROM images i "
                "JOIN categories c ON c.id = i.category_id "
                "WHERE c.path <@ (SELECT path FROM categories WHERE id = :cid) "
                "AND (CAST(:after AS uuid) IS NULL OR i.id > CAST(:after AS uuid)) "
                "ORDER BY i.id LIMIT :lim FOR UPDATE OF i"
            ),
            {"cid": int(category_id), "after": after, "lim": int(chunk)},
        ).mappings().fetchall()
        if not images:
            return total, changed
        _, plans = _recompute(conn, images, bump=True)
        total += len(plans)
        changed.extend(p.image_id for p in plans if p.changed)
        if len(images) < chunk:
            return total, changed
        after = _as_uuid(images[-1]["id"])


def _lock_image(conn: Any, image_id: str) -> Mapping[str, Any]:
    image = conn.execute(
        text("SELECT id, category_id, payload_version FROM images WHERE id = CAST(:id AS uuid) FOR UPDATE"),
        {"id": image_id},
    ).mappings().fetchone()
    if image is None:
        raise err.validation_error(f"image not found: {image_id}")
    return image


def _membership_result(image_id: str, catalog: _Catalog, plan: _Plan) -> dict[str, Any]:
    return {
        "image_id": image_id,
        "scene_codes": [catalog.code_by_id[sid] for sid in sorted(plan.new_effective)],
        "rule_member_ids": sorted(plan.rule_members),
        "effective_ids": sorted(plan.new_effective),
        "payload_version": plan.payload_version,
    }


def _read_membership_snapshot(conn: Any, image_id: str) -> dict[str, Any]:
    """一条语句 / 一个 MVCC 视图，即使场景目录为空也包含图片。

    只读取已存的行：GET 不能预览规则或物化任何东西。implicit 是响应里的来源，不是持久化的
    SceneOrigin。写入在释放锁之前调用这个函数。
    """
    rows = conn.execute(
        text(
            "SELECT i.id::text AS image_id, i.payload_version, s.id AS scene_id, s.code AS scene_code, "
            "s.is_active, s.include_all, m.origin, m.is_member "
            "FROM images i LEFT JOIN scenes s ON true "
            "LEFT JOIN image_scenes m ON m.image_id = i.id AND m.scene_id = s.id "
            "WHERE i.id = CAST(:iid AS uuid) ORDER BY s.id"
        ),
        {"iid": image_id},
    ).mappings().fetchall()
    if not rows:
        raise err.validation_error(f"image not found: {image_id}")
    memberships = []
    scenes = []
    for row in rows:
        if row["scene_id"] is None:
            continue
        include_all, active = bool(row["include_all"]), bool(row["is_active"])
        origin = "implicit" if include_all else row["origin"]
        override = bool(row["is_member"]) if not include_all and origin == SceneOrigin.MANUAL.value else None
        member = active and (include_all or bool(row["is_member"]))
        memberships.append({
            "scene_id": int(row["scene_id"]), "scene_code": row["scene_code"],
            "is_active": active, "include_all": include_all, "is_member": member,
            "origin": origin, "manual_override": override,
        })
        if member:
            scenes.append(row["scene_code"])
    return {
        "image_id": rows[0]["image_id"], "payload_version": int(rows[0]["payload_version"]),
        "scenes": scenes, "memberships": memberships,
    }


def _upsert_manual_row(conn: Any, image_id: str, scene_id: int, is_member: bool) -> None:
    """和旧的按 code 设置的函数共用；调用方先持有目录锁，再持有图片锁。"""
    conn.execute(
        text(
            "INSERT INTO image_scenes (image_id, scene_id, origin, is_member, updated_at) "
            "VALUES (CAST(:iid AS uuid), :sid, :manual, :m, now()) "
            "ON CONFLICT (image_id, scene_id) DO UPDATE SET "
            "origin = :manual, is_member = EXCLUDED.is_member, updated_at = now() "
            "WHERE image_scenes.origin IS DISTINCT FROM EXCLUDED.origin "
            "OR image_scenes.is_member IS DISTINCT FROM EXCLUDED.is_member"
        ),
        {"iid": image_id, "sid": scene_id, "manual": SceneOrigin.MANUAL.value, "m": is_member},
    )


class PgScenesMixin:
    """场景查询、image_scenes 物化和场景标签映射。"""

    def _scene_codes_by_image(self, conn, image_ids: list[str]) -> dict[str, list[str]]:
        """给定 id 的 {image_id: [场景 code]}（见 _SCENE_CODES_SQL）。"""
        if not image_ids:
            return {}
        id_sql, id_params = _uuid_in_clause(image_ids)
        rows = conn.execute(
            text(_SCENE_CODES_SQL.format(ids=id_sql)), id_params
        ).mappings().fetchall()
        return {str(r["image_id"]): list(r["codes"] or []) for r in rows}

    def _scene_membership_row(self, conn, code: str) -> dict[str, Any] | None:
        """场景 code 对应的 id / include_all / is_active，不存在时为 None。"""
        row = conn.execute(
            text("SELECT id, include_all, is_active FROM scenes WHERE code = :c LIMIT 1"),
            {"c": str(code).strip()},
        ).mappings().fetchone()
        return dict(row) if row else None

    def list_scene_codes(self, *, active_only: bool = True) -> list[str]:
        self.ensure_schema()
        sql = "SELECT code FROM scenes"
        if active_only:
            sql += " WHERE is_active = true"
        sql += " ORDER BY id"
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(sql)).fetchall()
        return [str(r[0]) for r in rows]

    def get_scene_by_code(self, code: str) -> dict[str, Any] | None:
        self.ensure_schema()
        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, code, name, description, include_all, "
                    "outputs_heating, is_active, created_at "
                    "FROM scenes WHERE code = :c LIMIT 1"
                ),
                {"c": str(code).strip()},
            ).mappings().fetchone()
        return _row_to_dict(row) if row else None

    def get_category_id_by_code(self, code: str) -> int | None:
        self.ensure_schema()
        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM categories WHERE lower(code) = lower(:c) LIMIT 1"),
                {"c": str(code).strip()},
            ).fetchone()
        return int(row[0]) if row else None

    def get_effective_scene_codes(self, image_id: UUID | str) -> list[str]:
        self.ensure_schema()

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        with engine.connect() as conn:
            return self._scene_codes_by_image(conn, [uid]).get(uid, [])

    def recompute_image_scenes(
        self,
        image_id: UUID | str,
        *,
        bump_version: bool = True,
    ) -> dict[str, Any]:
        """按当前规则重写一张图片的规则行（手工行保留）。

        bump_version：生效场景集合变化时递增 images.payload_version（已经递增过、或者会在之后
        立刻自己推送 payload 的调用方传 False）。
        返回 {image_id, scene_codes, rule_member_ids, effective_ids, payload_version}。
        """
        self.ensure_schema()
        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_read(conn)
                image = _lock_image(conn, uid)
                catalog, (plan,) = _recompute(conn, [image], bump=bump_version)
            return _membership_result(uid, catalog, plan)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"recompute_image_scenes: {type(exc).__name__}: {exc}"
            ) from exc

    def recompute_scene_memberships(self, *, after: UUID | str | None = None, limit: int = 200) -> dict[str, Any]:
        """按 id 顺序重算 `after` 之后的 `limit` 张图片（一个事务）。

        返回 {image_ids, changed_ids, last_id}；没有剩下的图片时 last_id 为 None。
        """
        self.ensure_schema()
        after_id = _as_uuid(after) if after is not None else None
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_read(conn)
                images = conn.execute(
                    text(
                        "SELECT id, category_id, payload_version FROM images "
                        "WHERE CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid) "
                        "ORDER BY id LIMIT :lim FOR UPDATE"
                    ),
                    {"after": after_id, "lim": int(limit)},
                ).mappings().fetchall()
                if not images:
                    return {"image_ids": [], "changed_ids": [], "last_id": None}
                _, plans = _recompute(conn, images, bump=True)
            return {
                "image_ids": [p.image_id for p in plans],
                "changed_ids": [p.image_id for p in plans if p.changed],
                "last_id": plans[-1].image_id,
            }
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"recompute_scene_memberships: {type(exc).__name__}: {exc}"
            ) from exc

    def get_scene_memberships(self, image_id: UUID | str) -> dict[str, Any]:
        """在一条一致的 PG 语句里读取完整的目录和已存的归属。"""
        uid = str(_as_uuid(image_id))
        try:
            self.ensure_schema()
            with self._get_engine().connect() as conn:
                return _read_membership_snapshot(conn, uid)
        except SQLAlchemyError as exc:
            raise err.index_postgres_failed(
                f"get_scene_memberships: {type(exc).__name__}: {exc}"
            ) from exc

    def put_manual_scene_membership(
        self, image_id: UUID | str, scene_id: int, *, is_member: bool
    ) -> dict[str, Any]:
        """只用 PG 的手工包含 / 排除，在同一个事务里重算并生成快照。"""
        if not isinstance(is_member, bool):
            raise err.validation_error("is_member must be true or false")
        return self._change_manual_scene_membership(image_id, scene_id, is_member=is_member)

    def clear_manual_scene_membership(self, image_id: UUID | str, scene_id: int) -> dict[str, Any]:
        """只清空手工覆盖，然后恢复当前规则，即使原来没有手工行。"""
        return self._change_manual_scene_membership(image_id, scene_id, is_member=None)

    def _change_manual_scene_membership(
        self, image_id: UUID | str, scene_id: int, *, is_member: bool | None
    ) -> dict[str, Any]:
        uid = str(_as_uuid(image_id))
        sid = normalize_row_id(scene_id, "scene_id")
        try:
            self.ensure_schema()
            with self._get_engine().begin() as conn:
                # 和入库 / 同步相同的加锁顺序：这里从不获取场景写锁。
                lock_catalog_for_read(conn)
                image = _lock_image(conn, uid)
                scene = conn.execute(
                    text("SELECT id, code, include_all, is_active FROM scenes WHERE id = :sid"),
                    {"sid": sid},
                ).mappings().fetchone()
                if scene is None:
                    raise err.scene_not_found(f"scene {sid} does not exist")
                if scene["include_all"]:
                    raise err.validation_error(f"scene {sid} includes all images; manual membership is not allowed")
                if is_member is not None and not scene["is_active"]:
                    raise err.validation_error(f"scene {sid} is inactive; manual membership cannot be set")
                # 在改变 origin 之前记下来：rule=true -> manual=true 不能递增版本。
                before = _effective_from_rows(
                    _load_rows(conn, [_as_uuid(uid)]).get(uid, []), _load_catalog(conn)
                )
                if is_member is None:
                    conn.execute(
                        text(
                            "DELETE FROM image_scenes WHERE image_id = CAST(:iid AS uuid) "
                            "AND scene_id = :sid AND origin = :manual"
                        ),
                        {"iid": uid, "sid": sid, "manual": SceneOrigin.MANUAL.value},
                    )
                else:
                    _upsert_manual_row(conn, uid, sid, is_member)
                _recompute(conn, [image], bump=True, before={uid: before})
                snapshot = _read_membership_snapshot(conn, uid)
            return snapshot
        except SQLAlchemyError as exc:
            # 数据库错误越过仓储边界之前，begin() 已经回滚了。
            raise err.index_postgres_failed(
                f"change_manual_scene_membership: {type(exc).__name__}: {exc}"
            ) from exc

    def set_manual_scene_membership(
        self,
        image_id: UUID | str,
        scene_code: str,
        *,
        is_member: bool,
    ) -> dict[str, Any]:
        """在一个事务里新增或更新 origin=manual 的覆盖并重算；图片生效场景变化时递增
        payload_version。

        include_all 场景包含所有图片，不接受覆盖。
        """
        self.ensure_schema()
        uid = str(_as_uuid(image_id))
        scene = self.get_scene_by_code(scene_code)
        if not scene:
            raise err.validation_error(f"unknown scene: {scene_code!r}")
        if scene.get("include_all"):
            raise err.validation_error(
                f"scene {scene_code!r} includes all images; manual membership is not allowed"
            )
        sid = int(scene["id"])
        engine = self._get_engine()
        with engine.begin() as conn:
            lock_catalog_for_read(conn)
            image = _lock_image(conn, uid)
            # 覆盖之前的集合：下面的重算和它比较，而不是和新行比较
            before = _effective_from_rows(_load_rows(conn, [_as_uuid(uid)]).get(uid, []), _load_catalog(conn))
            _upsert_manual_row(conn, uid, sid, bool(is_member))
            catalog, (plan,) = _recompute(conn, [image], bump=True, before={uid: before})
        return _membership_result(uid, catalog, plan)

    def map_categories_to_scene_labels(
        self,
        scene_id: int,
        category_ids: Sequence[int],
    ) -> dict[int, dict[str, Any]]:
        """这些类别在场景标签里的 {category_id: {id, code, name}}（4.3 节）。

        没有覆盖它、并映射到启用标签的规则的类别不返回。
        """
        ids = sorted({int(c) for c in category_ids})
        if not ids:
            return {}
        self.ensure_schema()
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(_LABELS_SQL), {"sid": int(scene_id), "ids": ids}).mappings().fetchall()
        return {int(r["category_id"]): {"id": r["id"], "code": r["code"], "name": r["name"]} for r in rows}
