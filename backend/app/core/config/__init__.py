"""应用配置的加载和兼容辅助函数。"""

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
