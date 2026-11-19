"""Alembic 环境——数据库取自和应用相同的配置。

连接：生效配置里的 POSTGRES_CONFIG（Nacos 叠加在本地 YAML 上，app.core.config.nacos），
由 PostgresSettings.url() 生成。以代码方式调用的一方（tests/test_pg_live.py）可以通过
Config.attributes["database_url"] 指向另一个数据库。

表结构的唯一权威来源是 app.models（ORM）。迁移版本仍然是人工审阅的 SQL；用
`alembic check` 确认迁移后的数据库和模型一致，或者用 `alembic revision --autogenerate`
起草下一个版本。
categories.path 触发器只存在于迁移里（autogenerate 看不到）。
"""
from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL, make_url

import app.models  # noqa: F401  registers all 13 tables on Base.metadata
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> URL:
    explicit = config.attributes.get("database_url")
    if explicit:
        return make_url(explicit)
    from app.core.config.nacos import load_config_once
    from app.core.config.settings import PostgresSettings, settings

    app_config = load_config_once(settings.nacos, settings.config.yaml_path)
    url = PostgresSettings.from_config(app_config).url()
    # 说明即将迁移的是哪个数据库（密码已遮盖），以及它的配置来自哪里
    nacos = settings.nacos
    logging.getLogger("alembic.env").info(
        "target database %s | NACOS_ENV=%s, %s",
        url,
        settings.application.env,
        f"Nacos namespace {nacos.namespace} over local YAML" if nacos.enabled else "Nacos disabled, local YAML only",
    )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
