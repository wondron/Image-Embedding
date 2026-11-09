"""images: food attributes besides food_state — food_processing, food_weight_g, food_count.

Revision ID: 20261109_0005
Revises: 20261109_0004
Create Date: 2026-11-09

All three are nullable (unknown):
- food_processing: editable enum like food_state (values in config/param/enums.yaml, no CHECK);
- food_weight_g:   grams, CHECK > 0;
- food_count:      pieces, CHECK > 0.
None of them is in the Qdrant payload, so existing points need no re-push.

ADD COLUMN without a default only touches the catalog. Each new CHECK makes PostgreSQL scan
images once under the ALTER's ACCESS EXCLUSIVE lock (every row is NULL there, so it cannot
fail); on a large table run this at a quiet time. Downgrade drops the columns and their data.
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
