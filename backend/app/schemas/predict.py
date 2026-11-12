"""Predict wire: image search through FoodService (FlyB global search)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search_hit import SearchHitBase


class PredictUrlBody(BaseModel):
    img_url: str | None = None
    search_num: int | None = None
    machine_info: str | None = None
    trace_id: str | None = None
    save_res: bool | None = None


class PredictHitDetails(BaseModel):
    """Everything of a FlyB result besides the card fields; values as FlyB sends them."""

    model_config = ConfigDict(extra="allow")

    rank: Any = Field(default=None, description="FlyB 排名（Rank）")
    page_url: Any = Field(default=None, description="来源网页（Url）")
    site: Any = Field(default=None, description="来源站点（HostInfo，目前为 {Hostname, IconUrl}）")
    images: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "结果里的图片 {image_url, thumbnail_url, alt, width, height}，与自建库 details.images[] 同名；"
            "thumbnail_url 是按尺寸给的字典，目前为 {Tiny, Small, Medium, Large}"
        ),
    )
    document_info: Any = Field(default=None, description="DocumentInfo")


class PredictHit(SearchHitBase):
    """One FlyB result: the same card as a self-built hit (app.schemas.retrieval.SearchHit)."""

    details: PredictHitDetails = Field(default_factory=PredictHitDetails)


class PredictUpstreamEmpty(BaseModel):
    """FlyB answered that it found nothing (status=upstream_empty_response)."""

    request_id: Any = None
    total_doc_count: int = 0
    result_count: int = 0
    results: list[PredictHit] = Field(default_factory=list)
    status: str
    upstream_error: dict[str, Any] | None = None


class PredictResponse(BaseModel):
    ok: bool = True
    trace_id: str
    result: list[PredictHit] | PredictUpstreamEmpty = Field(
        ..., description="结果卡列表；FlyB 返回空结果时为 status=upstream_empty_response 的说明"
    )


class PingResponse(BaseModel):
    ok: bool = True
    ts: str
    timeout_s: float
    max_image_bytes: int
