"""Gallery admin of one stored image: read, patch, delete (/api/images/{image_id}).

Needs PostgreSQL only (built by app.bootstrap.catalog, like the scene admin), so edits and
deletions work while the encoder or Qdrant are down. Field rules: services.retrieval.image_fields;
the checks against the stored row and the write are one transaction in the catalog store
(repositories.images.patch_image, repositories.deletions.enqueue_image_deletion).

The vector side belongs to the HTTP layer (app.api.services.images), each part only when the
retrieval stack is up: after a patch it pushes the image's payload to the current collection,
after a delete it removes the point and the two files at once, and the detail gets the image's
state in the current collection (services.retrieval.vector_status).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import CatalogStorePort
from app.services.retrieval.food_fields import FOOD_FIELDS
from app.services.retrieval.image_fields import normalize_image_patch


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
    """The wire image of a detail row: the list item's fields (§6.1.2) plus what only the detail
    shows. Food fields and heating_group are always present, whatever the scene."""
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
        """Where the image's stored file is: the thumbnail (the original when it has none) or the
        original. A deleted image's files stay readable until the cleanup removes them."""
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
        """Only the fields given change (image_fields); returns the image as committed."""
        return image_detail(self._catalog.patch_image(image_id, normalize_image_patch(fields)))

    def delete(self, image_id: UUID | str) -> dict[str, Any]:
        """Delete from PostgreSQL and queue the vector / file cleanup; returns the queue row
        {image_id, original_url, thumb_url, deleted_at}. Deleting again while it is queued
        answers the same row."""
        return dict(self._catalog.enqueue_image_deletion(image_id))
