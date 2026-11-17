"""Recipe detection service: the names of the dishes in an image (RECIPE_DETECT_CONFIG.url).

    POST <url>   JSON {"image_url": "...", "traceId": "..."}    the service downloads the image
                 multipart file=<the image>, traceId=<...>      an image without a link (an upload)

    200 {"code": "200", "success": true, "message": "识别成功", "traceId": "...",
         "model": "detect_recipe_onnx", "data": ["荷塘小炒", "凉拌猪耳", "清蒸鲈鱼"]}

data is [] when no dish is recognised. A failure answers a non-2xx status with success=false
and the reason in message. Failures raise AppError: UPSTREAM_TIMEOUT / UPSTREAM_ERROR.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core import errors

# What an upload starts with -> (media type, file extension) of its multipart part
_SIGNATURES = (
    (b"\xff\xd8\xff", ("image/jpeg", ".jpg")),
    (b"\x89PNG\r\n\x1a\n", ("image/png", ".png")),
    (b"GIF8", ("image/gif", ".gif")),
    (b"BM", ("image/bmp", ".bmp")),
)


def _upload_part(image_bytes: bytes) -> tuple[str, bytes, str]:
    """The multipart file part (filename, content, media type), the type read from the content."""
    media_type, extension = "application/octet-stream", ""
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        media_type, extension = "image/webp", ".webp"
    else:
        for signature, kind in _SIGNATURES:
            if image_bytes.startswith(signature):
                media_type, extension = kind
                break
    return f"query{extension}", image_bytes, media_type


def _dish_names(response: httpx.Response) -> list[str]:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise errors.upstream_error(f"菜谱识别服务返回的不是 JSON 对象（HTTP {response.status_code}）")
    if not response.is_success or body.get("success") is not True:
        reason = str(body.get("message") or "").strip() or "没有给出原因"
        raise errors.upstream_error(f"菜谱识别服务报错（HTTP {response.status_code}，code={body.get('code')}）：{reason}")
    data = body.get("data")
    if not isinstance(data, list):
        raise errors.upstream_error("菜谱识别服务返回的 data 不是列表")
    names: list[str] = []
    for item in data:
        name = item.strip() if isinstance(item, str) else ""
        if name and name not in names:
            names.append(name)
    return names


async def detect_recipes(
    client: httpx.AsyncClient,
    url: str,
    *,
    image_url: str | None = None,
    image_bytes: bytes | None = None,
    trace_id: str = "",
    timeout: float,
) -> list[str]:
    """The dish names the service recognises in the image, in its order, blanks and repeats left
    out; [] when it recognises none.

    image_url when the image has a link (the service downloads it), else image_bytes (uploaded).
    timeout bounds the whole call, upload included.
    """
    request: dict[str, Any]
    if image_url:
        request = {"json": {"image_url": image_url, "traceId": trace_id}}
    elif image_bytes:
        request = {"files": {"file": _upload_part(image_bytes)}, "data": {"traceId": trace_id}}
    else:
        raise errors.upstream_error("菜谱识别没有图片可用：既没有图片链接，也没有图片内容")
    try:
        # httpx applies its timeout to each connect / write / read; wait_for bounds the call as a whole
        response = await asyncio.wait_for(client.post(url, timeout=timeout, **request), timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise errors.upstream_timeout(f"菜谱识别服务 {timeout:g} 秒内没有返回") from exc
    except httpx.HTTPError as exc:
        raise errors.upstream_error(f"无法连接菜谱识别服务（{type(exc).__name__}）：{exc}") from exc
    return _dish_names(response)
