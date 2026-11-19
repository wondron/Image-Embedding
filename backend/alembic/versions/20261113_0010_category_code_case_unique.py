"""类别 code 保留原来的写法，并且不区分大小写地唯一。

Revision ID: 20261113_0010
Revises: 20261112_0009
Create Date: 2026-11-13

保留现有的按精确 code 的唯一约束，再加一个 lower(code) 上的函数唯一索引。
这也能防住应用目录锁之外的写入方。
已有的冲突写法会连同它们的 id、code 和中文名称一起报告；迁移不会重命名或删除
任何类别。

预检用 SQL 实现，这样 --sql 生成的脚本之后执行时也会做同样的检查。
表锁让并发写入进不来，直到预检和建索引完成。
降级只删除新的索引；已存的写法和类别都保留。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261113_0010"
down_revision: Union[str, None] = "20261112_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
LOCK TABLE categories IN SHARE ROW EXCLUSIVE MODE;

DO $category_code_check$
DECLARE
  conflicts text;
BEGIN
  SELECT string_agg(format('%s: %s', duplicates.normalized_code, duplicates.members), E'\n'
                    ORDER BY duplicates.normalized_code)
    INTO conflicts
    FROM (
      SELECT lower(c.code) AS normalized_code,
             string_agg(format('id=%s code=%L name=%L', c.id, c.code, n.name), ', ' ORDER BY c.id) AS members
        FROM categories c
        LEFT JOIN category_names n
          ON n.category_id = c.id AND n.lang = 'zh' AND n.kind = 'primary'
       GROUP BY lower(c.code)
      HAVING count(*) > 1
       ORDER BY lower(c.code)
       LIMIT 20
    ) duplicates;
  IF conflicts IS NOT NULL THEN
    RAISE EXCEPTION 'Category codes conflict ignoring case (up to 20 groups): %', E'\n' || conflicts
      USING HINT = 'Rename conflicting category codes so lower(code) is unique, then rerun alembic upgrade head. No categories were changed.';
  END IF;
END;
$category_code_check$;

CREATE UNIQUE INDEX uq_categories_code_lower ON categories (lower(code));
"""

DOWNGRADE_SQL = r"""
DROP INDEX uq_categories_code_lower;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
