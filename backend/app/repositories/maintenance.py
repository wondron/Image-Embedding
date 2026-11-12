"""Session advisory locks for maintenance jobs that may run in several processes at once (the
API's background reconciler in each worker process, a cron job): whoever holds the key runs, the
others skip. The lock lives on a dedicated autocommit connection, so no transaction stays open
while the job runs, and it goes with the connection if the process dies.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.db.common import logger


class PgMaintenanceMixin:
    @contextmanager
    def try_maintenance_lock(self, key: int) -> Iterator[bool]:
        """Yields True while this process holds the lock, False (holding nothing) when another has it."""
        self.ensure_schema()
        with self._get_engine().connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            held = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": int(key)}).scalar())
            try:
                yield held
            finally:
                if held:
                    try:
                        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": int(key)})
                    except Exception:  # the connection is gone, and the lock with it
                        logger.warning("advisory unlock failed key=%s; released with the connection", key)
