"""Composition root for retrieval (图搜图): builds and wires objects, nothing else.

    build_retrieval_infra   PG -> active embedding model -> Qdrant collection -> S3
                            (no ONNX; enough for workers)
    build_retrieval_stack   infra + encoder (dim checked against the model) + use cases
                            (API process; the encoder is the process-wide Embedder
                            passed in by bootstrap.lifespan, shared with predict)

The API process passes the catalog's PostgreSQL store (metadata=...; app.bootstrap.catalog)
so there is one pool; the infra then borrows it and does not close it. Workers let the infra
open and own its store.

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
from app.services.retrieval.reembed_image import ReembedImageUseCase
from app.services.retrieval.recompute_scenes import RecomputeScenesUseCase
from app.services.retrieval.search_image import SearchImageUseCase
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
from app.services.retrieval.vector_status import ImageVectorStatusUseCase

logger = logging.getLogger("retrieval")


@dataclass
class RetrievalInfra:
    """Connections + the active model. Owns their lifecycle, except a borrowed metadata store."""

    metadata: PgMetadataStore
    vectors: QdrantVectorStore
    storage: ImageStoragePort
    model: ActiveModel
    owns_metadata: bool = True

    def ping(self) -> dict[str, dict[str, Any]]:
        """Dependency status; never raises."""
        return {
            "postgres": self.metadata.ping(),
            "qdrant": self.vectors.ping(),
            "storage": self.storage.ping(),
        }

    def close(self) -> None:
        closers = [("qdrant", self.vectors.close)]
        if self.owns_metadata:
            closers.append(("postgres", self.metadata.close))
        for name, closer in closers:
            try:
                closer()
            except Exception:
                logger.exception("关闭 %s 失败", name)


@dataclass
class RetrievalStack:
    """Everything the retrieval HTTP layer calls."""

    infra: RetrievalInfra
    embedding: Embedder
    index_uc: IndexImageUseCase
    reembed_uc: ReembedImageUseCase
    search_uc: SearchImageUseCase
    delete_uc: DeleteImageUseCase
    deletion_worker: DeletionWorker
    recompute_scenes_uc: RecomputeScenesUseCase
    scene_sync_uc: SyncSceneMembershipsUseCase
    vector_status_uc: ImageVectorStatusUseCase

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


def build_retrieval_infra(
    config: Mapping[str, Any],
    *,
    metadata: PgMetadataStore | None = None,
) -> RetrievalInfra:
    """PG schema check -> current embedding model -> its Qdrant collection -> object storage.

    metadata: a store to borrow (the catalog's); None opens one from POSTGRES_CONFIG.
    """
    # Parse every section before opening anything: a config error fails fast with nothing to release.
    pg = PostgresSettings.from_config(config) if metadata is None else None
    connection = _qdrant_connection(config)
    storage_settings = ImageStorageSettings.from_config(config)

    owns_metadata = metadata is None
    if metadata is None:
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
        if owns_metadata:
            metadata.close()
        raise

    logger.info(
        "Retrieval 基础设施已就绪 | postgres=%s/%s | qdrant=%s | model=%s | id=%s | collection=%s | dim=%s | distance=%s",
        metadata.host,
        metadata.database,
        connection.url,
        model.space_id,
        model.id,
        model.collection,
        model.dim,
        model.distance,
    )
    return RetrievalInfra(
        metadata=metadata, vectors=vectors, storage=storage, model=model, owns_metadata=owns_metadata
    )


def build_retrieval_stack(
    config: Mapping[str, Any],
    settings: Settings,
    *,
    embedder: Optional[Embedder] = None,
    indexed_dir: Optional[str] = None,
    metadata: PgMetadataStore | None = None,
) -> RetrievalStack:
    """Infra + encoder + use cases.

    embedder: the process-wide encoder (shared with predict). Only standalone
    callers omit it, in which case one is built from config.
    metadata: the catalog's store to borrow (see build_retrieval_infra).
    """
    infra = build_retrieval_infra(config, metadata=metadata)
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
        reembed_uc=ReembedImageUseCase(embedding, vectors, metadata, model=model, storage=storage),
        search_uc=SearchImageUseCase(embedding, vectors, metadata, model=model),
        delete_uc=DeleteImageUseCase(metadata),
        deletion_worker=DeletionWorker(metadata, vectors, storage),
        recompute_scenes_uc=RecomputeScenesUseCase(metadata),
        scene_sync_uc=SyncSceneMembershipsUseCase(metadata, vectors, model=model),
        vector_status_uc=ImageVectorStatusUseCase(vectors, model=model),
    )
