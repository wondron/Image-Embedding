"""使用后端生效的 QDRANT_CONFIG 检查真实 Qdrant 连接，只执行读取操作。

与 check_oss_connectivity.py 一样，读取本地 YAML，再由 Nacos 逐键覆盖；
复用生产 QdrantConnection 创建客户端，沿用 API Key、超时和 prefer_grpc 配置。
本脚本不依赖 PostgreSQL 或模型，不创建 collection、不写入或删除向量。

在 backend 目录、使用后端同一 Python 环境运行：
  python scripts/check_qdrant_connectivity.py
  python scripts/check_qdrant_connectivity.py --offline
  python scripts/check_qdrant_connectivity.py --collection my_collection

--offline 仍按后端规则加载配置（可能访问 Nacos），但不访问 Qdrant。
退出码：0 成功；2 配置或依赖错误；3 连接/列表查询失败；4 指定 collection 查询失败。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CONNECT = 3
EXIT_COLLECTION = 4


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def load_effective_qdrant_config() -> tuple[dict[str, Any], dict[str, Any]]:
    from app.core.config.nacos import NacosConfigProvider
    from app.core.config.settings import settings

    provider = NacosConfigProvider(settings.nacos)
    try:
        return provider.load(settings.config.yaml_path)
    finally:
        provider.close()


def parse_connection(config: Mapping[str, Any]) -> Any:
    from app.clients.qdrant import QdrantConnection

    section = config.get("QDRANT_CONFIG")
    if not isinstance(section, Mapping):
        raise ValueError("QDRANT_CONFIG 缺失或不是 mapping")
    if not section.get("url") and not (section.get("host") and section.get("port")):
        raise ValueError("QDRANT_CONFIG 必须配置 url，或同时配置 host 和 port")
    connection = QdrantConnection.from_mapping(section)
    if connection.url == ":memory:":
        raise ValueError("QDRANT_CONFIG.url=:memory: 是进程内实例，无法用于检查服务器连通性")
    parsed = urlsplit(connection.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("QDRANT_CONFIG.url 必须是包含主机名的 HTTP(S) 地址")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("QDRANT_CONFIG.url 端口必须在 1 到 65535 之间")
    if not math.isfinite(connection.timeout) or connection.timeout <= 0:
        raise ValueError("QDRANT_CONFIG.timeout 必须是大于 0 的有限秒数")
    return connection


def summarize_effective(config: Mapping[str, Any], source: Mapping[str, Any]) -> list[str]:
    from app.bootstrap.dependency_logging import config_summary

    return [
        "=== 生效配置摘要（凭据已脱敏）===",
        f"config_source={source.get('source', 'unknown')}  nacos_profile={source.get('nacos_profile', '')}  "
        f"nacos_status={source.get('nacos_status', '')}  yaml_base={source.get('yaml_base')}",
        "QDRANT_CONFIG=" + json.dumps(config_summary(config, "QDRANT_CONFIG"), ensure_ascii=False, default=str),
    ]


def format_exception(exc: Exception, config: Mapping[str, Any]) -> str:
    from app.bootstrap.dependency_logging import redact_text

    return redact_text(f"{type(exc).__name__}: {exc}", config)


def failure_hint(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (401, 403) or any(word in text for word in ("unauthenticated", "permission_denied", "unauthorized", "forbidden")):
        return "请检查 QDRANT_CONFIG.api_key 和该密钥的读取权限。"
    return (
        "请检查服务状态、地址、端口和网络/防火墙。prefer_grpc=true 时还需保证 gRPC 端口可达"
        "（通常 REST 为 6333、gRPC 为 6334，沿用生产客户端配置）。"
        "Compose 服务名仅适用于同一 Docker 网络；在宿主机检查时需使用宿主机可访问的地址。"
    )


def check_connection(connection: Any, config: Mapping[str, Any], collection: str | None) -> int:
    started = perf_counter()
    try:
        client = connection.create_client()
    except Exception as exc:
        print(f"FAIL 无法按生产 QdrantConnection 创建客户端: {format_exception(exc, config)}")
        return EXIT_CONFIG
    try:
        try:
            response = client.get_collections()
            names = [item.name for item in response.collections]
        except Exception as exc:
            print(f"FAIL Qdrant get_collections 失败: {format_exception(exc, config)}")
            print(failure_hint(exc))
            return EXIT_CONNECT
        print(f"OK  已访问 Qdrant get_collections，collections={len(names)}")
        from app.bootstrap.dependency_logging import redact_text

        print("collection_names=" + redact_text(json.dumps(names[:20], ensure_ascii=False), config))
        if len(names) > 20:
            print(f"仅显示前 20 个名称，共 {len(names)} 个。")
        if collection is not None:
            try:
                info = client.get_collection(collection_name=collection)
            except Exception as exc:
                print(f"FAIL 指定 collection 查询失败: {format_exception(exc, config)}")
                print("Qdrant 列表查询已成功；请检查 collection 名称及该 collection 的读取权限。")
                return EXIT_COLLECTION
            status = getattr(info.status, "value", info.status)
            print(redact_text(f"OK  collection={collection} status={status} points_count={info.points_count}", config))
        elapsed = (perf_counter() - started) * 1000
        print(f"PASS Qdrant 连接及读取检查成功，耗时 {elapsed:.0f} ms（未检查写入权限）。")
        return EXIT_OK
    finally:
        try:
            client.close()
        except Exception as exc:
            print(f"WARN Qdrant 客户端关闭失败: {format_exception(exc, config)}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--offline", action="store_true", help="仅输出生效配置和前置检查，不连接 Qdrant")
    parser.add_argument("--collection", help="额外读取指定 collection 的状态和向量数量")
    args = parser.parse_args(argv)
    collection = args.collection.strip() if args.collection is not None else None
    if collection == "":
        parser.error("--collection 不能为空")
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    os.chdir(BACKEND_ROOT)
    try:
        config, source = load_effective_qdrant_config()
        for line in summarize_effective(config, source):
            print(line)
    except Exception as exc:
        # YAML errors may echo complete source lines containing credentials.
        print(f"FAIL 无法加载生效配置或依赖（{type(exc).__name__}），请检查配置文件、Nacos 和后端 Python 环境。")
        return EXIT_CONFIG
    try:
        connection = parse_connection(config)
    except Exception as exc:
        print(f"FAIL Qdrant 配置前置检查失败: {format_exception(exc, config)}")
        return EXIT_CONFIG
    if args.offline:
        print("OFFLINE 配置前置检查通过。未访问 Qdrant，不能据此判断连通性。")
        return EXIT_OK
    return check_connection(connection, config, collection)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消 Qdrant 连接检查。")
        raise SystemExit(130)
