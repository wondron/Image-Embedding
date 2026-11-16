"""Application configuration loading and compatibility helpers."""

from app.core.config.constant import NACOS_DATA_ID_LIST
from app.core.config.settings import (
    NacosSettings,
    Settings,
    settings,
)

__all__ = [
    "NACOS_DATA_ID_LIST",
    "NacosSettings",
    "Settings",
    "settings",
]
