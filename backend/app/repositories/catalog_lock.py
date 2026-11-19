"""目录（类别树和场景配置）的事务级 advisory lock，只有一把。

- 管理写入（app.repositories.categories、app.repositories.scene_config）独占持有，这样两个
  写入的检查永远不会交错，例如在删除某个类别的同时为它创建一条场景规则（规则会通过
  ON DELETE CASCADE 一起被删掉）。
- 归属重算（app.repositories.scenes）共享持有：它们分几次查询读取场景、规则和类别树，
  不能看到管理修改落到一半的状态。

两者都在事务结束时释放。绕过这些仓储的写入方不受保护；数据库约束仍然是最后一道防线。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

# pg_advisory_xact_lock 的键（"CATE"）；这个值早于场景配置就有了，保持不变
CATALOG_LOCK_KEY = 0x43415445


def lock_catalog_for_write(conn: Any) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK_KEY})


def lock_catalog_for_read(conn: Any) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": CATALOG_LOCK_KEY})
