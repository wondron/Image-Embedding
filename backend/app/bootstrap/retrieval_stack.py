"""检索（图搜图）的组合根：只负责构建和组装对象，别的什么都不做。

    build_retrieval_infra   PG -> 当前 embedding 模型 -> Qdrant collection -> S3
                            （不加载 ONNX；对后台任务来说足够了）
    build_retrieval_stack   infra + 与模型配对的编码器 + 各个用例
                            （API 进程；配置的编码器是 bootstrap.lifespan 传进来的
                            进程内共用 Embedder，和预测共用）

配对（pair_encoder）：只有配置的编码器（EMBEDDING_CONFIG）的指纹就是该模型版本的指纹时
（services.retrieval.model_versions.same_encoder），检索服务才使用它。EMBEDDING_CONFIG 指向
另一个模型时，按版本指纹记录的 spec 加载该版本自己的编码器，这样在新模型登记和构建期间
（/api/embedding-models），搜索仍然用写入 collection 的那个编码器去查询。加载不了时检索服务
不启动（EMBEDDING_MODEL_MISMATCH）：错误的配对会让每次搜索都返回另一个 embedding 空间里
的近邻。在有指纹之前登记的版本，先用它的几张已存图片核对，然后记下配置的编码器的指纹。

API 进程传入目录的 PostgreSQL 存储（metadata=...；app.bootstrap.catalog），这样只有一个
连接池；infra 借用它，不负责关闭。后台任务让 infra 自己打开并持有存储。

业务规则在 app.services.retrieval 里；适配器在 app.repositories / app.clients 里。
后面的步骤失败时，两个构建函数都会释放已经打开的东西。

所有连接参数都来自生效配置（Nacos 叠加在本地 YAML 上）：POSTGRES_CONFIG、QDRANT_CONFIG、
IMAGE_STORAGE_CONFIG。它们只在这里读取一次，所以在 Nacos 里修改后要重启才生效。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from app.bootstrap.dependency_logging import dependency_initialization, redact_text
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
    """各个连接 + 当前模型。负责它们的生命周期，借来的元数据存储除外。"""

    metadata: PgMetadataStore
    vectors: QdrantVectorStore
    storage: ImageStoragePort
    model: ActiveModel
    owns_metadata: bool = True
    _siblings: dict[str, QdrantVectorStore] = field(default_factory=dict, repr=False)

    def other_vectors(self) -> list[QdrantVectorStore]:
        """同一个客户端上其他仍在使用的版本（building / ready）的 collection；collection 已经从
        Qdrant 消失的版本不包括在内。每次调用时读取：之后登记或下线的版本立刻就能看到。
        """
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
        """依赖状态；从不抛出。"""
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
    """检索 HTTP 层调用的全部内容。"""

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
    """原样使用 QDRANT_CONFIG（url、api_key、timeout、prefer_grpc）；endpoint 必须显式给出。"""
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
    """PG 表结构检查 -> 当前 embedding 模型 -> 它的 Qdrant collection -> 对象存储。

    metadata：要借用的存储（目录的）；None 时用 POSTGRES_CONFIG 打开一个。
    model：用来代替当前版本构建的版本（切换时，在 PostgreSQL 把它设为当前版本之前，
    先构建它的检索服务）。
    """
    # 在打开任何东西之前解析所有配置段：配置错误尽早失败，不需要释放任何东西。
    with dependency_initialization(logger, config, "POSTGRES_CONFIG", stage="配置解析"):
        pg = PostgresSettings.from_config(config) if metadata is None else None
    with dependency_initialization(logger, config, "QDRANT_CONFIG", stage="配置解析"):
        connection = _qdrant_connection(config)
    storage_settings = ImageStorageSettings.from_config(config)

    owns_metadata = metadata is None
    if metadata is None:
        with dependency_initialization(logger, config, "POSTGRES_CONFIG", stage="创建连接"):
            metadata = PgMetadataStore(url=pg.url(), connect_timeout=pg.connect_timeout)
    vectors: QdrantVectorStore | None = None
    try:
        with dependency_initialization(logger, config, "POSTGRES_CONFIG", stage="连接及 schema 检查"):
            metadata.ensure_schema()
        if model is None:
            model = ActiveModel.from_row(metadata.get_current_embedding_model())
        with dependency_initialization(
            logger, config, "QDRANT_CONFIG", stage="连接及 collection 检查",
            collection=model.collection, dim=model.dim, distance=model.distance,
        ):
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
            # 重新建的 collection 里没有向量点：不管台账怎么说，里面都是空的
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
        redact_text(connection.url, config),
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
    """用本进程的运行时开关加载指纹 spec 所描述的编码器。"""

    def load(spec: Mapping[str, Any]) -> Embedder:
        return Embedder(config_for_fingerprint(spec, config))

    return load


def _adopt_configured_encoder(infra: RetrievalInfra, configured: Embedder) -> None:
    """当前版本还没有指纹（在有指纹之前登记的）：用配置的编码器核对它的几张已存图片，
    然后记录它的指纹。

    collection 是另一个编码器写的时为 EMBEDDING_MODEL_MISMATCH。核对无法进行时（原图或
    向量读不了），编码器照旧使用，但什么都不记录，下次启动时再核对。
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
    """在另一个路径下（或者现在带着文件哈希）找到了这个版本的编码器：记下位置，这样以后
    加载（回滚、换了 EMBEDDING_CONFIG 后重启）能找到权重。
    """
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
    """写入 infra.model 的 collection 的编码器：配置的那个、candidates 中的一个（已经在内存里的
    编码器），或者根据指纹加载的该版本自己的编码器。

    都没有时为 VECTOR_DIM_MISMATCH / EMBEDDING_MODEL_MISMATCH。
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
    """infra + 编码器 + 各个用例。

    embedder：进程内共用的配置编码器（和预测共用）。只有独立调用方才不传，这时用配置
    构建一个。
    metadata：要借用的目录存储（见 build_retrieval_infra）。
    model / candidates / load：见 build_retrieval_infra 和 pair_encoder（load 默认用本配置的
    运行时开关按指纹的 spec 加载）。
    """
    infra = build_retrieval_infra(config, metadata=metadata, model=model)
    try:
        configured = embedder if embedder is not None else Embedder(config)
        # 编码器和 collection 必须属于同一个模型版本。
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
        index_uc=IndexImageUseCase(embedding, vectors, metadata, model=model, storage=storage, indexed_dir=save_dir),
        reembed_uc=ReembedImageUseCase(embedding, vectors, metadata, model=model, storage=storage),
        search_uc=SearchImageUseCase(embedding, vectors, metadata, model=model),
        delete_uc=DeleteImageUseCase(metadata),
        deletion_worker=DeletionWorker(metadata, vectors, storage, others=infra.other_vectors),
        recompute_scenes_uc=RecomputeScenesUseCase(metadata),
        scene_sync_uc=SyncSceneMembershipsUseCase(metadata, vectors, model=model),
        vector_status_uc=ImageVectorStatusUseCase(vectors, model=model),
    )
