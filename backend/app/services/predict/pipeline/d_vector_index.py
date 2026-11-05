from __future__ import annotations

"""Vector-side helpers (embed + Qdrant). HTTP / PG owned by 后端.

SSOT: docs/image-search-pg-v01-data-contract.md
point_id must be images.id (uuid). Collection from embedding_models.current.
"""

import logging
from typing import Any, Dict, Mapping, Optional, Sequence, Union
from uuid import UUID

import numpy as np

from app.clients.inference_onnx import OnnxEmbedding
from app.clients.qdrant import (
    DEFAULT_SCORE_THRESHOLD,
    DEFAULT_VECTOR_DIM,
    QdrantStore,
)

logger = logging.getLogger("vector_index")


class VectorIndexPipeline:
    """Image -> L2 embedding -> Qdrant upsert / search."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        collection: str | None = None,
        vector_dim: int | None = None,
        distance: str | None = None,
    ):
        self.embedding = OnnxEmbedding(config)
        self.store = QdrantStore(
            config,
            collection=collection,
            vector_dim=vector_dim,
            distance=distance,
        )
        conf = dict(config.get("QDRANT_CONFIG") or {})
        self.default_top_k = int(conf.get("top_k", 10))
        raw_tau = conf.get("score_threshold", DEFAULT_SCORE_THRESHOLD)
        self.default_score_threshold = (
            float(raw_tau) if raw_tau is not None else DEFAULT_SCORE_THRESHOLD
        )

    @property
    def embedding_dim(self) -> int:
        return int(self.store.vector_dim)

    def ensure_ready(self, *, recreate: bool = False) -> None:
        self.store.ensure_collection(recreate=recreate)

    def set_collection(
        self,
        collection: str,
        *,
        vector_dim: int,
        distance: str,
    ) -> None:
        self.store.set_collection(
            collection, vector_dim=vector_dim, distance=distance
        )

    def embed(self, img_data: Union[np.ndarray, bytes]) -> np.ndarray:
        """Return L2-normalized embedding ``(dim,)``."""
        vector = self.embedding.detect(img_data)
        expected = int(self.store.vector_dim)
        if int(vector.shape[0]) != expected:
            raise ValueError(
                f"embedding dim={vector.shape[0]} != vector_dim={expected}"
            )
        return vector

    def index_image(
        self,
        img_data: Union[np.ndarray, bytes],
        *,
        point_id: UUID | str,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Embed + upsert. ``point_id`` required (== images.id uuid)."""
        if point_id is None:
            raise TypeError("point_id is required (== images.id uuid)")
        vector = self.embed(img_data)
        pid = self.store.upsert(point_id, vector, payload=payload)
        return {
            "id": pid,
            "qdrant_point_id": pid,
            "embedding_dim": int(self.store.vector_dim),
        }

    def search_image(
        self,
        img_data: Union[np.ndarray, bytes],
        *,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        scene: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Embed + top-k with optional scene filter."""
        vector = self.embed(img_data)
        k = int(top_k if top_k is not None else self.default_top_k)
        thr = (
            score_threshold
            if score_threshold is not None
            else self.default_score_threshold
        )
        hits = self.store.search(
            vector, top_k=k, score_threshold=thr, scene=scene
        )
        return {
            "embedding_dim": int(self.store.vector_dim),
            "results": hits,
        }

    def index_vectors(self, points: Sequence[Mapping[str, Any]]) -> int:
        """Batch upsert of precomputed {id, vector, payload}."""
        return self.store.upsert_batch(points)

    def delete(self, point_id: UUID | str) -> None:
        self.store.delete(point_id)
