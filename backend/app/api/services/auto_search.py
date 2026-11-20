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
失败按没有结果处理，写在它那一行耗时日志里。兜底失败时 fallback.status=failed
带上原因，调用方能区分"没找到"和"兜底没跑成"。

查询图只读取一次（上传或下载），交给每个通道使用。请求带图片链接时，菜谱识别服务
拿到的是链接（它自己下载图片），否则拿到图片内容。

耗时日志（logger svc.auto_search，同时写平台日志）：每一步一行，「标题 | 各阶段 | 耗时 | 其他信息」，
按固定的顺序，不管哪一步先结束：整图搜索；整图没有结果时，再写区域搜索、菜谱识别，
以及跑了的 Global 兜底。路由最后为请求写一行（app.api.v1.auto_search），从请求到达到返回
结果的各步骤，相加约等于总耗时：

    整图搜索完成 | embedding推理 0.045s | vector搜索 0.012s | 后处理 0.003s | 耗时 0.061s | 结果=0
    区域搜索完成 | 排队 0.000s | 图像解码 0.005s | 目标检测 0.440s | 区域裁剪 0.003s | 区域检索 0.073s | 耗时 0.521s | 检测框=2 | 结果=3 | 区域检索明细（各框累计）=embedding推理 0.061s, vector搜索 0.010s, 后处理 0.002s
    菜谱识别完成 | 发送请求 0.001s | 等待响应 1.365s | 接收响应 0.000s | 耗时 1.368s | 结果=["荷塘小炒"] | 输入=图片链接
    业务搜图(图片链接) | 接收请求 0.001s | 图像下载 0.120s | 整图搜索 0.061s | 等待区域搜索 0.460s | 等待菜谱识别 0.847s | 总耗时 1.490s | 参数=…

「等待…」是整图搜索结束之后还要等它的时间。整图有结果时只有整图那一行和请求那一行：
还在进行的步骤被取消，整图那一行用「已取消=」列出它们。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.api.services.predict import run_predict
from app.api.services.region_search import RegionOutcome, run_region_search
from app.bootstrap.state import ApplicationState
from app.clients.recipe_detect import detect_recipes
from app.core import errors as err
from app.core.config.settings import AutoSearchRegionSettings, RecipeDetectSettings
from app.core.observability.log_helper import show_text
from app.core.rules.text_fields import tags_text
from app.utils.timer import timer_tool

logger = logging.getLogger("svc.auto_search")

T = TypeVar("T")

# 各步骤的名字：它那一行耗时的标题，也是请求那一行里的步骤名
WHOLE_STEP = "整图搜索"
REGION_STEP = "区域搜索"
RECIPE_STEP = "菜谱识别"
GLOBAL_STEP = "Global兜底"

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
    error: err.AppError | None = None


def _log(level: int, message: str, trace_id: str) -> None:
    """同一行写到本服务的 logger（末尾带 trace id）和 show_text。"""
    logger.log(level, "%s | trace_id=%s", message, trace_id)
    show_text(message, trace_id=trace_id, logger_level=level)


def _log_timing(level: int, title: str, timer: timer_tool, *details: str, trace_id: str) -> None:
    """一步的耗时行：「标题 | 各阶段 | 耗时 | 其他信息」。"""
    _log(level, " | ".join([title, timer.summary("耗时"), *details]), trace_id)


def _reason(exc: BaseException) -> str:
    return f"{exc.code}: {exc.detail}" if isinstance(exc, err.AppError) else f"{type(exc).__name__}: {exc}"


def _steps_text(steps: tuple[tuple[str, float], ...]) -> str:
    return ", ".join(f"{step} {seconds:.3f}s" for step, seconds in steps)


@contextmanager
def _timed_step(request: timer_tool, name: str) -> Generator[timer_tool, None, None]:
    """一步自己的计时器（记这一步的各阶段）。结束时停住它，并把它的耗时作为 name 记到
    请求的计时器上（抛出异常时为「name失败」）。
    """
    own = timer_tool()
    failed = True
    try:
        yield own
        failed = False
    finally:
        own.stop()
        request.record(name, own.elapsed, failed=failed)


async def _run_timed(timer: timer_tool, work: Callable[..., Awaitable[T]], /, *args: Any, **kwargs: Any) -> T:
    """work(*args, timer=timer, **kwargs)，结束时（被取消也一样）停住 timer：它那一行的耗时
    是它自己的，而不是算到输出的那一刻。协程在这里才创建，这样任务在开始之前就被取消时
    不会留下从未执行的协程。
    """
    try:
        return await work(*args, timer=timer, **kwargs)
    finally:
        timer.stop()


def _recipe_settings(runtime: ApplicationState, trace_id: str) -> RecipeDetectSettings | None:
    """当前的 RECIPE_DETECT_CONFIG；这一步关闭或配置段用不了时为 None。"""
    try:
        settings = RecipeDetectSettings.from_config(runtime.config)
    except ValueError as exc:
        _log(logging.WARNING, f"{RECIPE_STEP}已跳过：RECIPE_DETECT_CONFIG 配置有误 | {exc}", trace_id)
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


def _log_regions(outcome: RegionOutcome, timer: timer_tool, trace_id: str) -> None:
    """区域搜索那一行，在用到它的答案时记录。"""
    if outcome.skipped is not None:
        _log(logging.INFO, f"{REGION_STEP}已跳过：{outcome.skipped}", trace_id)
        return
    detail = (f"区域检索明细（各框累计）={_steps_text(outcome.searched)}",) if outcome.searched else ()
    if outcome.error is not None:
        boxes = (f"检测框={outcome.regions}",) if outcome.regions else ()  # 检测失败时还没有检测框
        _log_timing(
            logging.WARNING, f"{REGION_STEP}失败", timer, *boxes, f"错误={outcome.error}", *detail, trace_id=trace_id
        )
        return
    failed = (f"失败={outcome.failed}",) if outcome.failed else ()
    _log_timing(
        logging.WARNING if outcome.failed else logging.INFO,
        f"{REGION_STEP}完成", timer, f"检测框={outcome.regions}", f"结果={len(outcome.hits)}", *failed, *detail,
        trace_id=trace_id,
    )


def _log_recipe(recipe: _Recipe, timer: timer_tool, *, by_link: bool, trace_id: str) -> None:
    """菜谱识别那一行，在用到它的答案时记录。"""
    sent = "输入=图片链接" if by_link else "输入=图片内容"
    if recipe.error is None:
        found = json.dumps(recipe.names, ensure_ascii=False)
        _log_timing(logging.INFO, f"{RECIPE_STEP}完成", timer, f"结果={found}", sent, trace_id=trace_id)
    else:
        _log_timing(
            logging.WARNING, f"{RECIPE_STEP}失败", timer, f"错误={_reason(recipe.error)}", sent, trace_id=trace_id
        )


async def _detect_recipe(
    runtime: ApplicationState,
    settings: RecipeDetectSettings,
    *,
    image_bytes: bytes,
    image_url: str | None,
    trace_id: str,
    timer: timer_tool,
) -> _Recipe:
    """执行菜谱识别，调用的各阶段记在 timer 上。失败是结果的一部分，不是异常：这一步的
    任何问题都不能让搜索失败。
    """
    try:
        names = await detect_recipes(
            runtime.http_client,
            settings.url,
            image_url=image_url,
            image_bytes=image_bytes,
            trace_id=trace_id,
            timeout=settings.timeout,
            timer=timer,
        )
    except err.AppError as exc:
        return _Recipe([], exc)
    except Exception as exc:
        return _Recipe([], err.upstream_error(f"{type(exc).__name__}: {exc}"))
    return _Recipe(names)


# 还在运行时被丢弃的区域搜索：任务结束之前需要有引用指向它
_LEFT_RUNNING: set[asyncio.Task[Any]] = set()


def _forget(task: asyncio.Task[Any]) -> None:
    _LEFT_RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # 取出异常，这样不会被报告为"异常从未被获取"


async def _abandon(
    detection: asyncio.Task[_Recipe] | None, regions: asyncio.Task[RegionOutcome] | None
) -> list[str]:
    """停止没人会读结果的步骤（整图已经有结果，或者请求失败了），返回其中还在进行、
    被取消的那些步骤名。菜谱识别是事件循环上的调用：立刻结束。区域搜索可能正在工作线程里
    （GroundingDINO、编码器），线程无法中断，取消时可能要等那个线程：所以只取消，不等待。
    """
    cancelled = []
    if regions is not None:
        if regions.done():
            _forget(regions)
        else:
            cancelled.append(REGION_STEP)
            regions.cancel()
            _LEFT_RUNNING.add(regions)
            regions.add_done_callback(_forget)
    if detection is not None and not detection.done():
        cancelled.append(RECIPE_STEP)
        detection.cancel()
        await asyncio.gather(detection, return_exceptions=True)
    return cancelled


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

    timer：路由的计时器，这里在上面记录请求的几步：整图搜索；整图没有结果时，等待区域搜索 /
    等待菜谱识别（整图搜索结束后还要等它的时间）和 Global兜底。各步骤自己的阶段写在它们
    各自的那一行里（见模块说明）；路由负责写出请求的耗时行。
    """
    timer = timer or timer_tool()
    stack = require_retrieval(runtime)
    recipe_settings = _recipe_settings(runtime, trace_id)
    region_settings = _region_settings(runtime, score_threshold, trace_id)
    recipe_timer, region_timer = timer_tool(), timer_tool()
    detection: asyncio.Task[_Recipe] | None = None
    regions: asyncio.Task[RegionOutcome] | None = None
    if recipe_settings is not None:
        detection = asyncio.create_task(_run_timed(
            recipe_timer, _detect_recipe, runtime, recipe_settings,
            image_bytes=image_bytes, image_url=image_url, trace_id=trace_id,
        ))
    if region_settings is not None:
        regions = asyncio.create_task(_run_timed(
            region_timer, run_region_search, runtime, stack, region_settings,
            image_bytes=image_bytes, scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id,
        ))
    found: RegionOutcome | None = None
    recipe: _Recipe | None = None
    try:
        try:
            with _timed_step(timer, WHOLE_STEP) as whole:
                local = await run_in_threadpool(
                    stack.search_uc.execute,
                    image_bytes=image_bytes,
                    scene=scene,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    trace_id=trace_id,
                    timer=whole,
                )
        except Exception as exc:
            _log_timing(logging.WARNING, f"{WHOLE_STEP}失败", whole, f"错误={_reason(exc)}", trace_id=trace_id)
            raise
        # 整图有结果就是答案：另外两步不用等
        cancelled = await _abandon(detection, regions) if local["items"] else []
        _log_timing(
            logging.INFO, f"{WHOLE_STEP}完成", whole, f"结果={len(local['items'])}",
            *((f"已取消={'、'.join(cancelled)}",) if cancelled else ()),
            trace_id=trace_id,
        )
        # 整图没有结果：另外两步就是答案。按固定的顺序等它们、写它们那一行，不管谁先结束
        if not local["items"] and regions is not None:
            with timer.step(f"等待{REGION_STEP}"):
                found = await regions
            _log_regions(found, region_timer, trace_id)
        if not local["items"] and detection is not None:
            with timer.step(f"等待{RECIPE_STEP}"):
                recipe = await detection
            _log_recipe(recipe, recipe_timer, by_link=bool(image_url), trace_id=trace_id)
    except BaseException:
        await _abandon(detection, regions)
        raise

    body: dict[str, Any] = {"ok": True, "trace_id": trace_id, "scene": local["scene"]}
    heating = bool(local.get("outputs_heating"))
    if local["items"]:
        body.update(items=_self_cards(local["items"], heating), fallback={"status": "not_needed"})
        return body

    cards = _self_cards(found.hits if found is not None else [], heating)
    if recipe is not None and recipe.names:
        cards.append(_recipe_card(recipe.names))
    if cards:  # 区域结果、菜名，或者两者都有
        body.update(items=cards, fallback={"status": "not_needed"})
    elif not global_fallback:
        body.update(items=[], fallback={"status": "disabled"}, message=_empty_message("disabled", recipe))
    else:
        try:
            with _timed_step(timer, GLOBAL_STEP) as fallback:
                answer = await run_predict(
                    runtime=runtime,
                    img_bytes=image_bytes,
                    source=input_label,
                    machine_info=machine_info,
                    trace_id=trace_id,
                    save_res=False,
                    search_num=top_k,
                    timer=fallback,
                )
        except err.AppError as exc:
            _log_timing(logging.WARNING, f"{GLOBAL_STEP}失败", fallback, f"错误={_reason(exc)}", trace_id=trace_id)
            body.update(
                items=[],
                fallback={"status": "failed", "error": exc.code, "detail": err.client_detail(exc)},
                message=_empty_message("failed", recipe),
            )
        else:
            cards = _global_cards(answer.get("result"), top_k)
            _log_timing(logging.INFO, f"{GLOBAL_STEP}完成", fallback, f"结果={len(cards)}", trace_id=trace_id)
            if cards:
                body.update(items=cards, fallback={"status": "hit"})
            else:
                body.update(items=[], fallback={"status": "empty"}, message=_empty_message("empty", recipe))
    return body
