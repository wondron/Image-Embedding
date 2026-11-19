"""下载作为模型输入的图片 URL（预测、检索搜索）。

和浏览器下载代理的规则相同，使用同一个下载器（app.clients.public_image）：只允许公网
地址（DNS 解析后检查，并为这次连接固定地址）、端口 80/443、每次重定向都重新检查、
限制大小和时间、内容校验为图片。上游失败为 DOWNLOAD_FAILED（502）/ DOWNLOAD_TIMEOUT
（504），从不使用远端的状态码。
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from app.clients import public_image
from app.core import errors

logger = logging.getLogger("image_fetch")


def clean_url(url: str, *, default_scheme: str = "https") -> str:
    """规范化搜索 URL，接受缺少协议和以 // 开头的 URL。"""
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
    """规范化 URL，并在 timeout_s 内最多下载 max_image_bytes 字节。

    INVALID_URL (422) / FORBIDDEN_HOST (403) / FILE_TOO_LARGE (413) /
    UNSUPPORTED_MEDIA_TYPE (415) / DOWNLOAD_FAILED (502) / DOWNLOAD_TIMEOUT (504)。
    """
    url = clean_url(url)
    logger.info("download img_url repr=%r", url)
    # 带自己超时的阻塞 socket I/O：放在事件循环之外执行。
    content, _media_type = await asyncio.to_thread(
        public_image.fetch_image, url, max_bytes=max_image_bytes, total_timeout=timeout_s
    )
    return content
