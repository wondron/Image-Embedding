"""Where one stored image stands in the current model's collection (GET /api/images/{image_id}).

Reads the image's point (payload only, no vector) and compares it with the payload PostgreSQL
gives now - scene_membership.build_qdrant_payload, the one index, sync and pushes write - and
reports the current model's ledger row (image_embeddings):

    status    meaning
    synced    the point exists and its payload matches PostgreSQL: search filters see the image
              as it is stored
    stale     the point exists but its payload is behind: push it (POST
              /api/images/{image_id}/vector-sync, or POST /api/scenes/sync for everything)
    missing   no point in the current collection: search cannot find the image (index it again)

The HTTP layer (app.api.services.images) answers status=unavailable when the retrieval stack is
not built or the read fails. Scenes are compared as a set: search only asks "contains".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.repositories.ports import VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.scene_membership import build_qdrant_payload


def expected_payload(image: Mapping[str, Any]) -> dict[str, Any]:
    """The payload the image's point should carry, from its stored fields and effective scenes."""
    return build_qdrant_payload(
        category_id=image.get("category_id"),
        scene_codes=list(image.get("scenes") or []),
        food_state=image.get("food_state"),
        label_status=image.get("label_status"),
    )


def payload_matches(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """True when every field of expected has the same value in actual (scenes as a set); keys
    that only actual has do not count (no filter reads them)."""
    for key, want in expected.items():
        have = actual.get(key)
        if key == "scenes":
            if sorted(str(code) for code in have or []) != sorted(str(code) for code in want or []):
                return False
        elif have != want:
            return False
    return True


class ImageVectorStatusUseCase:
    def __init__(self, vectors: VectorStorePort, *, model: ActiveModel) -> None:
        self._vectors = vectors
        self._model = model

    def statuses(self, expected: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        """{image id: synced / stale / missing} for many images with one read of the collection
        (a gallery page). expected: {image id: expected_payload(image)}.

        Raises AppError (SEARCH_QDRANT_FAILED) when the vector store cannot be read.
        """
        if not expected:
            return {}
        payloads = self._vectors.get_payloads(list(expected))
        out: dict[str, str] = {}
        for image_id, want in expected.items():
            payload = payloads.get(image_id)
            out[image_id] = "missing" if payload is None else "synced" if payload_matches(payload, want) else "stale"
        return out

    def inspect(self, image: Mapping[str, Any]) -> dict[str, Any]:
        """The vector part of the image detail; image is what ManageImagesUseCase.get returns.

        Raises AppError (SEARCH_QDRANT_FAILED) when the vector store cannot be read.
        """
        image_id = str(image["id"])
        expected = expected_payload(image)
        payload = self._vectors.get_payloads([image_id]).get(image_id)
        ledger = next(
            (row for row in image.get("embeddings") or [] if row.get("model_id") == self._model.id), None
        )
        exists = payload is not None
        matches = payload_matches(payload, expected) if exists else None
        return {
            "status": "missing" if not exists else "synced" if matches else "stale",
            "collection": self._model.collection,
            "embedding_space_id": self._model.space_id,
            "exists": exists,
            "payload": payload,
            "expected_payload": expected,
            "payload_matches": matches,
            "payload_version": image.get("payload_version"),
            "synced_version": ledger.get("synced_version") if ledger else None,
            "embedded_at": ledger.get("embedded_at") if ledger else None,
        }
