"""Write rules for one stored image as the gallery edits it (PATCH /api/images/{image_id}).

Everything here needs no database: which fields a patch may carry, what null means for each,
and how label_status follows the category (images invariant: unlabeled <=> no category). The
checks that need the stored row - the new category exists and is active - run in the write
transaction (app.repositories.images.patch_image), which settles the label with
resolve_label_status against the row it locked.

    field                         null means                        vector payload
    category_id                   clear it: the image is unlabeled  yes (scenes follow it)
    label_status                  refused: leave it out to follow   yes
                                  the category
    description                   ""                                no
    tags                          []                                no
    food_state                    clear it                          yes
    food_processing, food_count,  clear it                          no
    total / water / main mass

A field left out stays as it is. A patch that changes a payload field bumps
images.payload_version (the HTTP layer then pushes the payload).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import errors as err
from app.core.enums import LabelStatus
from app.services.retrieval.category_fields import normalize_category_id
from app.services.retrieval.enum_fields import coerce_enum
from app.services.retrieval.food_fields import FOOD_FIELDS, coerce_food_field
from app.services.retrieval.text_fields import normalize_description, normalize_tags

# The fields a patch may carry, in the order they are checked
IMAGE_PATCH_FIELDS = ("category_id", "label_status", "description", "tags", *FOOD_FIELDS)


def normalize_image_patch(fields: Any) -> dict[str, Any]:
    """The given fields, each normalized (see the module docstring); at least one, no others."""
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
    """label_status of an image the patch leaves with new_category_id.

    A label sent (requested) is kept when it fits the category. Left out, the label follows the
    category: none -> unlabeled; set or changed by this patch -> verified (a person picked it in
    the gallery); unchanged -> the current label. VALIDATION_ERROR when the label sent
    contradicts the category.
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
