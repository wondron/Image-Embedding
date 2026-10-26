"""Processing and validation for diagnostic model operations."""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, UploadFile

from app.adapter.http.dependencies import require_food_service
from app.adapter.http.schemas.tools import EmbeddingResponse, SegmentResponse
from app.adapter.http.services.image_io import encode_png_base64, read_file_bytes
from app.bootstrap.state import ApplicationState


logger = logging.getLogger("svc.tools")


async def _read_tool_image(file: UploadFile, runtime: ApplicationState) -> bytes:
    return await read_file_bytes(
        file,
        runtime.max_image_bytes,
        allowed_content_types=runtime.settings.allowed_content_types,
        empty_detail="uploaded file is empty",
        too_large_detail=f"file too large (> {runtime.max_image_bytes} bytes)",
    )


async def segment(
    runtime: ApplicationState,
    file: UploadFile,
    *,
    output_vis: bool = False,
) -> SegmentResponse:
    image_bytes = await _read_tool_image(file, runtime)
    service = require_food_service(runtime)

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
            mask_png_base64=encode_png_base64(mask),
            visual_png_base64=(
                encode_png_base64(visual, bgr=True) if output_vis else None
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


async def embedding(runtime: ApplicationState, file: UploadFile) -> EmbeddingResponse:
    image_bytes = await _read_tool_image(file, runtime)
    service = require_food_service(runtime)

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
