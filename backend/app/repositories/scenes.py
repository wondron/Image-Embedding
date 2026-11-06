from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.core.enums import SceneOrigin
from app.db.common import _as_uuid, _row_to_dict, _uuid_in_clause
from app.services.retrieval import errors as err
from app.services.retrieval.scene_membership import (
    apply_manual_overrides,
    compute_rule_member_scene_ids,
    effective_cuisines,
    include_all_scene_ids,
    most_specific_label_id,
    rule_matches_category,
)

# Effective scene codes per image: active include_all scenes (implicit, no rows)
# plus image_scenes member rows of the other scenes. Ordered by scene id.
_SCENE_CODES_SQL = (
    "SELECT x.image_id, array_agg(x.code ORDER BY x.scene_id) AS codes FROM ("
    "  SELECT isc.image_id::text AS image_id, s.id AS scene_id, s.code "
    "  FROM image_scenes isc JOIN scenes s ON s.id = isc.scene_id "
    "  WHERE isc.is_member AND NOT s.include_all AND isc.image_id IN {ids} "
    "  UNION ALL "
    "  SELECT i.id::text, s.id, s.code "
    "  FROM images i CROSS JOIN scenes s "
    "  WHERE s.include_all AND s.is_active AND i.id IN {ids}"
    ") x GROUP BY x.image_id"
)


class PgScenesMixin:
    """scenes / image_scenes materialize + label mapping."""

    def _scene_codes_by_image(self, conn, image_ids: list[str]) -> dict[str, list[str]]:
        """{image_id: [scene codes]} for the given ids (see _SCENE_CODES_SQL)."""
        if not image_ids:
            return {}
        from sqlalchemy import text

        id_sql, id_params = _uuid_in_clause(image_ids)
        rows = conn.execute(
            text(_SCENE_CODES_SQL.format(ids=id_sql)), id_params
        ).mappings().fetchall()
        return {str(r["image_id"]): list(r["codes"] or []) for r in rows}

    def _scene_membership_row(self, conn, code: str) -> dict[str, Any] | None:
        """id / include_all / is_active for a scene code, or None if unknown."""
        from sqlalchemy import text

        row = conn.execute(
            text(
                "SELECT id, include_all, is_active FROM scenes "
                "WHERE code = :c LIMIT 1"
            ),
            {"c": str(code).strip()},
        ).mappings().fetchone()
        return dict(row) if row else None

    def list_scene_codes(self, *, active_only: bool = True) -> list[str]:
        self.ensure_schema()
        from sqlalchemy import text

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
        from sqlalchemy import text

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
        from sqlalchemy import text

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
        """Write-time materialize effective membership into image_scenes.

        - Replaces origin='rule' rows; preserves origin='manual'.
        - include_all scenes get no rows (they contain every image).
        - Effective codes = include_all ∪ (rule members with manual overrides applied).
        - Optionally bumps images.payload_version (skip when caller already bumped).
        """
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                img = conn.execute(
                    text(
                        "SELECT id, category_id, food_state, label_status, "
                        "payload_version FROM images "
                        "WHERE id = CAST(:id AS uuid) FOR UPDATE"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
                if img is None:
                    raise err.validation_error(f"image not found: {uid}")

                category_id = img["category_id"]
                image_path = None
                ancestors: list[dict[str, Any]] = []
                cat_row = None
                if category_id is not None:
                    cat_row = conn.execute(
                        text(
                            "SELECT id, code, path::text AS path, cuisines, "
                            "parent_id, heating_group_id "
                            "FROM categories WHERE id = :cid"
                        ),
                        {"cid": int(category_id)},
                    ).mappings().fetchone()
                    if cat_row is not None:
                        image_path = cat_row["path"]
                        ancestors = list(
                            conn.execute(
                                text(
                                    "SELECT id, code, path::text AS path, cuisines, "
                                    "heating_group_id FROM categories "
                                    "WHERE path @> CAST(:p AS ltree) "
                                    "ORDER BY nlevel(path)"
                                ),
                                {"p": image_path},
                            ).mappings().fetchall()
                        )

                cuisines = effective_cuisines(cat_row, ancestors)

                scenes = list(
                    conn.execute(
                        text(
                            "SELECT id, code, include_all, is_active "
                            "FROM scenes WHERE is_active = true ORDER BY id"
                        )
                    ).mappings().fetchall()
                )
                rules = list(
                    conn.execute(
                        text(
                            "SELECT id, scene_id, category_id, cuisine, label_id "
                            "FROM scene_rules"
                        )
                    ).mappings().fetchall()
                )
                rules_by: dict[int, list] = {}
                cat_ids = set()
                for rule in rules:
                    rules_by.setdefault(int(rule["scene_id"]), []).append(rule)
                    if rule["category_id"] is not None:
                        cat_ids.add(int(rule["category_id"]))
                rule_cat_paths: dict[int, str] = {}
                if cat_ids:
                    for r in conn.execute(
                        text(
                            "SELECT id, path::text AS path FROM categories "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": list(cat_ids)},
                    ).mappings().fetchall():
                        rule_cat_paths[int(r["id"])] = r["path"]

                rule_members = compute_rule_member_scene_ids(
                    scenes=scenes,
                    rules_by_scene=rules_by,
                    rule_cat_paths=rule_cat_paths,
                    image_category_id=int(category_id) if category_id is not None else None,
                    image_path=image_path,
                    image_cuisines=cuisines,
                )

                manual = list(
                    conn.execute(
                        text(
                            "SELECT isc.scene_id, isc.is_member FROM image_scenes isc "
                            "JOIN scenes s ON s.id = isc.scene_id "
                            "WHERE isc.image_id = CAST(:id AS uuid) "
                            "  AND isc.origin = :manual AND NOT s.include_all"
                        ),
                        {"id": uid, "manual": SceneOrigin.MANUAL.value},
                    ).mappings().fetchall()
                )
                manual_scene_ids = {int(m["scene_id"]) for m in manual}
                effective = apply_manual_overrides(rule_members, manual)
                effective |= include_all_scene_ids(scenes)

                conn.execute(
                    text(
                        "DELETE FROM image_scenes "
                        "WHERE image_id = CAST(:id AS uuid) AND origin = :rule"
                    ),
                    {"id": uid, "rule": SceneOrigin.RULE.value},
                )
                for sid in sorted(rule_members - manual_scene_ids):
                    conn.execute(
                        text(
                            "INSERT INTO image_scenes "
                            "(image_id, scene_id, origin, is_member, updated_at) "
                            "VALUES (CAST(:iid AS uuid), :sid, :rule, true, now())"
                        ),
                        {"iid": uid, "sid": sid, "rule": SceneOrigin.RULE.value},
                    )

                code_by_id = {int(s["id"]): str(s["code"]) for s in scenes}
                scene_codes = [
                    code_by_id[sid]
                    for sid in sorted(effective)
                    if sid in code_by_id
                ]

                payload_version = int(img["payload_version"] or 1)
                if bump_version:
                    conn.execute(
                        text(
                            "UPDATE images SET payload_version = payload_version + 1, "
                            "updated_at = now() WHERE id = CAST(:id AS uuid)"
                        ),
                        {"id": uid},
                    )
                    payload_version += 1

            return {
                "image_id": uid,
                "scene_codes": scene_codes,
                "rule_member_ids": sorted(rule_members),
                "effective_ids": sorted(effective),
                "payload_version": payload_version,
            }
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(
                f"recompute_image_scenes: {type(exc).__name__}: {exc}"
            ) from exc

    def set_manual_scene_membership(
        self,
        image_id: UUID | str,
        scene_code: str,
        *,
        is_member: bool,
    ) -> dict[str, Any]:
        """Upsert origin=manual override then recompute (bumps payload_version).

        include_all scenes contain every image and take no overrides.
        """
        self.ensure_schema()
        from sqlalchemy import text

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
        return self.recompute_image_scenes(uid, bump_version=True)

    def map_category_to_scene_label(
        self,
        scene_code: str,
        category_id: int,
    ) -> dict[str, Any] | None:
        """Map general category to scene_labels via matching scene_rules (section 4.3)."""
        self.ensure_schema()
        from sqlalchemy import text

        engine = self._get_engine()
        with engine.connect() as conn:
            scene = conn.execute(
                text("SELECT id FROM scenes WHERE code = :c LIMIT 1"),
                {"c": scene_code},
            ).mappings().fetchone()
            if scene is None:
                return None
            sid = int(scene["id"])
            cat = conn.execute(
                text(
                    "SELECT id, path::text AS path, cuisines FROM categories "
                    "WHERE id = :cid"
                ),
                {"cid": int(category_id)},
            ).mappings().fetchone()
            if cat is None:
                return None
            ancestors = list(
                conn.execute(
                    text(
                        "SELECT id, path::text AS path, cuisines FROM categories "
                        "WHERE path @> CAST(:p AS ltree) ORDER BY nlevel(path)"
                    ),
                    {"p": cat["path"]},
                ).mappings().fetchall()
            )
            cuisines = effective_cuisines(cat, ancestors)
            rules = list(
                conn.execute(
                    text(
                        "SELECT id, scene_id, category_id, cuisine, label_id "
                        "FROM scene_rules WHERE scene_id = :sid"
                    ),
                    {"sid": sid},
                ).mappings().fetchall()
            )
            cat_ids = {int(r["category_id"]) for r in rules if r["category_id"] is not None}
            paths: dict[int, str] = {}
            if cat_ids:
                for r in conn.execute(
                    text(
                        "SELECT id, path::text AS path FROM categories "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(cat_ids)},
                ).mappings().fetchall():
                    paths[int(r["id"])] = r["path"]
            matching = []
            for rule in rules:
                rcat = rule.get("category_id")
                rpath = paths.get(int(rcat)) if rcat is not None else None
                if rule_matches_category(
                    image_category_id=int(category_id),
                    image_path=cat["path"],
                    image_cuisines=cuisines,
                    rule=rule,
                    rule_category_path=rpath,
                ):
                    matching.append(rule)
            label_id = most_specific_label_id(matching, paths)
            if label_id is None:
                return None
            lab = conn.execute(
                text(
                    "SELECT id, scene_id, code, name, sort_order, is_active "
                    "FROM scene_labels WHERE id = :lid"
                ),
                {"lid": label_id},
            ).mappings().fetchone()
        return _row_to_dict(lab) if lab else None
