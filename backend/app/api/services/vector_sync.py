"""PostgreSQL 写入提交之后，把一张图的向量 payload 同步过去（尽力而为）。

用于手工场景归属以及图片的修改 / 推送（app.api.services 的 scene_memberships、images）。
写入本身只需要 catalog（PostgreSQL），所以编码器或 Qdrant 挂了时也能保存；提交之后，
检索服务就绪时立刻把图片的 payload 推送到当前 collection，这样不用完整执行一次
POST /api/scenes/sync，搜索就能看到修改。推送失败不会让请求失败：

    vector_sync   sync_required   含义
    pushed        false           当前 collection 里已经是新的 payload
    missing       true            图片在当前 collection 里没有向量（需要重新入库）
    not_ready     true            检索服务没构建（NOT_READY）；之后再执行同步
    failed        true            Qdrant 或台账出错，或者图片一直在变（已写日志）

push_payloads 对多张图做同样的事（移动类别时）：请求期间最多推送 PUSH_NOW_MAX 张，
分块进行；其余的 payload_version 保持高于台账，由对账任务（app.services.retrieval.reconcile）
或 POST /api/scenes/sync 之后推送。它的 vector_sync 多一种 `partial`：一部分推送了，
其余的还在等待。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT

logger = logging.getLogger("svc.vector_sync")

# 请求等待期间推送的 payload 数；更大的移动把剩下的交给对账任务
PUSH_NOW_MAX = 2000


def push_image_payload(runtime: ApplicationState, image_id: UUID | str) -> dict[str, Any]:
    """推送图片已提交的 payload 之后的 {vector_sync, sync_required}。"""
    try:
        stack = runtime.require_retrieval()
    except err.NotReadyError:
        return {"vector_sync": "not_ready", "sync_required": True}
    # PostgreSQL 的修改无论如何都已经提交了；告诉客户端需要同步，原因写日志。
    try:
        result = stack.scene_sync_uc.push_images([str(image_id)])
    except err.AppError as exc:  # INDEX_QDRANT_FAILED / INDEX_POSTGRES_FAILED
        logger.warning("payload push failed image=%s | %s: %s", image_id, exc.code, exc.detail)
        return {"vector_sync": "failed", "sync_required": True}
    except Exception:
        logger.exception("payload push failed image=%s", image_id)
        return {"vector_sync": "failed", "sync_required": True}
    if result.pushed:
        return {"vector_sync": "pushed", "sync_required": False}
    if result.missing:
        return {"vector_sync": "missing", "sync_required": True}
    return {"vector_sync": "failed", "sync_required": True}


def push_payloads(
    runtime: ApplicationState,
    image_ids: Iterable[UUID | str],
    *,
    limit: int | None = None,
    chunk: int = SCENE_SYNC_BATCH_DEFAULT,
) -> dict[str, Any]:
    """推送多张图已提交的 payload 之后的 {pushed, pending, vector_sync, sync_required}。

    limit：现在最多推送这么多张（None：PUSH_NOW_MAX）。pending：没有在当前 collection 里
    确认的（超出上限、没有向量、仍在变化，或推送失败）；它们在台账里保持未同步，
    直到有东西推送它们。
    """
    ids = list(dict.fromkeys(str(image_id) for image_id in image_ids))
    if not ids:
        return {"pushed": 0, "pending": 0, "vector_sync": "pushed", "sync_required": False}
    try:
        stack = runtime.require_retrieval()
    except err.NotReadyError:
        return {"pushed": 0, "pending": len(ids), "vector_sync": "not_ready", "sync_required": True}
    pushed = 0
    status = "pushed"
    now = ids[: max(0, PUSH_NOW_MAX if limit is None else int(limit))]
    try:
        for start in range(0, len(now), chunk):
            pushed += len(stack.scene_sync_uc.push_images(now[start : start + chunk]).pushed)
    except err.AppError as exc:  # INDEX_QDRANT_FAILED / INDEX_POSTGRES_FAILED
        logger.warning("payload push failed images=%d pushed=%d | %s: %s", len(ids), pushed, exc.code, exc.detail)
        status = "failed"
    except Exception:
        logger.exception("payload push failed images=%d pushed=%d", len(ids), pushed)
        status = "failed"
    pending = len(ids) - pushed
    if status == "pushed" and pending:
        status = "partial"
    if len(ids) > len(now):
        logger.info("payload push deferred images=%d now=%d (the reconciler pushes the rest)", len(ids), len(now))
    return {"pushed": pushed, "pending": pending, "vector_sync": status, "sync_required": pending > 0}
