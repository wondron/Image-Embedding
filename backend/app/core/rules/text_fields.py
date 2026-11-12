"""Free-text image fields (images.description, images.tags)."""

from __future__ import annotations

from typing import Any

from app.core import errors as err

# API-side bounds; the columns themselves are unbounded text / text[].
MAX_DESCRIPTION_LENGTH = 1000
MAX_TAGS = 32
MAX_TAG_LENGTH = 64


def normalize_description(value: Any) -> str:
    """None / blank -> "" (the column default); otherwise stripped text within the bound."""
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
    """None -> [] (the column default); otherwise each tag stripped, blanks dropped and
    duplicates removed (the first one keeps its place), within the bounds."""
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
