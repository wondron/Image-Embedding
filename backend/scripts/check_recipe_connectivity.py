#!/usr/bin/env python3
"""菜谱识别服务连通性检查：在部署环境里按后端的方式调用一次识别接口（RECIPE_DETECT_CONFIG.url），
找出后端连不上它的原因。

不依赖项目代码，只用标准库，Python 3.6+；装了 httpx（后端用的 HTTP 客户端，镜像里有）时用它发请求，
和后端一样读取代理环境变量；没装时用标准库 urllib。依次检查：

  1. 代理环境变量：后端的 httpx 会读 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY（大小写都认），
     识别服务的地址可能因此走了代理
  2. 不走代理，直接和识别服务的端口建 TCP 连接；在容器里时，再试一下容器网关（宿主机）的同一端口
  3. 按后端的方式调用接口，并给出后端日志里对应的那一行；会走代理时，再不走代理调用一次作对比

最后给出结论和改法。

要在和后端相同的环境里运行。后端在容器里时进容器运行（同一个网络、同一套环境变量），不用拷文件：
  docker exec -i roki-imageembedding python - < check_recipe_connectivity.py
  docker exec -i roki-imageembedding python - --image-url https://example.com/dish.jpg < check_recipe_connectivity.py
再在宿主机上运行一次作对比：
  python3 check_recipe_connectivity.py

不给图片时上传一张脚本生成的小图：只为测连通，识别结果没有意义。
退出码：0 按后端的方式调用成功；2 参数错误；3 连不上识别服务；4 连上了，但服务报错或超过 --timeout。
"""

import argparse
import asyncio
import io
import ipaddress
import json
import math
import os
import platform
import re
import socket
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zlib
from time import perf_counter
from urllib.parse import urlsplit

try:
    import httpx  # 后端用的 HTTP 客户端；宿主机上可能没装，没装时用 urllib
except ImportError:
    httpx = None
if not hasattr(asyncio, "run"):  # Python 3.6 上即使装了旧版 httpx 也用 urllib
    httpx = None

DEFAULT_URL = "http://118.31.105.171:8866/detect_recipe_onnx"  # config.yaml 里的 RECIPE_DETECT_CONFIG.url
DEFAULT_TIMEOUT = 5.0  # RECIPE_DETECT_CONFIG.timeout
DEFAULT_MAX_WAIT = 15.0
USER_AGENT = "svc.predict/1.0"  # 后端共用 HTTP 客户端的 User-Agent
EXIT_OK, EXIT_ARGS, EXIT_CONNECT, EXIT_SERVICE = 0, 2, 3, 4


def mark(ok):
    return "OK  " if ok else "FAIL"


# ---- 运行环境 -----------------------------------------------------------------------

def container_hint():
    """在容器里时给出依据，否则为 None。"""
    for marker in ("/.dockerenv", "/run/.containerenv"):
        if os.path.exists(marker):
            return f"有 {marker}"
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="replace") as f:
            cgroup = f.read()
    except OSError:
        return None
    for word in ("docker", "containerd", "kubepods", "libpod", "lxc"):
        if word in cgroup:
            return f"/proc/1/cgroup 里有 {word}"
    return None


def default_gateway():
    """Linux 默认路由的网关；在容器里它就是宿主机（docker 网桥的地址）。"""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            rows = [line.split() for line in f.read().splitlines()[1:]]
        for row in rows:
            # 列：Iface Destination Gateway Flags …；Flags 的 0x2 表示经过网关
            if len(row) > 3 and row[1] == "00000000" and int(row[3], 16) & 0x2:
                return socket.inet_ntoa(struct.pack("<L", int(row[2], 16)))
    except (OSError, ValueError):
        pass
    return None


# ---- 代理 ---------------------------------------------------------------------------

def redact(text):
    """代理地址里的密码换成 ***。"""
    return re.sub(r"(://[^/@:]*:)[^/@]*@", r"\1***@", text)


def proxy_variables():
    """所有 *_proxy 环境变量（大小写都算，httpx、requests、urllib 认的都是这些）。"""
    return sorted(f"{name}={redact(value)}" for name, value in os.environ.items() if name.lower().endswith("_proxy"))


def proxy_for(url):
    """httpx 访问 url 时会用的代理（读环境变量；Windows / macOS 上还读系统代理设置），直连时为 None。

    规则和 httpx 一样：同协议的 http_proxy / https_proxy 优先，其次 all_proxy；
    NO_PROXY 里有 *，或者有这个主机（或它的上级域名）时直连。
    """
    proxies = urllib.request.getproxies()
    parts = urlsplit(url)
    proxy = proxies.get(parts.scheme) or proxies.get("all")
    if not proxy or urllib.request.proxy_bypass_environment(parts.hostname or "", proxies):
        return None
    return proxy if "://" in proxy else "http://" + proxy


# ---- TCP ----------------------------------------------------------------------------

class Tcp:
    """一次 TCP 直连的结果。kind：ok / refused / timeout / dns / other；tun：连上的是本机 TUN 模式的代理。"""

    def __init__(self, kind, text, tun=False):
        self.kind, self.text, self.tun = kind, text, tun

    @property
    def ok(self):
        return self.kind == "ok"


def tcp_check(host, port, timeout):
    """不走代理，直接和 host:port 建一次 TCP 连接。"""
    started = perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            peer, local = sock.getpeername()[0], sock.getsockname()[0]
    except socket.gaierror as exc:
        return Tcp("dns", f"解析不了 {host}：{exc}")
    except ConnectionRefusedError:
        return Tcp("refused", f"被拒绝（{(perf_counter() - started) * 1000:.0f} ms）")
    except (socket.timeout, TimeoutError):
        return Tcp("timeout", f"{timeout:g} 秒内没连上")
    except OSError as exc:
        return Tcp("other", f"{type(exc).__name__}: {exc}")
    text = f"连上了（{(perf_counter() - started) * 1000:.0f} ms），对端 {peer}，本机这端 {local}"
    try:
        tun = ipaddress.ip_address(local) in ipaddress.ip_network("198.18.0.0/15")
    except ValueError:  # 带 %网卡 的 IPv6 地址，老版本 Python 解析不了
        tun = False
    if tun:
        text += "。本机这端是 198.18.x.x：TUN 模式的代理（如 Clash）接管了流量，连上的是代理，不一定是对方"
    return Tcp("ok", text, tun)


def is_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def resolve(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return f"FAIL 解析不了 {host}：{exc}"
    return "OK   {} 解析到 {}".format(host, "、".join(dict.fromkeys(info[4][0] for info in infos)))


# ---- 请求 ---------------------------------------------------------------------------

def test_png(size=128):
    """一张 size×size 的渐变 PNG（标准库生成），只用来测连通。"""
    rows = bytearray()
    for y in range(size):
        # Sub 过滤：每个像素存它和左边像素的差，渐变图压缩后只有几百字节。像素是 (2x, 2y, 160)
        rows += bytes((1, 0, y * 2 % 256, 160)) + bytes((2, 0, 0)) * (size - 1)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8 位 RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")


_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF8", "image/gif", ".gif"),
    (b"BM", "image/bmp", ".bmp"),
)


def upload_part(content):
    """multipart 里文件那一部分（文件名, 内容, 类型），和后端一样按内容判断类型。"""
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "query.webp", content, "image/webp"
    for signature, media_type, extension in _SIGNATURES:
        if content.startswith(signature):
            return "query" + extension, content, media_type
    return "query", content, "application/octet-stream"


def multipart(fields, name, part):
    """urllib 用的 multipart/form-data 请求体和 Content-Type。"""
    boundary = uuid.uuid4().hex
    filename, content, media_type = part
    pieces = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
        for key, value in fields.items()
    ]
    pieces.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n".encode("utf-8")
    )
    pieces += [content, f"\r\n--{boundary}--\r\n".encode("utf-8")]
    return b"".join(pieces), "multipart/form-data; boundary=" + boundary


class Payload:
    """发给识别服务的请求，和后端一样：有图片链接时发 JSON（服务自己下载），否则上传图片。"""

    def __init__(self, image_url, image_file, trace_id):
        self.generated = False  # 用的是脚本生成的测试图
        if image_url:
            fields = {"image_url": image_url, "traceId": trace_id}
            self.describe = f"JSON image_url={image_url}（同 /api/search/auto/url）"
            self.httpx_kwargs = {"json": fields}
            self.body, self.content_type = json.dumps(fields).encode("utf-8"), "application/json"
            return
        if image_file:
            with open(image_file, "rb") as f:
                content = f.read()
            source = image_file
        else:
            content, source, self.generated = test_png(), "脚本生成的测试 PNG", True
        part = upload_part(content)
        fields = {"traceId": trace_id}
        self.describe = f"上传{source}，{len(content)} 字节（multipart file，同 /api/search/auto/file）"
        self.httpx_kwargs = {"files": {"file": part}, "data": fields}
        self.body, self.content_type = multipart(fields, "file", part)


class Outcome:
    """一次 HTTP 调用的结果。"""

    def __init__(self):
        self.status = None  # HTTP 状态码；没收到响应时为 None
        self.content_type = ""
        self.body = None  # 响应的 JSON；不是 JSON 时为 None
        self.text = ""  # 响应体的开头
        self.error_type = ""  # 没收到响应时的异常
        self.error_text = ""
        self.phase = None  # 没收到响应时停在哪一步（用 httpx 时才知道）
        self.elapsed = 0.0

    def read(self, status, content_type, content):
        self.status, self.content_type = status, content_type or ""
        self.text = content[:400].decode("utf-8", "replace")
        try:
            self.body = json.loads(content.decode("utf-8"))
        except ValueError:  # 解码失败和 JSON 格式错误都是 ValueError
            self.body = None

    def fail(self, error_type, error_text):
        self.error_type, self.error_text = error_type, error_text

    @property
    def error(self):
        return f"{self.error_type}: {self.error_text}" if self.error_text else self.error_type

    @property
    def from_service(self):
        """回复来自识别服务（JSON 里有 success），而不是代理、网关或者别的程序。"""
        return isinstance(self.body, dict) and "success" in self.body

    @property
    def ok(self):
        """后端会当成功的回复：2xx、success 为 true、data 是列表。"""
        return (self.from_service and 200 <= self.status < 300 and self.body.get("success") is True
                and isinstance(self.body.get("data"), list))

    def summary(self, limit):
        """（标记, 说明）；limit 是后端的时限。"""
        took = f"{self.elapsed:.2f} 秒"
        if self.status is None:
            where = f"，停在「{self.phase}」" if self.phase else ""
            return "FAIL", f"{took}，没有收到响应{where}：{self.error}"
        if not self.from_service:
            snippet = " ".join(self.text.split())[:200]
            return "FAIL", f"HTTP {self.status}，{took}，不是识别服务的回复（Content-Type: {self.content_type or '无'}）：{snippet}"
        data = json.dumps(self.body.get("data"), ensure_ascii=False)
        text = f"HTTP {self.status}，{took}，success={self.body.get('success')}，message={self.body.get('message')}，data={data}"
        if not self.ok:
            return "FAIL", text
        if self.elapsed > limit:
            return "WARN", f"{text}（超过后端的 {limit:g} 秒）"
        return "OK  ", text


def backend_log(outcome, limit):
    """后端遇到同样的结果时，日志里「菜谱识别完成 / 失败」那一行的结果（规则同 app/clients/recipe_detect.py）。"""
    if outcome.elapsed > limit or (outcome.status is None and "timeout" in outcome.error.lower()):
        return f"菜谱识别失败 | 错误=UPSTREAM_TIMEOUT: 菜谱识别服务 {limit:g} 秒内没有返回"
    if outcome.status is None:
        return f"菜谱识别失败 | 错误=UPSTREAM_ERROR: 无法连接菜谱识别服务（{outcome.error_type}）：{outcome.error_text}"
    body = outcome.body
    if not isinstance(body, dict):
        return f"菜谱识别失败 | 错误=UPSTREAM_ERROR: 菜谱识别服务返回的不是 JSON 对象（HTTP {outcome.status}）"
    if not 200 <= outcome.status < 300 or body.get("success") is not True:
        reason = str(body.get("message") or "").strip() or "没有给出原因"
        return f"菜谱识别失败 | 错误=UPSTREAM_ERROR: 菜谱识别服务报错（HTTP {outcome.status}，code={body.get('code')}）：{reason}"
    if not isinstance(body.get("data"), list):
        return "菜谱识别失败 | 错误=UPSTREAM_ERROR: 菜谱识别服务返回的 data 不是列表"
    names = []
    for item in body["data"]:
        name = item.strip() if isinstance(item, str) else ""
        if name and name not in names:
            names.append(name)
    return "菜谱识别完成 | 结果=" + json.dumps(names, ensure_ascii=False)


class _Steps:
    """httpcore 的 trace 回调（后端记各阶段耗时用的也是这些事件）：请求停在了哪一步。"""

    NAMES = {
        "connect_tcp": "建立 TCP 连接",
        "start_tls": "TLS 握手",
        "send_request_headers": "发送请求",
        "send_request_body": "发送请求",
        "receive_response_headers": "等待响应",
        "receive_response_body": "接收响应",
    }

    def __init__(self):
        self.current = None  # 还没完成或者失败了的那一步

    async def __call__(self, event, info):
        name, _, outcome = event.rpartition(".")  # 如 connection.connect_tcp.started
        step = self.NAMES.get(name.partition(".")[2])
        if step is not None:
            self.current = None if outcome == "complete" else step


async def _httpx_post(url, payload, trust_env, timeout, max_wait, outcome):
    """和后端的客户端一样（trust_env 决定读不读代理环境变量）；TCP 连接等 timeout 秒，其余最多 max_wait 秒。"""
    steps = _Steps()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(max_wait, connect=timeout),
            follow_redirects=True,
            trust_env=trust_env,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await asyncio.wait_for(
                client.post(url, extensions={"trace": steps}, **payload.httpx_kwargs), max_wait
            )
        outcome.read(response.status_code, response.headers.get("content-type"), response.content)
    finally:
        outcome.phase = steps.current


def _urllib_post(url, payload, use_env_proxy, max_wait, outcome):
    """没装 httpx 时用。代理按 httpx 的规则选（proxy_for），而不是 urllib 自己的（它不认 ALL_PROXY）。"""
    proxy = proxy_for(url) if use_env_proxy else None
    handler = urllib.request.ProxyHandler({urlsplit(url).scheme: proxy} if proxy else {})
    request = urllib.request.Request(
        url, data=payload.body, method="POST",
        headers={"Content-Type": payload.content_type, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.build_opener(handler).open(request, timeout=max_wait) as response:
            outcome.read(response.status, response.headers.get("Content-Type"), response.read())
    except urllib.error.HTTPError as exc:  # 非 2xx 也可能是识别服务的回复，照样读
        outcome.read(exc.code, exc.headers.get("Content-Type") if exc.headers else "", exc.read())
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, BaseException):
            outcome.fail(type(reason).__name__, str(reason))
        else:
            outcome.fail("URLError", str(reason))


def post(url, payload, use_env_proxy, timeout, max_wait):
    """POST 一次；use_env_proxy=False 时不管代理环境变量，直连。"""
    outcome = Outcome()
    started = perf_counter()
    try:
        if httpx is not None:
            asyncio.run(_httpx_post(url, payload, use_env_proxy, timeout, max_wait, outcome))
        else:
            _urllib_post(url, payload, use_env_proxy, max_wait, outcome)
    except asyncio.TimeoutError:
        outcome.fail("TimeoutError", f"{max_wait:g} 秒内没有完成")
    except Exception as exc:
        outcome.fail(type(exc).__name__, str(exc))
    outcome.elapsed = perf_counter() - started
    return outcome


# ---- 结论 ---------------------------------------------------------------------------

def tcp_hints(tcp, host, port, path, timeout, max_wait, container, gateway, gateway_tcp, attempt):
    """两条路都没到识别服务时，按 TCP 直连的结果说明原因。attempt：不走代理的那次调用。"""
    if tcp.kind == "refused":
        lines = [
            f"     {host}:{port} 拒绝连接：那里没有程序在监听这个端口，或者防火墙直接拒绝。",
            f"     识别服务在后端这台机器上时，在宿主机上执行 ss -ltnp | grep :{port}：只监听 127.0.0.1 时，"
            "容器和别的机器都连不到它，要改成监听 0.0.0.0；它也在容器里时，用 docker ps 看端口有没有映射出来。",
        ]
    elif tcp.kind == "timeout":
        lines = [
            f"     {timeout:g} 秒内 TCP 没连上，包被丢掉了：阿里云安全组或者机器上的防火墙（ufw / firewalld / iptables）"
            f"没放行 {port}，或者从这里到不了这个地址。",
            "     识别服务和后端在同一台机器（或同一个 VPC）时，不要绕公网 IP，改用它的内网 IP（在那台机器上用 ip -4 addr 查看）。",
        ]
    elif tcp.kind == "dns":
        lines = [f"     {tcp.text}：容器里的 DNS 和宿主机不一样时常见，改用 IP，或者在 compose 里配 extra_hosts。"]
    elif tcp.tun:
        lines = [
            "     TCP「连上」的是本机 TUN 模式的代理（本机这端 198.18.x.x），代理没把请求送到识别服务："
            f"给 {host} 加一条直连（DIRECT）规则，或者关掉 TUN 再试。"
        ]
    elif tcp.ok:
        where = f"，停在「{attempt.phase}」" if attempt.phase else ""
        if "timeout" in attempt.error.lower():
            reason = f"{max_wait:g} 秒都没等到回复，识别服务可能卡住了或者太慢"
        else:
            reason = "对方没回就断开了：这个端口上可能不是识别服务，或者中间有代理、防火墙截断了连接，也可能是识别服务处理时崩了"
        lines = [f"     TCP 能连上，但没有收到 HTTP 响应{where}：{reason}。"]
    else:
        lines = [f"     TCP 直连失败：{tcp.text}"]
    if container and gateway_tcp is not None and gateway_tcp.ok and not tcp.ok:
        lines.append(
            f"     容器网关 {gateway}（宿主机）的 {port} 端口能连上：识别服务就在这台宿主机上的话，RECIPE_DETECT_CONFIG.url "
            f"改用宿主机的内网 IP（或 {gateway}），先用 --url http://{gateway}:{port}{path} 跑一次本脚本确认。"
        )
    return lines


def conclude(args, host, port, path, payload, container, proxy, proxy_tcp, direct_tcp, gateway, gateway_tcp, backend, direct):
    """（退出码, 结论的各行）。backend：按后端的方式（读代理变量）；direct：不走代理（只在会走代理时才有）。"""
    if backend.from_service:
        if backend.ok and backend.elapsed <= args.timeout:
            return EXIT_OK, [
                f"PASS 按后端的方式调得通（{backend.elapsed:.2f} 秒）。",
                "     这里就是后端的环境（后端的容器里）的话，网络和代理都没问题。后端还是失败时再看：",
                "     - 后端实际用的地址：Nacos（dataId roki-ai-image-search）里有 RECIPE_DETECT_CONFIG 时，会逐项覆盖镜像里 config.yaml 的值；",
                "     - output/logs/app.log 里「菜谱识别失败」那一行的「错误=」"
                + ("，和上面「后端日志里对应」的比一比；" if httpx is not None else "；"),
                "     - 「整图搜索完成 … 已取消=菜谱识别」不是连接失败：整图已经搜到结果时，后端不再等菜谱识别。",
            ]
        if backend.ok:
            return EXIT_SERVICE, [
                f"WARN 调得通，但用了 {backend.elapsed:.2f} 秒，超过后端的 {args.timeout:g} 秒：后端会按超时处理（UPSTREAM_TIMEOUT）。",
                "     调大 RECIPE_DETECT_CONFIG.timeout，或者查识别服务为什么慢（按图片链接识别时，包括它自己下载图片的时间）。",
            ]
        lines = ["FAIL 网络是通的，是识别服务自己返回了错误（看上面的 HTTP 状态码和 message）。"]
        if payload.generated:
            lines.append("     这次用的是脚本生成的测试图，换一张真实的菜品照片再试：--image-file 或 --image-url。")
        return EXIT_SERVICE, lines

    if direct is not None and direct.from_service:
        lines = [f"FAIL 问题在代理：后端的 httpx 会读代理环境变量，访问 {host} 走了代理 {redact(proxy)}，不走代理就能到识别服务。"]
        if proxy_tcp is not None and not proxy_tcp.ok:
            lines.append(f"     代理本身就连不上：{proxy_tcp.text}。代理写成 127.0.0.1 / localhost 时，在容器里指的是容器自己。")
        if not proxy_variables():  # 代理来自系统设置（Windows / macOS），不是环境变量
            lines.append(f"     代理来自系统代理设置：在那里把 {host} 加进不走代理的例外，或者给后端设环境变量 NO_PROXY={host}。")
            return EXIT_CONNECT, lines
        lines += [
            "     在 docker-compose.server.yml 的 api.environment 里加上这两行（已经有 NO_PROXY 时，用逗号把地址加在后面），",
            "     然后 docker compose -f docker-compose.server.yml up -d 重建容器：",
            f'       NO_PROXY: "{host}"',
            f'       no_proxy: "{host}"',
            "     或者去掉容器里的代理变量：它们通常来自宿主机 ~/.docker/config.json 里的 proxies。",
        ]
        return EXIT_CONNECT, lines

    # 两条路都没到识别服务
    if direct is None and backend.status is not None:  # 没有代理，回复不是识别服务给的
        return EXIT_SERVICE, [f"FAIL {host}:{port} 上回 HTTP {backend.status} 的不是识别服务：可能是别的程序，或者路径 {path} 不对。"]
    if backend.status is not None:
        lines = [f"FAIL 走代理收到 HTTP {backend.status}，不是识别服务的回复（多半是代理 {redact(proxy)} 回的错误页）。"]
    elif direct_tcp.ok:
        lines = [f"FAIL 没有收到识别服务的回复：{backend.error}"]
    else:
        lines = [f"FAIL 连不上识别服务：{backend.error}"]
    if direct is not None and direct.status is not None:
        lines.append(f"     不走代理时 {host}:{port} 回 HTTP {direct.status}，也不是识别服务：可能是别的程序，或者路径 {path} 不对。")
    else:
        attempt = direct or backend
        lines += tcp_hints(direct_tcp, host, port, path, args.timeout, args.max_wait, container, gateway, gateway_tcp, attempt)
    if proxy is not None:
        lines.append(f"     另外，后端会走代理 {redact(proxy)}，直连修好以后，还要用 NO_PROXY 让 {host} 不走代理。")
        if proxy_tcp is not None and not proxy_tcp.ok:
            lines.append(f"     代理本身也连不上：{proxy_tcp.text}。")
    return EXIT_CONNECT, lines


# ---- 入口 ---------------------------------------------------------------------------

def configure_stdio():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", line_buffering=True)
        elif hasattr(stream, "buffer"):  # Python 3.6
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8", line_buffering=True))


def parse_args(argv):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"识别接口地址，默认 {DEFAULT_URL}")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help="后端的时限 RECIPE_DETECT_CONFIG.timeout（秒）：超过它后端就按超时处理，TCP 连接也只等这么久。默认 5",
    )
    parser.add_argument(
        "--max-wait", type=float, default=DEFAULT_MAX_WAIT,
        help="本脚本一次调用最多等多久（秒），比后端久一些，好看出到底要多久。默认 15",
    )
    image = parser.add_mutually_exclusive_group()
    image.add_argument("--image-url", help="发图片链接（JSON image_url），识别服务自己下载")
    image.add_argument("--image-file", help="上传这张图片（multipart file）")
    args = parser.parse_args(argv)
    try:
        parts = urlsplit(args.url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:  # 端口不是数字或者超出范围
        valid = False
    if not valid:
        parser.error(f"--url 要是 http(s) 地址，现在是 {args.url!r}")
    if not all(math.isfinite(value) and value > 0 for value in (args.timeout, args.max_wait)):
        parser.error("--timeout 和 --max-wait 都要是大于 0 的秒数")
    args.max_wait = max(args.max_wait, args.timeout)
    return args


def main(argv=None):
    configure_stdio()
    args = parse_args(argv)
    parts = urlsplit(args.url)
    host, path = parts.hostname, parts.path or "/"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    trace_id = "recipe-check-" + uuid.uuid4().hex[:12]
    try:
        payload = Payload(args.image_url, args.image_file, trace_id)
    except OSError as exc:
        print(f"FAIL 读不了图片 {args.image_file}：{exc}")
        return EXIT_ARGS
    container = container_hint()

    print("=== 运行环境 ===")
    print(f"Python {platform.python_version()}，主机名 {socket.gethostname()}，"
          + (f"在容器里（{container}）" if container else "不在容器里"))
    if httpx is not None:
        print(f"HTTP 客户端：httpx {httpx.__version__}，和后端一样")
    else:
        print("HTTP 客户端：标准库 urllib（没装 httpx；后端用的是 httpx，这里按 httpx 的规则选代理）")
    print(f"识别接口：{args.url}")
    print(f"请求：{payload.describe}，traceId={trace_id}")
    print(f"后端的时限 {args.timeout:g} 秒（RECIPE_DETECT_CONFIG.timeout），本脚本最多等 {args.max_wait:g} 秒")

    print("\n=== 1. 代理环境变量（后端的 httpx 会读它们） ===")
    variables = proxy_variables()
    print("\n".join(variables) if variables else "没有 *_proxy 环境变量")
    proxy = proxy_for(args.url)
    proxy_tcp = None
    if proxy is None:
        print(f"=> 后端访问 {host} 会直连")
    else:
        origin = "" if variables else "（来自系统代理设置）"
        print(f"=> 后端访问 {host} 会走代理 {redact(proxy)}{origin}")
        proxy_parts = urlsplit(proxy)
        try:
            proxy_port = proxy_parts.port or {"https": 443, "socks5": 1080, "socks5h": 1080}.get(proxy_parts.scheme, 80)
        except ValueError:
            proxy_port = None
        if proxy_parts.hostname and proxy_port:
            proxy_tcp = tcp_check(proxy_parts.hostname, proxy_port, args.timeout)
            print(f"{mark(proxy_tcp.ok)} 代理 {proxy_parts.hostname}:{proxy_port}：{proxy_tcp.text}")

    print("\n=== 2. TCP 直连（不走代理） ===")
    if not is_ip(host):
        print(resolve(host, port))
    direct_tcp = tcp_check(host, port, args.timeout)
    print(f"{mark(direct_tcp.ok)} {host}:{port}：{direct_tcp.text}")
    gateway = default_gateway() if container else None
    gateway_tcp = None
    if gateway and gateway != host:
        gateway_tcp = tcp_check(gateway, port, min(args.timeout, 3.0))
        print(f"{mark(gateway_tcp.ok)} 容器网关（宿主机）{gateway}:{port}：{gateway_tcp.text}")

    print("\n=== 3. 调用识别接口 ===")
    backend = post(args.url, payload, True, args.timeout, args.max_wait)
    flag, text = backend.summary(args.timeout)
    print(f"{flag} 按后端的方式（读代理变量）：{text}")
    if httpx is not None:
        print(f"     后端日志里对应：{backend_log(backend, args.timeout)}")
    direct = None
    if proxy is not None:
        direct = post(args.url, payload, False, args.timeout, args.max_wait)
        flag, text = direct.summary(args.timeout)
        print(f"{flag} 不走代理：{text}")

    print("\n=== 结论 ===")
    code, lines = conclude(args, host, port, path, payload, container, proxy, proxy_tcp, direct_tcp,
                           gateway, gateway_tcp, backend, direct)
    print("\n".join(lines))
    if not container:
        print("INFO 这次不在容器里运行。后端在容器里时，进容器再跑一次（命令见脚本开头），两边结果不同，问题就在容器的网络或者环境变量。")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消菜谱识别连通性检查。")
        raise SystemExit(130)
