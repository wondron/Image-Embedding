from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.enums import FoodState, LabelStatus, License, NameKind, SourceType
from app.db.common import (
    _as_uuid,
    _row_to_dict,
    _uuid_in_clause,
)
from app.services.retrieval import errors as err
from app.services.retrieval.enum_fields import coerce_enum, coerce_optional_enum
from app.services.retrieval.image_ids import uuid_from_sha256
from app.services.retrieval.text_fields import normalize_description

# Category display name (zh primary) + nearest ancestor's heating group.
# Expects aliases i (images) and c (categories); binds :primary_kind.
_CATEGORY_NAME_JOIN = (
    "LEFT JOIN category_names cn "
    "  ON cn.category_id = c.id AND cn.lang = 'zh' AND cn.kind = :primary_kind "
)
_HEATING_GROUP_JOIN = (
    "LEFT JOIN LATERAL ("
    "  SELECT h.id, h.code, h.name FROM categories anc "
    "  JOIN heating_groups h ON h.id = anc.heating_group_id "
    "  WHERE c.path IS NOT NULL AND anc.path @> c.path "
    "    AND anc.heating_group_id IS NOT NULL "
    "  ORDER BY nlevel(anc.path) DESC LIMIT 1"
    ") hg ON true "
)
_HYDRATE_SELECT = (
    "SELECT i.*, cn.name AS category_name, "
    "hg.id AS heating_group_id, hg.code AS heating_group_code, "
    "hg.name AS heating_group_name "
)

# Enum columns -> enum class (validated before they reach the CHECK constraints)
_ENUM_COLUMNS = {
    "source_type": SourceType,
    "license": License,
    "label_status": LabelStatus,
}


def _check_label_matches_category(label: LabelStatus, category_id: Any) -> None:
    """Mirror ck_images_label_matches_category: unlabeled iff no category."""
    if (label is LabelStatus.UNLABELED) != (category_id is None):
        raise err.validation_error(
            "label_status='unlabeled' must go with category_id IS NULL "
            f"(got label_status={label.value!r}, category_id={category_id!r})"
        )


def _positive_int(value: Any, field: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise err.validation_error(f"{field} must be a positive integer, got {value!r}") from None
    if n <= 0:
        raise err.validation_error(f"{field} must be a positive integer, got {value!r}")
    return n


def _decorate(row: Any, codes_by: dict[str, list[str]]) -> dict[str, Any]:
    """Row -> wire dict with scenes + heating_group object."""
    d = _row_to_dict(row)
    d["scenes"] = codes_by.get(str(d["id"]), [])
    if d.get("heating_group_id") is not None:
        d["heating_group"] = {
            "id": d.get("heating_group_id"),
            "code": d.get("heating_group_code"),
            "name": d.get("heating_group_name"),
        }
    else:
        d["heating_group"] = None
    return d


class PgImagesMixin:
    """images table CRUD / list / hydrate helpers."""

    def insert_image(self, row: dict[str, Any]) -> UUID | str:
        """Insert images row (uuid PK = sha256[:16]).

        Identical bytes are stored once: on conflict the existing id is returned
        and the first upload's columns are kept (app.models.image.Image).
        """
        self.ensure_schema()
        from sqlalchemy import text

        sha = row.get("sha256")
        if isinstance(sha, str):
            sha = bytes.fromhex(sha)
        if not isinstance(sha, (bytes, bytearray)) or len(sha) != 32:
            raise err.validation_error("insert_image requires 32-byte sha256")
        sha = bytes(sha)
        image_id = row.get("id") or uuid_from_sha256(sha)
        uid = str(_as_uuid(image_id))
        original_url = str(row.get("original_url") or "").strip()
        thumb_url = str(row.get("thumb_url") or "").strip()
        if not original_url or not thumb_url:
            raise err.validation_error("original_url and thumb_url required")
        width = _positive_int(row.get("width"), "width")
        height = _positive_int(row.get("height"), "height")
        source_type = coerce_enum(
            SourceType, row.get("source_type"), field="source_type", default=SourceType.USER_UPLOAD
        )
        license_v = coerce_enum(License, row.get("license"), field="license", default=License.UNKNOWN)
        label_status = coerce_enum(
            LabelStatus, row.get("label_status"), field="label_status", default=LabelStatus.UNLABELED
        )
        food_state = coerce_optional_enum(FoodState, row.get("food_state"), field="food_state")
        category_id = row.get("category_id")
        _check_label_matches_category(label_status, category_id)
        description = normalize_description(row.get("description"))

        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                inserted = conn.execute(
                    text(
                        "INSERT INTO images ("
                        "id, sha256, original_url, thumb_url, width, height, "
                        "source_type, license, category_id, food_state, label_status, description"
                        ") VALUES ("
                        "CAST(:id AS uuid), :sha, :ou, :tu, :w, :h, "
                        ":st, :lic, :cid, :fs, :ls, :desc"
                        ") ON CONFLICT DO NOTHING RETURNING id"
                    ),
                    {
                        "id": uid,
                        "sha": sha,
                        "ou": original_url,
                        "tu": thumb_url,
                        "w": width,
                        "h": height,
                        "st": source_type.value,
                        "lic": license_v.value,
                        "cid": category_id,
                        "fs": food_state.value if food_state else None,
                        "ls": label_status.value,
                        "desc": description,
                    },
                ).fetchone()
                if inserted is not None:
                    return _as_uuid(inserted[0])
                existing = conn.execute(
                    text("SELECT id FROM images WHERE sha256 = :sha LIMIT 1"),
                    {"sha": sha},
                ).fetchone()
            if existing is None:
                raise err.index_mysql_failed(
                    f"insert_image: conflict on id={uid} but no row with this sha256"
                )
            return _as_uuid(existing[0])
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
        from sqlalchemy.exc import IntegrityError

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
            "description",
        }
        # description is not in the Qdrant payload, so it does not bump payload_version
        bump_keys = {"category_id", "food_state", "label_status"}
        sets: list[str] = []
        params: dict[str, Any] = {"id": str(_as_uuid(image_id))}
        bump = False
        for key, val in (fields or {}).items():
            if key not in allowed:
                continue
            if key in _ENUM_COLUMNS:
                val = coerce_enum(_ENUM_COLUMNS[key], val, field=key).value
            elif key == "food_state":
                fs = coerce_optional_enum(FoodState, val, field=key)
                val = fs.value if fs else None
            elif key in ("width", "height"):
                val = _positive_int(val, key)
            elif key == "description":
                val = normalize_description(val)
            sets.append(f"{key} = :{key}")
            params[key] = val
            if key in bump_keys:
                bump = True
        if not sets:
            return
        if "label_status" in params and "category_id" in params:
            _check_label_matches_category(LabelStatus(params["label_status"]), params["category_id"])
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
        except IntegrityError as exc:
            # e.g. only one of label_status / category_id patched and the pair
            # no longer satisfies ck_images_label_matches_category
            raise err.validation_error(
                f"update_image violates a constraint: {exc.orig}"
            ) from exc
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
        """Hydrate images by uuid; optional scene membership filter."""
        self.ensure_schema()
        from sqlalchemy import text

        if not image_ids:
            return {}
        ids = [str(_as_uuid(i)) for i in image_ids]
        id_sql, id_params = _uuid_in_clause(ids)
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                member_ids: set[str] | None = None
                if scene:
                    member_ids = self._scene_member_ids(conn, scene, id_sql, id_params)
                    if not member_ids:
                        return {}
                rows = conn.execute(
                    text(
                        _HYDRATE_SELECT
                        + "FROM images i "
                        "LEFT JOIN categories c ON c.id = i.category_id "
                        + _CATEGORY_NAME_JOIN
                        + _HEATING_GROUP_JOIN
                        + f"WHERE i.id IN {id_sql}"
                    ),
                    {**id_params, "primary_kind": NameKind.PRIMARY.value},
                ).mappings().fetchall()
                codes_by = self._scene_codes_by_image(conn, ids)

            out: dict[str, dict[str, Any]] = {}
            for row in rows:
                iid = str(row["id"])
                if member_ids is not None and iid not in member_ids:
                    continue
                out[iid] = _decorate(row, codes_by)
            return out
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.search_mysql_failed(
                f"get_active_by_ids: {type(exc).__name__}: {exc}"
            ) from exc

    def _scene_member_ids(
        self, conn, scene: str, id_sql: str, id_params: dict[str, str]
    ) -> set[str]:
        """Which of the given ids are members of scene code (empty if unknown)."""
        from sqlalchemy import text

        meta = self._scene_membership_row(conn, scene)
        if meta is None:
            return set()
        if meta["include_all"]:
            if not meta["is_active"]:
                return set()
            rows = conn.execute(
                text(f"SELECT id::text FROM images WHERE id IN {id_sql}"), id_params
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    "SELECT image_id::text FROM image_scenes "
                    f"WHERE scene_id = :sid AND is_member AND image_id IN {id_sql}"
                ),
                {**id_params, "sid": int(meta["id"])},
            ).fetchall()
        return {str(r[0]) for r in rows}

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
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                meta = self._scene_membership_row(conn, scene)
                if meta is None or (meta["include_all"] and not meta["is_active"]):
                    return [], 0

                params: dict[str, Any] = {
                    "lim": size,
                    "off": offset,
                    "primary_kind": NameKind.PRIMARY.value,
                }
                if meta["include_all"]:
                    member_join = ""
                else:
                    # served by ix_image_scenes_scene_member (partial on is_member)
                    member_join = (
                        "JOIN image_scenes isc ON isc.image_id = i.id "
                        "AND isc.scene_id = :sid AND isc.is_member "
                    )
                    params["sid"] = int(meta["id"])
                where = ["true"]
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
                    + member_join
                    + "LEFT JOIN categories c ON c.id = i.category_id "
                    + _CATEGORY_NAME_JOIN
                )
                total = conn.execute(
                    text(f"SELECT COUNT(*) {base_from} WHERE {where_sql}"),
                    params,
                ).scalar_one()
                rows = conn.execute(
                    text(
                        _HYDRATE_SELECT
                        + base_from
                        + _HEATING_GROUP_JOIN
                        + f"WHERE {where_sql} "
                        "ORDER BY i.created_at DESC "
                        "LIMIT :lim OFFSET :off"
                    ),
                    params,
                ).mappings().fetchall()
                codes_by = self._scene_codes_by_image(conn, [str(r["id"]) for r in rows])
            return [_decorate(row, codes_by) for row in rows], int(total or 0)
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
