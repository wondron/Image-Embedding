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
    category: str | None = None
    tags: list[Any] | None = None
    scene: str | None = None
    scenes: list[Any] | None = None


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
    width: int
    height: int
    payload_version: int | None = None
    created_at: str | None = None


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
    width: int | None = None
    height: int | None = None
    food_state: str | None = None
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
    width: int | None = None
    height: int | None = None
    food_state: str | None = None
    heating_group: HeatingGroup | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ListImagesResponse(BaseModel):
    ok: bool = True
    trace_id: str
    scene: str
    page: int
    page_size: int
    total: int
    items: list[ListImageItem] = Field(default_factory=list)
