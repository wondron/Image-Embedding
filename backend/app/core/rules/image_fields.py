"""图库编辑一张已存图片时的写入规则（PATCH /api/images/{image_id}）。

这里的检查都不需要数据库：修改请求可以带哪些字段、每个字段的 null 是什么意思，
以及 label_status 怎样跟随类别（images 的不变式：unlabeled <=> 没有类别）。
需要已存行的检查（新类别存在且启用）在写入事务里进行
（app.repositories.images.patch_image），它对锁住的那一行用 resolve_label_status
确定标注状态。

    字段                          null 的含义                       向量 payload
    category_id                   清空：图片变为未标注              是（场景随之变化）
    label_status                  拒绝：不传就跟随类别              是
    description                   ""                                否
    tags                          []                                否
    food_state                    清空                              是
    food_processing, food_count,  清空                              否
    total / water / main mass

没传的字段保持不变。修改了 payload 字段的请求会递增
images.payload_version（随后 HTTP 层推送 payload）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import errors as err
from app.core.enums import LabelStatus
from app.core.rules.category_fields import normalize_category_id
from app.core.rules.enum_fields import coerce_enum
from app.core.rules.food_fields import FOOD_FIELDS, coerce_food_field
from app.core.rules.text_fields import normalize_description, normalize_tags

# 修改请求可以带的字段，按检查顺序排列
IMAGE_PATCH_FIELDS = ("category_id", "label_status", "description", "tags", *FOOD_FIELDS)


def normalize_image_patch(fields: Any) -> dict[str, Any]:
    """传来的字段，每个都已规范化（见模块文档字符串）；至少一个，不能有其他字段。"""
    if not isinstance(fields, Mapping):
        raise err.validation_error("image fields must be an object")
    unknown = sorted(str(key) for key in set(fields) - set(IMAGE_PATCH_FIELDS))
    if unknown:
        raise err.validation_error(f"unknown image field(s): {', '.join(unknown)}")
    if not fields:
        raise err.validation_error("no image fields to update")
    out: dict[str, Any] = {}
    for key in IMAGE_PATCH_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "category_id":
            out[key] = None if value is None else normalize_category_id(value)
        elif key == "label_status":
            if value is None:
                raise err.validation_error(
                    "label_status cannot be null: leave it out to follow category_id, "
                    "or send unlabeled / auto / verified"
                )
            out[key] = coerce_enum(LabelStatus, value, field="label_status")
        elif key == "description":
            out[key] = normalize_description(value)
        elif key == "tags":
            out[key] = normalize_tags(value)
        else:
            out[key] = coerce_food_field(key, value)
    return out


def resolve_label_status(
    *,
    current_label: LabelStatus | str,
    current_category_id: int | None,
    new_category_id: int | None,
    requested: LabelStatus | str | None,
) -> LabelStatus:
    """修改后类别为 new_category_id 的图片的 label_status。

    请求里传了标注状态（requested）且和类别相符时保留。没传时标注状态跟随类别：
    没有类别 -> unlabeled；本次修改设置或改变了类别 -> verified（有人在图库里选的）；
    类别没变 -> 保持当前的标注状态。传来的标注状态和类别矛盾时 VALIDATION_ERROR。
    """
    if requested is not None:
        label = LabelStatus(requested)
        if label is LabelStatus.UNLABELED and new_category_id is not None:
            raise err.validation_error(
                "label_status=unlabeled needs category_id: null (an unlabeled image has no category)"
            )
        if label is not LabelStatus.UNLABELED and new_category_id is None:
            raise err.validation_error(
                f"label_status={label.value} needs a category: the image has none, send category_id too"
            )
        return label
    if new_category_id is None:
        return LabelStatus.UNLABELED
    if new_category_id != current_category_id:
        return LabelStatus.VERIFIED
    return LabelStatus(current_label)
