"""Request and response contracts for image search."""

from typing import Any

from pydantic import BaseModel


class PredictUrlBody(BaseModel):
    img_url: str | None = None
    machine_info: str | None = None
    trace_id: str | None = None
    save_res: bool | None = None


class PredictResponse(BaseModel):
    # Search results may be a document list or an upstream status envelope.
    result: Any
    trace_id: str | None = None


class PingResponse(BaseModel):
    ok: bool
    ts: str
    timeout_s: float
    max_image_bytes: int
