"""直接给 Qdrant 地址和 API Key，检查连不连得通；不读 Nacos 和本地 YAML（比如切换前先测新库）。

REST 和 gRPC 各测一遍，用后端同一个客户端（QdrantConnection.create_client）：读版本号、列出 collection
（这一步要鉴权，key 不对会在这里失败），给了 --collection 时再读它的状态和点数。
只读：不建 collection、不写入、不删除。

后端默认 prefer_grpc=true，检索和入库走 gRPC，端口固定 6334（客户端不跟着地址里的端口走）；
迁移脚本 migrate_qdrant_snapshots.sh 走 REST（地址里的端口，一般 6333）。两条都通才能放心切换。

在 backend 目录、用后端同一 Python 环境运行：
  python scripts/check_qdrant_endpoint.py http://10.0.0.8:6333 --api-key <key>
  python scripts/check_qdrant_endpoint.py http://10.0.0.8:6333 --api-key <key> --collection <名字>
  QDRANT_API_KEY=<key> python scripts/check_qdrant_endpoint.py http://10.0.0.8:6333    key 不留在命令历史里
服务器上在 API 容器里跑，测的就是后端出去的那条网络：
  docker cp backend/scripts/check_qdrant_endpoint.py roki-imageembedding:/app/scripts/
  docker exec roki-imageembedding python scripts/check_qdrant_endpoint.py http://新地址:6333 --api-key <key>
退出码：0 两条都通；2 参数错误或缺依赖；3 连不上或 key 不对；4 指定的 collection 读取失败。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import warnings
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPTS_DIR.parent
for _path in (BACKEND_ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import check_qdrant_connectivity as base  # noqa: E402  地址检查、脱敏、失败提示和它共用

# 后端不配 port / grpc_port，用的是 qdrant-client 的默认值；地址里写了端口时 REST 用地址里的
REST_PORT, GRPC_PORT = 6333, 6334


def probe(connection: Any, config: dict[str, Any], collection: str | None) -> int:
    started = perf_counter()
    try:
        client = connection.create_client()
    except Exception as exc:
        print(f"FAIL 建不了客户端: {base.format_exception(exc, config)}")
        return base.EXIT_CONFIG
    try:
        try:
            version = client.info().version
            names = [item.name for item in client.get_collections().collections]
        except Exception as exc:
            print(f"FAIL {base.format_exception(exc, config)}")
            print(base.failure_hint(exc))
            return base.EXIT_CONNECT
        elapsed = (perf_counter() - started) * 1000
        print(f"OK   Qdrant {version}，{elapsed:.0f} ms；{len(names)} 个 collection：{', '.join(names) or '无'}")
        if collection is not None:
            try:
                info = client.get_collection(collection_name=collection)
            except Exception as exc:
                print(f"FAIL 读不了 collection {collection}: {base.format_exception(exc, config)}")
                return base.EXIT_COLLECTION
            status = getattr(info.status, "value", info.status)
            print(f"OK   {collection}：status={status} points_count={info.points_count}")
        return base.EXIT_OK
    finally:
        try:
            client.close()
        except Exception as exc:
            print(f"WARN 关闭客户端失败: {base.format_exception(exc, config)}")


def summary(rest: int, grpc: int) -> str:
    if rest == grpc == base.EXIT_OK:
        return "PASS REST 和 gRPC 都通：这个地址和 key 可以写进 QDRANT_CONFIG。"
    if rest == base.EXIT_OK and grpc == base.EXIT_CONNECT:
        return (
            f"FAIL REST 通、gRPC 不通：迁移脚本能用，后端默认走 gRPC 会连不上。放通 {GRPC_PORT} 端口，"
            "或者在 QDRANT_CONFIG 里设 prefer_grpc: false。"
        )
    if grpc == base.EXIT_OK and rest == base.EXIT_CONNECT:
        return "FAIL gRPC 通、REST 不通：后端检索能连，迁移脚本 migrate_qdrant_snapshots.sh 走 REST 连不上。"
    return "FAIL 见上面每条的报错。"


def main(argv: list[str] | None = None) -> int:
    base.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="Qdrant 的 REST 地址，比如 http://10.0.0.8:6333")
    parser.add_argument("--api-key", default=os.environ.get("QDRANT_API_KEY"), help="不传时读环境变量 QDRANT_API_KEY；没开鉴权就都不设")
    parser.add_argument("--timeout", type=float, default=5.0, help="每个请求最多等几秒；默认 5")
    parser.add_argument("--collection", help="额外读这个 collection 的状态和点数")
    args = parser.parse_args(argv)
    collection = args.collection.strip() if args.collection is not None else None
    if collection == "":
        parser.error("--collection 不能为空")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout 要大于 0")
    # 没有 TLS 时带 key 每个客户端都会警告一遍；后端也是这样连的，这里不重复刷屏
    warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

    section = {"url": args.url.strip(), "api_key": args.api_key or None, "timeout": args.timeout}
    codes = {}
    for name, grpc in (("REST", False), ("gRPC", True)):
        config = {"QDRANT_CONFIG": {**section, "prefer_grpc": grpc}}
        try:
            connection = base.parse_connection(config)
        except Exception as exc:
            print(f"FAIL 地址不对: {base.format_exception(exc, config)}")
            return base.EXIT_CONFIG
        port = GRPC_PORT if grpc else urlsplit(connection.url).port or REST_PORT
        print(f"=== {name}（端口 {port}{'，后端走这条' if grpc else '，迁移脚本走这条'}）")
        codes[name] = probe(connection, config, collection)
    print()
    print(summary(codes["REST"], codes["gRPC"]))
    failed = [code for code in codes.values() if code != base.EXIT_OK]
    return min(failed) if failed else base.EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消 Qdrant 连接检查。")
        raise SystemExit(130)
