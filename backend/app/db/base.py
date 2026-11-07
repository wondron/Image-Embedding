"""ORM 基类：约束命名规则、类型映射、时间字段。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 约束和索引按统一规则命名，迁移脚本里的名字稳定可读
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有表的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        str: Text,
        datetime: DateTime(timezone=True),
        # 模型都开了 from __future__ import annotations，Mapped[dict[str, Any]] 是字符串注解，
        # 由 SQLAlchemy 解析后匹配这一项；早期 2.0.x（如 2.0.25）解析不了，会报
        # Could not locate SQLAlchemy Core type。版本要求见 requirements.txt（2.1 系列）
        dict[str, Any]: JSONB,
    }


class CreatedAtMixin:
    """创建时间，由数据库默认值 now() 填入。"""

    # 只对 ORM 会话生效：插入后立即取回数据库生成的值。目前运行时都是手写 SQL，不经过 ORM 会话
    __mapper_args__ = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), comment="创建时间")


class UpdatedAtMixin:
    """更新时间。

    onupdate=func.now() 只对 SQLAlchemy 自己生成的 UPDATE（ORM / Core 语句）生效。仓储层是手写
    SQL，UPDATE 里必须自己写 SET updated_at = now()（images、image_scenes 现在都是这样写的）。
    """

    __mapper_args__ = {"eager_defaults": True}

    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class TimestampMixin(CreatedAtMixin, UpdatedAtMixin):
    """创建时间 + 更新时间。"""
