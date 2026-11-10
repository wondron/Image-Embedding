"""One transaction-level advisory lock for the catalog: the category tree and the scene configuration.

- Admin writes (app.repositories.categories, app.repositories.scene_config) take it exclusively, so
  two of them never interleave their checks, e.g. a scene rule created for a category while that
  category is being deleted (the rule would go with it through ON DELETE CASCADE).
- Membership recomputes (app.repositories.scenes) take it shared: they read scenes, rules and the
  category tree in several queries and must not see an admin change land halfway.

Both are released when the transaction ends. Writers that bypass these repositories are not
covered; the database constraints stay the last guard.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

# pg_advisory_xact_lock key ("CATE"); the value predates the scene configuration, keep it stable
CATALOG_LOCK_KEY = 0x43415445


def lock_catalog_for_write(conn: Any) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK_KEY})


def lock_catalog_for_read(conn: Any) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": CATALOG_LOCK_KEY})
