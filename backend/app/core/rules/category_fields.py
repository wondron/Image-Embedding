"""Category tree write rules (categories + category_names): what a create or patch may carry.

Everything here needs no database: shapes, lengths and spellings (app.core.category_rules), plus

- names: one row per (lang, name); at most one primary per language; the zh primary is
  required, because results and the gallery show it as the image's category;
- roots: only the domains (app.core.enums.CategoryDomain) may sit at the top of the tree.

Checks that need the stored tree (the parent exists, the code is free, no move under an own
descendant) run inside the write transaction, in app.repositories.categories.
Bad input raises VALIDATION_ERROR naming the field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.core import errors as err
from app.core.category_rules import (
    CATEGORY_CODE_MAX_LEN,
    CATEGORY_CODE_PATTERN,
    CATEGORY_CUISINE_MAX_LEN,
    CATEGORY_CUISINE_PATTERN,
    CATEGORY_DISPLAY_LANG,
    CATEGORY_LANG_MAX_LEN,
    CATEGORY_LANG_PATTERN,
    CATEGORY_MAX_CUISINES,
    CATEGORY_MAX_NAMES,
    CATEGORY_NAME_MAX_LEN,
    CATEGORY_SORT_ORDER_MAX,
    CATEGORY_SORT_ORDER_MIN,
)
from app.core.enums import CategoryDomain, NameKind
from app.core.rules.enum_fields import coerce_enum

# The columns a request may set, in the order they are checked
CATEGORY_WRITE_FIELDS = (
    "code",
    "parent_id",
    "names",
    "cuisines",
    "heating_group_id",
    "sort_order",
    "is_active",
)
# Codes a root may use
ROOT_CODES = tuple(member.value for member in CategoryDomain)

_CODE_RE = re.compile(CATEGORY_CODE_PATTERN)
_LANG_RE = re.compile(CATEGORY_LANG_PATTERN)
_CUISINE_RE = re.compile(CATEGORY_CUISINE_PATTERN)
_PG_INT_MAX = 2_147_483_647


def _text(value: Any, field: str, *, max_len: int) -> str:
    """Stripped, non-empty text within the bound."""
    if not isinstance(value, str):
        raise err.validation_error(f"{field} must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise err.validation_error(f"{field} is required")
    if len(text) > max_len:
        raise err.validation_error(f"{field} is too long ({len(text)} > {max_len} characters)")
    return text


def normalize_category_id(value: Any, field: str = "category_id") -> int:
    """A row id: a positive int (True must not count as 1)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _PG_INT_MAX:
        raise err.validation_error(f"{field} must be a positive integer, got {value!r}")
    return value


def _optional_id(value: Any, field: str) -> int | None:
    return None if value is None else normalize_category_id(value, field)


def normalize_category_code(value: Any) -> str:
    code = _text(value, "code", max_len=CATEGORY_CODE_MAX_LEN)
    if not _CODE_RE.fullmatch(code):
        raise err.validation_error(
            "code must be lowercase letters, digits and underscores in dot-separated segments, "
            f"starting with a letter (e.g. dish.pizza); got {code!r}"
        )
    return code


def normalize_category_names(value: Any) -> list[dict[str, str]]:
    """[{lang, name, kind}] in the given order; see the module docstring for the rules."""
    if not isinstance(value, (list, tuple)):
        raise err.validation_error(f"names must be an array, got {type(value).__name__}")
    if len(value) > CATEGORY_MAX_NAMES:
        raise err.validation_error(f"too many names ({len(value)} > {CATEGORY_MAX_NAMES})")
    names: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    primaries: set[str] = set()
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise err.validation_error(f"names[{i}] must be an object with lang, name and kind")
        lang = _text(item.get("lang"), f"names[{i}].lang", max_len=CATEGORY_LANG_MAX_LEN)
        if not _LANG_RE.fullmatch(lang):
            raise err.validation_error(
                f"names[{i}].lang must be a lowercase language code such as zh or en, got {lang!r}"
            )
        name = _text(item.get("name"), f"names[{i}].name", max_len=CATEGORY_NAME_MAX_LEN)
        kind = coerce_enum(NameKind, item.get("kind"), field=f"names[{i}].kind")
        if (lang, name) in seen:
            raise err.validation_error(f"names[{i}]: the {lang} name {name!r} is listed twice")
        seen.add((lang, name))
        if kind is NameKind.PRIMARY:
            if lang in primaries:
                raise err.validation_error(f"names[{i}]: {lang} already has a primary name")
            primaries.add(lang)
        names.append({"lang": lang, "name": name, "kind": kind.value})
    if CATEGORY_DISPLAY_LANG not in primaries:
        raise err.validation_error(
            f"names must include the {CATEGORY_DISPLAY_LANG} primary name (the one results show)"
        )
    return names


def normalize_cuisines(value: Any) -> list[str]:
    """Cuisine codes, duplicates removed (the first one keeps its place)."""
    if not isinstance(value, (list, tuple)):
        raise err.validation_error(f"cuisines must be an array of strings, got {type(value).__name__}")
    cuisines: list[str] = []
    for i, item in enumerate(value):
        cuisine = _text(item, f"cuisines[{i}]", max_len=CATEGORY_CUISINE_MAX_LEN)
        if not _CUISINE_RE.fullmatch(cuisine):
            raise err.validation_error(
                f"cuisines[{i}] must be lowercase letters, digits and underscores, starting with a "
                f"letter (e.g. chinese); got {cuisine!r}"
            )
        if cuisine not in cuisines:
            cuisines.append(cuisine)
    if len(cuisines) > CATEGORY_MAX_CUISINES:
        raise err.validation_error(f"too many cuisines ({len(cuisines)} > {CATEGORY_MAX_CUISINES})")
    return cuisines


def _sort_order(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not CATEGORY_SORT_ORDER_MIN <= value <= CATEGORY_SORT_ORDER_MAX
    ):
        raise err.validation_error(f"sort_order must be an integer, got {value!r}")
    return value


def _is_active(value: Any) -> bool:
    if not isinstance(value, bool):
        raise err.validation_error(f"is_active must be true or false, got {value!r}")
    return value


_NORMALIZERS = {
    "code": normalize_category_code,
    "parent_id": lambda value: _optional_id(value, "parent_id"),
    "names": normalize_category_names,
    "cuisines": normalize_cuisines,
    "heating_group_id": lambda value: _optional_id(value, "heating_group_id"),
    "sort_order": _sort_order,
    "is_active": _is_active,
}
# What a create request may leave out
_CREATE_DEFAULTS: dict[str, Any] = {
    "parent_id": None,
    "cuisines": [],
    "heating_group_id": None,
    "sort_order": 0,
    "is_active": True,
}


def normalize_category_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    """A create request (partial=False: code and names required, the rest defaulted) or a patch
    (partial=True: only the keys given, at least one). A key outside CATEGORY_WRITE_FIELDS is
    rejected; in a patch, parent_id / heating_group_id may be None (move to the root / clear).
    """
    if not isinstance(fields, Mapping):
        raise err.validation_error("category fields must be an object")
    unknown = sorted(str(key) for key in set(fields) - set(CATEGORY_WRITE_FIELDS))
    if unknown:
        raise err.validation_error(f"unknown category field(s): {', '.join(unknown)}")
    if partial and not fields:
        raise err.validation_error("no category fields to update")
    out: dict[str, Any] = {}
    for key in CATEGORY_WRITE_FIELDS:
        if key in fields:
            out[key] = _NORMALIZERS[key](fields[key])
        elif not partial:
            if key not in _CREATE_DEFAULTS:
                raise err.validation_error(f"{key} is required")
            default = _CREATE_DEFAULTS[key]
            out[key] = list(default) if isinstance(default, list) else default
    return out


def check_root_code(code: str) -> None:
    """Roots are the domains; any other code needs a parent."""
    if code not in ROOT_CODES:
        raise err.validation_error(
            f"a root category must use a domain code ({', '.join(ROOT_CODES)}); {code!r} needs a parent"
        )


def display_name(names: Sequence[Mapping[str, Any]]) -> str | None:
    """The name results show for a category: its zh primary name, if it has one."""
    for entry in names:
        if entry.get("lang") == CATEGORY_DISPLAY_LANG and entry.get("kind") == NameKind.PRIMARY.value:
            return str(entry.get("name"))
    return None
