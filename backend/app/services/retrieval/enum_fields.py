"""Coerce wire/row values into enum values (VALIDATION_ERROR on bad input).

The first argument picks the source of the allowed values (app.core.enums):
- a system enum class (StrEnum): returns the member; the DB column also has a CHECK;
- an editable enum's name (SOURCE_TYPE, LICENSE, FOOD_STATE): returns the plain str, checked
  against config/param/enums.yaml as it is now (values added at runtime pass at once).
  Those columns have no CHECK, so this is the only guard.
Validating here turns a would-be IntegrityError (500) into a 422 that names the field and allowed values.
"""

from __future__ import annotations

from typing import Any, TypeVar, overload

from app.core import errors as err
from app.core.config.enum_store import get_enum_store
from app.core.enums import StrEnum

E = TypeVar("E", bound=StrEnum)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@overload
def coerce_enum(enum: type[E], value: Any, *, field: str, default: E | None = None) -> E: ...
@overload
def coerce_enum(enum: str, value: Any, *, field: str, default: str | None = None) -> str: ...
def coerce_enum(enum: type[StrEnum] | str, value: Any, *, field: str, default: Any = None) -> Any:
    """Required enum field; blank -> default, or VALIDATION_ERROR when no default."""
    if _blank(value):
        if default is None:
            raise err.validation_error(f"{field} is required")
        return default
    return _parse(enum, value, field=field)


@overload
def coerce_optional_enum(enum: type[E], value: Any, *, field: str) -> E | None: ...
@overload
def coerce_optional_enum(enum: str, value: Any, *, field: str) -> str | None: ...
def coerce_optional_enum(enum: type[StrEnum] | str, value: Any, *, field: str) -> Any:
    """Nullable enum field; blank -> None."""
    if _blank(value):
        return None
    return _parse(enum, value, field=field)


def _parse(enum: type[StrEnum] | str, value: Any, *, field: str) -> Any:
    if isinstance(enum, str):
        definition = get_enum_store().get(enum)  # ENUM_FILE_INVALID (503) if the file never loaded
        text = str(value).strip()
        if text in definition:
            return text
        allowed = definition.value_list()
    else:
        if isinstance(value, enum):
            return value
        try:
            return enum(str(value).strip())
        except ValueError:
            allowed = [m.value for m in enum]
    raise err.validation_error(f"{field} must be one of {allowed}, got {value!r}")
