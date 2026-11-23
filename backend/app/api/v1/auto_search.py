"""业务搜图的路由：用整图查自建库，同时用 GroundingDINO 在图里找目标（区域搜索和菜谱识别共用
这次检测：找到的区域搜自建库、识别菜名），Global 搜图作为可选兜底（app.api.services.auto_search）。
和 Global 搜图的路由一样，每种输入一个路由：上传文件（multipart）或图片 URL（JSON）。

每个请求最后写一行耗时（logger autosearch，同时写平台日志），从请求到达到返回结果：

    业务搜图(上传文件) | 接收请求 0.310s | 读取上传 0.001s | 整图搜索 0.061s | 总耗时 0.373s | 参数=top_k=10, global_fallback=False

接收请求是从请求到达（TraceIdMiddleware）到进入接口函数，主要是接收上传的文件；然后是
读取上传或图像下载，以及业务搜图记的几步。各步骤自己的阶段在它们各自的那一行里。
"""

from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, File, Form, Request, UploadFile
from app.api.deps import require_retrieval
from app.api.services.auto_search import run_auto_search
from app.api.uploads import read_upload
from app.bootstrap.state import get_application_state
from app.clients.image_fetch import read_url_bytes
from app.core.middleware import bind_trace_id, request_received_at
from app.core.observability.log_helper import log_timing
from app.schemas.auto_search import (
    GLOBAL_FALLBACK_DOC,
    MACHINE_INFO_DOC,
    MACHINE_INFO_MAX_LENGTH,
    SCENE_DOC,
    SCORE_THRESHOLD_DOC,
    TOP_K_DEFAULT,
    TOP_K_DOC,
    TOP_K_MAX,
    TRACE_ID_DOC,
    AutoSearchResponse,
    AutoSearchUrlRequest,
)
from app.utils.timer import timer_tool

logger = logging.getLogger("autosearch")
router = APIRouter(prefix="/api/search/auto", tags=["business-search"])

RECEIVE_STEP = "接收请求"
READ_STEP = "读取上传"

_DESCRIPTION = (
    "给业务方用的搜图接口：先用整图查自建库（按检测场景过滤，取相似度达到阈值的前 top_k 条），有结果就直接返回。"
    "同时用 GroundingDINO 在查询图里检测目标（参数见 AUTO_SEARCH_REGION_CONFIG，只检测一次），检测框给两步用："
    "区域搜索（AUTO_SEARCH_REGION_CONFIG.enabled 开启时）按检测框裁出区域，每个区域按同样的 "
    "scene / top_k / score_threshold 查自建库，合并后同一张图只留最高分，"
    "取相似度最高的 top_k 条（details.region 标明来自哪个检测框）；菜谱识别（RECIPE_DETECT_CONFIG.enabled 开启时）"
    "把每个检测框的裁剪图交给菜谱识别模型，和底库菜品的相似度高于 RECIPE_DETECT_CONFIG.threshold 的菜名"
    "（每个框最多一个，同名只留一个，按相似度从高到低）作为一条结果卡（source=recipe，菜名在 tags 里）。"
    "整图没有结果时，返回区域搜索的结果，"
    "菜谱识别的那条排在后面；都没有结果且 global_fallback=true 时，再用现网 Global 搜图兜底（取 top_k 条）。"
    "整图有结果时不等这两步，也不返回菜谱识别的结果卡。score_threshold=0 时整图总有结果，不做区域搜索。"
    "各通道的结果是同一种结果卡，每条结果卡的 source 说明它来自哪里（self / recipe / global）；"
    "自建库结果卡的 details.outputs_heating 说明它是否带加热信息。没有结果时 items 为 []，"
    "fallback.status 说明兜底的情况。Global 兜底失败不会让请求失败：返回空结果，fallback.status=failed 并给出原因；"
    "区域搜索、菜谱识别失败也不会让请求失败，按没有结果处理。"
    "整图搜索出错（未知场景 SEARCH_UNKNOWN_SCENE、图片无法解码 SEARCH_BAD_IMAGE、检索服务未就绪 NOT_READY 等）"
    "按统一错误格式返回，不走兜底。详见契约 §6.6。"
)


def _request_timer(request: Request) -> timer_tool:
    """这个请求的计时器，从请求到达算起：第一步是接收请求。没有经过 TraceIdMiddleware 时
    （服务里总是经过）从现在算起，没有这一步。
    """
    received = request_received_at(request)
    if received is None:
        return timer_tool()
    timer = timer_tool(start=received)
    timer.mark(RECEIVE_STEP)
    return timer


def _log_search_timing(
    timer: timer_tool,
    *,
    input_type: str,
    params: dict[str, Any],
    trace_id: str,
    completed: bool,
) -> None:
    # 没传的参数（None）不输出
    params_text = ", ".join(f"{key}={value}" for key, value in params.items() if value is not None)
    log_timing(
        logger, f"业务搜图({input_type})", timer,
        trace_id=trace_id, failed=not completed, extra=f"参数={params_text}",
    )


@router.post(
    "/file",
    response_model=AutoSearchResponse,
    response_model_exclude_unset=True,  # fallback.error / detail 和 message 只在设置了时输出
    summary="业务搜图（上传文件）：整图 + 区域搜索 + 菜谱识别，可选 Global 兜底",
    description=_DESCRIPTION,
)
async def auto_search_by_file(
    request: Request,
    file: UploadFile = File(..., description="查询图片（JPG / PNG / WebP / BMP，最大 15 MB）"),
    scene: str | None = Form(default=None, description=SCENE_DOC),
    top_k: int = Form(default=TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=TOP_K_DOC),
    score_threshold: float | None = Form(default=None, description=SCORE_THRESHOLD_DOC),
    global_fallback: bool = Form(default=False, description=GLOBAL_FALLBACK_DOC),
    trace_id: str | None = Form(default=None, description=TRACE_ID_DOC),
    machine_info: str | None = Form(default=None, max_length=MACHINE_INFO_MAX_LENGTH, description=MACHINE_INFO_DOC),
) -> Any:
    """multipart/form-data：file 加上其余表单字段。"""
    timer = _request_timer(request)
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, trace_id)
    params = {
        "scene": scene,
        "top_k": top_k,
        "score_threshold": score_threshold,
        "global_fallback": global_fallback,
        "machine_info": machine_info,
    }
    completed = False
    try:
        require_retrieval(runtime)  # 在读取上传文件之前先确定 NOT_READY
        with timer.step(READ_STEP):
            image_bytes = await read_upload(file, runtime.max_image_bytes)
        result = await run_auto_search(
            runtime,
            image_bytes=image_bytes,
            scene=scene,
            top_k=top_k,
            score_threshold=score_threshold,
            global_fallback=global_fallback,
            machine_info=machine_info,
            trace_id=trace_id,
            input_label=f"file:{file.filename or 'upload'}",
            timer=timer,
        )
        completed = True
        return result
    finally:
        _log_search_timing(timer, input_type="上传文件", params=params, trace_id=trace_id, completed=completed)


@router.post(
    "/url",
    response_model=AutoSearchResponse,
    response_model_exclude_unset=True,
    summary="业务搜图（图片链接）：整图 + 区域搜索 + 菜谱识别，可选 Global 兜底",
    description=_DESCRIPTION + " 请求体为 JSON，未知字段返回 422。",
)
async def auto_search_by_url(request: Request, body: AutoSearchUrlRequest) -> Any:
    """application/json：{image_url, scene, top_k, score_threshold, global_fallback, trace_id, machine_info}。"""
    timer = _request_timer(request)
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, body.trace_id)
    params = body.model_dump(mode="json", exclude={"trace_id"})
    completed = False
    try:
        require_retrieval(runtime)  # 在下载任何东西之前先确定 NOT_READY
        with timer.step("图像下载"):
            image_bytes = await read_url_bytes(
                body.image_url,
                max_image_bytes=runtime.max_image_bytes,
                timeout_s=runtime.http_timeout,
            )
        result = await run_auto_search(
            runtime,
            image_bytes=image_bytes,
            scene=body.scene,
            top_k=body.top_k,
            score_threshold=body.score_threshold,
            global_fallback=body.global_fallback,
            machine_info=body.machine_info,
            trace_id=trace_id,
            input_label=f"url:{body.image_url}",
            timer=timer,
        )
        completed = True
        return result
    finally:
        _log_search_timing(timer, input_type="图片链接", params=params, trace_id=trace_id, completed=completed)
