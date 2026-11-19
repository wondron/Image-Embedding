"""场景配置的写入规则（scenes、scene_labels、scene_rules）：请求可以带什么。

这里的检查都不需要数据库：形状、长度和写法（app.core.scene_rules），以及

- 场景的 code 创建后就固定（它在每张图的 Qdrant payload 里），也不能是
  请求里仍然会映射的 MVP code 之一（SCENE_ALIASES）；
- 默认场景（DEFAULT_SCENE）始终包含所有图片：不能停用，不能改成按规则的场景，
  也不能删除；
- 一条规则针对一个类别（含其子树）或一个菜系，不能两者都有；只有类别规则能映射到标签。
  修改请求可以原地替换规则的对象（一个事务，规则保留原 id），也可以修改或清空标签。

需要已存配置的检查（场景存在、code 没被占用、标签属于这个场景、规则没有重复）
在写入事务里进行，见 app.repositories.scene_config。输入不合法时抛出 VALIDATION_ERROR，
并指明字段。

哪些写入需要重算已存图片（POST /api/scenes/sync）也在这里决定
（scene_flags_need_sync、rule_write_needs_sync）：场景归属取决于场景的 is_active /
include_all 和它的规则。名称、描述、outputs_heating 和标签每次请求时读取，
所以修改后立刻生效。
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
    """去掉首尾空白、非空且不超过长度上限的文本。"""
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
    """行 id：正整数（True 不能当成 1）。"""
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


# -- 场景 ---------------------------------------------------------------------------------

def normalize_scene_code(value: Any) -> str:
    code = _code(value, "code", pattern=_SCENE_CODE_RE, max_len=SCENE_CODE_MAX_LEN)
    if code in SCENE_ALIASES:
        raise err.validation_error(
            f"code {code!r} is an old scene code that requests still map to {SCENE_ALIASES[code]!r}; choose another"
        )
    return code


def _description(value: Any) -> str | None:
    """为空时为 None（这一列可以为空）。"""
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
    """创建请求（code 和 name 必填）或修改请求（只处理传来的键；code 不能改）。"""
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
    """默认场景为什么不允许这个修改；允许时（或者不是默认场景）为 None。"""
    if code != DEFAULT_SCENE:
        return None
    if changes.get("is_active") is False:
        return f"the default scene {DEFAULT_SCENE!r} cannot be disabled: requests without a scene use it"
    if changes.get("include_all") is False:
        return f"the default scene {DEFAULT_SCENE!r} must keep containing every image"
    return None


def _membership_shape(scene: Mapping[str, Any]) -> tuple[bool, bool]:
    """场景的开关对归属的影响：（是否有成员，是否包含所有图片）。"""
    active = bool(scene["is_active"])
    return active, active and bool(scene["include_all"])


def scene_flags_need_sync(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> bool:
    """场景创建（before 为 None）或修改之后，是否需要重算已存图片。

    新的启用的 include_all 场景立刻包含所有图片；新的按规则的场景还没有规则。
    修改只在以下情况有影响：开启或关闭场景，或者让一个启用的场景在"所有图片"和
    "按规则"之间切换（在停用的场景上切换 include_all 没有影响）。
    """
    if before is None:
        return _membership_shape(after) == (True, True)
    return _membership_shape(before) != _membership_shape(after)


def rule_write_needs_sync(scene: Mapping[str, Any], *, target_changed: bool = True) -> bool:
    """一次规则写入是否改变归属：只有启用的、按规则的场景的规则才算（停用的场景
    没有成员；include_all 的场景忽略它的规则）。只改规则的标签不算：标签在每次搜索时查。
    """
    return target_changed and bool(scene["is_active"]) and not bool(scene["include_all"])


# -- 场景标签 -----------------------------------------------------------------------------

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


# -- 场景规则 -----------------------------------------------------------------------------

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
    """只有类别规则能映射到标签（ck_scene_rules_label_needs_category）。

    label_sent 为 False：修改请求把规则改成了菜系规则，却保留了标签；说明怎样清空。
    """
    if rule.get("label_id") is not None and rule.get("category_id") is None:
        hint = "" if label_sent else "; send label_id: null with the new cuisine to clear its label"
        raise err.validation_error("only a category rule can map to a label" + hint)


def normalize_rule_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    """创建：{category_id | cuisine, label_id?}。

    修改：只处理传来的键。新的对象（category_id 或 cuisine，恰好一个非 null）
    替换旧的，所以结果里两个键都有；label_id 为 null 时清空标签。
    标签是否仍适用于修改后的规则，对照已存的规则检查（check_rule_label）。
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
