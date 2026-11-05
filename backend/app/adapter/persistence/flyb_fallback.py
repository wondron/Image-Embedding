"""DEPRECATED (DDD D5/D6): FlyB stripped from retrieval stack/search.

Left as an unused stub so old imports do not resurrect FlyB wiring.
Predict/FoodService may still use domain.common.flybSearch directly; this
adapter is not on the retrieval path.
"""

from __future__ import annotations

from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import FlyBFallbackPort


class FlyBFallbackAdapter(FlyBFallbackPort):
    """No-op stub — never constructs FlyBSearch."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        del config
        self._init_error = "FlyB out of scope for retrieval (D5/D6)"

    @property
    def wired(self) -> bool:
        return False

    def ping(self) -> dict[str, Any]:
        return {
            "ok": False,
            "optional": True,
            "disabled": True,
            "detail": self._init_error,
        }

    def search_fallback(
        self,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
    ) -> Any:
        del image_bytes, image_url, top_k
        raise err.search_flyb_failed(self._init_error)
