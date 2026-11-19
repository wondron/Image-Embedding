"""单张已存图片的 HTTP 适配层：详情、修改、删除、推送 payload。

先处理 PostgreSQL，通过 catalog（app.bootstrap.catalog；还没构建时 NOT_READY 503），
所以编码器或 Qdrant 挂了时也能编辑和删除。向量这一侧在检索服务就绪时跟上，而且
PostgreSQL 部分成功的请求不会因为它失败：

- GET 额外返回 vector：图片在当前 collection 里的情况（services.retrieval.vector_status），
  或者 status=unavailable 并给出原因；
- PATCH 和 POST .../vector-sync 推送已提交的 payload：vector_sync / sync_required 的含义
  和场景归属的一样（app.api.services.vector_sync）；
- DELETE 立刻删除向量点和两个文件：cleanup=done；现在做不到时为 queued（由
  image_deletions 后台任务完成）。不管哪种，图片都会立刻从图库和搜索结果里消失：
  两者都读 PostgreSQL。
- POST .../reembed 用存下来的原图重新写入向量点（services.retrieval reembed_image）：
  这里唯一需要完整检索服务的写入。
- GET .../file 通过配置的对象存储返回存下来的缩略图或原图，用于图片 URL 无法直接加载
  的情况（mock 驱动的占位域名、私有存储桶）：检索服务就绪时用它的存储，否则用同一份
  配置构建一个。

失败都是 AppError：IMAGE_NOT_FOUND 404、VALIDATION_ERROR 422、SEARCH_POSTGRES_FAILED /
INDEX_POSTGRES_FAILED / IMAGE_FILE_UNAVAILABLE 502、NOT_READY 503。路由是同步函数，
所以 FastAPI 在线程池里执行它们。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request, Response

from app.api.deps import require_catalog, require_retrieval
from app.api.services.vector_sync import push_image_payload
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.middleware import get_trace_id
from app.schemas.images import ImageUpdateRequest

logger = logging.getLogger("svc.images")


def _vector_state(runtime: ApplicationState, image: dict[str, Any]) -> dict[str, Any]:
    try:
        stack = runtime.require_retrieval()
    except err.NotReadyError as exc:
        return {"status": "unavailable", "detail": f"retrieval stack not ready: {exc.detail}"}
    try:
        return stack.vector_status_uc.inspect(image)
    except Exception as exc:
        if isinstance(exc, err.AppError):
            logger.warning("vector status read failed image=%s | %s: %s", image["id"], exc.code, exc.detail)
            detail = err.client_detail(exc)
        else:
            logger.exception("vector status read failed image=%s", image["id"])
            detail = "vector store read failed"
        model = stack.model
        return {
            "status": "unavailable",
            "collection": model.collection,
            "embedding_space_id": model.space_id,
            "detail": detail,
        }


def get_image(runtime: ApplicationState, image_id: UUID) -> dict[str, Any]:
    image = require_catalog(runtime).images_uc.get(image_id)
    return {"ok": True, "image": image, "vector": _vector_state(runtime, image)}


def _after_push(runtime: ApplicationState, image_id: UUID, image: dict[str, Any], sync: dict[str, Any]) -> dict[str, Any]:
    """推送之后图片现在的样子：推送会改动它的台账行（synced_version），所以重新读一次；
    没有推送，或者图片在这期间被删了，就保留传入的那份。
    """
    if sync["vector_sync"] == "not_ready":
        return image
    try:
        return require_catalog(runtime).images_uc.get(image_id)
    except err.AppError:
        return image


def update_image(
    runtime: ApplicationState, request: Request, image_id: UUID, body: ImageUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # 没传的保持不变；显式传 null 会保留
    image = require_catalog(runtime).images_uc.update(image_id, fields)
    sync = push_image_payload(runtime, image_id)
    image = _after_push(runtime, image_id, image, sync)
    logger.info(
        "image updated id=%s fields=%s payload_version=%s vector_sync=%s | trace_id=%s",
        image_id, ",".join(sorted(fields)), image.get("payload_version"), sync["vector_sync"], get_trace_id(request),
    )
    # vector：详情页现在会显示的内容，这样页面不用再发第二个请求
    return {"ok": True, "image": image, "vector": _vector_state(runtime, image), **sync}


def push_vector(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    """把图片当前的 payload 重新写到当前 collection（修复 stale）。"""
    image = require_catalog(runtime).images_uc.get(image_id)  # 推送之前先确定 IMAGE_NOT_FOUND
    sync = push_image_payload(runtime, image_id)
    image = _after_push(runtime, image_id, image, sync)
    logger.info(
        "image payload pushed id=%s payload_version=%s vector_sync=%s | trace_id=%s",
        image_id, image.get("payload_version"), sync["vector_sync"], get_trace_id(request),
    )
    return {
        "ok": True,
        "image_id": image["id"],
        "payload_version": image["payload_version"],
        "vector": _vector_state(runtime, image),
        **sync,
    }


def reembed_image(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    """用存下来的原图重新计算 embedding 并写入向量点（修复 vector.status=missing）。
    需要完整的检索服务：编码器、Qdrant 和对象存储（否则 NOT_READY）。
    """
    require_catalog(runtime).images_uc.get(image_id)  # 先于其他一切确定 IMAGE_NOT_FOUND
    require_retrieval(runtime)
    with runtime.vector_writes.shared():  # 正在切换模型版本：等待，然后用切换后的检索服务
        require_retrieval(runtime).reembed_uc.execute(image_id)
    image = require_catalog(runtime).images_uc.get(image_id)  # 台账行已经变了
    logger.info(
        "image re-embedded id=%s payload_version=%s | trace_id=%s",
        image_id, image.get("payload_version"), get_trace_id(request),
    )
    return {
        "ok": True,
        "image_id": image["id"],
        "payload_version": image["payload_version"],
        "vector": _vector_state(runtime, image),
    }


def delete_image(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    queued = require_catalog(runtime).images_uc.delete(image_id)
    try:
        stack = runtime.require_retrieval()
    except err.NotReadyError:
        stack = None
    cleanup = "done" if stack is not None and stack.deletion_worker.clean_up(queued) else "queued"
    logger.info(
        "image deleted id=%s cleanup=%s | trace_id=%s", queued.get("image_id"), cleanup, get_trace_id(request)
    )
    return {"ok": True, "image_id": str(queued["image_id"]), "deleted_at": queued.get("deleted_at"), "cleanup": cleanup}


# 存储的文件可以作为什么类型返回：入库接受的图片类型，按文件内容判断
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
# 一个 id 对应的内容永远不变（id 就是内容的哈希），所以浏览器可以一直缓存
_FILE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def image_media_type(data: bytes) -> str | None:
    """这些字节的图片类型；不是可返回的类型时为 None。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((media for magic, media in _SIGNATURES if data.startswith(magic)), None)


def _file_storage(runtime: ApplicationState):
    """检索服务就绪时用它的存储（写入这些文件的那个实例：mock 驱动还把它们放在内存里），
    否则用 catalog 的。
    """
    try:
        return runtime.require_retrieval().infra.storage
    except err.NotReadyError:
        pass
    try:
        return require_catalog(runtime).file_storage()
    except (ValueError, err.AppError) as exc:  # IMAGE_STORAGE_CONFIG 有误，或者没有安装 boto3
        detail = exc.detail if isinstance(exc, err.AppError) else str(exc)
        raise err.not_ready(f"object storage unavailable: {detail}") from exc


def image_file(runtime: ApplicationState, image_id: UUID, variant: str) -> Response:
    """图片缩略图或原图的字节，以及它们的图片类型。"""
    url = require_catalog(runtime).images_uc.file_url(image_id, variant)
    data = _file_storage(runtime).read_by_url_or_key(url)  # 读不了时为 IMAGE_FILE_UNAVAILABLE
    media_type = image_media_type(data)
    if media_type is None:  # 绝不返回浏览器可能执行的内容
        raise err.image_file_unavailable(f"stored {variant} of {image_id} is not a served image type")
    return Response(content=data, media_type=media_type, headers=_FILE_HEADERS)
