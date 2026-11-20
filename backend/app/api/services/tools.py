"""诊断用模型操作的处理和校验。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Sequence

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_food_service
from app.api.services.region_search import decode, pixel_box
from app.schemas.tools import EmbeddingResponse, GdinoDetection, GdinoResponse, SegmentResponse
from app.api.uploads import read_upload
from app.core import errors
from app.core.errors import AppError
from app.utils.image import encode_png_base64
from app.bootstrap.state import ApplicationState


logger = logging.getLogger("svc.tools")


async def _read_tool_image(file: UploadFile, runtime: ApplicationState) -> bytes:
    return await read_upload(
        file,
        runtime.max_image_bytes,
        allowed_content_types=runtime.settings.allowed_content_types,
        empty_detail="uploaded file is empty",
        too_large_detail=f"file too large (> {runtime.max_image_bytes} bytes)",
    )


def _segment_response(
    segmentor: Any,
    image_bytes: bytes,
    filename: str,
    *,
    output_vis: bool,
    started: float,
) -> SegmentResponse:
    """在一个工作线程里同时完成解码、推理和图像编码。

    mask 和原图同尺寸，所以返回的宽高取自 mask。图片无效时 detect 抛出 ValueError（422），
    模型输出不对是服务端的问题（500）。
    """
    mask, visual = segmentor.detect(image_bytes, output_vis=output_vis)
    if getattr(mask, "ndim", 0) != 2:
        raise RuntimeError(f"segment model returned an invalid mask: {getattr(mask, 'shape', None)}")

    height, width = mask.shape
    return SegmentResponse(
        ok=True,
        filename=filename,
        width=int(width),
        height=int(height),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        mask_png_base64=encode_png_base64(mask),
        visual_png_base64=(
            encode_png_base64(visual, bgr=True) if output_vis else None
        ),
    )


def _embedding_response(
    embedding_model: Any,
    image_bytes: bytes,
    filename: str,
    *,
    started: float,
) -> EmbeddingResponse:
    """把推理、向量转换和校验都放在事件循环之外。"""
    vector = embedding_model.detect(image_bytes)
    if getattr(vector, "ndim", 0) != 1:
        raise ValueError(
            f"embedding model returned an invalid shape: {getattr(vector, 'shape', None)}"
        )

    return EmbeddingResponse(
        ok=True,
        filename=filename,
        embedding_dim=int(vector.shape[0]),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        embedding=vector.tolist(),
    )


async def segment(
    runtime: ApplicationState,
    file: UploadFile,
    *,
    output_vis: bool = False,
) -> SegmentResponse:
    image_bytes = await _read_tool_image(file, runtime)
    segmentor = runtime.require_segmentor()

    started = time.perf_counter()
    try:
        response = await run_in_threadpool(
            _segment_response,
            segmentor,
            image_bytes,
            file.filename or "upload",
            output_vis=output_vis,
            started=started,
        )
    except ValueError as exc:
        raise AppError("INVALID_IMAGE", str(exc), http_status=422) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("segment tool failed | filename=%s | err=%s", file.filename, exc)
        raise AppError("TOOL_FAILED", f"segment failed: {exc}", http_status=500) from exc

    logger.info(
        "segment tool ok | filename=%s | bytes=%d | size=%dx%d | output_vis=%s | latency_ms=%.2f",
        response.filename,
        len(image_bytes),
        response.width,
        response.height,
        output_vis,
        response.latency_ms,
    )
    return response


def _text_range(value: str | Sequence[str] | None) -> str | list[str] | None:
    """请求里的 text_range 交给 GdinoDetect.detect 的形式：空白项去掉；只剩一项时当作一个 caption
    （和 AUTO_SEARCH_REGION_CONFIG.caption 一样，如 "food . dish"），多项时当作类别列表；
    什么都没有时为 None（用 GROUNDINGDINO_CONFIG.caption）。
    """
    terms = [value] if isinstance(value, str) else list(value or ())
    terms = [term.strip() for term in terms if term and term.strip()]
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else terms


def _gdino_response(
    detector: Any,
    image_bytes: bytes,
    filename: str,
    *,
    text_range: str | list[str] | None,
    box_threshold: float | None,
    text_threshold: float | None,
    started: float,
) -> GdinoResponse:
    """在工作线程里解码、检测，并把检测框换成原图上的像素坐标。

    解码方式和区域搜索一样（OpenCV，BGR），解不开是 ValueError（422）；图解码之后的错误都是
    模型或配置的问题（500）。没传的参数取 GROUNDINGDINO_CONFIG，实际用的值写在响应里。
    """
    image = decode(image_bytes)
    height, width = image.shape[:2]
    caption = detector.prepare_caption(text_range)
    box_threshold = detector.box_threshold if box_threshold is None else box_threshold
    text_threshold = detector.text_threshold if text_threshold is None else text_threshold
    try:
        boxes, scores, labels = detector.detect(
            image, text_range=caption, box_threshold=box_threshold, text_threshold=text_threshold
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    detections = [
        GdinoDetection(
            label=str(label).strip(),
            score=round(float(score), 4),
            box=list(pixel_box(box, width=width, height=height)),
        )
        for box, score, label in zip(boxes, scores, labels)
    ]
    detections.sort(key=lambda item: item.score, reverse=True)
    return GdinoResponse(
        ok=True,
        filename=filename,
        width=int(width),
        height=int(height),
        caption=caption,
        box_threshold=float(box_threshold),
        text_threshold=float(text_threshold),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        detections=detections,
    )


async def _in_detector_turn(runtime: ApplicationState, func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """和区域搜索轮流使用 GroundingDINO（ApplicationState.detector_turn，规则见 region_search）：
    轮到时在工作线程里执行 func。请求在执行时被取消，也要等这次运行结束才把轮次交出去。
    """
    turn = runtime.detector_turn
    await turn.acquire()

    async def run() -> Any:
        try:
            return await run_in_threadpool(func, *args, **kwargs)
        finally:
            turn.release()

    job = asyncio.ensure_future(run())
    # 请求被取消后没人再取这次运行的结果：取出异常，免得 asyncio 报告异常从未被获取
    job.add_done_callback(lambda done: done.cancelled() or done.exception())
    return await asyncio.shield(job)


async def gdino(
    runtime: ApplicationState,
    file: UploadFile,
    *,
    text_range: str | Sequence[str] | None = None,
    box_threshold: float | None = None,
    text_threshold: float | None = None,
) -> GdinoResponse:
    image_bytes = await _read_tool_image(file, runtime)
    # 没有加载（GROUNDINGDINO_CONFIG 缺失 / 关闭，或加载失败）时是 NOT_READY（503），detail 是原因
    detector = runtime.require_detector()

    started = time.perf_counter()
    try:
        response = await _in_detector_turn(
            runtime,
            _gdino_response,
            detector,
            image_bytes,
            file.filename or "upload",
            text_range=_text_range(text_range),
            box_threshold=box_threshold,
            text_threshold=text_threshold,
            started=started,
        )
    except ValueError as exc:
        raise AppError("INVALID_IMAGE", str(exc), http_status=422) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("gdino tool failed | filename=%s | err=%s", file.filename, exc)
        raise AppError("TOOL_FAILED", f"gdino failed: {exc}", http_status=500) from exc

    logger.info(
        "gdino tool ok | filename=%s | bytes=%d | size=%dx%d | caption=%r | box_threshold=%s | "
        "text_threshold=%s | detections=%d | latency_ms=%.2f",
        response.filename,
        len(image_bytes),
        response.width,
        response.height,
        response.caption,
        response.box_threshold,
        response.text_threshold,
        len(response.detections),
        response.latency_ms,
    )
    return response


async def embedding(runtime: ApplicationState, file: UploadFile) -> EmbeddingResponse:
    image_bytes = await _read_tool_image(file, runtime)
    service = require_food_service(runtime)

    embedding_model = getattr(service, "embeddingTool", None)
    if embedding_model is None:
        raise errors.not_ready("embedding model is not initialized")

    started = time.perf_counter()
    try:
        response = await run_in_threadpool(
            _embedding_response,
            embedding_model,
            image_bytes,
            file.filename or "upload",
            started=started,
        )
    except ValueError as exc:
        raise AppError("INVALID_IMAGE", str(exc), http_status=422) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("embeddingTool失败 name=%s err=%s", file.filename, exc)
        raise AppError("TOOL_FAILED", f"embedding failed: {exc}", http_status=500) from exc

    logger.info("embedding调用成功 name=%s bytes=%d dim=%d latency_ms=%.2f", response.filename, len(image_bytes), response.embedding_dim, response.latency_ms)
    return response
