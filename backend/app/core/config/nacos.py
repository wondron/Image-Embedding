"""Application config: local YAML as the base layer, Nacos dataIds layered on top.

Startup (NacosConfigProvider.load):
  - the local YAML is read first and used as the base;
  - the selected profile's dataIds are fetched and deep-merged over it: Nacos
    wins key by key, keys Nacos does not define keep their YAML value;
  - Nacos disabled / profile not configured / unreachable / invalid -> the YAML
    alone is used; neither source usable -> the YAML error is raised.

Polling (start_polling): periodic GET of the dataIds (no long-poll listener).
A complete fetch whose fingerprint differs from the active one is offered to
``on_update``. Failures keep the current config and retry after
``retry_interval_seconds``. Polling also runs after a YAML fallback, so the
service switches to Nacos once it becomes reachable. A config ``on_update``
rejected is not offered again until it changes.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import requests

from app.core.config.constant import NacosProfile
from app.core.config.loader import load_yaml, parse_config_text, prepare_config
from app.core.config.settings import NacosSettings
from app.core.observability.log_helper import show_text

logger = logging.getLogger("nacos")
_MARKDOWN_URL = re.compile(r"\[(https?://[^\]]+)\]\([^)]+\)")


def _log(level: int, message: str, **fields: Any) -> None:
    """Same message to the application log and the platform log."""
    logger.log(level, message)
    show_text(message, logger_level=level, **fields)


class NacosConfigError(RuntimeError):
    """Raised when a Nacos operation cannot produce a usable configuration."""


def _server_address(value: str) -> str:
    """Return the first address and tolerate an accidentally pasted Markdown URL."""
    value = value.strip()
    match = _MARKDOWN_URL.fullmatch(value)
    if match:
        value = match.group(1)

    value = re.split(r"[,;]", value, maxsplit=1)[0].strip()
    if not value:
        raise NacosConfigError("Nacos server address is empty")

    return value.rstrip("/")


def _deep_merge(target: dict, incoming: dict) -> dict:
    """Merge incoming into target in place: nested dicts merge, anything else is replaced."""
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


@dataclass(frozen=True, slots=True)
class NacosConfigSnapshot:
    config: dict  # effective config: local YAML base + Nacos documents
    profile: NacosProfile
    raw_configs: dict[str, str]

    @property
    def fingerprint(self) -> str:
        """Stable fingerprint including profile and every dataId payload."""
        digest = hashlib.sha256()
        digest.update(self.profile["profile"].encode("utf-8"))

        for data_id in self.profile["data_id_list"]:
            digest.update(b"\0")
            digest.update(data_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self.raw_configs[data_id].encode("utf-8"))

        return digest.hexdigest()


class NacosConfigClient:
    """Small client using only the Nacos authentication and config GET APIs."""

    def __init__(
        self,
        profile: NacosProfile,
        settings: NacosSettings,
        session: requests.Session | None = None,
    ) -> None:
        self.profile = profile
        self.settings = settings
        self.session = session or requests.Session()
        self.base_url = _server_address(profile["server_addresses"])
        self._access_token: str | None = None

    def _url(self, endpoint: str) -> str:
        prefix = "" if self.base_url.endswith("/nacos") else "/nacos"
        return f"{self.base_url}{prefix}/v1/{endpoint.lstrip('/')}"

    @property
    def _timeout(self) -> tuple[float, float]:
        return (
            float(self.settings.connect_timeout_seconds),
            float(self.settings.request_timeout_seconds),
        )

    @property
    def _has_credentials(self) -> bool:
        return bool(self.profile["username"] or self.profile["password"])

    def _authenticate(self) -> None:
        response = self.session.post(
            self._url("auth/login"),
            data={"username": self.profile["username"], "password": self.profile["password"]},
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            body = response.text.strip()
            raise NacosConfigError(
                f"Nacos authentication failed with HTTP {response.status_code}"
                + (f" | response={body[:500]}" if body else "")
            ) from exc

        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise NacosConfigError("Nacos authentication response has no access token")
        self._access_token = str(token)

    def _auth_params(self) -> dict[str, str]:
        if not self._has_credentials:
            return {}
        if self._access_token is None:
            self._authenticate()
        return {"accessToken": self._access_token or ""}

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Send an authenticated request and re-authenticate once on 401/403."""
        base_params = dict(kwargs.pop("params", {}))
        response: requests.Response | None = None
        for attempt in range(2):
            auth_params = self._auth_params()
            request_kwargs = dict(kwargs)
            headers = dict(request_kwargs.pop("headers", {}))
            if auth_params.get("accessToken"):
                headers["Authorization"] = f"Bearer {auth_params['accessToken']}"
            if headers:
                request_kwargs["headers"] = headers
            response = self.session.request(method, url, params={**base_params, **auth_params}, **request_kwargs)
            if response.status_code in {401, 403} and self._has_credentials and attempt == 0:
                self._access_token = None
                continue
            return response
        return response  # type: ignore[return-value]

    def get_config(self, data_id: str) -> str | None:
        """Fetch one dataId; None when it is missing or blank."""
        response = self._request(
            "GET",
            self._url("cs/configs"),
            params={
                "dataId": data_id,
                "group": self.profile["group"],
                "tenant": self.profile["namespace"],
            },
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            body = response.text.strip()
            raise NacosConfigError(
                f"Nacos config request failed with HTTP {response.status_code}"
                + (f" | response={body[:500]}" if body else "")
            ) from exc
        text = response.text
        return text if text.strip() else None

    def fetch_documents(self) -> dict[str, str]:
        """Raw text of every configured dataId (all of them, or NacosConfigError)."""
        documents: dict[str, str] = {}
        for data_id in self.profile["data_id_list"]:
            text = self.get_config(data_id)
            if text is None:
                raise NacosConfigError(f"Nacos dataId {data_id!r} is empty or missing")
            documents[data_id] = text
        return documents

    def close(self) -> None:
        self.session.close()


ClientFactory = Callable[[NacosProfile, NacosSettings], NacosConfigClient]
UpdateCallback = Callable[[NacosConfigSnapshot], bool]


class NacosConfigProvider:
    """Layer Nacos over the local YAML at startup; poll Nacos for changes afterwards."""

    def __init__(self, settings: NacosSettings, client_factory: ClientFactory = NacosConfigClient) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._clients: dict[str, NacosConfigClient] = {}
        self._base: dict | None = None  # raw local YAML (before prepare_config)
        self._active: NacosConfigSnapshot | None = None
        self._rejected_fingerprint: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    # -- settings ------------------------------------------------------------------

    @property
    def poll_enabled(self) -> bool:
        return bool(self.settings.poll_enabled)

    @property
    def poll_interval_seconds(self) -> float:
        interval = float(self.settings.poll_interval_seconds)
        if interval <= 0:
            raise ValueError("Nacos poll_interval_seconds must be greater than 0")
        return interval

    @property
    def retry_interval_seconds(self) -> float:
        return max(float(self.settings.retry_interval_seconds), 0.1)

    def _selected_profile(self) -> NacosProfile | None:
        return next(
            (item for item in self.settings.profiles if item["profile"] == self.settings.profile),
            None,
        )

    def _polling_possible(self) -> bool:
        return bool(self.settings.enabled and self.poll_enabled and self._selected_profile() is not None)

    # -- fetching ------------------------------------------------------------------

    def _client(self, profile: NacosProfile) -> NacosConfigClient:
        with self._lock:
            client = self._clients.get(profile["profile"])
            if client is None:
                client = self._client_factory(profile, self.settings)
                self._clients[profile["profile"]] = client
            return client

    def _fetch(self, profile: NacosProfile) -> NacosConfigSnapshot:
        """Fetch every dataId and layer them over the local YAML base (raises on failure)."""
        documents = self._client(profile).fetch_documents()
        merged = deepcopy(self._base) if self._base is not None else {}
        for data_id in profile["data_id_list"]:
            _deep_merge(merged, parse_config_text(documents[data_id], f"Nacos dataId {data_id!r}"))
        return NacosConfigSnapshot(config=prepare_config(merged), profile=profile, raw_configs=documents)

    # -- startup -------------------------------------------------------------------

    def source_info(self, snapshot: NacosConfigSnapshot) -> dict:
        return {
            "source": "nacos",
            "yaml_base": self._base is not None,
            "nacos_enabled": True,
            "nacos_polling": self.poll_enabled,
            "nacos_poll_interval_seconds": self.poll_interval_seconds,
            "nacos_profile": snapshot.profile["profile"],
            "nacos_namespace": snapshot.profile["namespace"],
            "nacos_group": snapshot.profile["group"],
            "nacos_data_ids": list(snapshot.profile["data_id_list"]),
            "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def load(self, yaml_path: str) -> tuple[dict, dict]:
        """Effective startup config: YAML base + Nacos, or YAML alone if Nacos is unusable."""
        local_error: Exception | None = None
        try:
            self._base = load_yaml(yaml_path)
        except Exception as exc:
            self._base, local_error = None, exc
            _log(logging.WARNING, f"Local YAML unavailable | path={yaml_path} | error={type(exc).__name__}: {exc}",
                 yaml_path=yaml_path)

        profile = self._selected_profile()
        if self.settings.enabled and profile is None:
            _log(logging.WARNING, f"Nacos profile not configured | profile={self.settings.profile}")
        if self.settings.enabled and profile is not None:
            try:
                snapshot = self._fetch(profile)
            except Exception as exc:
                _log(logging.WARNING,
                     f"Nacos profile unavailable, using local YAML | profile={profile['profile']} | "
                     f"error={type(exc).__name__}: {exc}",
                     profile=profile["profile"], data_ids=list(profile["data_id_list"]))
            else:
                with self._lock:
                    self._active = snapshot
                _log(logging.INFO,
                     f"Nacos configuration loaded | profile={profile['profile']} | "
                     f"data_ids={profile['data_id_list']} | yaml_base={self._base is not None}",
                     profile=profile["profile"], data_ids=list(profile["data_id_list"]))
                return snapshot.config, self.source_info(snapshot)

        if self._base is None:
            raise local_error  # type: ignore[misc]  # neither source is usable
        status = "fallback" if self.settings.enabled else "disabled"
        _log(logging.INFO,
             f"Local YAML configuration loaded | path={yaml_path} | "
             f"nacos_profile={self.settings.profile} | nacos_status={status}",
             profile=self.settings.profile, yaml_path=yaml_path)
        return prepare_config(self._base), {
            "source": "yaml",
            "nacos_enabled": self.settings.enabled,
            "nacos_profile": self.settings.profile,
            "nacos_polling": self._polling_possible(),
            "nacos_poll_interval_seconds": self.poll_interval_seconds,
            "nacos_status": status,
        }

    # -- polling -------------------------------------------------------------------

    def start_polling(self, on_update: UpdateCallback) -> None:
        """Start periodic GET polling (also after a YAML fallback, to pick Nacos up later)."""
        if not self._polling_possible():
            _log(logging.INFO, f"Nacos 配置轮询未启动  配置开关={self.settings.enabled}  轮询开关={self.poll_enabled}  "
                               f"profile={self.settings.profile}")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, args=(on_update,), name="nacos-config-poller", daemon=True)
        self._thread.start()
        # _log(logging.INFO, f"Nacos 配置轮询启动  间隔={self.poll_interval_seconds:.3f}s")

    def _poll_loop(self, on_update: UpdateCallback) -> None:
        profile = self._selected_profile()
        wait = self.poll_interval_seconds
        try:
            while profile is not None and not self._stop_event.wait(wait):
                wait = self.poll_interval_seconds
                try:
                    candidate = self._fetch(profile)
                except Exception as exc:
                    # _log(logging.WARNING, f"Nacos 配置轮询失败，保留当前配置，{self.retry_interval_seconds:.1f}s 后重试 | error={type(exc).__name__}: {exc}")
                    wait = self.retry_interval_seconds
                    continue
                self._offer(candidate, on_update)
        finally:
            self._close_clients()

    def _offer(self, candidate: NacosConfigSnapshot, on_update: UpdateCallback) -> None:
        fingerprint = candidate.fingerprint
        with self._lock:
            active, rejected = self._active, self._rejected_fingerprint
        if active is not None and fingerprint == active.fingerprint:
            logger.debug("Nacos 配置未变更 | profile=%s", candidate.profile["profile"])
            return
        if fingerprint == rejected:
            return  # same config on_update already refused; wait for a new one
        name = candidate.profile["profile"]
        _log(logging.INFO, f"Nacos 已可用，切换到远端配置 | profile={name}" if active is None
             else f"Nacos 配置变更被检测到 | profile={name}")
        if on_update(candidate):
            with self._lock:
                self._active, self._rejected_fingerprint = candidate, None
            _log(logging.INFO, f"Nacos 配置更新已应用 | profile={name}")
        else:
            with self._lock:
                self._rejected_fingerprint = fingerprint
            _log(logging.WARNING, f"Nacos 配置更新被拒绝，保留当前配置 | profile={name}")

    # -- lifecycle -----------------------------------------------------------------

    def _close_clients(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                logger.exception("Failed to close Nacos client")

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            join_timeout = max(
                2.0,
                float(self.settings.connect_timeout_seconds) + float(self.settings.request_timeout_seconds) + 1.0,
            )
            thread.join(timeout=join_timeout)
        if thread is None or not thread.is_alive():
            self._close_clients()
        logger.info("Nacos 配置轮询已停止")


def load_config_once(settings: NacosSettings, yaml_path: str) -> dict:
    """One-shot processes (workers, Alembic): local YAML base + Nacos, loaded once, no polling.

    Same rules as application startup; raises when neither source is usable.
    """
    provider = NacosConfigProvider(settings)
    try:
        config, _source = provider.load(yaml_path)
        return config
    finally:
        provider.close()
