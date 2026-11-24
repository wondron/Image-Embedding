"""检查 Qdrant 服务是否可以连接并完成 API 鉴权。"""

from __future__ import annotations

import sys
from typing import Any

try:
    from qdrant_client import QdrantClient
except ImportError:
    print("缺少依赖 qdrant-client，请先执行：pip install qdrant-client")
    sys.exit(1)


# ==================== 直接修改这里 ====================
QDRANT_URL = "https://robam-ai-qdrant.myroki.com"
QDRANT_API_KEY = "27hp6qjQRRqCo7-GZMMymeDJu6FRZmSDukEHqJxt7gqrgZupOtsMTqcvDuyFI2Nd"
TIMEOUT_SECONDS = 10
# =====================================================


def mask_api_key(api_key: str) -> str:
    """仅用于输出脱敏后的 API Key，避免把完整密钥打印到终端。"""
    if not api_key:
        return "未设置"
    if len(api_key) <= 8:
        return "********"
    return f"{api_key[:4]}...{api_key[-4:]}"


def main() -> int:
    if not QDRANT_URL or "your-qdrant-domain.com" in QDRANT_URL:
        print("错误：请先填写 QDRANT_URL")
        return 1

    if not QDRANT_API_KEY or QDRANT_API_KEY == "your-api-key":
        print("错误：请先填写 QDRANT_API_KEY")
        return 1

    print("Qdrant 连接检测")
    print(f"URL     : {QDRANT_URL}")
    print(f"API Key : {mask_api_key(QDRANT_API_KEY)}")
    print(f"超时时间: {TIMEOUT_SECONDS} 秒")
    print()

    client: Any = None
    try:
        client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            timeout=TIMEOUT_SECONDS,
            prefer_grpc=False,
        )

        # 获取集合列表，同时验证网络连通性和 API Key 鉴权。
        response = client.get_collections()
        collections = getattr(response, "collections", [])

        print("[成功] Qdrant 连接正常，API Key 鉴权成功")
        print(f"[信息] 当前集合数量：{len(collections)}")

        if collections:
            print("[信息] 集合列表：")
            for collection in collections:
                print(f"  - {collection.name}")
        else:
            print("[信息] 当前没有集合")

        return 0

    except Exception as exc:
        error_type = type(exc).__name__
        print(f"[失败] Qdrant 连接检测失败：{error_type}")
        print(f"[详情] {exc}")
        print()
        print("排查方向：")
        print("1. 确认 URL 包含 http:// 或 https://，并且端口正确")
        print("2. 确认 API Key 没有复制错误或过期")
        print("3. 确认服务器安全组、防火墙和反向代理放行了 Qdrant 端口")
        print("4. 如果使用 HTTPS，确认域名证书和反向代理配置正常")
        return 2

    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
