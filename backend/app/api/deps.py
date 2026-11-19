"""Access to application resources; unavailable ones become NOT_READY (503)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.bootstrap.state import ApplicationState

if TYPE_CHECKING:
    from app.bootstrap.catalog import CatalogServices
    from app.bootstrap.retrieval_stack import RetrievalStack
    from app.services.predict.food_service import FoodService


def require_food_service(runtime: ApplicationState) -> "FoodService":
    return runtime.require_food_service()


def require_retrieval(runtime: ApplicationState) -> "RetrievalStack":
    return runtime.require_retrieval()


def require_catalog(runtime: ApplicationState) -> "CatalogServices":
    """The category / scene admin (PostgreSQL only)."""
    return runtime.require_catalog()
