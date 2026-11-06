"""外部图搜候选。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ReviewStatus
from app.db.base import Base, CreatedAtMixin
from app.db.types import str_enum

if TYPE_CHECKING:
    from app.models.image import Image


class ExternalCandidate(CreatedAtMixin, Base):
    """外部图搜结果：先存这里等人工审核，不写进 Qdrant；
    通过后作为 source_type 为 external 的普通图片入库。"""

    __tablename__ = "external_candidates"
    __table_args__ = (
        UniqueConstraint("provider", "source_url"),
        CheckConstraint("(review_status = 'pending') = (reviewed_at IS NULL)", name="reviewed_at_matches"),
        CheckConstraint("image_id IS NULL OR review_status = 'accepted'", name="image_only_when_accepted"),
        Index(
            "ix_external_candidates_pending",
            "created_at",
            postgresql_where=text("review_status = 'pending'"),
        ),
        {"comment": "外部图搜候选"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    provider: Mapped[str] = mapped_column(comment="外部图搜服务名")
    source_url: Mapped[str] = mapped_column(comment="结果原链接；同一服务的同一链接只存一次")
    staged_url: Mapped[str] = mapped_column(comment="暂存文件的完整 https 地址（S3 staging/）")
    external_label: Mapped[str | None] = mapped_column(comment="外部返回的标签文字，审核时参考")
    query_ref: Mapped[str | None] = mapped_column(comment="触发这次搜索的查询，如请求 ID")
    review_status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus, "review_status"),
        server_default=ReviewStatus.PENDING.value,
        comment="pending / accepted / rejected",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(comment="审核时间，待审核时为空")
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("images.id", ondelete="SET NULL"),
        index=True,
        comment="入库后的图片 ID，未入库时为空",
    )

    image: Mapped[Image | None] = relationship()
