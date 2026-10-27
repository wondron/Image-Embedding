"""Build retrieval stack (embedder + Qdrant + MySQL + FlyB + use cases)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.adapter.embedding.embedder_port_impl import EmbedderPortImpl
from app.adapter.persistence.flyb_fallback import FlyBFallbackAdapter
from app.adapter.persistence.mysql_metadata import MySQLMetadataStore
from app.adapter.persistence.qdrant_store import QdrantVectorStore
from app.core.config.settings import Settings
from app.domain.retrieval.embedding_space import resolve_space_id
from app.domain.retrieval.index_image import IndexImageUseCase
from app.domain.retrieval.search_image import SearchImageUseCase

logger = logging.getLogger("bootstrap.retrieval")


@dataclass
class RetrievalStack:
    embedding: EmbedderPortImpl
    vectors: QdrantVectorStore
    metadata: MySQLMetadataStore
    flyb: FlyBFallbackAdapter
    index_uc: IndexImageUseCase
    search_uc: SearchImageUseCase
    embedding_space_id: str = ""


def build_retrieval_stack(
    config: dict[str, Any],
    settings: Settings,
    *,
    indexed_dir: Optional[str] = None,
) -> RetrievalStack:
    q = settings.qdrant
    m = settings.mysql

    # Prefer settings/env URL so host-side overrides work outside compose DNS.
    vectors = QdrantVectorStore(
        url=q.url,
        host=q.host,
        port=q.port,
        collection=q.collection,
        vector_size=q.vector_size,
        distance=q.distance,
        config=config,
    )
    if not vectors.wired:
        raise RuntimeError("QdrantVectorStore failed to wire domain QdrantStore")
    vectors.ensure_collection(recreate=False)

    metadata = MySQLMetadataStore(
        host=m.host,
        port=m.port,
        user=m.user,
        password=m.password,
        database=m.database,
    )
    metadata.ensure_schema()

    # SSOT §1.5: resolve space_id then gate against embedding_collection_meta.
    # Mismatch -> VECTOR_SPACE_MISMATCH (no silent mix / no auto recreate).
    space_id = resolve_space_id(config, vector_dim=int(q.vector_size))
    metadata.ensure_embedding_space(
        q.collection,
        space_id,
        int(q.vector_size),
    )
    logger.info(
        "embedding_space gate ok | collection=%s space_id=%s dim=%s",
        q.collection,
        space_id,
        q.vector_size,
    )

    embedding = EmbedderPortImpl(config=config)
    flyb = FlyBFallbackAdapter(config)

    save_dir = indexed_dir or f"{settings.paths.save_images_dir}/indexed"
    index_uc = IndexImageUseCase(embedding, vectors, metadata, indexed_dir=save_dir)
    search_uc = SearchImageUseCase(embedding, vectors, metadata, flyb)
    return RetrievalStack(
        embedding=embedding,
        vectors=vectors,
        metadata=metadata,
        flyb=flyb,
        index_uc=index_uc,
        search_uc=search_uc,
        embedding_space_id=space_id,
    )