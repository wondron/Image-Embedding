"""区域搜索，业务搜图的第二次尝试（app.api.services.auto_search）：GroundingDINO 在查询图里
找到的检测框，每个都作为一张单独的查询图搜自建库，合并成一个结果列表。

检测（detect_regions）每个业务搜图最多做一次，区域搜索和菜谱识别（app.api.services.recipe_detect）
都用它的检测框：

    目标检测（detect_regions）：
        查询图 -> 按编码器的方式解码（OpenCV，BGR）
               -> 用 AUTO_SEARCH_REGION_CONFIG 的 caption、box_threshold、text_threshold 跑 GroundingDINO
               -> 检测框换成像素坐标并裁到图内；任一边小于 MIN_REGION_SIDE 的丢掉；
                  按检测置信度从高到低，最多 max_regions 个
               -> 每个框裁剪出来（BGR；区域搜索要用时再编码成 JPEG，编码方式和上传的一样）
    区域搜索（run_region_search）：
        每个框的裁剪图（JPEG）像 POST /api/search/batch 那样搜索
        （search_images）：每个都用请求的 scene、top_k 和 score_threshold
        -> 所有框的结果：每张已存图片只留一条（相似度最高的），按相似度从高到低，
           最多 top_k 条；details.region 说明是哪个框找到的

它们从不让业务搜图失败：detect_regions 和 run_region_search 返回结果（检测框 / 命中的结果，
或者没有结果的原因），由调用方记日志。检测的各步骤记在检测结果上，用到它的每一步（区域搜索、
菜谱识别）都把它们记到自己的计时器上，所以两步的那一行里都有这几步：

    排队       等 GroundingDINO 的轮次（见下）和工作线程
    图像解码 / 目标检测 / 区域裁剪     在工作线程里

区域搜索自己的一步：

    区域检索   搜索所有裁剪图；每个框的 embedding推理 / vector搜索 / 后处理 的累计在结果的 searched 里

失败的那一步记为「…失败」（区域检索：每个框都失败了）。

GroundingDINO 一次只处理一张图，而每个业务搜图都会启动检测，所以各次检测轮流使用它
（ApplicationState.detector_turn）。它们在事件循环上排队，整图已经有结果而被丢弃的检测
直接离开队列，不会运行模型。工作线程无法中断，所以一次轮次持续到模型跑完为止，即使对应的
检测在这期间被丢弃：如果改成线程去等模型自己的锁，等到之后就会为没人要的结果跑一次模型，
每一次这样的运行都会推迟仍在等待结果的检测。区域搜索被丢弃之后，它剩下的裁剪图都不会再搜索。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable
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
class Detection:
    """查询图上的一次目标检测（detect_regions）：区域搜索和菜谱识别共用。"""

    regions: list[Region] = field(default_factory=list)  # 检测框，按检测置信度从高到低
    crops: list[np.ndarray] = field(default_factory=list)  # 每个框的裁剪图（BGR，是解码后查询图的视图）
    jpegs: list[bytes] = field(default_factory=list)  # 每个框的裁剪图编码成 JPEG；要了才有（区域搜索）
    steps: tuple[tuple[str, float], ...] = ()  # 排队 / 图像解码 / 目标检测 / 区域裁剪；失败的那一步带「失败」
    error: str | None = None  # 检测中途失败的原因
    skipped: str | None = None  # 完全没有检测的原因

    def record(self, timer: timer_tool) -> None:
        """把检测的各步骤记到用到它的那一步的计时器上。"""
        for step, seconds in self.steps:
            timer.record(step, seconds)


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


def find_regions(detector: Any, image: np.ndarray, settings: AutoSearchRegionSettings) -> tuple[list[Region], int]:
    """查询图（BGR）上用到的检测框：按 settings 的 caption 和两个阈值跑 GroundingDINO，再经过
    regions_of（像素坐标、丢掉小的、按置信度最多 max_regions 个）。另返回 GroundingDINO 给出的框数
    （丢掉之前）。业务搜图的目标检测和 /api/tools/recipe 都用它：工具里看到的框就是业务搜图用的框。
    """
    boxes, scores, labels = detector.detect(
        image,
        text_range=settings.caption,
        box_threshold=settings.box_threshold,
        text_threshold=settings.text_threshold,
    )
    height, width = image.shape[:2]
    return regions_of(boxes, scores, labels, width=width, height=height, limit=settings.max_regions), len(boxes)


def crop(image: np.ndarray, region: Region) -> np.ndarray:
    """BGR 图像中的一块区域（视图，不复制）。"""
    x1, y1, x2, y2 = region.box
    return image[y1:y2, x1:x2]


def encode(piece: np.ndarray) -> bytes:
    """一张裁剪图，编码方式和上传的一样（JPEG）。"""
    ok, encoded = cv2.imencode(".jpg", piece, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError(f"a {piece.shape[1]}x{piece.shape[0]} region cannot be encoded")
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


_Found = tuple[list[Region], list[np.ndarray], list[bytes]]  # 检测框、它们的裁剪图（BGR / JPEG）


def _detect_and_crop(
    detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings, timer: timer_tool, jpeg: bool
) -> _Found:
    """在工作线程里执行：查询图里找到的检测框，以及它们的裁剪图（jpeg 为 true 时另有 JPEG）。
    timer 是这次运行自己的计时器，在提交时创建：从那时到线程开始执行的时间记为排队。
    """
    timer.mark(QUEUE_STEP)
    with timer.step(DECODE_STEP):
        image = decode(image_bytes)
    with timer.step(DETECT_STEP):
        regions, _ = find_regions(detector, image, settings)
    with timer.step(CROP_STEP):
        crops = [crop(image, region) for region in regions]
        jpegs = [encode(piece) for piece in crops] if jpeg else []
    return regions, crops, jpegs


async def _detect_in_turn(
    runtime: ApplicationState,
    detector: Any,
    image_bytes: bytes,
    settings: AutoSearchRegionSettings,
    timer: timer_tool,
    jpeg: bool,
) -> _Found:
    """轮到这次检测使用 GroundingDINO 时执行 _detect_and_crop。在等待时被取消，就不会执行；
    在执行时被取消，立刻返回，轮次随这次运行结束而结束。

    timer：检测的计时器。等轮次的时间记为排队（加上线程里记的等线程的时间），线程结束后
    再记线程里的几步；在执行时被取消，线程里的几步不记。
    """
    turn = runtime.detector_turn
    queued = time.perf_counter()
    await turn.acquire()
    timer.record(QUEUE_STEP, time.perf_counter() - queued)
    work = timer_tool()  # 线程里的几步：线程结束之前只有线程在写它

    async def run() -> _Found:
        try:
            return await run_in_threadpool(_detect_and_crop, detector, image_bytes, settings, work, jpeg)
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


async def detect_regions(
    runtime: ApplicationState, image_bytes: bytes, settings: AutoSearchRegionSettings, *, jpeg: bool
) -> Detection:
    """轮到这次检测时检测、裁剪（见模块说明）；jpeg：另把裁剪图编码成 JPEG（区域搜索要用）。
    失败是结果的一部分，不是异常：检测的任何问题都不能让业务搜图失败。在等轮次时被取消，
    就不会运行模型。
    """
    try:
        detector = runtime.require_detector()
    except err.NotReadyError as exc:
        return Detection(skipped=f"GroundingDINO 未就绪：{exc.detail}")
    timer = timer_tool()
    try:
        regions, crops, jpegs = await _detect_in_turn(runtime, detector, image_bytes, settings, timer, jpeg)
    except Exception as exc:
        return Detection(steps=tuple(timer.items()), error=_reason(exc))
    return Detection(regions, crops, jpegs, steps=tuple(timer.items()))


async def run_region_search(
    stack: RetrievalStack,
    detection: Awaitable[Detection],
    *,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
    timer: timer_tool | None = None,
) -> RegionOutcome:
    """等检测结果（detect_regions 的任务，要了 JPEG），搜索每个框的裁剪图、合并。失败是结果的一部分，
    不是异常：这一步的任何问题都不能让业务搜图失败。检测和菜谱识别共用：这一步被取消时，检测照常进行。

    timer：这次区域搜索的计时器，记录 排队 / 图像解码 / 目标检测 / 区域裁剪（检测的几步）和
    区域检索（见模块说明）。被取消时只有已经结束的几步。
    """
    timer = timer if timer is not None else timer_tool()
    found = await asyncio.shield(detection)
    found.record(timer)
    if found.skipped is not None:
        return RegionOutcome(skipped=found.skipped)
    if found.error is not None:
        return RegionOutcome(error=found.error)
    regions = found.regions
    if not regions:
        return RegionOutcome()

    images = [
        QueryImage.from_bytes(f"region:{i}:{r.label}", data) for i, (r, data) in enumerate(zip(regions, found.jpegs))
    ]
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
