"""Composition root for retrieval (图搜图): builds and wires objects, nothing else.

    build_retrieval_infra   PG -> active embedding model -> Qdrant collection -> S3
                            (no ONNX; enough for workers)
    build_retrieval_stack   infra + encoder (dim checked against the model) + use cases
                            (API process; the encoder is the process-wide Embedder
                            passed in by bootstrap.lifespan, shared with predict)

Business rules live in app.services.retrieval; adapters in app.repositories /
app.clients. Both builders release what they opened if a later step fails.

Every connection parameter comes from the effective config (Nacos layered over the
local YAML): POSTGRES_CONFIG, QDRANT_CONFIG, IMAGE_STORAGE_CONFIG. They are read
once here, so changing them in Nacos takes effect after a restart.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from app.clients.inference import Embedder
from app.clients.qdrant import QdrantConnection, QdrantStore
from app.clients.image_storage import build_image_storage
from app.core.config.settings import ImageStorageSettings, PostgresSettings, Settings
from app.repositories.metadata import PgMetadataStore
from app.repositories.ports import ImageStoragePort
from app.repositories.vectors import QdrantVectorStore
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.delete_image import DeleteImageUseCase, DeletionWorker
from app.services.retrieval.index_image import IndexImageUseCase
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.manage_categories import ManageCategoriesUseCase
from app.services.retrieval.recompute_scenes import RecomputeScenesUseCase
from app.services.retrieval.search_image import SearchImageUseCase

logger = logging.getLogger("retrieval")


@dataclass
class RetrievalInfra:
    """Connections + the active model. Owns their lifecycle."""

    metadata: PgMetadataStore
    vectors: QdrantVectorStore
    storage: ImageStoragePort
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
    embedding: Embedder
    index_uc: IndexImageUseCase
    search_uc: SearchImageUseCase
    list_uc: ListImagesUseCase
    delete_uc: DeleteImageUseCase
    deletion_worker: DeletionWorker
    recompute_scenes_uc: RecomputeScenesUseCase
    categories_uc: ManageCategoriesUseCase

    @property
    def model(self) -> ActiveModel:
        return self.infra.model

    def close(self) -> None:
        self.infra.close()


def _qdrant_connection(config: Mapping[str, Any]) -> QdrantConnection:
    """QDRANT_CONFIG as-is (url, api_key, timeout, prefer_grpc); the endpoint must be explicit."""
    section = dict(config.get("QDRANT_CONFIG") or {})
    if not section.get("url") and not (section.get("host") and section.get("port")):
        raise ValueError("QDRANT_CONFIG.url (or host + port) missing from the configuration (Nacos / local YAML)")
    return QdrantConnection.from_mapping(section)


def build_retrieval_infra(config: Mapping[str, Any]) -> RetrievalInfra:
    """PG schema check -> current embedding model -> its Qdrant collection -> object storage."""
    # Parse every section before opening anything: a config error fails fast with nothing to release.
    pg = PostgresSettings.from_config(config)
    connection = _qdrant_connection(config)
    storage_settings = ImageStorageSettings.from_config(config)

    metadata = PgMetadataStore(url=pg.url(), connect_timeout=pg.connect_timeout)
    vectors: QdrantVectorStore | None = None
    try:
        metadata.ensure_schema()
        model = ActiveModel.from_row(metadata.get_current_embedding_model())
        vectors = QdrantVectorStore(
            QdrantStore(
                connection,
                collection=model.collection,
                vector_dim=model.dim,
                distance=model.distance,
            )
        )
        vectors.ensure_collection(recreate=False)
        storage = build_image_storage(storage_settings)
    except BaseException:
        if vectors is not None:
            vectors.close()
        metadata.close()
        raise

    logger.info(
        "retrieval infra ready | postgres=%s/%s qdrant=%s | model=%s id=%s collection=%s dim=%s distance=%s",
        metadata.host,
        metadata.database,
        connection.url,
        model.space_id,
        model.id,
        model.collection,
        model.dim,
        model.distance,
    )
    return RetrievalInfra(metadata=metadata, vectors=vectors, storage=storage, model=model)


def build_retrieval_stack(
    config: Mapping[str, Any],
    settings: Settings,
    *,
    embedder: Optional[Embedder] = None,
    indexed_dir: Optional[str] = None,
) -> RetrievalStack:
    """Infra + encoder + use cases.

    embedder: the process-wide encoder (shared with predict). Only standalone
    callers omit it, in which case one is built from config.
    """
    infra = build_retrieval_infra(config)
    try:
        embedding = embedder if embedder is not None else Embedder(config)
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
        categories_uc=ManageCategoriesUseCase(metadata),
    )
