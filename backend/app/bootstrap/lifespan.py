from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.bootstrap.dependency_logging import log_startup_exception, redact_text
from app.bootstrap.reconciler import start_reconciler
from app.bootstrap.state import ApplicationState
from app.core import errors
from app.core.config.enum_store import get_enum_store
from app.core.config.nacos import NacosConfigProvider, NacosConfigSnapshot
from app.core.config.settings import ImageDownloadSettings, Settings, config_flag, settings
from app.core.observability.logging import setup_logger


logger = logging.getLogger("lifespan")

def create_http_client(app_settings: Settings = settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=app_settings.predict.http_timeout,
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=app_settings.http_client.max_connections,
            max_keepalive_connections=app_settings.http_client.max_keepalive_connections,
        ),
        headers={"User-Agent": "svc.predict/1.0"},
    )


def load_application_config(
    app_settings: Settings = settings,
    config_provider: NacosConfigProvider | None = None,
) -> tuple[dict, dict]:
    own_provider = config_provider is None
    config_provider = config_provider or NacosConfigProvider(app_settings.nacos)
    try:
        config, source = config_provider.load(app_settings.config.yaml_path)
    finally:
        if own_provider:
            config_provider.close()
    return config, {
        "app_env": app_settings.application.env,
        "yaml_path": app_settings.config.yaml_path,
        "loaded": True,
        **source,
    }


def create_embedder(config: dict):
    """创建进程内共享的特征编码器，供预测和检索使用。"""
    from app.clients.inference import Embedder
    return Embedder(config)


def create_detector(config: dict):
    """创建进程内共享的 GroundingDINO 客户端，仅在配置启用时加载。"""
    from app.clients.ground_dino import GdinoDetect
    return GdinoDetect(config)


def create_food_service(config: dict, embedder):
    from app.services.predict.food_service import FoodService
    return FoodService(config, embedder)


def warmup_embedder(embedder, app_settings: Settings = settings) -> None:
    logger.info("预热开关：%s", app_settings.warmup.enabled)
    if not app_settings.warmup.enabled:
        return
    times = app_settings.warmup.embedding_runs
    logger.info("预热中 | embedding=%d 次", times)
    embedder.warmup(times)
    logger.info("预热配置 | warmup=%s", app_settings.warmup)


def warmup_detector(detector, app_settings: Settings = settings) -> None:
    if not app_settings.warmup.enabled:
        return
    times = app_settings.warmup.groundingdino_runs
    detector.warmup(times)


def _detector_config(config: dict) -> dict | None:
    section = config.get("GROUNDINGDINO_CONFIG")
    if not config_flag(config, "GROUNDINGDINO_CONFIG", "enabled", bool(section)):
        return None
    return {
        "GROUNDINGDINO_CONFIG": dict(section),
        "TOTAL_CONFIG": {"device_id": (config.get("TOTAL_CONFIG") or {}).get("device_id", 0)},
    }


def _detector_for(runtime: ApplicationState, config: dict):
    """准备可用的检测器，保留正在处理请求的旧实例。"""
    detector_config = _detector_config(config)
    if detector_config is None:
        return None
    if runtime.detector is not None and _detector_config(runtime.config) == detector_config:
        return runtime.detector
    detector = create_detector(detector_config)
    warmup_detector(detector, runtime.settings)
    return detector


def initialize_detector(runtime: ApplicationState):
    """加载可选的检测器；失败时记录错误，特征提取服务继续运行。"""
    runtime.detector = None
    runtime.detector_error = None
    try:
        runtime.detector = _detector_for(runtime, runtime.config)
    except Exception as exc:
        runtime.detector_error = f"{type(exc).__name__}: {exc}"
        logger.exception("GroundingDINO 初始化或预热失败：%s", exc)
    return runtime.detector




def initialize_embedder(runtime: ApplicationState):
    """加载并预热特征编码器；加载失败时记录 initialization_error 并返回 None。"""
    try:
        embedder = create_embedder(runtime.config)
    except Exception as exc:
        runtime.initialization_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Embedding 模型初始化失败：%s", exc)
        return None
    try:
        warmup_embedder(embedder, runtime.settings)
    except Exception as exc:
        runtime.warmup_error = f"{type(exc).__name__}: {exc}"
        logger.exception("预热失败：%s", exc)
    runtime.embedder = embedder
    return embedder


def _loaded_embedder_for(runtime: ApplicationState, config: dict):
    """查找已加载、规格与配置一致且权重文件未变的编码器，否则返回 None。

    即使模型路径相同，权重文件被原地替换后也需要重新加载。
    """
    from app.clients.inference import EmbeddingSpec

    spec = EmbeddingSpec.try_from_config(config)
    if spec is None:
        return None
    holders = (
        runtime.embedder,
        getattr(runtime.food_service, "embeddingTool", None),
        getattr(runtime.retrieval, "embedding", None),
    )
    for embedder in holders:
        if embedder is None or getattr(embedder, "spec", None) != spec:
            continue
        unchanged = getattr(embedder, "files_unchanged", None)
        if callable(unchanged) and not unchanged():
            logger.warning("%s 的权重文件已被替换，重新加载", spec.model_path)
            continue
        return embedder
    return None


def apply_download_policy(config: dict) -> None:
    """启动时读取 IMAGE_DOWNLOAD_CONFIG.trusted_networks，统一应用于远程图片下载。"""
    from app.clients import public_image

    try:
        trusted = ImageDownloadSettings.from_config(config).trusted_networks
    except ValueError as exc:
        logger.error("IMAGE_DOWNLOAD_CONFIG 已忽略，下载仍仅允许公网地址：%s", exc)
        trusted = ()
    public_image.set_trusted_networks(trusted)
    if trusted:
        logger.warning("图像下载同时信任 %s（IMAGE_DOWNLOAD_CONFIG.trusted_networks）",
                       ", ".join(str(network) for network in trusted))


def load_enum_file() -> None:
    """启动时读取 config/param/enums.yaml，提前记录文件中的错误。

    枚举文件错误不阻止启动，预测流程不依赖该文件。修复前，携带可编辑枚举值的入库请求
    和 /api/enums 接口返回 ENUM_FILE_INVALID；文件变化后，枚举存储会自动重新读取。
    """
    try:
        get_enum_store().catalog()
    except errors.AppError:
        pass  # 枚举存储已记录具体错误


def _retrieval_enabled(config: dict) -> bool:
    """读取检索开关；为 false 时跳过 PostgreSQL 和 Qdrant 初始化，供本机调试等仅预测场景使用。"""
    return config_flag(config, "TOTAL_CONFIG", "retrieval_enabled", True)


def _is_infra_unreachable(exc: BaseException) -> bool:
    """判断访问 Compose 服务名时是否出现域名解析失败、连接被拒绝或超时等连接错误。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "getaddrinfo failed",
        "failed to resolve host",
        "name or service not known",
        "nodename nor servname",
        "connection refused",
        "actively refused",
        "could not connect",
        "timeout expired",
        "operationalerror",
    )
    return any(m in text for m in markers)


def _retrieval_local_debug_hint() -> str:
    return (
        "本机调试提示：compose 服务名（postgres/qdrant）仅在 docker 内可解析；"
        "请在 Nacos 或本地 YAML 中将 POSTGRES_CONFIG.host / QDRANT_CONFIG.url 指向 localhost"
        "（并执行 `docker compose up -d postgres qdrant`），"
        "或将 TOTAL_CONFIG.retrieval_enabled: false 以仅启动 predict"
    )


def initialize_catalog(runtime: ApplicationState) -> None:
    """基于 PostgreSQL 创建类别和场景管理服务；失败时保留其他服务。

    类别和场景管理不依赖特征编码器，也不受 TOTAL_CONFIG.retrieval_enabled 开关影响。
    """
    runtime.catalog = None
    runtime.catalog_error = None
    from app.bootstrap.catalog import build_catalog

    try:
        runtime.catalog = build_catalog(runtime.config)
    except Exception as exc:
        if isinstance(exc, errors.AppError):
            runtime.catalog_error = f"{exc.code}: {errors.client_detail(exc)}"
        else:
            runtime.catalog_error = f"{type(exc).__name__}: {exc}"
        if _is_infra_unreachable(exc):
            logger.warning("Catalog 不可用（基础设施） | message=%s | %s",
                           redact_text(f"{type(exc).__name__}: {exc}", runtime.config), _retrieval_local_debug_hint())
        else:
            log_startup_exception(logger, "Catalog 初始化失败", exc, runtime.config)


def initialize_retrieval(runtime: ApplicationState, embedder) -> None:
    """基于共享的特征编码器装配检索服务；失败时保留预测服务。

    类别和场景管理服务可用时，复用其 PostgreSQL 存储，使每个进程共用一个连接池。
    """
    runtime.retrieval = None
    runtime.retrieval_error = None
    runtime.retrieval_error_code = None
    try:
        enabled = _retrieval_enabled(runtime.config)
    except ValueError as exc:  # 与其他检索错误一样，开关配置错误只影响检索服务
        runtime.retrieval_error = f"{type(exc).__name__}: {exc}"
        runtime.retrieval_error_code = "NOT_READY"
        logger.warning("Retrieval 栈已跳过 | %s", runtime.retrieval_error)
        return
    if not enabled:
        runtime.retrieval_error = "TOTAL_CONFIG.retrieval_enabled is false"
        runtime.retrieval_error_code = "RETRIEVAL_DISABLED"
        logger.info("Retrieval 栈已跳过 | TOTAL_CONFIG.retrieval_enabled is false")
        return
    if embedder is None:
        runtime.retrieval_error = f"embedding model unavailable: {runtime.initialization_error}"
        runtime.retrieval_error_code = "NOT_READY"
        logger.warning("Retrieval 栈已跳过 | %s", runtime.retrieval_error)
        return

    from app.bootstrap.retrieval_stack import build_retrieval_stack

    catalog = runtime.catalog
    try:
        runtime.retrieval = build_retrieval_stack(
            runtime.config,
            runtime.settings,
            embedder=embedder,
            metadata=catalog.store if catalog is not None else None,
        )
    except Exception as exc:
        # 健康检查接口会展示 retrieval_error；内部错误的详细信息仅记录到日志
        if isinstance(exc, errors.AppError):
            runtime.retrieval_error = f"{exc.code}: {errors.client_detail(exc)}"
            runtime.retrieval_error_code = exc.code
        else:
            runtime.retrieval_error = f"{type(exc).__name__}: {exc}"
        if _is_infra_unreachable(exc):
            logger.warning("Retrieval 栈不可用（基础设施） | message=%s | %s",
                           redact_text(f"{type(exc).__name__}: {exc}", runtime.config), _retrieval_local_debug_hint())
        else:
            log_startup_exception(logger, "Retrieval 栈初始化失败", exc, runtime.config)
        return

    model = runtime.retrieval.model  # 连接地址由 build_retrieval_infra 记录到日志
    logger.info("Retrieval 栈已就绪 | collection=%s | model=%s", model.collection, model.space_id)


def initialize_models(runtime: ApplicationState) -> None:
    """检索启用时创建模型版本管理服务，供 /api/embedding-models 使用。

    即使检索服务初始化失败也会创建，以便通过切换模型版本修复编码器与向量库不匹配的问题。
    """
    if runtime.retrieval_error_code == "RETRIEVAL_DISABLED":
        return
    from app.bootstrap.model_registry import ModelRegistry

    runtime.models = ModelRegistry(runtime)


def initialize_food_service(runtime: ApplicationState, embedder) -> None:
    if embedder is None:
        return  # initialization_error 已记录编码器不可用的原因
    try:
        runtime.food_service = create_food_service(runtime.config, embedder)
    except Exception as exc:
        runtime.initialization_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Food service 初始化失败：%s", exc)


def apply_nacos_config_update(runtime: ApplicationState, provider: NacosConfigProvider, snapshot: NacosConfigSnapshot,) -> bool:
    """先构建新的预测服务，再原子更新运行时配置及服务实例。

    EMBEDDING_CONFIG 与已加载的编码器匹配时复用实例，普通配置变化不会重新加载 ONNX。
    更换模型时，预测服务使用新编码器，检索服务继续使用与其 Qdrant 集合配对的编码器；
    新模型可通过 /api/embedding-models 登记版本并生成向量。
    GroundingDINO 的配置和设备未变时复用实例；需要替换时，先加载并预热新实例，
    全部准备成功后再发布新配置和服务实例。
    """
    if runtime.stopping:
        return False
    try:
        embedder = _loaded_embedder_for(runtime, snapshot.config)
        if embedder is None:
            embedder = create_embedder(snapshot.config)
            warmup_embedder(embedder, runtime.settings)
            if runtime.retrieval is not None:
                logger.warning("EMBEDDING_CONFIG 换了模型：predict 改用 %s；检索仍用 %s（collection %s），到「模型版本」登记新版本、生成向量后再切换",
                    embedder.model_path, runtime.retrieval.model.space_id, runtime.retrieval.model.collection)
        detector = _detector_for(runtime, snapshot.config)
        service = create_food_service(snapshot.config, embedder)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        runtime.record_config_reload_error(error)
        logger.exception("Nacos配置重读失败 | data_ids=%s | error=%s", list(snapshot.data_ids), error)
        return False

    config_info = {
        "app_env": runtime.settings.application.env,
        "yaml_path": runtime.settings.config.yaml_path,
        "loaded": True,
        **provider.source_info(snapshot),
    }
    runtime.replace_configuration(snapshot.config, config_info, service, embedder, detector=detector)
    logger.info("Nacos配置重读成功 | data_ids=%s", list(snapshot.data_ids))
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    config_provider = NacosConfigProvider(settings.nacos)
    config, config_info = load_application_config(settings, config_provider)
    runtime = ApplicationState(
        config=config,
        config_info=config_info,
        http_client=create_http_client(settings),
        settings=settings,
        config_provider=config_provider,
    )
    app.state.runtime = runtime
    logger.info("应用正在启动 | ENV=%s | cfg=%s", runtime.settings.application.env, runtime.config_info,)
    apply_download_policy(config)
    load_enum_file()
    embedder = initialize_embedder(runtime)
    initialize_detector(runtime)
    initialize_food_service(runtime, embedder)
    initialize_catalog(runtime)
    initialize_retrieval(runtime, embedder)
    initialize_models(runtime)
    runtime.reconciler = start_reconciler(runtime)
    config_provider.start_polling(lambda snapshot: apply_nacos_config_update(runtime, config_provider, snapshot))
    try:
        yield
    finally:
        try:
            await runtime.close()
        except Exception as exc:
            logger.exception("应用资源清理失败：%s", exc)
        logger.info("应用正在关闭")
