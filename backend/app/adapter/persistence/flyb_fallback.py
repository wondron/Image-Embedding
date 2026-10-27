"""FlyB empty-topk fallback — delegates to existing FlyBSearch.

Does NOT replace predict / FoodService pipeline. Only used when final
self-built list length == 0 (SSOT §4.1).
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import FlyBFallbackPort

logger = logging.getLogger("flyb_fallback")


class FlyBFallbackAdapter(FlyBFallbackPort):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._client = None
        self._init_error: str | None = None
        try:
            from app.domain.common.flybSearch import FlyBSearch

            self._client = FlyBSearch(self._config)
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            logger.warning("FlyB fallback client not ready: %s", self._init_error)

    @property
    def wired(self) -> bool:
        return self._client is not None

    def ping(self) -> dict[str, Any]:
        if self._client is None:
            return {"ok": False, "optional": True, "detail": self._init_error or "not wired"}
        return {"ok": True, "optional": True, "url": getattr(self._client, "url", None)}

    def search_fallback(
        self,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
    ) -> Any:
        if self._client is None:
            raise err.search_flyb_failed(self._init_error or "FlyB client not wired")
        # Existing FlyBSearch.detect reliably accepts image bytes (urls path is unused/broken).
        data = image_bytes
        if data is None and image_url:
            try:
                import requests

                resp = requests.get(str(image_url).strip(), timeout=getattr(self._client, "timeout", 15))
                resp.raise_for_status()
                data = resp.content
            except Exception as exc:
                raise err.search_flyb_failed(f"download for flyb: {exc}") from exc
        if not data:
            raise err.search_flyb_failed("image_bytes required for FlyB fallback")
        try:
            # DOC_COUNT is config-driven; top_k not overridden on client for MVP
            return self._client.detect(data, None, None, None)
        except Exception as exc:
            raise err.search_flyb_failed(f"{type(exc).__name__}: {exc}") from exc
