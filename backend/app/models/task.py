"""入库任务：每次入库请求里每个文件的结果。"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import IndexTaskStatus
from app.db.base import Base, CreatedAtMixin
from app.db.types import str_enum


class IndexTask(CreatedAtMixin, Base):
    """一个文件的入库结果：成功时记得到的图片，失败时记原因。

    入库是同步的，请求结束时一次写入，只有最终结果。图片后来被删除时记录保留，所以 image_id 不设外键。
    写记录失败不影响入库本身（只记日志）。
    """

    __tablename__ = "index_tasks"
    __table_args__ = (
        CheckConstraint("(status = 'succeeded') = (image_id IS NOT NULL)", name="image_matches_status"),
        # 任务中心按时间倒序分页，可按结果筛选
        Index("ix_index_tasks_created_at", "created_at"),
        Index("ix_index_tasks_status_created_at", "status", "created_at"),
        {"comment": "入库任务记录"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    filename: Mapped[str] = mapped_column(comment="上传时的文件名（JSON 入库时是本地路径）")
    status: Mapped[IndexTaskStatus] = mapped_column(
        str_enum(IndexTaskStatus, "status"), comment="succeeded 成功 / failed 失败"
    )
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="得到的图片（images.id）；失败时为空；图片删除后记录保留，不设外键"
    )
    new_image: Mapped[bool | None] = mapped_column(
        comment="是否这次新入库；false 表示相同图片此前已入库，沿用原有标注；失败时为空"
    )
    error_code: Mapped[str | None] = mapped_column(comment="失败时的错误码，如 INDEX_BAD_IMAGE")
    error_detail: Mapped[str | None] = mapped_column(comment="失败原因，给用户看的文字（不含内部细节）")
    duration_ms: Mapped[int | None] = mapped_column(comment="服务端处理这个文件的用时（毫秒）")
    trace_id: Mapped[str | None] = mapped_column(comment="入库请求的追踪号")
