"""类别的 wire：GET / POST /api/categories、PATCH / DELETE /api/categories/{category_id}。

管理页读取和编辑的类别树（categories + category_names）。成功的响应体不带 trace_id
（它在 X-Trace-Id 响应头里，和 /api/enums 一样）。请求的限制来自 app.core.category_rules；
app.core.rules.category_fields 会再检查一次，同时检查 schema 表达不了的规则（zh 主名称、
每种语言一个主名称）。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.category_rules import (
    CATEGORY_CODE_MAX_LEN,
    CATEGORY_CODE_PATTERN,
    CATEGORY_CUISINE_MAX_LEN,
    CATEGORY_CUISINE_PATTERN,
    CATEGORY_LANG_MAX_LEN,
    CATEGORY_LANG_PATTERN,
    CATEGORY_MAX_CUISINES,
    CATEGORY_MAX_NAMES,
    CATEGORY_NAME_MAX_LEN,
    CATEGORY_SORT_ORDER_MAX,
    CATEGORY_SORT_ORDER_MIN,
)
from app.core.enums import NameKind
from app.schemas.retrieval import HeatingGroup

# -- 共用部分 -----------------------------------------------------------------------------

Cuisine = Annotated[
    str, Field(min_length=1, max_length=CATEGORY_CUISINE_MAX_LEN, pattern=CATEGORY_CUISINE_PATTERN)
]


class CategoryName(BaseModel):
    """category_names 的一行。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lang: str = Field(
        ...,
        min_length=2,
        max_length=CATEGORY_LANG_MAX_LEN,
        pattern=CATEGORY_LANG_PATTERN,
        description="语言代码，小写，如 zh、en",
        examples=["zh"],
    )
    name: str = Field(..., min_length=1, max_length=CATEGORY_NAME_MAX_LEN, examples=["披萨"])
    kind: NameKind = Field(..., description="primary 主名（每种语言最多一个）/ alias 别名")


# -- 响应 ---------------------------------------------------------------------------------

class CategoryItem(BaseModel):
    id: int
    code: str = Field(..., description="稳定编码，如 dish.pizza")
    parent_id: int | None = Field(..., description="父节点；根节点为 null")
    path: str = Field(..., description="从根到本节点的 id 链，如 2.15.137；数据库维护，只读")
    name: str | None = Field(..., description="中文主名（检索结果里的 category）；没有时为 null")
    names: list[CategoryName] = Field(..., description="全部名称：中文在前，同一语言主名在前")
    cuisines: list[str] = Field(..., description="本节点自己设的菜系；为空时沿用最近祖先的")
    heating_group: HeatingGroup | None = Field(
        ..., description="本节点自己设的加热组；为 null 时沿用最近祖先的"
    )
    sort_order: int
    is_active: bool
    image_count: int = Field(..., description="直接标在这个类别上的图片数（不含子类别）")
    scene_rule_count: int = Field(..., description="引用这个类别的场景规则数")
    created_at: str | None = None
    updated_at: str | None = None


class CategoryListResponse(BaseModel):
    ok: bool = True
    categories: list[CategoryItem] = Field(
        ..., description="整棵树的全部节点：父节点一定在子节点之前，同级按 sort_order、id 排"
    )
    heating_groups: list[HeatingGroup] = Field(..., description="可选的加热组（heating_groups 表）")
    root_codes: list[str] = Field(..., description="根节点允许使用的编码（六个域，根节点最多六个）")


class CategoryResponse(BaseModel):
    ok: bool = True
    category: CategoryItem


class CategorySceneSync(BaseModel):
    """移动或修改菜系对类别下已存图片造成的影响。"""

    images: int = Field(..., description="标在这个类别及其子类别上、已重算场景归属的图片数")
    changed: int = Field(..., description="其中场景归属有变化的图片数（payload_version 已递增）")
    pushed: int = Field(..., description="其中新的向量载荷已写入当前向量库的图片数")
    pending: int = Field(
        ...,
        description=(
            "其中还没写入向量库的图片数：超出本次请求的上限、向量库失败或未就绪的，后台对账或 "
            "POST /api/scenes/sync 会补上；当前向量库里没有向量的，要重新生成向量"
        ),
    )
    vector_sync: Literal["pushed", "partial", "not_ready", "failed"] = Field(
        ...,
        description="pushed 全部已同步 / partial 部分待同步 / not_ready 检索服务未就绪 / failed 写入向量库失败",
    )
    sync_required: bool = Field(..., description="pending 大于 0")


class CategoryUpdateResponse(CategoryResponse):
    scene_sync: CategorySceneSync | None = Field(
        default=None,
        description="移动类别或改了菜系时才有：其下已入库图片的场景归属在同一事务里重算后的结果；其余修改为 null",
    )


class DeletedCategory(BaseModel):
    id: int
    code: str


class CategoryDeleteResponse(BaseModel):
    ok: bool = True
    deleted: DeletedCategory


# -- 请求 ---------------------------------------------------------------------------------

_CODE = dict(
    min_length=1,
    max_length=CATEGORY_CODE_MAX_LEN,
    pattern=CATEGORY_CODE_PATTERN,
    examples=["dish.pizza"],
)
_SORT_ORDER = dict(ge=CATEGORY_SORT_ORDER_MIN, le=CATEGORY_SORT_ORDER_MAX, strict=True)


class CategoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(
        ..., description="大小写字母、数字、下划线，用点分段，字母开头。保留大小写，按小写判重；根节点只能用六个域的编码", **_CODE
    )
    parent_id: int | None = Field(default=None, gt=0, strict=True, description="父节点；不传或 null 为根节点")
    names: list[CategoryName] = Field(
        ..., min_length=1, max_length=CATEGORY_MAX_NAMES, description="至少要有中文（zh）主名"
    )
    cuisines: list[Cuisine] = Field(default_factory=list, max_length=CATEGORY_MAX_CUISINES)
    heating_group_id: int | None = Field(default=None, gt=0, strict=True)
    sort_order: int = Field(default=0, description="同级排序，小的在前", **_SORT_ORDER)
    is_active: bool = Field(default=True, strict=True)


class CategoryUpdateRequest(BaseModel):
    """只修改传来的字段。parent_id 为 null 时把节点移到根；heating_group_id 为 null 时清空它；
    其他字段不能为 null。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, description="修改后递归更新后代编码；同时移动时以最后一段作为本地编码", **_CODE)
    parent_id: int | None = Field(
        default=None, gt=0, strict=True, description="换父节点：整棵子树一起移动，节点及后代编码随新父级重建；null 移到根"
    )
    names: list[CategoryName] | None = Field(
        default=None, min_length=1, max_length=CATEGORY_MAX_NAMES, description="给了就整体替换全部名称"
    )
    cuisines: list[Cuisine] | None = Field(default=None, max_length=CATEGORY_MAX_CUISINES)
    heating_group_id: int | None = Field(default=None, gt=0, strict=True, description="null 清空")
    sort_order: int | None = Field(default=None, **_SORT_ORDER)
    is_active: bool | None = Field(default=None, strict=True)
