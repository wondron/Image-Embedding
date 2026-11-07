from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.enums import Distance, ModelStatus
from app.db.common import _as_uuid, _row_to_dict, logger
from app.core import errors as err


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
                    "FROM embedding_models WHERE status = :current LIMIT 1"
                ),
                {"current": ModelStatus.CURRENT.value},
            ).mappings().fetchone()
        if row is None:
            raise err.not_ready(
                "no embedding_models row with status='current'; "
                "seed a current model (collection/dim/distance) before starting retrieval"
            )
        out = _row_to_dict(row)
        out["vector_dim"] = int(out["dim"])
        # Plain str for QdrantStore (cosine/dot/euclid); ck_embedding_models_distance
        # guarantees a valid value
        out["distance"] = Distance(str(out.get("distance") or Distance.COSINE).lower()).value
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
                        raise err.index_postgres_failed(
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
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"upsert_image_embedding: {type(exc).__name__}: {exc}"
            ) from exc
