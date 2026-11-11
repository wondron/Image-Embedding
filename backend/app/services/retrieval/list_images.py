"""List-images use case (PG v0.1 + §6.1 Wire).

Library pagination via image_scenes membership for the requested scene (scenes table;
default general). Attach heating_group and the food attributes
(food_fields.FOOD_FIELDS) only when scene.outputs_heating (P9).

Needs PostgreSQL only (built by app.bootstrap.catalog), so the gallery lists while the encoder
or Qdrant are down; the HTTP layer adds each item's vector state when the vector store is up.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.enums import LabelStatus
from app.repositories.ports import MetadataStorePort
from app.services.retrieval.category_fields import normalize_category_id
from app.services.retrieval.enum_fields import coerce_enum
from app.services.retrieval.food_fields import FOOD_FIELDS
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

        tag_norm: str | None = None  # stored tags are stripped, so strip the filter too
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
            # Not part of the wire: what each image's point should carry, so the HTTP layer can
            # tell the vector state of the page when the vector store is up (one read per page)
            "expected_payloads": {item["id"]: expected_payload(row) for item, row in zip(items, rows)},
        }
