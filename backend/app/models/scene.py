"""场景：场景、分配规则、场景自己的类别、图片与场景的关系。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import SceneOrigin
from app.db.base import Base, CreatedAtMixin, UpdatedAtMixin
from app.db.types import str_enum

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.image import Image


class Scene(CreatedAtMixin, Base):
    """应用场景，可配置新增：通用、一体机、抽油烟机、美国饮食、中国饮食、日本饮食……

    接口传入场景编码，只在该场景的图片里检索；场景有自己的类别时，结果按场景类别给出。
    """

    __tablename__ = "scenes"
    __table_args__ = {"comment": "应用场景"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(
        unique=True, comment="场景编码，接口传入、写进 Qdrant payload，如 general、oven"
    )
    name: Mapped[str] = mapped_column(comment="名称，如 通用、一体机")
    description: Mapped[str | None] = mapped_column(comment="说明")
    include_all: Mapped[bool] = mapped_column(
        server_default=false(), comment="是否包含全部图片；通用场景为是"
    )
    outputs_heating: Mapped[bool] = mapped_column(
        server_default=false(), comment="结果是否另带加热组和食物状态；一体机场景为是"
    )
    is_active: Mapped[bool] = mapped_column(server_default=true(), comment="停用后不再分配新图片")

    rules: Mapped[list[SceneRule]] = relationship(
        back_populates="scene", cascade="all, delete-orphan", passive_deletes=True
    )
    labels: Mapped[list[SceneLabel]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SceneLabel.sort_order",
    )


class SceneLabel(Base):
    """场景自己的类别（如一体机的类别）。多个通用类别可以对应同一个场景类别。"""

    __tablename__ = "scene_labels"
    __table_args__ = (
        UniqueConstraint("scene_id", "code"),
        # 供 scene_rules 的组合外键引用，保证规则只能映射到本场景的类别
        UniqueConstraint("scene_id", "id"),
        {"comment": "场景类别"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), comment="所属场景")
    code: Mapped[str] = mapped_column(comment="场景类别编码，场景内唯一，如 steak")
    name: Mapped[str] = mapped_column(comment="名称，如 牛排")
    sort_order: Mapped[int] = mapped_column(server_default=text("0"), comment="排序")
    is_active: Mapped[bool] = mapped_column(server_default=true(), comment="停用后不再输出")

    scene: Mapped[Scene] = relationship(back_populates="labels")


class SceneRule(Base):
    """场景分配规则：按类别（含子孙）或按菜系二选一；满足任意一条即属于该场景。

    按类别的规则可以带 label_id，把这些通用类别映射到本场景的类别；
    一张图被多条规则覆盖时，取类别最具体（层级最深）的那条。
    """

    __tablename__ = "scene_rules"
    __table_args__ = (
        CheckConstraint("(category_id IS NULL) <> (cuisine IS NULL)", name="one_target"),
        CheckConstraint("label_id IS NULL OR category_id IS NOT NULL", name="label_needs_category"),
        ForeignKeyConstraint(["scene_id", "label_id"], ["scene_labels.scene_id", "scene_labels.id"]),
        UniqueConstraint("scene_id", "category_id"),
        UniqueConstraint("scene_id", "cuisine"),
        {"comment": "场景分配规则"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), comment="所属场景")
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        index=True,
        comment="按类别：这个类别及其所有子孙",
    )
    cuisine: Mapped[str | None] = mapped_column(comment="按菜系：菜系包含它的类别")
    label_id: Mapped[int | None] = mapped_column(comment="映射到本场景自己的类别（scene_labels），可为空")

    scene: Mapped[Scene] = relationship(back_populates="rules")
    category: Mapped[Category | None] = relationship()
    label: Mapped[SceneLabel | None] = relationship(
        primaryjoin="and_(SceneRule.scene_id == SceneLabel.scene_id, SceneRule.label_id == SceneLabel.id)",
        foreign_keys="[SceneRule.label_id]",
    )


class ImageScene(UpdatedAtMixin, Base):
    """图片属于哪些场景。

    人工记录优先，不会被规则覆盖；删掉人工记录就恢复按规则分配。
    include_all 的场景（如通用）包含全部图片，不在这里写记录。
    """

    __tablename__ = "image_scenes"
    __table_args__ = (
        CheckConstraint("is_member OR origin = 'manual'", name="exclude_only_manual"),
        Index("ix_image_scenes_scene_member", "scene_id", postgresql_where=text("is_member")),
        {"comment": "图片与场景的关系"},
    )

    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True)
    origin: Mapped[SceneOrigin] = mapped_column(
        str_enum(SceneOrigin, "origin"), comment="rule 按规则分配 / manual 人工调整"
    )
    is_member: Mapped[bool] = mapped_column(server_default=true(), comment="是否属于该场景；人工排除时为否")

    image: Mapped[Image] = relationship(back_populates="scene_links")
    scene: Mapped[Scene] = relationship()
