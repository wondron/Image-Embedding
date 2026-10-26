"""Shared access to application resources with HTTP error translation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.bootstrap.state import ApplicationState

if TYPE_CHECKING:
    from app.domain.food_service import FoodService


def require_food_service(runtime: ApplicationState) -> FoodService:
    """Translate an unavailable application service into an HTTP 503."""
    try:
        return runtime.require_food_service()
    except RuntimeError as exc:
        raise HTTPException(503, f"service unavailable: {exc}") from exc
