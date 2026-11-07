"""Shared helpers for PostgreSQL metadata adapters (PG v0.1)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger("pg_metadata")


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

def _jsonable(value: Any) -> Any:
    """datetime -> ISO 8601 (naive means UTC), UUID -> str, bytea (sha256) -> hex; the rest as-is."""
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value

def _row_to_dict(row: Any) -> dict[str, Any]:
    """A result row as a JSON-friendly dict. Converts by value type, so new columns need no edit here."""
    if row is None:
        return {}
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {key: _jsonable(value) for key, value in dict(mapping).items()}
