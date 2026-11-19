"""类别树的 HTTP 适配层：调用用例，组装响应体，每次写入记日志。

用例通过 catalog 访问（只用 PostgreSQL；还没构建时 NOT_READY 503）。
失败都是规则和存储抛出的 AppError：VALIDATION_ERROR 422、
CATEGORY_NOT_FOUND 404、CATEGORY_CODE_EXISTS 409、CATEGORY_IN_USE 409、
CATEGORY_POSTGRES_FAILED 502。路由是同步函数，所以 FastAPI 在线程池里执行它们。

移动节点或修改菜系时，在存储的事务里重算该节点下所有图片的场景；提交之后，
检索服务就绪时推送它们的 payload（vector_sync.push_payloads），推送失败不会让请求失败。

POST /api/categories/import 通过同一个用例逐个节点应用整个类别树文件
（app.services.retrieval.import_categories）；菜系修改涉及的所有图片的 payload
在最后一个节点之后统一推送一次。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.api.deps import require_catalog
from app.api.services.vector_sync import push_payloads
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.categories import CategoryCreateRequest, CategoryUpdateRequest
from app.services.retrieval.import_categories import ImportCategoriesUseCase

logger = logging.getLogger("svc.categories")


def list_categories(runtime: ApplicationState) -> dict[str, Any]:
    return {"ok": True, **require_catalog(runtime).categories_uc.tree()}


def create_category(
    runtime: ApplicationState, request: Request, body: CategoryCreateRequest
) -> dict[str, Any]:
    category = require_catalog(runtime).categories_uc.create(body.model_dump())
    logger.info(
        "category created id=%s code=%s parent_id=%s | trace_id=%s",
        category.get("id"), category.get("code"), category.get("parent_id"), get_trace_id(request),
    )
    return {"ok": True, "category": category}


def update_category(
    runtime: ApplicationState, request: Request, category_id: int, body: CategoryUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # 没传的保持不变；显式传 null 会保留
    result = require_catalog(runtime).categories_uc.update(category_id, fields)
    category = result["category"]
    membership = result.get("scene_membership")
    scene_sync = None
    if membership is not None:
        # 已经提交了：推送尽力而为，没推完的在台账里保持未同步状态
        changed = membership["changed_ids"]
        scene_sync = {"images": membership["images"], "changed": len(changed), **push_payloads(runtime, changed)}
    logger.info(
        "category updated id=%s code=%s fields=%s%s | trace_id=%s",
        category.get("id"), category.get("code"), ",".join(sorted(fields)),
        "" if scene_sync is None else (
            " scenes: images={images} changed={changed} pushed={pushed} pending={pending} "
            "vector_sync={vector_sync}".format(**scene_sync)
        ),
        get_trace_id(request),
    )
    return {"ok": True, "category": category, "scene_sync": scene_sync}


def import_categories(
    runtime: ApplicationState, request: Request, document: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    """应用（dry_run：预览）一个类别树文件；响应体就是导入报告。"""
    use_case = ImportCategoriesUseCase(require_catalog(runtime).categories_uc)
    result = use_case.execute(document, dry_run=dry_run)
    membership = result.pop("scene_membership", None)
    scene_sync = None
    if membership is not None:
        # 已经提交了：推送尽力而为，没推完的在台账里保持未同步状态
        changed = membership["changed_ids"]
        scene_sync = {"images": membership["images"], "changed": len(changed), **push_payloads(runtime, changed)}
    summary = result["summary"]
    logger.info(
        "category import dry_run=%s total=%s create=%s update=%s unchanged=%s error=%s not_in_file=%s%s | trace_id=%s",
        dry_run, summary["total"], summary["create"], summary["update"], summary["unchanged"], summary["error"],
        summary["not_in_file"],
        "" if scene_sync is None else (
            " scenes: images={images} changed={changed} pushed={pushed} pending={pending} "
            "vector_sync={vector_sync}".format(**scene_sync)
        ),
        get_trace_id(request),
    )
    return {"ok": True, **result, "scene_sync": scene_sync}


def delete_category(runtime: ApplicationState, request: Request, category_id: int) -> dict[str, Any]:
    deleted = require_catalog(runtime).categories_uc.delete(category_id)
    logger.info(
        "category deleted id=%s code=%s | trace_id=%s",
        deleted.get("id"), deleted.get("code"), get_trace_id(request),
    )
    return {"ok": True, "deleted": deleted}
