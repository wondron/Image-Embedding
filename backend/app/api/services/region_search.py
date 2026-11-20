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
由调用方记日志。各步骤记在调用方传入的计时器上：

    排队       等 GroundingDINO 的轮次（见下）和工作线程
    图像解码 / 目标检测 / 区域裁剪     在工作线程里
    区域检索   搜索所有裁剪图；每个框的 embedding推理 / vector搜索 / 后处理 的累计在结果的 searched 里

失败的那一步记为「…失败」（区域检索：每个框都失败了）。

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
from app.utils.timer import timer_tool

if TYPE_CHECKING:
    from app.bootstrap.retrieval_stack import RetrievalStack

# 区域搜索那一行耗时里的步骤名
QUEUE_STEP = "排队"
DECODE_STEP = "图像解码"
DETECT_STEP = "目标检测"
CROP_STEP = "区域裁剪"
SEARCH_STEP = "区域检索"

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
    searched: tuple[tuple[str, float], ...] = ()  # 区域检索里每个框的各步骤，所有框累计 (步骤, 秒数)
    error: str | None = None  # 中途停下的原因（某一步失败了）
    skipped: str | None = None  # 完全没有运行的原因


def decode(image_bytes: bytes) -> np.ndarray:
    """按编码器的方式读取的查询图：BGR 数组。"""
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("the query image cannot be decoded")
    return image


def pixel_box(box: Sequence[float], *, width: int, height: int) -> tuple[int, int, int, int]:
    """GroundingDINO 的一个检测框（归一化的 cx, cy, w, h）换成 width x height 图片上的像素坐标
    x1, y1, x2, y2，并裁到图内（/api/tools/gdino 也用它，返回的框和这里裁剪的框一致）。
    """
    cx, cy, w, h = box
    return (
        max(0, math.floor((cx - w / 2) * width)),
        max(0, math.floor((cy - h / 2) * height)),
        min(width, math.ceil((cx + w / 2) * width)),
        min(height, math.ceil((cy + h / 2) * height)),
    )


def regions_of(
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    labels: Sequence[str],
    *,
    width: int,
    height: int,
    limit: int,
) -> list[Region]:
    """GroundingDINO 的检测框换成 width x height 图片上的像素坐标（pixel_box）；丢掉小的；
    按置信度从高到低，最多 limit 个。
    """
    found = []
    for box, score, label in zip(boxes, scores, labels):
        x1, y1, x2, y2 = pixel_box(box, width=width, height=height)
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
    detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings, timer: timer_tool
) -> tuple[list[Region], list[bytes]]:
    """在工作线程里执行：查询图里找到的检测框，以及它们的裁剪图。timer 是这次运行自己的
    计时器，在提交时创建：从那时到线程开始执行的时间记为排队。
    """
    timer.mark(QUEUE_STEP)
    with timer.step(DECODE_STEP):
        image = decode(image_bytes)
    with timer.step(DETECT_STEP):
        boxes, scores, labels = detector.detect(
            image,
            text_range=settings.caption,
            box_threshold=settings.box_threshold,
            text_threshold=settings.text_threshold,
        )
    with timer.step(CROP_STEP):
        height, width = image.shape[:2]
        regions = regions_of(boxes, scores, labels, width=width, height=height, limit=settings.max_regions)
        crops = [crop(image, region) for region in regions]
    return regions, crops


async def _detect_in_turn(
    runtime: ApplicationState,
    detector: Any,
    image_bytes: bytes,
    settings: AutoSearchRegionSettings,
    timer: timer_tool,
) -> tuple[list[Region], list[bytes]]:
    """轮到这次搜索使用 GroundingDINO 时执行 _detect_and_crop。在等待时被取消，就不会执行；
    在执行时被取消，立刻返回，轮次随这次运行结束而结束。

    timer：区域搜索的计时器。等轮次的时间记为排队（加上线程里记的等线程的时间），线程结束后
    再记线程里的几步；在执行时被取消，线程里的几步不记。
    """
    turn = runtime.detector_turn
    queued = time.perf_counter()
    await turn.acquire()
    timer.record(QUEUE_STEP, time.perf_counter() - queued)
    work = timer_tool()  # 线程里的几步：线程结束之前只有线程在写它

    async def run() -> tuple[list[Region], list[bytes]]:
        try:
            return await run_in_threadpool(_detect_and_crop, detector, image_bytes, settings, work)
        finally:
            turn.release()

    # 用 shield 保护：取消既不会影响这次运行，也不会影响释放。取消之后 shield 不再管这次运行，
    # 也就没人再取它的结果：由 _retrieve 取出它的异常
    job = asyncio.ensure_future(run())
    job.add_done_callback(_retrieve)
    try:
        return await asyncio.shield(job)
    finally:
        if job.done():
            timer.merge(work)


def _retrieve(job: asyncio.Future[Any]) -> None:
    """取出一次运行的异常，这样在没人等它时（搜索被丢弃了），失败不会被 asyncio 报告为
    "异常从未被获取"。
    """
    if not job.cancelled():
        job.exception()


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
    timer: timer_tool | None = None,
) -> RegionOutcome:
    """检测、裁剪、搜索、合并。失败是结果的一部分，不是异常：这一步的任何问题都不能让
    业务搜图失败。

    timer：这次区域搜索的计时器，记录 排队 / 图像解码 / 目标检测 / 区域裁剪 / 区域检索
    （见模块说明）。被取消时只有已经结束的几步。
    """
    timer = timer if timer is not None else timer_tool()
    try:
        detector = runtime.require_detector()
    except err.NotReadyError as exc:
        return RegionOutcome(skipped=f"GroundingDINO 未就绪：{exc.detail}")

    try:
        regions, crops = await _detect_in_turn(runtime, detector, image_bytes, settings, timer)
    except Exception as exc:
        return RegionOutcome(error=_reason(exc))
    if not regions:
        return RegionOutcome()

    images = [QueryImage.from_bytes(f"region:{i}:{r.label}", data) for i, (r, data) in enumerate(zip(regions, crops))]
    per_box = timer_tool()  # 每个框的步骤，search_images 按框的顺序累计到这里
    started = time.perf_counter()
    try:
        entries = await search_images(
            stack, images, scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id,
            timer=per_box,
        )
    except Exception as exc:  # 每个框都以同样的方式失败，而且是服务的故障
        timer.record(SEARCH_STEP, time.perf_counter() - started, failed=True)
        return RegionOutcome(
            regions=len(regions), failed=len(regions), searched=tuple(per_box.items()), error=_reason(exc)
        )
    failed = sum(1 for entry in entries if not entry["ok"])
    timer.record(SEARCH_STEP, time.perf_counter() - started, failed=failed == len(entries))
    return RegionOutcome(
        hits=merge(regions, entries, top_k), regions=len(regions), failed=failed, searched=tuple(per_box.items())
    )
