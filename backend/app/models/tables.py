"""12-table declarative mirrors (docs/image-search-pg-v01-data-contract.md §2).

Not wired into Alembic autogenerate this wave. Repositories keep hand-written SQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Ltree


class HeatingGroup(Base):
    __tablename__ = "heating_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    path: Mapped[str] = mapped_column(Ltree(), nullable=False)
    cuisines: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    heating_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("heating_groups.id"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CategoryName(Base):
    __tablename__ = "category_names"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Image(Base):
    __tablename__ = "images"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_url: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    food_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageDeletion(Base):
    __tablename__ = "image_deletions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    outputs_heating: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SceneLabel(Base):
    __tablename__ = "scene_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class SceneRule(Base):
    __tablename__ = "scene_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id"), nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    cuisine: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scene_labels.id"), nullable=True)


class ImageScene(Base):
    __tablename__ = "image_scenes"

    image_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("images.id"), primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id"), primary_key=True)
    is_member: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    label_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scene_labels.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbeddingModel(Base):
    __tablename__ = "embedding_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    collection: Mapped[str] = mapped_column(Text, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    distance: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageEmbedding(Base):
    __tablename__ = "image_embeddings"

    image_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("images.id"), primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("embedding_models.id"), primary_key=True)
    synced_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    embedded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalCandidate(Base):
    __tablename__ = "external_candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
