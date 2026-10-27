from __future__ import annotations

"""Qdrant vector store — SSOT: docs/image-search-mvp-data-contract.md

- collection: image_embeddings
- distance: Cosine
- VECTOR_DIM: 1024 (cls_emb, L2-normalized)
- point id: unsigned int == MySQL images.id (no path / UUID)
- payload: empty by default; cards join MySQL (optional image_id only)
"""

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger("qdrant_store")

VECTOR_DIM = 1024
DEFAULT_DISTANCE = "Cosine"
DEFAULT_URL = "http://localhost:6333"
DEFAULT_COLLECTION = "image_embeddings"
# Back-compat alias used by settings / older config keys
DEFAULT_VECTOR_SIZE = VECTOR_DIM


class VectorDimMismatchError(ValueError):
    """Raised when embedding / collection dim != VECTOR_DIM (contract VECTOR_DIM_MISMATCH)."""

    error_code = "VECTOR_DIM_MISMATCH"


class BadEmbeddingError(ValueError):
    """Illegal embedding (dim / NaN / zero) — INDEX_BAD_EMBEDDING / SEARCH_BAD_EMBEDDING."""

    error_code = "BAD_EMBEDDING"


def _resolve_url(conf: Mapping[str, Any]) -> str:
    url = conf.get("url")
    if url:
        return str(url).rstrip("/")
    host = conf.get("host")
    port = conf.get("port")
    if host is not None and port is not None:
        return f"http://{host}:{int(port)}"
    return DEFAULT_URL


def _resolve_vector_dim(conf: Mapping[str, Any]) -> int:
    # Contract prefers vector_dim; settings still use vector_size.
    raw = conf.get("vector_dim", conf.get("vector_size", VECTOR_DIM))
    dim = int(raw)
    if dim != VECTOR_DIM:
        raise VectorDimMismatchError(
            f"QDRANT_CONFIG vector dim={dim} != locked VECTOR_DIM={VECTOR_DIM}"
        )
    return dim


def _normalize_point_id(point_id: Union[str, int]) -> int:
    """Point id must equal MySQL images.id (unsigned integer)."""
    if isinstance(point_id, bool):
        raise TypeError("point id must be an unsigned integer, not bool")
    if isinstance(point_id, int):
        value = point_id
    elif isinstance(point_id, str) and point_id.isdigit():
        value = int(point_id)
    else:
        raise TypeError(
            f"point id must be unsigned int (== MySQL images.id), got {point_id!r}"
        )
    if value < 0:
        raise ValueError(f"point id must be >= 0, got {value}")
    return value


class QdrantStore:
    """Thin Qdrant client: ensure collection, upsert, cosine top-k search."""

    def __init__(self, config: Mapping[str, Any]):
        self.load_config(config)
        self._client = None

    def load_config(self, config: Mapping[str, Any]) -> None:
        conf = dict(config.get("QDRANT_CONFIG") or {})
        self.url = _resolve_url(conf)
        self.api_key = conf.get("api_key") or None
        self.collection = str(conf.get("collection") or DEFAULT_COLLECTION)
        self.vector_size = _resolve_vector_dim(conf)
        self.vector_dim = self.vector_size
        distance = str(conf.get("distance") or DEFAULT_DISTANCE)
        if distance.lower() not in {"cosine", "euclid", "dot"}:
            raise ValueError(f"unsupported QDRANT_CONFIG.distance: {distance}")
        self.distance = distance
        self.timeout = float(conf.get("timeout", 30.0))
        # Local docker REST on :6333 may 502 via httpx; gRPC :6334 is reliable.
        if "prefer_grpc" in conf:
            self.prefer_grpc = bool(conf.get("prefer_grpc"))
        else:
            self.prefer_grpc = True
        self.check_compatibility = bool(conf.get("check_compatibility", False))

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is not installed; add it to requirements.txt"
            ) from exc

        kwargs: Dict[str, Any] = {
            "url": self.url,
            "timeout": self.timeout,
            "prefer_grpc": self.prefer_grpc,
            "check_compatibility": self.check_compatibility,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        self._client = QdrantClient(**kwargs)
        return self._client

    def _distance_enum(self):
        from qdrant_client.http import models as rest

        mapping = {
            "cosine": rest.Distance.COSINE,
            "euclid": rest.Distance.EUCLID,
            "dot": rest.Distance.DOT,
        }
        return mapping[self.distance.lower()]

    def ensure_collection(self, *, recreate: bool = False) -> None:
        """Create collection if missing; reject dim mismatch (VECTOR_DIM_MISMATCH)."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        names = {c.name for c in client.get_collections().collections}
        if self.collection in names:
            if not recreate:
                info = client.get_collection(self.collection)
                vectors = getattr(getattr(info.config, "params", None), "vectors", None)
                actual = getattr(vectors, "size", None) if vectors is not None else None
                if actual is not None and int(actual) != self.vector_size:
                    raise VectorDimMismatchError(
                        f"collection {self.collection!r} size={actual}, "
                        f"expected VECTOR_DIM={self.vector_size}"
                    )
                logger.info("qdrant collection ready: %s", self.collection)
                return
            client.delete_collection(self.collection)

        client.create_collection(
            collection_name=self.collection,
            vectors_config=rest.VectorParams(
                size=self.vector_size,
                distance=self._distance_enum(),
            ),
        )
        logger.info(
            "created qdrant collection=%s size=%d distance=%s",
            self.collection,
            self.vector_size,
            self.distance,
        )

    def _as_vector(self, vector: Union[np.ndarray, Sequence[float]]) -> List[float]:
        arr = np.asarray(vector, dtype=np.float32)
        if arr.ndim != 1:
            raise BadEmbeddingError(f"vector must be 1-D, got shape {arr.shape}")
        if not np.isfinite(arr).all():
            raise BadEmbeddingError("vector contains NaN or Inf")
        norm = float(np.linalg.norm(arr))
        if norm <= 1e-12:
            raise BadEmbeddingError("vector is zero")
        if int(arr.shape[0]) != self.vector_size:
            raise VectorDimMismatchError(
                f"vector dim={arr.shape[0]} != VECTOR_DIM={self.vector_size}"
            )
        return arr.astype(np.float32).tolist()

    @staticmethod
    def _normalize_payload(
        payload: Optional[Mapping[str, Any]],
        point_id: int,
    ) -> Optional[Dict[str, Any]]:
        """MVP: Qdrant holds neighbors only; optional redundant image_id allowed."""
        if not payload:
            return {"image_id": point_id}
        out = dict(payload)
        # Drop presentation fields that belong in MySQL only.
        for key in (
            "path",
            "url",
            "title",
            "category",
            "tags",
            "width",
            "height",
            "created_at",
            "updated_at",
            "status",
        ):
            out.pop(key, None)
        out["image_id"] = int(out.get("image_id", point_id))
        if out["image_id"] != point_id:
            raise ValueError(
                f"payload.image_id={out['image_id']} must equal point id={point_id}"
            )
        return out

    def upsert(
        self,
        point_id: Union[str, int],
        vector: Union[np.ndarray, Sequence[float]],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Upsert one point. point_id == MySQL images.id (unsigned int)."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        pid = _normalize_point_id(point_id)
        vec = self._as_vector(vector)
        point = rest.PointStruct(
            id=pid,
            vector=vec,
            payload=self._normalize_payload(payload, pid),
        )
        client.upsert(collection_name=self.collection, points=[point], wait=True)
        return pid

    def upsert_batch(
        self,
        points: Sequence[Mapping[str, Any]],
    ) -> int:
        """Batch upsert. Each item: {id, vector, payload?} with integer id."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        structs = []
        for item in points:
            pid = _normalize_point_id(item["id"])
            vec = self._as_vector(item["vector"])
            structs.append(
                rest.PointStruct(
                    id=pid,
                    vector=vec,
                    payload=self._normalize_payload(item.get("payload"), pid),
                )
            )
        if structs:
            client.upsert(collection_name=self.collection, points=structs, wait=True)
        return len(structs)

    def search(
        self,
        vector: Union[np.ndarray, Sequence[float]],
        top_k: int = 10,
        filters: Optional[Mapping[str, Any]] = None,
        *,
        score_threshold: Optional[float] = None,
        with_payload: bool = True,
    ) -> List[Dict[str, Any]]:
        """Cosine top-k. Returns [{id, score, payload}, ...].

        If score_threshold is set, points with score strictly below it are dropped
        (Qdrant keeps score >= threshold). ``filters`` reserved; unused in MVP.
        """
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        if top_k > 50:
            raise ValueError("top_k must be <= 50 (API contract)")
        if filters:
            logger.debug("qdrant filters unused in MVP: %s", filters)
        client = self._get_client()
        vec = self._as_vector(vector)
        # qdrant-client >=1.12 removed Client.search; use query_points.
        response = client.query_points(
            collection_name=self.collection,
            query=vec,
            limit=int(top_k),
            score_threshold=score_threshold,
            with_payload=with_payload,
        )
        hits = getattr(response, "points", None)
        if hits is None:
            hits = list(response)
        else:
            hits = list(hits)
        results: List[Dict[str, Any]] = []
        for hit in hits:
            results.append(
                {
                    "id": int(hit.id) if hit.id is not None else hit.id,
                    "score": float(hit.score),
                    "payload": dict(hit.payload) if hit.payload else {},
                }
            )
        return results

    def count(self) -> int:
        client = self._get_client()
        result = client.count(collection_name=self.collection, exact=True)
        return int(getattr(result, "count", 0))