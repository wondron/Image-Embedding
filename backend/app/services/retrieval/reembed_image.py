"""Write one stored image's point again from its stored original (POST /api/images/{image_id}/reembed).

Repairs vector.status=missing - the point was deleted, the collection rebuilt, or the image never
reached the current model's collection - without the person uploading the file again:

    stored original (object storage) -> current encoder -> upsert the point with the payload
    PostgreSQL gives now (the same one the status check expects) -> ledger row of the current model

Nothing in PostgreSQL changes but the ledger, so the image keeps its annotation. A failure before
the upsert leaves everything as it was; when the image changes while this runs, the ledger keeps
the version read here and the image shows as stale (a push fixes it).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import ImageStoragePort, MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.services.retrieval.vector_status import expected_payload

logger = logging.getLogger("retrieval.reembed")


class ReembedImageUseCase:
    def __init__(
        self,
        embedding: Any,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        *,
        model: ActiveModel,
        storage: ImageStoragePort | None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._model = model
        self._storage = storage

    def execute(self, image_id: UUID | str) -> dict[str, Any]:
        """{image_id, payload_version} once the point is written.

        Raises IMAGE_NOT_FOUND, IMAGE_FILE_UNAVAILABLE (the original cannot be read),
        INDEX_BAD_IMAGE / INDEX_BAD_EMBEDDING (it cannot be embedded), INDEX_QDRANT_FAILED.
        """
        image = self._metadata.get_by_id(image_id)
        if image is None:
            raise err.image_not_found(f"image {image_id} does not exist")
        if self._storage is None:
            raise err.image_file_unavailable("no object storage is configured")
        original_url = str(image.get("original_url") or "")
        if not original_url:
            raise err.image_file_unavailable(f"image {image_id} has no original_url")
        data = self._storage.read_by_url_or_key(original_url)

        try:
            vector = self._embedding.embed_bytes(data)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        vector = validate_embedding(vector, for_search=False, expected_dim=self._model.dim)

        payload = expected_payload(image)
        try:
            self._vectors.upsert(image_id, vector, payload)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

        version = image.get("payload_version")
        self._metadata.upsert_image_embedding(
            model_id=self._model.id,
            image_id=image_id,
            synced_version=int(version) if version is not None else None,
        )
        logger.info("re-embedded image=%s model=%s payload_version=%s", image_id, self._model.space_id, version)
        return {"image_id": str(image_id), "payload_version": version}
