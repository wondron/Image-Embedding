"""images.source_type / license / food_state become editable enums: drop their CHECK lists.

Revision ID: 20261109_0004
Revises: 20261106_0003
Create Date: 2026-11-09

The allowed values of these three columns now live in config/param/enums.yaml and can grow at
runtime (POST /api/enums/{name}/values), so a CHECK listing them would reject every new value.
The application validates them before each write (app.services.retrieval.enum_fields); the
columns stay varchar(32). System enums (label_status, kind, origin, distance, status,
review_status) keep their CHECK constraints.

Dropping a CHECK only touches the catalog (brief ACCESS EXCLUSIVE lock, no table scan).
Downgrade restores the 0001 CHECK lists and fails with a check violation (rolled back, nothing
changed) while rows still hold values added after this revision; change those rows first.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261109_0004"
down_revision: Union[str, None] = "20261106_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE images
  DROP CONSTRAINT ck_images_source_type,
  DROP CONSTRAINT ck_images_license,
  DROP CONSTRAINT ck_images_food_state;
COMMENT ON COLUMN images.source_type IS '来源大类，取值见 config/param/enums.yaml';
COMMENT ON COLUMN images.license IS '许可，取值见 config/param/enums.yaml';
COMMENT ON COLUMN images.food_state IS '食物状态，可为空，取值见 config/param/enums.yaml';
"""

DOWNGRADE_SQL = r"""
ALTER TABLE images
  ADD CONSTRAINT ck_images_source_type CHECK (
    source_type IN ('crawled', 'device', 'open_dataset', 'user_upload', 'external')
  ),
  ADD CONSTRAINT ck_images_license CHECK (
    license IN ('commercial', 'research_only', 'unknown')
  ),
  ADD CONSTRAINT ck_images_food_state CHECK (
    food_state IS NULL OR food_state IN ('raw', 'cooked', 'frozen')
  );
COMMENT ON COLUMN images.source_type IS '来源大类';
COMMENT ON COLUMN images.license IS '许可';
COMMENT ON COLUMN images.food_state IS '食物状态：生 / 熟 / 冷冻';
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
