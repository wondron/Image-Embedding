"""Shared config loading for worker entry points (same sources as the API)."""

from __future__ import annotations

from typing import Any


def load_worker_config() -> dict[str, Any]:
    """Local YAML base + Nacos (when reachable), loaded once without polling.

    Workers take every connection parameter from it (POSTGRES_CONFIG, QDRANT_CONFIG,
    IMAGE_STORAGE_CONFIG), so a config that cannot be loaded is an error.
    """
    from app.core.config.nacos import load_config_once
    from app.core.config.settings import settings

    return load_config_once(settings.nacos, settings.config.yaml_path)
