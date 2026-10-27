from __future__ import annotations

"""Vector-side helpers (embed + Qdrant). HTTP / MySQL owned by 后端.

SSOT: docs/image-search-mvp-data-contract.md
point_id must be MySQL images.id (unsigned int).
"""

import logging
from typing import Any, Dict, Mapping, Optional, Sequence, Union

import numpy as np

from app.domain.common.embedding import OnnxEmbedding
from app.domain.common.qdrant_store import VECTOR_DIM, QdrantStore

logger = logging.getLogger("vector_index")


class VectorIndexPipeline:
    """Image -> L2 embedding -> Qdrant upsert / search."""

    def __init__(self, config: Mapping[str, Any]):
        self.embedding = OnnxEmbedding(config)
        self.store = QdrantStore(config)
        conf = dict(config.get("QDRANT_CONFIG") or {})
        self.default_top_k = int(conf.get("top_k", 10))
        raw_tau = conf.get("score_threshold")
        self.default_score_threshold = (
            float(raw_tau) if raw_tau is not None else None
        )

    @property
    def embedding_dim(self) -> int:
        return int(self.store.vector_dim)

    def ensure_ready(self, *, recreate: bool = False) -> None:
        self.store.ensure_collection(recreate=recreate)

    def embed(self, img_data: Union[np.ndarray, bytes]) -> np.ndarray:
        """Return L2-normalized embedding ``(VECTOR_DIM,)``."""
        vector = self.embedding.detect(img_data)
        if int(vector.shape[0]) != VECTOR_DIM:
            raise ValueError(
                f"embedding dim={vector.shape[0]} != VECTOR_DIM={VECTOR_DIM}"
            )
        return vector

    def index_image(
        self,
        img_data: Union[np.ndarray, bytes],
        *,
        point_id: int,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Embed + upsert. ``point_id`` required (== MySQL images.id)."""
        if point_id is None:
            raise TypeError("point_id is required (== MySQL images.id)")
        vector = self.embed(img_data)
        pid = self.store.upsert(point_id, vector, payload=payload)
        return {"id": pid, "qdrant_point_id": pid, "embedding_dim": VECTOR_DIM}

    def search_image(
        self,
        img_data: Union[np.ndarray, bytes],
        *,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Embed + cosine top-k. Returns {embedding_dim, results}."""
        vector = self.embed(img_data)
        k = int(top_k if top_k is not None else self.default_top_k)
        thr = (
            score_threshold
            if score_threshold is not None
            else self.default_score_threshold
        )
        hits = self.store.search(vector, top_k=k, score_threshold=thr)
        return {
            "embedding_dim": VECTOR_DIM,
            "results": hits,
        }

    def index_vectors(self, points: Sequence[Mapping[str, Any]]) -> int:
        """Batch upsert of precomputed {id, vector, payload?}."""
        return self.store.upsert_batch(points)