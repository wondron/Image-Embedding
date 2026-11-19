"""images：food_state 之外的食物属性——food_processing、food_weight_g、food_count。

Revision ID: 20261109_0005
Revises: 20261109_0004
Create Date: 2026-11-09

三个都可以为空（未知）：
- food_processing：和 food_state 一样的可编辑枚举（取值在 config/param/enums.yaml，没有 CHECK）；
- food_weight_g：  克，CHECK > 0；
- food_count：     个数，CHECK > 0。
它们都不在 Qdrant payload 里，所以已有的向量点不需要重新推送。

不带默认值的 ADD COLUMN 只改系统目录。每个新的 CHECK 都会让 PostgreSQL 在 ALTER 的
ACCESS EXCLUSIVE 锁下扫描一次 images（这时每一行都是 NULL，所以不会失败）；表很大时
请在空闲时段执行。降级会删除这些列及其数据。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261109_0005"
down_revision: Union[str, None] = "20261109_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE images
  ADD COLUMN food_processing varchar(32),
  ADD COLUMN food_weight_g integer,
  ADD COLUMN food_count integer,
  ADD CONSTRAINT ck_images_food_weight_positive CHECK (food_weight_g > 0),
  ADD CONSTRAINT ck_images_food_count_positive CHECK (food_count > 0);
COMMENT ON COLUMN images.food_processing IS '加工状态（切片、切块、整个、切丝等），可为空，取值见 config/param/enums.yaml';
COMMENT ON COLUMN images.food_weight_g IS '食物重量（克），可为空';
COMMENT ON COLUMN images.food_count IS '食物个数，可为空';
"""

DOWNGRADE_SQL = r"""
ALTER TABLE images
  DROP COLUMN food_count,
  DROP COLUMN food_weight_g,
  DROP COLUMN food_processing;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
