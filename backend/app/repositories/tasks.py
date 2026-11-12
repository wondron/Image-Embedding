"""index_tasks: the outcome of each file of an index request (app.models.task.IndexTask).

Written by the index use case after each file (best effort: a failed write is logged, the index
result stands); read and cleared by the task center (GET / DELETE /api/index-tasks).

A succeeded record keeps its image_id after the image is deleted (no foreign key): joined with
images, such a record has no image any more ("deleted"), the others still have one ("present").
"""

from __future__ import annotations

from typing import Any

from app.core import errors as err
from app.core.enums import IndexTaskStatus, NameKind
from app.db.common import _as_uuid, _row_to_dict

# Bounds for what lands in the record (the columns are text): a pathological filename or
# message must not bloat the table
_MAX_FILENAME = 255
_MAX_DETAIL = 1000
_MAX_CODE = 64
_MAX_TRACE = 128


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _filter(status: str | None, image_state: str | None) -> tuple[str, dict[str, Any]]:
    """WHERE over `index_tasks t LEFT JOIN images i ON i.id = t.image_id`, shared by the list and
    the clear so that a clear removes exactly what the list shows. image_state: present (a
    succeeded record whose image is still stored) / deleted (whose image was deleted since)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if status:
        clauses.append("t.status = :status")
        params["status"] = IndexTaskStatus(status).value
    if image_state:
        if image_state not in ("present", "deleted"):
            raise ValueError(f"unknown image_state {image_state!r}")
        clauses.append("t.status = :succeeded AND i.id IS " + ("NOT NULL" if image_state == "present" else "NULL"))
        params["succeeded"] = IndexTaskStatus.SUCCEEDED.value
    return ("WHERE " + " AND ".join(clauses) + " ") if clauses else "", params


def _matching(counts: dict[str, int], status: str | None, image_state: str | None) -> int:
    """How many records a filter selects, from the counts (one read for both)."""
    succeeded = IndexTaskStatus.SUCCEEDED.value
    if image_state is None:
        return counts.get(status, 0) if status else counts[succeeded] + counts[IndexTaskStatus.FAILED.value]
    if status not in (None, succeeded):
        return 0
    deleted = counts["image_deleted"]
    return deleted if image_state == "deleted" else counts[succeeded] - deleted


def _record_params(row: dict[str, Any]) -> dict[str, Any]:
    status = IndexTaskStatus(row["status"])
    image_id = row.get("image_id")
    duration = row.get("duration_ms")
    return {
        "filename": _clip(row.get("filename") or "unknown", _MAX_FILENAME),
        "status": status.value,
        "image_id": str(_as_uuid(image_id)) if image_id and status is IndexTaskStatus.SUCCEEDED else None,
        "new_image": row.get("new_image") if status is IndexTaskStatus.SUCCEEDED else None,
        "error_code": _clip(row.get("error_code"), _MAX_CODE) if status is IndexTaskStatus.FAILED else None,
        "error_detail": _clip(row.get("error_detail"), _MAX_DETAIL) if status is IndexTaskStatus.FAILED else None,
        "duration_ms": max(0, int(duration)) if duration is not None else None,
        "trace_id": _clip(row.get("trace_id"), _MAX_TRACE),
    }


class PgIndexTasksMixin:
    """Task center records."""

    def record_index_tasks(self, rows: list[dict[str, Any]]) -> None:
        """Insert one record per file: {filename, status, image_id?, new_image?, error_code?,
        error_detail?, duration_ms?, trace_id?}. A succeeded record needs its image_id."""
        if not rows:
            return
        self.ensure_schema()
        from sqlalchemy import text

        params = [_record_params(row) for row in rows]
        try:
            with self._get_engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO index_tasks "
                        "(filename, status, image_id, new_image, error_code, error_detail, duration_ms, trace_id) "
                        "VALUES (:filename, :status, CAST(:image_id AS uuid), :new_image, :error_code, "
                        ":error_detail, :duration_ms, :trace_id)"
                    ),
                    params,
                )
        except Exception as exc:
            raise err.index_postgres_failed(f"record_index_tasks: {type(exc).__name__}: {exc}") from exc

    def list_index_tasks(
        self, *, status: str | None = None, image_state: str | None = None, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        """Newest first, optionally one status and / or image state:
        {items, total, counts: {succeeded, failed, image_deleted}} (image_deleted: the succeeded
        records whose image was deleted since; counted in succeeded too). Each item carries its
        image's thumbnail and category while the image still exists."""
        self.ensure_schema()
        from sqlalchemy import text

        size = max(1, min(int(page_size), 100))
        offset = (max(1, int(page)) - 1) * size
        where, filter_params = _filter(status, image_state)
        params: dict[str, Any] = {"lim": size, "off": offset, "primary_kind": NameKind.PRIMARY.value, **filter_params}
        try:
            with self._get_engine().connect() as conn:
                counts = {member.value: 0 for member in IndexTaskStatus}
                counts["image_deleted"] = 0
                for row in conn.execute(text(
                    "SELECT t.status, i.id IS NULL AS image_gone, count(*) "
                    "FROM index_tasks t LEFT JOIN images i ON i.id = t.image_id GROUP BY 1, 2"
                )):
                    counts[str(row[0])] += int(row[2])
                    if row[0] == IndexTaskStatus.SUCCEEDED.value and row[1]:
                        counts["image_deleted"] += int(row[2])
                rows = conn.execute(
                    text(
                        "SELECT t.id, t.filename, t.status, t.image_id::text AS image_id, t.new_image, "
                        "t.error_code, t.error_detail, t.duration_ms, t.trace_id, t.created_at, "
                        "i.id IS NOT NULL AS image_exists, i.thumb_url, i.category_id, "
                        "i.label_status AS image_label_status, cn.name AS category_name "
                        "FROM index_tasks t "
                        "LEFT JOIN images i ON i.id = t.image_id "
                        "LEFT JOIN category_names cn ON cn.category_id = i.category_id "
                        "  AND cn.lang = 'zh' AND cn.kind = :primary_kind "
                        + where
                        + "ORDER BY t.created_at DESC, t.id DESC LIMIT :lim OFFSET :off"
                    ),
                    params,
                ).mappings().fetchall()
        except Exception as exc:
            raise err.list_postgres_failed(f"list_index_tasks: {type(exc).__name__}: {exc}") from exc
        return {"items": [_row_to_dict(row) for row in rows], "total": _matching(counts, status, image_state), "counts": counts}

    def clear_index_tasks(self, *, status: str | None = None, image_state: str | None = None) -> int:
        """Delete the records the list shows for this filter (at least one of the two: never
        everything by accident); returns how many went. Only records: images are not touched."""
        if not status and not image_state:
            raise ValueError("clear_index_tasks needs a status or an image_state")
        self.ensure_schema()
        from sqlalchemy import text

        where, params = _filter(status, image_state)
        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text(
                        "DELETE FROM index_tasks WHERE id IN (SELECT t.id FROM index_tasks t "
                        "LEFT JOIN images i ON i.id = t.image_id " + where + ")"
                    ),
                    params,
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"clear_index_tasks: {type(exc).__name__}: {exc}") from exc

    def delete_index_task(self, task_id: int) -> int:
        """Delete one record; 1 when it was there, 0 when not. The image is not touched."""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text("DELETE FROM index_tasks WHERE id = :id"), {"id": int(task_id)}
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"delete_index_task: {type(exc).__name__}: {exc}") from exc
