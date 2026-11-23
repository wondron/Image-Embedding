"""业务搜图（POST /api/search/auto/file、/api/search/auto/url）：先用整图查自建库，
整图没有结果时用从图里找到的区域和识别出的菜名，Global 搜图作为可选兜底；最后给出一个结果。

    查询图 -> 整图搜索（scene、top_k、score_threshold）                                ┐
           -> 目标检测：GroundingDINO 的检测框（AUTO_SEARCH_REGION_CONFIG 的参数），     │
              区域搜索或菜谱识别要用时才做，两步共用这一次检测                              │ 同时启动
                -> 区域搜索（AUTO_SEARCH_REGION_CONFIG.enabled）：逐个框搜索后合并        │
                -> 菜谱识别（RECIPE_DETECT_CONFIG.enabled）：逐个框识别菜名              ┘
        整图有结果                   -> 返回它们的结果卡，立即返回：另外几步被丢弃
        整图没有结果                 -> 等区域搜索和菜谱识别结束，然后
            区域有结果               -> 返回它们的结果卡（识别出菜名时后面跟一张菜谱卡）
            只有菜名                 -> 返回菜谱卡
            都没有，global_fallback=false  -> 没有结果卡（fallback.status=disabled）
            都没有，global_fallback=true   -> 用 Global 搜 top_k 条
                有结果               -> 返回它们的结果卡（fallback.status=hit）
                没有，或者失败了     -> 没有结果卡（fallback.status 说明是哪种）

区域搜索（app.api.services.region_search）用请求的 scene、top_k 和 score_threshold
搜索每个检测框，从所有框的结果里取最好的 top_k 条。score_threshold=0 时，只要库里有图，
整图就一定有结果，所以不启动区域搜索。

菜谱识别（app.api.services.recipe_detect）把每个检测框的裁剪图交给菜谱识别模型
（ApplicationState.recipe_classifier），相似度高于 RECIPE_DETECT_CONFIG.threshold 的菜名
（同名只留一个，按相似度从高到低）作为一张菜谱卡；每个框最多一个菜名，所以菜名数不超过
检测框数。区域搜索关闭时，菜谱识别照样按 AUTO_SEARCH_REGION_CONFIG 的参数检测。

每个通道返回的都是同一种结果卡（app.schemas.search_hit），所以不管来自哪个通道，
items[] 的形状都一样。和通道有关的信息写在结果卡上，不在外层：结果卡的第一个字段
source（self / recipe / global），以及自建库结果卡的 details 里说明是否带加热信息
（outputs_heating，按场景）。区域搜索的结果也是自建库结果卡，details.region 标明是
哪个检测框找到的。菜谱识别给出一张结果卡：菜名作为 tags，其余字段为空。

整图搜索失败（场景不存在、图片无法解码、检索服务未就绪）会让请求失败，和
POST /api/search 一样。目标检测、区域搜索、菜谱识别和兜底失败不会让请求失败。区域搜索或
菜谱识别失败（检测失败时两步都失败）按没有结果处理，写在它那一行耗时日志里。兜底失败时
fallback.status=failed 带上原因，调用方能区分"没找到"和"兜底没跑成"。

查询图只读取一次（上传或下载），交给每个通道使用。

耗时日志（logger svc.auto_search，同时写平台日志）：每一步一行，「标题 | 各阶段 | 耗时 | 其他信息」，
按固定的顺序，不管哪一步先结束：整图搜索；整图没有结果时，再写区域搜索、菜谱识别，
以及跑了的 Global 兜底。区域搜索和菜谱识别那两行都有它们共用的那次检测的几步（排队 /
图像解码 / 目标检测 / 区域裁剪）。路由最后为请求写一行（app.api.v1.auto_search），从请求到达
到返回结果的各步骤，相加约等于总耗时：

    整图搜索完成 | embedding推理 0.045s | vector搜索 0.012s | 后处理 0.003s | 耗时 0.061s | 结果=0
    区域搜索完成 | 排队 0.000s | 图像解码 0.005s | 目标检测 0.440s | 区域裁剪 0.003s | 区域检索 0.073s | 耗时 0.521s | 检测框=2 | 结果=3 | 区域检索明细（各框累计）=embedding推理 0.061s, vector搜索 0.010s, 后处理 0.002s
    菜谱识别完成 | 排队 0.000s | 图像解码 0.005s | 目标检测 0.440s | 区域裁剪 0.003s | 菜谱推理 0.031s | 耗时 0.479s | 检测框=2 | 结果=["荷塘小炒"] | 阈值=0.95 | 各框最相近=荷塘小炒 0.9712、清蒸鲈鱼 0.9031
    业务搜图(图片链接) | 接收请求 0.001s | 图像下载 0.120s | 整图搜索 0.061s | 等待区域搜索 0.460s | 等待菜谱识别 0.000s | 总耗时 0.642s | 参数=…

「等待…」是整图搜索结束之后还要等它的时间。整图有结果时只有整图那一行和请求那一行：
还在进行的步骤被取消，整图那一行用「已取消=」列出它们。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Any, TypeVar

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.api.services.predict import run_predict
from app.api.services.recipe_detect import RecipeOutcome, run_recipe_detect
from app.api.services.region_search import Detection, RegionOutcome, detect_regions, run_region_search
from app.bootstrap.state import ApplicationState
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


def _detect_settings(
    runtime: ApplicationState, *, regions: bool, recipe: bool, trace_id: str
) -> AutoSearchRegionSettings | None:
    """当前的 AUTO_SEARCH_REGION_CONFIG：区域搜索的开关，以及检测的参数（菜谱识别用同一次检测，
    也用这些参数）。regions：这次可以做区域搜索（有阈值：没有阈值时，只要能找到，整图就一定有
    结果）；recipe：要做菜谱识别。两步都不做，或者配置段用不了时为 None；用不了时为会用到它的
    每一步记一条警告。
    """
    if not (regions or recipe):
        return None
    name = "AUTO_SEARCH_REGION_CONFIG"
    try:
        settings = AutoSearchRegionSettings.from_config(runtime.config)
    except ValueError as exc:
        for step, uses in ((REGION_STEP, regions), (RECIPE_STEP, recipe)):
            if uses:
                _log(logging.WARNING, f"{step}已跳过：{name} 配置有误 | {exc}", trace_id)
        return None
    if recipe and not settings.enabled:  # 区域搜索关着，from_config 没有检查检测参数
        try:
            settings.check()
        except ValueError as exc:
            _log(logging.WARNING, f"{RECIPE_STEP}已跳过：{name} 配置有误 | {exc}", trace_id)
            return None
    return settings


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


def _log_recipe(recipe: RecipeOutcome, timer: timer_tool, threshold: float, trace_id: str) -> None:
    """菜谱识别那一行，在用到它的答案时记录。各框最相近：每个检测框在底库里最相近的菜品和
    相似度（没过阈值的也列出来，调阈值时看它）。
    """
    if recipe.skipped is not None:
        _log(logging.INFO, f"{RECIPE_STEP}已跳过：{recipe.skipped}", trace_id)
        return
    boxes = (f"检测框={recipe.regions}",) if recipe.regions else ()  # 检测失败时还没有检测框
    if recipe.error is not None:
        _log_timing(logging.WARNING, f"{RECIPE_STEP}失败", timer, *boxes, f"错误={recipe.error}", trace_id=trace_id)
        return
    closest = "、".join(f"{match.label} {match.score:.4f}" for match in recipe.matches)
    _log_timing(
        logging.INFO, f"{RECIPE_STEP}完成", timer, f"检测框={recipe.regions}",
        f"结果={json.dumps(recipe.names, ensure_ascii=False)}", f"阈值={threshold:g}",
        *((f"各框最相近={closest}",) if closest else ()),
        trace_id=trace_id,
    )


# 还在运行时被丢弃的步骤：任务结束之前需要有引用指向它
_LEFT_RUNNING: set[asyncio.Task[Any]] = set()


def _forget(task: asyncio.Task[Any]) -> None:
    _LEFT_RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # 取出异常，这样不会被报告为"异常从未被获取"


def _abandon(*steps: tuple[str | None, asyncio.Task[Any] | None]) -> list[str]:
    """停止没人会读结果的步骤（整图已经有结果，或者请求失败了），返回其中还在进行、被取消的那些
    步骤名。steps：（步骤名，任务）；目标检测不是单独的一步，名字为 None。它们都可能正在工作线程里
    （GroundingDINO、编码器、菜谱识别模型），线程无法中断，取消时可能要等那个线程：所以只取消，不等待。
    """
    cancelled = []
    for name, task in steps:
        if task is None:
            continue
        if task.done():
            _forget(task)
            continue
        if name is not None:
            cancelled.append(name)
        task.cancel()
        _LEFT_RUNNING.add(task)
        task.add_done_callback(_forget)
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


def _empty_message(status: str, recipe: RecipeOutcome | None) -> str:
    """items 为空的原因：status 是 fallback.status；recipe 是菜谱识别的结果（开启了这一步时）。"""
    if recipe is None:
        return _MESSAGES[status]
    if recipe.skipped is not None:
        came_to = f"{RECIPE_STEP}没有运行"
    elif recipe.error is not None:
        came_to = f"{RECIPE_STEP}失败"
    else:
        came_to = f"{RECIPE_STEP}没有识别到菜名"
    return _MESSAGES_WITH_RECIPE[status].format(recipe=came_to)


async def run_auto_search(
    runtime: ApplicationState,
    *,
    image_bytes: bytes,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    global_fallback: bool,
    machine_info: str | None,
    trace_id: str,
    input_label: str,
    timer: timer_tool | None = None,
) -> dict[str, Any]:
    """image_bytes：查询图（上传的或下载好的），各通道共用。

    timer：路由的计时器，这里在上面记录请求的几步：整图搜索；整图没有结果时，等待区域搜索 /
    等待菜谱识别（整图搜索结束后还要等它的时间）和 Global兜底。各步骤自己的阶段写在它们
    各自的那一行里（见模块说明）；路由负责写出请求的耗时行。
    """
    timer = timer or timer_tool()
    stack = require_retrieval(runtime)
    recipe_settings = _recipe_settings(runtime, trace_id)
    detect_settings = _detect_settings(
        runtime, regions=score_threshold != 0, recipe=recipe_settings is not None, trace_id=trace_id
    )
    searching_regions = detect_settings is not None and detect_settings.enabled and score_threshold != 0
    # 菜谱识别：开着、检测参数能用时才做；模型没有就绪时这一步的答案就是「已跳过」
    classifier = None
    recipe: RecipeOutcome | None = None
    if recipe_settings is not None and detect_settings is not None:
        try:
            classifier = runtime.require_recipe_classifier()
        except err.NotReadyError as exc:
            recipe = RecipeOutcome(skipped=f"菜谱识别模型未就绪：{exc.detail}")

    region_timer, recipe_timer = timer_tool(), timer_tool()
    detection: asyncio.Task[Detection] | None = None
    regions: asyncio.Task[RegionOutcome] | None = None
    recognition: asyncio.Task[RecipeOutcome] | None = None
    if searching_regions or classifier is not None:  # 两步共用这一次检测：区域搜索要 JPEG 的裁剪图
        detection = asyncio.create_task(detect_regions(runtime, image_bytes, detect_settings, jpeg=searching_regions))
    if searching_regions:
        regions = asyncio.create_task(_run_timed(
            region_timer, run_region_search, stack, detection,
            scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id,
        ))
    if classifier is not None:
        recognition = asyncio.create_task(_run_timed(
            recipe_timer, run_recipe_detect, classifier, detection, threshold=recipe_settings.threshold,
        ))
    steps = ((REGION_STEP, regions), (RECIPE_STEP, recognition), (None, detection))
    found: RegionOutcome | None = None
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
        # 整图有结果就是答案：另外几步不用等
        cancelled = _abandon(*steps) if local["items"] else []
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
        if not local["items"] and recognition is not None:
            with timer.step(f"等待{RECIPE_STEP}"):
                recipe = await recognition
        if not local["items"] and recipe is not None:
            _log_recipe(recipe, recipe_timer, recipe_settings.threshold, trace_id)
    except BaseException:
        _abandon(*steps)
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
