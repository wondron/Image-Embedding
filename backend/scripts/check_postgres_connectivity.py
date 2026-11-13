"""测试 PostgreSQL 登录及只读查询，不创建表、不修改数据。

在项目根目录运行：
  python backend/script/check_postgres_connectivity.py
  python backend/script/check_postgres_connectivity.py --local-config
  python backend/script/check_postgres_connectivity.py --local-config --host 10.0.0.10
  python backend/script/check_postgres_connectivity.py --host db.example.com --ask-password

在 backend 目录运行时，把上述脚本路径改为 script/check_postgres_connectivity.py。
默认与后端一样读取本地 YAML + Nacos；--local-config 只读取本地 YAML。
连接参数来自 POSTGRES_CONFIG，命令行参数仅覆盖本次测试，不保存到配置文件。
退出码：0 成功，1 连接/查询失败，2 配置/依赖错误。
"""

from __future__ import annotations

import argparse
import getpass
import math
import os
import sys
from pathlib import Path
from time import perf_counter
from urllib.parse import quote, quote_plus

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return result


def safe_error(exc: Exception, password: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if password:
        for secret in {password, quote(password, safe=""), quote_plus(password)}:
            text = text.replace(secret, "***")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, help="本地 YAML 路径，默认 backend/config/param/config.yaml")
    parser.add_argument("--local-config", action="store_true", help="只读取本地 YAML，不访问 Nacos")
    parser.add_argument("--host", help="覆盖 POSTGRES_CONFIG.host，例如远程服务器 IP")
    parser.add_argument("--port", type=positive_int, help="覆盖端口，默认读取配置（通常为 5432）")
    parser.add_argument("--database", help="覆盖数据库名")
    parser.add_argument("--user", help="覆盖用户名")
    parser.add_argument("--timeout", type=positive_int, help="连接超时秒数，默认读取配置")
    parser.add_argument("--sslmode", choices=("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
                        default="prefer", help="SSL 模式，默认 prefer")
    parser.add_argument("--ask-password", action="store_true", help="交互输入密码（不回显），覆盖配置中的密码")
    args = parser.parse_args(argv)

    # Resolve CLI paths before switching cwd; application config paths are backend-relative.
    config_path = args.config.resolve() if args.config else BACKEND_ROOT / "config/param/config.yaml"
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    os.chdir(BACKEND_ROOT)

    try:
        import psycopg
    except ImportError:
        print('FAIL 缺少 PostgreSQL 驱动，请在当前 Python 环境执行：python -m pip install "psycopg[binary]>=3.1,<4.0"')
        return 2

    try:
        from app.core.config.loader import load_yaml
        from app.core.config.settings import PostgresSettings, settings

        if args.local_config:
            config = load_yaml(config_path)
            source = {"source": "yaml", "nacos_status": "disabled by --local-config"}
        else:
            from app.core.config.nacos import NacosConfigProvider

            provider = NacosConfigProvider(settings.nacos)
            try:
                config, source = provider.load(str(config_path))
            finally:
                provider.close()

        section = dict(config.get("POSTGRES_CONFIG") or {})
        for key in ("host", "port", "database", "user"):
            value = getattr(args, key)
            if value is not None:
                section[key] = value
        if args.timeout is not None:
            section["connect_timeout"] = args.timeout
        if args.ask_password:
            section["password"] = getpass.getpass("PostgreSQL 密码：")
        pg = PostgresSettings.from_config({"POSTGRES_CONFIG": section})
        if not 1 <= pg.port <= 65535:
            raise ValueError("POSTGRES_CONFIG.port 必须在 1 到 65535 之间")
        if not math.isfinite(pg.connect_timeout) or pg.connect_timeout <= 0:
            raise ValueError("POSTGRES_CONFIG.connect_timeout 必须是大于 0 的有限数值")
    except Exception as exc:
        # YAML/parser errors may contain complete source lines, including credentials.
        print(f"FAIL 无法加载 PostgreSQL 配置（{type(exc).__name__}）。请检查 POSTGRES_CONFIG、配置路径及命令行参数。")
        return 2

    timeout = max(2, math.ceil(pg.connect_timeout))
    print(f"配置来源：{source.get('source')}；本地文件：{config_path}")
    if source.get("nacos_profile"):
        print(f"Nacos 环境：{source['nacos_profile']}")
    if source.get("nacos_status"):
        print(f"Nacos 状态：{source['nacos_status']}")
    print(f"目标：host={pg.host} port={pg.port} database={pg.database} user={pg.user}")
    print(f"连接超时={timeout}s（每个地址）；查询超时=10s；sslmode={args.sslmode}")
    if pg.host.lower() in {"localhost", "127.0.0.1", "::1"}:
        print("提示：当前目标是本机。测试远程服务器可用 --host 指定地址。")

    started = perf_counter()
    try:
        with psycopg.connect(
            host=pg.host,
            port=pg.port,
            dbname=pg.database,
            user=pg.user,
            password=pg.password,
            connect_timeout=timeout,
            sslmode=args.sslmode,
            application_name="image_embedding_connectivity_check",
            options="-c statement_timeout=10000",
        ) as conn:
            conn.read_only = True
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1, current_database(), current_user, "
                    "inet_server_addr()::text, inet_server_port(), current_setting('server_version')"
                )
                row = cursor.fetchone()
                if row is None or row[0] != 1:
                    raise RuntimeError("只读查询未返回预期结果")
        elapsed = (perf_counter() - started) * 1000
        print(f"PASS PostgreSQL 登录及只读查询成功，耗时 {elapsed:.0f} ms")
        print(f"数据库={row[1]} 用户={row[2]} 服务端={row[3]}:{row[4]} PostgreSQL={row[5]}")
        return 0
    except Exception as exc:
        print(f"FAIL PostgreSQL 连接或查询失败：{safe_error(exc, pg.password)}")
        print("请检查服务器地址/端口、网络或防火墙、账号密码、数据库名及 pg_hba.conf 访问规则。")
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消连接测试。")
        raise SystemExit(130)
