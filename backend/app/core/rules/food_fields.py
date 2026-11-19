"""图片的食物标注：六个可选字段，单独存在 image_foods 表里。

和 image_embeddings 一样，食物数据是 images 的子表（app.models.food.ImageFood）：
每张图最多一行，随图片一起删除；没有这一行表示什么都没标注。
每个字段都是可选的（非必填）：

- food_state（生/熟/冷冻）和 food_processing（切片/切块/整个/切丝……）：可编辑枚举
  （config/param/enums.yaml）；
- food_count：个数，>= 1；
- total_mass_g（总质量）、main_ingredient_mass_g（主食材质量）：克，>= 1；
  water_mass_g（含水质量）：克，>= 0。
数据库里用 CHECK 约束重复了这些范围。

只有 food_state 在 Qdrant payload 里（修改它会递增 payload_version）；其余五个
只是普通元数据。只有场景开启 outputs_heating 时，搜索结果和列表项才带这六个字段
（契约 §6.1 P9，和 heating_group 的规则相同）。
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.core import errors as err
from app.core.enums import FOOD_PROCESSING, FOOD_STATE
from app.core.rules.enum_fields import coerce_optional_enum

FOOD_FIELDS = (
    "food_state",
    "food_processing",
    "food_count",
    "total_mass_g",
    "water_mass_g",
    "main_ingredient_mass_g",
)
PG_INT_MAX = 2_147_483_647  # 整数列

_FOOD_ENUMS = {"food_state": FOOD_STATE, "food_processing": FOOD_PROCESSING}
# 整数字段 -> 允许的最小值（image_foods 上的 CHECK 约束也是这样）
_FOOD_MINIMUMS = {"food_count": 1, "total_mass_g": 1, "water_mass_g": 0, "main_ingredient_mass_g": 1}


def coerce_food_field(key: str, value: Any) -> str | int | None:
    """请求或数据库行里的一个食物字段；为空时为 None，不合法时 VALIDATION_ERROR。"""
    if key in _FOOD_ENUMS:
        return coerce_optional_enum(_FOOD_ENUMS[key], value, field=key)
    if key in _FOOD_MINIMUMS:
        return coerce_optional_int(value, field=key, minimum=_FOOD_MINIMUMS[key])
    raise KeyError(f"not a food field: {key!r}")


def coerce_optional_int(value: Any, *, field: str, minimum: int) -> int | None:
    """>= minimum 的整数（int、值为整数的 float 或数字字符串）；为空时为 None。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):  # True 不能变成 1
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str):
        with contextlib.suppress(ValueError):
            number = int(value.strip())
    if number is None or not minimum <= number <= PG_INT_MAX:
        raise err.validation_error(f"{field} must be a whole number >= {minimum}, got {value!r}")
    return number
