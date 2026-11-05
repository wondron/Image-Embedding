from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.adapter.persistence.pg_common import (
    _as_uuid,
    _row_to_dict,
    _uuid_in_clause,
)
from app.domain.retrieval import errors as err
from app.domain.retrieval.image_ids import uuid_from_sha256


class PgImagesMixin:
    """images table CRUD / list / hydrate helpers."""

    def insert_image(self, row: dict[str, Any]) -> UUID | str:
        """Insert images row (uuid PK). ON CONFLICT sha256 → return existing id (A7)."""
        self.ensure_schema()
        from sqlalchemy import text

        sha = row.get("sha256")
        if isinstance(sha, str):
            sha = bytes.fromhex(sha)
        if not isinstance(sha, (bytes, bytearray)) or len(sha) < 16:
            raise err.validation_error("insert_image requires sha256 bytes")
        image_id = row.get("id") or uuid_from_sha256(bytes(sha))
        uid = str(_as_uuid(image_id))
        original_url = str(row.get("original_url") or "").strip()
        thumb_url = str(row.get("thumb_url") or "").strip()
        if not original_url or not thumb_url:
            raise err.validation_error("original_url and thumb_url required")
        source_type = str(row.get("source_type") or "user_upload")
        license_v = str(row.get("license") or "unknown")
        label_status = str(row.get("label_status") or "unlabeled")
        category_id = row.get("category_id")
        food_state = row.get("food_state")
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                existing = conn.execute(
                    text(
                        "SELECT id FROM images WHERE sha256 = :sha LIMIT 1"
                    ),
                    {"sha": bytes(sha)},
                ).fetchone()
                if existing is not None:
                    return _as_uuid(existing[0])
                conn.execute(
                    text(
                        "INSERT INTO images ("
                        "id, sha256, original_url, thumb_url, width, height, "
                        "source_type, license, category_id, food_state, "
                        "label_status, payload_version"
                        ") VALUES ("
                        "CAST(:id AS uuid), :sha, :ou, :tu, :w, :h, "
                        ":st, :lic, :cid, :fs, :ls, 1"
                        ")"
                    ),
                    {
                        "id": uid,
                        "sha": bytes(sha),
                        "ou": original_url,
                        "tu": thumb_url,
                        "w": row.get("width"),
                        "h": row.get("height"),
                        "st": source_type,
                        "lic": license_v,
                        "cid": category_id,
                        "fs": food_state,
                        "ls": label_status,
                    },
                )
            return _as_uuid(uid)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(
                f"insert_image: {type(exc).__name__}: {exc}"
            ) from exc

    def update_image(self, image_id: UUID | str, fields: dict[str, Any]) -> None:
        """Patch allowed image columns; bump payload_version on membership-relevant fields."""
        self.ensure_schema()
        from sqlalchemy import text

        allowed = {
            "original_url",
            "thumb_url",
            "width",
            "height",
            "category_id",
            "food_state",
            "label_status",
            "source_type",
            "license",
        }
        bump_keys = {"category_id", "food_state", "label_status"}
        sets: list[str] = []
        params: dict[str, Any] = {"id": str(_as_uuid(image_id))}
        bump = False
        for key, val in (fields or {}).items():
            if key not in allowed:
                continue
            sets.append(f"{key} = :{key}")
            params[key] = val
            if key in bump_keys:
                bump = True
        if not sets:
            return
        sets.append("updated_at = now()")
        if bump:
            sets.append("payload_version = payload_version + 1")
        sql = (
            "UPDATE images SET "
            + ", ".join(sets)
            + " WHERE id = CAST(:id AS uuid)"
        )
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text(sql), params)
            if bump:
                self.recompute_image_scenes(image_id, bump_version=False)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(
                f"update_image: {type(exc).__name__}: {exc}"
            ) from exc

    def mark_deleted(self, image_id: UUID | str) -> None:
        """Port alias: enqueue hard-delete into image_deletions (§2.5)."""
        self.enqueue_image_deletion(image_id)

    def get_active_by_ids(
        self,
        image_ids: list[UUID | str],
        *,
        scene: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Hydrate images by uuid; optional scene membership check via image_scenes."""
        self.ensure_schema()
        from sqlalchemy import text

        if not image_ids:
            return {}
        ids = [str(_as_uuid(i)) for i in image_ids]
        id_sql, id_params = _uuid_in_clause(ids)
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT i.*, "
                        "cn.name AS category_name, "
                        "hg.id AS heating_group_id, hg.code AS heating_group_code, "
                        "hg.name AS heating_group_name "
                        "FROM images i "
                        "LEFT JOIN categories c ON c.id = i.category_id "
                        "LEFT JOIN category_names cn "
                        "  ON cn.category_id = c.id AND cn.lang = 'zh' "
                        " AND cn.kind = 'primary' "
                        "LEFT JOIN LATERAL ("
                        "  SELECT h.id, h.code, h.name FROM categories anc "
                        "  JOIN heating_groups h ON h.id = anc.heating_group_id "
                        "  WHERE c.path IS NOT NULL AND anc.path @> c.path "
                        "    AND anc.heating_group_id IS NOT NULL "
                        "  ORDER BY nlevel(anc.path) DESC LIMIT 1"
                        ") hg ON true "
                        f"WHERE i.id IN {id_sql}"
                    ),
                    id_params,
                ).mappings().fetchall()

                member_ids: set[str] | None = None
                if scene:
                    mparams = dict(id_params)
                    mparams["code"] = scene
                    mrows = conn.execute(
                        text(
                            "SELECT isc.image_id::text AS image_id "
                            "FROM image_scenes isc "
                            "JOIN scenes s ON s.id = isc.scene_id "
                            "WHERE s.code = :code AND isc.is_member = true "
                            f"  AND isc.image_id IN {id_sql}"
                        ),
                        mparams,
                    ).fetchall()
                    member_ids = {str(r[0]) for r in mrows}

                sc_rows = conn.execute(
                    text(
                        "SELECT isc.image_id::text AS image_id, "
                        "array_agg(s.code ORDER BY s.id) AS codes "
                        "FROM image_scenes isc "
                        "JOIN scenes s ON s.id = isc.scene_id "
                        "WHERE isc.is_member = true "
                        f"  AND isc.image_id IN {id_sql} "
                        "GROUP BY isc.image_id"
                    ),
                    id_params,
                ).mappings().fetchall()
                codes_by = {r["image_id"]: list(r["codes"] or []) for r in sc_rows}

            out: dict[str, dict[str, Any]] = {}
            for row in rows:
                d = _row_to_dict(row)
                iid = str(d["id"])
                if member_ids is not None and iid not in member_ids:
                    continue
                d["scenes"] = codes_by.get(iid, [])
                if d.get("heating_group_id") is not None:
                    d["heating_group"] = {
                        "id": d.get("heating_group_id"),
                        "code": d.get("heating_group_code"),
                        "name": d.get("heating_group_name"),
                    }
                else:
                    d["heating_group"] = None
                out[iid] = d
            return out
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.search_mysql_failed(
                f"get_active_by_ids: {type(exc).__name__}: {exc}"
            ) from exc

    def list_images(
        self,
        *,
        scene: str,
        page: int = 1,
        page_size: int = 24,
        q: str | None = None,
        category: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginate images that are effective members of scene code."""
        self.ensure_schema()
        from sqlalchemy import text

        page_i = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        offset = (page_i - 1) * size
        where = [
            "s.code = :scene",
            "isc.is_member = true",
        ]
        params: dict[str, Any] = {"scene": scene, "lim": size, "off": offset}
        if category:
            where.append(
                "(c.code = :cat OR cn.name ILIKE :cat_like OR "
                "CAST(i.category_id AS text) = :cat)"
            )
            params["cat"] = category
            params["cat_like"] = f"%{category}%"
        if q:
            where.append(
                "(cn.name ILIKE :q OR i.original_url ILIKE :q OR "
                "CAST(i.id AS text) ILIKE :q)"
            )
            params["q"] = f"%{q}%"
        where_sql = " AND ".join(where)
        base_from = (
            "FROM images i "
            "JOIN image_scenes isc ON isc.image_id = i.id "
            "JOIN scenes s ON s.id = isc.scene_id "
            "LEFT JOIN categories c ON c.id = i.category_id "
            "LEFT JOIN category_names cn "
            "  ON cn.category_id = c.id AND cn.lang = 'zh' AND cn.kind = 'primary' "
        )
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                total = conn.execute(
                    text(f"SELECT COUNT(*) {base_from} WHERE {where_sql}"),
                    params,
                ).scalar_one()
                rows = conn.execute(
                    text(
                        "SELECT i.*, cn.name AS category_name, "
                        "hg.id AS heating_group_id, hg.code AS heating_group_code, "
                        "hg.name AS heating_group_name "
                        f"{base_from} "
                        "LEFT JOIN LATERAL ("
                        "  SELECT h.id, h.code, h.name FROM categories anc "
                        "  JOIN heating_groups h ON h.id = anc.heating_group_id "
                        "  WHERE c.path IS NOT NULL AND anc.path @> c.path "
                        "    AND anc.heating_group_id IS NOT NULL "
                        "  ORDER BY nlevel(anc.path) DESC LIMIT 1"
                        ") hg ON true "
                        f"WHERE {where_sql} "
                        "ORDER BY i.created_at DESC "
                        "LIMIT :lim OFFSET :off"
                    ),
                    params,
                ).mappings().fetchall()
                ids = [str(r["id"]) for r in rows]
                codes_by: dict[str, list[str]] = {}
                if ids:
                    id_sql, id_params = _uuid_in_clause(ids)
                    sc_rows = conn.execute(
                        text(
                            "SELECT isc.image_id::text AS image_id, "
                            "array_agg(s.code ORDER BY s.id) AS codes "
                            "FROM image_scenes isc "
                            "JOIN scenes s ON s.id = isc.scene_id "
                            "WHERE isc.is_member = true "
                            f"  AND isc.image_id IN {id_sql} "
                            "GROUP BY isc.image_id"
                        ),
                        id_params,
                    ).mappings().fetchall()
                    codes_by = {
                        r["image_id"]: list(r["codes"] or []) for r in sc_rows
                    }
            items: list[dict[str, Any]] = []
            for row in rows:
                d = _row_to_dict(row)
                iid = str(d["id"])
                d["scenes"] = codes_by.get(iid, [])
                if d.get("heating_group_id") is not None:
                    d["heating_group"] = {
                        "id": d.get("heating_group_id"),
                        "code": d.get("heating_group_code"),
                        "name": d.get("heating_group_name"),
                    }
                else:
                    d["heating_group"] = None
                items.append(d)
            return items, int(total or 0)
        except Exception as exc:
            raise err.list_mysql_failed(
                f"list_images: {type(exc).__name__}: {exc}"
            ) from exc

    def get_by_id(self, image_id: UUID | str) -> dict[str, Any] | None:
        row = self.get_image_by_uuid(image_id)
        if not row:
            return None
        # attach scene codes
        codes = self.get_effective_scene_codes(image_id)
        row["scenes"] = codes
        return row

    def get_image_by_uuid(self, image_id: UUID | str) -> dict[str, Any] | None:
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM images WHERE id = CAST(:id AS uuid)"),
                    {"id": uid},
                ).mappings().fetchone()
            return _row_to_dict(row) if row else None
        except Exception as exc:
            raise err.search_mysql_failed(f"{type(exc).__name__}: {exc}") from exc
