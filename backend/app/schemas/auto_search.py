"""业务搜图的 wire（POST /api/search/auto/file、POST /api/search/auto/url）。

用整图查自建库；整图没有结果时，用 GroundingDINO 在图里找到的区域（AUTO_SEARCH_REGION_CONFIG）
和在这些区域里识别出的菜名（RECIPE_DETECT_CONFIG），它们和整图搜索同时启动，最后合并；都没有
结果时，用 Global 搜图作为可选兜底（app.api.services.auto_search）。
items[] 是各通道共用的结果卡（app.schemas.search_hit），每张卡的第一个字段是 source：
它来自哪个通道。和通道有关的信息在结果卡上，不在外层：自建库结果卡的 details 说明是否
带加热信息（outputs_heating）。fallback 说明 Global 搜图的情况，也就说明了 items 为空时
的原因。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search_hit import SearchHitBase

TOP_K_DEFAULT = 10
TOP_K_MAX = 20  # Global 搜图最多返回的条数（FLAB_CONFIG.DOC_COUNT 为 1–20）
MACHINE_INFO_MAX_LENGTH = 128

# 文件路由和 URL 路由共用的参数说明
SCENE_DOC = (
    "检测场景 code，如 general、oven（一体机）、range_hood（抽油烟机），见 GET /api/scenes；不传为 general。"
    "只作用于自建库：按场景过滤、场景类别名和加热信息都按它；Global 不分场景"
)
TOP_K_DOC = (
    f"最多返回多少条，1～{TOP_K_MAX}，默认 {TOP_K_DEFAULT}。自建库：相似度达到 score_threshold 的前 top_k 条"
    "（区域搜索时每个检测框各取 top_k 条，合并后再取 top_k 条）；"
    "Global 兜底：向上游要 top_k 条。菜谱识别的那一条结果卡不占这个数量"
)
SCORE_THRESHOLD_DOC = (
    "自建库的相似度阈值，整图和区域搜索都用它：不传为 0.55；0 表示不设阈值（总是返回最相近的 top_k 条，"
    "也就不做区域搜索、不会走兜底）；其余取值 0.30～1，超出范围返回 SEARCH_BAD_SCORE_THRESHOLD"
)
GLOBAL_FALLBACK_DOC = (
    "整图和区域搜索都没有达到阈值的结果、菜谱识别（开启时）也没有识别到菜名时，是否用现网 Global 搜图兜底；"
    "默认 false，直接返回空结果。Global 是外部付费服务，开启后没命中的请求会多花几秒"
)
TRACE_ID_DOC = (
    "请求追踪 ID，1～64 位字母、数字或 . _ : -；原样出现在响应体 trace_id 和响应头 X-Trace-Id，"
    "不传或不合规时由服务端生成。也可以放在请求头 X-Trace-Id 里（参数校验失败的响应只认请求头）"
)
MACHINE_INFO_DOC = f"调用方设备信息（如设备型号），只用于日志排查，最长 {MACHINE_INFO_MAX_LENGTH} 个字符"


class AutoSearchUrlRequest(BaseModel):
    """POST /api/search/auto/url 的 JSON 请求体。未知字段会被拒绝（422），这样拼错的或过时的
    参数名（img_url、search_num、global_switch）不会被悄悄忽略。
    """

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


class _CardSource(BaseModel):
    """结果卡来自哪个通道。单独作为一个基类，这样 source 是结果卡在 wire 上的第一个字段：
    pydantic 把最后一个基类的字段放在最前面。
    """

    source: Literal["self", "recipe", "global"] = Field(
        ...,
        description=(
            "这条结果卡来自哪里：self 自建库；recipe 菜谱识别——tags 是识别出的菜名（用「、」连接），"
            "description 为空字符串，original_url、similarity 为 null，details 为 {}；global 现网 Global（兜底）"
        ),
    )


class AutoSearchHit(SearchHitBase, _CardSource):
    """一张结果卡：来源通道、四个卡片字段，其余放在 details 里（按通道给出的原样）。"""

    details: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "其余字段，内容按来源。source=self：同 POST /api/search 的 details——id、category_id、category、"
            "scenes、scene_label（场景有标签映射时）、label_status、images[]，另有 outputs_heating（布尔）："
            "这条结果是否带加热信息，按场景配置；为 true 时另有六个食物字段和 heating_group。"
            "区域搜索的结果另有 region：{label 检测到的类别, score 检测置信度, box [x1, y1, x2, y2] 查询图上的像素坐标}，"
            "整图搜索的结果没有 region。"
            "source=global：同 Global 搜图的 details——rank、page_url、site、images[]、document_info。"
            "source=recipe：{}"
        ),
    )


class AutoSearchFallback(BaseModel):
    """Global 兜底的情况；总是出现。"""

    status: Literal["not_needed", "disabled", "hit", "empty", "failed"] = Field(
        ...,
        description=(
            "not_needed：整图、区域搜索或菜谱识别有结果，没有走兜底；disabled：都没有结果，global_fallback=false；"
            "hit：Global 有结果；empty：Global 也没有结果；failed：Global 搜图失败（原因见 error、detail），"
            "按没有结果返回。disabled、empty、failed 时 items 为 []"
        ),
    )
    error: str | None = Field(
        default=None, description="status=failed 时的错误码，如 UPSTREAM_TIMEOUT、UPSTREAM_ERROR、NOT_READY"
    )
    detail: str | None = Field(default=None, description="status=failed 时的说明")


class AutoSearchResponse(BaseModel):
    ok: bool = True
    trace_id: str
    scene: str = Field(..., description="实际使用的检测场景 code")
    items: list[AutoSearchHit] = Field(
        default_factory=list,
        description=(
            "结果卡，每条的 source 说明它来自哪里：整图有结果时只有整图的自建库结果（self），按相似度从高到低；"
            "整图没有结果时是区域搜索的自建库结果（self，按相似度从高到低），菜谱识别（recipe）的一条排在后面；"
            "都没有结果、走了兜底时是 Global（global）的结果，按上游排名。"
            "没有结果时为 []，原因见 fallback.status 和 message"
        ),
    )
    fallback: AutoSearchFallback
    message: str | None = Field(default=None, description="没有结果时给人看的说明")
