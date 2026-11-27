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

# 只在启动时读取的配置段：Nacos 热更新会换掉 runtime.config，但检索、入库、模型版本仍用启动时
# 建好的连接，要重启才按新配置连接
RESTART_ONLY_SECTIONS = ("POSTGRES_CONFIG", "QDRANT_CONFIG")

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


def create_segmentor(config: dict):
    """创建进程内共享的分割模型（/api/tools/segment 使用），仅在配置启用时加载。"""
    from app.services.predict.pipeline.a_onnx_segment import OnnxSegmentPy
    return OnnxSegmentPy(config)


def create_recipe_classifier(config: dict):
    """创建进程内共享的菜谱识别模型（业务搜图使用），仅在 RECIPE_DETECT_CONFIG.enabled 开启时加载。"""
    from app.clients.recipe_classifier import RecipeClassifier
    return RecipeClassifier(config)


def create_food_service(config: dict, embedder):
    from app.services.predict.food_service import FoodService
    return FoodService(config, embedder)


# ---- 模型的加载和预热 ---------------------------------------------------------------
# 启动和 Nacos 重新加载都在这里创建模型，创建后马上按 Settings.warmup 预热，每个实例被用上之前
# 都预热过。create_* 只负责创建（测试替换的是它们）。

def _warm_up(model, runs: int, app_settings: Settings) -> None:
    """预热刚创建的模型，Settings.warmup.enabled 为 false 时跳过；次数和耗时由模型自己记日志。"""
    if app_settings.warmup.enabled:
        model.warmup(runs)


def _load_and_warm_up(create, config: dict, runs: int, app_settings: Settings):
    """创建并预热一个模型。预热失败和创建失败一样抛出，没预热成功的实例不会被用上。"""
    model = create(config)
    _warm_up(model, runs, app_settings)
    return model


def _optional_model_config(config: dict, name: str) -> dict | None:
    """可选模型的加载配置：配置节 name 加上 TOTAL_CONFIG.device_id。配置节缺失、为空或
    enabled 为 false 时为 None（不加载）。两次结果相等时可以继续用已加载的实例。
    """
    section = config.get(name)
    if not config_flag(config, name, "enabled", bool(section)):
        return None
    return {
        name: dict(section),
        "TOTAL_CONFIG": {"device_id": (config.get("TOTAL_CONFIG") or {}).get("device_id", 0)},
    }


def _detector_config(config: dict) -> dict | None:
    return _optional_model_config(config, "GROUNDINGDINO_CONFIG")


def _segmentor_config(config: dict) -> dict | None:
    return _optional_model_config(config, "SEGMENT_CONFIG")


# RECIPE_DETECT_CONFIG 里加载模型用的几项。enabled、threshold 每次请求都读，改它们不用换模型
_RECIPE_MODEL_KEYS = ("model_path", "labels_path", "use_gpu")


def _recipe_classifier_config(config: dict) -> dict | None:
    """菜谱识别模型的加载配置：RECIPE_DETECT_CONFIG 里模型的几项加上 TOTAL_CONFIG.device_id。
    这一步没有开启时为 None（不加载）：enabled 和 RecipeDetectSettings 一样默认 false。
    两次结果相等时可以继续用已加载的实例。
    """
    name = "RECIPE_DETECT_CONFIG"
    if not config_flag(config, name, "enabled", False):
        return None
    section = config.get(name) or {}
    return {
        name: {key: section[key] for key in _RECIPE_MODEL_KEYS if key in section},
        "TOTAL_CONFIG": {"device_id": (config.get("TOTAL_CONFIG") or {}).get("device_id", 0)},
    }


def _detector_for(runtime: ApplicationState, config: dict):
    """config 下用哪个检测器：没有启用时为 None；配置和设备没变时继续用已加载的实例（不重新加载、
    不重新预热），否则加载并预热一个新实例。被替换的旧实例由正在处理的请求持有，跑完后释放。
    """
    detector_config = _detector_config(config)
    if detector_config is None:
        return None
    if runtime.detector is not None and _detector_config(runtime.config) == detector_config:
        return runtime.detector
    runs = runtime.settings.warmup.groundingdino_runs
    return _load_and_warm_up(create_detector, detector_config, runs, runtime.settings)


def _segmentor_for(runtime: ApplicationState, config: dict):
    """config 下用哪个分割模型，规则和 _detector_for 一样。"""
    segmentor_config = _segmentor_config(config)
    if segmentor_config is None:
        return None
    if runtime.segmentor is not None and _segmentor_config(runtime.config) == segmentor_config:
        return runtime.segmentor
    runs = runtime.settings.warmup.segment_runs
    return _load_and_warm_up(create_segmentor, segmentor_config, runs, runtime.settings)


def _recipe_classifier_for(runtime: ApplicationState, config: dict):
    """config 下用哪个菜谱识别模型，规则和 _detector_for 一样（只比较加载模型用的几项）。"""
    model_config = _recipe_classifier_config(config)
    if model_config is None:
        return None
    if runtime.recipe_classifier is not None and _recipe_classifier_config(runtime.config) == model_config:
        return runtime.recipe_classifier
    runs = runtime.settings.warmup.recipe_runs
    return _load_and_warm_up(create_recipe_classifier, model_config, runs, runtime.settings)


def _load_optional_model(runtime: ApplicationState, model_for, name: str):
    """启动时加载并预热一个可选模型，返回（实例, 失败原因）；失败只记下原因，其他服务继续运行。"""
    try:
        return model_for(runtime, runtime.config), None
    except Exception as exc:
        logger.exception("%s 初始化或预热失败：%s", name, exc)
        return None, f"{type(exc).__name__}: {exc}"


def initialize_detector(runtime: ApplicationState):
    """加载并预热可选的 GroundingDINO；失败时记录 detector_error，特征提取服务继续运行。"""
    runtime.detector = None
    runtime.detector, runtime.detector_error = _load_optional_model(runtime, _detector_for, "GroundingDINO")
    return runtime.detector


def initialize_segmentor(runtime: ApplicationState):
    """加载并预热可选的分割模型；失败时记录 segmentor_error，其他服务继续运行。"""
    runtime.segmentor = None
    runtime.segmentor, runtime.segmentor_error = _load_optional_model(runtime, _segmentor_for, "分割模型")
    return runtime.segmentor


def initialize_recipe_classifier(runtime: ApplicationState):
    """加载并预热可选的菜谱识别模型；失败时记录 recipe_classifier_error，业务搜图跳过菜谱识别，
    其他服务继续运行。
    """
    runtime.recipe_classifier = None
    runtime.recipe_classifier, runtime.recipe_classifier_error = _load_optional_model(
        runtime, _recipe_classifier_for, "菜谱识别模型"
    )
    return runtime.recipe_classifier


def initialize_embedder(runtime: ApplicationState):
    """加载并预热特征编码器。加载失败记录 initialization_error 并返回 None；预热失败记录
    warmup_error（/api/readyz 报未就绪），模型照常交给预测和检索。两种失败处理不同，
    所以这里分两步，不用 _load_and_warm_up。
    """
    try:
        embedder = create_embedder(runtime.config)
    except Exception as exc:
        runtime.initialization_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Embedding 模型初始化失败：%s", exc)
        return None
    try:
        _warm_up(embedder, runtime.settings.warmup.embedding_runs, runtime.settings)
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


def initialize_consistency(runtime: ApplicationState) -> None:
    """创建数据核对（/api/consistency）。只要有 PostgreSQL 就能用：Qdrant 或对象存储连不上时，
    核对照样跑，并在报告里说明哪边查不了。
    """
    from app.bootstrap.consistency import ConsistencyChecker

    runtime.consistency = ConsistencyChecker(runtime)


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
    GroundingDINO、分割模型和菜谱识别模型的配置和设备未变时复用实例；需要替换时，先加载并预热
    新实例，全部准备成功后再发布新配置和服务实例。任何一步失败，这次更新整体不生效，原来的配置、
    服务和模型继续使用。RESTART_ONLY_SECTIONS 改了只记一条警告：连接要重启才换。
    """
    if runtime.stopping:
        return False
    try:
        embedder = _loaded_embedder_for(runtime, snapshot.config)
        if embedder is None:
            runs = runtime.settings.warmup.embedding_runs
            embedder = _load_and_warm_up(create_embedder, snapshot.config, runs, runtime.settings)
            if runtime.retrieval is not None:
                logger.warning("EMBEDDING_CONFIG 换了模型：predict 改用 %s；检索仍用 %s（collection %s），到「模型版本」登记新版本、生成向量后再切换",
                    embedder.model_path, runtime.retrieval.model.space_id, runtime.retrieval.model.collection)
        detector = _detector_for(runtime, snapshot.config)
        segmentor = _segmentor_for(runtime, snapshot.config)
        recipe_classifier = _recipe_classifier_for(runtime, snapshot.config)
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
    previous = runtime.config
    runtime.replace_configuration(
        snapshot.config, config_info, service, embedder,
        detector=detector, segmentor=segmentor, recipe_classifier=recipe_classifier,
    )
    logger.info("Nacos配置重读成功 | data_ids=%s", list(snapshot.data_ids))
    changed = [name for name in RESTART_ONLY_SECTIONS if previous.get(name) != snapshot.config.get(name)]
    if changed:
        logger.warning("Nacos 里的 %s 改了：检索、入库和模型版本仍用启动时的连接，重启后才按新配置连接",
                       " / ".join(changed))
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
    logger.info("预热配置 | %s", runtime.settings.warmup)
    embedder = initialize_embedder(runtime)
    initialize_detector(runtime)
    initialize_segmentor(runtime)
    initialize_recipe_classifier(runtime)
    initialize_food_service(runtime, embedder)
    initialize_catalog(runtime)
    initialize_retrieval(runtime, embedder)
    initialize_models(runtime)
    initialize_consistency(runtime)
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
