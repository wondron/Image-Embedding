"""场景的 wire：场景配置和归属同步。

    GET    /api/scenes                                  所有场景及其标签和规则
    POST   /api/scenes                                  创建场景（201）
    PATCH  /api/scenes/{scene_id}                       修改名称 / 描述 / 开关
    DELETE /api/scenes/{scene_id}                       删除场景（默认场景除外）
    POST   /api/scenes/{scene_id}/labels                添加标签（201）
    PATCH  /api/scenes/{scene_id}/labels/{label_id}     修改标签
    DELETE /api/scenes/{scene_id}/labels/{label_id}     删除没有规则使用的标签
    POST   /api/scenes/{scene_id}/rules                 添加规则（201）
    PATCH  /api/scenes/{scene_id}/rules/{rule_id}       修改规则的对象和 / 或标签
    DELETE /api/scenes/{scene_id}/rules/{rule_id}       删除规则
    POST   /api/scenes/sync                             重算一批已存图片

对场景、它的标签或规则的写入返回整个场景和 sync_required：修改只有通过
POST /api/scenes/sync 才能作用到已存图片时为 true（规则、场景的 is_active / include_all），
已经生效时为 false（标签、名称、描述、outputs_heating）。成功的响应体不带 trace_id
（它在 X-Trace-Id 响应头里，和 /api/enums、/api/categories 一样）。请求的限制来自
app.core.scene_rules；app.core.rules.scene_fields 会再检查一次，同时检查 schema 表达不了的
规则。
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

# -- 响应 ---------------------------------------------------------------------------------


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


_SYNC_REQUIRED_DOC = (
    "true：改动影响已入库图片的场景归属，要「重算场景归属」（POST /api/scenes/sync）后才作用到它们；"
    "false：已经生效（场景类别、名称、描述、加热输出在请求时读取）"
)


class SceneResponse(BaseModel):
    ok: bool = True
    scene: SceneItem
    sync_required: bool = Field(..., description=_SYNC_REQUIRED_DOC)


class DeletedScene(BaseModel):
    id: int
    code: str


class SceneDeleteResponse(BaseModel):
    ok: bool = True
    deleted: DeletedScene
    sync_required: bool = Field(..., description=_SYNC_REQUIRED_DOC)


class SceneSyncResponse(BaseModel):
    ok: bool = True
    processed: int = Field(..., description="这一批处理的图片数")
    membership_changed: int = Field(..., description="其中场景归属有变化的图片数")
    payload_pushed: int = Field(..., description="写进当前向量库 payload 的图片数")
    payload_missing: list[str] = Field(..., description="当前向量库里没有向量的图片 id（需要重新入库）")
    next_after: str | None = Field(..., description="下一批从这个图片 id 之后开始；完成时为 null")
    done: bool


# -- 请求 ---------------------------------------------------------------------------------

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
    """只修改传来的字段；code 不能修改。"""

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
    """category_id 和 cuisine 恰好一个；label_id 只能和 category_id 一起出现。"""

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
    """只修改传来的字段。新的对象是 category_id 或 cuisine（恰好一个非 null；另一个被清空）；
    label_id 为 null 时清空标签。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category_id: int | None = Field(default=None, gt=0, strict=True, description="换成按这个类别（含全部子孙）")
    cuisine: str | None = Field(
        default=None,
        min_length=1,
        max_length=CATEGORY_CUISINE_MAX_LEN,
        pattern=CATEGORY_CUISINE_PATTERN,
        description="换成按这个菜系；按菜系的规则不能映射场景类别",
    )
    label_id: int | None = Field(default=None, gt=0, strict=True, description="本场景的场景类别；null 清空")


class SceneSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    after: str | None = Field(default=None, description="从这个图片 id 之后开始；不传从头开始")
    limit: int = Field(default=SCENE_SYNC_BATCH_DEFAULT, ge=1, le=SCENE_SYNC_BATCH_MAX, strict=True)
