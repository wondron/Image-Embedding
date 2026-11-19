"""所有接口共用的 wire 约定（pydantic v2）。

- 每个 JSON 响应体都有 ``ok``。
- 业务接口（预测、检索）在响应体里回写 ``trace_id``；每个响应都在 ``X-Trace-Id``
  响应头里带上它。
- 失败是 ErrorResponse（只由 app.core.exceptions 构建）。
- id 是字符串；时间戳是带 UTC 偏移的 ISO 8601。
"""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: str
    trace_id: str
    errors: list[dict[str, Any]] | None = None  # 字段级的列表，只用于 VALIDATION_ERROR


# OpenAPI：接口可能返回的错误状态码，都是 ErrorResponse。
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse}
    for status in (400, 403, 413, 415, 422, 500, 502, 503, 504)
}
