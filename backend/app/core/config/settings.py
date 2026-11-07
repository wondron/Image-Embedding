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


_NACOS_ENV_OVERRIDES = (
    ("server_addresses", "NACOS_SERVER_ADDR"),
    ("namespace", "NACOS_NAMESPACE"),
    ("username", "NACOS_USERNAME"),
    ("password", "NACOS_PASSWORD"),
    ("group", "NACOS_GROUP"),
)


def _nacos_profiles() -> tuple[NacosProfile, ...]:
    """Built-in profiles with NACOS_* environment overrides (credentials belong in env)."""
    overrides = {key: os.getenv(env, "").strip() for key, env in _NACOS_ENV_OVERRIDES}
    data_ids = [item.strip() for item in os.getenv("NACOS_DATA_IDS", "").split(",") if item.strip()]
    profiles = []
    for base in NACOS_CONFIG_PROFILES:
        profile = dict(base)
        profile.update({key: value for key, value in overrides.items() if value})
        if data_ids:
            profile["data_id_list"] = data_ids
        profiles.append(profile)
    return tuple(profiles)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class NacosSettings:
    profile: str = field(
        default_factory=lambda: os.getenv("NACOS_ENV", "").strip().lower() or "prod"
    )
    enabled: bool = field(default_factory=lambda: _env_bool("NACOS_ENABLED", NACOS_ENABLED))
    poll_enabled: bool = field(default_factory=lambda: _env_bool("NACOS_POLL_ENABLED", NACOS_POLL_ENABLED))
    connect_timeout_seconds: float = NACOS_CONNECT_TIMEOUT_SECONDS
    request_timeout_seconds: float = NACOS_REQUEST_TIMEOUT_SECONDS
    poll_interval_seconds: float = NACOS_POLL_INTERVAL_SECONDS
    retry_interval_seconds: float = NACOS_RETRY_INTERVAL_SECONDS
    profiles: tuple[NacosProfile, ...] = field(default_factory=_nacos_profiles)

    def __post_init__(self) -> None:
        if self.profile not in {"prod", "test"}:
            raise ValueError("NACOS_ENV must be 'prod' or 'test'")


@dataclass(frozen=True, slots=True)
class QdrantSettings:
    """本地/compose 默认；可用环境变量覆盖。collection / 表结构最终归数据规范。
    向量客户端实现: app.clients.qdrant.QdrantStore；此处仅连接与 collection 形状。
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
class PostgresSettings:
    """PG v0.1 metadata DB (compose service `postgres`). Prefer DATABASE_URL when set."""

    host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "postgres"))
    port: int = field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5432")))
    user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER", "image_emb"))
    password: str = field(
        default_factory=lambda: os.getenv("POSTGRES_PASSWORD", "image_emb")
    )
    database: str = field(
        default_factory=lambda: os.getenv("POSTGRES_DATABASE", "image_embedding")
    )
    url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://image_emb:image_emb@postgres:5432/image_embedding",
        )
    )

    def dsn(self) -> str:
        """SQLAlchemy URL; DATABASE_URL wins when non-empty."""
        if self.url and self.url.strip():
            return self.url.strip()
        return (
            f"postgresql+psycopg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass(frozen=True, slots=True)
class ImageStorageSettings:
    """Image storage: originals / thumbnails / staging files (contract §5).

    Any S3-compatible service (AWS S3, MinIO, OSS); env vars keep the S3_* names.
    Secrets only via env; mock by default.

    ASSUMPTION A8: public_base_url is the CDN/bucket HTTPS prefix baked into
    original_url / thumb_url / staged_url. Keys never include scene/category.
    """

    endpoint_url: str = field(
        default_factory=lambda: os.getenv("S3_ENDPOINT_URL", "").strip()
    )
    bucket: str = field(
        default_factory=lambda: os.getenv("S3_BUCKET", "image-embedding").strip()
    )
    public_base_url: str = field(
        default_factory=lambda: os.getenv(
            "S3_PUBLIC_BASE_URL", "https://cdn.example.local/image-embedding"
        ).strip().rstrip("/")
    )
    region: str = field(
        default_factory=lambda: os.getenv("S3_REGION", "us-east-1").strip()
    )
    access_key_env: str = field(
        default_factory=lambda: os.getenv("S3_ACCESS_KEY_ENV", "S3_ACCESS_KEY_ID")
    )
    secret_key_env: str = field(
        default_factory=lambda: os.getenv("S3_SECRET_KEY_ENV", "S3_SECRET_ACCESS_KEY")
    )
    # When true, adapter may attempt boto3; default mock (no network).
    use_boto3: bool = field(
        default_factory=lambda: os.getenv("S3_USE_BOTO3", "").strip().lower()
        in {"1", "true", "yes", "y", "on"}
    )
    # Local mock also optionally mirrors bytes under this dir (empty = memory only).
    mock_mirror_dir: str = field(
        default_factory=lambda: os.getenv("S3_MOCK_MIRROR_DIR", "").strip()
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
    postgres: PostgresSettings = field(default_factory=PostgresSettings)
    image_storage: ImageStorageSettings = field(default_factory=ImageStorageSettings)
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
