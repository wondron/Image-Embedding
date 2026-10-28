"""List-images use case (SSOT section 3.5).

Library pagination: active + scene JSON_CONTAINS, optional q/category.
Does not soft-delete. image_url prefers stored url else /api/images/file?id=.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import MetadataStorePort
from app.domain.retrieval.scenes import normalize_list_scene

logger = logging.getLogger("retrieval.list")


def _image_url(
    public_file_base: str | None,
    image_id: int,
    stored_url: str | None,
) -> str | None:
    if stored_url:
        return stored_url
    if public_file_base:
        return f"{public_file_base.rstrip('/')}/api/images/file?id={image_id}"
    return f"/api/images/file?id={image_id}"


def _item_from_row(
    row: dict[str, Any],
    *,
    public_file_base: str | None,
) -> dict[str, Any]:
    image_id = int(row["id"])
    return {
        "id": image_id,
        "qdrant_point_id": int(row.get("qdrant_point_id") or image_id),
        "path": row.get("path"),
        "url": row.get("url"),
        "image_url": _image_url(public_file_base, image_id, row.get("url")),
        "title": row.get("title"),
        "category": row.get("category"),
        "tags": row.get("tags"),
        "scenes": row.get("scenes"),
        "width": row.get("width"),
        "height": row.get("height"),
        "status": row.get("status") or "active",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


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
        public_file_base: str | None = None,
        trace_id: str | None = None,
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

        # Empty category must not act as a filter (SSOT section 3.5).
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
        items = [
            _item_from_row(row, public_file_base=public_file_base) for row in rows
        ]
        return {
            "ok": True,
            "trace_id": trace_id,
            "scene": active_scene,
            "page": page_i,
            "page_size": page_size_i,
            "total": int(total),
            "items": items,
        }
