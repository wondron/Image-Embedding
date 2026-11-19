"""Business search (POST /api/search/auto/file, /api/search/auto/url): the whole image in the
self-built library first, the regions found in it when that finds nothing, the recipe detection
beside them, the global search as an optional fallback; one answer.

    query image -> whole-image search (scene, top_k, score_threshold)       ┐
                -> region search (AUTO_SEARCH_REGION_CONFIG.enabled):         │ started together
                   GroundingDINO's boxes, each searched, merged               │
                -> recipe detection (RECIPE_DETECT_CONFIG.enabled)           ┘
        whole-image hits                -> their cards, at once: the other two are dropped
        no whole-image hit              -> wait for the other two, then
            region hits                 -> their cards (a recipe card after them when dishes were recognised)
            dish names only             -> the recipe card
            nothing, global_fallback=false  -> no card (fallback.status=disabled)
            nothing, global_fallback=true   -> global search for top_k results
                hits                    -> their cards (fallback.status=hit)
                nothing, or it failed   -> no card (fallback.status says which)

The region search (app.api.services.region_search) searches every box with the request's scene,
top_k and score_threshold, and keeps the best top_k hits of all boxes. With score_threshold=0 the
whole image always has hits when the library has anything, so the region search is not started.

Every channel answers with the same result card (app.schemas.search_hit), so items[] has one shape
whatever the channel. What depends on the channel is on the card itself, not on the envelope: its
source (self / recipe / global) comes first, and a self-built card's details say whether they
carry the heating information (outputs_heating, from the scene). A region hit is a self-built card
whose details.region names the box that found it. The recipe detection gives one card: the dish
names as its tags, the other card fields empty.

A failure of the whole-image search (unknown scene, an image that cannot be decoded, the retrieval
stack down) fails the request, as on POST /api/search. A failure of the region search, the recipe
detection or the fallback does not. A failed region search or recipe detection counts as having
found nothing: it is logged and named in the request's timing line. For the fallback,
fallback.status=failed carries the reason, so a caller can tell "nothing found" from "the fallback
could not run".

The query image is read once (upload or download) and given to every channel. The recipe service
gets the image's link when the request came with one (it downloads the image itself), else the bytes.
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

RECIPE_STEP = "菜谱识别"  # the recipe detection's step in the request's timing line

# Why items is empty, by fallback.status
_MESSAGES = {
    "disabled": "自建库没有达到阈值的结果，未开启 Global 兜底",
    "empty": "自建库和 Global 都没有找到结果",
    "failed": "自建库没有达到阈值的结果，Global 兜底失败",
}
# The same when the recipe detection ran too; {recipe} is what it came to
_MESSAGES_WITH_RECIPE = {
    "disabled": "自建库没有达到阈值的结果，{recipe}，未开启 Global 兜底",
    "empty": "自建库没有达到阈值的结果，{recipe}，Global 也没有找到结果",
    "failed": "自建库没有达到阈值的结果，{recipe}，Global 兜底失败",
}


@dataclass(frozen=True, slots=True)
class _Recipe:
    """What one run of the recipe detection came to."""

    names: list[str]  # the dish names; [] when none was recognised, or the call failed
    seconds: float
    error: err.AppError | None = None


def _log(level: int, message: str, trace_id: str) -> None:
    """One line through this service's logger (the trace id at its end) and through show_text."""
    logger.log(level, "%s | trace_id=%s", message, trace_id)
    show_text(message, trace_id=trace_id, logger_level=level)


def _recipe_settings(runtime: ApplicationState, trace_id: str) -> RecipeDetectSettings | None:
    """RECIPE_DETECT_CONFIG as it is now; None when the step is off, or the section cannot be used."""
    try:
        settings = RecipeDetectSettings.from_config(runtime.config)
    except ValueError as exc:
        _log(logging.WARNING, f"菜谱识别已跳过：RECIPE_DETECT_CONFIG 配置有误 | {exc}", trace_id)
        return None
    return settings if settings.enabled else None


def _region_settings(
    runtime: ApplicationState, score_threshold: float | None, trace_id: str
) -> AutoSearchRegionSettings | None:
    """AUTO_SEARCH_REGION_CONFIG as it is now; None when the step is off, the section cannot be
    used, or there is no threshold (the whole image then always answers, when anything can)."""
    if score_threshold == 0:
        return None
    try:
        settings = AutoSearchRegionSettings.from_config(runtime.config)
    except ValueError as exc:
        _log(logging.WARNING, f"{REGION_STEP}已跳过：AUTO_SEARCH_REGION_CONFIG 配置有误 | {exc}", trace_id)
        return None
    return settings if settings.enabled else None


def _log_regions(outcome: RegionOutcome, trace_id: str) -> None:
    """What the region search came to, once its answer is used."""
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
    """Run the recipe detection and log how long it took and what it found. A failure is part of
    the answer, not an exception: nothing this step does may fail the search."""
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


# Region searches dropped while still running: a task needs a reference until it ends
_LEFT_RUNNING: set[asyncio.Task[Any]] = set()


def _forget(task: asyncio.Task[Any]) -> None:
    _LEFT_RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved, so that it is not reported as never retrieved


async def _abandon(detection: asyncio.Task[_Recipe] | None, regions: asyncio.Task[RegionOutcome] | None) -> None:
    """Stop the steps whose answer nobody will read (the whole image answered, or the request
    failed). The recipe detection is a call on the event loop: it ends at once. The region search
    may be inside a worker thread (GroundingDINO, the encoder), which cannot be interrupted, and a
    cancellation may wait for that thread: it is cancelled and not waited for."""
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
    """Self-built hits (the whole image's or the regions') as cards of this answer: each says in
    its details whether they carry the heating information (the scene's outputs_heating, the same
    for every hit)."""
    return [
        {"source": "self", **hit, "details": {**hit["details"], "outputs_heating": bool(heating)}}
        for hit in hits
    ]


def _recipe_card(names: list[str]) -> dict[str, Any]:
    """The recipe detection's result card: the dish names as its tags. It stands for no stored
    image, so the other card fields keep their empty values."""
    return {
        "source": "recipe",
        "tags": tags_text(names),
        "description": "",
        "original_url": None,
        "similarity": None,
        "details": {},
    }


def _global_cards(result: Any, top_k: int) -> list[dict[str, Any]]:
    """The result cards of a global answer: its list (at most top_k), or none for the upstream's
    "found nothing" envelope (status=upstream_empty_response)."""
    if not isinstance(result, list):
        return []
    return [{"source": "global", **card} for card in result if isinstance(card, dict)][:top_k]


def _empty_message(status: str, recipe: _Recipe | None) -> str:
    """Why items is empty: status is fallback.status; recipe the recipe detection's run, if it ran."""
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
    """image_url: the query image's link when the request came with one (the URL route); the
    recipe service is then sent the link instead of the bytes.

    timer: the route's timer. Every channel records its steps on it (embedding推理 / vector搜索 /
    后处理 for the whole image; then, when it found nothing, 目标检测 / 区域搜索 for the regions,
    菜谱识别, and global搜索 / 后处理 for the fallback); the route writes the request's timing line.
    The region search and 菜谱识别 run beside the whole-image steps, so the steps can add up to
    more than the total.
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
        if not local["items"]:  # the whole image found nothing: the other two are the answer
            found = await regions if regions is not None else None
            recipe = await detection if detection is not None else None
    except BaseException:
        await _abandon(detection, regions)
        raise

    body: dict[str, Any] = {"ok": True, "trace_id": trace_id, "scene": local["scene"]}
    heating = bool(local.get("outputs_heating"))
    if local["items"]:
        await _abandon(detection, regions)  # answered without them
        body.update(items=_self_cards(local["items"], heating), fallback={"status": "not_needed"})
        return body

    # recorded here, not when they ended: after the whole-image steps, whichever ended first
    if found is not None:
        _log_regions(found, trace_id)
        for step, seconds, failed in found.steps:
            timer.record(step, seconds, failed=failed)
    if recipe is not None:
        timer.record(RECIPE_STEP, recipe.seconds, failed=recipe.error is not None)

    cards = _self_cards(found.hits if found is not None else [], heating)
    if recipe is not None and recipe.names:
        cards.append(_recipe_card(recipe.names))
    if cards:  # region hits, the dish names, or both
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
