"""Category codes keep their spelling and are unique ignoring case.

Revision ID: 20261113_0010
Revises: 20261112_0009
Create Date: 2026-11-13

Keep the existing exact-code unique constraint and add a functional unique index
on lower(code). This also protects writers outside the application catalog lock.
Existing conflicting spellings are reported with their ids, codes and Chinese
names; the migration does not rename or delete any category.

The preflight is SQL so --sql output enforces the same check when executed later.
The table lock keeps concurrent writes out between the preflight and index build.
Downgrade removes only the new index; stored spellings and categories are kept.
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
