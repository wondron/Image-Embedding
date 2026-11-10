"""images.tags: free-text tag array, '{}' while untagged; GIN index for exact-tag filters.

Revision ID: 20261109_0007
Revises: 20261109_0006
Create Date: 2026-11-09

ADD COLUMN with a constant default is metadata-only on PostgreSQL >= 11
(no table rewrite, no long lock). Existing rows read as '{}'. The GIN index is built
over those empty arrays, so it is quick, but plain CREATE INDEX still blocks writes to
images while it runs.

Downgrade drops the column: stored tags are lost.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261109_0007"
down_revision: Union[str, None] = "20261109_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE images ADD COLUMN tags text[] NOT NULL DEFAULT '{}';
CREATE INDEX ix_images_tags_gin ON images USING gin (tags);
COMMENT ON COLUMN images.tags IS '图片标签，如 {红薯叶,带皮}；未标注时为空数组；不进 Qdrant payload，修改不影响 payload_version';
"""

DOWNGRADE_SQL = r"""
DROP INDEX ix_images_tags_gin;
ALTER TABLE images DROP COLUMN tags;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
