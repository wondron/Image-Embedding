"""Free-text image fields (images.description)."""

from __future__ import annotations

from typing import Any

from app.core import errors as err

# API-side bound; the column itself is unbounded text.
MAX_DESCRIPTION_LENGTH = 1000


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
