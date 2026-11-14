"""Business search (POST /api/search/auto/file, /api/search/auto/url): the self-built library
first, the global search as an optional fallback, one answer.

    query image -> self-built search (scene, top_k, score_threshold)
        hits                            -> source=self
        nothing, global_fallback=false  -> source=empty
        nothing, global_fallback=true   -> global search for top_k results
            hits                        -> source=global
            nothing, or it failed       -> source=empty (fallback.status says which)

Both channels answer with the same result card (app.schemas.search_hit), so items[] has one shape
whatever the source. A failure of the self-built search (unknown scene, an image that cannot be
decoded, the retrieval stack down) fails the request, as on POST /api/search. A failure of the
fallback does not: the self-built answer (nothing found) stands, and fallback.status=failed carries
the reason, so a caller can tell "nothing found" from "the fallback could not run".

The query image is read once (upload or download) and given to both channels.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.api.services.predict import run_predict
from app.bootstrap.state import ApplicationState
from app.core import errors as err

logger = logging.getLogger("svc.auto_search")

_MESSAGES = {
    "disabled": "自建库没有达到阈值的结果，未开启 Global 兜底",
    "empty": "自建库和 Global 都没有找到结果",
    "failed": "自建库没有达到阈值的结果，Global 兜底失败",
}


def _global_cards(result: Any, top_k: int) -> list[dict[str, Any]]:
    """The result cards of a global answer: its list (at most top_k), or none for the upstream's
    "found nothing" envelope (status=upstream_empty_response)."""
    if not isinstance(result, list):
        return []
    return [card for card in result if isinstance(card, dict)][:top_k]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 1)


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
) -> dict[str, Any]:
    """The response body ({ok, trace_id, source, scene, outputs_heating, items, fallback, message?}).

    input_label names the query image in the logs, e.g. "file:a.jpg" or "url:https://...".
    """
    started = time.perf_counter()
    stack = require_retrieval(runtime)
    local = await run_in_threadpool(
        stack.search_uc.execute,
        image_bytes=image_bytes,
        scene=scene,
        top_k=top_k,
        score_threshold=score_threshold,
    )
    self_ms = _elapsed_ms(started)
    body: dict[str, Any] = {
        "ok": True,
        "trace_id": trace_id,
        "scene": local["scene"],
        "outputs_heating": bool(local.get("outputs_heating")),
    }

    global_ms: float | None = None
    if local["items"]:
        body.update(source="self", items=local["items"], fallback={"status": "not_needed"})
    elif not global_fallback:
        body.update(source="empty", items=[], fallback={"status": "disabled"}, message=_MESSAGES["disabled"])
    else:
        global_started = time.perf_counter()
        try:
            answer = await run_predict(
                runtime=runtime,
                img_bytes=image_bytes,
                source=input_label,
                machine_info=machine_info,
                trace_id=trace_id,
                save_res=False,
                search_num=top_k,
            )
        except err.AppError as exc:
            # Not the request's failure: nothing was found in the library either way
            logger.warning(
                "auto search: global fallback failed | trace_id=%s | %s: %s", trace_id, exc.code, exc.detail
            )
            body.update(
                source="empty",
                items=[],
                fallback={"status": "failed", "error": exc.code, "detail": err.client_detail(exc)},
                message=_MESSAGES["failed"],
            )
        else:
            cards = _global_cards(answer.get("result"), top_k)
            if cards:
                # Global results carry no heating information, whatever the scene
                body.update(source="global", outputs_heating=False, items=cards, fallback={"status": "hit"})
            else:
                body.update(source="empty", items=[], fallback={"status": "empty"}, message=_MESSAGES["empty"])
        finally:
            global_ms = _elapsed_ms(global_started)

    logger.info(
        "auto search | trace_id=%s | input=%s | scene=%s | top_k=%s | source=%s | fallback=%s | items=%d | "
        "self_ms=%s | global_ms=%s | total_ms=%s | machine_info=%s",
        trace_id, input_label, body["scene"], top_k, body["source"], body["fallback"]["status"],
        len(body["items"]), self_ms, global_ms, _elapsed_ms(started), machine_info,
    )
    return body
