"""SQLAlchemy declarative base for app.models."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata for declarative table mirrors."""

    pass
