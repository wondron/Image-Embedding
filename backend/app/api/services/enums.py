"""HTTP adapter for the enums file (app.core.config.enum_store).

Failures are AppError from the store: ENUM_NOT_FOUND 404, ENUM_NOT_EDITABLE 403,
ENUM_VALUE_EXISTS 409, VALIDATION_ERROR 422, ENUM_FILE_INVALID 503 (file unreadable or
breaking a rule), ENUM_FILE_WRITE_FAILED 500. Callers run these in the threadpool (file I/O).
"""

from __future__ import annotations

from typing import Any

from app.core.config.enum_store import EnumDef, get_enum_store
from app.schemas.enums import AddEnumValueRequest


def enum_to_wire(definition: EnumDef) -> dict[str, Any]:
    return {
        "name": definition.name,
        "label": definition.label,
        "description": definition.description,
        "editable": definition.editable,
        "values": [
            {"value": v.value, "label": v.label, "description": v.description} for v in definition.values
        ],
    }


def list_enums() -> dict[str, Any]:
    store = get_enum_store()
    catalog = store.catalog()
    return {"ok": True, "enums": [enum_to_wire(d) for d in catalog], "file_error": store.error}


def get_enum(name: str) -> dict[str, Any]:
    return {"ok": True, "enum": enum_to_wire(get_enum_store().get(name))}


def add_enum_value(name: str, body: AddEnumValueRequest) -> dict[str, Any]:
    definition = get_enum_store().add_value(name, body.value, body.label, body.description)
    return {"ok": True, "enum": enum_to_wire(definition)}
