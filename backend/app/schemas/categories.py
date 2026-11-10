"""Categories wire: GET / POST /api/categories, PATCH / DELETE /api/categories/{category_id}.

The category tree (categories + category_names) as the admin page reads and edits it. Success
bodies carry no trace_id (it is in the X-Trace-Id header, as for /api/enums). The request
limits are app.core.category_rules; app.services.retrieval.category_fields checks them again
together with the rules a schema cannot say (the zh primary name, one primary per language).
"""

from typing import Annotated

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

# -- shared parts -----------------------------------------------------------------------

Cuisine = Annotated[
    str, Field(min_length=1, max_length=CATEGORY_CUISINE_MAX_LEN, pattern=CATEGORY_CUISINE_PATTERN)
]


class CategoryName(BaseModel):
    """One row of category_names."""

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


# -- responses --------------------------------------------------------------------------

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
    root_codes: list[str] = Field(..., description="根节点允许使用的编码（四个域）")


class CategoryResponse(BaseModel):
    ok: bool = True
    category: CategoryItem


class DeletedCategory(BaseModel):
    id: int
    code: str


class CategoryDeleteResponse(BaseModel):
    ok: bool = True
    deleted: DeletedCategory


# -- requests ---------------------------------------------------------------------------

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
        ..., description="小写字母、数字、下划线，用点分段，字母开头。根节点只能用四个域的编码", **_CODE
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
    """Only the fields sent are changed. parent_id: null moves the node to the root;
    heating_group_id: null clears it; the other fields cannot be null."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, **_CODE)
    parent_id: int | None = Field(
        default=None, gt=0, strict=True, description="换父节点：整棵子树一起移动；null 移到根"
    )
    names: list[CategoryName] | None = Field(
        default=None, min_length=1, max_length=CATEGORY_MAX_NAMES, description="给了就整体替换全部名称"
    )
    cuisines: list[Cuisine] | None = Field(default=None, max_length=CATEGORY_MAX_CUISINES)
    heating_group_id: int | None = Field(default=None, gt=0, strict=True, description="null 清空")
    sort_order: int | None = Field(default=None, **_SORT_ORDER)
    is_active: bool | None = Field(default=None, strict=True)
