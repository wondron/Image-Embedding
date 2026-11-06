"""Shared config loading for worker entry points (same sources as the API)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("workers")


def load_worker_config() -> dict[str, Any]:
    """Local YAML base + Nacos (when reachable), loaded once without polling.

    Falls back to {} if neither source is usable: workers can still run on the
    environment Settings (PG / Qdrant endpoints) alone.
    """
    from app.core.config.nacos import NacosConfigProvider
    from app.core.config.settings import settings

    provider = NacosConfigProvider(settings.nacos)
    try:
        config, source = provider.load(settings.config.yaml_path)
        logger.info("worker config loaded | source=%s", source.get("source"))
        return config
    except Exception as exc:
        logger.warning("config not loaded, using defaults: %s: %s", type(exc).__name__, exc)
        return {}
    finally:
        provider.close()
