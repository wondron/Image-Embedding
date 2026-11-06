"""Access to application resources; unavailable ones become NOT_READY (503)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.bootstrap.state import ApplicationState
from app.core import errors

if TYPE_CHECKING:
    from app.bootstrap.retrieval_stack import RetrievalStack
    from app.services.predict.food_service import FoodService


def require_food_service(runtime: ApplicationState) -> "FoodService":
    try:
        return runtime.require_food_service()
    except RuntimeError as exc:
        raise errors.not_ready(f"service unavailable: {exc}") from exc


def require_retrieval(runtime: ApplicationState) -> "RetrievalStack":
    try:
        return runtime.require_retrieval()
    except RuntimeError as exc:
        raise errors.not_ready(str(exc)) from exc
