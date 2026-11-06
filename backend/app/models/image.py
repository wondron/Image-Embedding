"""图片：图片表和清理队列。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import FoodState, LabelStatus, License, SourceType
from app.db.base import Base, TimestampMixin
from app.db.types import str_enum

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.embedding import ImageEmbedding
    from app.models.scene import ImageScene


class Image(TimestampMixin, Base):
    """一张原图一行。

    id 由原图内容算出：字节完全相同的图只入库一次，来源只记第一次的。
    """

    __tablename__ = "images"
    __table_args__ = (
        CheckConstraint("octet_length(sha256) = 32", name="sha256_len"),
        CheckConstraint("width > 0 AND height > 0", name="size_positive"),
        CheckConstraint(
            "(label_status = 'unlabeled') = (category_id IS NULL)", name="label_matches_category"
        ),
        # 图库列表按创建时间倒序分页（btree 可反向扫描，不必建 DESC）
        Index("ix_images_created_at", "created_at"),
        {"comment": "图片"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, comment="原图 SHA-256 的前 16 字节；同时是 S3 文件名和 Qdrant 点 ID"
    )
    sha256: Mapped[bytes] = mapped_column(unique=True, comment="原图 SHA-256，用来校验和去重")
    original_url: Mapped[str] = mapped_column(comment="原图的完整 https 地址")
    thumb_url: Mapped[str] = mapped_column(comment="缩略图的完整 https 地址（长边 512，WebP）")
    width: Mapped[int] = mapped_column(comment="原图宽（像素）")
    height: Mapped[int] = mapped_column(comment="原图高（像素）")
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, "source_type"), index=True, comment="来源大类"
    )
    license: Mapped[License] = mapped_column(str_enum(License, "license"), index=True, comment="许可")
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        index=True,
        comment="类别，可以标到任意层级；未标注时为空",
    )
    food_state: Mapped[FoodState | None] = mapped_column(
        str_enum(FoodState, "food_state"), comment="食物状态：生 / 熟 / 冷冻"
    )
    label_status: Mapped[LabelStatus] = mapped_column(
        str_enum(LabelStatus, "label_status"),
        server_default=LabelStatus.UNLABELED.value,
        index=True,
        comment="标注状态",
    )
    payload_version: Mapped[int] = mapped_column(
        BigInteger,
        server_default=text("1"),
        comment="类别、状态或所属场景变化时加 1，用来判断 Qdrant 里的 payload 要不要重推",
    )

    category: Mapped[Category | None] = relationship()
    scene_links: Mapped[list[ImageScene]] = relationship(
        back_populates="image", cascade="all, delete-orphan", passive_deletes=True
    )
    embeddings: Mapped[list[ImageEmbedding]] = relationship(
        back_populates="image", cascade="all, delete-orphan", passive_deletes=True
    )


class ImageDeletion(Base):
    """清理队列：图片删除后，清理任务据此删掉各 collection 里的点和 S3 文件，处理完删记录。"""

    __tablename__ = "image_deletions"
    __table_args__ = {"comment": "图片清理队列"}

    image_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, comment="被删除的图片；图片行已删除，所以不设外键"
    )
    original_url: Mapped[str] = mapped_column(comment="要清理的原图")
    thumb_url: Mapped[str] = mapped_column(comment="要清理的缩略图")
    # 清理任务按删除时间从旧到新取
    deleted_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True, comment="删除时间"
    )
