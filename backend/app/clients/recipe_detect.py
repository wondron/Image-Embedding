"""菜谱识别服务：图片里的菜名（RECIPE_DETECT_CONFIG.url）。

    POST <url>   JSON {"image_url": "...", "traceId": "..."}    服务自己下载图片
                 multipart file=<图片>, traceId=<...>           没有链接的图片（上传的）

    200 {"code": "200", "success": true, "message": "识别成功", "traceId": "...",
         "model": "detect_recipe_onnx", "data": ["荷塘小炒", "凉拌猪耳", "清蒸鲈鱼"]}

没有识别出菜名时 data 为 []。失败时返回非 2xx 状态码、success=false，原因在 message 里。
失败时抛出 AppError：UPSTREAM_TIMEOUT / UPSTREAM_ERROR。

调用方传了计时器时，记下这次调用的各阶段（来自 httpcore 的 trace 事件）：

    建立连接   DNS、TCP（和 TLS）；复用已有的连接时没有这一步
    发送请求   把请求头和请求体写进连接。上传图片时主要是上传；发送缓冲区里还没发出去的部分
               算在下一步里
    等待响应   发完请求到收到响应头：服务处理（按链接时包括它下载图片）加上网络往返
    接收响应   读取响应体

调用失败或超时时，失败的那一步（超时时是还没完成的那一步）记为「…失败」，例如超时时还在
上传是「发送请求失败」。各阶段之外的时间（等连接池、解析结果）只算在整个调用的耗时里。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core import errors
from app.utils.timer import timer_tool

# 上传内容的开头 -> 它在 multipart 中那一部分的（媒体类型, 文件扩展名）
_SIGNATURES = (
    (b"\xff\xd8\xff", ("image/jpeg", ".jpg")),
    (b"\x89PNG\r\n\x1a\n", ("image/png", ".png")),
    (b"GIF8", ("image/gif", ".gif")),
    (b"BM", ("image/bmp", ".bmp")),
)


# httpcore 的 trace 事件名（如 http11.send_request_body.started / .complete / .failed）去掉前缀和
# 结果后的部分 -> 阶段。没列出的事件（如 response_closed）不计
_PHASES = {
    "connect_tcp": "建立连接",
    "connect_unix_socket": "建立连接",
    "start_tls": "建立连接",
    "retry": "建立连接",  # 连接失败后重试前的等待
    "send_connection_init": "建立连接",  # HTTP/2
    "send_request_headers": "发送请求",
    "send_request_body": "发送请求",
    "receive_response_headers": "等待响应",
    "receive_response_body": "接收响应",
}


class _Phases:
    """一次调用的各阶段耗时，由 httpcore 的 trace 事件累计（httpx 的 trace 扩展）。

    只在事件循环上更新。只有真实的网络传输才会发事件（测试用的 httpx.MockTransport 不会），
    这时没有任何阶段。
    """

    def __init__(self) -> None:
        self._open: dict[str, float] = {}  # 已经开始、还没结束的事件 -> 开始的时间
        self._seconds: dict[str, float] = {}  # 阶段 -> 累计秒数，按第一次出现的顺序
        self._failed: set[str] = set()

    async def trace(self, event: str, info: dict[str, Any]) -> None:
        """httpx 的 trace 回调：异步客户端要求它是协程函数。"""
        name, _, outcome = event.rpartition(".")
        phase = _PHASES.get(name.partition(".")[2])
        if phase is None:
            return
        now = time.perf_counter()
        if outcome == "started":
            self._open[name] = now
            self._seconds.setdefault(phase, 0.0)
            return
        started = self._open.pop(name, None)
        if started is not None:
            self._seconds[phase] += now - started
            if outcome != "complete":
                self._failed.add(phase)

    def record(self, timer: timer_tool) -> None:
        """把各阶段记到 timer 上。还没结束的（调用在它进行时被取消）算到现在，记为失败。"""
        now = time.perf_counter()
        for name, started in self._open.items():
            phase = _PHASES[name.partition(".")[2]]
            self._seconds[phase] += now - started
            self._failed.add(phase)
        self._open.clear()
        for phase, seconds in self._seconds.items():
            timer.record(phase, seconds, failed=phase in self._failed)


def _upload_part(image_bytes: bytes) -> tuple[str, bytes, str]:
    """multipart 的文件部分（文件名, 内容, 媒体类型），类型根据内容判断。"""
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
    timer: timer_tool | None = None,
) -> list[str]:
    """服务在图片里识别出的菜名，保持它给出的顺序，去掉空白项和重复项；没有识别出时为 []。

    图片有链接时用 image_url（服务自己下载），否则用 image_bytes（上传）。
    timeout 限制整个调用，包括上传。timer：调用方的计时器，记录这次调用的各阶段
    （建立连接 / 发送请求 / 等待响应 / 接收响应，见模块说明），成功和失败时都记。
    """
    request: dict[str, Any]
    if image_url:
        request = {"json": {"image_url": image_url, "traceId": trace_id}}
    elif image_bytes:
        request = {"files": {"file": _upload_part(image_bytes)}, "data": {"traceId": trace_id}}
    else:
        raise errors.upstream_error("菜谱识别没有图片可用：既没有图片链接，也没有图片内容")
    phases = _Phases()
    try:
        # httpx 的超时分别作用于每次连接 / 写入 / 读取；wait_for 限制整个调用
        response = await asyncio.wait_for(
            client.post(url, timeout=timeout, extensions={"trace": phases.trace}, **request), timeout
        )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise errors.upstream_timeout(f"菜谱识别服务 {timeout:g} 秒内没有返回") from exc
    except httpx.HTTPError as exc:
        raise errors.upstream_error(f"无法连接菜谱识别服务（{type(exc).__name__}）：{exc}") from exc
    finally:
        if timer is not None:
            phases.record(timer)
    return _dish_names(response)
