"""Qdrant access for one collection with the named vector ``global``.

Mechanism only: connection, collection lifecycle, point CRUD, filtered top-k.
Retrieval rules (point id == images.id uuid, filterable payload fields and their
indexes, score policy, error codes) live in app.repositories.vectors and
app.services.retrieval. This module imports nothing from ``app``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger("qdrant")

NAMED_VECTOR = "global"
DEFAULT_URL = "http://localhost:6333"
DISTANCES = ("cosine", "dot", "euclid")

PointId = Union[int, str, uuid.UUID]


class CollectionMismatchError(ValueError):
    """The existing collection does not have the requested shape."""

    error_code = "VECTOR_DIM_MISMATCH"


class VectorDimMismatchError(CollectionMismatchError):
    """Vector length or collection size differs from vector_dim."""


class BadEmbeddingError(ValueError):
    """Vector is not 1-D, not finite, or zero."""

    error_code = "BAD_EMBEDDING"


@dataclass(frozen=True, slots=True)
class QdrantConnection:
    """How to reach Qdrant. ``url=":memory:"`` gives an in-process local instance."""

    url: str = DEFAULT_URL
    api_key: Optional[str] = None
    timeout: float = 30.0
    prefer_grpc: bool = True
    check_compatibility: bool = False

    @classmethod
    def from_mapping(cls, conf: Mapping[str, Any], **overrides: Any) -> "QdrantConnection":
        """From a QDRANT_CONFIG-style dict; non-None overrides win."""
        url = conf.get("url")
        if not url and conf.get("host") is not None and conf.get("port") is not None:
            url = f"http://{conf['host']}:{int(conf['port'])}"
        values: Dict[str, Any] = {
            "url": str(url or DEFAULT_URL).rstrip("/"),
            "api_key": conf.get("api_key") or None,
            "timeout": float(conf.get("timeout", 30.0)),
            "prefer_grpc": bool(conf.get("prefer_grpc", True)),
            "check_compatibility": bool(conf.get("check_compatibility", False)),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def create_client(self) -> Any:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is not installed; add it to requirements.txt") from exc
        if self.url == ":memory:":
            return QdrantClient(location=":memory:")
        kwargs: Dict[str, Any] = {
            "url": self.url,
            "timeout": self.timeout,
            "prefer_grpc": self.prefer_grpc,
            "check_compatibility": self.check_compatibility,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return QdrantClient(**kwargs)


def _point_id(point_id: PointId) -> Union[int, str]:
    """Qdrant point ids are unsigned ints or UUIDs (canonical string)."""
    if isinstance(point_id, bool):
        raise TypeError("point id must be an unsigned int or uuid, got bool")
    if isinstance(point_id, int):
        if point_id < 0:
            raise TypeError(f"point id must be an unsigned int, got {point_id}")
        return point_id
    if isinstance(point_id, uuid.UUID):
        return str(point_id)
    if isinstance(point_id, str):
        return str(uuid.UUID(point_id.strip()))
    raise TypeError(f"point id must be an unsigned int or uuid, got {type(point_id).__name__}")


class QdrantStore:
    """One collection: named vector ``global`` of size vector_dim with the given distance."""

    def __init__(
        self,
        connection: Optional[QdrantConnection] = None,
        *,
        collection: str,
        vector_dim: int,
        distance: str = "cosine",
        client: Any = None,
    ) -> None:
        name = str(collection or "").strip()
        if not name:
            raise ValueError("collection is required")
        dim = int(vector_dim)
        if dim <= 0:
            raise ValueError(f"vector_dim must be > 0, got {dim}")
        dist = str(distance or "").strip().lower()
        if dist not in DISTANCES:
            raise ValueError(f"unsupported distance: {distance!r} (expected one of {DISTANCES})")
        self.connection = connection or QdrantConnection()
        self.collection = name
        self.vector_dim = dim
        self.distance = dist
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self.connection.create_client()
        return self._client

    # -- collection ------------------------------------------------------------

    def collection_exists(self) -> bool:
        return self.collection in {c.name for c in self.client.get_collections().collections}

    def ensure_collection(
        self,
        *,
        recreate: bool = False,
        payload_indexes: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Create the collection if missing (or drop + create); verify an existing one.

        payload_indexes: {field: "keyword" | "integer" | ...} created idempotently.
        """
        from qdrant_client.http import models as rest

        client = self.client
        if self.collection_exists():
            if not recreate:
                self._verify_existing()
                self._create_payload_indexes(payload_indexes)
                logger.info("qdrant collection ready: %s", self.collection)
                return
            client.delete_collection(self.collection)

        client.create_collection(
            collection_name=self.collection,
            vectors_config={
                NAMED_VECTOR: rest.VectorParams(size=self.vector_dim, distance=self._distance_enum())
            },
        )
        self._create_payload_indexes(payload_indexes)
        logger.info(
            "created qdrant collection=%s named_vector=%s size=%d distance=%s",
            self.collection,
            NAMED_VECTOR,
            self.vector_dim,
            self.distance,
        )

    def _distance_enum(self) -> Any:
        from qdrant_client.http import models as rest

        return {"cosine": rest.Distance.COSINE, "dot": rest.Distance.DOT, "euclid": rest.Distance.EUCLID}[
            self.distance
        ]

    def _verify_existing(self) -> None:
        info = self.client.get_collection(self.collection)
        vectors = getattr(getattr(info.config, "params", None), "vectors", None)
        params = vectors.get(NAMED_VECTOR) if isinstance(vectors, dict) else None
        if params is None:
            raise CollectionMismatchError(
                f"collection {self.collection!r} has no named vector {NAMED_VECTOR!r}"
            )
        if int(params.size) != self.vector_dim:
            raise VectorDimMismatchError(
                f"collection {self.collection!r} size={params.size}, expected vector_dim={self.vector_dim}"
            )
        actual = str(getattr(params.distance, "value", params.distance)).lower()
        if actual != self.distance:
            raise CollectionMismatchError(
                f"collection {self.collection!r} distance={actual}, expected {self.distance}"
            )

    def _create_payload_indexes(self, payload_indexes: Optional[Mapping[str, str]]) -> None:
        if not payload_indexes:
            return
        from qdrant_client.http import models as rest

        for field_name, schema in payload_indexes.items():
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=rest.PayloadSchemaType(str(schema).lower()),
                    wait=True,
                )
            except Exception as exc:  # index is an optimization; filtering still works
                logger.warning("create_payload_index %s on %s failed: %s", field_name, self.collection, exc)

    # -- points ----------------------------------------------------------------

    def _as_vector(self, vector: Union[np.ndarray, Sequence[float]]) -> List[float]:
        arr = np.asarray(vector, dtype=np.float32)
        if arr.ndim != 1:
            raise BadEmbeddingError(f"vector must be 1-D, got shape {arr.shape}")
        if int(arr.shape[0]) != self.vector_dim:
            raise VectorDimMismatchError(f"vector dim={arr.shape[0]} != vector_dim={self.vector_dim}")
        if not np.isfinite(arr).all():
            raise BadEmbeddingError("vector contains NaN or Inf")
        if float(np.linalg.norm(arr)) <= 1e-12:
            raise BadEmbeddingError("vector is zero")
        return arr.tolist()

    def upsert(
        self,
        point_id: PointId,
        vector: Union[np.ndarray, Sequence[float]],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Union[int, str]:
        from qdrant_client.http import models as rest

        pid = _point_id(point_id)
        point = rest.PointStruct(id=pid, vector={NAMED_VECTOR: self._as_vector(vector)}, payload=dict(payload or {}))
        self.client.upsert(collection_name=self.collection, points=[point], wait=True)
        return pid

    def upsert_batch(self, points: Iterable[Mapping[str, Any]]) -> int:
        """Each item: {id, vector, payload?}."""
        from qdrant_client.http import models as rest

        structs = [
            rest.PointStruct(
                id=_point_id(item["id"]),
                vector={NAMED_VECTOR: self._as_vector(item["vector"])},
                payload=dict(item.get("payload") or {}),
            )
            for item in points
        ]
        if structs:
            self.client.upsert(collection_name=self.collection, points=structs, wait=True)
        return len(structs)

    def search(
        self,
        vector: Union[np.ndarray, Sequence[float]],
        top_k: int = 10,
        *,
        score_threshold: Optional[float] = None,
        must: Optional[Mapping[str, Any]] = None,
        with_payload: bool = True,
    ) -> List[Dict[str, Any]]:
        """Top-k on the named vector -> [{id, score, payload}].

        score_threshold None = no threshold. must = {payload_key: value}, all must
        match (for list fields such as ``scenes`` "match" means "contains").
        """
        if int(top_k) <= 0:
            raise ValueError("top_k must be > 0")
        from qdrant_client.http import models as rest

        query_filter = None
        if must:
            query_filter = rest.Filter(
                must=[rest.FieldCondition(key=k, match=rest.MatchValue(value=v)) for k, v in must.items()]
            )
        response = self.client.query_points(
            collection_name=self.collection,
            query=self._as_vector(vector),
            using=NAMED_VECTOR,
            query_filter=query_filter,
            limit=int(top_k),
            score_threshold=score_threshold,
            with_payload=with_payload,
        )
        return [
            {"id": str(p.id), "score": float(p.score), "payload": dict(p.payload or {})}
            for p in response.points
        ]

    def delete(self, point_id: PointId) -> None:
        self.delete_batch([point_id])

    def delete_batch(self, point_ids: Sequence[PointId]) -> None:
        from qdrant_client.http import models as rest

        if not point_ids:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=rest.PointIdsList(points=[_point_id(p) for p in point_ids]),
            wait=True,
        )

    def count(self) -> int:
        return int(self.client.count(collection_name=self.collection, exact=True).count)

    # -- lifecycle ---------------------------------------------------------------

    def ping(self) -> Dict[str, Any]:
        """{ok, url, collection, points_count, vector_size, named_vector}; never raises."""
        try:
            points_count = None
            if self.collection_exists():
                points_count = getattr(self.client.get_collection(self.collection), "points_count", None)
            return {
                "ok": True,
                "url": self.connection.url,
                "collection": self.collection,
                "points_count": points_count,
                "vector_size": self.vector_dim,
                "named_vector": NAMED_VECTOR,
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "url": self.connection.url,
                "collection": self.collection,
            }

    def close(self) -> None:
        """Close the client (idempotent); the next call reconnects lazily."""
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("qdrant client close failed", exc_info=True)
