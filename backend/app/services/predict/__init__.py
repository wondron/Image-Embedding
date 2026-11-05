"""Predict / FoodService island — retrieval must not import this package."""
from app.services.predict.food_service import FoodService

__all__ = ["FoodService"]
