"""业务搜图（POST /api/search/auto/file、/api/search/auto/url）：先用整图查自建库，
整图没有结果时用从图里找到的区域，菜谱识别和它们同时进行，Global 搜图作为可选兜底；
最后给出一个结果。

    查询图 -> 整图搜索（scene、top_k、score_threshold）                    ┐
           -> 区域搜索（AUTO_SEARCH_REGION_CONFIG.enabled）：             │ 同时启动
              GroundingDINO 的检测框，逐个搜索后合并                      │
           -> 菜谱识别（RECIPE_DETECT_CONFIG.enabled）                    ┘
        整图有结果                   -> 返回它们的结果卡，立即返回：另外两步被丢弃
        整图没有结果                 -> 等另外两步结束，然后
            区域有结果               -> 返回它们的结果卡（识别出菜名时后面跟一张菜谱卡）
            只有菜名                 -> 返回菜谱卡
            都没有，global_fallback=false  -> 没有结果卡（fallback.status=disabled）
            都没有，global_fallback=true   -> 用 Global 搜 top_k 条
                有结果               -> 返回它们的结果卡（fallback.status=hit）
                没有，或者失败了     -> 没有结果卡（fallback.status 说明是哪种）

区域搜索（app.api.services.region_search）用请求的 scene、top_k 和 score_threshold
搜索每个检测框，从所有框的结果里取最好的 top_k 条。score_threshold=0 时，只要库里有图，
整图就一定有结果，所以不启动区域搜索。

每个通道返回的都是同一种结果卡（app.schemas.search_hit），所以不管来自哪个通道，
items[] 的形状都一样。和通道有关的信息写在结果卡上，不在外层：结果卡的第一个字段
source（self / recipe / global），以及自建库结果卡的 details 里说明是否带加热信息
（outputs_heating，按场景）。区域搜索的结果也是自建库结果卡，details.region 标明是
哪个检测框找到的。菜谱识别给出一张结果卡：菜名作为 tags，其余字段为空。

整图搜索失败（场景不存在、图片无法解码、检索服务未就绪）会让请求失败，和
POST /api/search 一样。区域搜索、菜谱识别和兜底失败不会让请求失败。区域搜索或菜谱识别
失败按没有结果处理：写日志，并在这次请求的耗时行里注明。兜底失败时 fallback.status=failed
带上原因，调用方能区分"没找到"和"兜底没跑成"。

查询图只读取一次（上传或下载），交给每个通道使用。请求带图片链接时，菜谱识别服务
拿到的是链接（它自己下载图片），否则拿到图片内容。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.api.services.predict import run_predict
from app.api.services.region_search import SEARCH_STEP as REGION_STEP
from app.api.services.region_search import RegionOutcome, run_region_search
from app.bootstrap.state import ApplicationState
from app.clients.recipe_detect import detect_recipes
from app.core import errors as err
from app.core.config.settings import AutoSearchRegionSettings, RecipeDetectSettings
from app.core.observability.log_helper import show_text
from app.core.rules.text_fields import tags_text
from app.utils.timer import timer_tool

logger = logging.getLogger("svc.auto_search")

RECIPE_STEP = "菜谱识别"  # 菜谱识别在请求耗时行里的步骤名

# items 为空的原因，按 fallback.status
_MESSAGES = {
    "disabled": "自建库没有达到阈值的结果，未开启 Global 兜底",
    "empty": "自建库和 Global 都没有找到结果",
    "failed": "自建库没有达到阈值的结果，Global 兜底失败",
}
# 菜谱识别也跑过时用的同样的信息；{recipe} 是它的结果
_MESSAGES_WITH_RECIPE = {
    "disabled": "自建库没有达到阈值的结果，{recipe}，未开启 Global 兜底",
    "empty": "自建库没有达到阈值的结果，{recipe}，Global 也没有找到结果",
    "failed": "自建库没有达到阈值的结果，{recipe}，Global 兜底失败",
}


@dataclass(frozen=True, slots=True)
class _Recipe:
    """一次菜谱识别的结果。"""

    names: list[str]  # 菜名；没有识别出菜名或调用失败时为 []
    seconds: float
    error: err.AppError | None = None


def _log(level: int, message: str, trace_id: str) -> None:
    """同一行写到本服务的 logger（末尾带 trace id）和 show_text。"""
    logger.log(level, "%s | trace_id=%s", message, trace_id)
    show_text(message, trace_id=trace_id, logger_level=level)


def _recipe_settings(runtime: ApplicationState, trace_id: str) -> RecipeDetectSettings | None:
    """当前的 RECIPE_DETECT_CONFIG；这一步关闭或配置段用不了时为 None。"""
    try:
        settings = RecipeDetectSettings.from_config(runtime.config)
    except ValueError as exc:
        _log(logging.WARNING, f"菜谱识别已跳过：RECIPE_DETECT_CONFIG 配置有误 | {exc}", trace_id)
        return None
    return settings if settings.enabled else None


def _region_settings(
    runtime: ApplicationState, score_threshold: float | None, trace_id: str
) -> AutoSearchRegionSettings | None:
    """当前的 AUTO_SEARCH_REGION_CONFIG；这一步关闭、配置段用不了，或者没有阈值时为 None
    （没有阈值时，只要能找到，整图就一定有结果）。
    """
    if score_threshold == 0:
        return None
    try:
        settings = AutoSearchRegionSettings.from_config(runtime.config)
    except ValueError as exc:
        _log(logging.WARNING, f"{REGION_STEP}已跳过：AUTO_SEARCH_REGION_CONFIG 配置有误 | {exc}", trace_id)
        return None
    return settings if settings.enabled else None


def _log_regions(outcome: RegionOutcome, trace_id: str) -> None:
    """区域搜索的结果，在用到它的答案时记录。"""
    if outcome.skipped is not None:
        _log(logging.INFO, f"{REGION_STEP}已跳过：{outcome.skipped}", trace_id)
        return
    seconds = sum(cost for _, cost, _ in outcome.steps)
    if outcome.error is not None:
        _log(
            logging.WARNING,
            f"{REGION_STEP}失败 | {outcome.error} | 检测框={outcome.regions} | 耗时 {seconds:.3f}s",
            trace_id,
        )
        return
    failed = f" | 失败={outcome.failed}" if outcome.failed else ""
    _log(
        logging.WARNING if outcome.failed else logging.INFO,
        f"{REGION_STEP}完成 | 检测框={outcome.regions} | 结果={len(outcome.hits)}{failed} | 耗时 {seconds:.3f}s",
        trace_id,
    )


async def _detect_recipe(
    runtime: ApplicationState,
    settings: RecipeDetectSettings,
    *,
    image_bytes: bytes,
    image_url: str | None,
    trace_id: str,
) -> _Recipe:
    """执行菜谱识别，记录耗时和识别结果。失败是结果的一部分，不是异常：这一步的任何问题
    都不能让搜索失败。
    """
    started = time.perf_counter()
    names: list[str] = []
    error: err.AppError | None = None
    try:
        names = await detect_recipes(
            runtime.http_client,
            settings.url,
            image_url=image_url,
            image_bytes=image_bytes,
            trace_id=trace_id,
            timeout=settings.timeout,
        )
    except err.AppError as exc:
        error = exc
    except Exception as exc:
        error = err.upstream_error(f"{type(exc).__name__}: {exc}")
    seconds = time.perf_counter() - started
    sent = "图片链接" if image_url else "图片内容"
    if error is None:
        found = json.dumps(names, ensure_ascii=False)
        _log(logging.INFO, f"{RECIPE_STEP}完成 | 结果={found} | 耗时 {seconds:.3f}s | 输入={sent}", trace_id)
    else:
        _log(
            logging.WARNING,
            f"{RECIPE_STEP}失败 | {error.code}: {error.detail} | 耗时 {seconds:.3f}s | 输入={sent}",
            trace_id,
        )
    return _Recipe(names, seconds, error)


# 还在运行时被丢弃的区域搜索：任务结束之前需要有引用指向它
_LEFT_RUNNING: set[asyncio.Task[Any]] = set()


def _forget(task: asyncio.Task[Any]) -> None:
    _LEFT_RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # 取出异常，这样不会被报告为"异常从未被获取"


async def _abandon(detection: asyncio.Task[_Recipe] | None, regions: asyncio.Task[RegionOutcome] | None) -> None:
    """停止没人会读结果的步骤（整图已经有结果，或者请求失败了）。菜谱识别是事件循环上的调用：
    立刻结束。区域搜索可能正在工作线程里（GroundingDINO、编码器），线程无法中断，
    取消时可能要等那个线程：所以只取消，不等待。
    """
    if regions is not None:
        if regions.done():
            _forget(regions)
        else:
            regions.cancel()
            _LEFT_RUNNING.add(regions)
            regions.add_done_callback(_forget)
    if detection is not None and not detection.done():
        detection.cancel()
        await asyncio.gather(detection, return_exceptions=True)


def _self_cards(hits: list[dict[str, Any]], heating: bool) -> list[dict[str, Any]]:
    """把自建库的结果（整图的或区域的）转成这次响应的结果卡：每张卡的 details 说明是否带
    加热信息（场景的 outputs_heating，每条结果都一样）。
    """
    return [
        {"source": "self", **hit, "details": {**hit["details"], "outputs_heating": bool(heating)}}
        for hit in hits
    ]


def _recipe_card(names: list[str]) -> dict[str, Any]:
    """菜谱识别的结果卡：菜名作为 tags。它不对应任何已存图片，所以其余字段保持空值。"""
    return {
        "source": "recipe",
        "tags": tags_text(names),
        "description": "",
        "original_url": None,
        "similarity": None,
        "details": {},
    }


def _global_cards(result: Any, top_k: int) -> list[dict[str, Any]]:
    """Global 结果对应的结果卡：它的列表（最多 top_k 条）；上游返回"没找到"的结构时
    （status=upstream_empty_response）没有结果卡。
    """
    if not isinstance(result, list):
        return []
    return [{"source": "global", **card} for card in result if isinstance(card, dict)][:top_k]


def _empty_message(status: str, recipe: _Recipe | None) -> str:
    """items 为空的原因：status 是 fallback.status；recipe 是菜谱识别的结果（如果跑过）。"""
    if recipe is None:
        return _MESSAGES[status]
    came_to = f"{RECIPE_STEP}失败" if recipe.error is not None else f"{RECIPE_STEP}没有识别到菜名"
    return _MESSAGES_WITH_RECIPE[status].format(recipe=came_to)


async def run_auto_search(
    runtime: ApplicationState,
    *,
    image_bytes: bytes,
    image_url: str | None = None,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    global_fallback: bool,
    machine_info: str | None,
    trace_id: str,
    input_label: str,
    timer: timer_tool | None = None,
) -> dict[str, Any]:
    """image_url：请求带图片链接时（URL 路由）的查询图链接；这时发给菜谱识别服务的是链接
    而不是图片内容。

    timer：路由的计时器。每个通道都把自己的步骤记在上面（整图的 embedding推理 / vector搜索 /
    后处理；整图没有结果时，再记区域搜索的 目标检测 / 区域搜索、菜谱识别，以及兜底的
    global搜索 / 后处理）；路由负责写出请求的耗时行。区域搜索和菜谱识别与整图的几步
    同时进行，所以各步骤相加可能超过总耗时。
    """
    timer = timer or timer_tool()
    stack = require_retrieval(runtime)
    recipe_settings = _recipe_settings(runtime, trace_id)
    region_settings = _region_settings(runtime, score_threshold, trace_id)
    detection: asyncio.Task[_Recipe] | None = None
    regions: asyncio.Task[RegionOutcome] | None = None
    if recipe_settings is not None:
        detection = asyncio.create_task(
            _detect_recipe(runtime, recipe_settings, image_bytes=image_bytes, image_url=image_url, trace_id=trace_id)
        )
    if region_settings is not None:
        regions = asyncio.create_task(
            run_region_search(
                runtime, stack, region_settings,
                image_bytes=image_bytes, scene=scene, top_k=top_k, score_threshold=score_threshold,
                trace_id=trace_id,
            )
        )
    found: RegionOutcome | None = None
    recipe: _Recipe | None = None
    try:
        local = await run_in_threadpool(
            stack.search_uc.execute,
            image_bytes=image_bytes,
            scene=scene,
            top_k=top_k,
            score_threshold=score_threshold,
            trace_id=trace_id,
            timer=timer,
        )
        if not local["items"]:  # 整图没有结果：另外两步就是答案
            found = await regions if regions is not None else None
            recipe = await detection if detection is not None else None
    except BaseException:
        await _abandon(detection, regions)
        raise

    body: dict[str, Any] = {"ok": True, "trace_id": trace_id, "scene": local["scene"]}
    heating = bool(local.get("outputs_heating"))
    if local["items"]:
        await _abandon(detection, regions)  # 不用它们就已经有答案了
        body.update(items=_self_cards(local["items"], heating), fallback={"status": "not_needed"})
        return body

    # 在这里记录，而不是在它们结束时：放在整图的几步之后，不管谁先结束
    if found is not None:
        _log_regions(found, trace_id)
        for step, seconds, failed in found.steps:
            timer.record(step, seconds, failed=failed)
    if recipe is not None:
        timer.record(RECIPE_STEP, recipe.seconds, failed=recipe.error is not None)

    cards = _self_cards(found.hits if found is not None else [], heating)
    if recipe is not None and recipe.names:
        cards.append(_recipe_card(recipe.names))
    if cards:  # 区域结果、菜名，或者两者都有
        body.update(items=cards, fallback={"status": "not_needed"})
    elif not global_fallback:
        body.update(items=[], fallback={"status": "disabled"}, message=_empty_message("disabled", recipe))
    else:
        try:
            answer = await run_predict(
                runtime=runtime,
                img_bytes=image_bytes,
                source=input_label,
                machine_info=machine_info,
                trace_id=trace_id,
                save_res=False,
                search_num=top_k,
                timer=timer,
            )
        except err.AppError as exc:
            message = f"auto search: global fallback failed | trace_id={trace_id} | {exc.code}: {exc.detail}"
            logger.warning(message)
            show_text(message, trace_id=trace_id, logger_level=logging.WARNING)
            body.update(
                items=[],
                fallback={"status": "failed", "error": exc.code, "detail": err.client_detail(exc)},
                message=_empty_message("failed", recipe),
            )
        else:
            cards = _global_cards(answer.get("result"), top_k)
            if cards:
                body.update(items=cards, fallback={"status": "hit"})
            else:
                body.update(items=[], fallback={"status": "empty"}, message=_empty_message("empty", recipe))
    return body
