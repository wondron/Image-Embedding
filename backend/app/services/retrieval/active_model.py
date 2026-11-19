"""检索所用的 embedding 模型（embedding_models 表里 status='current' 的那一行）。

一个模型版本对应一个 Qdrant collection。查询 / 入库用的编码器和 collection 必须一起切换
（app.models.embedding.EmbeddingModel）：检索服务按这一行的指纹把它们配对
（services.retrieval.model_versions.same_encoder），加载的编码器输出维度和这一行不同时
拒绝启动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.core.enums import Distance
from app.core import errors as err


@dataclass(frozen=True, slots=True)
class ActiveModel:
    id: int
    name: str
    version: str
    collection: str
    dim: int
    distance: str
    input_size: int | None = None
    weights_uri: str | None = None
    preprocess: Mapping[str, Any] = field(default_factory=dict)
    # 写入这个 collection 的编码器的 {spec, probe}（clients.inference.Embedder
    # .fingerprint）；记录指纹之前登记的版本为 None
    fingerprint: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)

    @property
    def space_id(self) -> str:
        """便于阅读的 embedding 空间 id（name:version）。"""
        return f"{self.name}:{self.version}"

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ActiveModel":
        try:
            distance = Distance(str(row.get("distance") or Distance.COSINE).lower()).value
            return cls(
                id=int(row["id"]),
                name=str(row["name"]),
                version=str(row["version"]),
                collection=str(row["collection"]),
                dim=int(row.get("dim") or row["vector_dim"]),
                distance=distance,
                input_size=int(row["input_size"]) if row.get("input_size") is not None else None,
                weights_uri=row.get("weights_uri"),
                preprocess=dict(row.get("preprocess") or {}),
                fingerprint=dict(row["fingerprint"]) if isinstance(row.get("fingerprint"), Mapping) else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise err.not_ready(f"invalid embedding_models current row: {exc}") from exc

    def check_encoder_dim(self, encoder_dim: int) -> None:
        """编码器产生的向量不适用于这个 collection 时抛出 VECTOR_DIM_MISMATCH。"""
        if int(encoder_dim) != self.dim:
            raise err.vector_dim_mismatch(
                f"encoder output dim={encoder_dim} but embedding_models "
                f"{self.space_id} (collection={self.collection}) expects dim={self.dim}; "
                "switch EMBEDDING_CONFIG and the current model together"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "collection": self.collection,
            "dim": self.dim,
            "distance": self.distance,
            "space_id": self.space_id,
        }
