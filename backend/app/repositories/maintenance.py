"""给可能在多个进程里同时运行的维护任务用的会话级 advisory lock（每个 worker 进程里 API 的
后台对账任务、cron 任务）：持有键的那个运行，其他的跳过。锁放在一个专用的自动提交连接上，
所以任务运行期间不会有事务一直开着，进程挂掉时锁随连接一起释放。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.db.common import logger


class PgMaintenanceMixin:
    @contextmanager
    def try_maintenance_lock(self, key: int) -> Iterator[bool]:
        """本进程持有锁时 yield True，另一个进程持有时 yield False（什么都不持有）。"""
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
                    except Exception:  # 连接没了，锁也随之释放
                        logger.warning("advisory unlock failed key=%s; released with the connection", key)
