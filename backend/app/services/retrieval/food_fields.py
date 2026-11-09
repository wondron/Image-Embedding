"""Food annotation of an image: six optional fields, kept in their own table image_foods.

Like image_embeddings, food data is a child table of images (app.models.food.ImageFood):
at most one row per image, deleted with the image; no row means nothing is annotated.
Every field is optional (非必填):

- food_state (生/熟/冷冻) and food_processing (切片/切块/整个/切丝……): editable enums
  (config/param/enums.yaml);
- food_count: pieces, >= 1;
- total_mass_g (总质量), main_ingredient_mass_g (主食材质量): grams, >= 1;
  water_mass_g (含水质量): grams, >= 0.
The database repeats these bounds as CHECK constraints.

Only food_state is in the Qdrant payload (changing it bumps payload_version); the other
five are plain metadata. Search hits and list items carry all six only when the scene
outputs_heating (contract §6.1 P9, same rule as heating_group).
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.core import errors as err
from app.core.enums import FOOD_PROCESSING, FOOD_STATE
from app.services.retrieval.enum_fields import coerce_optional_enum

FOOD_FIELDS = (
    "food_state",
    "food_processing",
    "food_count",
    "total_mass_g",
    "water_mass_g",
    "main_ingredient_mass_g",
)
PG_INT_MAX = 2_147_483_647  # integer columns

_FOOD_ENUMS = {"food_state": FOOD_STATE, "food_processing": FOOD_PROCESSING}
# whole-number fields -> smallest allowed value (the CHECKs on image_foods say the same)
_FOOD_MINIMUMS = {"food_count": 1, "total_mass_g": 1, "water_mass_g": 0, "main_ingredient_mass_g": 1}


def coerce_food_field(key: str, value: Any) -> str | int | None:
    """One food field from a request or a row; blank -> None, bad -> VALIDATION_ERROR."""
    if key in _FOOD_ENUMS:
        return coerce_optional_enum(_FOOD_ENUMS[key], value, field=key)
    if key in _FOOD_MINIMUMS:
        return coerce_optional_int(value, field=key, minimum=_FOOD_MINIMUMS[key])
    raise KeyError(f"not a food field: {key!r}")


def coerce_optional_int(value: Any, *, field: str, minimum: int) -> int | None:
    """Whole number >= minimum (an int, an integral float or a numeric string); blank -> None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):  # True must not become 1
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str):
        with contextlib.suppress(ValueError):
            number = int(value.strip())
    if number is None or not minimum <= number <= PG_INT_MAX:
        raise err.validation_error(f"{field} must be a whole number >= {minimum}, got {value!r}")
    return number
