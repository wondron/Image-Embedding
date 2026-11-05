"""Shared helpers for PostgreSQL metadata adapters (PG v0.1)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger("pg_metadata")

REQUIRED_TABLES = (
    "heating_groups",
    "categories",
    "category_names",
    "images",
    "image_deletions",
    "scenes",
    "scene_labels",
    "scene_rules",
    "image_scenes",
    "embedding_models",
    "image_embeddings",
    "external_candidates",
)

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

def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "_mapping"):
        out = dict(row._mapping)
    else:
        out = dict(row)
    for key in (
        "created_at",
        "updated_at",
        "deleted_at",
        "embedded_at",
        "activated_at",
        "reviewed_at",
    ):
        if key in out:
            out[key] = _iso(out[key])
    for key in ("id", "image_id"):
        if key in out and isinstance(out[key], uuid.UUID):
            out[key] = str(out[key])
    if "sha256" in out and isinstance(out["sha256"], (bytes, bytearray)):
        out["sha256"] = bytes(out["sha256"]).hex()
    return out
