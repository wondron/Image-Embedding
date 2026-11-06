"""List-images use case (PG v0.1 + §6.1 Wire).

Library pagination via image_scenes membership for the active scene code.
Default scene = general. Attach heating_group/food_state only when
scene.outputs_heating (P9).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.services.retrieval import errors as err
from app.repositories.ports import MetadataStorePort
from app.services.retrieval.scenes import normalize_list_scene

logger = logging.getLogger("retrieval.list")


def _as_id(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _item_from_row(
    row: dict[str, Any],
    *,
    scene_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one List item per §6.1.2 (authoritative fields only)."""
    image_id = _as_id(row["id"])
    item: dict[str, Any] = {
        "id": image_id,
        "original_url": row.get("original_url"),
        "thumb_url": row.get("thumb_url"),
        "category_id": row.get("category_id"),
        "category": row.get("category_name") or row.get("category"),
        "scenes": row.get("scenes"),
        "label_status": row.get("label_status"),
        "width": row.get("width"),
        "height": row.get("height"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if scene_meta and scene_meta.get("outputs_heating"):
        item["heating_group"] = row.get("heating_group")
        item["food_state"] = row.get("food_state")
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
    ) -> dict[str, Any]:
        active_scene = normalize_list_scene(scene)

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

        rows, total = self._metadata.list_images(
            scene=active_scene,
            page=page_i,
            page_size=page_size_i,
            q=q_norm,
            category=cat_norm,
        )

        scene_meta = None
        try:
            scene_meta = self._metadata.get_scene_by_code(active_scene)
        except Exception:
            logger.exception("get_scene_by_code failed scene=%s", active_scene)

        items = [_item_from_row(row, scene_meta=scene_meta) for row in rows]
        return {
            "scene": active_scene,
            "page": page_i,
            "page_size": page_size_i,
            "total": int(total),
            "items": items,
        }
