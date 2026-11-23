"""诊断用模型操作的处理和校验。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_food_service
from app.api.services.recipe_detect import dish_names, recognised
from app.api.services.region_search import Region, crop, decode, find_regions, pixel_box
from app.core.config.settings import AutoSearchRegionSettings, RecipeDetectSettings
from app.schemas.tools import (
    RECIPE_TOP_K_DEFAULT,
    EmbeddingResponse,
    GdinoDetection,
    GdinoResponse,
    RecipeCandidate,
    RecipeRegion,
    RecipeToolResponse,
    SegmentResponse,
)
from app.api.uploads import read_upload
from app.core import errors
from app.core.errors import AppError
from app.utils.image import encode_png_base64
from app.bootstrap.state import ApplicationState

if TYPE_CHECKING:
    from app.clients.recipe_classifier import RecipeClassifier, RecipeMatch


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


def _recipe_parameters(
    runtime: ApplicationState,
    detector: Any,
    *,
    text_range: str | list[str] | None,
    box_threshold: float | None,
    text_threshold: float | None,
    max_regions: int | None,
    threshold: float | None,
) -> tuple[AutoSearchRegionSettings, float]:
    """业务搜图的菜谱识别用的参数：检测按 AUTO_SEARCH_REGION_CONFIG（区域搜索关着也按它），阈值按
    RECIPE_DETECT_CONFIG.threshold；请求里给了的换成请求的。请求的取值路由已经检查过，所以这里
    出问题的只能是配置：NOT_READY（503），detail 说明是哪一项。
    """
    try:
        settings = AutoSearchRegionSettings.from_config(runtime.config)
        recipe = RecipeDetectSettings.from_config(runtime.config)
    except ValueError as exc:
        raise errors.not_ready(f"配置有误：{exc}") from exc
    overrides = {
        "caption": None if text_range is None else detector.prepare_caption(text_range),
        "box_threshold": box_threshold,
        "text_threshold": text_threshold,
        "max_regions": max_regions,
    }
    settings = dataclasses.replace(settings, **{key: value for key, value in overrides.items() if value is not None})
    try:
        settings.check()
    except ValueError as exc:
        raise errors.not_ready(f"配置有误：{exc}") from exc
    return settings, recipe.threshold if threshold is None else threshold


def _recipe_detection(
    detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings
) -> tuple[np.ndarray, list[Region], int]:
    """在工作线程里（轮到 GroundingDINO 时）解码、检测，和业务搜图一样（region_search.find_regions）。
    返回解码后的图、用到的检测框和 GroundingDINO 给出的框数。解不开是 ValueError（422）；
    图解码之后的错误都是模型或配置的问题（500）。
    """
    image = decode(image_bytes)
    try:
        regions, detected = find_regions(detector, image, settings)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return image, regions, detected


def _recipe_candidates(
    classifier: RecipeClassifier, image: np.ndarray, regions: Sequence[Region], top_k: int
) -> tuple[list[list[RecipeMatch]], list[RecipeMatch]]:
    """在工作线程里：每个检测框的裁剪图（业务搜图识别的就是它）和整张图最相近的 top_k 道菜。"""
    return [classifier.rank(crop(image, region), top_k) for region in regions], classifier.rank(image, top_k)


def _candidates(matches: Sequence[RecipeMatch]) -> list[RecipeCandidate]:
    return [RecipeCandidate(label=match.label, score=match.score) for match in matches]


async def recipe(
    runtime: ApplicationState,
    file: UploadFile,
    *,
    text_range: str | Sequence[str] | None = None,
    box_threshold: float | None = None,
    text_threshold: float | None = None,
    max_regions: int | None = None,
    threshold: float | None = None,
    top_k: int = RECIPE_TOP_K_DEFAULT,
) -> RecipeToolResponse:
    """业务搜图的菜谱识别，每一步都摊开：检测框、每个框最相近的几道菜、哪些框过了阈值、
    业务搜图会给出的菜名，另附整张图的识别结果作对比。

    检测和业务搜图用同一个 GroundingDINO 实例、同样的解码、取框和裁剪，和业务搜图、/api/tools/gdino
    轮流使用模型；识别在交出轮次之后进行，不占着 GroundingDINO。
    """
    image_bytes = await _read_tool_image(file, runtime)
    # 两个模型都要加载了：没有时是 NOT_READY（503），detail 是原因（菜谱识别没开启、模型加载失败……）
    classifier = runtime.require_recipe_classifier()
    detector = runtime.require_detector()
    settings, threshold = _recipe_parameters(
        runtime, detector,
        text_range=_text_range(text_range),
        box_threshold=box_threshold,
        text_threshold=text_threshold,
        max_regions=max_regions,
        threshold=threshold,
    )

    started = time.perf_counter()
    try:
        image, regions, detected = await _in_detector_turn(
            runtime, _recipe_detection, detector, image_bytes, settings
        )
    except ValueError as exc:
        raise AppError("INVALID_IMAGE", str(exc), http_status=422) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("recipe tool detection failed | filename=%s | err=%s", file.filename, exc)
        raise AppError("TOOL_FAILED", f"recipe detection failed: {exc}", http_status=500) from exc
    classify_started = time.perf_counter()
    try:
        per_region, whole = await run_in_threadpool(_recipe_candidates, classifier, image, regions, top_k)
    except Exception as exc:
        logger.exception("recipe tool classification failed | filename=%s | err=%s", file.filename, exc)
        raise AppError("TOOL_FAILED", f"recipe classification failed: {exc}", http_status=500) from exc
    finished = time.perf_counter()

    closest = [ranked[0] for ranked in per_region if ranked]  # 每个框最相近的那道菜：业务搜图用的就是它
    response = RecipeToolResponse(
        ok=True,
        filename=file.filename or "upload",
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        caption=settings.caption,
        box_threshold=float(settings.box_threshold),
        text_threshold=float(settings.text_threshold),
        max_regions=settings.max_regions,
        threshold=float(threshold),
        top_k=top_k,
        detected=detected,
        regions=[
            RecipeRegion(
                label=region.label,
                score=round(region.score, 4),
                box=list(region.box),
                candidates=_candidates(ranked),
                accepted=bool(ranked) and recognised(ranked[0], threshold),
            )
            for region, ranked in zip(regions, per_region)
        ],
        names=dish_names(closest, threshold),
        whole_image=_candidates(whole),
        latency_ms=round((finished - started) * 1000.0, 2),
        classify_ms=round((finished - classify_started) * 1000.0, 2),
    )
    logger.info(
        "recipe tool ok | filename=%s | bytes=%d | size=%dx%d | caption=%r | box_threshold=%s | text_threshold=%s | "
        "max_regions=%d | detected=%d | regions=%d | threshold=%s | names=%s | latency_ms=%.2f | classify_ms=%.2f",
        response.filename,
        len(image_bytes),
        response.width,
        response.height,
        response.caption,
        response.box_threshold,
        response.text_threshold,
        response.max_regions,
        response.detected,
        len(response.regions),
        response.threshold,
        json.dumps(response.names, ensure_ascii=False),
        response.latency_ms,
        response.classify_ms,
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
