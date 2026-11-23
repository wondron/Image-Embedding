"""进程级设置，以及从生效配置里解析连接参数。

参数有三个来源：
- app.core.config.constant：项目定下来后基本不变的值（服务名、端口、Nacos 读哪些 dataId、驱动名……）；
- 环境变量：NACOS_ENV（环境标识）和 Nacos 接入参数 NACOS_*（NacosSettings）。它们决定去哪里读配置，
  没法放进配置本身。本地调试写在 backend/.env（模板 .env.example，import 本模块时读入），
  docker compose 部署写在 compose 文件的 environment 里（镜像里没有 .env）；
- 生效配置：Nacos 逐键覆盖本地 YAML（config/param/config.yaml），见 app.core.config.nacos。
  密码、连接参数、模型等随环境变化的都在这里。

Settings 由常量和环境变量组成，import 时创建一次。连接参数在用到时从生效配置解析：
PostgresSettings.from_config（POSTGRES_CONFIG）、ImageStorageSettings.from_config
（IMAGE_STORAGE_CONFIG）；Qdrant 直接用 QDRANT_CONFIG（app.clients.qdrant.QdrantConnection）。
"""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from dotenv import load_dotenv
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
    NACOS_DATA_ID_LIST,
    PG_CONNECT_TIMEOUT_SECONDS,
    PG_DEFAULT_PORT,
    PG_DRIVER,
    SERVICE_NAME,
    VERSION,
)
from app.core.config.key_prefix import normalize_key_prefix

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"  # backend/.env


def load_env_file(path: Path = ENV_FILE) -> None:
    """把 .env 里的变量补进环境变量（本地调试用）。已经设置的不覆盖：命令行、compose 传进来的优先。"""
    load_dotenv(path, override=False)


load_env_file()  # 必须在下面 Settings() 读环境变量之前

_T = TypeVar("_T")
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}
_ENVS = ("prod", "test")


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


def _env_text(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _env_flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    return default if value is None else _parse_bool(value, name)


def _env_seconds(env: Mapping[str, str], name: str, default: float) -> float:
    text = _env_text(env, name)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {text!r}") from None


def _env_name() -> str:
    """NACOS_ENV：本进程的运行环境（prod / test），未设置或为空时按 prod。"""
    return os.getenv("NACOS_ENV", "").strip().lower() or "prod"


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    service_name: str = SERVICE_NAME
    # 环境标识（NACOS_ENV）：日志、/version、/config_info 都报这个，reset_data 在 prod 下拒绝执行。
    # 它只是标识，连哪个 Nacos 环境看 NACOS_NAMESPACE 等，两者要成套设置
    env: str = field(default_factory=_env_name)
    version: str = VERSION
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    reload: bool = True

    def __post_init__(self) -> None:
        if self.env not in _ENVS:
            raise ValueError(f"NACOS_ENV must be 'prod' or 'test', got {self.env!r}")


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
    groundingdino_runs: int = 3
    segment_runs: int = 3
    recipe_runs: int = 3


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


_NACOS_SECONDS = (  # (字段, 环境变量)
    ("poll_interval_seconds", "NACOS_POLL_INTERVAL_SECONDS"),
    ("retry_interval_seconds", "NACOS_RETRY_INTERVAL_SECONDS"),
    ("connect_timeout_seconds", "NACOS_CONNECT_TIMEOUT_SECONDS"),
    ("request_timeout_seconds", "NACOS_REQUEST_TIMEOUT_SECONDS"),
)


@dataclass(frozen=True, slots=True)
class NacosSettings:
    """Nacos 接入参数。读哪些 dataId 在 constant.NACOS_DATA_ID_LIST，其余都来自环境变量（from_env）：

    NACOS_ENABLED          是否读 Nacos。默认 false：只用本地 YAML，下面的都可以不设
    NACOS_SERVER_ADDR      地址，如 http://nacos.example.com:8848  ┐
    NACOS_NAMESPACE        namespace ID                            ├ 开启时必填
    NACOS_GROUP            group                                   ┘
    NACOS_USERNAME / NACOS_PASSWORD   账号密码；Nacos 没开鉴权时留空
    NACOS_POLL_ENABLED     启动后是否定时重读（默认 true）
    NACOS_POLL_INTERVAL_SECONDS / NACOS_RETRY_INTERVAL_SECONDS     重读间隔 / 失败后重试间隔（默认 10 / 5）
    NACOS_CONNECT_TIMEOUT_SECONDS / NACOS_REQUEST_TIMEOUT_SECONDS  HTTP 连接 / 读取超时（默认 2 / 3）
    """

    enabled: bool = False
    server_addr: str = ""
    namespace: str = ""
    group: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    data_ids: tuple[str, ...] = tuple(NACOS_DATA_ID_LIST)
    poll_enabled: bool = True
    poll_interval_seconds: float = 10.0
    retry_interval_seconds: float = 5.0
    connect_timeout_seconds: float = 2.0
    request_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        for key, env_name in _NACOS_SECONDS:
            value = getattr(self, key)
            if not (value > 0 and math.isfinite(value)):  # NaN、inf 也不行
                raise ValueError(f"{env_name} must be a positive number of seconds, got {value!r}")
        if not self.enabled:
            return
        required = (("NACOS_SERVER_ADDR", self.server_addr), ("NACOS_NAMESPACE", self.namespace),
                    ("NACOS_GROUP", self.group))
        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(
                f"NACOS_ENABLED=true 但没有设置 {', '.join(missing)}：本地调试写在 backend/.env，"
                "docker compose 写在 environment 里；不读 Nacos 就设 NACOS_ENABLED=false"
            )
        if not self.data_ids:
            raise ValueError("NACOS_DATA_ID_LIST in app/core/config/constant.py is empty")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> NacosSettings:
        env = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            enabled=_env_flag(env, "NACOS_ENABLED", defaults.enabled),
            server_addr=_env_text(env, "NACOS_SERVER_ADDR"),
            namespace=_env_text(env, "NACOS_NAMESPACE"),
            group=_env_text(env, "NACOS_GROUP"),
            username=_env_text(env, "NACOS_USERNAME"),
            password=_env_text(env, "NACOS_PASSWORD"),
            poll_enabled=_env_flag(env, "NACOS_POLL_ENABLED", defaults.poll_enabled),
            **{key: _env_seconds(env, name, getattr(defaults, key)) for key, name in _NACOS_SECONDS},
        )


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
    """图片存储：原图 / 缩略图 / 暂存文件（契约 §5），取自 IMAGE_STORAGE_CONFIG。

    支持任何兼容 S3 的服务（AWS S3、MinIO、OSS）。use_boto3 为 false（默认）时是
    mock 驱动：URL 由 public_base_url + key 拼出，不上传任何东西。配置里没写的键
    退回到 IMAGE_STORAGE_DEFAULT_* 常量。

    假设 A8：public_base_url 是写进 original_url / thumb_url / staged_url 的
    CDN / 存储桶 HTTPS 前缀，不是对象 key 的前缀。可选的 key_prefix
    （和 bucket / public_base_url 同一层）规范化后不为空时，加在新的
    original / thumb / staging key 前面；不写、null、空字符串，或规范化后为空的值，
    都存为 ""。
    ImageEmbedding 不是代码里的默认值。key 里从不包含场景或类别。
    """

    use_boto3: bool = False
    endpoint_url: str = ""  # S3 兼容服务（MinIO / OSS）的地址；AWS S3 留空
    bucket: str = IMAGE_STORAGE_DEFAULT_BUCKET
    public_base_url: str = IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL
    region: str = IMAGE_STORAGE_DEFAULT_REGION
    access_key_id: str = field(default="", repr=False)
    secret_access_key: str = field(default="", repr=False)
    mock_mirror_dir: str = ""  # mock 驱动另存一份字节的目录；空表示只在内存里
    key_prefix: str = ""  # 可选的对象 key 前缀；"" 表示现在生产环境用的 key

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_prefix", normalize_key_prefix(self.key_prefix))

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ImageStorageSettings:
        name = "IMAGE_STORAGE_CONFIG"
        section = _section(config, name)
        raw_prefix = section.get("key_prefix") if "key_prefix" in section else None
        if raw_prefix is not None and not isinstance(raw_prefix, str):
            raise ValueError(f"{name}.key_prefix 必须是字符串，实际类型为 {type(raw_prefix).__name__}")
        return cls(
            use_boto3=_flag(section, name, "use_boto3", False),
            endpoint_url=_text(section, "endpoint_url"),
            bucket=_text(section, "bucket") or IMAGE_STORAGE_DEFAULT_BUCKET,
            public_base_url=(_text(section, "public_base_url") or IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL).rstrip("/"),
            region=_text(section, "region") or IMAGE_STORAGE_DEFAULT_REGION,
            access_key_id=_text(section, "access_key_id"),
            secret_access_key=_text(section, "secret_access_key"),
            mock_mirror_dir=_text(section, "mock_mirror_dir"),
            key_prefix="" if raw_prefix is None else raw_prefix,
        )


@dataclass(frozen=True, slots=True)
class ImageDownloadSettings:
    """远程图片下载（浏览器代理和模型输入的 URL），取自 IMAGE_DOWNLOAD_CONFIG。

    只下载公网地址。trusted_networks 里的网段也按公网处理：生产环境保持为空；
    在 fake-IP 代理后面的机器写 198.18.0.0/15 和 2001:2::/48。
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
class ReconcileSettings:
    """向量库与 PostgreSQL 的后台对账，取自 RECONCILE_CONFIG。

    每一轮补推台账显示落后的 payload，并清理删除队列
    （app.services.retrieval.reconcile）。启动时读取。
    """

    enabled: bool = True
    interval_seconds: float = 300.0
    initial_delay_seconds: float = 30.0
    max_payloads: int = 2000  # 每轮最多补推的 payload 数
    max_deletions: int = 200  # 每轮最多处理的排队删除数
    max_verifications: int = 2000  # 每轮对照 collection 检查的台账行数（0：关闭）
    max_embeddings: int = 100  # 每轮为当前 collection 里没有向量的图片补算 embedding 的数量（0：关闭）

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ReconcileSettings:
        name = "RECONCILE_CONFIG"
        section = _section(config, name)
        defaults = cls()
        settings = cls(
            enabled=_flag(section, name, "enabled", defaults.enabled),
            interval_seconds=_number(section, name, "interval_seconds", defaults.interval_seconds, float),
            initial_delay_seconds=_number(section, name, "initial_delay_seconds", defaults.initial_delay_seconds, float),
            max_payloads=_number(section, name, "max_payloads", defaults.max_payloads, int),
            max_deletions=_number(section, name, "max_deletions", defaults.max_deletions, int),
            max_verifications=_number(section, name, "max_verifications", defaults.max_verifications, int),
            max_embeddings=_number(section, name, "max_embeddings", defaults.max_embeddings, int),
        )
        if settings.interval_seconds < 10:
            raise ValueError(f"{name}.interval_seconds must be at least 10, got {settings.interval_seconds!r}")
        if settings.initial_delay_seconds < 0:
            raise ValueError(f"{name}.initial_delay_seconds must not be negative, got {settings.initial_delay_seconds!r}")
        for key in ("max_payloads", "max_deletions"):
            if getattr(settings, key) < 1:
                raise ValueError(f"{name}.{key} must be at least 1, got {getattr(settings, key)!r}")
        for key in ("max_verifications", "max_embeddings"):
            if getattr(settings, key) < 0:
                raise ValueError(f"{name}.{key} must not be negative (0 turns it off), got {getattr(settings, key)!r}")
        return settings


@dataclass(frozen=True, slots=True)
class RecipeDetectSettings:
    """菜谱识别：业务搜图的一步（app.api.services.recipe_detect），取自 RECIPE_DETECT_CONFIG。
    GroundingDINO 在查询图里找到的检测框（和区域搜索同一次检测，参数在 AUTO_SEARCH_REGION_CONFIG）
    逐个裁出来交给菜谱识别模型，和底库最相近的菜品的相似度高于 threshold 才算识别出这道菜。

    这里是每次请求都解析的两项，Nacos 里的修改不用重启就生效。模型的几项（model_path、
    labels_path、use_gpu）在加载模型时读（app.clients.recipe_classifier），改了会在 Nacos 重读配置时
    换模型。没有这一节时这一步关闭（也不加载模型）。threshold 只在开启时检查。
    """

    enabled: bool = False
    # 余弦相似度高于它（不含等于）才算识别出这道菜，0～1；默认是模型自带脚本的阈值
    threshold: float = 0.95

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> RecipeDetectSettings:
        name = "RECIPE_DETECT_CONFIG"
        section = _section(config, name)
        defaults = cls()
        settings = cls(
            enabled=_flag(section, name, "enabled", defaults.enabled),
            threshold=_number(section, name, "threshold", defaults.threshold, float),
        )
        if settings.enabled and not 0 <= settings.threshold <= 1:  # NaN 也不通过
            raise ValueError(f"{name}.threshold must be between 0 and 1, got {settings.threshold!r}")
        return settings


# 区域搜索对一张查询图最多搜索的检测框数（AUTO_SEARCH_REGION_CONFIG.max_regions 的上限）
AUTO_SEARCH_MAX_REGIONS = 20


@dataclass(frozen=True, slots=True)
class AutoSearchRegionSettings:
    """区域搜索：业务搜图和整图搜索同时启动的一步（app.api.services.region_search），
    取自 AUTO_SEARCH_REGION_CONFIG：GroundingDINO 在查询图里找 caption 说的目标，
    找到的每个框都单独作为一张查询图去搜索。

    检测的参数（caption、两个阈值、max_regions）也是菜谱识别的：两步共用同一次检测，区域搜索
    关闭（enabled 为 false）时菜谱识别照样按这些参数检测。

    每次请求都解析，所以 Nacos 里的修改不用重启就生效（模型按 GROUNDINGDINO_CONFIG，
    启动时加载）。没有这一节时这一步关闭。其余的值只在用到时检查：开启时 from_config 检查，
    关闭时由要用它们检测的菜谱识别调用 check。
    """

    enabled: bool = False
    caption: str = "food"  # 检测什么；多个类别写成 "food . dish"
    box_threshold: float = 0.4
    text_threshold: float = 0.4
    max_regions: int = 5  # 用到的检测框数，按检测置信度从高到低

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AutoSearchRegionSettings:
        name = "AUTO_SEARCH_REGION_CONFIG"
        section = _section(config, name)
        defaults = cls()
        settings = cls(
            enabled=_flag(section, name, "enabled", defaults.enabled),
            caption=_text(section, "caption") or defaults.caption,
            box_threshold=_number(section, name, "box_threshold", defaults.box_threshold, float),
            text_threshold=_number(section, name, "text_threshold", defaults.text_threshold, float),
            max_regions=_number(section, name, "max_regions", defaults.max_regions, int),
        )
        return settings.check() if settings.enabled else settings

    def check(self) -> AutoSearchRegionSettings:
        """检查检测参数的取值，不对时抛出 ValueError（指出是哪一项）；对的话返回自己。"""
        name = "AUTO_SEARCH_REGION_CONFIG"
        for key in ("box_threshold", "text_threshold"):
            value = getattr(self, key)
            if not 0 <= value <= 1:  # NaN 也不通过
                raise ValueError(f"{name}.{key} must be between 0 and 1, got {value!r}")
        if not 1 <= self.max_regions <= AUTO_SEARCH_MAX_REGIONS:
            raise ValueError(f"{name}.max_regions must be in 1..{AUTO_SEARCH_MAX_REGIONS}, got {self.max_regions!r}")
        return self


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
    nacos: NacosSettings = field(default_factory=NacosSettings.from_env)
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
