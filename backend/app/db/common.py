"""PostgreSQL 元数据适配器共用的辅助函数（PG v0.1）。"""

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
    """把 uuid 列表展开成 IN (:id0, :id1, ...)，便于驱动安全地绑定参数。"""
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
    """datetime 转 ISO 8601（不带时区的按 UTC），UUID 转 str，bytea（sha256）转十六进制；其余原样返回。"""
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value

def _row_to_dict(row: Any) -> dict[str, Any]:
    """把一行结果转成可 JSON 序列化的 dict。按值的类型转换，所以新增列不用改这里。"""
    if row is None:
        return {}
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return {key: _jsonable(value) for key, value in dict(mapping).items()}
