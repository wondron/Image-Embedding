"""任务中心的 HTTP 适配层：入库记录和删除队列。

读取和清空通过 catalog 访问 PostgreSQL（app.bootstrap.catalog；还没构建时
NOT_READY 503），所以编码器或 Qdrant 挂了时历史记录也能读。处理删除队列需要向量库
和对象存储：它运行检索服务的 DeletionWorker（没有检索服务时 NOT_READY 503）。
路由是同步函数：FastAPI 在线程池里执行它们。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.api.deps import require_catalog, require_retrieval
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.tasks import ImageDeletionProcessRequest

logger = logging.getLogger("svc.tasks")


def list_index_tasks(
    runtime: ApplicationState,
    *,
    status: str | None,
    image_state: str | None,
    new_image: bool | None = None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.list_index_tasks(
        status=status, image_state=image_state, new_image=new_image, page=page, page_size=page_size
    )
    return {"ok": True, **result}


def clear_index_tasks(
    runtime: ApplicationState, request: Request, *, status: str | None, image_state: str | None
) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.clear_index_tasks(status=status, image_state=image_state)
    logger.info(
        "index task records cleared status=%s image_state=%s deleted=%s | trace_id=%s",
        result["status"], result["image_state"], result["deleted"], get_trace_id(request),
    )
    return {"ok": True, **result}


def delete_index_task(runtime: ApplicationState, request: Request, *, task_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.delete_index_task(task_id)
    logger.info("index task record deleted id=%s deleted=%s | trace_id=%s", result["id"], result["deleted"], get_trace_id(request))
    return {"ok": True, **result}


def list_deletions(runtime: ApplicationState, *, limit: int) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.list_deletions(limit=limit)
    return {"ok": True, **result, "cleanup_ready": runtime.retrieval is not None}


def process_deletions(
    runtime: ApplicationState, request: Request, body: ImageDeletionProcessRequest
) -> dict[str, Any]:
    """现在清理最早排队的删除；失败的继续排队（原因写日志）。"""
    tasks = require_catalog(runtime).tasks_uc
    stack = require_retrieval(runtime)
    result = stack.deletion_worker.run_once(limit=body.limit)
    failed = [
        {"image_id": item["image_id"], "error": item["code"], "detail": item["detail"]}
        for item in result["failed"]
    ]
    remaining = tasks.count_deletions()
    logger.info(
        "deletion queue processed scanned=%s processed=%s failed=%s remaining=%s | trace_id=%s",
        result["scanned"], len(result["processed"]), len(failed), remaining, get_trace_id(request),
    )
    return {
        "ok": True,
        "scanned": result["scanned"],
        "processed": list(result["processed"]),
        "failed": failed,
        "remaining": remaining,
    }
