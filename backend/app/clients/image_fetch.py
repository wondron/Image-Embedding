"""Bounded download of an image URL given as model input (predict, retrieval search).

Uses the application's shared httpx.AsyncClient. Host policy is permissive
(any http/https host); the browser-download proxy uses the stricter
app.clients.public_image instead.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.core import errors

logger = logging.getLogger("image_fetch")


def clean_url(url: str, *, default_scheme: str = "https") -> str:
    """Normalize search URLs, accepting missing schemes and protocol-relative URLs."""
    url = url.strip()
    if not url:
        raise errors.invalid_url("img_url is empty.")
    if url.startswith("//"):
        url = f"{default_scheme}:{url}"

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"{default_scheme}://{url}"
            parsed = urlparse(url)
    except ValueError as exc:
        raise errors.invalid_url(f"Invalid img_url: {url!r}") from exc

    if parsed.scheme not in ("http", "https"):
        raise errors.invalid_url(f"img_url scheme not allowed: {parsed.scheme!r}")
    if not parsed.netloc:
        raise errors.invalid_url(f"img_url invalid (missing host): {url!r}")
    return url


async def read_url_bytes(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_image_bytes: int,
    timeout_s: float,
) -> bytes:
    """Normalize a URL and stream at most max_image_bytes of the response.

    INVALID_URL (422) / FILE_TOO_LARGE (413) / DOWNLOAD_TIMEOUT (504) /
    DOWNLOAD_FAILED (upstream status, or 502) / EMPTY_FILE (400).
    """
    url = clean_url(url)
    logger.info("download img_url repr=%r", url)
    too_large = f"Downloaded file too large (> {max_image_bytes} bytes)."
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
                        raise errors.file_too_large(too_large)

            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=min(64 * 1024, max_image_bytes + 1)):
                if len(data) + len(chunk) > max_image_bytes:
                    raise errors.file_too_large(too_large)
                data.extend(chunk)
    except httpx.UnsupportedProtocol as exc:
        raise errors.invalid_url(f"Invalid img_url: {url!r}") from exc
    except httpx.TimeoutException as exc:
        raise errors.download_timeout(f"Download timeout after {timeout_s}s.") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        raise errors.download_failed(f"Download failed with status {status}.", http_status=status) from exc
    except httpx.RequestError as exc:
        raise errors.download_failed(f"Download request error: {exc}") from exc

    if not data:
        raise errors.empty_file("Downloaded image is empty.")
    return bytes(data)
