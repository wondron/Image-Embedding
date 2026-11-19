"""区域搜索，业务搜图的第二次尝试（app.api.services.auto_search）：GroundingDINO 在查询图里
找到的检测框，每个都作为一张单独的查询图搜自建库，合并成一个结果列表。

    查询图 -> 按编码器的方式解码（OpenCV，BGR）
           -> 用 AUTO_SEARCH_REGION_CONFIG 的 caption、box_threshold、text_threshold 跑 GroundingDINO
           -> 检测框换成像素坐标并裁到图内；任一边小于 MIN_REGION_SIDE 的丢掉；
              按检测置信度从高到低，最多 max_regions 个
           -> 每个框裁剪出来（JPEG），像 POST /api/search/batch 那样搜索
              （search_images）：每个都用请求的 scene、top_k 和 score_threshold
           -> 所有框的结果：每张已存图片只留一条（相似度最高的），按相似度从高到低，
              最多 top_k 条；details.region 说明是哪个框找到的

它从不让业务搜图失败：run_region_search 返回它的结果（命中的结果，或者没有结果的原因），
由调用方记日志，并把它的步骤记到请求的计时器上。

GroundingDINO 一次只处理一张图，而每个业务搜图都会启动区域搜索，所以各次搜索轮流使用它
（ApplicationState.detector_turn）。它们在事件循环上排队，整图已经有结果而被丢弃的搜索
直接离开队列，不会运行模型。工作线程无法中断，所以一次轮次持续到模型跑完为止，即使对应的
搜索在这期间被丢弃：如果改成线程去等模型自己的锁，等到之后就会为没人要的结果跑一次模型，
每一次这样的运行都会推迟仍在等待结果的搜索。搜索被丢弃之后，它剩下的裁剪图都不会再搜索。
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

import cv2
import numpy as np
from starlette.concurrency import run_in_threadpool

from app.api.services.batch_search import QueryImage, search_images
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.config.settings import AutoSearchRegionSettings

if TYPE_CHECKING:
    from app.bootstrap.retrieval_stack import RetrievalStack

DETECT_STEP = "目标检测"  # 请求耗时行里的步骤名
SEARCH_STEP = "区域搜索"

MIN_REGION_SIDE = 16  # 像素：更小的框对编码器没有可用的信息
JPEG_QUALITY = 95


@dataclass(frozen=True, slots=True)
class Region:
    """在查询图里找到的一个检测框。"""

    label: str  # GroundingDINO 匹配到的 caption 里的词
    score: float  # 它的检测置信度
    box: tuple[int, int, int, int]  # 查询图上的像素坐标 x1, y1, x2, y2

    def wire(self) -> dict[str, Any]:
        """它找到的结果的 details.region。"""
        return {"label": self.label, "score": round(self.score, 4), "box": list(self.box)}


@dataclass(frozen=True, slots=True)
class RegionOutcome:
    """一次区域搜索的结果。"""

    hits: list[dict[str, Any]] = field(default_factory=list)  # 搜索结果（不带 source），最多 top_k 条
    regions: int = 0  # 搜索的检测框数
    failed: int = 0  # 其中无法搜索的框数
    steps: tuple[tuple[str, float, bool], ...] = ()  # 给请求计时器用的 (步骤, 秒数, 是否失败)
    error: str | None = None  # 中途停下的原因（某一步失败了）
    skipped: str | None = None  # 完全没有运行的原因


def decode(image_bytes: bytes) -> np.ndarray:
    """按编码器的方式读取的查询图：BGR 数组。"""
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("the query image cannot be decoded")
    return image


def regions_of(
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    labels: Sequence[str],
    *,
    width: int,
    height: int,
    limit: int,
) -> list[Region]:
    """GroundingDINO 的检测框（归一化的 cx, cy, w, h）换成 width x height 图片上的像素坐标，
    并裁到图内；丢掉小的；按置信度从高到低，最多 limit 个。
    """
    found = []
    for (cx, cy, w, h), score, label in zip(boxes, scores, labels):
        x1 = max(0, math.floor((cx - w / 2) * width))
        y1 = max(0, math.floor((cy - h / 2) * height))
        x2 = min(width, math.ceil((cx + w / 2) * width))
        y2 = min(height, math.ceil((cy + h / 2) * height))
        if x2 - x1 < MIN_REGION_SIDE or y2 - y1 < MIN_REGION_SIDE:
            continue
        found.append(Region(str(label).strip(), float(score), (x1, y1, x2, y2)))
    found.sort(key=lambda region: region.score, reverse=True)  # 稳定排序：置信度相同的保持原来的顺序
    return found[:limit]


def crop(image: np.ndarray, region: Region) -> bytes:
    """BGR 图像中的一块区域，编码方式和上传的一样。"""
    x1, y1, x2, y2 = region.box
    ok, encoded = cv2.imencode(".jpg", image[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError(f"region {region.box} cannot be encoded")
    return encoded.tobytes()


def merge(regions: Sequence[Region], entries: Sequence[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """每个框的结果（search_images 返回的 results[] 条目，按框的顺序）：每张已存图片
    只留相似度最高的那条，按相似度从高到低，最多 top_k 条。每条结果在 details.region
    里带上它的框。
    """
    best: dict[Any, dict[str, Any]] = {}
    for region, entry in zip(regions, entries):
        if not entry.get("ok"):
            continue
        for hit in entry["items"]:
            card = {**hit, "details": {**hit["details"], "region": region.wire()}}
            key = hit["details"].get("id") or id(card)
            kept = best.get(key)
            if kept is None or (card.get("similarity") or 0.0) > (kept.get("similarity") or 0.0):
                best[key] = card
    ranked = sorted(best.values(), key=lambda card: card.get("similarity") or 0.0, reverse=True)
    return ranked[:top_k]


def _detect_and_crop(
    detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings
) -> tuple[list[Region], list[bytes]]:
    """在工作线程里执行：查询图里找到的检测框，以及它们的裁剪图。"""
    image = decode(image_bytes)
    boxes, scores, labels = detector.detect(
        image,
        text_range=settings.caption,
        box_threshold=settings.box_threshold,
        text_threshold=settings.text_threshold,
    )
    height, width = image.shape[:2]
    regions = regions_of(boxes, scores, labels, width=width, height=height, limit=settings.max_regions)
    return regions, [crop(image, region) for region in regions]


async def _detect_in_turn(
    runtime: ApplicationState, detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings
) -> tuple[list[Region], list[bytes]]:
    """轮到这次搜索使用 GroundingDINO 时执行 _detect_and_crop。在等待时被取消，就不会执行；
    在执行时被取消，立刻返回，轮次随这次运行结束而结束。
    """
    turn = runtime.detector_turn
    await turn.acquire()

    async def run() -> tuple[list[Region], list[bytes]]:
        try:
            return await run_in_threadpool(_detect_and_crop, detector, image_bytes, settings)
        finally:
            turn.release()

    # 用 shield 保护：取消既不会影响这次运行，也不会影响释放（没人再等的运行，结果由 shield 取走）
    return await asyncio.shield(asyncio.ensure_future(run()))


def _reason(exc: Exception) -> str:
    return f"{exc.code}: {exc.detail}" if isinstance(exc, err.AppError) else f"{type(exc).__name__}: {exc}"


async def run_region_search(
    runtime: ApplicationState,
    stack: RetrievalStack,
    settings: AutoSearchRegionSettings,
    *,
    image_bytes: bytes,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
) -> RegionOutcome:
    """检测、裁剪、搜索、合并。失败是结果的一部分，不是异常：这一步的任何问题都不能让
    业务搜图失败。
    """
    try:
        detector = runtime.require_detector()
    except err.NotReadyError as exc:
        return RegionOutcome(skipped=f"GroundingDINO 未就绪：{exc.detail}")

    started = time.perf_counter()  # 目标检测 includes the wait for the turn
    try:
        regions, crops = await _detect_in_turn(runtime, detector, image_bytes, settings)
    except Exception as exc:
        return RegionOutcome(steps=((DETECT_STEP, time.perf_counter() - started, True),), error=_reason(exc))
    detected = (DETECT_STEP, time.perf_counter() - started, False)
    if not regions:
        return RegionOutcome(steps=(detected,))

    images = [QueryImage.from_bytes(f"region:{i}:{r.label}", data) for i, (r, data) in enumerate(zip(regions, crops))]
    started = time.perf_counter()
    try:
        entries = await search_images(
            stack, images, scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id
        )
    except Exception as exc:  # 每个框都以同样的方式失败，而且是服务的故障
        searched = (SEARCH_STEP, time.perf_counter() - started, True)
        return RegionOutcome(regions=len(regions), failed=len(regions), steps=(detected, searched), error=_reason(exc))
    failed = sum(1 for entry in entries if not entry["ok"])
    searched = (SEARCH_STEP, time.perf_counter() - started, failed == len(entries))
    return RegionOutcome(
        hits=merge(regions, entries, top_k), regions=len(regions), failed=failed, steps=(detected, searched)
    )
