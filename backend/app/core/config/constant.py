"""项目常量：项目定下来后基本不变的值。

参数只有两个来源：这里，以及生效配置（Nacos 逐键覆盖本地 config/param/config.yaml）。
密码、连接参数、模型等随环境变化的配置都放在 Nacos / YAML，不放这里，也不走环境变量。
"""

from __future__ import annotations
from typing import TypedDict


SERVICE_NAME = "image-embedding"
VERSION = "1.0.0"
DEFAULT_PORT = 8320
DEFAULT_YAML_PATH = "config/param/config.yaml"
DEFAULT_ENUMS_PATH = "config/param/enums.yaml"  # 枚举取值（app.core.config.enum_store），不走 Nacos
LOGGER_NAME = 'roki_ai_image_embedding'
DEFAULT_TIME_OUT = 15.0

# PostgreSQL 元数据库：host / user / password / database 在配置的 POSTGRES_CONFIG 里
PG_DRIVER = "postgresql+psycopg"
PG_DEFAULT_PORT = 5432
PG_CONNECT_TIMEOUT_SECONDS = 5.0

# 对象存储：IMAGE_STORAGE_CONFIG 里没给的项用这些值（默认是 mock 驱动，不真正上传）
IMAGE_STORAGE_DEFAULT_BUCKET = "image-embedding"
IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL = "https://cdn.example.local/image-embedding"
IMAGE_STORAGE_DEFAULT_REGION = "us-east-1"

class NacosProfile(TypedDict):
    profile: str
    server_addresses: str
    namespace: str
    username: str
    password: str
    group: str
    data_id_list: list[str]

NACOS_ENABLED = True
NACOS_POLL_ENABLED = True
NACOS_CONNECT_TIMEOUT_SECONDS = 2.0
NACOS_REQUEST_TIMEOUT_SECONDS = 3.0
NACOS_POLL_INTERVAL_SECONDS = 10.0
NACOS_RETRY_INTERVAL_SECONDS = 5.0
NACOS_CONFIG_PROFILES: tuple[NacosProfile, ...] = (
    {
        "profile": "prod",
        "server_addresses": "http://nacos.myroki.com:8848",
        "namespace": "239acdd2-c154-4d07-ba96-6d52ebc0dccb",
        "username": "roki-ai-prod",
        "password": "IsAbprjbEGj2ME77jxiOR",
        "group": "ROKI_AI_GROUP",
        "data_id_list": ["roki-ai-image-search"],
    },
    {
        "profile": "test",
        "server_addresses": "http://nacos.myroki.com:8848",
        "namespace": "adfc3ec2-a1ea-4b31-9b1a-fafb4228c8ca",
        "username": "roki-ai-test",
        "password": "Q65ILEBfsAmqnPbJqcSig",
        "group": "ROKI_AI_GROUP",
        "data_id_list": ["roki-ai-image-search"],
    },
)
