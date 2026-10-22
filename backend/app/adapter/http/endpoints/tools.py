"""Low-level model tools exposed for diagnostics and integration."""

from __future__ import annotations

import base64
import logging
import time
from io import BytesIO
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel

from app.bootstrap.state import get_application_state


logger = logging.getLogger("svc.tools")
router = APIRouter(prefix="/api/tools", tags=["tools"])


class SegmentResponse(BaseModel):
    ok: bool
    filename: str
    width: int
    height: int
    latency_ms: float
    mask_png_base64: str
    visual_png_base64: str | None = None


class EmbeddingResponse(BaseModel):
    ok: bool
    filename: str
    embedding_dim: int
    latency_ms: float
    embedding: list[float]


def _encode_png_base64(image: Any, *, bgr: bool = False) -> str:
    if image is None:
        raise ValueError("image is empty")

    ndim = getattr(image, "ndim", 0)
    shape = getattr(image, "shape", ())
    if ndim == 2:
        height, width = shape
        png_image = Image.frombytes("L", (width, height), image.tobytes())
    elif ndim == 3 and shape[2] == 3:
        height, width, _ = shape
        raw_mode = "BGR" if bgr else "RGB"
        png_image = Image.frombytes(
            "RGB",
            (width, height),
            image.tobytes(),
            "raw",
            raw_mode,
        )
    else:
        raise ValueError(f"unsupported image shape for PNG encoding: {shape}")

    output = BytesIO()
    png_image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


@router.post("/segment", response_model=SegmentResponse)
async def segment(
    request: Request,
    file: UploadFile = File(..., description="待分割的图片文件"),
    output_vis: bool = Query(default=False, description="是否返回 mask 叠加可视化图"),
) -> SegmentResponse:
    runtime = get_application_state(request)

    content_type = file.content_type
    if content_type and content_type not in runtime.settings.allowed_content_types:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported image content type: {content_type}",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(image_bytes) > runtime.max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (> {runtime.max_image_bytes} bytes)",
        )

    try:
        service = runtime.require_food_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"service unavailable: {exc}") from exc

    segmentor = getattr(service, "segmentor", None)
    if segmentor is None:
        raise HTTPException(status_code=503, detail="segmentor is not initialized")

    started = time.perf_counter()
    try:
        # mask, visual, original = segmentor.detect(image_bytes, output_vis=output_vis)
        original = None
        if original is None or getattr(original, "ndim", 0) < 2:
            raise ValueError("segmentor returned an invalid original image")

        height, width = original.shape[:2]
        response = SegmentResponse(
            ok=True,
            filename=file.filename or "upload",
            width=int(width),
            height=int(height),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            mask_png_base64=_encode_png_base64(mask),
            visual_png_base64=(
                _encode_png_base64(visual, bgr=True) if output_vis else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("segment tool failed | filename=%s | err=%s", file.filename, exc)
        raise HTTPException(status_code=500, detail=f"segment failed: {exc}") from exc

    logger.info(
        "segment tool ok | filename=%s | bytes=%d | size=%dx%d | output_vis=%s | latency_ms=%.2f",
        response.filename,
        len(image_bytes),
        response.width,
        response.height,
        output_vis,
        response.latency_ms,
    )
    return response


@router.post("/embedding", response_model=EmbeddingResponse)
async def embedding(
    request: Request,
    file: UploadFile = File(..., description="待生成全局 embedding 的图片文件"),
) -> EmbeddingResponse:
    runtime = get_application_state(request)

    content_type = file.content_type
    if content_type and content_type not in runtime.settings.allowed_content_types:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported image content type: {content_type}",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(image_bytes) > runtime.max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (> {runtime.max_image_bytes} bytes)",
        )

    try:
        service = runtime.require_food_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"service unavailable: {exc}") from exc

    embedding_model = getattr(service, "embedding", None)
    if embedding_model is None:
        raise HTTPException(status_code=503, detail="embedding model is not initialized")

    started = time.perf_counter()
    try:
        vector = embedding_model.detect(image_bytes)
        if getattr(vector, "ndim", 0) != 1:
            raise ValueError(
                f"embedding model returned an invalid shape: {getattr(vector, 'shape', None)}"
            )

        response = EmbeddingResponse(
            ok=True,
            filename=file.filename or "upload",
            embedding_dim=int(vector.shape[0]),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            embedding=vector.tolist(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("embedding tool failed | filename=%s | err=%s", file.filename, exc)
        raise HTTPException(status_code=500, detail=f"embedding failed: {exc}") from exc

    logger.info(
        "embedding tool ok | filename=%s | bytes=%d | dim=%d | latency_ms=%.2f",
        response.filename,
        len(image_bytes),
        response.embedding_dim,
        response.latency_ms,
    )
    return response
