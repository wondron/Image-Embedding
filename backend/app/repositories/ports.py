"""Retrieval ports: what services/retrieval needs from the outside world.

Implementations: PgMetadataStore (repositories.metadata; both MetadataStorePort for the
retrieval use cases and CatalogStorePort for the category / scene admin), QdrantVectorStore
(repositories.vectors, over clients.qdrant.QdrantStore), Mock/S3ImageStorage
(clients.image_storage), Embedder (clients.inference; one instance shared with predict).
Wiring: app.bootstrap.catalog (admin, PostgreSQL only) and app.bootstrap.retrieval_stack.

PG v0.1: point id == images.id (uuid). Collection/dim/distance come from the
embedding_models row with status='current' (services.retrieval.active_model).
VECTOR_DIM is only the fallback default (food ONNX = 1024).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


VECTOR_DIM = 1024


@runtime_checkable
class EmbeddingPort(Protocol):
    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        ...


class VectorStorePort(ABC):
    """Qdrant read/write. Point ids are uuid (== images.id)."""

    @abstractmethod
    def upsert(
        self,
        point_id: UUID | str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        score_threshold: float | None = None,
        scene: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return [{id, score, payload}, ...]. Empty list = no neighbors."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, point_id: UUID | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_payloads(self, payloads: dict[str, dict[str, Any]]) -> list[str]:
        """Write each point's payload fields (vectors untouched); returns the ids that are not in
        the collection, which are skipped."""
        raise NotImplementedError

    @abstractmethod
    def get_payloads(self, point_ids: list[UUID | str]) -> dict[str, dict[str, Any]]:
        """{point id: payload} for the given ids that are points of the collection (no vectors);
        an id without a point is left out."""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        """{ok, collection, points_count, ...}; never raises."""
        raise NotImplementedError

    def close(self) -> None:
        """Release the client (idempotent)."""


class MetadataStorePort(ABC):
    """PostgreSQL metadata (PG v0.1). Backend owns implementation."""

    @abstractmethod
    def insert_image(self, row: dict[str, Any]) -> UUID | str:
        raise NotImplementedError

    @abstractmethod
    def update_image(self, image_id: UUID | str, fields: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_deleted(self, image_id: UUID | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_active_by_ids(
        self,
        image_ids: list[UUID | str],
        *,
        scene: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_images(
        self,
        *,
        scene: str,
        page: int = 1,
        page_size: int = 24,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        category_id: int | None = None,
        label_status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """category_id filters on the category's subtree; label_status on the exact value."""
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, image_id: UUID | str) -> dict[str, Any] | None:
        raise NotImplementedError

    # -- categories / scenes ------------------------------------------------

    @abstractmethod
    def get_category_id_by_code(self, code: str) -> int | None:
        raise NotImplementedError

    @abstractmethod
    def get_scene_by_code(self, code: str) -> dict[str, Any] | None:
        """The scenes row (any state) or None."""
        raise NotImplementedError

    @abstractmethod
    def list_scene_codes(self, *, active_only: bool = True) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def recompute_image_scenes(
        self, image_id: UUID | str, *, bump_version: bool = True
    ) -> dict[str, Any]:
        """Materialize image_scenes; returns {scene_codes, payload_version, ...}."""
        raise NotImplementedError

    @abstractmethod
    def recompute_scene_memberships(
        self, *, after: UUID | str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """Recompute the next batch of images in id order: {image_ids, changed_ids, last_id}."""
        raise NotImplementedError

    @abstractmethod
    def map_categories_to_scene_labels(
        self, scene_id: int, category_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """{category_id: {id, code, name}} for the categories that map to an active label."""
        raise NotImplementedError

    # -- embedding models / ledger -----------------------------------------

    @abstractmethod
    def get_current_embedding_model(self) -> dict[str, Any]:
        """The single embedding_models row with status='current' (NOT_READY if none)."""
        raise NotImplementedError

    @abstractmethod
    def upsert_image_embedding(
        self,
        *,
        model_id: int,
        image_id: UUID | str,
        synced_version: int | None = None,
    ) -> None:
        """Record that image_id's payload at synced_version is in model_id's collection."""
        raise NotImplementedError

    @abstractmethod
    def get_payload_rows(self, image_ids: list[str]) -> list[dict[str, Any]]:
        """What the Qdrant payload of these images is built from: {id, payload_version,
        category_id, label_status, food_state, scenes} per existing image."""
        raise NotImplementedError

    @abstractmethod
    def mark_payloads_pushed(self, *, model_id: int, versions: dict[str, int]) -> list[str]:
        """Ledger: {image_id: payload_version} is now in model_id's collection. Only versions that
        are still the image's current one are recorded; returns the image ids recorded."""
        raise NotImplementedError

    # -- deletion queue -----------------------------------------------------

    @abstractmethod
    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_pending_deletions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def complete_deletion(self, image_id: UUID | str) -> None:
        raise NotImplementedError

    # -- lifecycle ------------------------------------------------------------

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        """Release the connection pool (idempotent)."""


class CatalogStorePort(ABC):
    """The category tree and the scene configuration, as the admin pages edit them.

    PostgreSQL only: the admin use cases (services.retrieval.manage_categories / manage_scenes)
    work while the encoder, Qdrant or object storage are down. Each write is one transaction
    under the catalog lock (repositories.catalog_lock). Implemented by PgMetadataStore.
    """

    # -- category tree ------------------------------------------------------

    @abstractmethod
    def list_categories(self) -> list[dict[str, Any]]:
        """Every node, parents before children, siblings in display order."""
        raise NotImplementedError

    @abstractmethod
    def list_heating_groups(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_category(self, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_category(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """Patch the given fields; parent_id moves the node with its subtree."""
        raise NotImplementedError

    @abstractmethod
    def delete_category(self, category_id: int) -> dict[str, Any]:
        """Delete a node nothing uses; CATEGORY_IN_USE otherwise."""
        raise NotImplementedError

    # -- image scene memberships (PG only; writes return a consistent snapshot) --

    @abstractmethod
    def get_scene_memberships(self, image_id: UUID | str) -> dict[str, Any]:
        """Full scene directory, effective codes and image payload_version; no recompute."""
        raise NotImplementedError

    @abstractmethod
    def put_manual_scene_membership(
        self, image_id: UUID | str, scene_id: int, *, is_member: bool
    ) -> dict[str, Any]:
        """Upsert manual and recompute atomically; does not push vector payloads (the HTTP layer
        does that after the commit when the retrieval stack is up)."""
        raise NotImplementedError

    @abstractmethod
    def clear_manual_scene_membership(self, image_id: UUID | str, scene_id: int) -> dict[str, Any]:
        """Clear only manual and recompute atomically (inactive scenes may be cleared)."""
        raise NotImplementedError

    # -- one stored image (PG only; the HTTP layer brings the vector store along) --

    @abstractmethod
    def get_image_detail(self, image_id: UUID | str) -> dict[str, Any] | None:
        """The image with category, heating group, food, effective scenes and its ledger rows
        (embeddings), read in one snapshot; None when there is no such image."""
        raise NotImplementedError

    @abstractmethod
    def patch_image(self, image_id: UUID | str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply a normalized patch (services.retrieval.image_fields) in one transaction and
        return the detail as committed; IMAGE_NOT_FOUND when there is no such image."""
        raise NotImplementedError

    @abstractmethod
    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        """Delete the image and queue its vector / file cleanup (image_deletions); returns the
        queue row. IMAGE_NOT_FOUND when there is neither an image nor a queue row."""
        raise NotImplementedError

    # -- scenes (each write returns the whole scene: fields, labels, rules, plus sync_required:
    #    whether stored images need POST /api/scenes/sync; a delete returns {id, code, sync_required})

    @abstractmethod
    def list_scene_configs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_scene(self, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene(self, scene_id: int) -> dict[str, Any]:
        """Delete a scene with its labels, rules and memberships; returns {id, code}."""
        raise NotImplementedError

    @abstractmethod
    def create_scene_label(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene_label(self, scene_id: int, label_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene_label(self, scene_id: int, label_id: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_scene_rule(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene_rule(self, scene_id: int, rule_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        raise NotImplementedError

    # -- lifecycle ------------------------------------------------------------

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        """Release the connection pool (idempotent)."""


class FlyBFallbackPort(ABC):
    """PG v0.1: FlyB OUT OF SCOPE (M0). Kept for legacy imports only."""

    @abstractmethod
    def search_fallback(
        self,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
    ) -> Any:
        raise NotImplementedError


class ImageStoragePort(ABC):
    """S3/OSS object storage (contract §5). Returns full https URLs for DB columns."""

    @abstractmethod
    def put_original(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        """Upload original/{image_id}.{ext}; return full https original_url."""
        raise NotImplementedError

    @abstractmethod
    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        """Upload thumb/{image_id}.webp; return full https thumb_url."""
        raise NotImplementedError

    @abstractmethod
    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        """Upload staging/{candidate_id}.{ext}; return full https staged_url."""
        raise NotImplementedError

    @abstractmethod
    def delete_by_url_or_key(self, url_or_key: str) -> None:
        """Best-effort delete by full https URL or object key. Missing object = ok."""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        """{ok, driver, bucket, ...}; never raises."""
        raise NotImplementedError

