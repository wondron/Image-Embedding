from __future__ import annotations

import inspect
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import httpx
from fastapi import Request

from app.core.config.settings import Settings

if TYPE_CHECKING:
    from app.core.config.nacos import NacosConfigProvider
    from app.domain.food_service import FoodService
    from app.bootstrap.retrieval_stack import RetrievalStack


@dataclass(slots=True)
class ApplicationState:
    """Resources owned by one FastAPI application instance."""

    config: dict[str, Any]
    config_info: dict[str, Any]
    http_client: httpx.AsyncClient
    settings: Settings
    started_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_monotonic: float = field(default_factory=time.monotonic)
    food_service: FoodService | None = None
    initialization_error: str | None = None
    warmup_error: str | None = None
    retrieval: Optional["RetrievalStack"] = None
    retrieval_error: str | None = None
    stopping: bool = False
    config_provider: NacosConfigProvider | None = None
    configuration_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False
    )

    @property
    def http_timeout(self) -> float:
        return self.settings.predict.http_timeout

    @property
    def max_image_bytes(self) -> int:
        return self.settings.predict.max_image_bytes

    @property
    def ready(self) -> bool:
        service = self.food_service
        return (
            not self.stopping
            and self.initialization_error is None
            and self.warmup_error is None
            and service is not None
            and getattr(service, "segmentor", None) is not None
            and getattr(service, "clip", None) is not None
        )

    def require_food_service(self) -> FoodService:
        with self.configuration_lock:
            if self.food_service is None:
                detail = self.initialization_error or "food service is not initialized"
                raise RuntimeError(detail)
            return self.food_service

    def require_retrieval(self) -> "RetrievalStack":
        with self.configuration_lock:
            if self.retrieval is None:
                detail = self.retrieval_error or "retrieval stack is not initialized"
                raise RuntimeError(detail)
            return self.retrieval

    def replace_configuration(
        self,
        config: dict[str, Any],
        config_info: dict[str, Any],
        food_service: FoodService,
    ) -> None:
        """Atomically expose a fully initialized configuration and service."""
        with self.configuration_lock:
            self.config = config
            self.config_info = config_info
            self.food_service = food_service
            self.initialization_error = None
            self.warmup_error = None

    def record_config_reload_error(self, error: str) -> None:
        with self.configuration_lock:
            self.config_info = {
                **self.config_info,
                "last_reload_error": error,
                "last_reload_attempt_at": datetime.now()
                .astimezone()
                .isoformat(timespec="seconds"),
            }

    def runtime_info(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_s": round(time.monotonic() - self.started_monotonic, 2),
            "now": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    async def close(self) -> None:
        """Release all resources owned by the application state."""
        with self.configuration_lock:
            self.stopping = True
        if self.config_provider is not None:
            self.config_provider.close()
        service = self.food_service
        try:
            service_close = getattr(service, "close", None)
            if callable(service_close):
                result = service_close()
                if inspect.isawaitable(result):
                    await result
        finally:
            self.food_service = None
            await self.http_client.aclose()


def get_application_state(request: Request) -> ApplicationState:
    """Return the initialized state for the current FastAPI application."""
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApplicationState):
        raise RuntimeError("application state is not initialized; lifespan must be running")
    return runtime
