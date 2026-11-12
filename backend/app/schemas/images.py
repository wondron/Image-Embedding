"""One stored image as the gallery admin reads and edits it.

GET / PATCH / DELETE /api/images/{image_id}, POST /api/images/{image_id}/vector-sync. The image
carries the list item's fields (§6.1.2, same names) plus what only the detail shows; food fields
and heating_group are always present. Success bodies carry no trace_id (it is in the X-Trace-Id
header), as for the scene memberships. Field rules: app.core.rules.image_fields.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import LabelStatus
from app.schemas.retrieval import HeatingGroup

_PG_INT_MAX = 2_147_483_647
VectorSync = Literal["pushed", "missing", "not_ready", "failed"]
_VECTOR_SYNC = (
    "pushed：当前向量库已是新 payload；missing：这张图在当前向量库里没有向量（需要重新入库）；"
    "not_ready：检索栈未就绪，没有推送；failed：推送或记账失败，或图片在推送期间一直被改（见日志）"
)
_SYNC_REQUIRED = "false：检索已能看到修改；true：要等 POST /api/scenes/sync（或修好后用 vector-sync 重推）"


# -- responses --------------------------------------------------------------------------

class ImageEmbeddingRecord(BaseModel):
    """One image_embeddings row: the image's point is in this model version's collection."""

    model_id: int
    name: str = Field(..., description="模型名")
    version: str
    collection: str = Field(..., description="这个模型版本的 Qdrant collection")
    model_status: str = Field(..., description="模型状态：building / ready / current / retired")
    synced_version: int = Field(..., description="写进这个 collection 的 payload 对应的 payload_version")
    embedded_at: str | None = None


class ImageDetail(BaseModel):
    id: str
    sha256: str = Field(..., description="原图 SHA-256（hex）；id 是它的前 16 字节")
    original_url: str
    thumb_url: str
    width: int
    height: int
    category_id: int | None = Field(..., description="类别；未标注时为 null")
    category: str | None = Field(..., description="类别的中文主名")
    category_code: str | None = Field(..., description="类别编码，如 dish.steak")
    label_status: LabelStatus
    scenes: list[str] = Field(..., description="有效场景编码（含 general），按场景 id 排")
    description: str
    tags: list[str]
    source_type: str
    license: str
    food_state: str | None
    food_processing: str | None
    food_count: int | None
    total_mass_g: int | None
    water_mass_g: int | None
    main_ingredient_mass_g: int | None
    heating_group: HeatingGroup | None = Field(..., description="类别或最近祖先的加热组")
    payload_version: int = Field(..., description="类别、标注状态、食物状态或所属场景变化时加 1")
    created_at: str | None = None
    updated_at: str | None = None
    embeddings: list[ImageEmbeddingRecord] = Field(
        ..., description="向量写入记录：这张图已写进哪些模型版本的 collection（image_embeddings）"
    )


class ImageVectorState(BaseModel):
    """The image in the current model's collection (services.retrieval.vector_status)."""

    status: Literal["synced", "stale", "missing", "unavailable"] = Field(
        ...,
        description=(
            "synced：有向量且 payload 和 PostgreSQL 一致；stale：有向量但 payload 落后（可用 vector-sync 重推）；"
            "missing：当前 collection 没有这张图的向量（检索不到，需要重新入库）；"
            "unavailable：检索栈未就绪或读向量库失败（见 detail）"
        ),
    )
    collection: str | None = Field(default=None, description="当前模型的 collection")
    embedding_space_id: str | None = Field(default=None, description="当前模型 name:version")
    exists: bool | None = Field(default=None, description="当前 collection 里有没有这张图的点")
    payload: dict[str, Any] | None = Field(default=None, description="向量库里这个点现在的 payload")
    expected_payload: dict[str, Any] | None = Field(
        default=None, description="按 PostgreSQL 现在的数据应有的 payload（category_id、scenes、food_state、label_status）"
    )
    payload_matches: bool | None = Field(default=None, description="payload 是否和应有的一致（scenes 按集合比）")
    payload_version: int | None = Field(default=None, description="PostgreSQL 里图片当前的 payload_version")
    synced_version: int | None = Field(default=None, description="当前模型记账的 payload_version；没有记账时为 null")
    embedded_at: str | None = None
    detail: str | None = Field(default=None, description="status=unavailable 时的原因")


class ImageDetailResponse(BaseModel):
    ok: Literal[True] = True
    image: ImageDetail
    vector: ImageVectorState


_VECTOR_AFTER = "写入并推送之后这张图在当前 collection 里的状态（同 GET 详情的 vector），页面不必再读一次详情"


class ImageUpdateResponse(BaseModel):
    ok: Literal[True] = True
    image: ImageDetail
    vector: ImageVectorState | None = Field(default=None, description=_VECTOR_AFTER)
    vector_sync: VectorSync = Field(..., description=_VECTOR_SYNC)
    sync_required: bool = Field(..., description=_SYNC_REQUIRED)


class ImageVectorSyncResponse(BaseModel):
    ok: Literal[True] = True
    image_id: str
    payload_version: int
    vector: ImageVectorState | None = Field(default=None, description=_VECTOR_AFTER)
    vector_sync: VectorSync = Field(..., description=_VECTOR_SYNC)
    sync_required: bool = Field(..., description=_SYNC_REQUIRED)


class ImageReembedResponse(BaseModel):
    ok: Literal[True] = True
    image_id: str
    payload_version: int
    vector: ImageVectorState = Field(..., description="重新写入向量之后这张图在当前 collection 里的状态")


class ImageDeleteResponse(BaseModel):
    ok: Literal[True] = True
    image_id: str
    deleted_at: str | None = None
    cleanup: Literal["done", "queued"] = Field(
        ...,
        description=(
            "done：向量点和原图、缩略图已删除；queued：检索栈未就绪或清理失败，"
            "留在 image_deletions 里由 python -m app.workers.process_deletions 补删。"
            "两种情况下图片都已从图库和检索结果里消失"
        ),
    )


# -- requests ---------------------------------------------------------------------------

class ImageUpdateRequest(BaseModel):
    """Only the fields sent are changed; see app.core.rules.image_fields for what null means."""

    model_config = ConfigDict(extra="forbid")

    category_id: int | None = Field(
        default=None, gt=0, le=_PG_INT_MAX, strict=True,
        description="换类别（必须存在且启用）；null 清空，图片变成未标注",
    )
    label_status: LabelStatus | None = Field(
        default=None,
        description=(
            "不传时跟着类别走：清空类别 → unlabeled；设置或更换类别 → verified；类别不变 → 保持。"
            "传了必须和类别一致（unlabeled ⟺ 没有类别），不能为 null"
        ),
    )
    description: str | None = Field(default=None, description="图片描述；null 清空。最长 1000 字符")
    tags: list[str] | None = Field(
        default=None, description="整体替换标签；null 或 [] 清空。去首尾空白、去重，最多 32 个，每个最长 64 字符"
    )
    food_state: str | None = Field(default=None, description="食物状态，取值见 GET /api/enums/food_state；null 清空")
    food_processing: str | None = Field(
        default=None, description="加工状态，取值见 GET /api/enums/food_processing；null 清空"
    )
    food_count: int | None = Field(default=None, gt=0, le=_PG_INT_MAX, strict=True, description="食物个数；null 清空")
    total_mass_g: int | None = Field(default=None, gt=0, le=_PG_INT_MAX, strict=True, description="总质量（克）")
    water_mass_g: int | None = Field(
        default=None, ge=0, le=_PG_INT_MAX, strict=True, description="含水质量（克），可以为 0"
    )
    main_ingredient_mass_g: int | None = Field(
        default=None, gt=0, le=_PG_INT_MAX, strict=True, description="主食材质量（克）"
    )
