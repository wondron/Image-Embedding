"""Wire conventions shared by every endpoint (pydantic v2).

- Every JSON body has ``ok``.
- Business endpoints (predict, retrieval) echo ``trace_id`` in the body; every
  response carries it in the ``X-Trace-Id`` header.
- Failures are ErrorResponse (built by app.core.exceptions only).
- Ids are strings; timestamps are ISO 8601 with a UTC offset.
"""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: str
    trace_id: str
    errors: list[dict[str, Any]] | None = None  # field-level list, VALIDATION_ERROR only


# OpenAPI: the error statuses an endpoint may answer with, all as ErrorResponse.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse}
    for status in (400, 403, 413, 415, 422, 500, 502, 503, 504)
}
