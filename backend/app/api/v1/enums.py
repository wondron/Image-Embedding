"""Enum values and labels (config/param/enums.yaml); editable enums accept new values."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.services import enums as enums_service
from app.schemas.common import ErrorResponse
from app.schemas.enums import AddEnumValueRequest, EnumListResponse, EnumResponse

router = APIRouter(prefix="/api/enums", tags=["enums"])

_NOT_FOUND = {404: {"model": ErrorResponse}}


@router.get(
    "",
    response_model=EnumListResponse,
    response_model_exclude_none=True,
    summary="List enums",
    description=(
        "返回全部枚举的取值、中文名和能否追加（editable）。内容来自 config/param/enums.yaml，"
        "文件改了下次请求就是新内容；文件改坏时返回上一份可用的内容并带 file_error。"
    ),
)
def list_enums() -> Any:
    return enums_service.list_enums()


@router.get(
    "/{name}",
    response_model=EnumResponse,
    summary="Get one enum",
    responses=_NOT_FOUND,
)
def get_enum(name: str) -> Any:
    return enums_service.get_enum(name)


@router.post(
    "/{name}/values",
    response_model=EnumResponse,
    status_code=201,
    summary="Add a value to an editable enum",
    description=(
        "给 editable 为 true 的枚举追加一个取值，写回 config/param/enums.yaml，立即生效（入库校验马上认这个值）。"
        "取值只增不删。系统枚举返回 ENUM_NOT_EDITABLE（403），取值已存在返回 ENUM_VALUE_EXISTS（409）。"
    ),
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse}},
)
def add_enum_value(name: str, body: AddEnumValueRequest) -> Any:
    return enums_service.add_enum_value(name, body)
