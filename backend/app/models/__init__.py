"""ORM 模型：导入这里即注册全部 12 张表（Alembic 依赖它拿到完整的 metadata）。

表结构以这里为准；迁移脚本（alembic/versions/）负责把数据库变成这个样子，
`alembic check` 可以确认两者一致。仓储层目前仍是手写 SQL，取值统一用 app.core.enums。
"""

from app.db.base import Base
from app.models.category import Category, CategoryName, HeatingGroup
from app.models.embedding import EmbeddingModel, ImageEmbedding
from app.models.external import ExternalCandidate
from app.models.image import Image, ImageDeletion
from app.models.scene import ImageScene, Scene, SceneLabel, SceneRule

__all__ = [
    "Base",
    "Category",
    "CategoryName",
    "EmbeddingModel",
    "ExternalCandidate",
    "HeatingGroup",
    "Image",
    "ImageDeletion",
    "ImageEmbedding",
    "ImageScene",
    "Scene",
    "SceneLabel",
    "SceneRule",
]
