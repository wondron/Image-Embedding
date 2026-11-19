"""把请求或数据库行里的值转成枚举值（输入不合法时 VALIDATION_ERROR）。

第一个参数决定允许值的来源（app.core.enums）：
- 系统枚举类（StrEnum）：返回枚举成员；数据库列上还有 CHECK 约束；
- 可编辑枚举的名称（SOURCE_TYPE、LICENSE、FOOD_STATE）：返回普通 str，按
  config/param/enums.yaml 当前的内容检查（运行时新增的取值立刻可用）。
  这些列没有 CHECK 约束，所以这里是唯一的把关。
在这里校验，把本来会出现的 IntegrityError（500）变成指明字段和允许值的 422。
"""

from __future__ import annotations

from typing import Any, TypeVar, overload

from app.core import errors as err
from app.core.config.enum_store import get_enum_store
from app.core.enums import StrEnum

E = TypeVar("E", bound=StrEnum)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@overload
def coerce_enum(enum: type[E], value: Any, *, field: str, default: E | None = None) -> E: ...
@overload
def coerce_enum(enum: str, value: Any, *, field: str, default: str | None = None) -> str: ...
def coerce_enum(enum: type[StrEnum] | str, value: Any, *, field: str, default: Any = None) -> Any:
    """必填的枚举字段；为空时用默认值，没有默认值时 VALIDATION_ERROR。"""
    if _blank(value):
        if default is None:
            raise err.validation_error(f"{field} is required")
        return default
    return _parse(enum, value, field=field)


@overload
def coerce_optional_enum(enum: type[E], value: Any, *, field: str) -> E | None: ...
@overload
def coerce_optional_enum(enum: str, value: Any, *, field: str) -> str | None: ...
def coerce_optional_enum(enum: type[StrEnum] | str, value: Any, *, field: str) -> Any:
    """可为空的枚举字段；为空时为 None。"""
    if _blank(value):
        return None
    return _parse(enum, value, field=field)


def _parse(enum: type[StrEnum] | str, value: Any, *, field: str) -> Any:
    if isinstance(enum, str):
        definition = get_enum_store().get(enum)  # 文件从来没有加载成功时为 ENUM_FILE_INVALID（503）
        text = str(value).strip()
        if text in definition:
            return text
        allowed = definition.value_list()
    else:
        if isinstance(value, enum):
            return value
        try:
            return enum(str(value).strip())
        except ValueError:
            allowed = [m.value for m in enum]
    raise err.validation_error(f"{field} must be one of {allowed}, got {value!r}")
