"""Thin adapter around app.domain.common.qdrant_store.QdrantStore.

Do NOT duplicate qdrant-client usage here. Point ids are unsigned ints (== images.id).
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import VECTOR_DIM, VectorStorePort

logger = logging.getLogger("qdrant_adapter")

_DOMAIN_QDRANT_MOD = "app.domain.common.qdrant_store"


def _load_domain_store_cls():
    try:
        from importlib import import_module

        mod = import_module(_DOMAIN_QDRANT_MOD)
        return getattr(mod, "QdrantStore", None)
    except ImportError:
        return None


class QdrantVectorStore(VectorStorePort):
    def __init__(
        self,
        *,
        url: str | None = None,
        host: str = "qdrant",
        port: int = 6333,
        collection: str = "image_embeddings",
        vector_size: int = VECTOR_DIM,
        distance: str = "Cosine",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.url = url or f"http://{host}:{port}"
        self.host = host
        self.port = port
        self.collection = collection
        self.vector_size = int(vector_size)
        self.distance = distance
        self._impl = None

        cls = _load_domain_store_cls()
        if cls is None:
            return

        # Domain QdrantStore: local REST :6333 via httpx often 502; prefer gRPC :6334.
        qdrant_cfg = {
            "url": self.url,
            "collection": collection,
            "vector_size": self.vector_size,
            "distance": distance,
            "prefer_grpc": True,
        }
        if config and isinstance(config.get("QDRANT_CONFIG"), dict):
            merged = dict(config.get("QDRANT_CONFIG") or {})
            merged.update(qdrant_cfg)
            wrapped = dict(config)
            wrapped["QDRANT_CONFIG"] = merged
        else:
            wrapped = {"QDRANT_CONFIG": qdrant_cfg}
            if config:
                wrapped.update({k: v for k, v in config.items() if k != "QDRANT_CONFIG"})
        try:
            self._impl = cls(wrapped)
        except Exception:
            logger.exception("failed to construct domain QdrantStore")
            self._impl = None

    @property
    def wired(self) -> bool:
        return self._impl is not None

    def ensure_collection(self, *, recreate: bool = False) -> None:
        if self._impl is None:
            raise err.not_ready("QdrantStore not wired")
        try:
            self._impl.ensure_collection(recreate=recreate)
        except ValueError as exc:
            # dim mismatch from domain ensure
            raise err.vector_dim_mismatch(str(exc)) from exc
        except Exception as exc:
            raise err.not_ready(f"qdrant ensure_collection: {exc}") from exc

    def ping(self) -> dict[str, Any]:
        if self._impl is None:
            return {"ok": False, "detail": "domain QdrantStore unavailable", "collection": self.collection}
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
        point_id: int,
        vector: list[float],
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._impl is None:
            raise err.index_qdrant_failed("QdrantStore not wired")
        try:
            self._impl.upsert(int(point_id), vector, payload)
        except Exception as exc:
            raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        score_threshold: float | None = None,
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
            )
        except TypeError:
            # older signature fallback
            try:
                return self._impl.search(vector, top_k=top_k, filters=filters)
            except Exception as exc:
                raise err.search_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc
        except Exception as exc:
            raise err.search_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

    def delete(self, point_id: int) -> None:
        if self._impl is None:
            raise err.index_qdrant_failed("QdrantStore not wired")
        try:
            client = self._impl._get_client()
            client.delete(
                collection_name=self.collection,
                points_selector=[int(point_id)],
            )
        except Exception as exc:
            raise err.index_qdrant_failed(f"delete: {type(exc).__name__}: {exc}") from exc
