"""index_tasks: the outcome of each file of an index request (app.models.task.IndexTask).

Written by the index use case after each file (best effort: a failed write is logged, the index
result stands); read and cleared by the task center (GET / DELETE /api/index-tasks).
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

    def list_index_tasks(self, *, status: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """Newest first, optionally one status: {items, total, counts: {succeeded, failed}}. Each
        item carries its image's thumbnail and category while the image still exists."""
        self.ensure_schema()
        from sqlalchemy import text

        size = max(1, min(int(page_size), 100))
        offset = (max(1, int(page)) - 1) * size
        params: dict[str, Any] = {"lim": size, "off": offset, "primary_kind": NameKind.PRIMARY.value}
        where = ""
        if status:
            where = "WHERE t.status = :status "
            params["status"] = IndexTaskStatus(status).value
        try:
            with self._get_engine().connect() as conn:
                counts = {member.value: 0 for member in IndexTaskStatus}
                for row in conn.execute(text("SELECT status, count(*) FROM index_tasks GROUP BY status")):
                    counts[str(row[0])] = int(row[1])
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
        total = counts.get(params["status"], 0) if status else sum(counts.values())
        return {"items": [_row_to_dict(row) for row in rows], "total": total, "counts": counts}

    def clear_index_tasks(self, *, status: str) -> int:
        """Delete the records of one status; returns how many went."""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text("DELETE FROM index_tasks WHERE status = :status"),
                    {"status": IndexTaskStatus(status).value},
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"clear_index_tasks: {type(exc).__name__}: {exc}") from exc
