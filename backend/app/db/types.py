"""自定义列类型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Enum
from sqlalchemy.types import UserDefinedType

from app.core.enums import StrEnum


class LtreeType(UserDefinedType[str]):
    """PostgreSQL ltree 路径类型（需要启用 ltree 扩展）。读写都按文本处理。"""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "LTREE"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """枚举存成 VARCHAR + CHECK 约束，不用 PostgreSQL 原生枚举，以后加取值只需改约束。"""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )
