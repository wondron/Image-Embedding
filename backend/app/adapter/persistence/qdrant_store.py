from __future__ import annotations

"""Qdrant vector store (adapter persistence) — SSOT: docs/image-search-pg-v01-data-contract.md

Moved from app.domain.common.qdrant_store (DDD D5). Public class name and method
signatures unchanged for algo (VectorIndexPipeline / scripts).

- one collection per embedding_models row (name from registry; not hard-coded)
- named vector: global
- dim/distance from embedding_models current row
- point id: uuid == images.id (first 16 bytes of content SHA-256)
- payload: category_id / scenes(code[]) / food_state / label_status + indexes
- score_threshold: MVP defaults (0.55; explicit 0 disables)

Also hosts QdrantVectorStore (VectorStorePort) — thin wrap of QdrantStore, no second client.
"""

import logging
import uuid
from uuid import UUID
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger("qdrant_store")

NAMED_VECTOR = "global"
DEFAULT_URL = "http://localhost:6333"
DEFAULT_DISTANCE = "cosine"
# Food-finetuned ONNX default; overridden by embedding_models.dim when wired.
DEFAULT_VECTOR_DIM = 1024
DEFAULT_SCORE_THRESHOLD = 0.55


class VectorDimMismatchError(ValueError):
    """Raised when embedding / collection dim mismatch (VECTOR_DIM_MISMATCH)."""

    error_code = "VECTOR_DIM_MISMATCH"


class BadEmbeddingError(ValueError):
    """Illegal embedding (dim / NaN / zero)."""

    error_code = "BAD_EMBEDDING"


class BadPayloadError(ValueError):
    """Missing or illegal Qdrant payload fields."""

    error_code = "BAD_PAYLOAD"


# Back-compat aliases used by older imports / settings
VECTOR_DIM = DEFAULT_VECTOR_DIM
DEFAULT_COLLECTION = ""  # no longer hard-coded; must come from embedding_models
DEFAULT_VECTOR_SIZE = DEFAULT_VECTOR_DIM


def _resolve_url(conf: Mapping[str, Any]) -> str:
    url = conf.get("url")
    if url:
        return str(url).rstrip("/")
    host = conf.get("host")
    port = conf.get("port")
    if host is not None and port is not None:
        return f"http://{host}:{int(port)}"
    return DEFAULT_URL


def _normalize_point_id(point_id: Union[str, uuid.UUID]) -> str:
    """Point id must equal images.id (uuid). Returns canonical str form for Qdrant."""
    if isinstance(point_id, uuid.UUID):
        return str(point_id)
    if isinstance(point_id, str):
        text = point_id.strip()
        if not text:
            raise TypeError("point id uuid string is empty")
        return str(uuid.UUID(text))
    if isinstance(point_id, bool) or isinstance(point_id, int):
        raise TypeError(
            f"point id must be uuid (== images.id), got int/bool {point_id!r} "
            "(PG v0.1 no longer uses BIGINT MySQL ids)"
        )
    raise TypeError(f"point id must be uuid, got {type(point_id).__name__}: {point_id!r}")


def _normalize_payload(payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Require PG v0.1 filter payload; drop presentation-only keys."""
    if payload is None:
        raise BadPayloadError(
            "payload required: category_id, scenes, food_state, label_status"
        )
    raw = dict(payload)
    for key in (
        "path",
        "url",
        "original_url",
        "thumb_url",
        "title",
        "tags",
        "width",
        "height",
        "created_at",
        "updated_at",
        "status",
        "image_id",
        "sha256",
    ):
        raw.pop(key, None)

    if "category_id" not in raw:
        raise BadPayloadError("payload.category_id is required (int|null)")
    category_id = raw.get("category_id")
    if category_id is not None:
        category_id = int(category_id)

    if "scenes" not in raw:
        raise BadPayloadError("payload.scenes is required (list[str] codes)")
    scenes = raw.get("scenes")
    if scenes is None:
        scenes = []
    if not isinstance(scenes, (list, tuple)):
        raise BadPayloadError("payload.scenes must be a list of scene codes")
    scenes_out = [str(s) for s in scenes]

    if "food_state" not in raw:
        raise BadPayloadError("payload.food_state is required (str|null)")
    food_state = raw.get("food_state")
    if food_state is not None:
        food_state = str(food_state)

    if "label_status" not in raw:
        raise BadPayloadError("payload.label_status is required (str)")
    label_status = raw.get("label_status")
    if label_status is None or str(label_status).strip() == "":
        raise BadPayloadError("payload.label_status must be a non-empty string")
    label_status = str(label_status)

    return {
        "category_id": category_id,
        "scenes": scenes_out,
        "food_state": food_state,
        "label_status": label_status,
    }


class QdrantStore:
    """Thin Qdrant client: ensure collection, upsert, search, delete.

    collection / vector_dim / distance come from embedding_models (current row).
    Named vector is always ``global``.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        collection: str | None = None,
        vector_dim: int | None = None,
        distance: str | None = None,
    ):
        self._client = None
        self.load_config(
            config or {},
            collection=collection,
            vector_dim=vector_dim,
            distance=distance,
        )

    def load_config(
        self,
        config: Mapping[str, Any],
        *,
        collection: str | None = None,
        vector_dim: int | None = None,
        distance: str | None = None,
    ) -> None:
        conf = dict(config.get("QDRANT_CONFIG") or {})
        self.url = _resolve_url(conf)
        self.api_key = conf.get("api_key") or None

        coll = collection or conf.get("collection") or ""
        self.collection = str(coll).strip()
        if not self.collection:
            raise ValueError(
                "collection is required (= embedding_models.collection for current model)"
            )

        raw_dim = (
            vector_dim
            if vector_dim is not None
            else conf.get("vector_dim", conf.get("vector_size", DEFAULT_VECTOR_DIM))
        )
        self.vector_size = int(raw_dim)
        self.vector_dim = self.vector_size
        if self.vector_size <= 0:
            raise ValueError(f"vector_dim must be > 0, got {self.vector_size}")

        dist = distance or conf.get("distance") or DEFAULT_DISTANCE
        dist_l = str(dist).lower()
        if dist_l not in {"cosine", "euclid", "dot"}:
            raise ValueError(f"unsupported distance: {dist}")
        self.distance = dist_l

        self.timeout = float(conf.get("timeout", 30.0))
        if "prefer_grpc" in conf:
            self.prefer_grpc = bool(conf.get("prefer_grpc"))
        else:
            self.prefer_grpc = True
        self.check_compatibility = bool(conf.get("check_compatibility", False))

        raw_tau = conf.get("score_threshold", DEFAULT_SCORE_THRESHOLD)
        self.default_score_threshold = (
            float(raw_tau) if raw_tau is not None else DEFAULT_SCORE_THRESHOLD
        )

    def set_collection(
        self,
        collection: str,
        *,
        vector_dim: int,
        distance: str,
    ) -> None:
        """Switch to another embedding_models row (e.g. new current)."""
        name = str(collection or "").strip()
        if not name:
            raise ValueError("collection is required")
        dim = int(vector_dim)
        if dim <= 0:
            raise ValueError(f"vector_dim must be > 0, got {dim}")
        dist_l = str(distance).lower()
        if dist_l not in {"cosine", "euclid", "dot"}:
            raise ValueError(f"unsupported distance: {distance}")
        self.collection = name
        self.vector_size = dim
        self.vector_dim = dim
        self.distance = dist_l

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
        return mapping[self.distance]

    def _create_payload_indexes(self, client) -> None:
        from qdrant_client.http import models as rest

        specs = (
            ("scenes", rest.PayloadSchemaType.KEYWORD),
            ("label_status", rest.PayloadSchemaType.KEYWORD),
            ("category_id", rest.PayloadSchemaType.INTEGER),
        )
        for field_name, schema in specs:
            try:
                client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=schema,
                    wait=True,
                )
            except Exception as exc:
                logger.debug(
                    "create_payload_index %s on %s: %s",
                    field_name,
                    self.collection,
                    exc,
                )

    def ensure_collection(self, *, recreate: bool = False) -> None:
        """Create collection if missing; named vector ``global`` + payload indexes.

        recreate=True deletes then recreates (empty-library rebuild).
        """
        from qdrant_client.http import models as rest

        client = self._get_client()
        names = {c.name for c in client.get_collections().collections}
        if self.collection in names:
            if not recreate:
                info = client.get_collection(self.collection)
                vectors = getattr(getattr(info.config, "params", None), "vectors", None)
                actual = None
                if isinstance(vectors, dict):
                    params = vectors.get(NAMED_VECTOR) or next(iter(vectors.values()), None)
                    actual = getattr(params, "size", None) if params is not None else None
                else:
                    actual = getattr(vectors, "size", None) if vectors is not None else None
                if actual is not None and int(actual) != self.vector_size:
                    raise VectorDimMismatchError(
                        f"collection {self.collection!r} size={actual}, "
                        f"expected vector_dim={self.vector_size}"
                    )
                self._create_payload_indexes(client)
                logger.info("qdrant collection ready: %s", self.collection)
                return
            client.delete_collection(self.collection)

        client.create_collection(
            collection_name=self.collection,
            vectors_config={
                NAMED_VECTOR: rest.VectorParams(
                    size=self.vector_size,
                    distance=self._distance_enum(),
                )
            },
        )
        self._create_payload_indexes(client)
        logger.info(
            "created qdrant collection=%s named_vector=%s size=%d distance=%s",
            self.collection,
            NAMED_VECTOR,
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
                f"vector dim={arr.shape[0]} != vector_dim={self.vector_size}"
            )
        return arr.astype(np.float32).tolist()

    def upsert(
        self,
        point_id: Union[str, uuid.UUID],
        vector: Union[np.ndarray, Sequence[float]],
        payload: Mapping[str, Any],
    ) -> str:
        """Upsert one point. point_id == images.id (uuid). Returns uuid str."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        pid = _normalize_point_id(point_id)
        vec = self._as_vector(vector)
        body = _normalize_payload(payload)
        point = rest.PointStruct(
            id=pid,
            vector={NAMED_VECTOR: vec},
            payload=body,
        )
        client.upsert(collection_name=self.collection, points=[point], wait=True)
        return pid

    def upsert_batch(self, points: Sequence[Mapping[str, Any]]) -> int:
        """Batch upsert. Each item: {id, vector, payload} with uuid id."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        structs = []
        for item in points:
            pid = _normalize_point_id(item["id"])
            vec = self._as_vector(item["vector"])
            body = _normalize_payload(item.get("payload"))
            structs.append(
                rest.PointStruct(
                    id=pid,
                    vector={NAMED_VECTOR: vec},
                    payload=body,
                )
            )
        if structs:
            client.upsert(collection_name=self.collection, points=structs, wait=True)
        return len(structs)

    def _build_query_filter(
        self,
        *,
        scene: Optional[str],
        filters: Optional[Mapping[str, Any]],
    ):
        from qdrant_client.http import models as rest

        must: List[Any] = []
        scene_code = scene
        if scene_code is None and filters:
            raw = filters.get("scenes") or filters.get("scene")
            if isinstance(raw, (list, tuple)) and raw:
                scene_code = str(raw[0])
            elif raw is not None:
                scene_code = str(raw)
        if scene_code:
            must.append(
                rest.FieldCondition(
                    key="scenes",
                    match=rest.MatchValue(value=str(scene_code)),
                )
            )
        if filters:
            if "label_status" in filters and filters["label_status"] is not None:
                must.append(
                    rest.FieldCondition(
                        key="label_status",
                        match=rest.MatchValue(value=str(filters["label_status"])),
                    )
                )
            if "category_id" in filters and filters["category_id"] is not None:
                must.append(
                    rest.FieldCondition(
                        key="category_id",
                        match=rest.MatchValue(value=int(filters["category_id"])),
                    )
                )
        if not must:
            return None
        return rest.Filter(must=must)

    def search(
        self,
        vector: Union[np.ndarray, Sequence[float]],
        top_k: int = 10,
        filters: Optional[Mapping[str, Any]] = None,
        *,
        score_threshold: Optional[float] = None,
        scene: Optional[str] = None,
        with_payload: bool = True,
    ) -> List[Dict[str, Any]]:
        """Top-k on named vector ``global``.

        Returns [{id, score, payload}, ...].
        - scene / filters.scenes: payload.scenes contains the code
        - score_threshold None → default 0.55; explicit 0 → no rejection
        """
        if top_k <= 0:
            raise ValueError("top_k must be > 0")

        client = self._get_client()
        vec = self._as_vector(vector)
        query_filter = self._build_query_filter(scene=scene, filters=filters)

        if score_threshold is None:
            thr: Optional[float] = float(self.default_score_threshold)
        else:
            thr = float(score_threshold)
        native_thr = None if thr == 0.0 else thr

        try:
            response = client.query_points(
                collection_name=self.collection,
                query=vec,
                using=NAMED_VECTOR,
                query_filter=query_filter,
                limit=int(top_k),
                score_threshold=native_thr,
                with_payload=with_payload,
            )
            hits = getattr(response, "points", None)
            if hits is None:
                hits = response if isinstance(response, list) else []
        except TypeError:
            hits = client.search(
                collection_name=self.collection,
                query_vector=(NAMED_VECTOR, vec),
                query_filter=query_filter,
                limit=int(top_k),
                score_threshold=native_thr,
                with_payload=with_payload,
            )

        out: List[Dict[str, Any]] = []
        for hit in hits or []:
            score = float(getattr(hit, "score", 0.0))
            if native_thr is not None and score < native_thr:
                continue
            pid = getattr(hit, "id", None)
            payload = getattr(hit, "payload", None) or {}
            out.append({"id": str(pid), "score": score, "payload": dict(payload)})
        return out

    def delete(self, point_id: Union[str, uuid.UUID]) -> None:
        """Delete one point by images.id uuid."""
        from qdrant_client.http import models as rest

        client = self._get_client()
        pid = _normalize_point_id(point_id)
        client.delete(
            collection_name=self.collection,
            points_selector=rest.PointIdsList(points=[pid]),
            wait=True,
        )

    def delete_batch(self, point_ids: Sequence[Union[str, uuid.UUID]]) -> None:
        """Delete many points by uuid."""
        from qdrant_client.http import models as rest

        if not point_ids:
            return
        client = self._get_client()
        ids = [_normalize_point_id(p) for p in point_ids]
        client.delete(
            collection_name=self.collection,
            points_selector=rest.PointIdsList(points=ids),
            wait=True,
        )


# ---------------------------------------------------------------------------
# VectorStorePort adapter (retrieval stack) — wraps QdrantStore above
# ---------------------------------------------------------------------------

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import VECTOR_DIM, VectorStorePort

_adapter_logger = logging.getLogger("qdrant_adapter")


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

        # Prefer gRPC for local REST :6333 (httpx often 502).
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
            self._impl = QdrantStore(
                wrapped,
                collection=collection,
                vector_dim=self.vector_size,
                distance=distance,
            )
        except Exception:
            _adapter_logger.exception("failed to construct QdrantStore")
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
                "detail": "QdrantStore unavailable",
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
