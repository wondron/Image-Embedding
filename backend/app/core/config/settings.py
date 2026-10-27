from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.config.constant import (
    NACOS_CONFIG_PROFILES,
    NACOS_CONNECT_TIMEOUT_SECONDS,
    NACOS_ENABLED,
    NACOS_POLL_ENABLED,
    NACOS_POLL_INTERVAL_SECONDS,
    NACOS_REQUEST_TIMEOUT_SECONDS,
    NACOS_RETRY_INTERVAL_SECONDS,
    SERVICE_NAME,
    VERSION,
    DEFAULT_PORT,
    DEFAULT_YAML_PATH,
    LOGGER_NAME,
    DEFAULT_TIME_OUT,
    NacosProfile,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    version: str = VERSION
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    reload: bool = True


@dataclass(frozen=True, slots=True)
class ConfigSettings:
    yaml_path: str = DEFAULT_YAML_PATH


@dataclass(frozen=True, slots=True)
class CorsSettings:
    origins: tuple[str, ...] = ("*",)
    allow_credentials: bool = False


@dataclass(frozen=True, slots=True)
class WarmupSettings:
    enabled: bool = True
    embedding_runs: int = 3


@dataclass(frozen=True, slots=True)
class PathSettings:
    output_dir: str = "output"
    save_images_dir: str = "save_images"
    save_images_enabled: bool = field(
        default_factory=lambda: _env_bool("SAVE_IMAGES_ENABLED", False)
    )


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    index: str = LOGGER_NAME
    save_to_file: bool = True
    save_k8s_file: bool = False


@dataclass(frozen=True, slots=True)
class PredictSettings:
    http_timeout: float = DEFAULT_TIME_OUT
    max_image_bytes: int = 15 * 2**20


@dataclass(frozen=True, slots=True)
class HttpClientSettings:
    min_connections: int = 40
    max_connections: int = 200
    min_keepalive_connections: int = 10
    max_keepalive_connections: int = 15
    timeout_seconds: float = DEFAULT_TIME_OUT


@dataclass(frozen=True, slots=True)
class ConcurrencySettings:
    default_max_workers: int = field(
        default_factory=lambda: min(32, (os.cpu_count() or 1) + 4)
    )
    default_max_concurrency: int = 400
    max_concurrency_limit: int = 500
    default_acquire_timeout: float = DEFAULT_TIME_OUT
    thread_name_prefix: str = "det-pool"


@dataclass(frozen=True, slots=True)
class NacosSettings:
    profile: str = field(
        default_factory=lambda: os.getenv("NACOS_ENV", "").strip().lower() or "prod"
    )
    enabled: bool = NACOS_ENABLED
    poll_enabled: bool = NACOS_POLL_ENABLED
    connect_timeout_seconds: float = NACOS_CONNECT_TIMEOUT_SECONDS
    request_timeout_seconds: float = NACOS_REQUEST_TIMEOUT_SECONDS
    poll_interval_seconds: float = NACOS_POLL_INTERVAL_SECONDS
    retry_interval_seconds: float = NACOS_RETRY_INTERVAL_SECONDS
    profiles: tuple[NacosProfile, ...] = field(default_factory=lambda: NACOS_CONFIG_PROFILES)

    def __post_init__(self) -> None:
        if self.profile not in {"prod", "test"}:
            raise ValueError("NACOS_ENV must be 'prod' or 'test'")


@dataclass(frozen=True, slots=True)
class QdrantSettings:
    """本地/compose 默认；可用环境变量覆盖。collection / 表结构最终归数据规范。
    向量客户端实现归算法逻辑 (domain.common.qdrant_store)；此处仅连接与 collection 形状。
    """

    host: str = field(default_factory=lambda: os.getenv("QDRANT_HOST", "qdrant"))
    port: int = field(default_factory=lambda: int(os.getenv("QDRANT_PORT", "6333")))
    url: str = field(
        default_factory=lambda: os.getenv("QDRANT_URL", "http://qdrant:6333")
    )
    collection: str = field(
        default_factory=lambda: os.getenv("QDRANT_COLLECTION", "image_embeddings")
    )
    vector_size: int = field(
        default_factory=lambda: int(os.getenv("QDRANT_VECTOR_SIZE", "1024"))
    )
    distance: str = field(
        default_factory=lambda: os.getenv("QDRANT_DISTANCE", "Cosine")
    )
    prefer_grpc: bool = field(
        default_factory=lambda: os.getenv("QDRANT_PREFER_GRPC", "1").strip().lower() in {"1", "true", "yes", "y", "on"}
    )


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    """本地/compose 默认；勿把生产密钥写进仓库。表结构归数据规范。"""

    host: str = field(default_factory=lambda: os.getenv("MYSQL_HOST", "mysql"))
    port: int = field(default_factory=lambda: int(os.getenv("MYSQL_PORT", "3306")))
    user: str = field(default_factory=lambda: os.getenv("MYSQL_USER", "image_emb"))
    password: str = field(
        default_factory=lambda: os.getenv("MYSQL_PASSWORD", "image_emb")
    )
    database: str = field(
        default_factory=lambda: os.getenv("MYSQL_DATABASE", "image_embedding")
    )

@dataclass(frozen=True, slots=True)
class Settings:
    application: ApplicationSettings = field(default_factory=ApplicationSettings)
    config: ConfigSettings = field(default_factory=ConfigSettings)
    cors: CorsSettings = field(default_factory=CorsSettings)
    warmup: WarmupSettings = field(default_factory=WarmupSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    predict: PredictSettings = field(default_factory=PredictSettings)
    http_client: HttpClientSettings = field(default_factory=HttpClientSettings)
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    nacos: NacosSettings = field(default_factory=NacosSettings)
    qdrant: QdrantSettings = field(default_factory=QdrantSettings)
    mysql: MySQLSettings = field(default_factory=MySQLSettings)
    allowed_content_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "image/jpg",
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/gif",
                "application/octet-stream",
            }
        )
    )


settings = Settings()
