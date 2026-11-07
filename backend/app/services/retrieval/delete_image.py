"""Image deletion pipeline (contract §2.5).

1. delete_image: TX delete images (+ CASCADE deps) → insert image_deletions
2. process_deletion_queue: drain image_deletions → Qdrant delete + S3 delete → remove row

Qdrant choice (M4): delete by uuid on the **current** collection via VectorStorePort.
Multi-collection sweep (all embedding_models.collection) deferred — document for later.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import MetadataStorePort, ImageStoragePort, VectorStorePort

logger = logging.getLogger("retrieval.delete")


class DeleteImageUseCase:
    """Enqueue hard-delete into image_deletions (metadata TX)."""

    def __init__(self, metadata: MetadataStorePort) -> None:
        self._metadata = metadata

    def execute(self, image_id: UUID | str) -> dict[str, Any]:
        row = self._metadata.enqueue_image_deletion(image_id)
        logger.info("enqueued image_deletions id=%s", row.get("image_id"))
        return {
            "ok": True,
            "image_id": row.get("image_id"),
            "original_url": row.get("original_url"),
            "thumb_url": row.get("thumb_url"),
            "deleted_at": row.get("deleted_at"),
            "queued": True,
        }


def process_deletion_queue(
    metadata: MetadataStorePort,
    vectors: VectorStorePort,
    storage: ImageStoragePort,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Drain up to `limit` image_deletions rows.

    On full success (Qdrant + both S3 objects): remove image_deletions row.
    On any failure: leave row for retry; continue with next.
    """
    if limit <= 0:
        raise err.validation_error("limit must be positive")
    pending = metadata.list_pending_deletions(limit=int(limit))
    processed: list[str] = []
    failed: list[dict[str, Any]] = []
    for row in pending:
        image_id = row.get("image_id")
        try:
            # Current collection only (VectorStorePort wired to embedding_models.current).
            vectors.delete(image_id)
            for url_field in ("original_url", "thumb_url"):
                url = str(row.get(url_field) or "").strip()
                if url:
                    storage.delete_by_url_or_key(url)
            metadata.complete_deletion(image_id)
            processed.append(str(image_id))
            logger.info("deletion queue completed id=%s", image_id)
        except Exception as exc:
            logger.exception("deletion queue item failed id=%s", image_id)
            failed.append(
                {
                    "image_id": str(image_id),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "ok": len(failed) == 0,
        "scanned": len(pending),
        "processed": processed,
        "failed": failed,
        # ASSUMPTION: only current collection; multi-collection delete TBD
        "qdrant_scope": "current_collection_only",
    }


class DeletionWorker:
    """Bootstrap-friendly wrapper around process_deletion_queue."""

    def __init__(
        self,
        metadata: MetadataStorePort,
        vectors: VectorStorePort,
        storage: ImageStoragePort,
    ) -> None:
        self._metadata = metadata
        self._vectors = vectors
        self._storage = storage

    def run_once(self, *, limit: int = 50) -> dict[str, Any]:
        return process_deletion_queue(
            self._metadata,
            self._vectors,
            self._storage,
            limit=limit,
        )
