"""元数据库的 PostgreSQL 连接：engine、ping、表结构检查。

只依赖 app.core 和 app.db / app.models，不依赖 app.services。
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

# 随代码发布的迁移脚本：backend/alembic（镜像里是 /app/alembic）
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


@lru_cache(maxsize=1)
def expected_revision() -> str:
    """与本代码放在一起的 Alembic 脚本的 head；数据库必须正好在这个版本。"""
    head = ScriptDirectory(str(ALEMBIC_DIR)).get_current_head()
    if head is None:
        raise errors.not_ready(f"no Alembic revisions under {ALEMBIC_DIR}")
    return head


class PgConnectionMixin:
    """连接、ping，以及 Alembic 表结构校验。

    url：通常是 PostgresSettings.url()（Nacos / 本地 YAML 里的 POSTGRES_CONFIG）。
    ping() 报告的 host / database 从这个 URL 读取，所以总是
    对应本存储实际连接的数据库。
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
        """释放连接池（可重复调用）；下次使用时再按需重新连接。"""
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
        """检查数据库处于本代码的 Alembic head，并且有 ltree 扩展和 app.models 里的每一张表。
        **不会**自动迁移。

        数据库能连上但没有迁移时为 NOT_READY（信息里会写要执行的命令）；
        连不上或查询失败为 INDEX_POSTGRES_FAILED。
        执行：`cd backend && alembic upgrade head`（使用和应用相同的 POSTGRES_CONFIG）。
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
