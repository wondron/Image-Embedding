"""PostgreSQL metadata adapter (PG v0.1 contract §2).

M2: replace MySQL with 12-table PG schema.
Schema ownership: Alembic (`alembic upgrade head`); this adapter pings /
verifies tables and supplies CRUD stubs + model/embedding helpers for stack wiring.

ASSUMPTION A7 / id policy: images.id = uuid_from_bytes(sha256(original)[0:16]);
retain content-hash bytes; do NOT rewrite RFC 4122 version/variant nibbles.

categories.path: DB trigger `categories_set_path` (migration); app never writes path;
forbid attaching under own descendant.

VectorStorePort is uuid (algo M3). Adapter persistence/qdrant_store thin-wraps
domain.common.qdrant_store.QdrantStore — no second client.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.domain.retrieval import errors as err
from app.domain.retrieval.image_ids import uuid_from_sha256
from app.domain.retrieval.ports import MetadataStorePort
from app.domain.retrieval.scene_membership import (
    apply_manual_overrides,
    compute_rule_member_scene_ids,
    effective_cuisines,
    most_specific_label_id,
    rule_matches_category,
)

logger = logging.getLogger("pg_metadata")

REQUIRED_TABLES = (
    "heating_groups",
    "categories",
    "category_names",
    "images",
    "image_deletions",
    "scenes",
    "scene_labels",
    "scene_rules",
    "image_scenes",
    "embedding_models",
    "image_embeddings",
    "external_candidates",
)


# uuid_from_sha256 imported from domain.retrieval.image_ids (A7)


def _iso(val: Any) -> Any:
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return val


def _as_uuid(image_id: UUID | str) -> uuid.UUID:
    if isinstance(image_id, uuid.UUID):
        return image_id
    return uuid.UUID(str(image_id).strip())


def _uuid_in_clause(image_ids: list[str]) -> tuple[str, dict[str, str]]:
    """Expand uuid list to IN (:id0, :id1, ...) for driver-safe binding."""
    if not image_ids:
        return "(NULL)", {}
    params: dict[str, str] = {}
    parts: list[str] = []
    for i, uid in enumerate(image_ids):
        key = f"id{i}"
        params[key] = uid
        parts.append(f"CAST(:{key} AS uuid)")
    return "(" + ", ".join(parts) + ")", params



def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "_mapping"):
        out = dict(row._mapping)
    else:
        out = dict(row)
    for key in (
        "created_at",
        "updated_at",
        "deleted_at",
        "embedded_at",
        "activated_at",
        "reviewed_at",
    ):
        if key in out:
            out[key] = _iso(out[key])
    for key in ("id", "image_id"):
        if key in out and isinstance(out[key], uuid.UUID):
            out[key] = str(out[key])
    if "sha256" in out and isinstance(out["sha256"], (bytes, bytearray)):
        out["sha256"] = bytes(out["sha256"]).hex()
    return out


class PgMetadataStore(MetadataStorePort):
    """Real PostgreSQL via SQLAlchemy 2 + psycopg3; schema via Alembic."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        host: str = "postgres",
        port: int = 5432,
        user: str = "image_emb",
        password: str = "image_emb",
        database: str = "image_embedding",
        connect_timeout: float = 5.0,
    ) -> None:
        if dsn and str(dsn).strip():
            self.dsn = str(dsn).strip()
        else:
            self.dsn = (
                f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
            )
        self.host = host
        self.port = int(port)
        self.user = user
        self.database = database
        self.connect_timeout = float(connect_timeout)
        self._engine = None
        self._schema_ready = False

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise err.index_mysql_failed(
                "SQLAlchemy is not installed; add sqlalchemy>=2 to requirements.txt"
            ) from exc
        try:
            self._engine = create_engine(
                self.dsn,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(self.connect_timeout)},
            )
            return self._engine
        except Exception as exc:
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        try:
            from sqlalchemy import text

            engine = self._get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {
                "ok": True,
                "host": self.host,
                "database": self.database,
                "driver": "postgresql",
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "host": self.host,
                "database": self.database,
                "driver": "postgresql",
            }

    def ensure_schema(self) -> None:
        """Verify Alembic-applied 12 tables + ltree. Does NOT auto-migrate.

        Run: `cd backend && alembic upgrade head` (DATABASE_URL / POSTGRES_*).
        """
        if self._schema_ready:
            return
        from sqlalchemy import text

        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                ext = conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'ltree'")
                ).fetchone()
                if not ext:
                    raise err.index_mysql_failed(
                        "ltree extension missing; run `alembic upgrade head`"
                    )
                missing: list[str] = []
                for table in REQUIRED_TABLES:
                    row = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name=:t"
                        ),
                        {"t": table},
                    ).fetchone()
                    if not row:
                        missing.append(table)
                if missing:
                    raise err.index_mysql_failed(
                        "PG schema incomplete (missing "
                        + ", ".join(missing)
                        + "); run `alembic upgrade head` from backend/"
                    )
            self._schema_ready = True
            logger.info(
                "pg metadata schema ready db=%s tables=%d",
                self.database,
                len(REQUIRED_TABLES),
            )
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(f"ensure_schema: {exc}") from exc

    # ---- MetadataStorePort (uuid; full index/list rewrite is M4/M5) ----

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

    # ---- Model registry / embedding sync (stack wiring) ----

    def get_current_embedding_model(self) -> dict[str, Any]:
        """Return the single status='current' embedding_models row.

        Raises clearly if none — seed expectation (M2 empty rebuild):
          INSERT INTO embedding_models
            (name, version, dim, distance, input_size, preprocess, weights_uri,
             collection, status, activated_at)
          VALUES (..., 'cosine', ..., '{}', '...', '<collection>', 'current', now());
        Partial unique index enforces at most one current.
        """
        self.ensure_schema()
        from sqlalchemy import text

        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, version, dim, distance, input_size, "
                    "preprocess, weights_uri, collection, status, "
                    "created_at, activated_at "
                    "FROM embedding_models WHERE status = 'current' LIMIT 1"
                )
            ).mappings().fetchone()
        if row is None:
            raise err.not_ready(
                "no embedding_models row with status='current'; "
                "seed a current model (collection/dim/distance) before starting retrieval"
            )
        out = _row_to_dict(row)
        out["vector_dim"] = int(out["dim"])
        # Normalize distance for QdrantStore (cosine/dot/euclid)
        out["distance"] = str(out.get("distance") or "cosine").lower()
        return out

    def upsert_image_embedding(
        self,
        *,
        model_id: int,
        image_id: UUID | str,
        synced_version: int | None = None,
    ) -> None:
        """After successful Qdrant upsert: sync image_embeddings ledger.

        If synced_version is None, read images.payload_version for image_id.
        """
        self.ensure_schema()
        from sqlalchemy import text

        uid = _as_uuid(image_id)
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                version = synced_version
                if version is None:
                    prow = conn.execute(
                        text(
                            "SELECT payload_version FROM images "
                            "WHERE id = CAST(:id AS uuid)"
                        ),
                        {"id": str(uid)},
                    ).fetchone()
                    if prow is None:
                        raise err.index_mysql_failed(
                            f"upsert_image_embedding: image {uid} not found"
                        )
                    version = int(prow[0])
                conn.execute(
                    text(
                        "INSERT INTO image_embeddings "
                        "(model_id, image_id, synced_version, embedded_at) "
                        "VALUES (:mid, CAST(:iid AS uuid), :ver, now()) "
                        "ON CONFLICT (model_id, image_id) DO UPDATE SET "
                        "synced_version = EXCLUDED.synced_version, "
                        "embedded_at = EXCLUDED.embedded_at"
                    ),
                    {"mid": int(model_id), "iid": str(uid), "ver": int(version)},
                )
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(
                f"upsert_image_embedding: {type(exc).__name__}: {exc}"
            ) from exc

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

    def ensure_embedding_space(
        self,
        collection_name: str,
        embedding_space_id: str,
        vector_dim: int,
    ) -> dict[str, Any]:
        """Compatibility shim: prefer get_current_embedding_model() in stack.

        If a row exists for collection, compare dim; else defer (no silent insert).
        """
        self.ensure_schema()
        from sqlalchemy import text

        name = str(collection_name or "").strip()
        if not name:
            raise err.validation_error("collection_name is required")
        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, version, dim, collection, status "
                    "FROM embedding_models WHERE collection = :c"
                ),
                {"c": name},
            ).mappings().fetchone()
        if row is None:
            logger.info(
                "embedding_models: no row for collection=%s; use get_current_embedding_model",
                name,
            )
            return {
                "collection_name": name,
                "embedding_space_id": str(embedding_space_id or ""),
                "vector_dim": int(vector_dim),
                "created": False,
                "deferred": True,
            }
        stored_dim = int(row["dim"])
        if stored_dim != int(vector_dim):
            raise err.vector_space_mismatch(
                f"embedding_models.dim mismatch: configured={vector_dim} stored={stored_dim}",
                configured=str(embedding_space_id or ""),
                stored=f"dim={stored_dim}",
            )
        return {
            "collection_name": name,
            "embedding_space_id": str(embedding_space_id or ""),
            "vector_dim": stored_dim,
            "created": False,
            "model_id": int(row["id"]),
            "status": row["status"],
        }


    # ---- image_deletions pipeline (§2.5 / M4) ----

    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        """TX: capture URLs → DELETE images (CASCADE deps) → INSERT image_deletions.

        Contract order: delete from images then insert image_deletions.
        URLs are selected first so the insert can store original_url/thumb_url.
        Idempotent if already queued (returns existing image_deletions row).
        """
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                existing = conn.execute(
                    text(
                        "SELECT image_id, original_url, thumb_url, deleted_at "
                        "FROM image_deletions WHERE image_id = CAST(:id AS uuid)"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
                if existing is not None:
                    return _row_to_dict(existing)

                row = conn.execute(
                    text(
                        "SELECT id, original_url, thumb_url FROM images "
                        "WHERE id = CAST(:id AS uuid) FOR UPDATE"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
                if row is None:
                    raise err.validation_error(
                        f"image not found for deletion: {uid}"
                    )
                original_url = str(row["original_url"] or "")
                thumb_url = str(row["thumb_url"] or "")
                if not original_url or not thumb_url:
                    raise err.validation_error(
                        f"image {uid} missing original_url/thumb_url"
                    )

                # 1) delete images (+ CASCADE image_scenes / image_embeddings)
                conn.execute(
                    text("DELETE FROM images WHERE id = CAST(:id AS uuid)"),
                    {"id": uid},
                )
                # 2) insert image_deletions for async Qdrant + S3 cleanup
                conn.execute(
                    text(
                        "INSERT INTO image_deletions "
                        "(image_id, original_url, thumb_url, deleted_at) "
                        "VALUES (CAST(:id AS uuid), :ou, :tu, now())"
                    ),
                    {"id": uid, "ou": original_url, "tu": thumb_url},
                )
                out = conn.execute(
                    text(
                        "SELECT image_id, original_url, thumb_url, deleted_at "
                        "FROM image_deletions WHERE image_id = CAST(:id AS uuid)"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
            return _row_to_dict(out)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(
                f"enqueue_image_deletion: {type(exc).__name__}: {exc}"
            ) from exc

    def list_pending_deletions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Oldest-first pending image_deletions rows for the worker."""
        self.ensure_schema()
        from sqlalchemy import text

        lim = max(1, min(int(limit), 1000))
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT image_id, original_url, thumb_url, deleted_at "
                        "FROM image_deletions "
                        "ORDER BY deleted_at ASC "
                        "LIMIT :lim"
                    ),
                    {"lim": lim},
                ).mappings().fetchall()
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            raise err.index_mysql_failed(
                f"list_pending_deletions: {type(exc).__name__}: {exc}"
            ) from exc

    def complete_deletion(self, image_id: UUID | str) -> None:
        """Remove image_deletions row after Qdrant + S3 cleanup succeeded."""
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM image_deletions "
                        "WHERE image_id = CAST(:id AS uuid)"
                    ),
                    {"id": uid},
                )
        except Exception as exc:
            raise err.index_mysql_failed(
                f"complete_deletion: {type(exc).__name__}: {exc}"
            ) from exc


    # ---- Scene membership / labels (M5 write-time materialize) ----

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
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.code FROM image_scenes isc "
                    "JOIN scenes s ON s.id = isc.scene_id "
                    "WHERE isc.image_id = CAST(:id AS uuid) "
                    "  AND isc.is_member = true "
                    "ORDER BY s.id"
                ),
                {"id": uid},
            ).fetchall()
        return [str(r[0]) for r in rows]

    def recompute_image_scenes(
        self,
        image_id: UUID | str,
        *,
        bump_version: bool = True,
    ) -> dict[str, Any]:
        """Write-time materialize effective membership into image_scenes.

        - Replaces origin='rule' rows; preserves origin='manual'.
        - Effective codes = rule members with manual overrides applied.
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
                            "SELECT scene_id, is_member FROM image_scenes "
                            "WHERE image_id = CAST(:id AS uuid) "
                            "  AND origin = 'manual'"
                        ),
                        {"id": uid},
                    ).mappings().fetchall()
                )
                manual_scene_ids = {int(m["scene_id"]) for m in manual}
                effective = apply_manual_overrides(rule_members, manual)

                conn.execute(
                    text(
                        "DELETE FROM image_scenes "
                        "WHERE image_id = CAST(:id AS uuid) AND origin = 'rule'"
                    ),
                    {"id": uid},
                )
                for sid in sorted(rule_members):
                    if sid in manual_scene_ids:
                        continue
                    conn.execute(
                        text(
                            "INSERT INTO image_scenes "
                            "(image_id, scene_id, origin, is_member, updated_at) "
                            "VALUES (CAST(:iid AS uuid), :sid, 'rule', true, now())"
                        ),
                        {"iid": uid, "sid": sid},
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
        """Upsert origin=manual override then recompute (bumps payload_version)."""
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        scene = self.get_scene_by_code(scene_code)
        if not scene:
            raise err.validation_error(f"unknown scene: {scene_code!r}")
        sid = int(scene["id"])
        engine = self._get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO image_scenes "
                    "(image_id, scene_id, origin, is_member, updated_at) "
                    "VALUES (CAST(:iid AS uuid), :sid, 'manual', :m, now()) "
                    "ON CONFLICT (image_id, scene_id) DO UPDATE SET "
                    "origin = 'manual', is_member = EXCLUDED.is_member, "
                    "updated_at = now()"
                ),
                {"iid": uid, "sid": sid, "m": bool(is_member)},
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

