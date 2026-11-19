"""image_foods：图片的可选食物标注（app.models.food.ImageFood）。

和 images 行在同一个事务里写入（app.repositories.images 的 insert_image / update_image）；
用 LEFT JOIN 读取，所以没有这一行的图片读出来全是 NULL。SQL 里的列名只来自
FOOD_FIELDS，从不来自请求。
"""

from __future__ import annotations

from typing import Any

from app.core.rules.food_fields import FOOD_FIELDS

# 给把 images 别名为 i 的图片查询用：额外的 SELECT 列，以及提供这些列的 join
FOOD_SELECT = ", ".join(f"f.{key}" for key in FOOD_FIELDS)
FOOD_JOIN = "LEFT JOIN image_foods f ON f.image_id = i.id "


def write_image_food(conn: Any, image_id: str, fields: dict[str, Any]) -> None:
    """插入这一行，或者只覆盖已有行里传来的列。"""
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
