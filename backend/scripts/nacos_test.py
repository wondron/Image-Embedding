
import json
import socket
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# Nacos 配置：直接修改这里即可，不需要传入 args
# ============================================================

NACOS_SERVER_ADDR = "http://ai-nacos.myroki.com:8848"
NACOS_GROUP = "ROKI_AI_GROUP"
NACOS_NAMESPACE = "roki-ai-agent-test"
NACOS_USERNAME = "roki-ai-agent-test"
NACOS_PASSWORD = "3@JhLk5s#dF9m"

# 超时时间（秒）
TIMEOUT = 5


# ============================================================
# HTTP 请求
# ============================================================

def http_request(url, method="GET", data=None):
    try:
        headers = {}

        if data is not None:
            data = urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = Request(
            url,
            data=data,
            headers=headers,
            method=method
        )

        with urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.read().decode(
                "utf-8", errors="replace"
            )

    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")

    except (URLError, TimeoutError, OSError) as e:
        return None, str(e)


# ============================================================
# DNS 检测
# ============================================================

def check_dns(host, port):
    print("\n[1] DNS 域名解析")

    try:
        results = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )

        ips = sorted({item[4][0] for item in results})

        print(f"  [成功] {host}")
        print(f"  解析 IP: {ips}")
        return True

    except Exception as e:
        print(f"  [失败] DNS 解析失败: {e}")
        return False


# ============================================================
# TCP 端口检测
# ============================================================

def check_tcp(host, port, name):
    print(f"\n[2] TCP 端口检测: {host}:{port} ({name})")

    try:
        with socket.create_connection(
            (host, port), timeout=TIMEOUT
        ):
            print(f"  [成功] TCP {port} 端口可以连接")
            return True

    except Exception as e:
        print(f"  [失败] TCP {port} 端口连接失败: {e}")
        return False


# ============================================================
# Nacos 登录
# ============================================================

def check_login(base_url):
    print("\n[3] Nacos 用户认证")

    url = f"{base_url}/v1/auth/users/login"

    status, body = http_request(
        url,
        method="POST",
        data={
            "username": NACOS_USERNAME,
            "password": NACOS_PASSWORD
        }
    )

    if status != 200:
        print(f"  [失败] 登录失败，HTTP 状态码: {status}")
        print(f"  响应内容: {body[:500]}")
        return None

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        print("  [失败] 登录响应不是有效 JSON")
        print(f"  响应内容: {body[:500]}")
        return None

    token = result.get("accessToken")

    if not token:
        print("  [失败] 登录成功响应中没有 accessToken")
        print(f"  响应内容: {body[:500]}")
        return None

    print("  [成功] Nacos 登录成功")
    print(f"  用户名: {NACOS_USERNAME}")
    print(f"  Token TTL: {result.get('tokenTtl', '未知')}")

    return token


# ============================================================
# Namespace 检测
# ============================================================

def check_namespace(base_url, token):
    print("\n[4] Namespace 检测")

    url = (
        f"{base_url}/v1/console/namespaces?"
        + urlencode({"accessToken": token})
    )

    status, body = http_request(url)

    if status != 200:
        print(f"  [失败] Namespace 查询失败，HTTP: {status}")
        print(f"  响应内容: {body[:500]}")
        return False

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        print("  [失败] Namespace 响应格式异常")
        return False

    namespaces = result.get("data", [])

    if not isinstance(namespaces, list):
        print("  [失败] Namespace 数据格式异常")
        return False

    for item in namespaces:
        namespace_id = (
            item.get("namespace")
            or item.get("namespaceId")
            or item.get("namespace_id")
        )

        if namespace_id == NACOS_NAMESPACE:
            print("  [成功] Namespace 存在")
            print(f"  Namespace: {NACOS_NAMESPACE}")
            print(f"  名称: {item.get('namespaceShowName', '')}")
            return True

    print("  [失败] 未找到指定 Namespace")
    print(f"  目标 Namespace: {NACOS_NAMESPACE}")

    available = [
        item.get("namespace")
        for item in namespaces
    ]

    print(f"  当前可用 Namespace: {available}")

    return False


# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 60)
    print("                  Nacos 连通性检测")
    print("=" * 60)

    print(f"Server    : {NACOS_SERVER_ADDR}")
    print(f"Group     : {NACOS_GROUP}")
    print(f"Namespace : {NACOS_NAMESPACE}")
    print(f"Username  : {NACOS_USERNAME}")

    parsed = urlsplit(NACOS_SERVER_ADDR.rstrip("/"))

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        print("\n[错误] NACOS_SERVER_ADDR 配置不正确")
        return

    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # 支持默认 /nacos 路径，也兼容代理直接映射根路径
    if parsed.path and parsed.path != "/":
        paths = [parsed.path.rstrip("/")]
    else:
        paths = ["/nacos", ""]

    # 1. DNS
    dns_ok = check_dns(host, port)

    if not dns_ok:
        print("\n[最终结果] DNS 解析失败，检测终止")
        return

    # 2. TCP
    tcp_http_ok = check_tcp(host, port, "HTTP")
    tcp_grpc_ok = check_tcp(host, port + 1000, "gRPC")

    # 3. HTTP 登录，自动尝试上下文路径
    token = None
    base_url = None

    for path in paths:
        candidate = origin + path

        print(f"\n  尝试服务路径: {candidate}")

        token = check_login(candidate)

        if token:
            base_url = candidate
            break

    if not token:
        print("\n" + "=" * 60)
        print("              Nacos 连接检测失败")
        print("=" * 60)
        print(f"HTTP 端口: {'正常' if tcp_http_ok else '异常'}")
        print(f"gRPC 端口: {'正常' if tcp_grpc_ok else '异常'}")
        print("用户认证: 失败")
        return

    # 4. Namespace
    namespace_ok = check_namespace(base_url, token)

    # 5. 最终结果
    print("\n" + "=" * 60)
    print("                  检测结果汇总")
    print("=" * 60)

    print(f"DNS 解析       : {'成功' if dns_ok else '失败'}")
    print(f"HTTP 8848      : {'成功' if tcp_http_ok else '失败'}")
    print(f"gRPC {port + 1000:<7}: {'成功' if tcp_grpc_ok else '失败'}")
    print(f"用户认证       : 成功")
    print(f"Namespace      : {'成功' if namespace_ok else '失败'}")
    print(f"Group          : {NACOS_GROUP}")
    print(f"服务地址       : {base_url}")

    if tcp_http_ok and tcp_grpc_ok and namespace_ok:
        print("\n[最终结果] Nacos 连接检测全部通过")
    else:
        print("\n[最终结果] Nacos 部分检测未通过，请检查配置")

    print("=" * 60)


if __name__ == "__main__":
    main()
