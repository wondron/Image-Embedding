"""Batch search (POST /api/search/batch/files, /api/search/batch/urls): several query images
against the self-built library in one request.

    shared parameters (scene, top_k, score_threshold)   checked once; a bad one fails the request
    for each query image, in order:
        read it (upload) or download it (URL)
        self-built search, as POST /api/search          -> results[i] = {ok: true, source, items}
        that image failed                               -> results[i] = {ok: false, error, detail}

One image that cannot be read, downloaded or decoded does not take the others down: its entry says
why and the request still answers 200. The request itself fails when nothing can be searched at
all: the retrieval stack is not ready (NOT_READY), a shared parameter is bad, or every image failed
with the same service failure (the vector store or the database is down), which is then raised
instead of being repeated in every entry.

Images are handled one after another: an upload is read from its spooled file only when its turn
comes, so the request holds one image in memory at a time. The global search is never called.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.observability.log_helper import log_timing
from app.schemas.batch_search import BATCH_MAX_IMAGES
from app.utils.timer import timer_tool

logger = logging.getLogger("svc.batch_search")

# Failures of the service rather than of one image, besides the codes registered as internal
_SERVICE_CODES = frozenset({"NOT_READY", "VECTOR_DIM_MISMATCH", "EMBEDDING_MODEL_MISMATCH"})


@dataclass(frozen=True)
class QueryImage:
    """One query image of a batch: how results[] names it, and how to get its bytes."""

    label: str
    read: Callable[[], Awaitable[bytes]]
    download: bool = False  # read() downloads a URL: timed as 图像下载 (reading an upload is not)


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

    One timer for the request: each image adds its steps to it, so the timing line gives every
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

    results: list[dict[str, Any]] = []
    failures: list[err.AppError] = []
    for index, image in enumerate(images):
        image_started = time.perf_counter()
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
                scene=shared["scene"],
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
            failures.append(failure)
            logger.warning(
                "batch search: image %d (%s) not searched | trace_id=%s | %s: %s",
                index, image.label, trace_id, failure.code, failure.detail,
            )
            entry.update(ok=False, error=failure.code, detail=err.client_detail(failure))
        entry["elapsed_ms"] = _elapsed_ms(image_started)
        results.append(entry)

    _raise_if_service_down(failures, len(results))

    succeeded = sum(1 for entry in results if entry["ok"])
    matched = sum(1 for entry in results if entry["ok"] and entry["items"])
    log_timing(
        logger, f"批量搜图({input_type})", timer,
        trace_id=trace_id,
        level=logging.WARNING if failures else logging.INFO,
        extra=(
            f"参数=scene={shared['scene']}, top_k={top_k}, score_threshold={shared['score_threshold']}, "
            f"images={len(results)}, succeeded={succeeded}, matched={matched}, failed={len(results) - succeeded}"
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
        "failed": len(results) - succeeded,
        "results": results,
    }
