"""菜谱识别，业务搜图的一步（app.api.services.auto_search）：查询图里的菜名。

    GroundingDINO 的检测框（和区域搜索同一次检测，app.api.services.region_search.detect_regions）
    -> 每个框的裁剪图（BGR）交给菜谱识别模型（app.clients.recipe_classifier）：底库里和它最相近的
       菜品，以及余弦相似度
    -> 相似度高于 RECIPE_DETECT_CONFIG.threshold（不含等于）的菜名；几个框是同一道菜时只留一个，
       按它最高的相似度算；按相似度从高到低

每个框最多给出一个菜名，没过阈值的框不给，所以菜名数不超过检测框数，可以更少；没有检测框时
没有菜名（不会拿整张图去识别）。

它从不让业务搜图失败：run_recipe_detect 返回结果（菜名，或者没有菜名的原因），由调用方记日志。
各步骤记在调用方传入的计时器上：检测的几步（排队 / 图像解码 / 目标检测 / 区域裁剪，见
region_search），以及

    菜谱推理   在工作线程里逐个推理检测框的裁剪图、和底库比对

失败的那一步记为「…失败」。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

from app.api.services.region_search import Detection
from app.core import errors as err
from app.utils.timer import timer_tool

if TYPE_CHECKING:
    from app.clients.recipe_classifier import RecipeClassifier, RecipeMatch

# 菜谱识别那一行耗时里自己的一步（其余是检测的几步）
CLASSIFY_STEP = "菜谱推理"


@dataclass(frozen=True, slots=True)
class RecipeOutcome:
    """一次菜谱识别的结果。"""

    names: list[str] = field(default_factory=list)  # 识别出的菜名，按相似度从高到低；没有时为 []
    matches: list[RecipeMatch] = field(default_factory=list)  # 每个框最相近的菜品，按框的顺序（没过阈值的也在）
    regions: int = 0  # 检测框数
    error: str | None = None  # 中途停下的原因（检测或推理失败了）
    skipped: str | None = None  # 完全没有运行的原因


def dish_names(matches: Sequence[RecipeMatch], threshold: float) -> list[str]:
    """相似度高于 threshold 的菜名：同名只留一个（按它最高的相似度），按相似度从高到低，
    相同时保持框的顺序。
    """
    best: dict[str, float] = {}
    for match in matches:
        if match.label and match.score > threshold and match.score > best.get(match.label, -math.inf):
            best[match.label] = match.score
    return sorted(best, key=best.__getitem__, reverse=True)


def _reason(exc: Exception) -> str:
    return f"{exc.code}: {exc.detail}" if isinstance(exc, err.AppError) else f"{type(exc).__name__}: {exc}"


async def run_recipe_detect(
    classifier: RecipeClassifier,
    detection: Awaitable[Detection],
    *,
    threshold: float,
    timer: timer_tool | None = None,
) -> RecipeOutcome:
    """等检测结果（detect_regions 的任务），识别每个框的裁剪图，留下高于 threshold 的菜名。
    失败是结果的一部分，不是异常：这一步的任何问题都不能让业务搜图失败。检测和区域搜索共用：
    这一步被取消时，检测照常进行。

    timer：这次菜谱识别的计时器，记录检测的几步和 菜谱推理（见模块说明）。被取消时只有已经结束的几步。
    """
    timer = timer if timer is not None else timer_tool()
    found = await asyncio.shield(detection)
    found.record(timer)
    if found.skipped is not None:
        return RecipeOutcome(skipped=found.skipped)
    if found.error is not None:
        return RecipeOutcome(error=found.error)
    if not found.regions:
        return RecipeOutcome()
    try:
        with timer.step(CLASSIFY_STEP):
            matches = list(await run_in_threadpool(classifier.classify, found.crops))
    except Exception as exc:
        return RecipeOutcome(regions=len(found.regions), error=_reason(exc))
    return RecipeOutcome(dish_names(matches, threshold), matches, regions=len(found.regions))
