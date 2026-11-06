"""VectorStorePort for retrieval, over app.clients.qdrant.QdrantStore.

Retrieval rules for the vector index live here: point id == images.id (uuid),
which payload fields are filterable (and indexed), the scene filter, and the
RetrievalError codes. Payload content comes from
services.retrieval.scene_membership.build_qdrant_payload; the score threshold
is decided in services.retrieval.score_threshold (None here = no threshold).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.clients.qdrant import CollectionMismatchError, QdrantStore
from app.repositories.ports import VectorStorePort
from app.services.retrieval import errors as err

# Filterable payload fields -> Qdrant index schema (contract §4.2)
PAYLOAD_INDEXES: dict[str, str] = {
    "scenes": "keyword",
    "label_status": "keyword",
    "category_id": "integer",
}
_EXTRA_FILTER_KEYS = ("label_status", "category_id")


def _image_point_id(point_id: UUID | str) -> str:
    """Points are keyed by images.id; anything else is a caller bug."""
    try:
        return str(point_id if isinstance(point_id, UUID) else UUID(str(point_id).strip()))
    except (ValueError, AttributeError) as exc:
        raise err.validation_error(f"point id must be images.id (uuid), got {point_id!r}") from exc


class QdrantVectorStore(VectorStorePort):
    def __init__(self, store: QdrantStore) -> None:
        self._store = store

    @property
    def collection(self) -> str:
        return self._store.collection

    def ensure_collection(self, *, recreate: bool = False) -> None:
        try:
            self._store.ensure_collection(recreate=recreate, payload_indexes=PAYLOAD_INDEXES)
        except CollectionMismatchError as exc:
            raise err.vector_dim_mismatch(str(exc)) from exc
        except Exception as exc:
            raise err.not_ready(f"qdrant ensure_collection: {type(exc).__name__}: {exc}") from exc

    def upsert(self, point_id: UUID | str, vector: list[float], payload: dict[str, Any]) -> None:
        pid = _image_point_id(point_id)
        try:
            self._store.upsert(pid, vector, payload)
        except Exception as exc:
            raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        score_threshold: float | None = None,
        scene: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        must: dict[str, Any] = {}
        if scene:
            must["scenes"] = str(scene)
        for key in _EXTRA_FILTER_KEYS:
            if filters and filters.get(key) is not None:
                must[key] = filters[key]
        try:
            return self._store.search(
                vector, top_k=top_k, score_threshold=score_threshold, must=must or None
            )
        except Exception as exc:
            raise err.search_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

    def delete(self, point_id: UUID | str) -> None:
        self.delete_batch([point_id])

    def delete_batch(self, point_ids: list[UUID | str]) -> None:
        ids = [_image_point_id(p) for p in point_ids]
        try:
            self._store.delete_batch(ids)
        except Exception as exc:
            raise err.index_qdrant_failed(f"delete: {type(exc).__name__}: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        return self._store.ping()

    def close(self) -> None:
        self._store.close()
