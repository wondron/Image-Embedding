"""数据核对的 wire（/api/consistency）：开始、停止、看进度和最近一次的报告。

成功的响应体不带 trace_id（它在 X-Trace-Id 响应头里），和模型版本一样。
运行时：app.bootstrap.consistency；核对规则：app.services.retrieval.consistency。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CheckState = Literal["running", "stopping", "done", "stopped", "failed"]
IssueLevel = Literal["error", "warn", "info"]


class ConsistencyCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vectors: bool = Field(default=True, description="PostgreSQL ↔ Qdrant：每个在用版本的点、写入记录和 payload")
    files: bool = Field(default=True, description="PostgreSQL → 对象存储：每张图的原图和缩略图还在不在（HEAD，不下载）")
    orphans: bool = Field(
        default=True, description="反过来找多余的：collection 里、对象存储 original/ thumb/ 下有，数据库里没有的"
    )
    content: bool = Field(default=False, description="下载每张原图核对 SHA-256（慢；需要 files）")
    encoders: bool = Field(default=True, description="用已加载的编码器重算几张图，确认 collection 是这个版本的编码器写的")


class Finding(BaseModel):
    count: int
    samples: list[str] = Field(..., description="前 20 个：图片 id、点 id 或对象 key")


class CheckProgress(BaseModel):
    phase: str = Field(..., description="starting / collections / images / points / files / recheck / encoders")
    done: int
    total: int | None = None
    label: str | None = Field(default=None, description="正在核对的版本（name:version）或目录")


class PostgresConnection(BaseModel):
    ok: bool
    host: str | None = None
    database: str | None = None
    detail: str | None = None


class QdrantConnection(BaseModel):
    ok: bool
    url: str | None = Field(default=None, description="本进程实际连着的地址（启动时的 QDRANT_CONFIG）")
    configured_url: str | None = Field(default=None, description="现在配置里（含 Nacos 热更新）的地址，和 url 不同时才有")
    restart_required: bool = Field(..., description="配置里的地址改了，本进程要重启才会改连过去")
    collections: int | None = Field(default=None, description="这个 Qdrant 上 collection 的个数")
    detail: str | None = None


class StorageConnection(BaseModel):
    ok: bool
    driver: str | None = Field(default=None, description="boto3 / mock")
    bucket: str | None = None
    endpoint_url: str | None = None
    public_base_url: str | None = None
    key_prefix: str | None = None
    detail: str | None = None


class ServingModel(BaseModel):
    model_id: int | None = Field(..., description="本进程检索在用的版本；检索没起来为 null")
    label: str | None = None
    detail: str | None = None


class Connections(BaseModel):
    postgres: PostgresConnection
    qdrant: QdrantConnection
    storage: StorageConnection
    serving: ServingModel


class DatabaseCounts(BaseModel):
    images: int
    pending_deletions: int = Field(..., description="删除队列里等着清理向量和文件的图片")
    oldest_deletion_at: str | None = None
    current_models: int = Field(..., description="status=current 的版本数；正常是 1")


class CollectionInfo(BaseModel):
    exists: bool | None = Field(..., description="collection 在不在；null：没读到")
    dim: int | None = None
    distance: str | None = None
    points: int | None = Field(default=None, description="实际的点数（精确值）")
    matches: bool | None = Field(default=None, description="维度和距离与 embedding_models 登记的一致")


class EncoderCheck(BaseModel):
    status: Literal["ok", "mismatch", "unchecked", "not_loaded", "off"] = Field(
        ..., description="ok 向量是它写的 / mismatch 不是 / unchecked 没能核对 / not_loaded 编码器没加载 / off 没选"
    )
    source: Literal["configured", "serving"] | None = None
    checked: int = 0
    min_similarity: float | None = None
    detail: str | None = None


class VersionFindings(BaseModel):
    missing: Finding = Field(..., description="没有点，也没有写入记录")
    lost: Finding = Field(..., description="有写入记录，collection 里没有点")
    unrecorded: Finding = Field(..., description="有点，没有写入记录")
    ledger_behind: Finding = Field(..., description="写入记录落后于 payload_version（会自动补推）")
    stale_payload: Finding = Field(..., description="写入记录是最新的，payload 却和数据库不一样")
    orphan_points: Finding = Field(..., description="点对应的图片不在数据库里，也不在删除队列里")
    pending_points: Finding = Field(..., description="点对应的图片在删除队列里")


class VersusCurrent(BaseModel):
    only_current: Finding = Field(..., description="当前版本有点、这个版本没有的图片")
    only_this: Finding = Field(..., description="这个版本有点、当前版本没有的图片")


class VersionReport(BaseModel):
    id: int
    label: str
    status: str
    collection: str
    dim: int
    distance: str
    serving: bool
    weights: Literal["ok", "missing", "changed"] | None = None
    qdrant: CollectionInfo
    error: str | None = Field(default=None, description="读这个版本的 collection 出错（之后的核对跳过它）")
    scanned: int = Field(..., description="核对过的图片数")
    ledger: int = Field(..., description="其中有写入记录的")
    with_vector: int = Field(..., description="其中 collection 里有点的")
    findings: VersionFindings
    vs_current: VersusCurrent | None = Field(default=None, description="和当前版本比较；当前版本自己为 null")
    encoder: EncoderCheck


class StrayCollection(BaseModel):
    name: str
    model_id: int | None = Field(default=None, description="登记过它的版本（已下线）；null：不是本服务登记的")
    label: str | None = None
    status: str | None = None


class FileFindings(BaseModel):
    missing_original: Finding
    missing_thumb: Finding
    unreadable: Finding = Field(..., description="说不清在不在（无权访问、网络出错）")
    content_mismatch: Finding = Field(..., description="原图内容和 sha256 不一致（content 时）")
    orphan_files: Finding = Field(..., description="对象存储里有、数据库里没有引用的对象（key）")
    pending_files: Finding = Field(..., description="只有删除队列引用的对象（key）")


class FilesReport(BaseModel):
    skipped: str | None = Field(default=None, description="没核对文件的原因（例如 mock 驱动没有镜像目录）")
    checked: int
    first_error: str | None = None
    listed: int | None = Field(default=None, description="列出的对象数（orphans 时）")
    referenced: int | None = None
    truncated: bool
    list_error: str | None = None
    findings: FileFindings


class Issue(BaseModel):
    level: IssueLevel
    code: str = Field(..., description="页面按它给出说明和去哪里修，如 missing_vectors、orphan_points、missing_original")
    count: int | None = None
    version_id: int | None = None
    label: str | None = None
    detail: str | None = None


class CheckOptionsEcho(BaseModel):
    vectors: bool
    files: bool
    orphans: bool
    content: bool
    encoders: bool


class ConsistencyReport(BaseModel):
    status: Literal["ok", "warn", "error"]
    complete: bool = Field(..., description="false：中途停下了，只核对了一部分")
    options: CheckOptionsEcho
    duration_ms: int
    connections: Connections
    database: DatabaseCounts
    versions: list[VersionReport]
    stray_collections: list[StrayCollection] | None = Field(
        default=None, description="这个 Qdrant 上不属于任何在用版本的 collection"
    )
    stray_error: str | None = None
    files: FilesReport | None = None
    issues: list[Issue] = Field(..., description="最严重的在前")


class ConsistencyRun(BaseModel):
    state: CheckState
    started_at: str
    finished_at: str | None = None
    options: CheckOptionsEcho
    progress: CheckProgress
    error: str | None = Field(default=None, description="核对本身失败的原因（state=failed）")
    report: ConsistencyReport | None = Field(default=None, description="结束（done / stopped）后的报告")


class ConsistencyResponse(BaseModel):
    ok: bool = True
    check: ConsistencyRun | None = Field(..., description="本进程最近一次核对；还没核对过为 null")
