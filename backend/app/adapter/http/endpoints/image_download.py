"""Download public raster images without exposing a general-purpose URL proxy."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import threading
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote, urljoin, urlsplit

from fastapi import APIRouter, HTTPException, Query, Response
from PIL import Image
from starlette.concurrency import run_in_threadpool


router = APIRouter(prefix="/api/images", tags=["images"])
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


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    # Embedded IPv4 addresses must not provide a route to private networks either.
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
        raise HTTPException(422, "Invalid image URL.")
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
        raise HTTPException(422, "Invalid image URL; use HTTP(S), no credentials, and port 80 or 443.") from exc

    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(record[4][0] for record in resolved))
        except OSError as exc:
            raise HTTPException(502, "Could not resolve the image host.") from exc
    if not addresses or any(not _is_public(address) for address in addresses):
        raise HTTPException(403, "Only public image hosts are allowed.")
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return _Target(parsed.scheme, host, port, path, addresses)


def _connection(target: _Target, timeout: float) -> http.client.HTTPConnection:
    connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(target.host, target.port, timeout=timeout)

    def connect_public_ip(_address, socket_timeout=timeout, source_address=None):
        # HTTP Host and HTTPS certificate verification/SNI still use target.host.
        # Only the TCP destination changes, so a second hostname lookup cannot rebind it.
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
        raise HTTPException(415, "The remote file is not a valid supported image.") from exc


def _fetch_image(url: str) -> tuple[bytes, str]:
    deadline = time.monotonic() + TOTAL_TIMEOUT
    try:
        for redirect in range(MAX_REDIRECTS + 1):
            target = _resolve_target(url)
            connection = _connection(target, _remaining(deadline))
            watchdog = None
            expired = threading.Event()
            try:
                connection.request("GET", target.path, headers={"Accept": "image/*", "Accept-Encoding": "identity", "User-Agent": "ImageEmbedding/1.0"})
                # Socket timeouts alone allow an endless slow trickle of headers/body.
                # Capture the socket now: HTTPResponse may detach it from connection.
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
                            raise HTTPException(502, "The image server returned too many or invalid redirects.")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise HTTPException(502, f"Image download failed (upstream HTTP {response.status}).")
                    length = response.getheader("Content-Length")
                    if length is not None:
                        try:
                            size = int(length)
                            if size < 0:
                                raise ValueError("negative size")
                        except ValueError as exc:
                            raise HTTPException(502, "The image server returned an invalid content length.") from exc
                        if size > MAX_IMAGE_BYTES:
                            raise HTTPException(413, "Image exceeds the 15 MB download limit.")
                    data = bytearray()
                    while True:
                        timeout = _remaining(deadline)
                        if connection.sock is not None:
                            connection.sock.settimeout(timeout)
                        chunk = response.read(min(CHUNK_SIZE, MAX_IMAGE_BYTES + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > MAX_IMAGE_BYTES:
                            raise HTTPException(413, "Image exceeds the 15 MB download limit.")
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
    except HTTPException:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise HTTPException(504, "The image download timed out.") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise HTTPException(502, "Could not download the remote image.") from exc
    raise HTTPException(502, "The image server returned too many redirects.")


@router.get("/download", response_class=Response)
async def download_image(url: str = Query(..., min_length=1, max_length=8192)) -> Response:
    """Return validated image bytes for browser downloads blocked by source CORS."""
    content, media_type = await run_in_threadpool(_fetch_image, url)
    return Response(content=content, media_type=media_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
