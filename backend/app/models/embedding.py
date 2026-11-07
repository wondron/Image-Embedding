"""模型与向量：模型注册表、每张图的写入记录。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Distance, ModelStatus
from app.db.base import Base, CreatedAtMixin
from app.db.types import str_enum

if TYPE_CHECKING:
    from app.models.image import Image


class EmbeddingModel(CreatedAtMixin, Base):
    """模型注册表：一个模型版本一行，对应一个 Qdrant collection。

    切换：新版本补齐后改为 ready，再设为 current，旧版本退回 ready；
    回滚就是把旧版本设回 current。查询编码器和 collection 必须成对切换。
    """

    __tablename__ = "embedding_models"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint("dim > 0", name="dim_positive"),
        CheckConstraint("input_size > 0", name="input_size_positive"),
        # 同一时间只有一个 current
        Index(
            "uq_embedding_models_one_current",
            "status",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        {"comment": "模型注册表"},
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(comment="模型名，如 dinov3_vith16plus_lora")
    version: Mapped[str] = mapped_column(comment="版本；name 加 version 唯一")
    dim: Mapped[int] = mapped_column(comment="向量维度，如 1024")
    distance: Mapped[Distance] = mapped_column(str_enum(Distance, "distance"), comment="距离")
    input_size: Mapped[int] = mapped_column(comment="输入尺寸，如 336")
    preprocess: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), comment="预处理参数：均值方差、缩放方式等"
    )
    weights_uri: Mapped[str] = mapped_column(comment="权重位置，含 LoRA")
    collection: Mapped[str] = mapped_column(unique=True, comment="对应的 Qdrant collection 名")
    status: Mapped[ModelStatus] = mapped_column(
        str_enum(ModelStatus, "status"),
        server_default=ModelStatus.BUILDING.value,
        comment="building / ready / current / retired",
    )
    activated_at: Mapped[datetime | None] = mapped_column(comment="切换为当前的时间")


class ImageEmbedding(Base):
    """每张图已经写进了哪些模型版本的 collection。"""

    __tablename__ = "image_embeddings"
    __table_args__ = {"comment": "向量写入记录"}

    model_id: Mapped[int] = mapped_column(
        ForeignKey("embedding_models.id", ondelete="CASCADE"), primary_key=True
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    synced_version: Mapped[int] = mapped_column(
        BigInteger,
        comment="写入 Qdrant 时这张图的 payload_version；和图片当前的不一致就要重推 payload",
    )
    embedded_at: Mapped[datetime] = mapped_column(server_default=func.now(), comment="写入时间")

    model: Mapped[EmbeddingModel] = relationship()
    image: Mapped[Image] = relationship(back_populates="embeddings")
