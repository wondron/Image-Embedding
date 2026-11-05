from __future__ import annotations

from typing import Any

from app.adapter.persistence.pg_common import REQUIRED_TABLES, logger
from app.domain.retrieval import errors as err


class PgConnectionMixin:
    """Connection, ping, and Alembic schema verification."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        host: str = "postgres",
        port: int = 5432,
        user: str = "image_emb",
        password: str = "image_emb",
        database: str = "image_embedding",
        connect_timeout: float = 5.0,
    ) -> None:
        if dsn and str(dsn).strip():
            self.dsn = str(dsn).strip()
        else:
            self.dsn = (
                f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
            )
        self.host = host
        self.port = int(port)
        self.user = user
        self.database = database
        self.connect_timeout = float(connect_timeout)
        self._engine = None
        self._schema_ready = False

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise err.index_mysql_failed(
                "SQLAlchemy is not installed; add sqlalchemy>=2 to requirements.txt"
            ) from exc
        try:
            self._engine = create_engine(
                self.dsn,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(self.connect_timeout)},
            )
            return self._engine
        except Exception as exc:
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        try:
            from sqlalchemy import text

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

        Run: `cd backend && alembic upgrade head` (DATABASE_URL / POSTGRES_*).
        """
        if self._schema_ready:
            return
        from sqlalchemy import text

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
