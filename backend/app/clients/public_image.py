"""下载公网栅格图片，但不暴露通用的 URL 代理。

远程图片 URL 唯一的下载器：浏览器下载代理（/api/images/download）以及模型输入 URL
（预测、检索搜索，经由 app.clients.image_fetch）。

防 SSRF：只允许公网地址（DNS 解析后检查，并为这次连接固定地址）、端口 80/443、
限制大小 / 时间 / 重定向次数，内容校验为图片。
失败时抛出 AppError：INVALID_URL / FORBIDDEN_HOST / FILE_TOO_LARGE /
UNSUPPORTED_MEDIA_TYPE / DOWNLOAD_TIMEOUT / DOWNLOAD_FAILED。
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import threading
import time
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote, urljoin, urlsplit

from app.core import errors
from app.core.errors import AppError
from PIL import Image


MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 3
SOCKET_TIMEOUT = 10
TOTAL_TIMEOUT = 30
CHUNK_SIZE = 64 * 1024
NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    port: int
    path: str
    addresses: tuple[str, ...]


# 除了公共互联网之外也按公网处理的网段，启动时从 IMAGE_DOWNLOAD_CONFIG.trusted_networks
# 设置一次（bootstrap.lifespan）。生产环境为空；在 fake-IP 代理后面的开发机（所有域名都
# 解析到 198.18.0.0/15 和 2001:2::/48）列出这两个网段。端口、重定向和各项限制照常检查。
_trusted_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


def set_trusted_networks(networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> None:
    global _trusted_networks
    _trusted_networks = tuple(networks)


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if any(ip in network for network in _trusted_networks):
        return True
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    # 内嵌的 IPv4 地址也不能提供通往私有网络的路由。
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour]
        if ip in NAT64_NETWORK:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.teredo:
            embedded.extend(ip.teredo)
        return all(value is None or _is_public(str(value)) for value in embedded)
    return True


def _resolve_target(url: str) -> _Target:
    if not url or len(url) > 8192 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise errors.invalid_url("Invalid image URL.")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("scheme or host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials")
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443) or "%" in host:
            raise ValueError("port or scoped address")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            labels = host.rstrip(".").split(".")
            if len(host) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
                raise ValueError("host")
            literal = None
    except (UnicodeError, ValueError) as exc:
        raise errors.invalid_url("Invalid image URL; use HTTP(S), no credentials, and port 80 or 443.") from exc

    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(record[4][0] for record in resolved))
        except OSError as exc:
            raise errors.download_failed("Could not resolve the image host.") from exc
    if not addresses or any(not _is_public(address) for address in addresses):
        raise errors.forbidden_host("Only public image hosts are allowed.")
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return _Target(parsed.scheme, host, port, path, addresses)


def _connection(target: _Target, timeout: float) -> http.client.HTTPConnection:
    connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(target.host, target.port, timeout=timeout)

    def connect_public_ip(_address, socket_timeout=timeout, source_address=None):
        # HTTP Host 以及 HTTPS 证书校验 / SNI 仍然使用 target.host。
        # 只改变 TCP 连接的目标，这样第二次域名解析无法重新绑定它。
        failure = None
        deadline = time.monotonic() + socket_timeout
        for address in target.addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Image connection timed out")
            try:
                return socket.create_connection((address, target.port), remaining, source_address)
            except OSError as exc:
                failure = exc
        raise failure or OSError("No public address is available")

    connection._create_connection = connect_public_ip
    return connection


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Image download timed out")
    return min(SOCKET_TIMEOUT, remaining)


def _verify_image(data: bytes) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                media_type = Image.MIME.get(image.format, "")
                image.verify()
        if not media_type.startswith("image/"):
            raise ValueError("Unsupported image format")
        return media_type
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise errors.unsupported_media_type("The remote file is not a valid supported image.") from exc


def _size_label(limit: int) -> str:
    return f"{limit / 2**20:g} MB" if limit >= 2**20 else f"{limit} bytes"


def fetch_image(url: str, *, max_bytes: int | None = None, total_timeout: float | None = None) -> tuple[bytes, str]:
    """公网图片 URL 的（内容, 校验过的媒体类型）。

    max_bytes / total_timeout 默认取 MAX_IMAGE_BYTES / TOTAL_TIMEOUT（调用时读取）。
    所有失败都是 AppError；上游的 HTTP 错误为 DOWNLOAD_FAILED（502），从不使用它的状态码。
    """
    limit = MAX_IMAGE_BYTES if max_bytes is None else int(max_bytes)
    too_large = f"Image exceeds the {_size_label(limit)} download limit."
    deadline = time.monotonic() + (TOTAL_TIMEOUT if total_timeout is None else float(total_timeout))
    try:
        for redirect in range(MAX_REDIRECTS + 1):
            target = _resolve_target(url)
            connection = _connection(target, _remaining(deadline))
            watchdog = None
            expired = threading.Event()
            try:
                connection.request("GET", target.path, headers={"Accept": "image/*", "Accept-Encoding": "identity", "User-Agent": "ImageEmbedding/1.0"})
                # 只靠 socket 超时，挡不住无休止的缓慢逐字节发送的响应头 / 响应体。
                # 现在就记下 socket：HTTPResponse 可能会把它从连接上摘下来。
                transport = connection.sock

                def expire_response(transport=transport, expired=expired):
                    expired.set()
                    if transport is not None:
                        try:
                            transport.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        try:
                            transport.close()
                        except OSError:
                            pass

                watchdog = threading.Timer(deadline - time.monotonic(), expire_response)
                watchdog.daemon = True
                watchdog.start()
                response = connection.getresponse()
                try:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if redirect >= MAX_REDIRECTS or not location:
                            raise errors.download_failed("The image server returned too many or invalid redirects.")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise errors.download_failed(f"Image download failed (upstream HTTP {response.status}).")
                    length = response.getheader("Content-Length")
                    if length is not None:
                        try:
                            size = int(length)
                            if size < 0:
                                raise ValueError("negative size")
                        except ValueError as exc:
                            raise errors.download_failed("The image server returned an invalid content length.") from exc
                        if size > limit:
                            raise errors.file_too_large(too_large)
                    data = bytearray()
                    while True:
                        timeout = _remaining(deadline)
                        if connection.sock is not None:
                            connection.sock.settimeout(timeout)
                        chunk = response.read(min(CHUNK_SIZE, limit + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > limit:
                            raise errors.file_too_large(too_large)
                finally:
                    response.close()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                try:
                    connection.close()
                finally:
                    if expired.is_set():
                        raise TimeoutError("Image response exceeded its deadline")
            content = bytes(data)
            return content, _verify_image(content)
    except AppError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise errors.download_timeout("The image download timed out.") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise errors.download_failed("Could not download the remote image.") from exc
    raise errors.download_failed("The image server returned too many redirects.")
