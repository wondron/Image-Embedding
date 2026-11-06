"""Shared config loading for worker entry points."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("workers")


def load_worker_config() -> dict[str, Any]:
    """Local yaml config (QDRANT_CONFIG etc.); {} if it cannot be loaded."""
    try:
        from app.core.config.loader import load_config

        return load_config() or {}
    except Exception as exc:
        logger.warning("config not loaded, using defaults: %s: %s", type(exc).__name__, exc)
        return {}
