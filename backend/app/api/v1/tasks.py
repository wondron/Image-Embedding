"""Task center routes: index records and the image deletion queue."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from app.api.services import tasks as tasks_service
from app.bootstrap.state import get_application_state
from app.core.enums import IndexTaskStatus
from app.schemas.tasks import (
    ImageDeletionListResponse,
    ImageDeletionProcessRequest,
    ImageDeletionProcessResponse,
    IndexTaskClearResponse,
    IndexTaskListResponse,
)
from app.services.retrieval.task_center import MAX_DELETIONS, MAX_PAGE_SIZE

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get(
    "/index-tasks",
    response_model=IndexTaskListResponse,
    summary="List index records",
    description=(
        "入库记录：POST /api/index 和 /api/index/file 每处理一个文件记一条（成功带图片 id，失败带错误码和原因），"
        "新的在前。整个请求被拒的情况（服务未就绪、文件过大、请求格式错误）没有记录。"
        "image 是图片现在的样子，图片删除后为 null。只读 PostgreSQL，检索栈未就绪时也能用。"
    ),
)
def list_index_tasks(
    request: Request,
    status: Optional[IndexTaskStatus] = Query(default=None, description="succeeded / failed；不传为全部"),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
) -> Any:
    return tasks_service.list_index_tasks(
        get_application_state(request), status=status.value if status else None, page=page, page_size=page_size
    )


@router.delete(
    "/index-tasks",
    response_model=IndexTaskClearResponse,
    summary="Clear index records",
    description="删掉某一种结果的全部入库记录（status 必填）；只删记录，不动图片。",
)
def clear_index_tasks(
    request: Request,
    status: IndexTaskStatus = Query(..., description="succeeded / failed"),
) -> Any:
    return tasks_service.clear_index_tasks(get_application_state(request), request, status=status.value)


@router.get(
    "/image-deletions",
    response_model=ImageDeletionListResponse,
    summary="List queued image cleanups",
    description=(
        "已从图库删除、向量点和原图 / 缩略图还没清理的图片（image_deletions），先删除的在前。"
        "这些图片已经不在图库和检索结果里。cleanup_ready 表示现在能否用 POST /api/image-deletions/process 清理。"
    ),
)
def list_deletions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=MAX_DELETIONS),
) -> Any:
    return tasks_service.list_deletions(get_application_state(request), limit=limit)


@router.post(
    "/image-deletions/process",
    response_model=ImageDeletionProcessResponse,
    summary="Clean queued images now",
    description=(
        "按删除先后清理最多 limit 条：删掉当前 collection 里的向量点和两个文件，成功的移出队列；"
        "失败的留在队列里（原因见 failed 和服务端日志），可以再次清理。图片在删除后又被重新入库时只移出队列，"
        "向量和文件保留。需要检索栈，未就绪时返回 NOT_READY（503）。"
        "和 python -m app.workers.process_deletions 做的是同一件事。"
    ),
)
def process_deletions(request: Request, body: Optional[ImageDeletionProcessRequest] = None) -> Any:
    return tasks_service.process_deletions(
        get_application_state(request), request, body or ImageDeletionProcessRequest()
    )
