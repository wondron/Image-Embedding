"""对账：每次一轮、每轮有上限，让向量库重新和 PostgreSQL 保持一致。

PostgreSQL 是数据的权威来源。每次改变向量 payload 来源字段的写入都会递增
images.payload_version，台账（image_embeddings.synced_version）记录当前 collection 里
是哪个版本。写入方在提交后立刻推送，尽力而为；没推上去的就在台账里落后：Qdrant 挂了、
进程在中间停了、一次类别移动涉及的图片超过一个请求推送的数量。已删除的图片同样在
image_deletions 里等待。

一轮：

1. 推送台账行落后的图片的 payload（SyncSceneMembershipsUseCase.push_images），
   按 id 顺序从上一轮返回的游标之后开始，最多绕回一圈，最多 max_payloads 张图。
   一直失败的图片（collection 里没有向量点：需要重新计算向量）因此每圈只访问一次，
   不会饿死其他图片；
2. 用 collection 里真实的向量点核对当前模型的一段台账（verify_ledger，从上一轮停下的
   地方开始，最多 max_verifications 行）：向量点已经不在的行被忘掉，图片重新算作
   没有向量；
3. 为当前模型没有向量的图片计算 embedding（用当前编码器的 BuildEmbeddingsUseCase
   .run_batch，最多 max_embeddings 张图，按 id 顺序从上一轮停下的地方开始），每一批都在
   向量写入闸门之后：即将切换时写进旧 collection 的图片、由还没跟上切换的进程写入的图片，
   或者向量点丢失的图片。原图读不了的图片在下一圈再试；
4. 清理删除队列（DeletionWorker.run_once），从最早的开始，最多 max_deletions 行；
   遇到什么都没清理掉的一批就停（最早的那些行一直失败）。

第 2、3 步需要当前模型的检索服务（第 3 步需要它的编码器）：命令行后台任务没有编码器，
只检查台账，不计算 embedding。

每一轮在 API 进程里运行（app.bootstrap.reconciler，RECONCILE_CONFIG），或者由 cron 调用
（`python -m app.workers.reconcile`）。PostgreSQL advisory lock 防止两个进程同时跑一轮：
后来的那个跳过这一轮。
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.repositories.ports import MetadataStorePort
from app.services.retrieval.build_embeddings import BUILD_BATCH, BuildEmbeddingsUseCase, verify_ledger
from app.services.retrieval.delete_image import DeletionWorker
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

logger = logging.getLogger("retrieval.reconcile")

# 一轮的 pg_try_advisory_lock 键（"RECS"）
RECONCILE_LOCK_KEY = 0x52454353


@dataclass(frozen=True)
class ReconcileLimits:
    payload_batch: int = 200
    max_payloads: int = 2000
    deletion_batch: int = 50
    max_deletions: int = 200
    max_verifications: int = 2000  # 每轮对照 collection 检查的台账行数（0：关闭）
    max_embeddings: int = 100  # 每轮为没有向量的图片计算 embedding 的数量（0：关闭）


@dataclass
class PayloadPass:
    scanned: int = 0
    pushed: int = 0
    missing: int = 0
    unconfirmed: int = 0


@dataclass
class VectorPass:
    verified: int = 0
    forgotten: int = 0
    embedded: int = 0
    failed: int = 0
    gone: int = 0


@dataclass
class DeletionPass:
    processed: int = 0
    failed: list[str] = field(default_factory=list)
    remaining: int = 0


@dataclass
class RoundResult:
    """skipped：这一轮跑了时为 None，否则为没跑的原因（'locked'）。cursor：下一轮的 payload
    推送从哪里开始（None：从第一张图开始）。
    """

    skipped: str | None
    cursor: str | None
    payloads: PayloadPass = field(default_factory=PayloadPass)
    deletions: DeletionPass = field(default_factory=DeletionPass)
    duration_ms: int = 0
    vectors: VectorPass = field(default_factory=VectorPass)
    verify_cursor: str | None = None
    embed_cursor: str | None = None

    @property
    def did_something(self) -> bool:
        return bool(
            self.payloads.scanned or self.deletions.processed or self.deletions.failed
            or self.vectors.forgotten or self.vectors.embedded or self.vectors.failed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "cursor": self.cursor,
            "payloads": vars(self.payloads),
            "vectors": vars(self.vectors),
            "deletions": {**vars(self.deletions), "failed": list(self.deletions.failed)},
            "duration_ms": self.duration_ms,
        }


def _never() -> bool:
    return False


class ReconcileUseCase:
    def __init__(
        self,
        metadata: MetadataStorePort,
        *,
        sync: SyncSceneMembershipsUseCase,
        deletions: DeletionWorker,
        limits: ReconcileLimits = ReconcileLimits(),
        ledger: tuple[int, Any, Any] | None = None,
        fill: BuildEmbeddingsUseCase | None = None,
        write_gate: Callable[[], Any] | None = None,
    ) -> None:
        """ledger：（当前模型 id、它的 collection、EmbeddingModelStorePort），用来对照 collection
        里的向量点检查；fill：当前模型的 BuildEmbeddingsUseCase（没有编码器的地方为 None）；
        write_gate：每一批补算时持有（API 的 VectorWriteGate.shared），所以切换模型版本
        永远不会和它竞争。
        """
        self._metadata = metadata
        self._sync = sync
        self._deletions = deletions
        self._limits = limits
        self._ledger = ledger
        self._fill = fill
        self._write_gate = write_gate

    def run_once(
        self,
        *,
        cursor: str | None = None,
        should_stop: Callable[[], bool] = _never,
        verify_cursor: str | None = None,
        embed_cursor: str | None = None,
    ) -> RoundResult:
        """一轮。每批之间检查 should_stop（API 正在关闭）。"""
        started = time.monotonic()
        with self._metadata.try_maintenance_lock(RECONCILE_LOCK_KEY) as held:
            if not held:
                logger.info("reconcile round skipped: another process holds the lock")
                return RoundResult(skipped="locked", cursor=cursor, verify_cursor=verify_cursor, embed_cursor=embed_cursor)
            payloads, cursor = self._push_payloads(cursor, should_stop)
            vectors, verify_cursor, embed_cursor = self._vector_pass(verify_cursor, embed_cursor, should_stop)
            deletions = self._drain_deletions(should_stop)
        result = RoundResult(
            skipped=None, cursor=cursor, payloads=payloads, deletions=deletions,
            duration_ms=int((time.monotonic() - started) * 1000),
            vectors=vectors, verify_cursor=verify_cursor, embed_cursor=embed_cursor,
        )
        level = logging.INFO if result.did_something else logging.DEBUG
        logger.log(
            level,
            "reconcile round payloads: scanned=%d pushed=%d missing=%d unconfirmed=%d | "
            "vectors: verified=%d forgotten=%d embedded=%d failed=%d | "
            "deletions: processed=%d failed=%d remaining=%d | %dms",
            payloads.scanned, payloads.pushed, payloads.missing, payloads.unconfirmed,
            vectors.verified, vectors.forgotten, vectors.embedded, vectors.failed,
            deletions.processed, len(deletions.failed), deletions.remaining, result.duration_ms,
        )
        return result

    def _vector_pass(
        self, verify_cursor: str | None, embed_cursor: str | None, should_stop: Callable[[], bool]
    ) -> tuple[VectorPass, str | None, str | None]:
        """第 2、3 步：检查一段台账，然后补写几个缺失的向量。"""
        limits = self._limits
        done = VectorPass()
        if self._ledger is not None and limits.max_verifications > 0 and not should_stop():
            model_id, vectors, models = self._ledger
            check = verify_ledger(
                model_id, vectors, models, after=verify_cursor, limit=limits.max_verifications, should_stop=should_stop
            )
            done.verified, done.forgotten, verify_cursor = check.checked, check.forgotten, check.last_id
        if self._fill is None or limits.max_embeddings <= 0:
            return done, verify_cursor, embed_cursor
        tried = 0
        while tried < limits.max_embeddings and not should_stop():
            gate = self._write_gate() if self._write_gate is not None else contextlib.nullcontext()
            with gate:
                batch = self._fill.run_batch(after=embed_cursor, limit=min(BUILD_BATCH, limits.max_embeddings - tried))
            tried += len(batch.embedded) + len(batch.failures) + len(batch.gone)
            done.embedded += len(batch.embedded)
            done.failed += len(batch.failures)
            done.gone += len(batch.gone)
            embed_cursor = None if batch.done else batch.last_id
            if batch.done:
                break  # 到了 id 范围的末尾：下一轮从第一张图开始
        if done.embedded or done.failed:
            logger.info(
                "reconcile: %d image(s) had no vector in the current collection and got one; %d could not be embedded",
                done.embedded, done.failed,
            )
        return done, verify_cursor, embed_cursor

    def _push_payloads(self, start: str | None, should_stop: Callable[[], bool]) -> tuple[PayloadPass, str | None]:
        """先 (start, end]，再 [first, start]：最多一圈，最多 max_payloads 张图。"""
        limits = self._limits
        done = PayloadPass()
        after: str | None = start
        until: str | None = None  # 第二段：到 start 为止，含 start
        while done.scanned < limits.max_payloads and not should_stop():
            ids = self._metadata.list_unsynced_payload_ids(
                after=after, until=until, limit=min(limits.payload_batch, limits.max_payloads - done.scanned)
            )
            if not ids:
                if start is None or until is not None:
                    return done, None  # 走完一整圈：下一轮从第一张图开始
                after, until = None, start
                continue
            result = self._sync.push_images(ids)
            done.scanned += len(ids)
            done.pushed += len(result.pushed)
            done.missing += len(result.missing)
            done.unconfirmed += len(result.unconfirmed)
            if result.missing and self._ledger is not None:
                # 没有向量点的台账行：忘掉它，补算向量那一步会重新计算这张图的 embedding
                model_id, _vectors, models = self._ledger
                models.forget_embeddings(model_id, list(result.missing))
            after = ids[-1]
        if done.missing:
            logger.warning(
                "reconcile: %d image(s) have a ledger row but no point in the current collection; "
                "their rows are checked and forgotten so they get a new vector", done.missing,
            )
        return done, after

    def _drain_deletions(self, should_stop: Callable[[], bool]) -> DeletionPass:
        limits = self._limits
        done = DeletionPass()
        failing: dict[str, None] = {}
        scanned = 0
        while scanned < limits.max_deletions and not should_stop():
            run = self._deletions.run_once(limit=min(limits.deletion_batch, limits.max_deletions - scanned))
            scanned += int(run.get("scanned") or 0)
            processed = [str(image_id) for image_id in run.get("processed") or []]
            done.processed += len(processed)
            for image_id in processed:
                failing.pop(image_id, None)
            for item in run.get("failed") or []:
                failing[str(item.get("image_id"))] = None
            if not processed:
                break  # 为空，或者最早的那些行一直失败：下一批也只会重试同样的行
        done.failed = list(failing)
        done.remaining = self._metadata.count_pending_deletions()
        return done
