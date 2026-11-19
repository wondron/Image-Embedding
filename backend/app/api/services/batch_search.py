"""Batch search (POST /api/search/batch/files, /api/search/batch/urls): several query images
against the self-built library in one request.

    shared parameters (scene, top_k, score_threshold)   checked once; a bad one fails the request
    every query image, SEARCH_CONCURRENCY at a time:
        read it (upload) or download it (URL)
        self-built search, as POST /api/search          -> results[i] = {ok: true, source, items}
        that image failed                               -> results[i] = {ok: false, error, detail}

One image that cannot be read, downloaded or decoded does not take the others down: its entry says
why and the request still answers 200. The request itself fails when nothing can be searched at
all: the retrieval stack is not ready (NOT_READY), a shared parameter is bad, or every image failed
with the same service failure (the vector store or the database is down), which is then raised
instead of being repeated in every entry.

search_images is the part other flows call too (the crops of the objects detected in one picture,
say: QueryImage.from_bytes for an image already in memory). It takes shared parameters the caller
has checked and answers the entries of results[], in the order the images were given whichever
finishes first.

An image is read only once it has one of the SEARCH_CONCURRENCY slots, so a request holds at most
that many images in memory; its search runs in the thread pool (the encoder and the Qdrant and
PostgreSQL clients are shared between threads). What overlaps is the download, the decoding and
resizing, and the Qdrant / PostgreSQL round trips: on the GPU the encoder's session runs one
inference after another, so small images such as crops gain little (the current model takes a
batch of one only). Each image records its steps on a timer of its own, added to the request's
timer in request order once all are done: the timing line gives every step's total over the batch,
in a fixed order, and as the images overlap the steps can add up to more than the total. The
global search is never called.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.observability.log_helper import log_timing
from app.schemas.batch_search import BATCH_MAX_IMAGES
from app.utils.timer import timer_tool

if TYPE_CHECKING:
    from app.bootstrap.retrieval_stack import RetrievalStack

logger = logging.getLogger("svc.batch_search")

# Images of one request read and searched at the same time. The web client sends 5 per request
# (frontend LOCAL_BATCH_SIZE), so each of its requests runs in one go. Bounded because every
# search holds a worker of the thread pool all requests share (40) while it runs, and its image
# in memory
SEARCH_CONCURRENCY = 5

# Failures of the service rather than of one image, besides the codes registered as internal
_SERVICE_CODES = frozenset({"NOT_READY", "VECTOR_DIM_MISMATCH", "EMBEDDING_MODEL_MISMATCH"})


@dataclass(frozen=True)
class QueryImage:
    """One query image of a batch: how results[] names it, and how to get its bytes."""

    label: str
    read: Callable[[], Awaitable[bytes]]
    download: bool = False  # read() downloads a URL: timed as 图像下载 (reading an upload is not)

    @classmethod
    def from_bytes(cls, label: str, image_bytes: bytes) -> QueryImage:
        """An image already in memory, encoded as an upload would be (a crop, say)."""

        async def read() -> bytes:
            return image_bytes

        return cls(label=label, read=read)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 1)


def _is_service_failure(exc: err.AppError) -> bool:
    return err.is_internal(exc) or exc.code in _SERVICE_CODES


def _raise_if_service_down(failures: Sequence[err.AppError], total: int) -> None:
    """Every image failed the same way and it is the service's failure: report that error."""
    if not failures or len(failures) < total:
        return
    first = failures[0]
    if _is_service_failure(first) and all(exc.code == first.code for exc in failures):
        raise first


async def _search_one(
    stack: RetrievalStack,
    index: int,
    image: QueryImage,
    *,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
    timer: timer_tool,
) -> tuple[dict[str, Any], err.AppError | None]:
    """This image's entry of results[], and why it could not be searched (None when it was)."""
    started = time.perf_counter()
    entry: dict[str, Any] = {"index": index, "input": image.label}
    try:
        if image.download:
            with timer.step("图像下载"):
                image_bytes = await image.read()
        else:
            image_bytes = await image.read()
        found = await run_in_threadpool(
            stack.search_uc.execute,
            image_bytes=image_bytes,
            scene=scene,
            top_k=top_k,
            score_threshold=score_threshold,
            trace_id=trace_id,
            timer=timer,
        )
    except err.AppError as exc:
        failure = exc
    except Exception as exc:  # one image must not take the others down
        logger.exception("batch search: image %d (%s) failed | trace_id=%s", index, image.label, trace_id)
        failure = err.internal_error(f"{type(exc).__name__}: {exc}")
    else:
        failure = None
        entry.update(ok=True, source=found["source"], items=found["items"])
        if found.get("message"):
            entry["message"] = found["message"]
    if failure is not None:
        logger.warning(
            "batch search: image %d (%s) not searched | trace_id=%s | %s: %s",
            index, image.label, trace_id, failure.code, failure.detail,
        )
        entry.update(ok=False, error=failure.code, detail=err.client_detail(failure))
    entry["elapsed_ms"] = _elapsed_ms(started)
    return entry, failure


async def search_images(
    stack: RetrievalStack,
    images: Iterable[QueryImage],
    *,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
    timer: timer_tool | None = None,
    concurrency: int = SEARCH_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Every image searched in the self-built library as POST /api/search, at most `concurrency`
    at a time. One entry per image, in the order given (the entries of results[]):

        {index, input, ok: true, source, items[, message], elapsed_ms}
        {index, input, ok: false, error, detail, elapsed_ms}

    The caller checks what the images share first (stack.search_uc.check), so that a bad scene,
    top_k or threshold fails its request before any image is read, and passes check's scene. One
    image's failure is its entry; this raises only when every image failed with the same service
    failure. No image, no entry.

    timer: the caller's timer. Every image's steps (图像下载 / embedding推理 / vector搜索 / 后处理)
    are added to it once all are done, image after image in the order given.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    images = list(images)  # walked twice below: the second walk of a generator would find nothing
    slots = asyncio.Semaphore(concurrency)
    timers = [timer_tool() for _ in images]

    async def search(index: int, image: QueryImage) -> tuple[dict[str, Any], err.AppError | None]:
        async with slots:
            return await _search_one(
                stack, index, image,
                scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id,
                timer=timers[index],
            )

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(search(index, image)) for index, image in enumerate(images)]
    outcomes = [task.result() for task in tasks]

    if timer is not None:
        for image_timer in timers:
            timer.merge(image_timer)
    _raise_if_service_down([failure for _, failure in outcomes if failure is not None], len(outcomes))
    return [entry for entry, _ in outcomes]


async def run_batch_search(
    runtime: ApplicationState,
    *,
    images: Sequence[QueryImage],
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
    input_type: str,
) -> dict[str, Any]:
    """The response body ({ok, trace_id, scene, outputs_heating, top_k, score_threshold, total,
    succeeded, matched, failed, results}). input_type names the route in the log (files / urls).

    One timer for the request, which every image's steps are added to: the timing line gives every
    step's total over the batch."""
    timer = timer_tool()
    stack = require_retrieval(runtime)
    if not images:
        raise err.validation_error("at least one query image is required")
    if len(images) > BATCH_MAX_IMAGES:
        raise err.validation_error(f"too many query images ({len(images)} > {BATCH_MAX_IMAGES})")

    # Before any image is read: a bad scene, top_k or threshold is the request's failure
    shared = await run_in_threadpool(
        stack.search_uc.check, scene=scene, top_k=top_k, score_threshold=score_threshold
    )
    results = await search_images(
        stack, images,
        scene=shared["scene"], top_k=top_k, score_threshold=score_threshold, trace_id=trace_id, timer=timer,
    )

    succeeded = sum(1 for entry in results if entry["ok"])
    matched = sum(1 for entry in results if entry["ok"] and entry["items"])
    failed = len(results) - succeeded
    log_timing(
        logger, f"批量搜图({input_type})", timer,
        trace_id=trace_id,
        level=logging.WARNING if failed else logging.INFO,
        extra=(
            f"参数=scene={shared['scene']}, top_k={top_k}, score_threshold={shared['score_threshold']}, "
            f"images={len(results)}, succeeded={succeeded}, matched={matched}, failed={failed}"
        ),
    )
    return {
        "ok": True,
        "trace_id": trace_id,
        "scene": shared["scene"],
        "outputs_heating": bool(shared["outputs_heating"]),
        "top_k": top_k,
        "score_threshold": shared["score_threshold"],
        "total": len(results),
        "succeeded": succeeded,
        "matched": matched,
        "failed": failed,
        "results": results,
    }
