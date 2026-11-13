"""Composition root for retrieval (图搜图): builds and wires objects, nothing else.

    build_retrieval_infra   PG -> active embedding model -> Qdrant collection -> S3
                            (no ONNX; enough for workers)
    build_retrieval_stack   infra + the encoder paired with the model + use cases
                            (API process; the configured encoder is the process-wide
                            Embedder passed in by bootstrap.lifespan, shared with predict)

Pairing (pair_encoder): the stack uses the configured encoder (EMBEDDING_CONFIG) only when its
fingerprint is the model version's (services.retrieval.model_versions.same_encoder). When
EMBEDDING_CONFIG names another model, the version's own encoder is loaded from the spec its
fingerprint recorded, so search keeps querying the collection with the encoder that wrote it
while the new model is registered and built (/api/embedding-models). When that cannot be
loaded the stack does not start (EMBEDDING_MODEL_MISMATCH): a wrong pair would answer every
search with neighbours from another embedding space. A version registered before fingerprints
existed is checked against a few of its stored images first, then gets the configured
encoder's fingerprint.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from app.clients.inference import Embedder, config_for_fingerprint, weights_status
from app.clients.qdrant import QdrantConnection, QdrantStore
from app.clients.image_storage import build_image_storage
from app.core import errors as err
from app.core.config.settings import ImageStorageSettings, PostgresSettings, Settings
from app.core.enums import ModelStatus
from app.repositories.metadata import PgMetadataStore
from app.repositories.ports import ImageStoragePort
from app.repositories.vectors import QdrantVectorStore
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.delete_image import DeleteImageUseCase, DeletionWorker
from app.services.retrieval.model_versions import ADOPTION_SAMPLES, check_collection_encoder, moved, same_encoder
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
    _siblings: dict[str, QdrantVectorStore] = field(default_factory=dict, repr=False)

    def other_vectors(self) -> list[QdrantVectorStore]:
        """The collections of the other versions still in use (building / ready), over the same
        client; one whose collection is gone from Qdrant is left out. Read per call: a version
        registered or retired since is seen at once."""
        out: list[QdrantVectorStore] = []
        rows = self.metadata.list_embedding_model_rows(statuses=(ModelStatus.BUILDING.value, ModelStatus.READY.value))
        for row in rows:
            if int(row["id"]) == self.model.id:
                continue
            name = str(row["collection"])
            store = self._siblings.get(name)
            if store is None:
                store = self._siblings[name] = self.vectors.sibling(
                    collection=name, dim=int(row["dim"]), distance=str(row["distance"])
                )
            if store.collection_exists():
                out.append(store)
        return out

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
    model: ActiveModel | None = None,
) -> RetrievalInfra:
    """PG schema check -> current embedding model -> its Qdrant collection -> object storage.

    metadata: a store to borrow (the catalog's); None opens one from POSTGRES_CONFIG.
    model: the version to build on instead of the current one (a switch builds the stack of the
    version it is about to make current, before PostgreSQL says so).
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
        if model is None:
            model = ActiveModel.from_row(metadata.get_current_embedding_model())
        vectors = QdrantVectorStore(
            QdrantStore(
                connection,
                collection=model.collection,
                vector_dim=model.dim,
                distance=model.distance,
            )
        )
        existed = vectors.collection_exists()
        vectors.ensure_collection(recreate=False)
        if not existed:
            # A collection made anew holds no point: whatever the ledger says, nothing is in it
            forgotten = metadata.clear_embeddings(model.id)
            if forgotten:
                logger.warning(
                    "collection=%s 不存在，已新建；%s 的 %d 条写入记录作废，这些图片要重新生成向量",
                    model.collection, model.space_id, forgotten,
                )
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


def load_encoder_from_spec(config: Mapping[str, Any]) -> Callable[[Mapping[str, Any]], Embedder]:
    """Loads the encoder a fingerprint's spec describes, with this process's runtime flags."""

    def load(spec: Mapping[str, Any]) -> Embedder:
        return Embedder(config_for_fingerprint(spec, config))

    return load


def _adopt_configured_encoder(infra: RetrievalInfra, configured: Embedder) -> None:
    """The current version has no fingerprint yet (registered before they existed): check a few
    of its stored images against the configured encoder, then record its fingerprint.

    EMBEDDING_MODEL_MISMATCH when the collection was written by another encoder. When the check
    cannot run (originals or vectors unreadable) the encoder is used as before but nothing is
    recorded, so the next start checks again.
    """
    model = infra.model
    model.check_encoder_dim(configured.probe_dim())
    fingerprint = configured.fingerprint()
    try:
        samples = list(infra.metadata.sample_embedded_images(model.id, limit=ADOPTION_SAMPLES))
        stored = infra.vectors.get_vectors([str(s["id"]) for s in samples]) if samples else {}
        report = check_collection_encoder(
            samples=samples, stored=stored, read_original=infra.storage.read_by_url_or_key, embed=configured.embed_bytes
        )
    except Exception as exc:
        logger.warning("无法核对 collection=%s 与配置的编码器（%s）；本次不记录指纹，下次启动再核对", model.collection, exc)
        return
    if report["matches"] is False:
        raise err.embedding_model_mismatch(
            f"collection {model.collection} ({model.space_id}) was written by another encoder than EMBEDDING_CONFIG "
            f"({configured.model_path}): {report['checked']} stored images embedded again, lowest similarity "
            f"{report['min_similarity']:.4f}; point EMBEDDING_CONFIG back at the model that wrote it, or register "
            "the configured encoder as a new version and build it (/api/embedding-models)"
        )
    if report["matches"] is None and samples:
        logger.warning("collection=%s 的样本图读不到，未能核对编码器；本次不记录指纹", model.collection)
        return
    if infra.metadata.record_model_fingerprint(model.id, fingerprint):
        logger.info("已记录 %s（collection=%s）的编码器指纹，核对了 %d 张图片", model.space_id, model.collection, report["checked"])
    infra.model = replace(model, fingerprint=fingerprint)


def _follow_moved_weights(infra: RetrievalInfra, encoder: Any) -> None:
    """The version's encoder found at another path (or now with file hashes): record where, so
    a later load (a roll back, a restart under another EMBEDDING_CONFIG) finds the weights."""
    model = infra.model
    current = encoder.fingerprint()
    if not moved(model.fingerprint, current):
        return
    try:
        infra.metadata.refresh_model_fingerprint(model.id, dict(current))
    except Exception:
        logger.exception("记录 %s 的权重新位置失败；下次再试", model.space_id)
        return
    logger.info("%s 的权重在新位置 %s（内容相同），已更新记录", model.space_id, current["spec"].get("model_path"))
    infra.model = replace(model, fingerprint=dict(current), weights_uri=str(current["spec"].get("model_path") or ""))


def pair_encoder(
    infra: RetrievalInfra,
    configured: Embedder,
    *,
    candidates: Sequence[Any] = (),
    load: Callable[[Mapping[str, Any]], Embedder] | None = None,
) -> Embedder:
    """The encoder that wrote infra.model's collection: the configured one, one of candidates
    (encoders already in memory), or the version's own loaded from its fingerprint.

    VECTOR_DIM_MISMATCH / EMBEDDING_MODEL_MISMATCH when there is none.
    """
    model = infra.model
    if model.fingerprint is None:
        _adopt_configured_encoder(infra, configured)
        return configured
    for encoder in (configured, *candidates):
        if encoder is not None and same_encoder(model.fingerprint, encoder.fingerprint()):
            _follow_moved_weights(infra, encoder)
            return encoder
    spec = dict(model.fingerprint.get("spec") or {})
    if load is None:
        raise err.embedding_model_mismatch(
            f"no encoder of {model.space_id} (collection {model.collection}) is loaded and none may be loaded here"
        )
    status = weights_status(model.fingerprint)
    if status in ("missing", "changed"):
        what = "are not in this image any more" if status == "missing" else "were replaced by other files"
        raise err.embedding_model_mismatch(
            f"EMBEDDING_CONFIG ({configured.model_path}) is another model than the current version {model.space_id} "
            f"(collection {model.collection}), and that version's weights at {spec.get('model_path')} {what}. Keep "
            "each version's weights in their own folder until the version is retired, or register the configured "
            "encoder as a new version and switch to it (/api/embedding-models)"
        )
    try:
        own = load(spec)
    except Exception as exc:
        raise err.embedding_model_mismatch(
            f"EMBEDDING_CONFIG ({configured.model_path}) is another model than the current version {model.space_id} "
            f"(collection {model.collection}), and the weights it was built with ({spec.get('model_path')}) could not "
            f"be loaded: {type(exc).__name__}: {exc}. Register the configured encoder as a new version and switch "
            "to it (/api/embedding-models), or restore the weights"
        ) from exc
    if not same_encoder(model.fingerprint, own.fingerprint()):
        raise err.embedding_model_mismatch(
            f"the weights at {spec.get('model_path')} are no longer the encoder that wrote {model.collection} "
            f"({model.space_id}); register the configured encoder as a new version and switch to it"
        )
    logger.warning(
        "EMBEDDING_CONFIG（%s）与当前模型 %s 不是同一个；检索改用它自己的编码器 %s（collection=%s）。"
        "新模型请到「模型版本」登记、生成向量后再切换",
        configured.model_path, model.space_id, spec.get("model_path"), model.collection,
    )
    return own


def build_retrieval_stack(
    config: Mapping[str, Any],
    settings: Settings,
    *,
    embedder: Optional[Embedder] = None,
    indexed_dir: Optional[str] = None,
    metadata: PgMetadataStore | None = None,
    model: ActiveModel | None = None,
    candidates: Sequence[Any] = (),
    load: Callable[[Mapping[str, Any]], Embedder] | None = None,
) -> RetrievalStack:
    """Infra + encoder + use cases.

    embedder: the process-wide configured encoder (shared with predict). Only standalone
    callers omit it, in which case one is built from config.
    metadata: the catalog's store to borrow (see build_retrieval_infra).
    model / candidates / load: see build_retrieval_infra and pair_encoder (load defaults to
    loading from the fingerprint's spec with this config's runtime flags).
    """
    infra = build_retrieval_infra(config, metadata=metadata, model=model)
    try:
        configured = embedder if embedder is not None else Embedder(config)
        # Encoder and collection must belong to the same model version.
        embedding = pair_encoder(
            infra, configured, candidates=candidates, load=load if load is not None else load_encoder_from_spec(config)
        )
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
        deletion_worker=DeletionWorker(metadata, vectors, storage, others=infra.other_vectors),
        recompute_scenes_uc=RecomputeScenesUseCase(metadata),
        scene_sync_uc=SyncSceneMembershipsUseCase(metadata, vectors, model=model),
        vector_status_uc=ImageVectorStatusUseCase(vectors, model=model),
    )
