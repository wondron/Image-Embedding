"""Image deletion pipeline (contract §2.5).

1. delete_image: TX delete images (+ CASCADE deps) → insert image_deletions
2. process_deletion_queue: drain image_deletions → Qdrant delete + S3 delete → remove row
   (DELETE /api/images/{image_id} runs this for its own row right away, DeletionWorker.clean_up;
   what fails there stays queued for `python -m app.workers.process_deletions` or
   POST /api/image-deletions/process, the task center's 「立即清理」)

Qdrant: the point goes from the current collection and from the collection of every other
version still in use (building / ready: a new model being filled, the previous one kept for a
roll back), so a switch or a roll back never brings a deleted image back into the results.
Retired versions have no collection any more.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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


def _no_other_collections() -> list[VectorStorePort]:
    return []


def _clean_up(
    metadata: MetadataStorePort,
    vectors: VectorStorePort,
    storage: ImageStoragePort,
    row: dict[str, Any],
    others: Callable[[], list[VectorStorePort]] = _no_other_collections,
) -> None:
    """Delete one queued image's point (current collection and the other versions' in use) and
    its two files, then its queue row.

    Raises on failure, leaving the row for a retry. An image stored again since it was deleted
    (same bytes: same id, point and object keys) keeps its point and files; only the row goes.
    """
    image_id = row.get("image_id")
    if metadata.get_by_id(image_id) is None:
        vectors.delete(image_id)
        for other in others():
            other.delete(image_id)
        for url_field in ("original_url", "thumb_url"):
            url = str(row.get(url_field) or "").strip()
            if url:
                storage.delete_by_url_or_key(url)
    else:
        logger.warning("image %s was stored again after its deletion; its point and files are kept", image_id)
    metadata.complete_deletion(image_id)


def _failure(image_id: Any, exc: Exception) -> dict[str, Any]:
    """One item that stays queued. `error` is the raw cause, for the operator's CLI output only;
    `code` / `detail` are what an HTTP client may see (POST /api/image-deletions/process)."""
    if isinstance(exc, err.AppError):
        code, detail = exc.code, err.client_detail(exc)
    else:
        code, detail = "INTERNAL_ERROR", "cleanup failed"
    return {"image_id": str(image_id), "error": f"{type(exc).__name__}: {exc}", "code": code, "detail": detail}


def process_deletion_queue(
    metadata: MetadataStorePort,
    vectors: VectorStorePort,
    storage: ImageStoragePort,
    *,
    limit: int = 50,
    others: Callable[[], list[VectorStorePort]] = _no_other_collections,
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
            _clean_up(metadata, vectors, storage, row, others)
            processed.append(str(image_id))
            logger.info("deletion queue completed id=%s", image_id)
        except Exception as exc:
            logger.exception("deletion queue item failed id=%s", image_id)
            failed.append(_failure(image_id, exc))
    return {
        "ok": len(failed) == 0,
        "scanned": len(pending),
        "processed": processed,
        "failed": failed,
    }


class DeletionWorker:
    """Bootstrap-friendly wrapper around process_deletion_queue."""

    def __init__(
        self,
        metadata: MetadataStorePort,
        vectors: VectorStorePort,
        storage: ImageStoragePort,
        *,
        others: Callable[[], list[VectorStorePort]] = _no_other_collections,
    ) -> None:
        """others: the collections of the other versions in use, read when a deletion is cleaned
        (bootstrap.retrieval_stack.RetrievalInfra.other_vectors)."""
        self._metadata = metadata
        self._vectors = vectors
        self._storage = storage
        self._others = others

    def run_once(self, *, limit: int = 50) -> dict[str, Any]:
        return process_deletion_queue(
            self._metadata,
            self._vectors,
            self._storage,
            limit=limit,
            others=self._others,
        )

    def clean_up(self, row: dict[str, Any]) -> bool:
        """Clean one just-queued deletion now (DELETE /api/images/{image_id}). False leaves the
        row queued for run_once (the cause is logged)."""
        try:
            _clean_up(self._metadata, self._vectors, self._storage, row, self._others)
        except Exception:
            logger.exception("deletion cleanup failed id=%s; left queued in image_deletions", row.get("image_id"))
            return False
        logger.info("deletion cleanup completed id=%s", row.get("image_id"))
        return True
