"""Tools wire: low-level model diagnostics."""

from pydantic import BaseModel


class SegmentResponse(BaseModel):
    ok: bool = True
    filename: str
    width: int
    height: int
    latency_ms: float
    mask_png_base64: str
    visual_png_base64: str | None = None


class EmbeddingResponse(BaseModel):
    ok: bool = True
    filename: str
    embedding_dim: int
    latency_ms: float
    embedding: list[float]
