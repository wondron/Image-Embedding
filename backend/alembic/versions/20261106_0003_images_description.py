"""images.description: free-text description, default empty string.

Revision ID: 20261106_0003
Revises: 20261105_0002
Create Date: 2026-11-06

ADD COLUMN with a constant default is metadata-only on PostgreSQL >= 11
(no table rewrite, no long lock). Existing rows read as ''.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261106_0003"
down_revision: Union[str, None] = "20261105_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE images ADD COLUMN description text NOT NULL DEFAULT '';
COMMENT ON COLUMN images.description IS '图片描述，默认空字符串；不进 Qdrant payload，修改不影响 payload_version';
"""

DOWNGRADE_SQL = r"""
ALTER TABLE images DROP COLUMN description;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
