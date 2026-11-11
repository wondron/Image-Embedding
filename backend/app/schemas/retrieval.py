"""Retrieval wire: index / search / list (search and list follow contract §6.1).

Routes declare these as response_model; search/list use exclude_unset so the
fields §6.1 makes conditional (scene_label, food_state, heating_group) appear
only when the use case set them.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


# -- requests ---------------------------------------------------------------------------

class IndexJsonRequest(BaseModel):
    """JSON body when the file is already on a path the API process can read (§3.1)."""

    path: str = Field(..., description="Local path reachable by the API process")
    description: str = Field(default="", description="图片描述，默认空字符串")
    category: str | None = None
    tags: list[str] | None = Field(
        default=None, description="图片标签，如 [\"红薯叶\"]；不传为空数组。去首尾空白、去重，最多 32 个，每个最长 64 字符"
    )
    scene: str | None = None
    scenes: list[Any] | None = None
    label_status: str | None = Field(
        default=None,
        description="标注状态 auto / verified（要有类别）；不传时有类别为 auto、没有类别为 unlabeled",
    )
    source_type: str | None = Field(default=None, description="来源，取值见 GET /api/enums/source_type；默认 user_upload")
    license: str | None = Field(default=None, description="许可，取值见 GET /api/enums/license；默认 unknown")
    # optional food annotation (stored in image_foods); the use case checks it
    # (app.services.retrieval.food_fields). Strict ints: JSON true must not count as 1
    food_state: str | None = Field(default=None, description="食物状态，取值见 GET /api/enums/food_state")
    food_processing: str | None = Field(
        default=None, description="加工状态，取值见 GET /api/enums/food_processing"
    )
    food_count: int | None = Field(default=None, gt=0, strict=True, description="食物个数")
    total_mass_g: int | None = Field(default=None, gt=0, strict=True, description="总质量（克）")
    water_mass_g: int | None = Field(default=None, ge=0, strict=True, description="含水质量（克），可以为 0")
    main_ingredient_mass_g: int | None = Field(default=None, gt=0, strict=True, description="主食材质量（克）")


class SearchJsonRequest(BaseModel):
    """JSON search body (§3.2 B)."""

    img_url: str = Field(..., description="Remote query image URL")
    scene: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    score_threshold: float | None = Field(
        default=None,
        description="omit/null=0.55 reject; 0=off; else [0.30,1] (inclusive). SSOT section 3.2.1",
    )
    trace_id: str | None = None


# -- shared parts -----------------------------------------------------------------------

class HeatingGroup(BaseModel):
    id: int
    code: str
    name: str


# -- POST /api/index ----------------------------------------------------------------------

class IndexedImage(BaseModel):
    id: str
    filename: str
    original_url: str
    thumb_url: str
    category_id: int | None = None
    scenes: list[str] = Field(default_factory=list)
    label_status: str
    food_state: str | None = None
    food_processing: str | None = None
    food_count: int | None = None
    total_mass_g: int | None = None
    water_mass_g: int | None = None
    main_ingredient_mass_g: int | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    width: int
    height: int
    payload_version: int | None = None
    created_at: str | None = None
    created: bool | None = Field(
        default=None,
        description=(
            "true：这次请求新入库；false：相同图片（字节完全相同）此前已入库，"
            "保留首次入库的类别、标签等字段，本次请求里的标注没有写入"
        ),
    )


class IndexFailure(BaseModel):
    filename: str
    error: str
    detail: str


class IndexResponse(BaseModel):
    ok: bool = True
    trace_id: str
    indexed: list[IndexedImage] = Field(default_factory=list)
    failed: list[IndexFailure] = Field(default_factory=list)


# -- POST /api/search (§6.1.1) ------------------------------------------------------------

class SearchHit(BaseModel):
    id: str
    original_url: str | None = None
    thumb_url: str | None = None
    similarity: float | None = None
    category_id: int | None = None
    category: str | None = None
    scenes: list[str] | None = None
    scene_label: str | None = None
    label_status: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    # food annotation: only when the scene outputs_heating (§6.1 P9), like heating_group
    food_state: str | None = None
    food_processing: str | None = None
    food_count: int | None = None
    total_mass_g: int | None = None
    water_mass_g: int | None = None
    main_ingredient_mass_g: int | None = None
    heating_group: HeatingGroup | None = None


class SearchResponse(BaseModel):
    ok: bool = True
    trace_id: str
    source: Literal["self", "empty"]
    scene: str
    outputs_heating: bool = False
    items: list[SearchHit] = Field(default_factory=list)
    message: str | None = None


# -- GET /api/images (§6.1.2) -------------------------------------------------------------

class ListImageItem(BaseModel):
    id: str
    original_url: str | None = None
    thumb_url: str | None = None
    category_id: int | None = None
    category: str | None = None
    scenes: list[str] | None = None
    label_status: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    # food annotation: only when the scene outputs_heating (§6.1 P9), like heating_group
    food_state: str | None = None
    food_processing: str | None = None
    food_count: int | None = None
    total_mass_g: int | None = None
    water_mass_g: int | None = None
    main_ingredient_mass_g: int | None = None
    heating_group: HeatingGroup | None = None
    created_at: str | None = None
    updated_at: str | None = None
    vector_status: Literal["synced", "stale", "missing", "unavailable"] | None = Field(
        default=None,
        description=(
            "这张图在当前模型 collection 里的状态（同 GET /api/images/{image_id} 的 vector.status）："
            "synced 一致；stale payload 落后于 PostgreSQL；missing 没有向量；unavailable 检索栈未就绪或读向量库失败"
        ),
    )


class ListImagesResponse(BaseModel):
    ok: bool = True
    trace_id: str
    scene: str
    page: int
    page_size: int
    total: int
    items: list[ListImageItem] = Field(default_factory=list)
