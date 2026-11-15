"""Batch search wire (POST /api/search/batch/files, POST /api/search/batch/urls).

Several query images against the self-built library in one request, all with the same scene, top_k
and score_threshold (app.api.services.batch_search). The answer has one entry per query image, in
the order they were sent: its hits (the result cards of POST /api/search), or why that image
could not be searched. The global search is not involved.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.retrieval import SearchHit

BATCH_MAX_IMAGES = 30
TOP_K_DEFAULT = 10
TOP_K_MAX = 50  # as POST /api/search

# Parameter descriptions shared by the files and the urls route
SCENE_DOC = (
    "检测场景 code，如 general、oven，见 GET /api/scenes；不传为 general。整批图片共用一个场景，"
    "场景不存在或已停用时整个请求返回 SEARCH_UNKNOWN_SCENE"
)
TOP_K_DOC = f"每张查询图最多返回多少条，1～{TOP_K_MAX}，默认 {TOP_K_DEFAULT}：相似度达到 score_threshold 的前 top_k 条"
SCORE_THRESHOLD_DOC = (
    "相似度阈值，整批共用：不传为 0.55；0 表示不设阈值（总是返回最相近的 top_k 条）；"
    "其余取值 0.30～1，超出范围整个请求返回 SEARCH_BAD_SCORE_THRESHOLD"
)
TRACE_ID_DOC = (
    "请求追踪 ID，1～64 位字母、数字或 . _ : -；原样出现在响应体 trace_id 和响应头 X-Trace-Id，"
    "不传或不合规时由服务端生成。整批图片共用一个"
)


class BatchSearchUrlsRequest(BaseModel):
    """JSON body of POST /api/search/batch/urls. Unknown fields are refused (422)."""

    model_config = ConfigDict(extra="forbid")

    image_urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=BATCH_MAX_IMAGES,
        description=(
            f"查询图片的 http(s) 地址，1～{BATCH_MAX_IMAGES} 个，服务端逐个下载（只允许公网地址，每张最大 15 MB）。"
            "某个地址下载失败只影响它自己的结果"
        ),
    )
    scene: str | None = Field(default=None, description=SCENE_DOC)
    top_k: int = Field(default=TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=TOP_K_DOC)
    score_threshold: float | None = Field(default=None, description=SCORE_THRESHOLD_DOC)
    trace_id: str | None = Field(default=None, description=TRACE_ID_DOC)


class BatchSearchResult(BaseModel):
    """One query image's outcome. ok=true: source / items (and message when nothing was found);
    ok=false: error / detail. The fields of the other case are absent."""

    index: int = Field(..., description="这张查询图在请求里的序号，从 0 开始")
    input: str = Field(..., description="这张查询图：上传时是文件名，链接时是图片地址")
    ok: bool = Field(..., description="true：检索完成（可能没有结果）；false：这张图没能检索，原因见 error、detail")
    source: Literal["self", "empty"] | None = Field(
        default=None, description="ok=true 时：self 自建库有结果；empty 没有达到阈值的结果"
    )
    items: list[SearchHit] = Field(
        default_factory=list, description="ok=true 时的结果卡，按相似度从高到低；同 POST /api/search 的 items"
    )
    message: str | None = Field(default=None, description="没有结果时给人看的说明")
    error: str | None = Field(
        default=None,
        description="ok=false 时的错误码，如 SEARCH_BAD_IMAGE、EMPTY_FILE、FILE_TOO_LARGE、DOWNLOAD_FAILED、INVALID_URL",
    )
    detail: str | None = Field(default=None, description="ok=false 时的说明")
    elapsed_ms: float | None = Field(default=None, description="这张图读取（或下载）加检索的耗时，毫秒")


class BatchSearchResponse(BaseModel):
    ok: bool = True
    trace_id: str
    scene: str = Field(..., description="实际使用的检测场景 code")
    outputs_heating: bool = Field(
        default=False, description="结果是否带加热信息（details 里的六个食物字段和 heating_group），按场景配置"
    )
    top_k: int = Field(..., description="实际使用的 top_k")
    score_threshold: float = Field(..., description="实际使用的相似度阈值；0 表示没有设阈值")
    total: int = Field(..., description="查询图片数，等于 results 的长度")
    succeeded: int = Field(..., description="检索完成的图片数（ok=true，含没有结果的）")
    matched: int = Field(..., description="其中有结果的图片数（source=self）")
    failed: int = Field(..., description="没能检索的图片数（ok=false）")
    results: list[BatchSearchResult] = Field(..., description="每张查询图一项，顺序同请求")
