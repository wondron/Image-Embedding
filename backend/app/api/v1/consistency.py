"""数据核对的路由：PostgreSQL、Qdrant、对象存储三边是否一致，模型版本之间是否一致。只读。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from app.api.services import consistency as consistency_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.consistency import ConsistencyCheckRequest, ConsistencyResponse
from app.services.retrieval.consistency import CheckOptions

router = APIRouter(prefix="/api/consistency", tags=["consistency"])


@router.get(
    "",
    response_model=ConsistencyResponse,
    summary="The last consistency check of this process",
    description=(
        "本进程最近一次数据核对：进行中时是进度，结束后是报告（连着哪个 PostgreSQL / Qdrant / 对象存储、"
        "每个在用版本的缺失和多余、文件、版本之间的差别，以及要处理的事 issues）。只在内存里，重启后为 null。"
    ),
)
def get_check(request: Request) -> Any:
    return consistency_service.get_status(get_application_state(request))


@router.post(
    "/check",
    response_model=ConsistencyResponse,
    summary="Start a consistency check in the background",
    description=(
        "后台核对 PostgreSQL 和每个在用版本的 Qdrant collection（点、写入记录、payload）、对象存储里的原图和缩略图，"
        "以及多余的点和文件；用已加载的编码器抽查向量是不是这个版本写的。只读，不修复。进度看 GET。"
        "同一时间只跑一次（CONSISTENCY_CHECK_RUNNING 409）。"
    ),
    responses={409: {"model": ErrorResponse}},
)
def start_check(
    request: Request,
    body: ConsistencyCheckRequest = Body(default_factory=ConsistencyCheckRequest),
) -> Any:
    options = CheckOptions(
        vectors=body.vectors, files=body.files, orphans=body.orphans, content=body.content, encoders=body.encoders,
    )
    return consistency_service.start_check(get_application_state(request), request, options)


@router.post(
    "/check/stop",
    response_model=ConsistencyResponse,
    summary="Stop the running consistency check",
    description="在当前这一批之后停下，已经核对的部分照样出报告（complete=false）。没有在跑时原样返回。",
)
def stop_check(request: Request) -> Any:
    return consistency_service.stop_check(get_application_state(request), request)
