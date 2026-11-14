"""Business search wire (POST /api/search/auto/file, POST /api/search/auto/url).

The self-built library first; when it has nothing that reaches the threshold, the global search
as an optional fallback (app.api.services.auto_search). items[] are the result cards both channels
share (app.schemas.search_hit): source says which channel they came from, fallback what happened to
the global search.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search_hit import SearchHitBase

TOP_K_DEFAULT = 10
TOP_K_MAX = 20  # what the global search returns at most (FLAB_CONFIG.DOC_COUNT is 1–20)
MACHINE_INFO_MAX_LENGTH = 128

# Parameter descriptions shared by the file and the URL route
SCENE_DOC = (
    "检测场景 code，如 general、oven（一体机）、range_hood（抽油烟机），见 GET /api/scenes；不传为 general。"
    "只作用于自建库：按场景过滤、场景类别名和加热信息都按它；Global 不分场景"
)
TOP_K_DOC = (
    f"最多返回多少条，1～{TOP_K_MAX}，默认 {TOP_K_DEFAULT}。自建库：相似度达到 score_threshold 的前 top_k 条；"
    "Global 兜底：向上游要 top_k 条"
)
SCORE_THRESHOLD_DOC = (
    "自建库的相似度阈值：不传为 0.55；0 表示不设阈值（总是返回最相近的 top_k 条，也就不会走兜底）；"
    "其余取值 0.30～1，超出范围返回 SEARCH_BAD_SCORE_THRESHOLD"
)
GLOBAL_FALLBACK_DOC = (
    "自建库没有达到阈值的结果时，是否用现网 Global 搜图兜底；默认 false，直接返回空结果。"
    "Global 是外部付费服务，开启后没命中的请求会多花几秒"
)
TRACE_ID_DOC = (
    "请求追踪 ID，1～64 位字母、数字或 . _ : -；原样出现在响应体 trace_id 和响应头 X-Trace-Id，"
    "不传或不合规时由服务端生成。也可以放在请求头 X-Trace-Id 里（参数校验失败的响应只认请求头）"
)
MACHINE_INFO_DOC = f"调用方设备信息（如设备型号），只用于日志排查，最长 {MACHINE_INFO_MAX_LENGTH} 个字符"


class AutoSearchUrlRequest(BaseModel):
    """JSON body of POST /api/search/auto/url. Unknown fields are refused (422), so a misspelt
    or outdated parameter name (img_url, search_num, global_switch) does not pass unnoticed."""

    model_config = ConfigDict(extra="forbid")

    image_url: str = Field(
        ..., min_length=1, description="查询图片的 http(s) 地址，服务端下载（只允许公网地址，最大 15 MB）"
    )
    scene: str | None = Field(default=None, description=SCENE_DOC)
    top_k: int = Field(default=TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=TOP_K_DOC)
    score_threshold: float | None = Field(default=None, description=SCORE_THRESHOLD_DOC)
    global_fallback: bool = Field(default=False, description=GLOBAL_FALLBACK_DOC)
    trace_id: str | None = Field(default=None, description=TRACE_ID_DOC)
    machine_info: str | None = Field(default=None, max_length=MACHINE_INFO_MAX_LENGTH, description=MACHINE_INFO_DOC)


class AutoSearchHit(SearchHitBase):
    """One result card: the four card fields, the rest in details (as the channel gives them)."""

    details: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "其余字段，内容按来源。source=self：同 POST /api/search 的 details——id、category_id、category、"
            "scenes、scene_label（场景有标签映射时）、label_status、images[]；场景 outputs_heating 时另有"
            "六个食物字段和 heating_group。source=global：同 Global 搜图的 details——rank、page_url、site、"
            "images[]、document_info"
        ),
    )


class AutoSearchFallback(BaseModel):
    """What happened to the global fallback; always present."""

    status: Literal["not_needed", "disabled", "hit", "empty", "failed"] = Field(
        ...,
        description=(
            "not_needed：自建库有结果，没有走兜底；disabled：自建库没有结果，global_fallback=false；"
            "hit：Global 有结果；empty：Global 也没有结果；failed：Global 搜图失败（原因见 error、detail），"
            "按没有结果返回"
        ),
    )
    error: str | None = Field(
        default=None, description="status=failed 时的错误码，如 UPSTREAM_TIMEOUT、UPSTREAM_ERROR、NOT_READY"
    )
    detail: str | None = Field(default=None, description="status=failed 时的说明")


class AutoSearchResponse(BaseModel):
    ok: bool = True
    trace_id: str
    source: Literal["self", "global", "empty"] = Field(
        ..., description="items 来自哪里：self 自建库；global 现网 Global（兜底）；empty 没有结果"
    )
    scene: str = Field(..., description="实际使用的检测场景 code")
    outputs_heating: bool = Field(
        default=False,
        description=(
            "items 是否带加热信息（details 里的六个食物字段和 heating_group）：自建库结果和空结果按场景配置；"
            "Global 结果总是 false"
        ),
    )
    items: list[AutoSearchHit] = Field(
        default_factory=list,
        description="结果卡：自建库按相似度从高到低，Global 按上游排名；source=empty 时为 []",
    )
    fallback: AutoSearchFallback
    message: str | None = Field(default=None, description="没有结果时给人看的说明")
