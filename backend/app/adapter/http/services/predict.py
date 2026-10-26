"""Image search orchestration and translation of upstream failures to HTTP errors."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

import requests
from fastapi import HTTPException

from app.adapter.http.dependencies import require_food_service
from app.adapter.http.services.image_io import read_url_bytes
from app.bootstrap.state import ApplicationState
from app.core.observability.log_helper import show_text
from app.domain.common.timer_utils import timer_tool

logger = logging.getLogger("svc.predict")


async def run_predict(
    runtime: ApplicationState,
    img_bytes: bytes,
    source: str,
    machine_info: str | None,
    trace_id: str | None,
    save_res: bool,
) -> dict[str, Any]:
    """Call FoodService.detect with shared logging and upstream error handling."""
    t0 = time.perf_counter()
    svc = require_food_service(runtime)
    if not img_bytes:
        raise HTTPException(400, "Image bytes is empty.")

    try:
        result = svc.detect(
            img_bytes,
            machine_info=machine_info,
            trace_id=trace_id,
            save_result=save_res,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        logger.info("图像检索成功 | 请求ID=%s | 来源=%s | 图片大小=%s 字节 | 耗时=%.2f 毫秒", trace_id, source, len(img_bytes), latency_ms)
        show_text(f"图像检索成功 | 请求ID={trace_id} | 来源={source} | 图片大小={len(img_bytes)} 字节 | 耗时={latency_ms:.2f} 毫秒", trace_id=trace_id)
        return {"result": result, "trace_id": trace_id}
    except HTTPException as http_err:
        logger.warning("图像检索请求异常 | 请求ID=%s | 来源=%s | 详情=%s", trace_id, source, getattr(http_err, "detail", str(http_err)),)
        show_text(f"图像检索请求异常 | 请求ID={trace_id} | 来源={source} | 详情={http_err.detail}", trace_id=trace_id, logger_level=logging.WARNING)
        raise
    except requests.Timeout as exc:
        logger.warning("上游搜索服务响应超时 | 请求ID=%s", trace_id)
        show_text(f"上游搜索服务响应超时 | 请求ID={trace_id}", trace_id=trace_id, logger_level=logging.WARNING)
        raise HTTPException(504, "上游搜索服务响应超时，请稍后重试。") from exc
    except requests.HTTPError as exc:
        upstream_status = exc.response.status_code if exc.response is not None else None
        status = upstream_status if upstream_status in (502, 503, 504) else 502
        detail = "global服务检索失败"
        if upstream_status is not None:
            detail += f"（上游 HTTP {upstream_status}）"
        logger.warning("上游搜索服务请求异常 | 请求ID=%s | 上游状态码=%s", trace_id, upstream_status)
        show_text(f"上游搜索服务请求异常 | 请求ID={trace_id} | 上游状态码={upstream_status} | 详情={detail}", trace_id=trace_id, logger_level=logging.WARNING)
        raise HTTPException(status, detail) from exc
    except requests.exceptions.JSONDecodeError as exc:
        logger.warning("上游搜索服务返回无效 JSON | 请求ID=%s", trace_id)
        show_text(f"上游搜索服务返回无效 JSON | 请求ID={trace_id}", trace_id=trace_id, logger_level=logging.WARNING)
        raise HTTPException(502, "上游搜索服务返回了无效响应，请稍后重试。") from exc
    except requests.RequestException as exc:
        logger.warning("上游搜索服务连接失败 | 请求ID=%s", trace_id)
        show_text(f"上游搜索服务连接失败 | 请求ID={trace_id}", trace_id=trace_id, logger_level=logging.WARNING)
        raise HTTPException(502, "无法连接上游搜索服务，请稍后重试。") from exc
    except Exception as exc:
        logger.exception("图像检索失败 | 请求ID=%s | 来源=%s | 错误=%s", trace_id, source, exc)
        show_text(f"图像检索失败 | 请求ID={trace_id} | 来源={source} | 错误={exc}", trace_id=trace_id, logger_level=logging.ERROR, exc_info=True)
        raise HTTPException(500, f"图像检索失败：{exc}") from exc


async def run_url_predict(
    runtime: ApplicationState,
    *,
    img_url: str,
    machine_info: str | None,
    trace_id: str | None,
    save_res: bool,
) -> dict[str, Any]:
    """Download a search input and run the same operation as a file upload."""
    trace_id = trace_id or str(uuid4())
    timer = timer_tool()
    source = f"use url |{img_url}"
    show_text(source, trace_id=trace_id)
    img_bytes = await read_url_bytes(
        img_url,
        client=runtime.http_client,
        max_image_bytes=runtime.max_image_bytes,
        timeout_s=runtime.http_timeout,
    )
    timer.mark("read_url")

    result = await run_predict(
        runtime=runtime,
        img_bytes=img_bytes,
        source=source,
        machine_info=machine_info,
        trace_id=trace_id,
        save_res=save_res,
    )
    timer.mark("predict")
    message = f"image search URL completed | time={timer.summary()}"
    logger.info(message)
    show_text(message, trace_id=trace_id)
    return result
