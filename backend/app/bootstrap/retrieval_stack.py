"""Build retrieval stack (embedder + Qdrant + PostgreSQL + S3 + use cases).

M2: MySQL removed. Collection/dim/distance from embedding_models.status='current'.
QdrantVectorStore thin-wraps app.clients.qdrant.QdrantStore (no second client).
After vector upsert success → image_embeddings.synced_version = images.payload_version.

M4: ObjectStoragePort (mock by default) + DeleteImageUseCase + DeletionWorker.
Qdrant delete scope = current collection only (multi-collection TBD).
D5/R3: QdrantStore under app.clients; FlyB removed from retrieval stack (search source=empty).
D6/R3: retrieval island — do not import app.services.predict / pipeline / flyb_search here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from app.clients.inference import EmbedderPortImpl
from app.clients.s3 import build_object_storage
from app.repositories.metadata import PgMetadataStore
from app.clients.qdrant import QdrantVectorStore
from app.core.config.settings import Settings
from app.services.retrieval.delete_image import DeleteImageUseCase, DeletionWorker
from app.services.retrieval.index_image import IndexImageUseCase
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.recompute_scenes import RecomputeScenesUseCase
from app.services.retrieval.search_image import SearchImageUseCase
from app.repositories.ports import ObjectStoragePort, VectorStorePort

logger = logging.getLogger("bootstrap.retrieval")


class EmbeddingLedgerVectorStore(VectorStorePort):
    def __init__(self, inner: QdrantVectorStore, metadata: PgMetadataStore, *, model_id: int) -> None:
        self._inner = inner
        self._metadata = metadata
        self._model_id = int(model_id)

    @property
    def wired(self) -> bool:
        return self._inner.wired

    @property
    def collection(self) -> str:
        return self._inner.collection

    def ensure_collection(self, *, recreate: bool = False) -> None:
        self._inner.ensure_collection(recreate=recreate)

    def ping(self) -> dict[str, Any]:
        return self._inner.ping()

    def set_collection(self, collection: str, *, vector_dim: int, distance: str,) -> None:
        self._inner.set_collection(collection, vector_dim=vector_dim, distance=distance)

    def upsert(self, point_id: UUID | str, vector: list[float], payload: dict[str, Any],) -> None:
        self._inner.upsert(point_id, vector, payload)
        try:
            self._metadata.upsert_image_embedding(model_id=self._model_id, image_id=point_id, synced_version=None, )
        except Exception:
            logger.exception("image_embeddings sync failed after upsert point_id=%s model_id=%s", point_id, self._model_id,)
            raise

    def search(self, vector: list[float], top_k: int = 10, *, score_threshold: float | None = None, scene: str | None = None, filters: dict[str, Any] | None = None,) -> list[dict[str, Any]]:
        return self._inner.search(vector, top_k=top_k, score_threshold=score_threshold, scene=scene, filters=filters, )

    def delete(self, point_id: UUID | str) -> None:
        self._inner.delete(point_id)


@dataclass
class RetrievalStack:
    embedding: EmbedderPortImpl
    vectors: VectorStorePort
    metadata: PgMetadataStore
    storage: ObjectStoragePort
    index_uc: IndexImageUseCase
    search_uc: SearchImageUseCase
    list_uc: ListImagesUseCase
    delete_uc: DeleteImageUseCase
    deletion_worker: DeletionWorker
    recompute_scenes_uc: RecomputeScenesUseCase
    embedding_space_id: str = ""
    current_model: dict[str, Any] | None = None


def build_retrieval_stack(
    config: dict[str, Any],
    settings: Settings,
    *,
    indexed_dir: Optional[str] = None,
) -> RetrievalStack:
    q = settings.qdrant
    pg = settings.postgres

    metadata = PgMetadataStore(
        dsn=pg.dsn(),
        host=pg.host,
        port=pg.port,
        user=pg.user,
        password=pg.password,
        database=pg.database,
    )
    metadata.ensure_schema()

    # Authority for collection/dim/distance: embedding_models.status='current'
    current = metadata.get_current_embedding_model()
    collection = str(current["collection"])
    vector_dim = int(current["vector_dim"])
    distance = str(current["distance"])
    model_id = int(current["id"])
    space_id = f"{current.get('name')}:{current.get('version')}"

    vectors_inner = QdrantVectorStore(
        url=q.url,
        host=q.host,
        port=q.port,
        collection=collection,
        vector_size=vector_dim,
        distance=distance,
        config=config,
    )
    if not vectors_inner.wired:
        raise RuntimeError("QdrantVectorStore failed to wire QdrantStore")
    vectors_inner.ensure_collection(recreate=False)

    vectors: VectorStorePort = EmbeddingLedgerVectorStore(
        vectors_inner, metadata, model_id=model_id
    )

    storage = build_object_storage(settings.s3)

    logger.info(
        "embedding_models current | id=%s collection=%s dim=%s distance=%s space_id=%s",
        model_id,
        collection,
        vector_dim,
        distance,
        space_id,
    )

    embedding = EmbedderPortImpl(config=config)
    save_dir = indexed_dir or f"{settings.paths.save_images_dir}/indexed"
    index_uc = IndexImageUseCase(embedding, vectors, metadata, storage=storage, indexed_dir=save_dir)
    search_uc = SearchImageUseCase(embedding, vectors, metadata)
    list_uc = ListImagesUseCase(metadata)
    delete_uc = DeleteImageUseCase(metadata)
    deletion_worker = DeletionWorker(metadata, vectors, storage)
    recompute_scenes_uc = RecomputeScenesUseCase(metadata)
    return RetrievalStack(
        embedding=embedding,
        vectors=vectors,
        metadata=metadata,
        storage=storage,
        index_uc=index_uc,
        search_uc=search_uc,
        list_uc=list_uc,
        delete_uc=delete_uc,
        deletion_worker=deletion_worker,
        recompute_scenes_uc=recompute_scenes_uc,
        embedding_space_id=space_id,
        current_model=current,
    )
