"""Declarative ORM mirrors of PG v0.1 12 tables (contract §2).

Parallel to repositories' SQLAlchemy Core SQL — not required for runtime CRUD yet.
Alembic target_metadata may remain None this wave.
"""
from app.models.tables import (
    Base,
    HeatingGroup,
    Category,
    CategoryName,
    Image,
    ImageDeletion,
    Scene,
    SceneLabel,
    SceneRule,
    ImageScene,
    EmbeddingModel,
    ImageEmbedding,
    ExternalCandidate,
)

__all__ = [
    "Base",
    "HeatingGroup",
    "Category",
    "CategoryName",
    "Image",
    "ImageDeletion",
    "Scene",
    "SceneLabel",
    "SceneRule",
    "ImageScene",
    "EmbeddingModel",
    "ImageEmbedding",
    "ExternalCandidate",
]
