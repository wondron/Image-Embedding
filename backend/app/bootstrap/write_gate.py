"""挡在当前 collection 向量写入前面的闸门，让切换模型版本时永远不会丢图片。

往当前 collection 写新向量的写入方（入库、重新计算 embedding、对账任务补缺失的向量）
在获取检索服务并写入期间共享持有它。切换时独占持有：新的写入方等待，进行中的完成，
然后切换为目标版本补算它还缺的，并换上新的检索服务，这一切都在任何写入方能再次
碰到旧 collection 之前完成。写入方只在切换进行时等待（几秒），彼此之间从不等待。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.core import errors as err

# 写入方等切换完成、以及切换等进行中的写入完成的最长时间
WRITE_WAIT_SECONDS = 120.0


class VectorWriteGate:
    def __init__(self, wait_seconds: float = WRITE_WAIT_SECONDS) -> None:
        self._cond = threading.Condition()
        self._writers = 0
        self._closed = False
        self._wait = wait_seconds

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def shared(self) -> Iterator[None]:
        """一次写入；切换进行时等待（没能按时结束时为 NOT_READY）。"""
        with self._cond:
            if not self._cond.wait_for(lambda: not self._closed, timeout=self._wait):
                raise err.not_ready("a model version switch is in progress; try again shortly")
            self._writers += 1
        try:
            yield
        finally:
            with self._cond:
                self._writers -= 1
                self._cond.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """一次切换：期间没有写入方在运行。进行中的写入没能按时结束时为 NOT_READY
        （什么都没切换；请重试）。
        """
        with self._cond:
            if not self._cond.wait_for(lambda: not self._closed, timeout=self._wait):
                raise err.not_ready("another model version switch is in progress")
            self._closed = True
            if not self._cond.wait_for(lambda: self._writers == 0, timeout=self._wait):
                self._closed = False
                self._cond.notify_all()
                raise err.not_ready("vector writes in flight did not finish in time; try the switch again")
        try:
            yield
        finally:
            with self._cond:
                self._closed = False
                self._cond.notify_all()
