"""食物标注从 images 移到单独的 image_foods 表（和 image_embeddings 一样）。

Revision ID: 20261109_0006
Revises: 20261109_0005
Create Date: 2026-11-09

image_foods：每张图片最多一行（主键 image_id，外键 images ON DELETE CASCADE），六个
可为空的字段——food_state、food_processing、food_count、total_mass_g（总质量）、
water_mass_g（含水质量）、main_ingredient_mass_g（主食材质量）。food_state / food_processing
是可编辑枚举（取值在 config/param/enums.yaml，没有 CHECK）；个数和质量有 CHECK
（含水质量可以为 0）。

数据：设置了任一食物列的图片都得到一行；images.food_weight_g 变成 total_mass_g。
然后删除 images.food_state / food_processing / food_weight_g / food_count（它们的 CHECK
随之删除）。food_state 以相同的取值留在 Qdrant payload 里，所以不需要重新推送。

降级会把这四列放回 images（food_weight_g <- total_mass_g）并删除 image_foods：
water_mass_g 和 main_ingredient_mass_g 会丢失。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261109_0006"
down_revision: Union[str, None] = "20261109_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
CREATE TABLE image_foods (
  image_id uuid NOT NULL,
  food_state varchar(32),
  food_processing varchar(32),
  food_count integer,
  total_mass_g integer,
  water_mass_g integer,
  main_ingredient_mass_g integer,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_image_foods PRIMARY KEY (image_id),
  CONSTRAINT fk_image_foods_image_id_images FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE CASCADE,
  CONSTRAINT ck_image_foods_food_count_positive CHECK (food_count > 0),
  CONSTRAINT ck_image_foods_total_mass_positive CHECK (total_mass_g > 0),
  CONSTRAINT ck_image_foods_water_mass_nonnegative CHECK (water_mass_g >= 0),
  CONSTRAINT ck_image_foods_main_ingredient_mass_positive CHECK (main_ingredient_mass_g > 0)
);

INSERT INTO image_foods (image_id, food_state, food_processing, food_count, total_mass_g)
SELECT id, food_state, food_processing, food_count, food_weight_g
FROM images
WHERE food_state IS NOT NULL OR food_processing IS NOT NULL
   OR food_count IS NOT NULL OR food_weight_g IS NOT NULL;

ALTER TABLE images
  DROP COLUMN food_state,
  DROP COLUMN food_processing,
  DROP COLUMN food_weight_g,
  DROP COLUMN food_count;

COMMENT ON TABLE image_foods IS '图片的食物标注';
COMMENT ON COLUMN image_foods.food_state IS '食物状态（生 / 熟 / 冷冻），取值见 config/param/enums.yaml';
COMMENT ON COLUMN image_foods.food_processing IS '加工状态（切片、切块、整个、切丝等），取值见 config/param/enums.yaml';
COMMENT ON COLUMN image_foods.food_count IS '食物个数';
COMMENT ON COLUMN image_foods.total_mass_g IS '总质量（克）';
COMMENT ON COLUMN image_foods.water_mass_g IS '含水质量（克），可以为 0';
COMMENT ON COLUMN image_foods.main_ingredient_mass_g IS '主食材质量（克）';
COMMENT ON COLUMN image_foods.updated_at IS '更新时间';
"""

# 回到 0005 的结构：类型、CHECK 名称和注释都和 0004 / 0005 留下的一样
DOWNGRADE_SQL = r"""
ALTER TABLE images
  ADD COLUMN food_state varchar(32),
  ADD COLUMN food_processing varchar(32),
  ADD COLUMN food_weight_g integer,
  ADD COLUMN food_count integer,
  ADD CONSTRAINT ck_images_food_weight_positive CHECK (food_weight_g > 0),
  ADD CONSTRAINT ck_images_food_count_positive CHECK (food_count > 0);

UPDATE images i
SET food_state = f.food_state,
    food_processing = f.food_processing,
    food_weight_g = f.total_mass_g,
    food_count = f.food_count
FROM image_foods f
WHERE f.image_id = i.id;

DROP TABLE image_foods;

COMMENT ON COLUMN images.food_state IS '食物状态，可为空，取值见 config/param/enums.yaml';
COMMENT ON COLUMN images.food_processing IS '加工状态（切片、切块、整个、切丝等），可为空，取值见 config/param/enums.yaml';
COMMENT ON COLUMN images.food_weight_g IS '食物重量（克），可为空';
COMMENT ON COLUMN images.food_count IS '食物个数，可为空';
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
