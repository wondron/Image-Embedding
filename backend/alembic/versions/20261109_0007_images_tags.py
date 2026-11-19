"""images.tags：自由文本标签数组，没有标签时为 '{}'；GIN 索引用于精确标签过滤。

Revision ID: 20261109_0007
Revises: 20261109_0006
Create Date: 2026-11-09

在 PostgreSQL >= 11 上，带常量默认值的 ADD COLUMN 只改元数据
（不重写表，不长时间持锁）。已有的行读出来为 '{}'。GIN 索引建在这些空数组上，
所以很快，但普通的 CREATE INDEX 在运行期间仍然会阻塞对 images 的写入。

降级会删除这一列：已存的标签会丢失。
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
