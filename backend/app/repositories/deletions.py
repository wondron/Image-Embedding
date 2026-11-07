from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.db.common import _as_uuid, _row_to_dict
from app.db.errors import index_postgres_failed
from app.services.retrieval import errors as err


class PgDeletionsMixin:
    """image_deletions enqueue / drain helpers."""

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
            raise index_postgres_failed(
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
            raise index_postgres_failed(
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
            raise index_postgres_failed(
                f"complete_deletion: {type(exc).__name__}: {exc}"
            ) from exc
