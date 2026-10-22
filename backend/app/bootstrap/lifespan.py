from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.bootstrap.state import ApplicationState
from app.core.config.nacos import (
    NacosConfigProvider,
    NacosConfigSnapshot,
)

from app.core.config.settings import Settings, settings
from app.core.observability.logging import setup_logger


logger = logging.getLogger("main.lifespan")


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
    # Keep model dependencies out of module import and initialize them at startup.
    from app.domain.food_service import FoodService

    return FoodService(config)


def warmup_models(service, app_settings: Settings = settings) -> None:
    logger.info("Warmup enabled: %s", app_settings.warmup.enabled)
    if not app_settings.warmup.enabled:
        return

    # if getattr(service, "segmentor", None):
    #     logger.info("预热中 | segment=%d 次", app_settings.warmup.segment_runs)
    #     service.segmentor.warmup(app_settings.warmup.segment_runs)

    logger.info("预热配置 | warmup=%s", app_settings.warmup)

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


def apply_nacos_config_update(
    runtime: ApplicationState,
    provider: NacosConfigProvider,
    snapshot: NacosConfigSnapshot,
) -> bool:
    """Build and warm a replacement before atomically publishing it."""
    if runtime.stopping:
        return False
    try:
        service = create_food_service(snapshot.config)
        warmup_models(service, runtime.settings)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        runtime.record_config_reload_error(error)
        logger.exception(
            "Nacos configuration reload rejected | profile=%s | error=%s",
            snapshot.profile["profile"],
            error,
        )
        return False

    config_info = {
        "app_env": runtime.settings.application.env,
        "yaml_path": runtime.settings.config.yaml_path,
        "loaded": True,
        **provider.source_info(snapshot),
    }
    runtime.replace_configuration(snapshot.config, config_info, service)
    logger.info(
        "Nacos configuration reload applied | profile=%s | data_ids=%s",
        snapshot.profile["profile"],
        snapshot.profile["data_id_list"],
    )
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
    logger.info("App starting | APP_ENV=%s | cfg=%s", runtime.settings.application.env, runtime.config_info,)
    initialize_food_service(runtime)
    config_provider.start_polling(lambda snapshot: apply_nacos_config_update(runtime, config_provider, snapshot))
    try:
        yield
    finally:
        try:
            await runtime.close()
        except Exception as exc:
            logger.exception("Application resource cleanup failed: %s", exc)
        logger.info("App shutting down")
