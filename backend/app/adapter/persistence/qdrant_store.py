"""Thin adapter around app.domain.common.qdrant_store.QdrantStore.

Do NOT duplicate qdrant-client usage here.
PG v0.1: point ids are uuid (== images.id); collection from embedding_models.current.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any
from uuid import UUID

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import VECTOR_DIM, VectorStorePort

logger = logging.getLogger("qdrant_adapter")

_DOMAIN_QDRANT_MOD = "app.domain.common.qdrant_store"


def _load_domain_store_cls():
    mod = import_module(_DOMAIN_QDRANT_MOD)
    return getattr(mod, "QdrantStore", None)


class QdrantVectorStore(VectorStorePort):
    """Adapter: Metadata supplies current collection/dim/distance."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        *,
        collection: str,
        vector_size: int = VECTOR_DIM,
        distance: str = "cosine",
        url: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.collection = collection
        self.vector_size = int(vector_size)
        self.distance = distance
        if url:
            self.url = str(url).rstrip("/")
        else:
            self.url = f"http://{host}:{int(port)}"

        cls = _load_domain_store_cls()
        # Domain QdrantStore: local REST :6333 via httpx often 502; prefer gRPC.
        qdrant_cfg = {
            "url": self.url,
            "collection": collection,
            "vector_size": self.vector_size,
            "vector_dim": self.vector_size,
            "distance": distance,
            "prefer_grpc": True,
        }
        if config and isinstance(config.get("QDRANT_CONFIG"), dict):
            merged = dict(config.get("QDRANT_CONFIG") or {})
            merged.update(qdrant_cfg)
            wrapped: dict[str, Any] = dict(config)
            wrapped["QDRANT_CONFIG"] = merged
        else:
            wrapped = {"QDRANT_CONFIG": qdrant_cfg}
            if config:
                wrapped.update({k: v for k, v in config.items() if k != "QDRANT_CONFIG"})
        try:
            self._impl = cls(
                wrapped,
                collection=collection,
                vector_dim=self.vector_size,
                distance=distance,
            )
        except Exception:
            logger.exception("failed to construct domain QdrantStore")
            self._impl = None

    @property
    def wired(self) -> bool:
        return self._impl is not None

    def set_collection(
        self,
        collection: str,
        *,
        vector_dim: int,
        distance: str,
    ) -> None:
        self.collection = collection
        self.vector_size = int(vector_dim)
        self.distance = distance
        if self._impl is not None:
            self._impl.set_collection(
                collection, vector_dim=vector_dim, distance=distance
            )

    def ensure_collection(self, *, recreate: bool = False) -> None:
        if self._impl is None:
            raise err.not_ready("QdrantStore not wired")
        try:
            self._impl.ensure_collection(recreate=recreate)
        except ValueError as exc:
            raise err.vector_dim_mismatch(str(exc)) from exc
        except Exception as exc:
            raise err.not_ready(f"qdrant ensure_collection: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        if self._impl is None:
            return {
                "ok": False,
                "detail": "domain QdrantStore unavailable",
                "collection": self.collection,
            }
        try:
            client = self._impl._get_client()
            names = {c.name for c in client.get_collections().collections}
            points_count = None
            if self.collection in names:
                info = client.get_collection(self.collection)
                points_count = getattr(info, "points_count", None)
            return {
                "ok": True,
                "url": self.url,
                "collection": self.collection,
                "points_count": points_count,
                "vector_size": self.vector_size,
                "named_vector": "global",
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "url": self.url,
                "collection": self.collection,
            }

    def upsert(
        self,
        point_id: UUID | str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        if self._impl is None:
            raise err.index_qdrant_failed("QdrantStore not wired")
        try:
            self._impl.upsert(point_id, vector, payload)
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
        if self._impl is None:
            raise err.search_qdrant_failed("QdrantStore not wired")
        try:
            return self._impl.search(
                vector,
                top_k=top_k,
                filters=filters,
                score_threshold=score_threshold,
                scene=scene,
            )
        except Exception as exc:
            raise err.search_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

    def delete(self, point_id: UUID | str) -> None:
        if self._impl is None:
            raise err.index_qdrant_failed("QdrantStore not wired")
        try:
            self._impl.delete(point_id)
        except Exception as exc:
            raise err.index_qdrant_failed(f"delete: {type(exc).__name__}: {exc}") from exc

    def delete_batch(self, point_ids: list[UUID | str]) -> None:
        if self._impl is None:
            raise err.index_qdrant_failed("QdrantStore not wired")
        try:
            self._impl.delete_batch(point_ids)
        except Exception as exc:
            raise err.index_qdrant_failed(
                f"delete_batch: {type(exc).__name__}: {exc}"
            ) from exc
