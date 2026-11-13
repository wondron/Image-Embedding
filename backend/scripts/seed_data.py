"""基础数据（Seed）：补齐默认场景和类别根节点，可以重复执行。

分工：Alembic 管表结构，本脚本管基础数据，scripts.reset_data 管业务数据清理。
在 backend 目录、用启动后端的 Python 环境执行：

  python -m alembic upgrade head        # 先建表 / 升级表结构
  python -m scripts.seed_data           # 补齐缺失的基础数据
  python -m scripts.seed_data --dry-run # 只列出会补哪些，不写库

基础数据就是下面的 BASE_SCENES 和 ROOT_CATEGORIES，要增减就改这里。规则：
- 按编码判断是否已有（类别编码不区分大小写）；缺的插入，已有的不改，和定义不同的只提示；
- 不删除任何数据，所以库里另外配置的场景、类别都保留；
- 全部写入在一个事务里，并持有类别 / 场景配置的写锁（和管理页面的写入互斥），
  要么全部补上，要么什么都不改。

连接哪个库和后端、Alembic 一样：本地 YAML 打底、NACOS_ENV 所选 Nacos 环境覆盖（NACOS_ENV 默认 prod）。
本机执行前先设 NACOS_ENV=test，或用 NACOS_ENABLED=0 只读本地 YAML。
退出码：0 基础数据已齐全；1 有基础数据补不上（见 FAIL 行）或写库失败；2 配置 / 连接 / 表结构问题。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:  # also works as `python scripts/seed_data.py`
    sys.path.insert(0, str(BACKEND_ROOT))


# -- 基础数据 -------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneSeed:
    code: str
    name: str
    description: str | None = None
    include_all: bool = False
    outputs_heating: bool = False
    is_active: bool = True

    def fields(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "include_all": self.include_all,
            "outputs_heating": self.outputs_heating,
            "is_active": self.is_active,
        }


@dataclass(frozen=True)
class RootCategorySeed:
    code: str  # 必须是 app.core.enums.CategoryDomain 的取值
    zh_name: str


# 默认场景，按这个顺序插入（场景列表按 id 排序）。general 是请求不带场景时用的默认场景，必须包含全部图片
BASE_SCENES: tuple[SceneSeed, ...] = (
    SceneSeed("general", "通用", "含全部图片", include_all=True),
    SceneSeed("oven", "一体机", "按类别规则 + 场景类别；结果带加热组", outputs_heating=True),
    SceneSeed("range_hood", "抽油烟机", "待配置规则"),
)

# 类别树的六个根节点（类别域），sort_order 依次为 0..5；中文名与 config/param/enums.yaml 的 category_domain 一致
ROOT_CATEGORIES: tuple[RootCategorySeed, ...] = (
    RootCategorySeed("ingredient", "食材"),
    RootCategorySeed("dish", "菜品"),
    RootCategorySeed("dessert", "甜点烘焙"),
    RootCategorySeed("beverage", "饮品"),
    RootCategorySeed("mixed", "混合"),
    RootCategorySeed("other", "其他"),
)


# -- 补齐逻辑 -------------------------------------------------------------------------


class SetupError(Exception):
    """配置、连接或表结构不满足要求（退出码 2）。消息已经是给人看的中文。"""


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # 补不上的基础数据

    @property
    def complete(self) -> bool:
        return not self.problems


def _scene_fields(spec: SceneSeed) -> dict[str, Any]:
    """经过接口同一套校验（编码写法、长度、旧编码不能占用）。"""
    from app.core.rules.scene_fields import normalize_scene_fields

    return normalize_scene_fields(spec.fields(), partial=False)


def _root_fields(spec: RootCategorySeed, sort_order: int) -> dict[str, Any]:
    from app.core.category_rules import CATEGORY_DISPLAY_LANG
    from app.core.rules.category_fields import check_root_code, normalize_category_fields

    fields = normalize_category_fields(
        {
            "code": spec.code,
            "parent_id": None,
            "names": [{"lang": CATEGORY_DISPLAY_LANG, "name": spec.zh_name, "kind": "primary"}],
            "sort_order": sort_order,
        },
        partial=False,
    )
    check_root_code(fields["code"])
    return fields


def _seed_scenes(conn: Any, report: SeedReport) -> None:
    from sqlalchemy import text

    rows = conn.execute(
        text("SELECT id, code, name, description, include_all, outputs_heating, is_active FROM scenes")
    ).mappings().fetchall()
    existing = {str(row["code"]): row for row in rows}
    for spec in BASE_SCENES:
        wanted = _scene_fields(spec)
        row = existing.get(wanted["code"])
        label = f"场景 {wanted['code']}（{wanted['name']}）"
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO scenes (code, name, description, include_all, outputs_heating, is_active) "
                    "VALUES (:code, :name, :description, :include_all, :outputs_heating, :is_active)"
                ),
                wanted,
            )
            report.created.append(label)
            if wanted["include_all"] and wanted["is_active"]:
                images = conn.execute(text("SELECT count(*) FROM images")).scalar_one()
                if images:
                    report.warnings.append(
                        f"新建的场景 {wanted['code']} 包含全部图片，库里已有 {images} 张图片：到「场景管理」重算场景归属，"
                        "或执行 python -m app.workers.sync_scenes，把它写进向量库"
                    )
            continue
        report.kept.append(f"场景 {row['code']}（{row['name']}，id={row['id']}）")
        differs = [
            f"{key}={row[key]!r}（定义为 {wanted[key]!r}）"
            for key in ("name", "description", "include_all", "outputs_heating", "is_active")
            if row[key] != wanted[key]
        ]
        if differs:
            report.warnings.append(f"已有场景 {row['code']} 与基础数据定义不同，保留现有值：{'；'.join(differs)}")


def _seed_root_categories(conn: Any, report: SeedReport) -> None:
    from sqlalchemy import text

    from app.core.category_rules import CATEGORY_MAX_ROOTS

    planned = [(_root_fields(spec, order), spec) for order, spec in enumerate(ROOT_CATEGORIES)]
    rows = conn.execute(
        text(
            "SELECT id, code, parent_id FROM categories "
            "WHERE parent_id IS NULL OR lower(code) = ANY(CAST(:codes AS text[]))"
        ),
        {"codes": [fields["code"].lower() for fields, _ in planned]},
    ).mappings().fetchall()
    by_code = {str(row["code"]).lower(): row for row in rows}
    root_count = sum(1 for row in rows if row["parent_id"] is None)
    for fields, spec in planned:
        label = f"根类别 {fields['code']}（{spec.zh_name}）"
        row = by_code.get(fields["code"].lower())
        if row is not None:
            if row["parent_id"] is not None:
                report.problems.append(
                    f"{label} 补不上：编码 {row['code']!r} 已被非根类别使用（id={row['id']}，parent_id={row['parent_id']}）；"
                    "先在「食材管理」把它移到最顶层或改编码，再重新执行"
                )
            else:
                report.kept.append(f"根类别 {row['code']}（id={row['id']}）")
            continue
        if root_count >= CATEGORY_MAX_ROOTS:
            report.problems.append(
                f"{label} 补不上：已有 {root_count} 个根类别（最多 {CATEGORY_MAX_ROOTS} 个，含停用和旧编码的根节点）；"
                "先处理多余的根类别，再重新执行"
            )
            continue
        category_id = conn.execute(
            text(
                "INSERT INTO categories (code, parent_id, cuisines, heating_group_id, sort_order, is_active) "
                "VALUES (:code, NULL, CAST(:cuisines AS text[]), :heating_group_id, :sort_order, :is_active) "
                "RETURNING id"
            ),
            {key: value for key, value in fields.items() if key not in ("names", "parent_id")},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO category_names (category_id, lang, name, kind) "
                "VALUES (:category_id, :lang, :name, :kind)"
            ),
            [{"category_id": category_id, **name} for name in fields["names"]],
        )
        root_count += 1
        report.created.append(label)


def seed_base_data(conn: Any) -> SeedReport:
    """在调用方的事务里补齐基础数据；先取类别 / 场景配置写锁（和管理接口的写入互斥）。"""
    from app.repositories.catalog_lock import lock_catalog_for_write

    lock_catalog_for_write(conn)
    report = SeedReport()
    _seed_scenes(conn, report)
    _seed_root_categories(conn, report)
    return report


# -- 连接（reset_data 共用） ------------------------------------------------------------


@dataclass
class Target:
    config: dict[str, Any]
    pg: Any  # app.core.config.settings.PostgresSettings
    profile: str
    nacos_enabled: bool

    def describe(self) -> str:
        source = "Nacos 覆盖本地 YAML" if self.nacos_enabled else "Nacos 已关闭，只读本地 YAML"
        return (
            f"PostgreSQL {self.pg.host}:{self.pg.port}/{self.pg.database}（用户 {self.pg.user}）"
            f" | NACOS_ENV={self.profile}，{source}"
        )


def load_target() -> Target:
    """和后端、Alembic 同一份生效配置；只解析 POSTGRES_CONFIG，不打印密码。"""
    os.chdir(BACKEND_ROOT)  # 配置文件路径相对 backend/
    try:
        from app.core.config.settings import PostgresSettings, settings
        from app.workers._config import load_worker_config

        config = load_worker_config()
        pg = PostgresSettings.from_config(config)
    except ValueError as exc:
        raise SetupError(f"配置不可用：{exc}") from exc
    except Exception as exc:  # YAML 解析错误可能带原文（含密码），不打印细节
        raise SetupError(
            f"无法加载配置（{type(exc).__name__}）；检查 NACOS_ENV / NACOS_ENABLED 和 config/param/config.yaml"
        ) from exc
    return Target(config=config, pg=pg, profile=settings.nacos.profile, nacos_enabled=settings.nacos.enabled)


def create_db_engine(target: Target) -> Any:
    from sqlalchemy import create_engine

    return create_engine(
        target.pg.url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": max(1, int(target.pg.connect_timeout))},
    )


def check_schema(conn: Any) -> str:
    """数据库必须已迁移到这份代码的 Alembic head；返回该版本号。"""
    from alembic.runtime.migration import MigrationContext

    from app.db.session import expected_revision

    expected = expected_revision()
    current = MigrationContext.configure(conn).get_current_revision()
    if current != expected:
        raise SetupError(
            f"数据库表结构在迁移 {current or '（未建表）'}，这份代码需要 {expected}；"
            "先在 backend 目录执行 python -m alembic upgrade head"
        )
    return expected


def connection_error(exc: Exception, target: Target) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if target.pg.password:
        text = text.replace(target.pg.password, "***")
    return f"连接或查询 PostgreSQL 失败：{text}"


def print_report(report: SeedReport) -> None:
    for item in report.created:
        print(f"  新增  {item}")
    for item in report.kept:
        print(f"  已有  {item}")
    for item in report.warnings:
        print(f"  WARN  {item}")
    for item in report.problems:
        print(f"  FAIL  {item}")


# -- 命令行 ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补齐默认场景和类别根节点（可重复执行，不覆盖已有数据）")
    parser.add_argument("--dry-run", action="store_true", help="只列出会补哪些，事务回滚，不写库")
    args = parser.parse_args(argv)

    from sqlalchemy.exc import SQLAlchemyError

    try:
        target = load_target()
        print(f"目标：{target.describe()}")
        engine = create_db_engine(target)
    except SetupError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    try:
        with engine.connect() as conn:
            check_schema(conn)
            conn.rollback()  # end the check's implicit transaction before the write transaction
            with conn.begin() as tx:
                report = seed_base_data(conn)
                if args.dry_run:
                    tx.rollback()
    except SetupError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"FAIL {connection_error(exc, target)}（未做任何修改）", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print_report(report)
    if args.dry_run:
        print(f"DRY-RUN 会新增 {len(report.created)} 项；已回滚，未写库。")
    elif report.created:
        print(f"PASS 已新增 {len(report.created)} 项基础数据，其余 {len(report.kept)} 项原本就有。")
    else:
        print(f"PASS 基础数据已齐全（{len(report.kept)} 项），未做修改。")
    if not report.complete:
        print(f"FAIL {len(report.problems)} 项基础数据补不上，处理后重新执行即可。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
