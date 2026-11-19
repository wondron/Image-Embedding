"""检索的 HTTP 适配层：解析请求，调用用例。

业务流程在 app.services.retrieval 里（通过 RetrievalStack 访问）。
函数以 dict 返回响应体（{ok, trace_id, ...result}）；路由的 response_model
（app.schemas.retrieval）负责校验和序列化。
失败以 AppError 抛出（错误码登记在 app.core.errors），由 app.core.exceptions 渲染。
用例会阻塞（ONNX / PG / Qdrant / S3），所以异步处理函数在线程池里执行它们，
而不是放在事件循环上。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_catalog, require_retrieval
from app.api.uploads import form_upload, read_upload
from app.bootstrap.state import ApplicationState
from app.clients.image_fetch import read_url_bytes
from app.core.middleware import bind_trace_id
from app.core.observability.log_helper import log_timing
from app.core.rules.food_fields import FOOD_FIELDS
from app.schemas.retrieval import IndexJsonRequest, SearchJsonRequest
from app.core import errors as err
from app.utils.timer import timer_tool

logger = logging.getLogger("svc.retrieval")

# 一次 POST /api/index 请求可以带的文件数。入库开始之前所有文件都读进内存
# （最多 MAX_INDEX_FILES x max_image_bytes），然后在请求里一个接一个地入库。
MAX_INDEX_FILES = 500
# 入库请求可以带的可选食物标注；由用例校验。
FOOD_KEYS = FOOD_FIELDS
# 入库请求可以设置的标注信息；不传时：label_status 跟随类别，source_type 为
# user_upload，license 为 unknown（app.services.retrieval.index_image）
LABEL_KEYS = ("label_status", "source_type", "license")
M = TypeVar("M", bound=BaseModel)


# -- 请求解析 ------------------------------------------------------------------------

def _is_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("content-type") or "").lower()


async def _json_body(request: Request, model: Type[M]) -> M:
    try:
        return model(**(await request.json()))
    except (ValueError, TypeError) as exc:  # JSON 或结构不合法（pydantic 的错误是 ValueError）
        raise err.validation_error(str(exc)) from exc


def _form_text(raw: Any) -> Optional[str]:
    """multipart 的文本字段；没传或为空时为 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _json_array(raw: Any, field: str) -> Optional[List[Any]]:
    """存放 JSON 数组的 multipart 字段；没传时为 None。"""
    if raw is None or raw == "":
        return None
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise err.validation_error(f"{field} JSON invalid: {exc}") from exc
    if not isinstance(value, list):
        raise err.validation_error(f"{field} must be a JSON array")
    return value


def _int_param(raw: Any, field: str) -> int:
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise err.validation_error(f"{field} must be an integer, got {raw!r}") from exc


def parse_score_threshold(raw: Any) -> Optional[float]:
    """请求里的 score_threshold -> float | None（范围规则在用例里）。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise err.search_bad_score_threshold(f"score_threshold must be a finite float; got {raw!r}") from exc


# -- POST /api/index -----------------------------------------------------------------

def _write_vectors(runtime: ApplicationState, write: Any) -> Any:
    """在这个工作线程里、在向量写入闸门之后执行 write(stack)：正在切换模型版本时等待，
    然后用切换后的检索服务写入。
    """
    with runtime.vector_writes.shared():
        return write(require_retrieval(runtime))


async def index_from_upload(
    runtime: ApplicationState, request: Request, file: UploadFile
) -> Dict[str, Any]:
    """读取一张上传的图片并入库（记录在 index_tasks 里；失败会抛出）。"""
    trace_id = bind_trace_id(request)
    data = await read_upload(file, runtime.max_image_bytes)
    require_retrieval(runtime)  # 在等待任何东西之前先确定 NOT_READY
    item = {"image_bytes": data, "filename": file.filename or "upload.bin"}
    indexed = await run_in_threadpool(
        _write_vectors, runtime, lambda stack: stack.index_uc.execute_file(item, trace_id=trace_id)
    )
    return {"ok": True, "trace_id": trace_id, "indexed": [indexed], "failed": []}


async def index_from_request(runtime: ApplicationState, request: Request) -> Dict[str, Any]:
    """multipart 的 file/files（<= MAX_INDEX_FILES），或 JSON {path, description, category, tags, scene,
    scenes, label_status, source_type, license，以及可选的食物字段 FOOD_KEYS}。
    """
    trace_id = bind_trace_id(request)
    if _is_json(request):
        raw = await _json_body(request, IndexJsonRequest)
        items = [{
            "source_path": raw.path,
            "filename": raw.path,
            "category": raw.category,
            "tags": raw.tags,
            "scene": raw.scene,
            "scenes": raw.scenes,
            "description": raw.description,
            **{key: getattr(raw, key) for key in LABEL_KEYS},
            **{key: getattr(raw, key) for key in FOOD_KEYS},
        }]
    else:
        items = await _index_items_from_form(request, runtime.max_image_bytes)

    require_retrieval(runtime)
    outcome = await run_in_threadpool(
        _write_vectors, runtime, lambda stack: stack.index_uc.execute_batch(items, trace_id=trace_id)
    )
    _raise_if_batch_failed_uniformly(outcome)
    return {"ok": True, "trace_id": trace_id, "indexed": outcome["indexed"], "failed": outcome["failed"]}


async def _index_items_from_form(request: Request, max_bytes: int) -> List[Dict[str, Any]]:
    form = await request.form()
    category = form.get("category")
    scene = form.get("scene")
    shared = {
        "category": str(category) if category is not None else None,
        "tags": _json_array(form.get("tags"), "tags"),
        "scene": str(scene).strip() if scene not in (None, "") else None,
        "scenes": _json_array(form.get("scenes"), "scenes"),
        "description": form.get("description"),  # 由用例规范化（并校验）
        **{key: _form_text(form.get(key)) for key in LABEL_KEYS},  # 枚举值；由用例检查
        **{key: form.get(key) for key in FOOD_KEYS},  # 字符串；由用例校验
    }
    uploads = []
    for value in [form.get("file"), *form.getlist("files"), *form.getlist("file")]:
        upload = form_upload(value)
        if upload is not None and upload not in uploads:
            uploads.append(upload)
    if len(uploads) > MAX_INDEX_FILES:
        raise err.validation_error(f"too many files (max {MAX_INDEX_FILES})")
    if not uploads:
        raise err.index_empty_request("no file and no path")

    items = []
    for upload in uploads:
        data = await read_upload(upload, max_bytes)
        items.append({"image_bytes": data, "filename": upload.filename or "upload.bin", **shared})
    return items


def _raise_if_batch_failed_uniformly(outcome: Dict[str, Any]) -> None:
    """什么都没入库，而且每一项都以同样的方式失败 -> 报告这个错误。"""
    if outcome["indexed"]:
        return
    failed = outcome["failed"]
    for code, fallback in (
        ("INDEX_UNKNOWN_SCENE", "unknown scene"),
        ("VALIDATION_ERROR", "validation error"),
    ):
        if failed and all(f.get("error") == code for f in failed):
            raise err.AppError(code, failed[0].get("detail") or fallback)
    if all(f.get("error") in {"INDEX_EMPTY_REQUEST", "INDEX_BAD_IMAGE"} for f in failed):
        raise err.index_empty_request("no decodable image in request")


# -- POST /api/search ------------------------------------------------------------------

@dataclass
class _SearchParams:
    image_bytes: Optional[bytes] = None
    image_url: Optional[str] = None
    scene: Optional[str] = None
    top_k: int = 10
    score_threshold: Optional[float] = None
    trace_id: Optional[str] = None


async def search_from_request(runtime: ApplicationState, request: Request) -> Dict[str, Any]:
    """multipart 的 file / img_url，或 JSON {img_url}；查询参数覆盖请求体里的字段。"""
    params = await _search_params(request, runtime.max_image_bytes)
    trace_id = bind_trace_id(request, params.trace_id)
    if params.image_bytes is None and not params.image_url:
        raise err.validation_error("file or img_url is required")
    if not 1 <= params.top_k <= 50:
        raise err.validation_error("top_k must be in 1..50")

    stack = require_retrieval(runtime)
    image_bytes = params.image_bytes
    input_type = "图片链接" if image_bytes is None else "上传文件"
    timer = timer_tool()
    completed = False
    try:
        if image_bytes is None:
            with timer.step("图像下载"):
                image_bytes = await read_url_bytes(
                    params.image_url,
                    max_image_bytes=runtime.max_image_bytes,
                    timeout_s=runtime.http_timeout,
                )
        result = await run_in_threadpool(
            stack.search_uc.execute,
            image_bytes=image_bytes,
            scene=params.scene,
            top_k=params.top_k,
            score_threshold=params.score_threshold,
            trace_id=trace_id,
            timer=timer,
        )
        completed = True
        return {"ok": True, "trace_id": trace_id, **result}
    finally:
        log_timing(logger, f"自建库搜图({input_type})", timer, trace_id=trace_id, failed=not completed)


async def _search_params(request: Request, max_bytes: int) -> _SearchParams:
    params = _SearchParams()
    if _is_json(request):
        raw = await _json_body(request, SearchJsonRequest)
        params.image_url = raw.img_url
        params.scene = raw.scene
        params.top_k = int(raw.top_k)
        params.score_threshold = parse_score_threshold(raw.score_threshold)
        params.trace_id = raw.trace_id
    else:
        form = await request.form()
        upload = form_upload(form.get("file"))
        if upload is not None:
            params.image_bytes = await read_upload(upload, max_bytes)
        if form.get("img_url") and params.image_bytes is None:
            params.image_url = str(form.get("img_url"))
        if form.get("scene") not in (None, ""):
            params.scene = str(form.get("scene"))
        if form.get("top_k") is not None:
            params.top_k = _int_param(form.get("top_k"), "top_k")
        if form.get("score_threshold") not in (None, ""):
            params.score_threshold = parse_score_threshold(form.get("score_threshold"))
        if form.get("trace_id") is not None:
            params.trace_id = str(form.get("trace_id"))

    query = request.query_params
    if query.get("scene") not in (None, ""):
        params.scene = query.get("scene")
    if query.get("top_k") is not None:
        params.top_k = _int_param(query.get("top_k"), "top_k")
    if query.get("score_threshold") not in (None, ""):
        params.score_threshold = parse_score_threshold(query.get("score_threshold"))
    if query.get("trace_id"):
        params.trace_id = query.get("trace_id")
    return params


# -- GET /api/images -------------------------------------------------------------------

def run_list_images(
    runtime: ApplicationState,
    request: Request,
    *,
    scene: Optional[str] = None,
    page: int = 1,
    page_size: int = 24,
    q: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    category_id: Optional[int] = None,
    label_status: Optional[str] = None,
    out_of_sync: Optional[bool] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """只用 PostgreSQL（catalog），所以编码器或 Qdrant 挂了时图库也能列出；
    向量库能读时，每一项带上 vector_status（整页只读一次）。
    """
    trace_id = bind_trace_id(request, trace_id)
    result = require_catalog(runtime).list_uc.execute(
        scene=scene, page=page, page_size=page_size, q=q, category=category, tag=tag,
        category_id=category_id, label_status=label_status, out_of_sync=bool(out_of_sync),
    )
    expected = result.pop("expected_payloads", None) or {}
    _attach_vector_status(runtime, result["items"], expected)
    return {"ok": True, "trace_id": trace_id, **result}


def _attach_vector_status(runtime: ApplicationState, items: List[Dict[str, Any]], expected: Dict[str, Any]) -> None:
    """给每一项加上 vector_status：synced / stale / missing；检索服务没构建或读取失败时为
    unavailable。从不让列表失败：列表是 PostgreSQL 给出的。
    """
    if not items:
        return
    statuses: Dict[str, str] = {}
    try:
        stack = runtime.require_retrieval()
    except err.NotReadyError:
        stack = None
    if stack is not None:
        try:
            statuses = stack.vector_status_uc.statuses(expected)
        except Exception as exc:
            logger.warning("list vector status read failed | %s: %s", type(exc).__name__, exc)
    for item in items:
        item["vector_status"] = statuses.get(item["id"], "unavailable")


# -- GET /api/health -------------------------------------------------------------------

_FLYB_DISABLED = {"ok": False, "optional": True, "disabled": True, "detail": "out of scope"}


def mvp_health(runtime: ApplicationState) -> JSONResponse:
    """GET /api/health：检索服务的 embedding / qdrant / metadata 状态。

    "postgres" 是元数据库；"flyb" 总是 disabled。
    不代替 /api/healthz 或 /api/readyz。
    """
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    stack = runtime.retrieval
    if stack is None:
        detail = runtime.retrieval_error or "not wired"
        components: Dict[str, Any] = {
            "api": {"ok": True},
            "embedding": {"ok": False, "detail": detail, "embedding_space_id": None},
            "qdrant": {
                "ok": False,
                "detail": detail,
                "collection": None,  # 真实的值来自当前的 embedding 模型
                "embedding_space_id": None,
            },
            "postgres": {"ok": False, "detail": detail},
            "flyb": dict(_FLYB_DISABLED),
        }
        return JSONResponse(status_code=503, content={"ok": False, "ts": ts, "components": components})

    model = stack.model
    pings = stack.infra.ping()
    q, pg = pings["qdrant"], pings["postgres"]
    qdrant: Dict[str, Any] = {
        "ok": bool(q.get("ok")),
        "collection": q.get("collection") or model.collection,
        "points_count": q.get("points_count"),
        "embedding_space_id": model.space_id,
    }
    if not qdrant["ok"]:
        qdrant["detail"] = q.get("detail")
    metadata: Dict[str, Any] = {
        "ok": bool(pg.get("ok")),
        "driver": pg.get("driver") or "postgresql",
        "host": pg.get("host"),
        "database": pg.get("database"),
    }
    if not metadata["ok"]:
        metadata["detail"] = pg.get("detail")
    components = {
        "api": {"ok": True},
        "embedding": {
            "ok": True,
            "model_path": stack.embedding.model_path,
            "vector_dim": model.dim,
            "embedding_space_id": model.space_id,
            "current_model": model.name,
            "current_collection": model.collection,
        },
        "qdrant": qdrant,
        "postgres": metadata,
        "flyb": dict(_FLYB_DISABLED),
    }
    ok = qdrant["ok"] and metadata["ok"]
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "ts": ts, "components": components},
    )
