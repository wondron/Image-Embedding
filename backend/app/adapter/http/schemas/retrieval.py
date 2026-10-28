"""Image index/search DTOs per SSOT §3.1 / §3.2.

Pydantic v1 compatible (host has pydantic==1.10.x). Prefer .dict() / getattr dump.
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
    id: int
    qdrant_point_id: int
    path: str
    url: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[Any] = None
    scenes: Optional[List[Any]] = None
    width: Optional[int] = None
    height: Optional[int] = None
    status: str = "active"
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
    score_threshold: Optional[float] = None
    trace_id: Optional[str] = None


class ErrorBody(BaseModel):
    ok: bool = False
    error: str
    detail: str
    trace_id: Optional[str] = None


class ListImageItem(BaseModel):
    """Library list item (SSOT section 3.5)."""

    id: int
    qdrant_point_id: int
    path: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[Any] = None
    scenes: Optional[List[Any]] = None
    width: Optional[int] = None
    height: Optional[int] = None
    status: str = "active"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ListImagesResponse(BaseModel):
    ok: bool = True
    trace_id: Optional[str] = None
    scene: str = "normal"
    page: int = 1
    page_size: int = 24
    total: int = 0
    items: List[ListImageItem] = Field(default_factory=list)
