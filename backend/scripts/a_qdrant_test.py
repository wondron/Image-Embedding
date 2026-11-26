
import os
import socket
import requests
from urllib.parse import urlparse
from qdrant_client import QdrantClient

# 如果使用 .env 文件，自动加载
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ==================== Qdrant 配置 ====================

QDRANT_CONFIG = {
    "url": "https://robam-ai-qdrant-test.myroki.com:443",
    "api_key": "1bcd6ced46b62e3d869a81b6d5bed3210ddb965b92f41bd400a332c6a0db499e",
    "timeout": 5,
}


# ==================== 基础信息 ====================

def get_server_info():
    url = QDRANT_CONFIG["url"].strip()
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Qdrant URL 格式错误: {url}")

    host = parsed.hostname
    port = parsed.port or (
        443 if parsed.scheme == "https" else 6333
    )

    return host, port


# ==================== DNS 检测 ====================

def check_dns(host):
    print("\n[1] DNS 域名解析")

    try:
        addresses = socket.getaddrinfo(host, None)
        ips = list(set(item[4][0] for item in addresses))

        print(f"  [成功] {host}")
        print(f"  解析 IP: {ips}")

        return True

    except Exception as e:
        print(f"  [失败] DNS 解析失败: {e}")
        return False


# ==================== TCP 端口检测 ====================

def check_tcp(host, port):
    print(f"\n[2] TCP 端口检测: {host}:{port}")

    try:
        with socket.create_connection(
            (host, port),
            timeout=QDRANT_CONFIG["timeout"]
        ):
            print(f"  [成功] TCP {port} 端口可以连接")
            return True

    except Exception as e:
        print(f"  [失败] TCP {port} 端口连接失败: {e}")
        return False


# ==================== HTTPS 健康检测 ====================

def check_http():
    print("\n[3] Qdrant HTTPS 服务检测")

    url = QDRANT_CONFIG["url"].rstrip("/") + "/healthz"

    headers = {}
    if QDRANT_CONFIG["api_key"]:
        headers["api-key"] = QDRANT_CONFIG["api_key"]

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=QDRANT_CONFIG["timeout"]
        )

        print(f"  HTTP 状态码: {response.status_code}")
        print(f"  响应内容: {response.text[:200]}")

        if response.status_code == 200:
            print("  [成功] Qdrant HTTPS 服务正常")
            return True

        print("  [提示] 健康检查接口未正常返回")
        return False

    except Exception as e:
        print(f"  [失败] HTTPS 请求异常: {e}")
        return False


# ==================== Qdrant API 认证及连接 ====================

def check_qdrant():
    print("\n[4] Qdrant API 身份认证及连接")

    api_key = QDRANT_CONFIG["api_key"]

    if not api_key:
        print("  [失败] 未设置 QDRANT_API_KEY")
        return False

    client = None

    try:
        client = QdrantClient(
            url=QDRANT_CONFIG["url"],
            api_key=api_key,
            timeout=QDRANT_CONFIG["timeout"],
            prefer_grpc=False,
        )

        # 实际调用 Qdrant API，验证认证和服务可用性
        result = client.get_collections()

        collections = result.collections

        print("  [成功] Qdrant API 连接成功")
        print("  [成功] API Key 认证通过")
        print(f"  Collection 数量: {len(collections)}")

        for collection in collections:
            print(f"    - {collection.name}")

        return client

    except Exception as e:
        print(f"  [失败] Qdrant API 请求失败: {e}")

        if client:
            client.close()

        return False


# ==================== 主程序 ====================

def main():

    config = QDRANT_CONFIG

    print("=" * 55)
    print("             Qdrant 连通性检测")
    print("=" * 55)

    print(f"URL      : {config['url']}")
    print(f"Timeout  : {config['timeout']}s")
    print(f"API Key  : {'已配置' if config['api_key'] else '未配置'}")

    try:
        host, port = get_server_info()
    except Exception as e:
        print(f"\n配置错误: {e}")
        return

    # 1. DNS
    if not check_dns(host):
        print("\n检测结束：DNS 解析失败")
        return

    # 2. TCP
    if not check_tcp(host, port):
        print("\n检测结束：Qdrant TCP 端口无法连接")
        return

    # 3. HTTPS
    check_http()

    # 4. API 认证及 Collection 读取
    client = check_qdrant()

    if not client:
        print("\n检测结束：Qdrant API 连接失败")
        return

    client.close()

    print("\n" + "=" * 55)
    print("             Qdrant 检测完成")
    print("=" * 55)


if __name__ == "__main__":
    main()
