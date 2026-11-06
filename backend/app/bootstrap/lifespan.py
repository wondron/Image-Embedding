from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.bootstrap.state import ApplicationState
from app.core.config.nacos import NacosConfigProvider, NacosConfigSnapshot
from app.core.config.settings import Settings, settings
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


def create_food_service(config: dict):
    from app.services.predict.food_service import FoodService
    return FoodService(config)


def warmup_models(service, app_settings: Settings = settings) -> None:
    logger.info("Warmup enabled: %s", app_settings.warmup.enabled)
    if not app_settings.warmup.enabled:
        return

    if getattr(service, "embeddingTool", None):
        times = app_settings.warmup.embedding_runs
        logger.info("预热中 | embedding=%d 次", times)
        service.embeddingTool.warmup(times)
    logger.info("预热配置 | warmup=%s", app_settings.warmup)


def _retrieval_enabled() -> bool:
    """Host-side debug: set RETRIEVAL_ENABLED=0 to skip PG/Qdrant init (predict-only)."""
    import os

    raw = os.getenv("RETRIEVAL_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _is_infra_unreachable(exc: BaseException) -> bool:
    """DNS / refused / timeout while reaching compose service names from the host."""
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


def _retrieval_local_debug_hint(settings: Settings) -> str:
    return (
        "host-side debug: docker DNS names (postgres/qdrant) only work inside compose; "
        "set POSTGRES_HOST=localhost DATABASE_URL=postgresql+psycopg://"
        f"{settings.postgres.user}:***@localhost:5432/{settings.postgres.database} "
        "QDRANT_HOST=localhost QDRANT_URL=http://localhost:6333 "
        "(and `docker compose up -d postgres qdrant`), "
        "or RETRIEVAL_ENABLED=0 for predict-only"
    )


def initialize_retrieval(runtime: ApplicationState) -> None:
    """Build the retrieval stack; on failure keep serving predict and record why."""
    runtime.retrieval = None
    runtime.retrieval_error = None
    runtime.retrieval_error_code = None
    if not _retrieval_enabled():
        runtime.retrieval_error = "RETRIEVAL_ENABLED=0"
        runtime.retrieval_error_code = "RETRIEVAL_DISABLED"
        logger.info("Retrieval stack skipped | RETRIEVAL_ENABLED=0 | postgres_host=%s | qdrant=%s", runtime.settings.postgres.host, runtime.settings.qdrant.url,)
        return

    from app.bootstrap.retrieval_stack import build_retrieval_stack
    from app.services.retrieval.errors import RetrievalError

    try:
        runtime.retrieval = build_retrieval_stack(runtime.config, runtime.settings)
    except Exception as exc:
        if isinstance(exc, RetrievalError):
            runtime.retrieval_error = f"{exc.code}: {exc.detail}"
            runtime.retrieval_error_code = exc.code
        else:
            runtime.retrieval_error = f"{type(exc).__name__}: {exc}"
        if _is_infra_unreachable(exc):
            logger.warning("Retrieval stack unavailable (infra) | %s | host=%s | %s", runtime.retrieval_error, runtime.settings.postgres.host, _retrieval_local_debug_hint(runtime.settings))
        else:
            logger.exception("Retrieval stack initialization failed: %s", exc)
        return

    model = runtime.retrieval.model
    logger.info("Retrieval stack ready  qdrant=%s  postgres=%s/%s  collection=%s  model=%s",
        runtime.settings.qdrant.url, runtime.settings.postgres.host, runtime.settings.postgres.database,
        model.collection, model.space_id)


def initialize_food_service(runtime: ApplicationState) -> None:
    try:
        runtime.food_service = create_food_service(runtime.config)
    except Exception as exc:
        runtime.initialization_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Food service initialization failed: %s", exc)
        return
    try:
        warmup_models(runtime.food_service, runtime.settings)
    except Exception as exc:
        runtime.warmup_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Warmup failed: %s", exc)


def apply_nacos_config_update(runtime: ApplicationState, provider: NacosConfigProvider, snapshot: NacosConfigSnapshot,) -> bool:
    """Build and warm a replacement before atomically publishing it."""
    if runtime.stopping:
        return False
    try:
        service = create_food_service(snapshot.config)
        warmup_models(service, runtime.settings)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        runtime.record_config_reload_error(error)
        logger.exception("Nacos配置重读失败 | profile=%s | error=%s", snapshot.profile["profile"], error)
        return False

    config_info = {
        "app_env": runtime.settings.application.env,
        "yaml_path": runtime.settings.config.yaml_path,
        "loaded": True,
        **provider.source_info(snapshot),
    }
    runtime.replace_configuration(snapshot.config, config_info, service)
    logger.info("Nacos配置重读成功| profile=%s | data_ids=%s", snapshot.profile["profile"], snapshot.profile["data_id_list"],)
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
    logger.info("App starting ENV=%s cfg=%s", runtime.settings.application.env, runtime.config_info,)
    initialize_food_service(runtime)
    initialize_retrieval(runtime)
    config_provider.start_polling(lambda snapshot: apply_nacos_config_update(runtime, config_provider, snapshot))
    try:
        yield
    finally:
        try:
            await runtime.close()
        except Exception as exc:
            logger.exception("Application resource cleanup failed: %s", exc)
        logger.info("App shutting down")
