"""Alembic env — URL from DATABASE_URL / PostgresSettings (no hard-coded secrets).

Schema SSOT is app.models (ORM). Revisions stay hand-reviewed SQL; use
`alembic check` to confirm the migrated DB matches the models, or
`alembic revision --autogenerate` to draft the next revision.
The categories.path trigger lives only in migrations (not visible to autogenerate).
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  registers all 12 tables on Base.metadata
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    # Host-side default: localhost (compose service name only works inside docker net)
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "image_emb")
    password = os.getenv("POSTGRES_PASSWORD", "image_emb")
    database = os.getenv("POSTGRES_DATABASE", "image_embedding")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
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
