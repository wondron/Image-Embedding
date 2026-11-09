"""自定义列类型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Enum, String
from sqlalchemy.types import UserDefinedType

from app.core.enums import ENUM_VALUE_MAX_LEN, StrEnum


class LtreeType(UserDefinedType[str]):
    """PostgreSQL ltree 路径类型（需要启用 ltree 扩展）。读写都按文本处理。"""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "LTREE"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """系统枚举：存成 VARCHAR + CHECK 约束（不用 PostgreSQL 原生枚举），取值来自代码里的类，改取值要写迁移。"""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_VALUE_MAX_LEN,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


def editable_enum() -> String:
    """可编辑枚举：只存 VARCHAR，不建 CHECK。

    取值在 config/param/enums.yaml，运行中可以追加，所以库里不列取值；写入前由
    app.services.retrieval.enum_fields 按文件校验。
    """
    return String(ENUM_VALUE_MAX_LEN)
