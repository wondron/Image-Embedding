"""API 进程的后台对账（RECONCILE_CONFIG）：一个守护线程，每隔 interval_seconds 在检索服务上
运行一轮对账（app.services.retrieval.reconcile）。

检索服务起来后由 lifespan 启动；在检索服务关闭之前由 ApplicationState.close() 停止。
API 停止时正在运行的一轮在下一个批次边界结束。多个 API worker 进程各自运行一个；一轮的
advisory lock 让其中一个工作，其他的跳过。失败的一轮写日志，下一轮再试。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.core import errors as err
from app.core.config.settings import ReconcileSettings
from app.services.retrieval.build_embeddings import BuildEmbeddingsUseCase
from app.services.retrieval.reconcile import ReconcileLimits, ReconcileUseCase, RoundResult

if TYPE_CHECKING:
    from app.bootstrap.state import ApplicationState

logger = logging.getLogger("reconciler")


class BackgroundReconciler:
    def __init__(self, runtime: "ApplicationState", settings: ReconcileSettings) -> None:
        self._runtime = runtime
        self._settings = settings
        self._limits = ReconcileLimits(
            max_payloads=settings.max_payloads, max_deletions=settings.max_deletions,
            max_verifications=settings.max_verifications, max_embeddings=settings.max_embeddings,
        )
        self._verify_cursor: str | None = None
        self._embed_cursor: str | None = None
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
        """让循环结束，并等正在运行的一轮到达下一个批次边界。"""
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
        """现在在当前检索服务上跑一轮；没有检索服务而跳过或失败时为 None。"""
        if self._stopping():
            return None
        try:
            stack = self._runtime.require_retrieval()
        except err.NotReadyError as exc:
            self._record({"skipped": "not_ready", "detail": exc.detail})
            return None
        followed = self._follow_current(stack)
        if followed is not None:
            stack = followed
        if stack is not self._stack:  # 新的检索服务（新的 collection）：从头开始绕圈
            self._stack, self._cursor = stack, None
            self._verify_cursor = self._embed_cursor = None
        ledger, fill = self._vector_parts(stack)
        use_case = ReconcileUseCase(
            stack.infra.metadata, sync=stack.scene_sync_uc, deletions=stack.deletion_worker, limits=self._limits,
            ledger=ledger, fill=fill, write_gate=self._runtime.vector_writes.shared,
        )
        try:
            result = use_case.run_once(
                cursor=self._cursor, should_stop=self._stopping,
                verify_cursor=self._verify_cursor, embed_cursor=self._embed_cursor,
            )
        except Exception as exc:
            logger.exception("reconcile round failed; the next round tries again")
            self._record({"skipped": None, "error": f"{type(exc).__name__}: {exc}"})
            return None
        self._cursor = result.cursor
        self._verify_cursor, self._embed_cursor = result.verify_cursor, result.embed_cursor
        self._record(result.as_dict())
        return result

    @staticmethod
    def _vector_parts(stack: Any) -> tuple[Any, Any]:
        """这个检索服务上本轮补算向量用的（ledger, fill）：它的模型、collection 和存储，以及一个用
        它的编码器的 BuildEmbeddingsUseCase。没有这些的检索服务返回 (None, None)。
        """
        infra = stack.infra
        vectors, storage = getattr(infra, "vectors", None), getattr(infra, "storage", None)
        model, encoder = getattr(stack, "model", None), getattr(stack, "embedding", None)
        if vectors is None or model is None:
            return None, None
        ledger = (model.id, vectors, infra.metadata)
        if encoder is None or storage is None:
            return ledger, None
        fill = BuildEmbeddingsUseCase(
            model=model, encoder=encoder, vectors=vectors, metadata=infra.metadata, models=infra.metadata, storage=storage,
        )
        return ledger, fill

    def _follow_current(self, stack: Any) -> Any:
        """当前模型版本被另一个进程切换了（或者在 PostgreSQL 里被手工改了）：在它上面重建本进程的
        检索服务，不再搜索和写入旧的 collection。返回新的检索服务；没有变化或者跟不上这次切换时
        为 None。
        """
        models = self._runtime.models
        if models is None:
            return None
        try:
            current = stack.infra.metadata.get_current_embedding_model()
        except Exception:
            return None  # 数据库读不了由这一轮自己报告
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
    """RECONCILE_CONFIG 启用且检索服务起来时，启动后台对账。"""
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
