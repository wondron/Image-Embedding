"""Category tree admin routes: read the whole tree, create / patch / delete one node."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from app.api.services import categories as categories_service
from app.bootstrap.state import get_application_state
from app.schemas.categories import (
    CategoryCreateRequest,
    CategoryDeleteResponse,
    CategoryListResponse,
    CategoryResponse,
    CategoryUpdateRequest,
    CategoryUpdateResponse,
)
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/api/categories", tags=["categories"])

_NOT_FOUND = {404: {"model": ErrorResponse}}
_CONFLICT = {409: {"model": ErrorResponse}}
_CategoryId = Path(..., gt=0, description="categories.id")


@router.get(
    "",
    response_model=CategoryListResponse,
    summary="Read the category tree",
    description=(
        "返回类别树的全部节点（不分页），以及可选的加热组和根节点允许的编码。"
        "节点按层级排好：父节点在子节点之前，同级按 sort_order、id。"
    ),
)
def list_categories(request: Request) -> Any:
    return categories_service.list_categories(get_application_state(request))


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=201,
    summary="Create a category",
    description=(
        "新建一个节点和它的名称，path 由数据库生成。parent_id 为空时是根节点，编码只能是六个域之一，根节点最多六个。"
        "编码已被占用返回 CATEGORY_CODE_EXISTS（409）；父节点或加热组不存在返回 VALIDATION_ERROR（422）。"
    ),
    responses=_CONFLICT,
)
def create_category(request: Request, body: CategoryCreateRequest) -> Any:
    return categories_service.create_category(get_application_state(request), request, body)


@router.patch(
    "/{category_id}",
    response_model=CategoryUpdateResponse,
    summary="Update a category",
    description=(
        "只改请求里给了的字段。改 parent_id 会把整棵子树一起移过去（不能移到自己或自己的子孙下面）；"
        "names 给了就整体替换。移动类别或改菜系时，同一事务里重算标在它和它子类别上的图片的场景归属，"
        "提交后把有变化的图片同步到向量库（scene_sync 给出结果；一次最多同步 2000 张，其余由后台对账补上）。"
    ),
    responses={**_NOT_FOUND, **_CONFLICT},
)
def update_category(request: Request, body: CategoryUpdateRequest, category_id: int = _CategoryId) -> Any:
    return categories_service.update_category(get_application_state(request), request, category_id, body)


@router.delete(
    "/{category_id}",
    response_model=CategoryDeleteResponse,
    summary="Delete a category",
    description=(
        "删除一个没有被使用的节点，名称一起删除。还有子类别、图片或场景规则引用时返回 CATEGORY_IN_USE（409），"
        "这种情况可以改为停用（is_active=false）。"
    ),
    responses={**_NOT_FOUND, **_CONFLICT},
)
def delete_category(request: Request, category_id: int = _CategoryId) -> Any:
    return categories_service.delete_category(get_application_state(request), request, category_id)
