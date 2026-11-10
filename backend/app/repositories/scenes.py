"""scenes / image_scenes: effective membership (write-time materialize) and scene-label mapping.

Effective membership (contract §2.9): for every active scene that is not include_all, the
images its rules match, overridden by origin='manual' rows; plus every active include_all
scene, which keeps no rows. An inactive scene has no members anywhere (rows are kept, so manual
overrides come back when it is enabled again).

Rule rows are rewritten from the current rules whenever images are recomputed:

- recompute_image_scenes: one image (after an insert, a manual override, a metadata change);
- recompute_scene_memberships: a batch of images in id order, after the scenes, their rules or
  the category tree changed (POST /api/scenes/sync, app.workers.sync_scenes).

Both plan with the pure rules in app.services.retrieval.scene_membership, bump
images.payload_version only for images whose effective scene set changed, and read the catalog
under its shared lock (app.repositories.catalog_lock), so an admin write never lands halfway
through a plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core import errors as err
from app.core.enums import SceneOrigin
from app.db.common import _as_uuid, _row_to_dict, _uuid_in_clause
from app.repositories.catalog_lock import lock_catalog_for_read
from app.services.retrieval.scene_membership import (
    apply_manual_overrides,
    compute_rule_member_scene_ids,
    effective_cuisines,
)

# Effective scene codes per image: active include_all scenes (implicit, no rows) plus member rows
# of the other active scenes. Ordered by scene id.
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

# For each category: the deepest rule of the scene that covers it (the category itself or an
# ancestor) and maps to an active label (contract §2.8: the most specific rule wins). Only
# category rules carry labels (ck_scene_rules_label_needs_category).
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
    """What every image is matched against: the active scenes and all rules."""

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
    """{category_id: (path, effective cuisines)}; cuisines come from the category or its nearest
    ancestor that has any (scene_membership.effective_cuisines)."""
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
        ancestors = [row for row in chain if int(row["id"]) != category_id]  # root first
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
    """The effective scene ids the stored rows give (what _SCENE_CODES_SQL reads)."""
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
    """before: the effective set to compare with when the caller changed rows first (a manual
    override); None compares with the rows as they are now."""
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
        # A manual row decides that scene; rule rows are kept for the other matches only
        desired_rule_rows=set(rule_members) - {int(r["scene_id"]) for r in manual},
        current_rule_rows={int(r["scene_id"]) for r in rows if r["origin"] == SceneOrigin.RULE.value},
        old_effective=_effective_from_rows(rows, catalog) if before is None else set(before),
        new_effective=apply_manual_overrides(set(rule_members), counted_manual) | include_all,
    )


def _apply_plans(conn: Any, plans: Sequence[_Plan], *, bump: bool) -> None:
    """Write the rule-row differences; bump payload_version of the images whose set changed."""
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
    """Plan and apply for images already locked FOR UPDATE, under the catalog read lock."""
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


class PgScenesMixin:
    """scenes lookups, image_scenes materialization and scene-label mapping."""

    def _scene_codes_by_image(self, conn, image_ids: list[str]) -> dict[str, list[str]]:
        """{image_id: [scene codes]} for the given ids (see _SCENE_CODES_SQL)."""
        if not image_ids:
            return {}
        id_sql, id_params = _uuid_in_clause(image_ids)
        rows = conn.execute(
            text(_SCENE_CODES_SQL.format(ids=id_sql)), id_params
        ).mappings().fetchall()
        return {str(r["image_id"]): list(r["codes"] or []) for r in rows}

    def _scene_membership_row(self, conn, code: str) -> dict[str, Any] | None:
        """id / include_all / is_active for a scene code, or None if unknown."""
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
                text("SELECT id FROM categories WHERE code = :c LIMIT 1"),
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
        """Rewrite one image's rule rows from the current rules (manual rows stay).

        bump_version: bump images.payload_version when the effective scene set changed (callers
        that already bumped, or that push the payload themselves right after, pass False).
        Returns {image_id, scene_codes, rule_member_ids, effective_ids, payload_version}.
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
        """Recompute the next `limit` images after `after` in id order (one transaction).

        Returns {image_ids, changed_ids, last_id}; last_id is None when no image was left.
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

    def set_manual_scene_membership(
        self,
        image_id: UUID | str,
        scene_code: str,
        *,
        is_member: bool,
    ) -> dict[str, Any]:
        """Upsert an origin=manual override and recompute, in one transaction; payload_version is
        bumped when the image's effective scenes changed.

        include_all scenes contain every image and take no overrides.
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
        manual = SceneOrigin.MANUAL.value
        engine = self._get_engine()
        with engine.begin() as conn:
            lock_catalog_for_read(conn)
            image = _lock_image(conn, uid)
            # The set before the override: the recompute below compares with it, not with the new row
            before = _effective_from_rows(_load_rows(conn, [_as_uuid(uid)]).get(uid, []), _load_catalog(conn))
            conn.execute(
                text(
                    "INSERT INTO image_scenes "
                    "(image_id, scene_id, origin, is_member, updated_at) "
                    "VALUES (CAST(:iid AS uuid), :sid, :manual, :m, now()) "
                    "ON CONFLICT (image_id, scene_id) DO UPDATE SET "
                    "origin = :manual, is_member = EXCLUDED.is_member, "
                    "updated_at = now()"
                ),
                {"iid": uid, "sid": sid, "manual": manual, "m": bool(is_member)},
            )
            catalog, (plan,) = _recompute(conn, [image], bump=True, before={uid: before})
        return _membership_result(uid, catalog, plan)

    def map_categories_to_scene_labels(
        self,
        scene_id: int,
        category_ids: Sequence[int],
    ) -> dict[int, dict[str, Any]]:
        """{category_id: {id, code, name}} of the scene's labels for these categories (section 4.3).

        A category without a covering rule to an active label is left out.
        """
        ids = sorted({int(c) for c in category_ids})
        if not ids:
            return {}
        self.ensure_schema()
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(_LABELS_SQL), {"sid": int(scene_id), "ids": ids}).mappings().fetchall()
        return {int(r["category_id"]): {"id": r["id"], "code": r["code"], "name": r["name"]} for r in rows}
