"""Image index/search/list DTOs (SSOT + §6.1 Wire).

Pydantic v1 compatible (host has pydantic==1.10.x). Prefer .dict() / getattr dump.

Index request/response shapes are outside §6.1 Search/List wire; List/Search
response models align with docs/image-search-pg-v01-data-contract.md §6.1.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


def dump_model(model: BaseModel) -> Dict[str, Any]:
    """pydantic v1/v2 compatible model -> dict (D1: never bare model_dump)."""
    return getattr(model, "model_dump", model.dict)()


class IndexJsonRequest(BaseModel):
    """JSON body when file already on a service-reachable path (§3.1)."""

    path: str = Field(..., description="Local path reachable by the API process")
    category: Optional[str] = None
    tags: Optional[List[Any]] = None
    scene: Optional[str] = None
    scenes: Optional[List[Any]] = None


class IndexItemOut(BaseModel):
    id: Union[int, str]
    qdrant_point_id: Optional[Union[int, str]] = None
    path: Optional[str] = None
    url: Optional[str] = None
    original_url: Optional[str] = None
    thumb_url: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    category_id: Optional[int] = None
    tags: Optional[Any] = None
    scenes: Optional[List[Any]] = None
    width: Optional[int] = None
    height: Optional[int] = None
    status: Optional[str] = "active"
    created_at: Optional[str] = None


class IndexFailedItem(BaseModel):
    filename_or_path: str
    error: str
    message: str


class IndexResponse(BaseModel):
    ok: bool = True
    trace_id: Optional[str] = None
    indexed: List[IndexItemOut] = Field(default_factory=list)
    failed: List[IndexFailedItem] = Field(default_factory=list)


class SearchJsonRequest(BaseModel):
    """JSON search body (§3.2 B)."""

    img_url: str = Field(..., description="Remote query image URL")
    scene: Optional[str] = None
    top_k: int = Field(default=10, ge=1, le=50)
    score_threshold: Optional[float] = Field(
        default=None,
        description="omit/null=0.55 reject; 0=off; else [0.30,1] (inclusive). SSOT section 3.2.1",
    )
    trace_id: Optional[str] = None


class ErrorBody(BaseModel):
    ok: bool = False
    error: str
    detail: str
    trace_id: Optional[str] = None


class SearchHitOut(BaseModel):
    """Search hit (§6.1.1). Forbidden: image_url/thumbnail_url/title/FlyB shell."""

    id: str
    original_url: Optional[str] = None
    thumb_url: Optional[str] = None
    similarity: Optional[float] = None
    category_id: Optional[int] = None
    category: Optional[str] = None
    scenes: Optional[List[Any]] = None
    scene_label: Optional[str] = None
    label_status: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    food_state: Optional[str] = None
    heating_group: Optional[str] = None


class SearchResponse(BaseModel):
    """Search envelope (§6.1.1). Array key is items; no result/results."""

    ok: bool = True
    trace_id: Optional[str] = None
    source: str = "self"
    scene: str = "general"
    outputs_heating: bool = False
    items: List[SearchHitOut] = Field(default_factory=list)
    message: Optional[str] = None


class ListImageItem(BaseModel):
    """Library list item (§6.1.2). Authoritative URL fields only (P2/P5/P11)."""

    id: str
    original_url: Optional[str] = None
    thumb_url: Optional[str] = None
    category_id: Optional[int] = None
    category: Optional[str] = None
    scenes: Optional[List[Any]] = None
    label_status: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    food_state: Optional[str] = None
    heating_group: Optional[str] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None


class ListImagesResponse(BaseModel):
    ok: bool = True
    trace_id: Optional[str] = None
    scene: str = "general"
    page: int = 1
    page_size: int = 24
    total: int = 0
    items: List[ListImageItem] = Field(default_factory=list)
