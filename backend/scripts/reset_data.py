"""清空测试业务数据，再恢复基础数据：开发 / 测试环境重新开始。

在 backend 目录执行（先停掉连着这个库的后端，否则可能等锁超时）：

  python -m scripts.reset_data --dry-run        # 只看会清空什么，不改任何东西
  python -m scripts.reset_data                  # 列出目标和行数，输入数据库名确认后执行
  python -m scripts.reset_data --keep-catalog   # 只清图片和任务记录，保留类别树和场景配置
  python -m scripts.reset_data --yes            # 不交互确认（脚本 / CI 用）

做什么：
1. PostgreSQL（一个事务，失败则什么都不改）：TRUNCATE ... RESTART IDENTITY 清空 RESET_TABLES
   （--keep-catalog 时只清 IMAGE_TABLES），然后执行 scripts.seed_data 补齐默认场景和类别根节点；
2. Qdrant：清空每个未下线模型版本 collection 里的点（保留 collection 本身和索引），
   免得向量库里留下已不存在的图片；--keep-vectors 跳过这一步。

保留：KEPT_TABLES（模型版本登记 embedding_models、加热组 heating_groups）和表结构；对象存储里的原图 / 缩略图
不删除（图片 ID 是内容哈希，重新上传同一张图会覆盖同一个文件）。

安全：NACOS_ENV 为 prod（未设置时的默认值）时拒绝执行，除非加 --allow-prod；
在测试环境执行前先设 NACOS_ENV=test（PowerShell：$env:NACOS_ENV='test'）。
退出码：0 成功；1 执行失败或取消；2 配置 / 连接 / 表结构问题或被安全检查拒绝。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:  # also works as `python scripts/reset_data.py`
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.seed_data import (  # noqa: E402
    SetupError,
    Target,
    check_schema,
    connection_error,
    create_db_engine,
    load_target,
    print_report,
    seed_base_data,
)

# 图片及其派生数据、入库任务记录、删除队列、外部候选
IMAGE_TABLES: tuple[str, ...] = (
    "index_tasks",
    "external_candidates",
    "image_deletions",
    "image_embeddings",
    "image_foods",
    "image_scenes",
    "images",
)
# 类别树和场景配置（清空后由 seed 恢复默认场景和根类别）
CATALOG_TABLES: tuple[str, ...] = (
    "scene_rules",
    "scene_labels",
    "scenes",
    "category_names",
    "categories",
)
RESET_TABLES: tuple[str, ...] = IMAGE_TABLES + CATALOG_TABLES
# 不清空：模型版本对应 Qdrant collection 和编码器指纹；加热组没有管理接口，只能手工维护
KEPT_TABLES: tuple[str, ...] = ("embedding_models", "heating_groups")

LOCK_TIMEOUT = "10s"


@dataclass
class Collection:
    model: str
    status: str
    name: str
    dim: int
    distance: str
    points: int | None = None  # None: collection 不存在


def tables_to_reset(*, keep_catalog: bool) -> tuple[str, ...]:
    return IMAGE_TABLES if keep_catalog else RESET_TABLES


def count_rows(conn: Any, tables: tuple[str, ...]) -> dict[str, int]:
    from sqlalchemy import text

    # 表名来自上面的常量，不是外部输入
    return {table: int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()) for table in tables}


def other_sessions(conn: Any) -> int:
    from sqlalchemy import text

    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        ).scalar_one()
    )


def model_collections(conn: Any) -> list[Collection]:
    from sqlalchemy import text

    from app.core.enums import ModelStatus

    rows = conn.execute(
        text(
            "SELECT name, version, status, collection, dim, distance FROM embedding_models "
            "WHERE status <> :retired ORDER BY id"
        ),
        {"retired": ModelStatus.RETIRED.value},
    ).mappings().fetchall()
    return [
        Collection(
            model=f"{row['name']}:{row['version']}", status=str(row["status"]), name=str(row["collection"]),
            dim=int(row["dim"]), distance=str(row["distance"]).lower(),
        )
        for row in rows
    ]


def open_qdrant(config: dict[str, Any], collections: list[Collection]) -> tuple[Any, dict[str, Any]]:
    """连上 Qdrant 并数出每个 collection 的点数；返回 (connection, {collection: QdrantStore})。"""
    from app.clients.qdrant import QdrantConnection, QdrantStore

    connection = QdrantConnection.from_mapping(dict(config.get("QDRANT_CONFIG") or {}))
    stores: dict[str, Any] = {}
    client = None
    for item in collections:
        store = QdrantStore(connection, collection=item.name, vector_dim=item.dim, distance=item.distance, client=client)
        client = store.client
        stores[item.name] = store
        item.points = store.count() if store.collection_exists() else None
    return connection, stores


def truncate_and_seed(conn: Any, tables: tuple[str, ...]):
    """在调用方事务里：取配置写锁、清空表（重置自增 ID）、补齐基础数据。"""
    from sqlalchemy import text

    from app.repositories.catalog_lock import lock_catalog_for_write

    conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    lock_catalog_for_write(conn)
    # 不加 CASCADE：哪张没登记的表引用了这些表，就报错停下，而不是顺带清掉它
    conn.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY"))
    return seed_base_data(conn)


def _confirm(target: Target) -> bool:
    if not sys.stdin.isatty():
        print("FAIL 非交互环境需要加 --yes 才会执行。", file=sys.stderr)
        return False
    try:
        answer = input(f"确认清空请输入数据库名 {target.pg.database}：").strip()
    except EOFError:
        answer = ""
    if answer != target.pg.database:
        print("已取消，未做任何修改。")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清空测试业务数据并恢复基础数据（开发 / 测试环境用）")
    parser.add_argument("--keep-catalog", action="store_true", help="保留类别树和场景配置，只清图片、任务记录等")
    parser.add_argument("--keep-vectors", action="store_true", help="不清 Qdrant（不推荐：会留下已不存在图片的向量）")
    parser.add_argument("--dry-run", action="store_true", help="只列出会清空什么，不做任何修改")
    parser.add_argument("--yes", action="store_true", help="跳过输入数据库名的确认")
    parser.add_argument("--allow-prod", action="store_true", help="允许在 NACOS_ENV=prod 时执行（慎用）")
    args = parser.parse_args(argv)

    from sqlalchemy.exc import OperationalError, SQLAlchemyError

    try:
        target = load_target()
    except SetupError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    print(f"目标：{target.describe()}")
    if target.profile == "prod" and not args.allow_prod:
        print(
            "FAIL NACOS_ENV 是 prod（未设置时默认 prod），拒绝清空。测试环境请先设 NACOS_ENV=test"
            "（PowerShell：$env:NACOS_ENV='test'）；确实要清空 prod 配置指向的库时加 --allow-prod。",
            file=sys.stderr,
        )
        return 2

    tables = tables_to_reset(keep_catalog=args.keep_catalog)
    engine = create_db_engine(target)
    try:
        # 1. 只读检查：表结构、行数、其他连接、向量库
        try:
            with engine.connect() as conn:
                check_schema(conn)
                counts = count_rows(conn, tables)
                sessions = other_sessions(conn)
                collections = [] if args.keep_vectors else model_collections(conn)
        except SetupError as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 2
        except SQLAlchemyError as exc:
            print(f"FAIL {connection_error(exc, target)}", file=sys.stderr)
            return 2
        stores: dict[str, Any] = {}
        qdrant_url = None
        if collections:
            try:
                connection, stores = open_qdrant(target.config, collections)
                qdrant_url = connection.url
            except Exception as exc:
                print(
                    f"FAIL 连不上 Qdrant（{type(exc).__name__}: {exc}）；先启动 Qdrant，或加 --keep-vectors 只清数据库。"
                    "未做任何修改。",
                    file=sys.stderr,
                )
                return 2

        print(f"将清空 {len(tables)} 张表（共 {sum(counts.values())} 行），ID 从 1 重新开始：")
        for table in tables:
            print(f"  {table:<20} {counts[table]:>8} 行")
        kept = KEPT_TABLES + (() if not args.keep_catalog else tuple(t for t in RESET_TABLES if t not in tables))
        print(f"保留：{', '.join(kept)}；对象存储里的文件不删除。")
        print("然后补齐基础数据：默认场景 general / oven / range_hood，根类别 ingredient / dish / dessert / beverage / mixed / other。")
        if args.keep_vectors:
            print("向量库：--keep-vectors，不清 Qdrant。")
        elif not collections:
            print("向量库：没有登记的模型版本，不需要清。")
        else:
            print(f"向量库 {qdrant_url}：清空以下 collection 里的点（保留 collection 和索引）：")
            for item in collections:
                points = "不存在，跳过" if item.points is None else f"{item.points} 个点"
                print(f"  {item.name}（{item.model}，{item.status}）：{points}")
        if sessions:
            print(f"WARN 这个库还有 {sessions} 个其他连接（后端可能在运行）；建议先停掉，否则可能等锁 {LOCK_TIMEOUT} 后失败。")

        if args.dry_run:
            print("DRY-RUN 未做任何修改。")
            return 0
        if not args.yes and not _confirm(target):
            return 1

        # 2. PostgreSQL：清空 + 补齐基础数据，一个事务
        try:
            with engine.begin() as conn:
                report = truncate_and_seed(conn, tables)
        except OperationalError as exc:
            if "lock timeout" in str(exc).lower() or "lock_timeout" in str(exc).lower():
                print(f"FAIL 等表锁超过 {LOCK_TIMEOUT}：还有连接在使用这些表（先停掉后端）。已回滚，未做任何修改。", file=sys.stderr)
            else:
                print(f"FAIL {connection_error(exc, target)}；已回滚，未做任何修改。", file=sys.stderr)
            return 1
        except SQLAlchemyError as exc:
            print(f"FAIL {connection_error(exc, target)}；已回滚，未做任何修改。", file=sys.stderr)
            return 1
        print(f"PASS PostgreSQL 已清空 {len(tables)} 张表，基础数据：")
        print_report(report)

        # 3. Qdrant：数据库已清空，再清向量
        failed = 0
        for item in collections:
            store = stores[item.name]
            if item.points is None:
                continue
            try:
                store.delete_all_points()
                left = store.count()
            except Exception as exc:
                failed += 1
                print(f"FAIL 清空 collection {item.name} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if left:
                failed += 1
                print(f"FAIL collection {item.name} 清空后还剩 {left} 个点（可能有写入正在进行）", file=sys.stderr)
            else:
                print(f"PASS 已清空 collection {item.name}（原有 {item.points} 个点）")
        for store in stores.values():
            store.close()
            break  # 所有 store 共用一个 client，关一次即可
        if failed:
            print("数据库已重置；向量库没清干净，重新执行同一条命令即可（重复执行是安全的）。", file=sys.stderr)
            return 1
        if not report.complete:
            print("FAIL 有基础数据补不上（见上面的 FAIL 行），处理后执行 python -m scripts.seed_data。", file=sys.stderr)
            return 1
        print("完成。后端在运行时不需要重启；浏览器里刷新页面即可。")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
