"""images.source_type / license / food_state 改为可编辑枚举：删除它们的 CHECK 列表。

Revision ID: 20261109_0004
Revises: 20261106_0003
Create Date: 2026-11-09

这三列的允许值现在放在 config/param/enums.yaml 里，并且可以在运行时增加
（POST /api/enums/{name}/values），所以列出取值的 CHECK 会拒绝每一个新值。
应用在每次写入之前校验它们（app.services.retrieval.enum_fields）；这些列仍然是
varchar(32)。系统枚举（label_status、kind、origin、distance、status、review_status）
保留它们的 CHECK 约束。

删除 CHECK 只改系统目录（短暂的 ACCESS EXCLUSIVE 锁，不扫描表）。
降级时恢复 0001 的 CHECK 列表；如果还有行保存着这个版本之后新增的值，降级会因为
违反检查而失败（回滚，什么都不变）；需要先修改那些行。
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
