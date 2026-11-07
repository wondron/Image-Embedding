"""Download of an image URL given as model input (predict, retrieval search).

Same policy as the browser download proxy, through the same fetcher
(app.clients.public_image): public hosts only (checked after DNS and pinned for the
connection), ports 80/443, every redirect re-checked, bounded size and time, content
verified as an image. An upstream failure is DOWNLOAD_FAILED (502) / DOWNLOAD_TIMEOUT
(504), never the remote status.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from app.clients import public_image
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


async def read_url_bytes(url: str, *, max_image_bytes: int, timeout_s: float) -> bytes:
    """Normalize the URL and download at most max_image_bytes within timeout_s.

    INVALID_URL (422) / FORBIDDEN_HOST (403) / FILE_TOO_LARGE (413) /
    UNSUPPORTED_MEDIA_TYPE (415) / DOWNLOAD_FAILED (502) / DOWNLOAD_TIMEOUT (504).
    """
    url = clean_url(url)
    logger.info("download img_url repr=%r", url)
    # Blocking socket I/O with its own deadline: keep it off the event loop.
    content, _media_type = await asyncio.to_thread(
        public_image.fetch_image, url, max_bytes=max_image_bytes, total_timeout=timeout_s
    )
    return content
