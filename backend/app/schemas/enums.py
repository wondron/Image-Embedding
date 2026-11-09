"""Enums wire: GET /api/enums, GET /api/enums/{name}, POST /api/enums/{name}/values.

Values, labels and editability come from config/param/enums.yaml (app.core.config.enum_store).
The request limits below are the file's rules (app.core.enums); the store checks them again.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    ENUM_DESCRIPTION_MAX_LEN,
    ENUM_LABEL_MAX_LEN,
    ENUM_VALUE_MAX_LEN,
    ENUM_VALUE_PATTERN,
)


class EnumValueItem(BaseModel):
    value: str = Field(..., description="存进数据库 / Qdrant payload 的取值")
    label: str = Field(..., description="中文名")
    description: str = ""


class EnumItem(BaseModel):
    name: str = Field(..., description="枚举名，如 food_state；也是接口路径里的 {name}")
    label: str
    description: str = ""
    editable: bool = Field(..., description="能否通过 POST /api/enums/{name}/values 追加取值")
    values: list[EnumValueItem] = Field(..., description="按文件里的顺序")


class EnumListResponse(BaseModel):
    ok: bool = True
    enums: list[EnumItem]
    file_error: str | None = Field(
        default=None,
        description="磁盘上的文件读不出来或不合规时的原因；此时返回的是上一份可用的内容，接口追加会被拒绝",
    )


class EnumResponse(BaseModel):
    ok: bool = True
    enum: EnumItem


class AddEnumValueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(
        ...,
        min_length=1,
        max_length=ENUM_VALUE_MAX_LEN,
        pattern=ENUM_VALUE_PATTERN,
        description="小写字母、数字和下划线，字母开头；写进库里后不要再改",
        examples=["thawed"],
    )
    label: str = Field(..., min_length=1, max_length=ENUM_LABEL_MAX_LEN, examples=["解冻"])
    description: str = Field(default="", max_length=ENUM_DESCRIPTION_MAX_LEN)
