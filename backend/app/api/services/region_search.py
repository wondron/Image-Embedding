"""Region search, the business search's second try (app.api.services.auto_search): the boxes
GroundingDINO finds in the query image, each searched in the self-built library as a query image
of its own, merged into one list of hits.

    query image -> decoded as the encoder decodes it (OpenCV, BGR)
                -> GroundingDINO with AUTO_SEARCH_REGION_CONFIG's caption, box_threshold, text_threshold
                -> the boxes in pixels, clipped to the image; under MIN_REGION_SIDE on a side left out;
                   highest detection score first, at most max_regions
                -> every box cropped (JPEG) and searched as POST /api/search/batch does
                   (search_images): the request's scene, top_k and score_threshold for each
                -> the hits of all boxes: one per stored image (its highest similarity), highest
                   first, at most top_k; details.region says which box found it

It never fails the business search: run_region_search answers what it came to (the hits, or why
there are none), and the caller logs that and records its steps on the request's timer.
GroundingDINO, the decoding and the cropping run in a worker thread, as every crop's search does.
A thread cannot be interrupted: when the region search is cancelled (the whole image answered),
the one it is in runs to its end and its result is discarded; no further crop is searched.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

import cv2
import numpy as np
from starlette.concurrency import run_in_threadpool

from app.api.services.batch_search import QueryImage, search_images
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.config.settings import AutoSearchRegionSettings

if TYPE_CHECKING:
    from app.bootstrap.retrieval_stack import RetrievalStack

DETECT_STEP = "目标检测"  # the steps on the request's timing line
SEARCH_STEP = "区域搜索"

MIN_REGION_SIDE = 16  # pixels: a smaller box says nothing the encoder can use
JPEG_QUALITY = 95


@dataclass(frozen=True, slots=True)
class Region:
    """One box found in the query image."""

    label: str  # the caption words GroundingDINO matched
    score: float  # its detection score
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels of the query image

    def wire(self) -> dict[str, Any]:
        """details.region of the hits it found."""
        return {"label": self.label, "score": round(self.score, 4), "box": list(self.box)}


@dataclass(frozen=True, slots=True)
class RegionOutcome:
    """What one region search came to."""

    hits: list[dict[str, Any]] = field(default_factory=list)  # search hits (no source), at most top_k
    regions: int = 0  # the boxes searched
    failed: int = 0  # of them, those that could not be searched
    steps: tuple[tuple[str, float, bool], ...] = ()  # (step, seconds, failed) for the request's timer
    error: str | None = None  # why it stopped part way (a step failed)
    skipped: str | None = None  # why it did not run at all


def decode(image_bytes: bytes) -> np.ndarray:
    """The query image as the encoder reads it: a BGR array."""
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("the query image cannot be decoded")
    return image


def regions_of(
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    labels: Sequence[str],
    *,
    width: int,
    height: int,
    limit: int,
) -> list[Region]:
    """GroundingDINO's boxes (normalized cx, cy, w, h) in pixels of a width x height image,
    clipped to it; the small ones left out; highest score first, at most limit."""
    found = []
    for (cx, cy, w, h), score, label in zip(boxes, scores, labels):
        x1 = max(0, math.floor((cx - w / 2) * width))
        y1 = max(0, math.floor((cy - h / 2) * height))
        x2 = min(width, math.ceil((cx + w / 2) * width))
        y2 = min(height, math.ceil((cy + h / 2) * height))
        if x2 - x1 < MIN_REGION_SIDE or y2 - y1 < MIN_REGION_SIDE:
            continue
        found.append(Region(str(label).strip(), float(score), (x1, y1, x2, y2)))
    found.sort(key=lambda region: region.score, reverse=True)  # stable: equal scores keep their order
    return found[:limit]


def crop(image: np.ndarray, region: Region) -> bytes:
    """The region of a BGR image, encoded as an upload would be."""
    x1, y1, x2, y2 = region.box
    ok, encoded = cv2.imencode(".jpg", image[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError(f"region {region.box} cannot be encoded")
    return encoded.tobytes()


def merge(regions: Sequence[Region], entries: Sequence[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """The hits of every box (results[] entries of search_images, in the boxes' order): one per
    stored image, the one with the highest similarity, highest first, at most top_k. Each hit
    gets its box in details.region."""
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


def _detect_and_crop(
    detector: Any, image_bytes: bytes, settings: AutoSearchRegionSettings
) -> tuple[list[Region], list[bytes]]:
    """In a worker thread: the boxes found in the query image, and their crops."""
    image = decode(image_bytes)
    boxes, scores, labels = detector.detect(
        image,
        text_range=settings.caption,
        box_threshold=settings.box_threshold,
        text_threshold=settings.text_threshold,
    )
    height, width = image.shape[:2]
    regions = regions_of(boxes, scores, labels, width=width, height=height, limit=settings.max_regions)
    return regions, [crop(image, region) for region in regions]


def _reason(exc: Exception) -> str:
    return f"{exc.code}: {exc.detail}" if isinstance(exc, err.AppError) else f"{type(exc).__name__}: {exc}"


async def run_region_search(
    runtime: ApplicationState,
    stack: RetrievalStack,
    settings: AutoSearchRegionSettings,
    *,
    image_bytes: bytes,
    scene: str | None,
    top_k: int,
    score_threshold: float | None,
    trace_id: str,
) -> RegionOutcome:
    """Detect, crop, search, merge. A failure is part of the answer, not an exception: nothing
    this step does may fail the business search."""
    try:
        detector = runtime.require_detector()
    except err.NotReadyError as exc:
        return RegionOutcome(skipped=f"GroundingDINO 未就绪：{exc.detail}")

    started = time.perf_counter()
    try:
        regions, crops = await run_in_threadpool(_detect_and_crop, detector, image_bytes, settings)
    except Exception as exc:
        return RegionOutcome(steps=((DETECT_STEP, time.perf_counter() - started, True),), error=_reason(exc))
    detected = (DETECT_STEP, time.perf_counter() - started, False)
    if not regions:
        return RegionOutcome(steps=(detected,))

    images = [QueryImage.from_bytes(f"region:{i}:{r.label}", data) for i, (r, data) in enumerate(zip(regions, crops))]
    started = time.perf_counter()
    try:
        entries = await search_images(
            stack, images, scene=scene, top_k=top_k, score_threshold=score_threshold, trace_id=trace_id
        )
    except Exception as exc:  # every box failed the same way, and it is the service's failure
        searched = (SEARCH_STEP, time.perf_counter() - started, True)
        return RegionOutcome(regions=len(regions), failed=len(regions), steps=(detected, searched), error=_reason(exc))
    failed = sum(1 for entry in entries if not entry["ok"])
    searched = (SEARCH_STEP, time.perf_counter() - started, failed == len(entries))
    return RegionOutcome(
        hits=merge(regions, entries, top_k), regions=len(regions), failed=failed, steps=(detected, searched)
    )
