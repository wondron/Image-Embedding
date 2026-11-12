"""Task center: the index records and the deletion queue (GET/DELETE /api/index-tasks,
GET /api/image-deletions).

PostgreSQL only (built by app.bootstrap.catalog, like the gallery admin), so the history stays
readable while the encoder or Qdrant are down. Draining the deletion queue needs the vector store
and the object storage: the HTTP layer runs the retrieval stack's DeletionWorker for that
(POST /api/image-deletions/process).

Records are written by the index use case (services.retrieval.index_image), one per file.

Deleting an image keeps its records (they tell what was indexed when): such a succeeded record
reads image_state=deleted, can be listed on its own (image_state filter) and cleared on its own
(DELETE ?image_state=deleted), or one by one (DELETE /api/index-tasks/{id}). Clearing only ever
removes records, never images.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from app.core import errors as err
from app.core.enums import IndexTaskStatus
from app.repositories.ports import CatalogStorePort
from app.core.rules.enum_fields import coerce_optional_enum

MAX_PAGE_SIZE = 100
MAX_DELETIONS = 200
MAX_TASK_ID = 2**63 - 1  # index_tasks.id is a bigint

# Whether a succeeded record's image is still stored: derived when reading (no column, no CHECK),
# hence not one of the system enums of app.core.enums
IndexTaskImageState = Literal["present", "deleted"]
IMAGE_STATES: tuple[str, ...] = ("present", "deleted")


def _image_state(value: Any) -> str | None:
    if value is None:
        return None
    if value not in IMAGE_STATES:
        raise err.validation_error(f"image_state must be one of {', '.join(IMAGE_STATES)}, got {value!r}")
    return value


def index_task_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """The wire record. `image` is the image as it is now: null when the file failed, or when its
    image was deleted since (a succeeded record keeps its image_id; image_state says which)."""
    image = None
    image_state = None
    if row.get("status") == IndexTaskStatus.SUCCEEDED.value:
        image_state = "present" if row.get("image_exists") else "deleted"
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
        "image_state": image_state,
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

    def list_index_tasks(
        self, *, status: Any = None, image_state: Any = None, page: Any = 1, page_size: Any = 50
    ) -> dict[str, Any]:
        """Newest first; `status` filters (succeeded / failed), `image_state` the succeeded records
        (present / deleted). counts are over all records."""
        wanted = coerce_optional_enum(IndexTaskStatus, status, field="status")
        state = _image_state(image_state)
        page = _bounded(page, field="page", low=1, high=1_000_000)
        page_size = _bounded(page_size, field="page_size", low=1, high=MAX_PAGE_SIZE)
        result = self._catalog.list_index_tasks(
            status=wanted.value if wanted else None, image_state=state, page=page, page_size=page_size
        )
        counts = dict(result.get("counts") or {})
        return {
            "status": wanted.value if wanted else None,
            "image_state": state,
            "page": page,
            "page_size": page_size,
            "total": int(result.get("total") or 0),
            "counts": {
                **{member.value: int(counts.get(member.value) or 0) for member in IndexTaskStatus},
                "image_deleted": int(counts.get("image_deleted") or 0),
            },
            "items": [index_task_item(row) for row in result.get("items") or []],
        }

    def clear_index_tasks(self, *, status: Any = None, image_state: Any = None) -> dict[str, Any]:
        """Delete the records of a filter: a status and / or an image state, at least one (never
        everything by accident). Only records go; images are not touched."""
        wanted = coerce_optional_enum(IndexTaskStatus, status, field="status")
        state = _image_state(image_state)
        if wanted is None and state is None:
            raise err.validation_error("status or image_state is required (clearing never removes every record)")
        deleted = self._catalog.clear_index_tasks(status=wanted.value if wanted else None, image_state=state)
        return {"status": wanted.value if wanted else None, "image_state": state, "deleted": deleted}

    def delete_index_task(self, task_id: Any) -> dict[str, Any]:
        """Delete one record (its image is not touched): deleted 1, or 0 when it was not there."""
        task_id = _bounded(task_id, field="task_id", low=1, high=MAX_TASK_ID)
        return {"id": task_id, "deleted": self._catalog.delete_index_task(task_id)}

    def list_deletions(self, *, limit: Any = 50) -> dict[str, Any]:
        """Queued cleanups, oldest first, with how many there are in all."""
        limit = _bounded(limit, field="limit", low=1, high=MAX_DELETIONS)
        rows = self._catalog.list_pending_deletions(limit=limit)
        return {"total": self._catalog.count_pending_deletions(), "items": [deletion_item(row) for row in rows]}

    def count_deletions(self) -> int:
        return self._catalog.count_pending_deletions()
