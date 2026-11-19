"""图库对单张已存图片的管理：读取、修改、删除（/api/images/{image_id}）。

只需要 PostgreSQL（和场景管理一样由 app.bootstrap.catalog 构建），所以编码器或 Qdrant
挂了时也能编辑和删除。字段规则：core.rules.image_fields；对照已存行的检查和写入，
在 catalog 存储里是一个事务（repositories.images.patch_image、
repositories.deletions.enqueue_image_deletion）。

向量这一侧归 HTTP 层（app.api.services.images），每一部分都只在检索服务就绪时进行：
修改之后把图片的 payload 推送到当前 collection，删除之后立刻删除向量点和两个文件，
详情里加上图片在当前 collection 里的状态（services.retrieval.vector_status）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import CatalogStorePort
from app.core.rules.food_fields import FOOD_FIELDS
from app.core.rules.image_fields import normalize_image_patch


def _ledger_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_id": row.get("model_id"),
        "name": row.get("name"),
        "version": row.get("version"),
        "collection": row.get("collection"),
        "model_status": row.get("model_status"),
        "synced_version": row.get("synced_version"),
        "embedded_at": row.get("embedded_at"),
    }


def image_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """详情行对应的 wire 图片：列表项的字段（§6.1.2），再加上只有详情才显示的字段。
    食物字段和 heating_group 总是出现，不管是什么场景。
    """
    return {
        "id": str(row["id"]),
        "sha256": row.get("sha256"),
        "original_url": row.get("original_url"),
        "thumb_url": row.get("thumb_url"),
        "width": row.get("width"),
        "height": row.get("height"),
        "category_id": row.get("category_id"),
        "category": row.get("category_name"),
        "category_code": row.get("category_code"),
        "label_status": row.get("label_status"),
        "scenes": list(row.get("scenes") or []),
        "description": row.get("description") or "",
        "tags": list(row.get("tags") or []),
        "source_type": row.get("source_type"),
        "license": row.get("license"),
        **{key: row.get(key) for key in FOOD_FIELDS},
        "heating_group": row.get("heating_group"),
        "payload_version": row.get("payload_version"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "embeddings": [_ledger_item(ledger) for ledger in row.get("embeddings") or []],
    }


class ManageImagesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def get(self, image_id: UUID | str) -> dict[str, Any]:
        row = self._catalog.get_image_detail(image_id)
        if row is None:
            raise err.image_not_found(f"image {image_id} does not exist")
        return image_detail(row)

    def file_url(self, image_id: UUID | str, variant: str) -> str:
        """图片存下来的文件在哪里：缩略图（没有缩略图时为原图）或原图。已删除图片的文件
        在清理删除它们之前仍然可以读取。
        """
        row = self._catalog.get_image_file_urls(image_id)
        if row is None:
            raise err.image_not_found(f"image {image_id} does not exist")
        thumb = str(row.get("thumb_url") or "").strip()
        original = str(row.get("original_url") or "").strip()
        url = (thumb or original) if variant == "thumb" else original
        if not url:
            raise err.image_file_unavailable(f"image {image_id} has no stored {variant}")
        return url

    def update(self, image_id: UUID | str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """只修改传来的字段（image_fields）；返回提交后的图片。"""
        return image_detail(self._catalog.patch_image(image_id, normalize_image_patch(fields)))

    def delete(self, image_id: UUID | str) -> dict[str, Any]:
        """从 PostgreSQL 删除，并把向量 / 文件的清理加入队列；返回队列行
        {image_id, original_url, thumb_url, deleted_at}。排队期间再次删除，返回同一行。
        """
        return dict(self._catalog.enqueue_image_deletion(image_id))
