"""使用后端相同的 NacosSettings / NacosConfigClient 检查认证及配置读取。

连接参数直接修改本脚本顶部 NACOS_CONFIG_PROFILES，NACOS_PROFILE 选择 prod/test。
超时和启用开关沿用后端 NacosSettings；认证和读取复用生产客户端。
直接检查选定环境的全部 dataId，不回退本地 YAML；只读取，不发布或修改配置。
不依赖 PostgreSQL、Qdrant、对象存储或模型，不打印密码、Token 或配置正文。

在 backend 目录、使用后端同一 Python 环境运行：
  python scripts/check_nacos_connectivity.py
  python scripts/check_nacos_connectivity.py --offline

--offline 仅检查参数，不访问 Nacos。NACOS_ENABLED=false 时前置检查失败。
退出码：0 成功；2 参数/依赖错误；3 连接/认证/读取失败；4 配置缺失或 YAML 无效。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

# ===== 直接在这里修改连接参数及环境，无需命令行传参 =====
NACOS_PROFILE = "prod"  # 改成 "test" 检查测试环境
NACOS_CONFIG_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "profile": "prod",
        "server_addresses": "http://nacos.myroki.com:8848",
        "namespace": "239acdd2-c154-4d07-ba96-6d52ebc0dccb",
        "username": "roki-ai-prod",
        "password": "IsAbprjbEGj2ME77jxiOR",
        "group": "ROKI_AI_GROUP",
        "data_id_list": ["roki-ai-image-search"],
    },
    {
        "profile": "test",
        "server_addresses": "http://nacos.myroki.com:8848",
        "namespace": "adfc3ec2-a1ea-4b31-9b1a-fafb4228c8ca",
        "username": "roki-ai-test",
        "password": "Q65ILEBfsAmqnPbJqcSig",
        "group": "ROKI_AI_GROUP",
        "data_id_list": ["roki-ai-image-search"],
    },
)
# ======================================================

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CONNECT = 3
EXIT_DOCUMENT = 4
_URL_AUTH = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/]*@")
_SECRET_ASSIGN = re.compile(
    r'''(?i)(["']?\b(?:password|access_?token|authorization|secret|api[_-]?key)["']?\s*[:=]\s*)'''
    r'''(?:"[^"]*"|'[^']*'|[^\s,;&}]+)'''
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)\S+")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def sanitize_error_text(text: str, profile: Mapping[str, Any], token: str | None = None) -> str:
    text = _URL_AUTH.sub(r"\1***@", text)
    text = _SECRET_ASSIGN.sub(r"\1***", text)
    text = _BEARER.sub(r"\1***", text)
    secrets = set()
    for value in (profile.get("password"), token):
        if value:
            value = str(value)
            secrets.update((value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]))
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def load_nacos_settings() -> Any:
    from app.core.config.settings import NacosSettings

    return NacosSettings(profile=NACOS_PROFILE, profiles=NACOS_CONFIG_PROFILES)


def select_profile(settings: Any) -> Mapping[str, Any] | None:
    return next((item for item in settings.profiles if item.get("profile") == settings.profile), None)


def server_address(profile: Mapping[str, Any]) -> str:
    from app.core.config.nacos import _server_address

    return _server_address(profile.get("server_addresses", ""))


def summarize_effective(settings: Any, profile: Mapping[str, Any] | None) -> list[str]:
    lines = [
        "=== Nacos 生效配置摘要（凭据已脱敏）===",
        "config_source=脚本 NACOS_CONFIG_PROFILES / NACOS_PROFILE（超时和开关沿用后端）",
        f"enabled={settings.enabled}  profile={settings.profile}  poll_enabled={settings.poll_enabled}",
        f"connect_timeout_seconds={settings.connect_timeout_seconds}  request_timeout_seconds={settings.request_timeout_seconds}",
    ]
    if profile is None:
        return lines + ["selected_profile=missing"]
    try:
        address = server_address(profile)
    except Exception:
        address = "<invalid server_addresses>"
    details = {
        "server_address": address,
        "namespace": profile.get("namespace", ""),
        "group": profile.get("group", ""),
        "data_id_list": profile.get("data_id_list"),
        "username": profile.get("username", ""),
        "password": "***" if profile.get("password") else "",
    }
    for key, value in details.items():
        if isinstance(value, str):
            details[key] = sanitize_error_text(value, profile)
        elif isinstance(value, list):
            details[key] = [sanitize_error_text(str(item), profile) for item in value]
    return lines + ["NACOS_CONFIG=" + json.dumps(details, ensure_ascii=False)]


def prerequisite_failures(settings: Any, profile: Mapping[str, Any] | None) -> list[str]:
    reasons = []
    if not settings.enabled:
        reasons.append("NACOS_ENABLED=false：后端当前未启用 Nacos；启用后再执行连接检查。")
    if profile is None:
        return reasons + [f"未找到 NACOS_PROFILE={settings.profile} 对应的连接配置。"]
    try:
        parsed = urlsplit(server_address(profile))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("地址必须包含 HTTP(S) 协议和主机名")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间")
    except (ValueError, TypeError, AttributeError, RuntimeError):
        reasons.append("server_addresses 无效，请填写 HTTP(S) 地址并检查端口。")
    if not profile.get("group"):
        reasons.append("group 不能为空。")
    data_ids = profile.get("data_id_list")
    if not isinstance(data_ids, list) or not data_ids or any(not isinstance(item, str) or not item.strip() for item in data_ids):
        reasons.append("data_id_list 必须是包含非空 dataId 的列表。")
    if bool(profile.get("username")) != bool(profile.get("password")):
        reasons.append("认证参数未填全：username 和 password 应同时填写，或同时留空。")
    for key in ("connect_timeout_seconds", "request_timeout_seconds"):
        try:
            value = float(getattr(settings, key))
            if not math.isfinite(value) or value <= 0:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            reasons.append(f"{key} 必须是大于 0 的有限秒数。")
    return reasons


def check_connection(settings: Any, profile: Mapping[str, Any]) -> int:
    from app.core.config.loader import parse_config_text
    from app.core.config.nacos import NacosConfigClient

    started = perf_counter()
    try:
        client = NacosConfigClient(profile, settings)
    except Exception as exc:
        detail = sanitize_error_text(f"{type(exc).__name__}: {exc}", profile)
        print(f"FAIL 无法创建生产 NacosConfigClient: {detail}")
        return EXIT_CONFIG

    def failure(exc: Exception) -> str:
        return sanitize_error_text(f"{type(exc).__name__}: {exc}", profile, client._access_token)

    try:
        if profile.get("username") or profile.get("password"):
            try:
                client._authenticate()
            except Exception as exc:
                print(f"FAIL Nacos auth/login 认证失败: {failure(exc)}")
                print("请检查地址、网络、账号密码，以及 Nacos 是否支持生产客户端使用的认证接口。")
                return EXIT_CONNECT
            print("OK  Nacos auth/login 认证成功。")
        else:
            print("INFO 未配置账号密码，按生产客户端规则直接读取配置。")
        failed_reads = invalid_documents = 0
        for data_id in profile["data_id_list"]:
            label = sanitize_error_text(f"dataId={data_id} group={profile['group']} namespace={profile.get('namespace', '')}", profile)
            try:
                text = client.get_config(data_id)
            except Exception as exc:
                failed_reads += 1
                print(f"FAIL 配置读取失败 | {label} | message={failure(exc)}")
                continue
            if text is None:
                invalid_documents += 1
                print(f"FAIL 配置不存在或内容为空 | {label} | 请核对 namespace、group 和 dataId。")
                continue
            try:
                document = parse_config_text(text, "Nacos configuration")
            except Exception as exc:
                invalid_documents += 1
                # YAML 异常里包含源文件的行；只报告类型和位置。
                mark = getattr(exc, "problem_mark", None)
                location = f" line={mark.line + 1} column={mark.column + 1}" if mark is not None else ""
                print(f"FAIL 配置须为有效 YAML mapping | {label} | {type(exc).__name__}{location}")
                continue
            print(f"OK  已读取配置 | {label} | bytes={len(text.encode('utf-8'))} top_level_keys={len(document)}")
        if failed_reads or invalid_documents:
            print(f"FAIL Nacos 检查未通过：读取失败 {failed_reads}，配置缺失或无效 {invalid_documents}。")
            return EXIT_CONNECT if failed_reads else EXIT_DOCUMENT
        print(f"PASS Nacos 认证及全部 {len(profile['data_id_list'])} 个配置读取检查成功，耗时 {(perf_counter() - started) * 1000:.0f} ms。")
        return EXIT_OK
    finally:
        try:
            client.close()
        except Exception as exc:
            print(f"WARN Nacos 客户端关闭失败: {failure(exc)}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--offline", action="store_true", help="仅输出连接参数及前置检查，不访问 Nacos")
    args = parser.parse_args(argv)
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    os.chdir(BACKEND_ROOT)
    try:
        settings = load_nacos_settings()
        profile = select_profile(settings)
        for line in summarize_effective(settings, profile):
            print(line)
        reasons = prerequisite_failures(settings, profile)
    except Exception as exc:
        print(f"FAIL 无法加载 Nacos 参数或依赖（{type(exc).__name__}），请检查脚本顶部的连接配置及后端 Python 环境。")
        return EXIT_CONFIG
    if reasons:
        for reason in reasons:
            print(f"FAIL {reason}")
        return EXIT_CONFIG
    if args.offline:
        print("OFFLINE 参数前置检查通过。未访问 Nacos，不能据此判断连通性。")
        return EXIT_OK
    return check_connection(settings, profile)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消 Nacos 连接检查。")
        raise SystemExit(130)
