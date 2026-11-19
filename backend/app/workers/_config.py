"""各后台任务入口共用的配置加载（来源和 API 相同）。"""

from __future__ import annotations

from typing import Any


def load_worker_config() -> dict[str, Any]:
    """本地 YAML 为底，再叠加 Nacos（能连上时），只加载一次，不轮询。

    后台任务的所有连接参数都取自这里（POSTGRES_CONFIG、QDRANT_CONFIG、
    IMAGE_STORAGE_CONFIG），所以配置加载不了就是错误。
    """
    from app.core.config.nacos import load_config_once
    from app.core.config.settings import settings

    return load_config_once(settings.nacos, settings.config.yaml_path)
