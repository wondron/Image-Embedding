"""Image search routes: bind HTTP inputs and delegate processing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile

from app.adapter.http.schemas.predict import PingResponse, PredictResponse, PredictUrlBody
from app.adapter.http.services.image_io import read_file_bytes
from app.adapter.http.services.predict import run_predict, run_url_predict
from app.bootstrap.state import get_application_state

router = APIRouter(prefix="/api", tags=["predict"])


@router.get("/ping", response_model=PingResponse)
def ping(request: Request) -> dict[str, Any]:
    runtime = get_application_state(request)
    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "timeout_s": runtime.http_timeout,
        "max_image_bytes": runtime.max_image_bytes,
    }


@router.post("/predict/searchImage_file", response_model=PredictResponse)
async def predict_file(
    request: Request,
    file: UploadFile = File(..., description="上传图片文件"),
    machine_info: str | None = Query(default=None, description="设备型号"),
    trace_id: str | None = Query(default=None, description="Trace ID"),
    save_res: bool = Query(default=False, description="是否保存结果"),
) -> dict[str, Any]:
    """使用 multipart/form-data 上传 file 字段进行图片检索。"""
    runtime = get_application_state(request)
    img_bytes = await read_file_bytes(file, runtime.max_image_bytes)
    return await run_predict(
        runtime=runtime,
        img_bytes=img_bytes,
        source=f"file:{file.filename or 'upload'}",
        machine_info=machine_info,
        trace_id=trace_id,
        save_res=save_res,
    )


@router.post("/predict/searchImage_url", response_model=PredictResponse)
async def predict_url(
    request: Request,
    img_url: str | None = Query(default=None, description="远程图片 URL（query，可选，与 JSON 二选一）"),
    machine_info: str | None = Query(default=None, description="设备型号（query，可选）"),
    trace_id: str | None = Query(default=None, description="Trace ID（query，可选）"),
    save_res: bool | None = Query(default=None, description="是否保存结果（query，可选）"),
    body: PredictUrlBody | None = Body(
        default=None,
        description="JSON 方式传参：{img_url, machine_info, trace_id, save_res}",
    ),
) -> dict[str, Any]:
    """URL 参数支持 Query 或 JSON；显式 Query 值优先。"""
    img_url = img_url if img_url is not None else (body.img_url if body else None)
    machine_info = machine_info if machine_info is not None else (body.machine_info if body else None)
    trace_id = trace_id if trace_id is not None else (body.trace_id if body else None)
    save_res = save_res if save_res is not None else (body.save_res if body else None)
    if img_url is None:
        raise HTTPException(422, "img_url is required (provide in query or JSON body).")

    return await run_url_predict(
        runtime=get_application_state(request),
        img_url=img_url,
        machine_info=machine_info,
        trace_id=trace_id,
        save_res=bool(save_res) if save_res is not None else False,
    )
