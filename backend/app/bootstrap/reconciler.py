"""The API process's background reconciler (RECONCILE_CONFIG): a daemon thread that runs a
reconcile round (app.services.retrieval.reconcile) every interval_seconds on the retrieval stack.

Started by the lifespan once the retrieval stack is up; stopped by ApplicationState.close() before
the stack is closed. A round that is running when the API stops ends at its next batch boundary.
Several API worker processes each run one; the round's advisory lock lets one of them work and
the others skip. A failed round is logged and the next one tries again.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.core.config.settings import ReconcileSettings
from app.services.retrieval.reconcile import ReconcileLimits, ReconcileUseCase, RoundResult

if TYPE_CHECKING:
    from app.bootstrap.state import ApplicationState

logger = logging.getLogger("reconciler")


class BackgroundReconciler:
    def __init__(self, runtime: "ApplicationState", settings: ReconcileSettings) -> None:
        self._runtime = runtime
        self._settings = settings
        self._limits = ReconcileLimits(max_payloads=settings.max_payloads, max_deletions=settings.max_deletions)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor: str | None = None
        self._stack: Any = None
        self.last_round: dict[str, Any] | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="reconciler", daemon=True)
        self._thread.start()
        logger.info(
            "后台 reconciler 已启动 | 每 %s 秒一次（首次在 %s 秒后），每轮最多 %d 条 payload、%d 条 deletion",
            self._settings.interval_seconds, self._settings.initial_delay_seconds,
            self._settings.max_payloads, self._settings.max_deletions,
        )

    def stop(self, timeout: float = 30.0) -> None:
        """Ask the loop to end and wait for a running round to reach its next batch boundary."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("后台 reconciler 在 %s 秒后仍在运行；已将其保留（daemon thread）", timeout)
            else:
                logger.info("后台 reconciler 已停止")

    def _stopping(self) -> bool:
        return self._stop.is_set() or self._runtime.stopping

    def _loop(self) -> None:
        if self._stop.wait(self._settings.initial_delay_seconds):
            return
        while not self._stopping():
            self.run_round()
            if self._stop.wait(self._settings.interval_seconds):
                return

    def run_round(self) -> RoundResult | None:
        """One round now, on the current stack; None when skipped for want of one, or failed."""
        if self._stopping():
            return None
        try:
            stack = self._runtime.require_retrieval()
        except RuntimeError as exc:
            self._record({"skipped": "not_ready", "detail": str(exc)})
            return None
        followed = self._follow_current(stack)
        if followed is not None:
            stack = followed
        if stack is not self._stack:  # a new stack (new collection): start the lap over
            self._stack, self._cursor = stack, None
        use_case = ReconcileUseCase(
            stack.infra.metadata, sync=stack.scene_sync_uc, deletions=stack.deletion_worker, limits=self._limits,
        )
        try:
            result = use_case.run_once(cursor=self._cursor, should_stop=self._stopping)
        except Exception as exc:
            logger.exception("reconcile round failed; the next round tries again")
            self._record({"skipped": None, "error": f"{type(exc).__name__}: {exc}"})
            return None
        self._cursor = result.cursor
        self._record(result.as_dict())
        return result

    def _follow_current(self, stack: Any) -> Any:
        """The current model version was switched by another process (or by hand in PostgreSQL):
        rebuild this process's stack on it, so it stops searching and indexing the old collection.
        Returns the new stack, or None when nothing changed or the switch could not be followed."""
        models = self._runtime.models
        if models is None:
            return None
        try:
            current = stack.infra.metadata.get_current_embedding_model()
        except Exception:
            return None  # the round itself reports a database that cannot be read
        current_id = int(current["id"])
        if current_id == stack.model.id:
            return None
        logger.warning(
            "当前模型版本已在别处切换为 %s:%s（id=%s），本进程原来用 %s；跟随切换",
            current.get("name"), current.get("version"), current_id, stack.model.space_id,
        )
        try:
            models.activate(current_id, allow_missing=True)
        except Exception:
            logger.exception("跟随切换模型版本失败；本进程仍用 %s，下一轮再试", stack.model.space_id)
            return None
        return self._runtime.retrieval

    def _record(self, outcome: dict[str, Any]) -> None:
        self.last_round = {"at": datetime.now().astimezone().isoformat(), **outcome}


def start_reconciler(runtime: "ApplicationState") -> BackgroundReconciler | None:
    """Start the background reconciler when RECONCILE_CONFIG enables it and retrieval is up."""
    try:
        settings = ReconcileSettings.from_config(runtime.config)
    except ValueError as exc:
        logger.error("RECONCILE_CONFIG 已忽略，后台 reconciler 保持关闭：%s", exc)
        return None
    if not settings.enabled:
        logger.info("后台 reconciler 未开启（RECONCILE_CONFIG.enabled is false）")
        return None
    if runtime.retrieval is None:
        logger.info("后台 reconciler 未启动：retrieval 栈不可用")
        return None
    reconciler = BackgroundReconciler(runtime, settings)
    reconciler.start()
    return reconciler
