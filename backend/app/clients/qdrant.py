"""访问一个使用命名向量 ``global`` 的 Qdrant collection。

只负责机制：连接、collection 生命周期、向量点的增删改查、带过滤的 top-k。
检索规则（向量点 id == images.id 的 uuid、可过滤的 payload 字段及其索引、分数策略、
错误码）在 app.repositories.vectors 和 app.services.retrieval 里。本模块不从 ``app``
导入任何东西。
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
    """已有的 collection 和要求的结构不一致。"""

    error_code = "VECTOR_DIM_MISMATCH"


class VectorDimMismatchError(CollectionMismatchError):
    """向量长度或 collection 的维度与 vector_dim 不同。"""


class BadEmbeddingError(ValueError):
    """向量不是一维、不是有限值，或者为零。"""

    error_code = "BAD_EMBEDDING"


@dataclass(frozen=True, slots=True)
class QdrantConnection:
    """怎样连接 Qdrant。``url=":memory:"`` 表示进程内的本地实例。"""

    url: str = DEFAULT_URL
    api_key: Optional[str] = None
    timeout: float = 30.0
    prefer_grpc: bool = True
    check_compatibility: bool = False

    @classmethod
    def from_mapping(cls, conf: Mapping[str, Any], **overrides: Any) -> "QdrantConnection":
        """从 QDRANT_CONFIG 格式的 dict 构建；非 None 的覆盖值优先。"""
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
    """Qdrant 的向量点 id 是无符号整数或 UUID（规范字符串形式）。"""
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
    """一个 collection：大小为 vector_dim、使用给定距离的命名向量 ``global``。"""

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

    def sibling(self, *, collection: str, vector_dim: int, distance: str) -> "QdrantStore":
        """基于本存储客户端的另一个 collection（多个模型版本共用一个连接）。不要关闭兄弟实例：
        关闭其中任何一个都会关闭共用的客户端。
        """
        return QdrantStore(
            self.connection, collection=collection, vector_dim=vector_dim, distance=distance, client=self.client
        )

    # -- collection ------------------------------------------------------------

    def collection_exists(self) -> bool:
        return self.collection in {c.name for c in self.client.get_collections().collections}

    def drop_collection(self) -> bool:
        """删除 collection 及其所有向量点；不存在时返回 False。"""
        if not self.collection_exists():
            return False
        self.client.delete_collection(self.collection)
        logger.info("已删除 Qdrant collection=%s", self.collection)
        return True

    def ensure_collection(
        self,
        *,
        recreate: bool = False,
        payload_indexes: Optional[Mapping[str, str]] = None,
    ) -> None:
        """collection 不存在时创建（或者删掉重建）；已存在时校验它。

        payload_indexes：{字段: "keyword" | "integer" | ...}，可重复地创建。
        """
        from qdrant_client.http import models as rest

        client = self.client
        if self.collection_exists():
            if not recreate:
                self._verify_existing()
                self._create_payload_indexes(payload_indexes)
                logger.info("Qdrant collection 已就绪：%s", self.collection)
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
            "已创建 Qdrant collection=%s | named_vector=%s | size=%d | distance=%s",
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
            except Exception as exc:  # 索引只是优化；没有它过滤照样能用
                logger.warning("create_payload_index %s（collection=%s）失败：%s", field_name, self.collection, exc)

    # -- 向量点 ----------------------------------------------------------------

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
        """每一项：{id, vector, payload?}。"""
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
        """在命名向量上做 top-k -> [{id, score, payload}]。

        score_threshold 为 None 表示没有阈值。must = {payload_key: value}，必须全部匹配
        （对 ``scenes`` 这样的列表字段，"匹配"表示"包含"）。
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

    def existing_ids(self, point_ids: Sequence[PointId]) -> set[Union[int, str]]:
        """给定 id 中是 collection 里向量点的那些（规范形式，和 _point_id 给出的一样）。"""
        if not point_ids:
            return set()
        records = self.client.retrieve(
            collection_name=self.collection,
            ids=[_point_id(p) for p in point_ids],
            with_payload=False,
            with_vectors=False,
        )
        return {_point_id(record.id) for record in records}

    def get_payloads(self, point_ids: Sequence[PointId]) -> Dict[Union[int, str], Dict[str, Any]]:
        """给定 id 中是 collection 里向量点的那些的 {id: payload}（规范 id，不含向量）。"""
        if not point_ids:
            return {}
        records = self.client.retrieve(
            collection_name=self.collection,
            ids=[_point_id(p) for p in point_ids],
            with_payload=True,
            with_vectors=False,
        )
        return {_point_id(record.id): dict(record.payload or {}) for record in records}

    def get_vectors(self, point_ids: Sequence[PointId]) -> Dict[Union[int, str], List[float]]:
        """给定 id 中是 collection 里向量点的那些的 {id: vector}（命名向量）。"""
        if not point_ids:
            return {}
        records = self.client.retrieve(
            collection_name=self.collection,
            ids=[_point_id(p) for p in point_ids],
            with_payload=False,
            with_vectors=[NAMED_VECTOR],
        )
        out: Dict[Union[int, str], List[float]] = {}
        for record in records:
            vector = record.vector.get(NAMED_VECTOR) if isinstance(record.vector, dict) else record.vector
            if vector is not None:
                out[_point_id(record.id)] = [float(v) for v in vector]
        return out

    def set_payloads(self, payloads: Mapping[PointId, Mapping[str, Any]]) -> int:
        """在一个请求里逐个向量点设置 payload 字段（其他字段和向量保持不变）。

        每个 id 都必须已经是向量点：否则 Qdrant 会拒绝整个请求，所以调用方先用 existing_ids
        过滤。
        """
        from qdrant_client.http import models as rest

        operations = [
            rest.SetPayloadOperation(set_payload=rest.SetPayload(payload=dict(payload), points=[_point_id(pid)]))
            for pid, payload in payloads.items()
        ]
        if operations:
            self.client.batch_update_points(collection_name=self.collection, update_operations=operations, wait=True)
        return len(operations)

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

    def delete_all_points(self) -> None:
        """删除所有向量点；collection、它的向量配置和 payload 索引保留。"""
        from qdrant_client.http import models as rest

        self.client.delete(
            collection_name=self.collection,
            points_selector=rest.FilterSelector(filter=rest.Filter()),
            wait=True,
        )

    def count(self) -> int:
        return int(self.client.count(collection_name=self.collection, exact=True).count)

    # -- 生命周期 ---------------------------------------------------------------

    def ping(self) -> Dict[str, Any]:
        """{ok, url, collection, points_count, vector_size, named_vector}；从不抛出。"""
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
        """关闭客户端（可重复调用）；下次调用时按需重新连接。"""
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("qdrant client close failed", exc_info=True)
