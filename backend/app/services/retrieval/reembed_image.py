"""Write one stored image's point again from its stored original (POST /api/images/{image_id}/reembed).

Repairs vector.status=missing - the point was deleted, the collection rebuilt, or the image never
reached the current model's collection - without the person uploading the file again:

    stored original (object storage) -> current encoder -> lock the image row -> upsert the point
    with the payload PostgreSQL gives now (the same one the status check expects) -> ledger row of
    the current model -> unlock

Nothing in PostgreSQL changes but the ledger, so the image keeps its annotation. A failure before
the upsert leaves everything as it was.

The slow part (reading and embedding the original) runs without a lock; the point and its ledger
row are written while the image row is held (lock_image_for_vector), as at index time. A deletion
that commits first leaves nothing to write: IMAGE_NOT_FOUND, no point. One that comes later waits,
and its cleanup then removes the point. Without the lock a deletion could be committed and cleaned
up between the embedding and the upsert, and the point would come back for an image that no
longer exists, with no queue row or ledger row left that would ever remove it.
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

        Raises IMAGE_NOT_FOUND (also when the image is deleted while it is embedded),
        IMAGE_FILE_UNAVAILABLE (the original cannot be read), INDEX_BAD_IMAGE /
        INDEX_BAD_EMBEDDING (it cannot be embedded), INDEX_QDRANT_FAILED, INDEX_POSTGRES_FAILED.
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

        # Held from here until the ledger row is committed: see the module docstring
        with self._metadata.lock_image_for_vector(image_id, model_id=self._model.id) as current:
            if current is None:
                raise err.image_not_found(f"image {image_id} was deleted while it was being embedded")
            version = current.get("payload_version")
            try:
                self._vectors.upsert(image_id, vector, expected_payload(current))
            except err.AppError:
                raise
            except Exception as exc:
                raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc
        logger.info("re-embedded image=%s model=%s payload_version=%s", image_id, self._model.space_id, version)
        return {"image_id": str(image_id), "payload_version": version}
