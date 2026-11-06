"""Retrieval ports: what services/retrieval needs from the outside world.

Implementations: PgMetadataStore (repositories.metadata), QdrantVectorStore
(repositories.vectors, over clients.qdrant.QdrantStore), Mock/Boto3ObjectStorage
(clients.s3), Embedder (clients.inference; one instance shared with predict).
Wiring: app.bootstrap.retrieval_stack.

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
    ) -> tuple[list[dict[str, Any]], int]:
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
        raise NotImplementedError

    @abstractmethod
    def recompute_image_scenes(
        self, image_id: UUID | str, *, bump_version: bool = True
    ) -> dict[str, Any]:
        """Materialize image_scenes; returns {scene_codes, payload_version, ...}."""
        raise NotImplementedError

    @abstractmethod
    def map_category_to_scene_label(
        self, scene_code: str, category_id: int
    ) -> dict[str, Any] | None:
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


class ObjectStoragePort(ABC):
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

