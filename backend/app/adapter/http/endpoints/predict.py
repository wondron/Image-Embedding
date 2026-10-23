"""Food prediction endpoints."""

from __future__ import annotations

import time
import logging
from uuid import uuid4
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
import httpx
import requests
from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from app.bootstrap.state import ApplicationState, get_application_state
from app.core.observability.log_helper import show_text
from app.domain.common.timer_utils import timer_tool

logger = logging.getLogger("svc.predict")
router = APIRouter(prefix="/api", tags=["predict"])

# --------- 工具函数 ----------
async def _read_file_bytes(file: UploadFile, max_image_bytes: int) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > max_image_bytes:
        raise HTTPException(413, f"File too large (> {max_image_bytes} bytes).")
    return data


async def _read_url_bytes(url: str, runtime: ApplicationState) -> bytes:
    """从 URL 获取原始二进制数据，并做大小限制"""
    url = clean_url(url, default_scheme="https")
    logger.info("download img_url repr=%r", url)  # %r 专抓隐形字符

    try:
        # 用 stream 防止一次性读爆内存
        async with runtime.http_client.stream("GET", url) as r:
            r.raise_for_status()

            # 先用 Content-Length 做快速拦截（有些源会提供）
            cl = r.headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > runtime.max_image_bytes:
                        raise HTTPException(413, f"Downloaded file too large (> {runtime.max_image_bytes} bytes).")
                except ValueError:
                    pass  # content-length 非数字则忽略

            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > runtime.max_image_bytes:
                    raise HTTPException(413, f"Downloaded file too large (> {runtime.max_image_bytes} bytes).")
            data = bytes(buf)

    except HTTPException:
        raise
    except httpx.UnsupportedProtocol as e:
        # 这里基本就是 url 带了奇怪字符或不是 http/https
        raise HTTPException(status_code=422, detail=f"Invalid img_url: {url!r}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Download timeout after {runtime.http_timeout}s.") from e
    except httpx.HTTPStatusError as e:
        # 把对方返回码带上，方便排查 OSS 权限/404 等
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"Download failed with status {status}.") from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Download request error: {str(e)}") from e

    if not data:
        raise HTTPException(400, "Downloaded image is empty.")
    return data


# --------- 路由 ----------
@router.get("/ping")
def ping(request: Request) -> dict:
    runtime = get_application_state(request)
    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "timeout_s": runtime.http_timeout,
        "max_image_bytes": runtime.max_image_bytes,
    }


async def _run_predict(
    runtime: ApplicationState,
    img_bytes: bytes,
    source: str,
    machine_info: Optional[str],
    trace_id: Optional[str],
    save_res: bool,
):
    """
    公共推理逻辑：给定 img_bytes，调用 FoodService.detect 并记录日志。
    """
    t0 = time.time()
    try:
        svc = runtime.require_food_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"service unavailable: {exc}") from exc
    if not img_bytes:
        raise HTTPException(400, "Image bytes is empty.")

    try:
        result = svc.detect(img_bytes, machine_info=machine_info, trace_id=trace_id, save_result=save_res,)
        returndata = {"result": result, "trace_id": trace_id,}
        latency_ms = round((time.time() - t0) * 1000.0, 2)
        logger.info("图像检索成功 | 请求ID=%s | 来源=%s | 图片大小=%s 字节 | 耗时=%.2f 毫秒", trace_id, source, len(img_bytes), latency_ms)
        show_text(f"图像检索成功 | 请求ID={trace_id} | 来源={source} | 图片大小={len(img_bytes)} 字节 | 耗时={latency_ms:.2f} 毫秒", trace_id=trace_id)
        return returndata
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
    except Exception as e:
        logger.exception("图像检索失败 | 请求ID=%s | 来源=%s | 错误=%s", trace_id, source, e)
        show_text(f"图像检索失败 | 请求ID={trace_id} | 来源={source} | 错误={e}", trace_id=trace_id, logger_level=logging.ERROR, exc_info=True)
        raise HTTPException(500, f"图像检索失败：{e}")


@router.post("/predict/embedding_file")
async def predict_file(
    request: Request,
    file: UploadFile = File(..., description="上传图片文件"),
    machine_info: Optional[str] = Query(default=None, description="设备型号"),
    trace_id: Optional[str] = Query(default=None, description="Trace ID"),
    save_res: bool = Query(default=False, description="是否保存结果"),
):
    """
    单张预测（文件上传版）：
    - 使用 multipart/form-data 上传 file 字段
    """
    runtime = get_application_state(request)
    img_bytes = await _read_file_bytes(file, runtime.max_image_bytes)
    source = f"file:{file.filename or 'upload'}"
    return await _run_predict(
        runtime=runtime,
        img_bytes=img_bytes,
        source=source,
        machine_info=machine_info,
        trace_id=trace_id,
        save_res=save_res,
    )


# ---------- 2) URL 版：同时支持 Query 和 Body JSON ----------
class PredictUrlBody(BaseModel):
    img_url: Optional[str] = None
    machine_info: Optional[str] = None
    trace_id: Optional[str] = None
    save_res: Optional[bool] = None


def clean_url(url: str, *, default_scheme: str = "https") -> str:
    # 去除常见不可见字符（\n \r 空格）
    url = url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="img_url is empty.")

    # 支持 //example.com/xx.jpg
    if url.startswith("//"):
        url = f"{default_scheme}:{url}"

    p = urlparse(url)

    # 可选：没 scheme 就补一个（也可以改成“必须带”）
    if not p.scheme:
        url = f"{default_scheme}://{url}"
        p = urlparse(url)

    if p.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail=f"img_url scheme not allowed: {p.scheme!r}")
    if not p.netloc:
        raise HTTPException(status_code=422, detail=f"img_url invalid (missing host): {url!r}")

    return url


@router.post("/predict/embedding_url")
async def predict_url(
    request: Request,
    img_url: Optional[str] = Query( default=None, description="远程图片 URL（query，可选，与 JSON 二选一）", ),
    machine_info: Optional[str] = Query( default=None, description="设备型号（query，可选）", ),
    trace_id: Optional[str] = Query( default=None, description="Trace ID（query，可选）", ),
    save_res: Optional[bool] = Query( default=None, description="是否保存结果（query，可选）",),
    # Body JSON（可选）
    body: Optional[PredictUrlBody] = Body( default=None, description="JSON 方式传参：{img_url, machine_info, trace_id, save_res}",),
):
    # 显式 Query 参数优先，避免 Swagger 请求体中的示例值覆盖真实 Query 值。
    img_url = img_url if img_url is not None else (body.img_url if body else None)
    machine_info = machine_info if machine_info is not None else (body.machine_info if body else None)
    trace_id = trace_id if trace_id is not None else (body.trace_id if body else None)
    save_res = save_res if save_res is not None else (body.save_res if body else None)
    save_res = bool(save_res) if save_res is not None else False

    trace_id = trace_id or str(uuid4())
    timer = timer_tool()
    # 2) 必填校验
    if img_url is None:
        raise HTTPException(status_code=422, detail="img_url is required (provide in query or JSON body).")

    # 3) 统一清洗（可选：这里清洗一次，_read_url_bytes 也会再清洗一次，双保险）
    img_url = clean_url(img_url, default_scheme="https")

    runtime = get_application_state(request)
    source = f"use url |{img_url}"
    show_text(source, trace_id=trace_id)

    img_bytes = await _read_url_bytes(img_url, runtime)
    timer.mark("read_url")

    res = await _run_predict(
        runtime=runtime,
        img_bytes=img_bytes,
        source=source,
        machine_info=machine_info,
        trace_id=trace_id,
        save_res=save_res,
    )
    timer.mark("predict")
    timing_summary = timer.summary()
    message = (f"embedding url completed | time={timing_summary}")
    logger.info(message)
    show_text(message, trace_id=trace_id)
    return res
