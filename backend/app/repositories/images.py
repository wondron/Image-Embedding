from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.enums import (
    DEFAULT_LICENSE,
    DEFAULT_SOURCE_TYPE,
    LICENSE,
    SOURCE_TYPE,
    LabelStatus,
    ModelStatus,
    NameKind,
    StrEnum,
)
from app.db.common import (
    _as_uuid,
    _row_to_dict,
    _uuid_in_clause,
)
from app.core import errors as err
from app.repositories.catalog_lock import lock_catalog_for_read
from app.repositories.foods import FOOD_JOIN, FOOD_SELECT, write_image_food
from app.repositories.scenes import _recompute
from app.services.retrieval.enum_fields import coerce_enum
from app.services.retrieval.food_fields import FOOD_FIELDS, coerce_food_field
from app.services.retrieval.image_fields import resolve_label_status
from app.services.retrieval.image_ids import uuid_from_sha256
from app.services.retrieval.text_fields import normalize_description, normalize_tags

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
# + the food annotation (image_foods, needs FOOD_JOIN)
_HYDRATE_SELECT = (
    "SELECT i.*, cn.name AS category_name, "
    "hg.id AS heating_group_id, hg.code AS heating_group_code, "
    f"hg.name AS heating_group_name, {FOOD_SELECT} "
)
# The ledger of one image: which model versions' collections have its point, at which version
_LEDGER_SQL = (
    "SELECT e.model_id, m.name, m.version, m.collection, m.status AS model_status, "
    "e.synced_version, e.embedded_at "
    "FROM image_embeddings e JOIN embedding_models m ON m.id = e.model_id "
    "WHERE e.image_id = CAST(:id AS uuid) ORDER BY e.model_id"
)

# Required enum columns -> system enum class (also has a CHECK) or editable enum name (only this check)
_ENUM_COLUMNS: dict[str, type[StrEnum] | str] = {
    "source_type": SOURCE_TYPE,
    "license": LICENSE,
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
        """Insert images row (uuid PK = sha256[:16]) and, when given, its image_foods row.

        Identical bytes are stored once: on conflict the existing id is returned
        and the first upload's columns (food annotation included) are kept.
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
            SOURCE_TYPE, row.get("source_type"), field="source_type", default=DEFAULT_SOURCE_TYPE
        )
        license_v = coerce_enum(LICENSE, row.get("license"), field="license", default=DEFAULT_LICENSE)
        label_status = coerce_enum(
            LabelStatus, row.get("label_status"), field="label_status", default=LabelStatus.UNLABELED
        )
        food = {key: coerce_food_field(key, row.get(key)) for key in FOOD_FIELDS}
        category_id = row.get("category_id")
        _check_label_matches_category(label_status, category_id)
        description = normalize_description(row.get("description"))
        tags = normalize_tags(row.get("tags"))

        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                inserted = conn.execute(
                    text(
                        "INSERT INTO images ("
                        "id, sha256, original_url, thumb_url, width, height, "
                        "source_type, license, category_id, label_status, description, tags"
                        ") VALUES ("
                        "CAST(:id AS uuid), :sha, :ou, :tu, :w, :h, "
                        ":st, :lic, :cid, :ls, :desc, CAST(:tags AS text[])"
                        ") ON CONFLICT DO NOTHING RETURNING id"
                    ),
                    {
                        "id": uid,
                        "sha": sha,
                        "ou": original_url,
                        "tu": thumb_url,
                        "w": width,
                        "h": height,
                        "st": source_type,
                        "lic": license_v,
                        "cid": category_id,
                        "ls": label_status.value,
                        "desc": description,
                        "tags": tags,
                    },
                ).fetchone()
                if inserted is not None:
                    if any(value is not None for value in food.values()):  # optional: no row when unknown
                        write_image_food(conn, uid, food)
                    return _as_uuid(inserted[0])
                existing = conn.execute(
                    text("SELECT id FROM images WHERE sha256 = :sha LIMIT 1"),
                    {"sha": sha},
                ).fetchone()
            if existing is None:
                raise err.index_postgres_failed(
                    f"insert_image: conflict on id={uid} but no row with this sha256"
                )
            return _as_uuid(existing[0])
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"insert_image: {type(exc).__name__}: {exc}"
            ) from exc

    def update_image(self, image_id: UUID | str, fields: dict[str, Any]) -> None:
        """Patch allowed image columns and food fields (image_foods, created on first use).

        Bumps payload_version on Qdrant-payload fields; food fields touch only their own
        columns and the image's updated_at.
        """
        self.ensure_schema()
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        allowed = {
            "original_url",
            "thumb_url",
            "width",
            "height",
            "category_id",
            "label_status",
            "source_type",
            "license",
            "description",
            "tags",
            *FOOD_FIELDS,
        }
        # The Qdrant payload holds only these; description, tags and the other food fields are
        # not in it, so they do not bump payload_version
        bump_keys = {"category_id", "food_state", "label_status"}
        sets: list[str] = []
        params: dict[str, Any] = {"id": str(_as_uuid(image_id))}
        food: dict[str, Any] = {}  # -> image_foods
        bump = False
        for key, val in (fields or {}).items():
            if key not in allowed:
                continue
            if key in bump_keys:
                bump = True
            if key in FOOD_FIELDS:
                food[key] = coerce_food_field(key, val)
                continue
            if key in _ENUM_COLUMNS:
                val = str(coerce_enum(_ENUM_COLUMNS[key], val, field=key))
            elif key in ("width", "height"):
                val = _positive_int(val, key)
            elif key == "description":
                val = normalize_description(val)
            elif key == "tags":
                val = normalize_tags(val)
            placeholder = f"CAST(:{key} AS text[])" if key == "tags" else f":{key}"
            sets.append(f"{key} = {placeholder}")
            params[key] = val
        if not sets and not food:
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
                matched = conn.execute(text(sql), params).rowcount
                if matched and food:  # same transaction; a missing image gets no food row
                    write_image_food(conn, params["id"], food)
            if bump:
                self.recompute_image_scenes(image_id, bump_version=False)
        except err.AppError:
            raise
        except IntegrityError as exc:
            # e.g. only one of label_status / category_id patched and the pair
            # no longer satisfies ck_images_label_matches_category
            raise err.validation_error(
                f"update_image violates a constraint: {exc.orig}"
            ) from exc
        except Exception as exc:
            raise err.index_postgres_failed(
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
                        + FOOD_JOIN
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
        except err.AppError:
            raise
        except Exception as exc:
            raise err.search_postgres_failed(
                f"get_active_by_ids: {type(exc).__name__}: {exc}"
            ) from exc

    def _scene_member_ids(
        self, conn, scene: str, id_sql: str, id_params: dict[str, str]
    ) -> set[str]:
        """Which of the given ids are members of scene code (empty if unknown or inactive)."""
        from sqlalchemy import text

        meta = self._scene_membership_row(conn, scene)
        if meta is None or not meta["is_active"]:
            return set()
        if meta["include_all"]:
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
        tag: str | None = None,
        category_id: int | None = None,
        label_status: str | None = None,
        out_of_sync: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginate images that are effective members of scene code.

        q: substring of the category name, any tag, the original URL or the id.
        tag: exact match on one element of images.tags.
        category_id: that category and every category below it (an unlabeled image never matches).
        label_status: exact value (unlabeled / auto / verified).
        out_of_sync: only images whose current-model ledger row is missing or behind their
        payload_version - they need a push, or a vector. By the ledger, not by reading Qdrant:
        a point deleted behind the ledger's back is not found this way.
        """
        self.ensure_schema()
        from sqlalchemy import text

        page_i = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        offset = (page_i - 1) * size
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                meta = self._scene_membership_row(conn, scene)
                if meta is None or not meta["is_active"]:  # an inactive scene has no members
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
                if tag:
                    # served by ix_images_tags_gin (@>, not = ANY, so the index applies)
                    where.append("i.tags @> ARRAY[CAST(:tag AS text)]")
                    params["tag"] = tag
                if category_id is not None:
                    # the subtree: ltree <@ (ix_categories_path_gist); an unknown id matches nothing
                    where.append("c.path <@ (SELECT path FROM categories WHERE id = :cat_id)")
                    params["cat_id"] = int(category_id)
                if label_status:
                    where.append("i.label_status = :label_status")
                    params["label_status"] = label_status
                if out_of_sync:
                    # served by pk_image_embeddings (model_id, image_id); no current model: every image
                    where.append(
                        "NOT EXISTS (SELECT 1 FROM image_embeddings e "
                        "JOIN embedding_models m ON m.id = e.model_id AND m.status = :current_status "
                        "WHERE e.image_id = i.id AND e.synced_version >= i.payload_version)"
                    )
                    params["current_status"] = ModelStatus.CURRENT.value
                if q:
                    where.append(
                        "(cn.name ILIKE :q OR i.original_url ILIKE :q OR "
                        "CAST(i.id AS text) ILIKE :q OR "
                        "EXISTS (SELECT 1 FROM unnest(i.tags) AS t(tag) WHERE t.tag ILIKE :q))"
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
                        + FOOD_JOIN
                        + f"WHERE {where_sql} "
                        "ORDER BY i.created_at DESC "
                        "LIMIT :lim OFFSET :off"
                    ),
                    params,
                ).mappings().fetchall()
                codes_by = self._scene_codes_by_image(conn, [str(r["id"]) for r in rows])
            return [_decorate(row, codes_by) for row in rows], int(total or 0)
        except Exception as exc:
            raise err.list_postgres_failed(
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

    def get_image_file_urls(self, image_id: UUID | str) -> dict[str, Any] | None:
        """{original_url, thumb_url, deleted} of a stored image, or of one deleted whose files wait
        in image_deletions for the cleanup (deleted=True); None when neither knows the id."""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT original_url, thumb_url, false AS deleted FROM images WHERE id = CAST(:id AS uuid) "
                        "UNION ALL "
                        "SELECT original_url, thumb_url, true FROM image_deletions WHERE image_id = CAST(:id AS uuid) "
                        "ORDER BY deleted LIMIT 1"
                    ),
                    {"id": str(_as_uuid(image_id))},
                ).mappings().fetchone()
            return dict(row) if row else None
        except Exception as exc:
            raise err.index_postgres_failed(f"get_image_file_urls: {type(exc).__name__}: {exc}") from exc

    def get_image_by_uuid(self, image_id: UUID | str) -> dict[str, Any] | None:
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(f"SELECT i.*, {FOOD_SELECT} FROM images i {FOOD_JOIN}WHERE i.id = CAST(:id AS uuid)"),
                    {"id": uid},
                ).mappings().fetchone()
            return _row_to_dict(row) if row else None
        except Exception as exc:
            raise err.search_postgres_failed(f"{type(exc).__name__}: {exc}") from exc

    # -- one image as the gallery admin reads and edits it ----------------------------

    def _image_detail(self, conn: Any, uid: str) -> dict[str, Any] | None:
        """Row + category (zh name, code) + heating group + food + effective scenes + ledger."""
        from sqlalchemy import text

        row = conn.execute(
            text(
                _HYDRATE_SELECT
                + ", c.code AS category_code "
                "FROM images i "
                "LEFT JOIN categories c ON c.id = i.category_id "
                + _CATEGORY_NAME_JOIN
                + _HEATING_GROUP_JOIN
                + FOOD_JOIN
                + "WHERE i.id = CAST(:id AS uuid)"
            ),
            {"id": uid, "primary_kind": NameKind.PRIMARY.value},
        ).mappings().fetchone()
        if row is None:
            return None
        detail = _decorate(row, self._scene_codes_by_image(conn, [uid]))
        detail["embeddings"] = [
            _row_to_dict(ledger) for ledger in conn.execute(text(_LEDGER_SQL), {"id": uid}).mappings()
        ]
        return detail

    def get_image_detail(self, image_id: UUID | str) -> dict[str, Any] | None:
        """One image with everything the gallery shows of it, or None. One snapshot: the payload
        fields and the scenes are read together, so they can be compared with the vector store."""
        self.ensure_schema()
        uid = str(_as_uuid(image_id))
        try:
            with self._get_engine().connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    return self._image_detail(conn, uid)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.search_postgres_failed(f"get_image_detail: {type(exc).__name__}: {exc}") from exc

    def patch_image(self, image_id: UUID | str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply a gallery patch (normalized by services.retrieval.image_fields) in one
        transaction; returns the image detail as committed.

        Takes the catalog read lock, then the image row lock (the order of every image writer),
        so the category checked here cannot be deleted or disabled before the commit. Settles
        label_status (image_fields.resolve_label_status), bumps payload_version once when a
        payload field (category_id, label_status, food_state) actually changes, and rewrites the
        image's rule memberships when the category changes. The vector store is not touched:
        the HTTP layer pushes the payload after the commit.
        """
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        try:
            with self._get_engine().begin() as conn:
                lock_catalog_for_read(conn)
                current = conn.execute(
                    text(
                        "SELECT i.category_id, i.label_status, i.payload_version, f.food_state "
                        "FROM images i LEFT JOIN image_foods f ON f.image_id = i.id "
                        "WHERE i.id = CAST(:id AS uuid) FOR UPDATE OF i"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
                if current is None:
                    raise err.image_not_found(f"image {uid} does not exist")

                category_id = fields["category_id"] if "category_id" in fields else current["category_id"]
                category_changed = category_id != current["category_id"]
                if category_changed and category_id is not None:
                    category = conn.execute(
                        text("SELECT is_active FROM categories WHERE id = :cid"), {"cid": category_id}
                    ).fetchone()
                    if category is None:
                        raise err.validation_error(f"category_id {category_id} does not exist")
                    if not category[0]:
                        raise err.validation_error(
                            f"category {category_id} is inactive: it is no longer used for labelling"
                        )
                label = resolve_label_status(
                    current_label=current["label_status"],
                    current_category_id=current["category_id"],
                    new_category_id=category_id,
                    requested=fields.get("label_status"),
                )
                label_changed = label.value != current["label_status"]
                food = {key: fields[key] for key in FOOD_FIELDS if key in fields}
                food_state_changed = "food_state" in food and food["food_state"] != current["food_state"]

                sets = ["updated_at = now()"]
                params: dict[str, Any] = {"id": uid}
                if category_changed:
                    sets.append("category_id = :category_id")
                    params["category_id"] = category_id
                if label_changed:
                    sets.append("label_status = :label_status")
                    params["label_status"] = label.value
                if "description" in fields:
                    sets.append("description = :description")
                    params["description"] = fields["description"]
                if "tags" in fields:
                    sets.append("tags = CAST(:tags AS text[])")
                    params["tags"] = fields["tags"]
                if category_changed or label_changed or food_state_changed:
                    sets.append("payload_version = payload_version + 1")
                conn.execute(
                    text(f"UPDATE images SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"), params
                )
                if food:
                    write_image_food(conn, uid, food)
                if category_changed:
                    # Rule memberships follow the category; the version is already bumped above
                    image = {"id": uid, "category_id": category_id, "payload_version": current["payload_version"]}
                    _recompute(conn, [image], bump=False)
                return self._image_detail(conn, uid)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(f"patch_image: {type(exc).__name__}: {exc}") from exc
