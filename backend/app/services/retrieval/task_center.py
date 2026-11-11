"""Task center: the index records and the deletion queue (GET/DELETE /api/index-tasks,
GET /api/image-deletions).

PostgreSQL only (built by app.bootstrap.catalog, like the gallery admin), so the history stays
readable while the encoder or Qdrant are down. Draining the deletion queue needs the vector store
and the object storage: the HTTP layer runs the retrieval stack's DeletionWorker for that
(POST /api/image-deletions/process).

Records are written by the index use case (services.retrieval.index_image), one per file.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import errors as err
from app.core.enums import IndexTaskStatus
from app.repositories.ports import CatalogStorePort
from app.services.retrieval.enum_fields import coerce_enum, coerce_optional_enum

MAX_PAGE_SIZE = 100
MAX_DELETIONS = 200


def index_task_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """The wire record. `image` is the image as it is now: null when the file failed, or when its
    image was deleted since (a succeeded record keeps its image_id)."""
    image = None
    if row.get("image_exists"):
        image = {
            "id": row.get("image_id"),
            "thumb_url": row.get("thumb_url"),
            "category_id": row.get("category_id"),
            "category": row.get("category_name"),
            "label_status": row.get("image_label_status"),
        }
    return {
        "id": row.get("id"),
        "filename": row.get("filename"),
        "status": row.get("status"),
        "image_id": row.get("image_id"),
        "new_image": row.get("new_image"),
        "error_code": row.get("error_code"),
        "error_detail": row.get("error_detail"),
        "duration_ms": row.get("duration_ms"),
        "trace_id": row.get("trace_id"),
        "created_at": row.get("created_at"),
        "image": image,
    }


def deletion_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "image_id": str(row.get("image_id")),
        "original_url": row.get("original_url"),
        "thumb_url": row.get("thumb_url"),
        "deleted_at": row.get("deleted_at"),
    }


def _bounded(value: Any, *, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise err.validation_error(f"{field} must be an integer in {low}..{high}, got {value!r}")
    return value


class TaskCenterUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def list_index_tasks(self, *, status: Any = None, page: Any = 1, page_size: Any = 50) -> dict[str, Any]:
        """Newest first; `status` filters (succeeded / failed). counts are over all records."""
        wanted = coerce_optional_enum(IndexTaskStatus, status, field="status")
        page = _bounded(page, field="page", low=1, high=1_000_000)
        page_size = _bounded(page_size, field="page_size", low=1, high=MAX_PAGE_SIZE)
        result = self._catalog.list_index_tasks(
            status=wanted.value if wanted else None, page=page, page_size=page_size
        )
        counts = dict(result.get("counts") or {})
        return {
            "status": wanted.value if wanted else None,
            "page": page,
            "page_size": page_size,
            "total": int(result.get("total") or 0),
            "counts": {member.value: int(counts.get(member.value) or 0) for member in IndexTaskStatus},
            "items": [index_task_item(row) for row in result.get("items") or []],
        }

    def clear_index_tasks(self, *, status: Any) -> dict[str, Any]:
        """Delete the records of one status (required: never everything by accident)."""
        wanted = coerce_enum(IndexTaskStatus, status, field="status")
        return {"status": wanted.value, "deleted": self._catalog.clear_index_tasks(status=wanted.value)}

    def list_deletions(self, *, limit: Any = 50) -> dict[str, Any]:
        """Queued cleanups, oldest first, with how many there are in all."""
        limit = _bounded(limit, field="limit", low=1, high=MAX_DELETIONS)
        rows = self._catalog.list_pending_deletions(limit=limit)
        return {"total": self._catalog.count_pending_deletions(), "items": [deletion_item(row) for row in rows]}

    def count_deletions(self) -> int:
        return self._catalog.count_pending_deletions()
