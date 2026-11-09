"""进程级设置，以及从生效配置里解析连接参数。

参数只有两个来源：
- app.core.config.constant：项目定下来后基本不变的值（服务名、端口、Nacos 接入点、驱动名……）；
- 生效配置：Nacos 逐键覆盖本地 YAML（config/param/config.yaml），见 app.core.config.nacos。
  密码、连接参数、模型等随环境变化的都在这里。

Settings 只由常量组成，import 时创建一次。连接参数在用到时从生效配置解析：
PostgresSettings.from_config（POSTGRES_CONFIG）、ImageStorageSettings.from_config
（IMAGE_STORAGE_CONFIG）；Qdrant 直接用 QDRANT_CONFIG（app.clients.qdrant.QdrantConnection）。

环境变量只剩 Nacos 自己的引导参数（NACOS_ENV 选 prod / test 等）：它们决定去哪个
namespace 读配置，没法放进配置本身。
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from sqlalchemy.engine import URL

from app.core.config.constant import (
    DEFAULT_ENUMS_PATH,
    DEFAULT_PORT,
    DEFAULT_TIME_OUT,
    DEFAULT_YAML_PATH,
    IMAGE_STORAGE_DEFAULT_BUCKET,
    IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL,
    IMAGE_STORAGE_DEFAULT_REGION,
    LOGGER_NAME,
    NACOS_CONFIG_PROFILES,
    NACOS_CONNECT_TIMEOUT_SECONDS,
    NACOS_ENABLED,
    NACOS_POLL_ENABLED,
    NACOS_POLL_INTERVAL_SECONDS,
    NACOS_REQUEST_TIMEOUT_SECONDS,
    NACOS_RETRY_INTERVAL_SECONDS,
    PG_CONNECT_TIMEOUT_SECONDS,
    PG_DEFAULT_PORT,
    PG_DRIVER,
    SERVICE_NAME,
    VERSION,
    NacosProfile,
)

_T = TypeVar("_T")
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def _parse_bool(value: Any, what: str) -> bool:
    """环境变量和配置开关共用一套规则：true/1/yes/on、false/0/no/off/空；其他值报错并指出是哪一项。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{what} must be true or false, got {value!r}")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else _parse_bool(value, name)


def _nacos_profile_name() -> str:
    """NACOS_ENV 选中的 profile（prod / test），也就是本进程的运行环境。"""
    return os.getenv("NACOS_ENV", "").strip().lower() or "prod"


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    service_name: str = SERVICE_NAME
    # 和 Nacos profile 一致（prod / test），日志、/version、/config_info 都报这个
    env: str = field(default_factory=_nacos_profile_name)
    version: str = VERSION
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    reload: bool = True


@dataclass(frozen=True, slots=True)
class ConfigSettings:
    yaml_path: str = DEFAULT_YAML_PATH
    enums_path: str = DEFAULT_ENUMS_PATH


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
    profile: str = field(default_factory=_nacos_profile_name)
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


# -- 生效配置（Nacos / 本地 YAML）里的连接参数 ------------------------------------------


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = config.get(name)
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise ValueError(f"{name} must be a mapping, got {type(section).__name__}")
    return section


def _text(section: Mapping[str, Any], key: str) -> str:
    """YAML 标量不一定是字符串（不加引号的 123456 会解析成 int），统一转成去空白的 str。"""
    value = section.get(key)
    return "" if value is None else str(value).strip()


def _number(section: Mapping[str, Any], name: str, key: str, default: _T, cast: Callable[[Any], _T]) -> _T:
    value = section.get(key)
    if value is None or value == "":
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}.{key} must be a number, got {value!r}") from None


def _flag(section: Mapping[str, Any], name: str, key: str, default: bool) -> bool:
    value = section.get(key)
    return default if value is None else _parse_bool(value, f"{name}.{key}")


def config_flag(config: Mapping[str, Any], name: str, key: str, default: bool) -> bool:
    """生效配置里的开关，如 config_flag(config, "TOTAL_CONFIG", "retrieval_enabled", True)。"""
    return _flag(_section(config, name), name, key, default)


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """元数据库连接，取自生效配置的 POSTGRES_CONFIG。"""

    host: str
    user: str
    database: str
    password: str = field(default="", repr=False)
    port: int = PG_DEFAULT_PORT
    connect_timeout: float = PG_CONNECT_TIMEOUT_SECONDS

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> PostgresSettings:
        name = "POSTGRES_CONFIG"
        section = _section(config, name)
        required = {key: _text(section, key) for key in ("host", "user", "database")}
        missing = [f"{name}.{key}" for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"{', '.join(missing)} missing from the configuration (Nacos / local YAML)")
        password = section.get("password")
        return cls(
            host=required["host"],
            user=required["user"],
            database=required["database"],
            password="" if password is None else str(password),
            port=_number(section, name, "port", PG_DEFAULT_PORT, int),
            connect_timeout=_number(section, name, "connect_timeout", PG_CONNECT_TIMEOUT_SECONDS, float),
        )

    def url(self) -> URL:
        """应用、workers、Alembic 共用的唯一连接串；URL.create 负责转义用户名和密码里的特殊字符。"""
        return URL.create(
            PG_DRIVER,
            username=self.user,
            password=self.password or None,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(frozen=True, slots=True)
class ImageStorageSettings:
    """Image storage: originals / thumbnails / staging files (contract §5), from IMAGE_STORAGE_CONFIG.

    Any S3-compatible service (AWS S3, MinIO, OSS). use_boto3 false (the default) is the
    mock driver: URLs are built from public_base_url + key and nothing is uploaded. Keys
    missing from the section fall back to the IMAGE_STORAGE_DEFAULT_* constants.

    ASSUMPTION A8: public_base_url is the CDN/bucket HTTPS prefix baked into
    original_url / thumb_url / staged_url. Keys never include scene/category.
    """

    use_boto3: bool = False
    endpoint_url: str = ""  # S3 兼容服务（MinIO / OSS）的地址；AWS S3 留空
    bucket: str = IMAGE_STORAGE_DEFAULT_BUCKET
    public_base_url: str = IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL
    region: str = IMAGE_STORAGE_DEFAULT_REGION
    access_key_id: str = field(default="", repr=False)
    secret_access_key: str = field(default="", repr=False)
    mock_mirror_dir: str = ""  # mock 驱动另存一份字节的目录；空表示只在内存里

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ImageStorageSettings:
        name = "IMAGE_STORAGE_CONFIG"
        section = _section(config, name)
        return cls(
            use_boto3=_flag(section, name, "use_boto3", False),
            endpoint_url=_text(section, "endpoint_url"),
            bucket=_text(section, "bucket") or IMAGE_STORAGE_DEFAULT_BUCKET,
            public_base_url=(_text(section, "public_base_url") or IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL).rstrip("/"),
            region=_text(section, "region") or IMAGE_STORAGE_DEFAULT_REGION,
            access_key_id=_text(section, "access_key_id"),
            secret_access_key=_text(section, "secret_access_key"),
            mock_mirror_dir=_text(section, "mock_mirror_dir"),
        )


@dataclass(frozen=True, slots=True)
class ImageDownloadSettings:
    """Remote image downloads (browser proxy and model-input URLs), from IMAGE_DOWNLOAD_CONFIG.

    Only public hosts are fetched. trusted_networks are treated as public too: keep it empty
    in production; a machine behind a fake-IP proxy lists 198.18.0.0/15 and 2001:2::/48.
    """

    trusted_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ImageDownloadSettings:
        name = "IMAGE_DOWNLOAD_CONFIG"
        raw = _section(config, name).get("trusted_networks") or []
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"{name}.trusted_networks must be a list of CIDR blocks, got {raw!r}")
        networks = []
        for item in raw:
            try:
                networks.append(ipaddress.ip_network(str(item).strip(), strict=False))
            except ValueError:
                raise ValueError(f"{name}.trusted_networks: invalid network {item!r}") from None
        return cls(trusted_networks=tuple(networks))


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
