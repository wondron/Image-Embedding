"""Predict wire: image search through FoodService."""

from typing import Any

from pydantic import BaseModel


class PredictUrlBody(BaseModel):
    img_url: str | None = None
    machine_info: str | None = None
    trace_id: str | None = None
    save_res: bool | None = None


class PredictResponse(BaseModel):
    ok: bool = True
    trace_id: str
    # Search results may be a document list or an upstream status envelope.
    result: Any


class PingResponse(BaseModel):
    ok: bool = True
    ts: str
    timeout_s: float
    max_image_bytes: int
