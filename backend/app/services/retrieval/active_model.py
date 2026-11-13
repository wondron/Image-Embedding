"""The embedding model retrieval runs against (embedding_models row, status='current').

One model version = one Qdrant collection. The query/index encoder and the
collection must switch together (app.models.embedding.EmbeddingModel): the stack
pairs them by the row's fingerprint (services.retrieval.model_versions.same_encoder),
and refuses to start when the loaded encoder's output dim differs from the row.
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
    # {spec, probe} of the encoder that writes this collection (clients.inference.Embedder
    # .fingerprint); None for a version registered before fingerprints were recorded
    fingerprint: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)

    @property
    def space_id(self) -> str:
        """Human-readable embedding space id (name:version)."""
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
        """VECTOR_DIM_MISMATCH unless the encoder produces vectors for this collection."""
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
