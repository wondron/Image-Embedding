"""图片列表用例（PG v0.1 + §6.1 Wire）。

按请求场景的 image_scenes 归属分页列出图库（scenes 表；默认 general）。只有
scene.outputs_heating 时才附带 heating_group 和食物属性（food_fields.FOOD_FIELDS）（P9）。

只需要 PostgreSQL（由 app.bootstrap.catalog 构建），所以编码器或 Qdrant 挂了时图库也能
列出；向量库可用时，HTTP 层为每一项加上向量状态。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.enums import LabelStatus
from app.repositories.ports import MetadataStorePort
from app.core.rules.category_fields import normalize_category_id
from app.core.rules.enum_fields import coerce_enum
from app.core.rules.food_fields import FOOD_FIELDS
from app.services.retrieval.scenes import lookup_scene, requested_scene_code
from app.services.retrieval.vector_status import expected_payload


def _as_id(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _item_from_row(
    row: dict[str, Any],
    *,
    scene_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """按 §6.1.2 构建一个列表项（只用权威字段）。"""
    image_id = _as_id(row["id"])
    item: dict[str, Any] = {
        "id": image_id,
        "original_url": row.get("original_url"),
        "thumb_url": row.get("thumb_url"),
        "category_id": row.get("category_id"),
        "category": row.get("category_name") or row.get("category"),
        "scenes": row.get("scenes"),
        "label_status": row.get("label_status"),
        "description": row.get("description") or "",
        "tags": list(row.get("tags") or []),
        "width": row.get("width"),
        "height": row.get("height"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if scene_meta and scene_meta.get("outputs_heating"):
        item["heating_group"] = row.get("heating_group")
        item.update({key: row.get(key) for key in FOOD_FIELDS})
    return item


class ListImagesUseCase:
    def __init__(self, metadata: MetadataStorePort) -> None:
        self._metadata = metadata

    def execute(
        self,
        *,
        scene: str | None = None,
        page: int = 1,
        page_size: int = 24,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        category_id: int | None = None,
        label_status: str | None = None,
        out_of_sync: bool = False,
    ) -> dict[str, Any]:
        scene_meta = lookup_scene(
            self._metadata,
            requested_scene_code(scene),
            unknown=err.list_unknown_scene,
            failed=err.list_postgres_failed,
        )
        active_scene = str(scene_meta["code"])

        try:
            page_i = int(page)
            page_size_i = int(page_size)
        except (TypeError, ValueError) as exc:
            raise err.validation_error("page and page_size must be integers") from exc

        if page_i < 1:
            raise err.validation_error("page must be >= 1")
        if not (1 <= page_size_i <= 100):
            raise err.validation_error("page_size must be in 1..100")

        q_norm: str | None = None
        if q is not None:
            q_s = str(q).strip()
            if q_s:
                q_norm = q_s

        cat_norm: str | None = None
        if category is not None:
            c = str(category).strip()
            if c:
                cat_norm = c

        tag_norm: str | None = None  # 存储的标签去掉了首尾空白，所以过滤条件也要去掉
        if tag is not None:
            t = str(tag).strip()
            if t:
                tag_norm = t

        subtree_id = None if category_id is None else normalize_category_id(category_id)
        label_norm: str | None = None
        if label_status is not None and str(label_status).strip():
            label_norm = coerce_enum(LabelStatus, label_status, field="label_status").value

        rows, total = self._metadata.list_images(
            scene=active_scene,
            page=page_i,
            page_size=page_size_i,
            q=q_norm,
            category=cat_norm,
            tag=tag_norm,
            category_id=subtree_id,
            label_status=label_norm,
            out_of_sync=bool(out_of_sync),
        )
        items = [_item_from_row(row, scene_meta=scene_meta) for row in rows]
        return {
            "scene": active_scene,
            "page": page_i,
            "page_size": page_size_i,
            "total": int(total),
            "items": items,
            # 不属于 wire：每张图的向量点应该带的内容，这样向量库可用时，HTTP 层能判断这一页的
            # 向量状态（每页读一次）
            "expected_payloads": {item["id"]: expected_payload(row) for item, row in zip(items, rows)},
        }
