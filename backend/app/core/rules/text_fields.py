"""图片的自由文本字段（images.description、images.tags）。"""

from __future__ import annotations

from typing import Any

from app.core import errors as err

# API 一侧的长度上限；这两列本身是不限长的 text / text[]。
MAX_DESCRIPTION_LENGTH = 1000
MAX_TAGS = 32
MAX_TAG_LENGTH = 64
# 搜索结果卡把标签作为一段文本（契约 §6.1.1，和 FlyB 的标题共用这个字段）。
TAG_SEPARATOR = "、"


def tags_text(value: Any) -> str:
    """images.tags 作为结果卡的 tags 文本：用 TAG_SEPARATOR 连接；没有标签时为 ""。
    只用于显示：标签本身可能包含分隔符。
    """
    if isinstance(value, (list, tuple)):
        return TAG_SEPARATOR.join(str(tag) for tag in value if tag)
    return str(value or "")


def normalize_description(value: Any) -> str:
    """None / 空白 -> ""（列的默认值）；否则为去掉首尾空白、不超过上限的文本。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise err.validation_error(f"description must be a string, got {type(value).__name__}")
    text = value.strip()
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise err.validation_error(
            f"description is too long ({len(text)} > {MAX_DESCRIPTION_LENGTH} characters)"
        )
    return text


def normalize_tags(value: Any) -> list[str]:
    """None -> []（列的默认值）；否则每个标签去掉首尾空白、丢掉空的、去掉重复项
    （重复的保留第一次出现的位置），并且不超过上限。
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise err.validation_error(f"tags must be an array of strings, got {type(value).__name__}")
    tags: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise err.validation_error(f"tags[{i}] must be a string, got {type(item).__name__}")
        tag = item.strip()
        if not tag or tag in seen:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise err.validation_error(
                f"tags[{i}] is too long ({len(tag)} > {MAX_TAG_LENGTH} characters)"
            )
        seen.add(tag)
        tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise err.validation_error(f"too many tags ({len(tags)} > {MAX_TAGS})")
    return tags
