"""使用后端的 Nacos 设置和客户端检查远端配置是否可读。

连接参数来自进程环境变量和 backend/.env，已设置的环境变量优先。
对 NACOS_DATA_ID_LIST 中的每个 dataId 执行一轮检查，不轮询或回退本地 YAML。

在 backend 目录使用后端的 Python 环境运行：
  python scripts/check_nacos_connectivity.py
  python scripts/check_nacos_connectivity.py --offline

退出码：0 成功；2 参数/依赖错误或 Nacos 未启用；3 连接/认证/读取失败；
4 配置缺失或 YAML 无效。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.core.config.settings import NacosSettings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CONNECT = 3
EXIT_DOCUMENT = 4


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def check_connection(settings: NacosSettings) -> int:
    from app.core.config.loader import parse_config_text
    from app.core.config.nacos import NacosConfigClient, NacosConfigError

    started = perf_counter()
    try:
        client = NacosConfigClient(settings)
    except Exception as exc:
        print(f"FAIL 无法创建 Nacos 客户端（{type(exc).__name__}）。")
        return EXIT_CONFIG

    failed_reads = invalid_documents = 0
    try:
        for data_id in settings.data_ids:
            label = f"dataId={data_id}"
            try:
                content = client.get_config(data_id)
            except NacosConfigError as exc:
                # 客户端的响应正文可能含有凭据，只保留它生成的错误摘要。
                detail = str(exc).partition(" | response=")[0]
                failed_reads += 1
                print(f"FAIL 配置读取失败 | {label} | {detail}")
                continue
            except Exception as exc:
                # requests 的异常可能包含带 accessToken 的请求 URL。
                failed_reads += 1
                print(f"FAIL 配置读取失败 | {label} | {type(exc).__name__}")
                continue
            if content is None:
                invalid_documents += 1
                print(f"FAIL 配置不存在或内容为空 | {label} | 请核对 namespace、group 和 dataId。")
                continue
            try:
                document = parse_config_text(content)
            except Exception as exc:
                invalid_documents += 1
                # YAML 异常可能包含配置原文，只报告类型和位置。
                mark = getattr(exc, "problem_mark", None)
                location = f" line={mark.line + 1} column={mark.column + 1}" if mark is not None else ""
                print(f"FAIL 配置须为有效的 YAML 映射 | {label} | {type(exc).__name__}{location}")
                continue
            print(f"OK  已读取配置 | {label} | bytes={len(content.encode('utf-8'))} top_level_keys={len(document)}")
    finally:
        try:
            client.close()
        except Exception as exc:
            print(f"WARN Nacos 客户端关闭失败（{type(exc).__name__}）。")

    if failed_reads or invalid_documents:
        print(f"FAIL Nacos 检查未通过：读取失败 {failed_reads}，配置缺失或无效 {invalid_documents}。")
        return EXIT_CONNECT if failed_reads else EXIT_DOCUMENT
    elapsed_ms = (perf_counter() - started) * 1000
    print(f"PASS Nacos 全部 {len(settings.data_ids)} 个配置读取成功，耗时 {elapsed_ms:.0f} ms。")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--offline", action="store_true", help="仅检查连接参数，不访问 Nacos")
    args = parser.parse_args(argv)
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    try:
        from app.core.config.settings import settings
        from app.core.config.nacos import _server_address
    except ValueError as exc:
        # Settings 的校验异常可能带上错误的环境变量原值，只报告变量名。
        names = re.findall(r"\bNACOS_[A-Z0-9_]+\b", str(exc).partition(", got ")[0])
        fields = f"（{', '.join(dict.fromkeys(names))}）" if names else ""
        print(f"FAIL 后端设置无效{fields}，请检查进程环境变量和 backend/.env。")
        return EXIT_CONFIG
    except ImportError as exc:
        print(f"FAIL 无法加载后端设置或客户端（{type(exc).__name__}），请使用后端的 Python 环境。")
        return EXIT_CONFIG

    nacos = settings.nacos
    if not nacos.enabled:
        print("FAIL NACOS_ENABLED=false，当前未启用 Nacos。")
        return EXIT_CONFIG

    try:
        endpoint = urlsplit(_server_address(nacos.server_addr))
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.port == 0:
            raise ValueError("invalid HTTP(S) server address")
    except (ValueError, RuntimeError):
        print("FAIL NACOS_SERVER_ADDR 无效，请检查 HTTP(S) 地址和端口。")
        return EXIT_CONFIG

    details = {
        "env": settings.application.env,
        "server_address": endpoint._replace(
            netloc=endpoint.netloc.rsplit("@", 1)[-1], query="", fragment=""
        ).geturl(),
        "namespace": nacos.namespace,
        "group": nacos.group,
        "data_ids": nacos.data_ids,
        "auth_enabled": bool(nacos.username or nacos.password),
        "connect_timeout_seconds": nacos.connect_timeout_seconds,
        "request_timeout_seconds": nacos.request_timeout_seconds,
    }
    print("Nacos 设置来自进程环境变量和 backend/.env，已设置的环境变量优先。")
    print("NACOS_CONFIG=" + json.dumps(details, ensure_ascii=False))
    if args.offline:
        print("OFFLINE 参数检查通过，未访问 Nacos。")
        return EXIT_OK
    return check_connection(nacos)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消 Nacos 连接检查。")
        raise SystemExit(130)
