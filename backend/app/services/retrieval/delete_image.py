"""图片删除流程（契约 §2.5）。

1. delete_image：事务里删除 images（+ CASCADE 的依赖）→ 插入 image_deletions
2. process_deletion_queue：清理 image_deletions → 删除 Qdrant 向量点 + 删除 S3 文件 → 删除这一行
   （DELETE /api/images/{image_id} 会立刻为它自己的那一行执行这一步，即 DeletionWorker.clean_up；
   那里失败的继续排队，由 `python -m app.workers.process_deletions` 或
   POST /api/image-deletions/process（任务中心的「立即清理」）处理）

Qdrant：从当前 collection 以及其他仍在使用的版本（building / ready：正在补齐的新模型，
为回滚保留的上一个模型）的 collection 里删除向量点，所以切换或回滚永远不会让已删除的
图片重新出现在结果里。已下线的版本没有 collection 了。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import MetadataStorePort, ImageStoragePort, VectorStorePort
from app.utils.storage_keys import different_objects

logger = logging.getLogger("retrieval.delete")


class DeleteImageUseCase:
    """把硬删除加入 image_deletions 队列（元数据事务）。"""

    def __init__(self, metadata: MetadataStorePort) -> None:
        self._metadata = metadata

    def execute(self, image_id: UUID | str) -> dict[str, Any]:
        row = self._metadata.enqueue_image_deletion(image_id)
        logger.info("enqueued image_deletions id=%s", row.get("image_id"))
        return {
            "ok": True,
            "image_id": row.get("image_id"),
            "original_url": row.get("original_url"),
            "thumb_url": row.get("thumb_url"),
            "deleted_at": row.get("deleted_at"),
            "queued": True,
        }


def _no_other_collections() -> list[VectorStorePort]:
    return []


def _clean_up(
    metadata: MetadataStorePort,
    vectors: VectorStorePort,
    storage: ImageStoragePort,
    row: dict[str, Any],
    others: Callable[[], list[VectorStorePort]] = _no_other_collections,
) -> None:
    """删除一张排队图片的向量点（当前 collection，以及其他在用版本的）和它的两个文件，
    然后删除它的队列行。

    失败时抛出，保留这一行以便重试。删除之后又被重新存入的图片（同样的字节：同样的 id
    和向量点）保留它的向量点，以及新行所指的文件。排队的文件如果在新行以外的 key 下
    （在分片目录或 key_prefix 变更之前存的），就不是新行的文件，仍然会被删除。
    """
    image_id = row.get("image_id")
    stored_again = metadata.get_by_id(image_id)
    if stored_again is None:
        vectors.delete(image_id)
        for other in others():
            other.delete(image_id)
    else:
        logger.warning("image %s was stored again after its deletion; its point and current files are kept", image_id)
    for url_field in ("original_url", "thumb_url"):
        url = str(row.get(url_field) or "").strip()
        if url and (stored_again is None or different_objects(url, stored_again.get(url_field))):
            storage.delete_by_url_or_key(url)
    metadata.complete_deletion(image_id)


def _failure(image_id: Any, exc: Exception) -> dict[str, Any]:
    """继续排队的一项。`error` 是原始原因，只用于运维的命令行输出；
    `code` / `detail` 是 HTTP 客户端可以看到的（POST /api/image-deletions/process）。
    """
    if isinstance(exc, err.AppError):
        code, detail = exc.code, err.client_detail(exc)
    else:
        code, detail = "INTERNAL_ERROR", "cleanup failed"
    return {"image_id": str(image_id), "error": f"{type(exc).__name__}: {exc}", "code": code, "detail": detail}


def process_deletion_queue(
    metadata: MetadataStorePort,
    vectors: VectorStorePort,
    storage: ImageStoragePort,
    *,
    limit: int = 50,
    others: Callable[[], list[VectorStorePort]] = _no_other_collections,
) -> dict[str, Any]:
    """清理最多 `limit` 行 image_deletions。

    全部成功（Qdrant + 两个 S3 对象）：删除 image_deletions 行。
    任何一步失败：保留这一行以便重试；继续处理下一行。
    """
    if limit <= 0:
        raise err.validation_error("limit must be positive")
    pending = metadata.list_pending_deletions(limit=int(limit))
    processed: list[str] = []
    failed: list[dict[str, Any]] = []
    for row in pending:
        image_id = row.get("image_id")
        try:
            _clean_up(metadata, vectors, storage, row, others)
            processed.append(str(image_id))
            logger.info("deletion queue completed id=%s", image_id)
        except Exception as exc:
            logger.exception("deletion queue item failed id=%s", image_id)
            failed.append(_failure(image_id, exc))
    return {
        "ok": len(failed) == 0,
        "scanned": len(pending),
        "processed": processed,
        "failed": failed,
    }


class DeletionWorker:
    """给 bootstrap 用的 process_deletion_queue 包装。"""

    def __init__(
        self,
        metadata: MetadataStorePort,
        vectors: VectorStorePort,
        storage: ImageStoragePort,
        *,
        others: Callable[[], list[VectorStorePort]] = _no_other_collections,
    ) -> None:
        """others：其他在用版本的 collection，在清理删除时读取
        （bootstrap.retrieval_stack.RetrievalInfra.other_vectors）。
        """
        self._metadata = metadata
        self._vectors = vectors
        self._storage = storage
        self._others = others

    def run_once(self, *, limit: int = 50) -> dict[str, Any]:
        return process_deletion_queue(
            self._metadata,
            self._vectors,
            self._storage,
            limit=limit,
            others=self._others,
        )

    def clean_up(self, row: dict[str, Any]) -> bool:
        """立刻清理一条刚加入队列的删除（DELETE /api/images/{image_id}）。返回 False 时
        这一行继续排队，留给 run_once（原因写日志）。
        """
        try:
            _clean_up(self._metadata, self._vectors, self._storage, row, self._others)
        except Exception:
            logger.exception("deletion cleanup failed id=%s; left queued in image_deletions", row.get("image_id"))
            return False
        logger.info("deletion cleanup completed id=%s", row.get("image_id"))
        return True
