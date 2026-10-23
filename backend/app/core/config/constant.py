"""Static Nacos configuration used by the application."""

from __future__ import annotations
from typing import TypedDict


SERVICE_NAME = "image-embedding"
VERSION = "1.0.0"
DEFAULT_PORT = 8320
DEFAULT_YAML_PATH = "config/param/config.yaml"
LOGGER_NAME = 'roki_ai_image_embedding'
DEFAULT_TIME_OUT = 15.0

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
