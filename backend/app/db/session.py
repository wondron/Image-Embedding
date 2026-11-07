from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.config.constant import PG_CONNECT_TIMEOUT_SECONDS
from app.db.common import REQUIRED_TABLES, logger
from app.services.retrieval import errors as err


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
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc

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
        """Verify Alembic-applied 12 tables + ltree. Does NOT auto-migrate.

        Run: `cd backend && alembic upgrade head` (same POSTGRES_CONFIG as the app).
        """
        if self._schema_ready:
            return
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                ext = conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'ltree'")
                ).fetchone()
                if not ext:
                    raise err.index_mysql_failed(
                        "ltree extension missing; run `alembic upgrade head`"
                    )
                missing: list[str] = []
                for table in REQUIRED_TABLES:
                    row = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name=:t"
                        ),
                        {"t": table},
                    ).fetchone()
                    if not row:
                        missing.append(table)
                if missing:
                    raise err.index_mysql_failed(
                        "PG schema incomplete (missing "
                        + ", ".join(missing)
                        + "); run `alembic upgrade head` from backend/"
                    )
            self._schema_ready = True
            logger.info(
                "pg metadata schema ready db=%s tables=%d",
                self.database,
                len(REQUIRED_TABLES),
            )
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_mysql_failed(f"ensure_schema: {exc}") from exc
