"""image_foods: the optional food annotation of an image (app.models.food.ImageFood).

Written in the same transaction as the images row (insert_image / update_image in
app.repositories.images); read with a LEFT JOIN, so an image without a row reads as all
NULL. Column names in the SQL come from FOOD_FIELDS only, never from the request.
"""

from __future__ import annotations

from typing import Any

from app.core.rules.food_fields import FOOD_FIELDS

# For image queries that alias images as i: extra SELECT columns and the join that feeds them
FOOD_SELECT = ", ".join(f"f.{key}" for key in FOOD_FIELDS)
FOOD_JOIN = "LEFT JOIN image_foods f ON f.image_id = i.id "


def write_image_food(conn: Any, image_id: str, fields: dict[str, Any]) -> None:
    """Insert the row, or overwrite only the given columns of an existing one."""
    from sqlalchemy import text

    keys = [key for key in FOOD_FIELDS if key in fields]
    if not keys:
        return
    columns = ", ".join(keys)
    values = ", ".join(f":{key}" for key in keys)
    updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in keys)
    conn.execute(
        text(
            f"INSERT INTO image_foods (image_id, {columns}) VALUES (CAST(:image_id AS uuid), {values}) "
            f"ON CONFLICT (image_id) DO UPDATE SET {updates}, updated_at = now()"
        ),
        {"image_id": image_id, **{key: fields[key] for key in keys}},
    )
