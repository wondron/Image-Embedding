"""Coerce wire/row values into app.core.enums members (VALIDATION_ERROR on bad input).

DB columns are VARCHAR + CHECK (app.db.types.str_enum); validating here turns a
would-be IntegrityError (500) into a 422 that names the field and allowed values.
"""

from __future__ import annotations

from typing import Any, TypeVar

from app.core.enums import StrEnum
from app.services.retrieval import errors as err

E = TypeVar("E", bound=StrEnum)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def coerce_enum(
    enum_cls: type[E],
    value: Any,
    *,
    field: str,
    default: E | None = None,
) -> E:
    """Required enum field; blank -> default, or VALIDATION_ERROR when no default."""
    if _blank(value):
        if default is None:
            raise err.validation_error(f"{field} is required")
        return default
    return _parse(enum_cls, value, field=field)


def coerce_optional_enum(enum_cls: type[E], value: Any, *, field: str) -> E | None:
    """Nullable enum field; blank -> None."""
    if _blank(value):
        return None
    return _parse(enum_cls, value, field=field)


def _parse(enum_cls: type[E], value: Any, *, field: str) -> E:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip())
    except ValueError:
        allowed = [m.value for m in enum_cls]
        raise err.validation_error(
            f"{field} must be one of {allowed}, got {value!r}"
        ) from None
