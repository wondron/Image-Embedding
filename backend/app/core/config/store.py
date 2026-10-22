"""Compatibility config cache for standalone scripts.

The FastAPI application owns its config snapshot through ``app.state.runtime``;
this module remains available to command-line utilities and model demos.
"""

from __future__ import annotations

from typing import Optional

from app.core.config.loader import load_config
from app.core.config.settings import settings


_cfg: Optional[dict] = None
_yaml_path: Optional[str] = None


def set_cfg(cfg: dict, yaml_path: Optional[str] = None):
    global _cfg, _yaml_path
    _cfg = cfg
    _yaml_path = yaml_path or settings.config.yaml_path


def load_cfg(yaml_path: Optional[str] = None) -> dict:
    """Load config once for scripts or application startup."""
    global _cfg, _yaml_path
    yaml_path = yaml_path or settings.config.yaml_path
    _cfg = load_config(yaml_path)
    _yaml_path = yaml_path
    return _cfg


def get_cfg() -> dict:
    """Lazy load config on first use and reuse it afterwards."""
    global _cfg
    if _cfg is None:
        load_cfg()
    return _cfg


def info() -> dict:
    """Return current config source for diagnostics."""
    return {
        "app_env": settings.application.env,
        "yaml_path": _yaml_path or settings.config.yaml_path,
        "loaded": _cfg is not None,
    }
