"""Bounded image input and encoding helpers for HTTP operations.

URL input uses the application's HTTP client and preserves the search API's
URL policy. Browser downloads use the stricter public-host downloader instead.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Collection
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, UploadFile
from PIL import Image

logger = logging.getLogger("svc.image_io")


async def read_file_bytes(
    file: UploadFile,
    max_image_bytes: int,
    *,
    allowed_content_types: Collection[str] | None = None,
    empty_detail: str = "Uploaded file is empty.",
    too_large_detail: str | None = None,
) -> bytes:
    """Read at most limit + 1 bytes, optionally validating the declared MIME type.

    Custom details keep existing endpoint error contracts while sharing checks.
    The caller owns and closes the UploadFile.
    """
    if (
        allowed_content_types is not None
        and file.content_type
        and file.content_type not in allowed_content_types
    ):
        raise HTTPException(415, f"unsupported image content type: {file.content_type}")

    data = await file.read(max_image_bytes + 1)
    if not data:
        raise HTTPException(400, empty_detail)
    if len(data) > max_image_bytes:
        detail = too_large_detail or f"File too large (> {max_image_bytes} bytes)."
        raise HTTPException(413, detail)
    return data


def clean_url(url: str, *, default_scheme: str = "https") -> str:
    """Normalize search URLs, accepting missing schemes and protocol-relative URLs."""
    url = url.strip()
    if not url:
        raise HTTPException(422, "img_url is empty.")
    if url.startswith("//"):
        url = f"{default_scheme}:{url}"

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"{default_scheme}://{url}"
            parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid img_url: {url!r}") from exc

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(422, f"img_url scheme not allowed: {parsed.scheme!r}")
    if not parsed.netloc:
        raise HTTPException(422, f"img_url invalid (missing host): {url!r}")
    return url


async def read_url_bytes(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_image_bytes: int,
    timeout_s: float,
) -> bytes:
    """Normalize a URL and stream a bounded response using the caller's client."""
    url = clean_url(url)
    logger.info("download img_url repr=%r", url)
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    declared_size = int(length)
                except ValueError:
                    pass
                else:
                    if declared_size > max_image_bytes:
                        raise HTTPException(413, f"Downloaded file too large (> {max_image_bytes} bytes).")

            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=min(64 * 1024, max_image_bytes + 1)):
                if len(data) + len(chunk) > max_image_bytes:
                    raise HTTPException(413, f"Downloaded file too large (> {max_image_bytes} bytes).")
                data.extend(chunk)
    except httpx.UnsupportedProtocol as exc:
        raise HTTPException(422, f"Invalid img_url: {url!r}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"Download timeout after {timeout_s}s.") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(status, f"Download failed with status {status}.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Download request error: {exc}") from exc

    if not data:
        raise HTTPException(400, "Downloaded image is empty.")
    return bytes(data)


def encode_png_base64(image: Any, *, bgr: bool = False) -> str:
    """Encode a grayscale or three-channel image array without importing model code."""
    if image is None:
        raise ValueError("image is empty")

    ndim = getattr(image, "ndim", 0)
    shape = getattr(image, "shape", ())
    if ndim == 2:
        height, width = shape
        png_image = Image.frombytes("L", (width, height), image.tobytes())
    elif ndim == 3 and shape[2] == 3:
        height, width, _ = shape
        png_image = Image.frombytes(
            "RGB", (width, height), image.tobytes(), "raw", "BGR" if bgr else "RGB"
        )
    else:
        raise ValueError(f"unsupported image shape for PNG encoding: {shape}")

    output = BytesIO()
    png_image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")
