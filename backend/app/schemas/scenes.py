"""Scenes wire: the scene configuration and the membership sync.

    GET    /api/scenes                                  every scene with its labels and rules
    POST   /api/scenes                                  create a scene (201)
    PATCH  /api/scenes/{scene_id}                       change name / description / flags
    DELETE /api/scenes/{scene_id}                       delete a scene (not the default one)
    POST   /api/scenes/{scene_id}/labels                add a label (201)
    PATCH  /api/scenes/{scene_id}/labels/{label_id}     change a label
    DELETE /api/scenes/{scene_id}/labels/{label_id}     delete a label no rule uses
    POST   /api/scenes/{scene_id}/rules                 add a rule (201)
    PATCH  /api/scenes/{scene_id}/rules/{rule_id}       change a rule's label
    DELETE /api/scenes/{scene_id}/rules/{rule_id}       delete a rule
    POST   /api/scenes/sync                             recompute one batch of stored images

Writes to a scene, its labels or its rules answer with the whole scene. Success bodies carry no
trace_id (it is in the X-Trace-Id header, as for /api/enums and /api/categories). Request limits
are app.core.scene_rules; app.services.retrieval.scene_fields checks them again together with the
rules a schema cannot say.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.category_rules import (
    CATEGORY_CUISINE_MAX_LEN,
    CATEGORY_CUISINE_PATTERN,
    CATEGORY_SORT_ORDER_MAX,
    CATEGORY_SORT_ORDER_MIN,
)
from app.core.scene_rules import (
    SCENE_CODE_MAX_LEN,
    SCENE_CODE_PATTERN,
    SCENE_DESCRIPTION_MAX_LEN,
    SCENE_LABEL_CODE_MAX_LEN,
    SCENE_LABEL_CODE_PATTERN,
    SCENE_LABEL_NAME_MAX_LEN,
    SCENE_NAME_MAX_LEN,
    SCENE_SYNC_BATCH_DEFAULT,
    SCENE_SYNC_BATCH_MAX,
)

# -- responses --------------------------------------------------------------------------


class SceneLabelItem(BaseModel):
    id: int
    code: str = Field(..., description="场景内唯一，如 steak")
    name: str = Field(..., description="检索结果里的 scene_label")
    sort_order: int
    is_active: bool = Field(..., description="停用后检索不再输出它，命中改按更上层的规则取类别")
    rule_count: int = Field(..., description="映射到它的规则数；为 0 才能删除")


class SceneRuleCategory(BaseModel):
    id: int
    code: str
    name: str | None = Field(..., description="中文主名；没有时为 null")


class SceneRuleLabel(BaseModel):
    id: int
    code: str
    name: str


class SceneRuleItem(BaseModel):
    id: int
    category: SceneRuleCategory | None = Field(..., description="按类别（含全部子孙）；按菜系的规则为 null")
    cuisine: str | None = Field(..., description="按菜系；按类别的规则为 null")
    label: SceneRuleLabel | None = Field(..., description="命中的图在本场景里归为哪个场景类别；可为空")


class SceneItem(BaseModel):
    id: int
    code: str = Field(..., description="接口按它选场景，也写进向量 payload；建好后不能改")
    name: str
    description: str | None
    include_all: bool = Field(..., description="是否包含全部图片（不看规则）")
    outputs_heating: bool = Field(..., description="检索结果是否另带加热组和食物属性")
    is_active: bool = Field(..., description="停用后不能检索，图片也不计入它；重新启用后需要重算场景归属")
    created_at: str | None = None
    member_count: int = Field(..., description="当前归属的图片数（按已计算的结果）；停用时为 0")
    labels: list[SceneLabelItem] = Field(..., description="按 sort_order、id 排")
    rules: list[SceneRuleItem] = Field(..., description="按类别的规则在前（按类别树顺序），按菜系的在后")


class SceneListResponse(BaseModel):
    ok: bool = True
    scenes: list[SceneItem] = Field(..., description="全部场景，按 id 排")
    default_scene: str = Field(..., description="请求没给场景时用的场景编码")


class SceneResponse(BaseModel):
    ok: bool = True
    scene: SceneItem


class DeletedScene(BaseModel):
    id: int
    code: str


class SceneDeleteResponse(BaseModel):
    ok: bool = True
    deleted: DeletedScene


class SceneSyncResponse(BaseModel):
    ok: bool = True
    processed: int = Field(..., description="这一批处理的图片数")
    membership_changed: int = Field(..., description="其中场景归属有变化的图片数")
    payload_pushed: int = Field(..., description="写进当前向量库 payload 的图片数")
    payload_missing: list[str] = Field(..., description="当前向量库里没有向量的图片 id（需要重新入库）")
    next_after: str | None = Field(..., description="下一批从这个图片 id 之后开始；完成时为 null")
    done: bool


# -- requests ---------------------------------------------------------------------------

_SORT_ORDER = dict(ge=CATEGORY_SORT_ORDER_MIN, le=CATEGORY_SORT_ORDER_MAX, strict=True)


class SceneCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(
        ...,
        min_length=1,
        max_length=SCENE_CODE_MAX_LEN,
        pattern=SCENE_CODE_PATTERN,
        description="小写字母、数字、下划线，字母开头；建好后不能改",
        examples=["steam_oven"],
    )
    name: str = Field(..., min_length=1, max_length=SCENE_NAME_MAX_LEN, examples=["蒸烤箱"])
    description: str | None = Field(default=None, max_length=SCENE_DESCRIPTION_MAX_LEN)
    include_all: bool = Field(default=False, strict=True)
    outputs_heating: bool = Field(default=False, strict=True)
    is_active: bool = Field(default=True, strict=True)


class SceneUpdateRequest(BaseModel):
    """Only the fields sent change; code cannot change."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=SCENE_NAME_MAX_LEN)
    description: str | None = Field(default=None, max_length=SCENE_DESCRIPTION_MAX_LEN)
    include_all: bool | None = Field(default=None, strict=True)
    outputs_heating: bool | None = Field(default=None, strict=True)
    is_active: bool | None = Field(default=None, strict=True)


class SceneLabelCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(
        ..., min_length=1, max_length=SCENE_LABEL_CODE_MAX_LEN, pattern=SCENE_LABEL_CODE_PATTERN, examples=["steak"]
    )
    name: str = Field(..., min_length=1, max_length=SCENE_LABEL_NAME_MAX_LEN, examples=["牛排"])
    sort_order: int = Field(default=0, **_SORT_ORDER)
    is_active: bool = Field(default=True, strict=True)


class SceneLabelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(
        default=None, min_length=1, max_length=SCENE_LABEL_CODE_MAX_LEN, pattern=SCENE_LABEL_CODE_PATTERN
    )
    name: str | None = Field(default=None, min_length=1, max_length=SCENE_LABEL_NAME_MAX_LEN)
    sort_order: int | None = Field(default=None, **_SORT_ORDER)
    is_active: bool | None = Field(default=None, strict=True)


class SceneRuleCreateRequest(BaseModel):
    """Exactly one of category_id and cuisine; label_id only with category_id."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category_id: int | None = Field(default=None, gt=0, strict=True, description="这个类别及其全部子孙")
    cuisine: str | None = Field(
        default=None,
        min_length=1,
        max_length=CATEGORY_CUISINE_MAX_LEN,
        pattern=CATEGORY_CUISINE_PATTERN,
        description="菜系（含沿用祖先的）包含它的类别",
        examples=["chinese"],
    )
    label_id: int | None = Field(default=None, gt=0, strict=True, description="本场景的场景类别")


class SceneRuleUpdateRequest(BaseModel):
    """A rule's target is fixed; only its label changes (null clears it)."""

    model_config = ConfigDict(extra="forbid")

    label_id: int | None = Field(..., gt=0, strict=True)


class SceneSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    after: str | None = Field(default=None, description="从这个图片 id 之后开始；不传从头开始")
    limit: int = Field(default=SCENE_SYNC_BATCH_DEFAULT, ge=1, le=SCENE_SYNC_BATCH_MAX, strict=True)
