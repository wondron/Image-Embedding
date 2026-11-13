"""VectorStorePort for retrieval, over app.clients.qdrant.QdrantStore.

Retrieval rules for the vector index live here: point id == images.id (uuid),
which payload fields are filterable (and indexed), the scene filter, and the
Qdrant error codes. Payload content comes from
core.rules.scene_membership.build_qdrant_payload; the score threshold
is decided in services.retrieval.score_threshold (None here = no threshold).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.clients.qdrant import CollectionMismatchError, QdrantStore
from app.repositories.ports import VectorStorePort
from app.core import errors as err

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

    def sibling(self, *, collection: str, dim: int, distance: str) -> "QdrantVectorStore":
        """The collection of another model version over the same client (never close it)."""
        return QdrantVectorStore(self._store.sibling(collection=collection, vector_dim=dim, distance=distance))

    def ensure_collection(self, *, recreate: bool = False) -> None:
        try:
            self._store.ensure_collection(recreate=recreate, payload_indexes=PAYLOAD_INDEXES)
        except CollectionMismatchError as exc:
            raise err.vector_dim_mismatch(str(exc)) from exc
        except Exception as exc:
            raise err.not_ready(f"qdrant ensure_collection: {type(exc).__name__}: {exc}") from exc

    def collection_exists(self) -> bool:
        try:
            return self._store.collection_exists()
        except Exception as exc:
            raise err.index_qdrant_failed(f"collection_exists: {type(exc).__name__}: {exc}") from exc

    def drop_collection(self) -> bool:
        """Delete the whole collection (a retired model version); False when it was not there."""
        try:
            return self._store.drop_collection()
        except Exception as exc:
            raise err.index_qdrant_failed(f"drop_collection: {type(exc).__name__}: {exc}") from exc

    def existing_ids(self, point_ids: list[UUID | str]) -> set[str]:
        """The image ids among point_ids that have a point in this collection."""
        ids = [_image_point_id(pid) for pid in point_ids]
        try:
            return {str(pid) for pid in self._store.existing_ids(ids)}
        except Exception as exc:
            raise err.search_qdrant_failed(f"existing_ids: {type(exc).__name__}: {exc}") from exc

    def point_count(self) -> int:
        """How many points the collection holds (exact)."""
        try:
            return self._store.count()
        except Exception as exc:
            raise err.search_qdrant_failed(f"point_count: {type(exc).__name__}: {exc}") from exc

    def get_vectors(self, point_ids: list[UUID | str]) -> dict[str, list[float]]:
        """{image id: vector} of the images that have a point in this collection."""
        ids = [_image_point_id(pid) for pid in point_ids]
        try:
            found = self._store.get_vectors(ids)
        except Exception as exc:
            raise err.search_qdrant_failed(f"get_vectors: {type(exc).__name__}: {exc}") from exc
        return {str(pid): vector for pid, vector in found.items()}

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

    def set_payloads(self, payloads: dict[str, dict[str, Any]]) -> list[str]:
        """Write the payload of each image's point; returns the image ids that have no point in
        this collection (never embedded into it), which are skipped."""
        if not payloads:
            return []
        by_id = {_image_point_id(pid): payload for pid, payload in payloads.items()}
        try:
            present = self._store.existing_ids(list(by_id))
            self._store.set_payloads({pid: payload for pid, payload in by_id.items() if pid in present})
        except Exception as exc:
            raise err.index_qdrant_failed(f"set_payloads: {type(exc).__name__}: {exc}") from exc
        return sorted(pid for pid in by_id if pid not in present)

    def get_payloads(self, point_ids: list[UUID | str]) -> dict[str, dict[str, Any]]:
        """{image id: payload} of the images that have a point in this collection."""
        ids = [_image_point_id(pid) for pid in point_ids]
        try:
            found = self._store.get_payloads(ids)
        except Exception as exc:
            raise err.search_qdrant_failed(f"get_payloads: {type(exc).__name__}: {exc}") from exc
        return {str(pid): payload for pid, payload in found.items()}

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
