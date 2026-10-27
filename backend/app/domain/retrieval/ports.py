"""Retrieval ports. Implementations live under adapter/.

Vector/ONNX details: 算法逻辑. Table/JSON contract: 数据规范 SSOT.
Point id == MySQL images.id (unsigned int). VECTOR_DIM=1024 Cosine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


VECTOR_DIM = 1024


@runtime_checkable
class EmbeddingPort(Protocol):
    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        ...

    def embed_url(self, image_url: str) -> list[float]:
        ...


class VectorStorePort(ABC):
    """Qdrant read/write. Point ids are unsigned ints (== images.id)."""

    @abstractmethod
    def upsert(
        self,
        point_id: int,
        vector: list[float],
        payload: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return [{id, score, payload}, ...]. Empty list = no neighbors."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, point_id: int) -> None:
        raise NotImplementedError


class MetadataStorePort(ABC):
    """MySQL `images` table (SSOT §2)."""

    @abstractmethod
    def insert_image(self, row: dict[str, Any]) -> int:
        """Insert row; return auto id. Sets qdrant_point_id=id after insert if needed."""
        raise NotImplementedError

    @abstractmethod
    def update_image(self, image_id: int, fields: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_deleted(self, image_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_active_by_ids(
        self,
        image_ids: list[int],
        *,
        scene: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        """id -> row for status='active' only; missing/deleted omitted.

        If scene is set, also require JSON_CONTAINS(scenes, JSON_QUOTE(scene), '$').
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, image_id: int) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError


class FlyBFallbackPort(ABC):
    """Only when final self-built list length == 0 and scene=normal (§4.1)."""

    @abstractmethod
    def search_fallback(
        self,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
    ) -> Any:
        """Return FlyB parse_results shape: list[dict] or empty envelope dict."""
        raise NotImplementedError
