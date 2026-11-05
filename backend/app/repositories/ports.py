"""Retrieval ports. Implementations live under adapter/.

Vector/ONNX details: 算法逻辑. Table/JSON contract: 数据规范 SSOT.
PG v0.1: point id == images.id (uuid). Collection from embedding_models.current.
Named vector ``global``. VECTOR_DIM default 1024 (food ONNX); overridden by model row.
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

    def embed_url(self, image_url: str) -> list[float]:
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

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError


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

