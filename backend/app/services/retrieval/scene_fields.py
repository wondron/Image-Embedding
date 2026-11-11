"""Scene configuration write rules (scenes, scene_labels, scene_rules): what a request may carry.

Everything here needs no database: shapes, lengths and spellings (app.core.scene_rules), plus

- a scene's code is fixed once created (it is in every image's Qdrant payload) and may not be
  one of the MVP codes that requests still map (SCENE_ALIASES);
- the default scene (DEFAULT_SCENE) keeps containing every image: it cannot be disabled or turned
  into a rule-based scene, nor deleted;
- a rule targets one category (with its subtree) or one cuisine, never both; only a category
  rule maps to a label. A patch may replace the target in place (one transaction, the rule keeps
  its id) and change or clear the label.

Checks that need the stored configuration (the scene exists, a code is free, a label belongs to
the scene, a rule is not a duplicate) run inside the write transaction, in
app.repositories.scene_config. Bad input raises VALIDATION_ERROR naming the field.

Which writes need stored images recomputed (POST /api/scenes/sync) is decided here too
(scene_flags_need_sync, rule_write_needs_sync): membership follows a scene's is_active /
include_all and its rules. Names, descriptions, outputs_heating and labels are read per request,
so changing them shows at once.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.core import errors as err
from app.core.category_rules import (
    CATEGORY_CUISINE_MAX_LEN,
    CATEGORY_CUISINE_PATTERN,
    CATEGORY_SORT_ORDER_MAX,
    CATEGORY_SORT_ORDER_MIN,
)
from app.core.scene_rules import (
    DEFAULT_SCENE,
    SCENE_ALIASES,
    SCENE_CODE_MAX_LEN,
    SCENE_CODE_PATTERN,
    SCENE_DESCRIPTION_MAX_LEN,
    SCENE_LABEL_CODE_MAX_LEN,
    SCENE_LABEL_CODE_PATTERN,
    SCENE_LABEL_NAME_MAX_LEN,
    SCENE_NAME_MAX_LEN,
)

SCENE_FIELDS = ("code", "name", "description", "include_all", "outputs_heating", "is_active")
LABEL_FIELDS = ("code", "name", "sort_order", "is_active")
RULE_FIELDS = ("category_id", "cuisine", "label_id")

_SCENE_CODE_RE = re.compile(SCENE_CODE_PATTERN)
_LABEL_CODE_RE = re.compile(SCENE_LABEL_CODE_PATTERN)
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


def _code(value: Any, field: str, *, pattern: re.Pattern[str], max_len: int) -> str:
    code = _text(value, field, max_len=max_len)
    if not pattern.fullmatch(code):
        raise err.validation_error(
            f"{field} must be lowercase letters, digits and underscores, starting with a letter; got {code!r}"
        )
    return code


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise err.validation_error(f"{field} must be true or false, got {value!r}")
    return value


def normalize_row_id(value: Any, field: str) -> int:
    """A row id: a positive int (True must not count as 1)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _PG_INT_MAX:
        raise err.validation_error(f"{field} must be a positive integer, got {value!r}")
    return value


def _check_fields(fields: Any, allowed: tuple[str, ...], what: str, *, partial: bool) -> Mapping[str, Any]:
    if not isinstance(fields, Mapping):
        raise err.validation_error(f"{what} fields must be an object")
    unknown = sorted(str(key) for key in set(fields) - set(allowed))
    if unknown:
        raise err.validation_error(f"unknown {what} field(s): {', '.join(unknown)}")
    if partial and not fields:
        raise err.validation_error(f"no {what} fields to update")
    return fields


# -- scenes -----------------------------------------------------------------------------

def normalize_scene_code(value: Any) -> str:
    code = _code(value, "code", pattern=_SCENE_CODE_RE, max_len=SCENE_CODE_MAX_LEN)
    if code in SCENE_ALIASES:
        raise err.validation_error(
            f"code {code!r} is an old scene code that requests still map to {SCENE_ALIASES[code]!r}; choose another"
        )
    return code


def _description(value: Any) -> str | None:
    """Blank -> None (the column is nullable)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise err.validation_error(f"description must be a string, got {type(value).__name__}")
    text = value.strip()
    if len(text) > SCENE_DESCRIPTION_MAX_LEN:
        raise err.validation_error(f"description is too long ({len(text)} > {SCENE_DESCRIPTION_MAX_LEN} characters)")
    return text or None


_SCENE_NORMALIZERS = {
    "code": normalize_scene_code,
    "name": lambda value: _text(value, "name", max_len=SCENE_NAME_MAX_LEN),
    "description": _description,
    "include_all": lambda value: _flag(value, "include_all"),
    "outputs_heating": lambda value: _flag(value, "outputs_heating"),
    "is_active": lambda value: _flag(value, "is_active"),
}
_SCENE_DEFAULTS: dict[str, Any] = {"description": None, "include_all": False, "outputs_heating": False, "is_active": True}


def normalize_scene_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    """A create request (code and name required) or a patch (only the keys given; code is fixed)."""
    fields = _check_fields(fields, SCENE_FIELDS, "scene", partial=partial)
    if partial and "code" in fields:
        raise err.validation_error("a scene's code cannot change (it is in every image's vector payload)")
    out: dict[str, Any] = {}
    for key in SCENE_FIELDS:
        if key in fields:
            out[key] = _SCENE_NORMALIZERS[key](fields[key])
        elif not partial:
            if key not in _SCENE_DEFAULTS:
                raise err.validation_error(f"{key} is required")
            out[key] = _SCENE_DEFAULTS[key]
    return out


def check_default_scene_change(code: str, changes: Mapping[str, Any]) -> str | None:
    """Why this change is not allowed on the default scene; None when it is (or not the default)."""
    if code != DEFAULT_SCENE:
        return None
    if changes.get("is_active") is False:
        return f"the default scene {DEFAULT_SCENE!r} cannot be disabled: requests without a scene use it"
    if changes.get("include_all") is False:
        return f"the default scene {DEFAULT_SCENE!r} must keep containing every image"
    return None


def _membership_shape(scene: Mapping[str, Any]) -> tuple[bool, bool]:
    """What a scene's flags do to membership: (it has members at all, it has every image)."""
    active = bool(scene["is_active"])
    return active, active and bool(scene["include_all"])


def scene_flags_need_sync(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> bool:
    """Whether stored images must be recomputed after a scene is created (before None) or changed.

    A new active include_all scene contains every image at once; a new rule-based one has no
    rules yet. A change matters when it turns the scene on or off, or switches an active scene
    between every image and its rules (include_all flipped on an inactive scene does nothing).
    """
    if before is None:
        return _membership_shape(after) == (True, True)
    return _membership_shape(before) != _membership_shape(after)


def rule_write_needs_sync(scene: Mapping[str, Any], *, target_changed: bool = True) -> bool:
    """Whether a rule write changes membership: only the rules of an active, rule-based scene
    count (an inactive scene has no members; an include_all one ignores its rules). Changing only
    a rule's label does not: labels are looked up per search."""
    return target_changed and bool(scene["is_active"]) and not bool(scene["include_all"])


# -- scene labels -----------------------------------------------------------------------

def _sort_order(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not CATEGORY_SORT_ORDER_MIN <= value <= CATEGORY_SORT_ORDER_MAX
    ):
        raise err.validation_error(f"sort_order must be an integer, got {value!r}")
    return value


_LABEL_NORMALIZERS = {
    "code": lambda value: _code(value, "code", pattern=_LABEL_CODE_RE, max_len=SCENE_LABEL_CODE_MAX_LEN),
    "name": lambda value: _text(value, "name", max_len=SCENE_LABEL_NAME_MAX_LEN),
    "sort_order": _sort_order,
    "is_active": lambda value: _flag(value, "is_active"),
}
_LABEL_DEFAULTS: dict[str, Any] = {"sort_order": 0, "is_active": True}


def normalize_label_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    fields = _check_fields(fields, LABEL_FIELDS, "label", partial=partial)
    out: dict[str, Any] = {}
    for key in LABEL_FIELDS:
        if key in fields:
            out[key] = _LABEL_NORMALIZERS[key](fields[key])
        elif not partial:
            if key not in _LABEL_DEFAULTS:
                raise err.validation_error(f"{key} is required")
            out[key] = _LABEL_DEFAULTS[key]
    return out


# -- scene rules ------------------------------------------------------------------------

def _cuisine(value: Any) -> str:
    cuisine = _text(value, "cuisine", max_len=CATEGORY_CUISINE_MAX_LEN)
    if not _CUISINE_RE.fullmatch(cuisine):
        raise err.validation_error(
            "cuisine must be lowercase letters, digits and underscores, starting with a letter "
            f"(e.g. chinese); got {cuisine!r}"
        )
    return cuisine


def _rule_target(fields: Mapping[str, Any]) -> dict[str, Any]:
    category_id = fields.get("category_id")
    cuisine = fields.get("cuisine")
    if (category_id is None) == (cuisine is None):
        raise err.validation_error("a rule needs exactly one of category_id and cuisine")
    return {
        "category_id": None if category_id is None else normalize_row_id(category_id, "category_id"),
        "cuisine": None if cuisine is None else _cuisine(cuisine),
    }


def check_rule_label(rule: Mapping[str, Any], *, label_sent: bool = True) -> None:
    """Only a category rule maps to a label (ck_scene_rules_label_needs_category).

    label_sent False: a patch moved the rule to a cuisine and kept its label; say how to clear it.
    """
    if rule.get("label_id") is not None and rule.get("category_id") is None:
        hint = "" if label_sent else "; send label_id: null with the new cuisine to clear its label"
        raise err.validation_error("only a category rule can map to a label" + hint)


def normalize_rule_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    """Create: {category_id | cuisine, label_id?}.

    Patch: only the keys given. A new target (category_id or cuisine, exactly one non-null)
    replaces the old one, so the result then has both keys; label_id null clears the label.
    Whether the label still fits the patched rule is checked against the stored rule
    (check_rule_label).
    """
    fields = _check_fields(fields, RULE_FIELDS, "rule", partial=partial)
    out: dict[str, Any] = {}
    if not partial or "category_id" in fields or "cuisine" in fields:
        out.update(_rule_target(fields))
    if not partial or "label_id" in fields:
        label_id = fields.get("label_id")
        out["label_id"] = None if label_id is None else normalize_row_id(label_id, "label_id")
    if not partial:
        check_rule_label(out)
    return out
