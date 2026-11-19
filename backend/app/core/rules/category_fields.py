"""类别树的写入规则（categories + category_names）：创建或修改请求可以带什么。

这里的检查都不需要数据库：形状、长度和写法（app.core.category_rules），以及

- 名称：每个 (lang, name) 一行；每种语言最多一个主名称；必须有 zh 主名称，
  因为结果和图库把它显示为图片的类别；
- 根节点：只有领域（app.core.enums.CategoryDomain）可以位于树的顶层。

需要已存的树才能做的检查（父节点存在、code 没被占用、不能移到自己的后代下面）
在写入事务里进行，见 app.repositories.categories。
输入不合法时抛出 VALIDATION_ERROR，并指明字段。
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

# 请求可以设置的列，按检查顺序排列
CATEGORY_WRITE_FIELDS = (
    "code",
    "parent_id",
    "names",
    "cuisines",
    "heating_group_id",
    "sort_order",
    "is_active",
)
# 根节点可以使用的 code
ROOT_CODES = tuple(member.value for member in CategoryDomain)

_CODE_RE = re.compile(CATEGORY_CODE_PATTERN)
_LANG_RE = re.compile(CATEGORY_LANG_PATTERN)
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


def normalize_category_id(value: Any, field: str = "category_id") -> int:
    """行 id：正整数（True 不能当成 1）。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _PG_INT_MAX:
        raise err.validation_error(f"{field} must be a positive integer, got {value!r}")
    return value


def _optional_id(value: Any, field: str) -> int | None:
    return None if value is None else normalize_category_id(value, field)


def normalize_category_code(value: Any) -> str:
    code = _text(value, "code", max_len=CATEGORY_CODE_MAX_LEN)
    if not _CODE_RE.fullmatch(code):
        raise err.validation_error(
            "code must use uppercase/lowercase letters, digits and underscores in dot-separated segments, "
            f"starting with a letter (e.g. dish.pizza); got {code!r}"
        )
    return code


def normalize_category_names(value: Any) -> list[dict[str, str]]:
    """按给定顺序的 [{lang, name, kind}]；规则见模块文档字符串。"""
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
    """菜系 code，去掉重复项（重复的保留第一次出现的位置）。"""
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
# 创建请求可以不写的字段
_CREATE_DEFAULTS: dict[str, Any] = {
    "parent_id": None,
    "cuisines": [],
    "heating_group_id": None,
    "sort_order": 0,
    "is_active": True,
}


def normalize_category_fields(fields: Any, *, partial: bool) -> dict[str, Any]:
    """创建请求（partial=False：code 和 names 必填，其余取默认值）或修改请求
    （partial=True：只处理传来的键，至少一个）。CATEGORY_WRITE_FIELDS 以外的键会被拒绝；
    修改请求里 parent_id / heating_group_id 可以为 None（移到根节点 / 清空）。
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
    """根节点是各个领域；其他 code 都需要父节点。"""
    if code.lower() not in ROOT_CODES:
        raise err.validation_error(
            f"a root category must use a domain code ({', '.join(ROOT_CODES)}); {code!r} needs a parent"
        )


def display_name(names: Sequence[Mapping[str, Any]]) -> str | None:
    """结果里显示的类别名：它的 zh 主名称（有的话）。"""
    for entry in names:
        if entry.get("lang") == CATEGORY_DISPLAY_LANG and entry.get("kind") == NameKind.PRIMARY.value:
            return str(entry.get("name"))
    return None
