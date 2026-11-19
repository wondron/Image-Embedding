"""给独立脚本用的兼容配置缓存。

FastAPI 应用通过 ``app.state.runtime`` 持有自己的配置快照；
这个模块留给命令行工具和模型演示使用。
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
    """为脚本或应用启动加载一次配置。"""
    global _cfg, _yaml_path
    yaml_path = yaml_path or settings.config.yaml_path
    _cfg = load_config(yaml_path)
    _yaml_path = yaml_path
    return _cfg


def get_cfg() -> dict:
    """第一次使用时延迟加载配置，之后复用。"""
    global _cfg
    if _cfg is None:
        load_cfg()
    return _cfg


def info() -> dict:
    """返回当前配置的来源，用于诊断。"""
    return {
        "app_env": settings.application.env,
        "yaml_path": _yaml_path or settings.config.yaml_path,
        "loaded": _cfg is not None,
    }
