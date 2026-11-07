"""PostgreSQL connection for the metadata store: engine, ping, schema check.

Depends only on app.core and app.db / app.models, never on app.services.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

import app.models  # noqa: F401  registers every table on Base.metadata
from app.core import errors
from app.core.config.constant import PG_CONNECT_TIMEOUT_SECONDS
from app.db.base import Base
from app.db.common import logger

# Migrations shipped with this code: backend/alembic (/app/alembic in the image)
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


@lru_cache(maxsize=1)
def expected_revision() -> str:
    """Head of the Alembic scripts next to this code; the database must be at exactly this revision."""
    head = ScriptDirectory(str(ALEMBIC_DIR)).get_current_head()
    if head is None:
        raise errors.not_ready(f"no Alembic revisions under {ALEMBIC_DIR}")
    return head


class PgConnectionMixin:
    """Connection, ping, and Alembic schema verification.

    url: normally PostgresSettings.url() (POSTGRES_CONFIG in Nacos / local YAML).
    host / database reported by ping() are read from that URL, so they always
    describe the database this store actually connects to.
    """

    def __init__(self, *, url: URL | str, connect_timeout: float = PG_CONNECT_TIMEOUT_SECONDS) -> None:
        self.url = make_url(url)
        self.host = self.url.host or ""
        self.database = self.url.database or ""
        self.connect_timeout = float(connect_timeout)
        self._engine = None
        self._schema_ready = False

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            self._engine = create_engine(
                self.url,
                pool_pre_ping=True,
                # libpq 只认整数秒，0 表示无限等待，所以至少给 1
                connect_args={"connect_timeout": max(1, int(self.connect_timeout))},
            )
            return self._engine
        except Exception as exc:
            raise errors.index_postgres_failed(f"create_engine: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        """Dispose the connection pool (idempotent); next call reconnects lazily."""
        engine, self._engine = self._engine, None
        self._schema_ready = False
        if engine is not None:
            engine.dispose()

    def ping(self) -> dict[str, Any]:
        try:
            engine = self._get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {
                "ok": True,
                "host": self.host,
                "database": self.database,
                "driver": "postgresql",
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "host": self.host,
                "database": self.database,
                "driver": "postgresql",
            }

    def ensure_schema(self) -> None:
        """Check the database is at this code's Alembic head and has ltree plus every
        table in app.models. Does NOT auto-migrate.

        A database that is reachable but not migrated is NOT_READY (the message says what to
        run); a failure to reach or query it is INDEX_POSTGRES_FAILED.
        Run: `cd backend && alembic upgrade head` (same POSTGRES_CONFIG as the app).
        """
        if self._schema_ready:
            return
        engine = self._get_engine()
        try:
            expected = expected_revision()
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
                if current != expected:
                    raise errors.not_ready(
                        f"database {self.database!r} is at migration {current or 'none'}, this code "
                        f"needs {expected}; run `alembic upgrade head` from backend/"
                    )
                ltree = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'ltree'")).fetchone()
                if not ltree:
                    raise errors.not_ready("ltree extension missing; run `alembic upgrade head`")
                missing = sorted(set(Base.metadata.tables) - set(inspect(conn).get_table_names()))
                if missing:
                    raise errors.not_ready(
                        f"tables missing although migration {current} is recorded: {', '.join(missing)}"
                    )
        except errors.AppError:
            raise
        except Exception as exc:
            raise errors.index_postgres_failed(f"ensure_schema: {type(exc).__name__}: {exc}") from exc
        self._schema_ready = True
        logger.info(
            "pg metadata schema ready db=%s revision=%s tables=%d",
            self.database,
            expected,
            len(Base.metadata.tables),
        )
