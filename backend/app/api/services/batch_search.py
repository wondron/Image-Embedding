"""多图搜索（POST /api/search/batch/files、/api/search/batch/urls）：一次请求用多张
查询图搜自建库。

    共享参数（scene、top_k、score_threshold）    只检查一次；有一个不合法就让请求失败
    每张查询图，同时最多 SEARCH_CONCURRENCY 张：
        读取（上传）或下载（URL）
        搜自建库，同 POST /api/search                    -> results[i] = {ok: true, source, items}
        这张图失败了                                     -> results[i] = {ok: false, error, detail}

一张图读不了、下载不了或解码不了，不会拖垮其他图：它的条目说明原因，请求仍然返回 200。
完全无法搜索时请求本身才失败：检索服务未就绪（NOT_READY）、共享参数不合法，或者
每张图都因同一个服务故障失败（向量库或数据库挂了），这时抛出那个错误，而不是在每个
条目里重复一遍。

search_images 是其他流程也会调用的部分（比如一张图里检测到的物体的裁剪图：已经在内存里的
图片用 QueryImage.from_bytes）。它接收调用方已经检查过的共享参数，返回 results[] 的各个
条目，顺序和传入图片的顺序一致，不管谁先完成。

一张图拿到 SEARCH_CONCURRENCY 个名额中的一个之后才会读取，所以一个请求同时最多在内存里
放这么多张图；搜索在线程池里执行（编码器以及 Qdrant 和 PostgreSQL 客户端由各线程共用）。
能重叠的是下载、解码和缩放，以及 Qdrant / PostgreSQL 的往返：在 GPU 上编码器的 session
一次只做一次推理，所以裁剪图这类小图收益不大（当前模型只支持 batch 为 1）。每张图把步骤
记在自己的计时器上，全部完成后按请求顺序加到请求的计时器里：耗时行给出每一步在整批上的
合计，顺序固定；因为各张图同时处理，各步骤相加可能超过总耗时。不会调用 Global 搜图。
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

# 一个请求里同时读取和搜索的图片数。网页端每次请求发 5 张（前端 LOCAL_BATCH_SIZE），
# 所以它的每个请求一轮就能处理完。要设上限，是因为每次搜索在运行时都占着所有请求共用的
# 线程池（40 个）里的一个线程，以及内存里的一张图
SEARCH_CONCURRENCY = 5

# 除了登记为内部错误的错误码之外，属于服务故障而不是单张图问题的错误码
_SERVICE_CODES = frozenset({"NOT_READY", "VECTOR_DIM_MISMATCH", "EMBEDDING_MODEL_MISMATCH"})


@dataclass(frozen=True)
class QueryImage:
    """批量里的一张查询图：results[] 怎么称呼它，以及怎么拿到它的内容。"""

    label: str
    read: Callable[[], Awaitable[bytes]]
    download: bool = False  # read() downloads a URL: timed as 图像下载 (reading an upload is not)

    @classmethod
    def from_bytes(cls, label: str, image_bytes: bytes) -> QueryImage:
        """已经在内存里的图片，编码方式和上传的一样（比如一张裁剪图）。"""

        async def read() -> bytes:
            return image_bytes

        return cls(label=label, read=read)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 1)


def _is_service_failure(exc: err.AppError) -> bool:
    return err.is_internal(exc) or exc.code in _SERVICE_CODES


def _raise_if_service_down(failures: Sequence[err.AppError], total: int) -> None:
    """每张图都以同样的方式失败，而且是服务的故障：报告这个错误。"""
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
    """这张图在 results[] 里的条目，以及它无法搜索的原因（能搜索时为 None）。"""
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
    except Exception as exc:  # 一张图不能拖垮其他图
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
    """每张图都像 POST /api/search 一样搜自建库，同时最多 `concurrency` 张。每张图一个条目，
    顺序和传入的一致（就是 results[] 的条目）：

        {index, input, ok: true, source, items[, message], elapsed_ms}
        {index, input, ok: false, error, detail, elapsed_ms}

    调用方先检查这些图共享的参数（stack.search_uc.check），这样场景、top_k 或阈值不合法时，
    在读任何图片之前请求就失败了，并传入 check 返回的 scene。单张图的失败就是它的条目；
    只有每张图都因同一个服务故障失败时才抛出。没有图片就没有条目。

    timer：调用方的计时器。全部完成后，每张图的步骤（图像下载 / embedding推理 / vector搜索 /
    后处理）按传入顺序一张接一张加到它上面。
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    images = list(images)  # 下面要遍历两次：生成器第二次遍历什么也拿不到
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
    """响应体（{ok, trace_id, scene, outputs_heating, top_k, score_threshold, total,
    succeeded, matched, failed, results}）。input_type 在日志里标明路由（files / urls）。

    整个请求一个计时器，每张图的步骤都加到上面：耗时行给出每一步在整批上的合计。
    """
    timer = timer_tool()
    stack = require_retrieval(runtime)
    if not images:
        raise err.validation_error("at least one query image is required")
    if len(images) > BATCH_MAX_IMAGES:
        raise err.validation_error(f"too many query images ({len(images)} > {BATCH_MAX_IMAGES})")

    # 在读取任何图片之前：场景、top_k 或阈值不合法就是请求的失败
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
