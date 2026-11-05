from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.db.common import _as_uuid, _row_to_dict, logger
from app.services.retrieval import errors as err


class PgEmbeddingsMixin:
    """embedding_models current row + image_embeddings ledger."""

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
