"""Composition root for retrieval (图搜图): builds and wires objects, nothing else.

    build_retrieval_infra   PG -> active embedding model -> Qdrant collection -> S3
                            (no ONNX; enough for workers)
    build_retrieval_stack   infra + encoder (dim checked against the model) + use cases
                            (API process)

Business rules live in app.services.retrieval; adapters in app.repositories /
app.clients. Both builders release what they opened if a later step fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.clients.inference import EmbedderPortImpl
from app.clients.qdrant import QdrantVectorStore
from app.clients.s3 import build_object_storage
from app.core.config.settings import Settings
from app.repositories.metadata import PgMetadataStore
from app.repositories.ports import ObjectStoragePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.delete_image import DeleteImageUseCase, DeletionWorker
from app.services.retrieval.index_image import IndexImageUseCase
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.recompute_scenes import RecomputeScenesUseCase
from app.services.retrieval.search_image import SearchImageUseCase

logger = logging.getLogger("bootstrap.retrieval")


@dataclass
class RetrievalInfra:
    """Connections + the active model. Owns their lifecycle."""

    metadata: PgMetadataStore
    vectors: QdrantVectorStore
    storage: ObjectStoragePort
    model: ActiveModel

    def ping(self) -> dict[str, dict[str, Any]]:
        """Dependency status; never raises."""
        return {
            "postgres": self.metadata.ping(),
            "qdrant": self.vectors.ping(),
            "storage": self.storage.ping(),
        }

    def close(self) -> None:
        for name, closer in (("qdrant", self.vectors.close), ("postgres", self.metadata.close)):
            try:
                closer()
            except Exception:
                logger.exception("closing %s failed", name)


@dataclass
class RetrievalStack:
    """Everything the retrieval HTTP layer calls."""

    infra: RetrievalInfra
    embedding: EmbedderPortImpl
    index_uc: IndexImageUseCase
    search_uc: SearchImageUseCase
    list_uc: ListImagesUseCase
    delete_uc: DeleteImageUseCase
    deletion_worker: DeletionWorker
    recompute_scenes_uc: RecomputeScenesUseCase

    @property
    def model(self) -> ActiveModel:
        return self.infra.model

    def close(self) -> None:
        self.infra.close()


def build_retrieval_infra(settings: Settings, config: dict[str, Any] | None = None) -> RetrievalInfra:
    """PG schema check -> current embedding model -> its Qdrant collection -> object storage."""
    pg = settings.postgres
    metadata = PgMetadataStore(
        dsn=pg.dsn(),
        host=pg.host,
        port=pg.port,
        user=pg.user,
        password=pg.password,
        database=pg.database,
    )
    vectors: QdrantVectorStore | None = None
    try:
        metadata.ensure_schema()
        model = ActiveModel.from_row(metadata.get_current_embedding_model())
        q = settings.qdrant
        vectors = QdrantVectorStore(
            url=q.url,
            host=q.host,
            port=q.port,
            collection=model.collection,
            vector_size=model.dim,
            distance=model.distance,
            config=config,
        )
        vectors.ensure_collection(recreate=False)
        storage = build_object_storage(settings.s3)
    except BaseException:
        if vectors is not None:
            vectors.close()
        metadata.close()
        raise

    logger.info(
        "retrieval infra ready | model=%s id=%s collection=%s dim=%s distance=%s",
        model.space_id,
        model.id,
        model.collection,
        model.dim,
        model.distance,
    )
    return RetrievalInfra(metadata=metadata, vectors=vectors, storage=storage, model=model)


def build_retrieval_stack(
    config: dict[str, Any],
    settings: Settings,
    *,
    indexed_dir: Optional[str] = None,
) -> RetrievalStack:
    """Infra + ONNX encoder for the active model + use cases."""
    infra = build_retrieval_infra(settings, config)
    try:
        embedding = EmbedderPortImpl(config=config)
        # Encoder and collection must belong to the same model version.
        infra.model.check_encoder_dim(embedding.probe_dim())
    except BaseException:
        infra.close()
        raise

    model = infra.model
    metadata, vectors, storage = infra.metadata, infra.vectors, infra.storage
    save_dir = indexed_dir or f"{settings.paths.save_images_dir}/indexed"
    return RetrievalStack(
        infra=infra,
        embedding=embedding,
        index_uc=IndexImageUseCase(
            embedding, vectors, metadata, model=model, storage=storage, indexed_dir=save_dir
        ),
        search_uc=SearchImageUseCase(embedding, vectors, metadata, model=model),
        list_uc=ListImagesUseCase(metadata),
        delete_uc=DeleteImageUseCase(metadata),
        deletion_worker=DeletionWorker(metadata, vectors, storage),
        recompute_scenes_uc=RecomputeScenesUseCase(metadata),
    )
