"""Model versions wire (/api/embedding-models): list, register, build, switch, retire.

Success bodies carry no trace_id (it is in the X-Trace-Id header), as for the gallery admin.
Runtime: app.bootstrap.model_registry; rules: app.services.retrieval.model_versions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Distance, ModelStatus
from app.services.retrieval.model_versions import LABEL_MAX_LEN

EncoderSource = Literal["configured", "serving", "recorded"]
BuildStateName = Literal["running", "stopping", "done", "stopped", "failed"]


class EmbeddingModelItem(BaseModel):
    id: int
    name: str
    version: str
    status: ModelStatus = Field(..., description="building 生成向量中 / ready 可切换 / current 当前 / retired 已下线")
    collection: str = Field(..., description="这个版本的 Qdrant collection")
    dim: int
    distance: Distance
    weights_uri: str = Field(..., description="权重位置（登记时的 EMBEDDING_CONFIG.model_path）")
    created_at: str | None = None
    activated_at: str | None = Field(default=None, description="最近一次切换为当前的时间")
    embedded: int = Field(..., description="有向量的图片数（写入记录）")
    missing: int = Field(..., description="还没有向量的图片数：切换到这个版本后检索不到它们；已下线的为 0")
    behind: int = Field(..., description="向量在、过滤信息（payload）落后于数据库的图片数")
    points: int | None = Field(..., description="collection 里实际的点数；读不到（或 collection 不在）为 null。少于 embedded 说明有向量丢了，生成向量会先核对补上")
    weights: Literal["ok", "missing", "changed"] | None = Field(
        ..., description="它的权重文件在本镜像里：ok 在且未变 / missing 不在了 / changed 被换过；null 没记录文件或已下线"
    )
    artifact: str | None = Field(..., description="权重文件的 SHA-256（多个文件时是它们的合并 hash），可用来给权重目录命名")
    fingerprint: bool = Field(..., description="是否记录了编码器指纹；没有时无法确认是哪个编码器写的")
    configured: bool = Field(..., description="EMBEDDING_CONFIG 现在配置的编码器就是这个版本的")
    serving: bool = Field(..., description="本进程的检索正在用这个版本")
    encoder: EncoderSource | None = Field(
        ...,
        description=(
            "这个版本的编码器：configured 就是配置的那个 / serving 检索正在用的那个（已加载） / "
            "recorded 没加载，切换时按指纹记录的权重重新加载 / null 没有指纹"
        ),
    )


class ConfiguredEncoder(BaseModel):
    loaded: bool = Field(..., description="EMBEDDING_CONFIG 的编码器是否加载成功")
    model_path: str | None = None
    dim: int | None = None
    artifact: str | None = Field(default=None, description="配置的权重文件的 SHA-256（多个文件时是合并 hash）")
    model_id: int | None = Field(..., description="它是哪个版本；null 表示还没登记")
    detail: str | None = Field(default=None, description="没加载成功的原因")


class RetrievalPairing(BaseModel):
    ready: bool = Field(..., description="检索（搜索 / 入库）是否可用")
    model_id: int | None = Field(..., description="检索正在用的版本")
    encoder: Literal["configured", "own"] | None = Field(
        ..., description="configured：检索用的就是配置的编码器 / own：配置的是另一个模型，检索用这个版本自己的编码器"
    )
    code: str | None = Field(default=None, description="不可用时的错误码，如 EMBEDDING_MODEL_MISMATCH")
    detail: str | None = None


class BuildFailure(BaseModel):
    image_id: str
    code: str = Field(..., description="IMAGE_FILE_UNAVAILABLE / INDEX_BAD_IMAGE / INDEX_BAD_EMBEDDING")
    detail: str


class BuildStatus(BaseModel):
    model_id: int
    state: BuildStateName = Field(..., description="running / stopping / done / stopped / failed")
    started_at: str
    finished_at: str | None = None
    verified: int = Field(..., description="开始时核对过的写入记录数（对照 collection 里实际的点）")
    repaired: int = Field(..., description="其中点已不在、重新列入待生成的图片数")
    embedded: int = Field(..., description="这次写入向量的图片数")
    failed: int = Field(..., description="这次没能生成向量的图片数")
    gone: int = Field(..., description="处理时已被删除的图片数")
    payloads: int = Field(..., description="生成后补推过滤信息的图片数")
    passes: int
    remaining: int | None = Field(default=None, description="结束时仍没有向量的图片数")
    error: str | None = Field(default=None, description="中止原因（state=failed）")
    failures: list[BuildFailure] = Field(..., description="没能生成的图片（最多 50 条）")


class EmbeddingModelsResponse(BaseModel):
    ok: bool = True
    images: int = Field(..., description="图库里的图片总数")
    configured: ConfiguredEncoder
    retrieval: RetrievalPairing
    build: BuildStatus | None = Field(..., description="本进程最近一次生成向量；没有为 null")
    switch_catch_up_max: int = Field(
        ..., description="切换时（入库暂停期间）最多补多少张没有向量的图片；缺得更多的版本要先生成向量，或 allow_missing=true"
    )
    models: list[EmbeddingModelItem]


class EmbeddingModelRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=LABEL_MAX_LEN, description="不传用权重文件名")
    version: str | None = Field(default=None, max_length=LABEL_MAX_LEN, description="不传用当前时间，如 20261112-1530")


class EmbeddingModelResponse(BaseModel):
    ok: bool = True
    model: EmbeddingModelItem


class EmbeddingModelActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_missing: bool = Field(
        default=False, description="还有图片没有向量时也切换（它们在补齐前检索不到）；默认 false：返回 EMBEDDING_MODEL_INCOMPLETE"
    )


class EmbeddingModelActivateResponse(BaseModel):
    ok: bool = True
    model: EmbeddingModelItem
    previous_id: int | None = Field(..., description="切换前的当前版本（现在是 ready，可回滚）；没有切换时为 null")
    switched: bool = Field(..., description="false：它本来就是当前版本，检索也已经在用它")
    caught_up: int = Field(..., description="切换时（入库暂停期间）补上向量的图片数")
    remaining: int = Field(..., description="切换后仍没有向量的图片数（allow_missing=true 时可能大于 0）")


class EmbeddingModelRetireResponse(BaseModel):
    ok: bool = True
    model: EmbeddingModelItem
    dropped: bool = Field(..., description="是否删除了它的 collection（本来就不在时为 false）")
    ledger_rows: int = Field(..., description="删除的写入记录条数")


class EmbeddingBuildResponse(BaseModel):
    ok: bool = True
    build: BuildStatus | None
