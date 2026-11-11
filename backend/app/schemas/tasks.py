"""Task center wire: index records (GET / DELETE /api/index-tasks) and the deletion queue
(GET /api/image-deletions, POST /api/image-deletions/process).

Success bodies carry no trace_id (it is in the X-Trace-Id header), as for the gallery admin.
Use case: app.services.retrieval.task_center.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import IndexTaskStatus, LabelStatus
from app.services.retrieval.task_center import MAX_DELETIONS


# -- index records ------------------------------------------------------------------------

class IndexTaskImage(BaseModel):
    """The record's image as it is now."""

    id: str
    thumb_url: str | None = None
    category_id: int | None = None
    category: str | None = Field(default=None, description="类别的中文主名；未标注时为 null")
    label_status: LabelStatus | None = None


class IndexTaskItem(BaseModel):
    id: int
    filename: str = Field(..., description="上传时的文件名（JSON 入库时是本地路径）")
    status: IndexTaskStatus = Field(..., description="succeeded 成功 / failed 失败")
    image_id: str | None = Field(..., description="得到的图片；失败时为 null。图片删除后仍保留")
    new_image: bool | None = Field(
        ..., description="true：这次新入库；false：相同图片此前已入库，沿用原有标注；失败时为 null"
    )
    error_code: str | None = Field(..., description="失败时的错误码，如 INDEX_BAD_IMAGE")
    error_detail: str | None = Field(..., description="失败原因（给用户看的文字）")
    duration_ms: int | None = Field(..., description="服务端处理这个文件的用时（毫秒）")
    trace_id: str | None = Field(..., description="入库请求的追踪号，用来查日志")
    created_at: str | None = None
    image: IndexTaskImage | None = Field(
        ..., description="图片现在的样子；失败，或图片已被删除时为 null（成功记录仍保留 image_id）"
    )


class IndexTaskCounts(BaseModel):
    succeeded: int
    failed: int


class IndexTaskListResponse(BaseModel):
    ok: Literal[True] = True
    status: IndexTaskStatus | None = Field(..., description="本次的筛选；null 表示全部")
    page: int
    page_size: int
    total: int = Field(..., description="符合筛选的记录数")
    counts: IndexTaskCounts = Field(..., description="全部记录按结果的数量（不受筛选影响）")
    items: list[IndexTaskItem] = Field(..., description="新的在前")


class IndexTaskClearResponse(BaseModel):
    ok: Literal[True] = True
    status: IndexTaskStatus
    deleted: int = Field(..., description="删掉的记录数")


# -- deletion queue -----------------------------------------------------------------------

class ImageDeletionItem(BaseModel):
    image_id: str
    original_url: str | None = None
    thumb_url: str | None = Field(default=None, description="清理完成前文件还在，可用来预览")
    deleted_at: str | None = None


class ImageDeletionListResponse(BaseModel):
    ok: Literal[True] = True
    total: int = Field(..., description="队列里一共有多少张图等待清理")
    items: list[ImageDeletionItem] = Field(..., description="先删除的在前，最多 limit 条")
    cleanup_ready: bool = Field(
        ..., description="现在能否清理：检索栈（向量库、对象存储）就绪时为 true，否则 POST …/process 返回 NOT_READY"
    )


class ImageDeletionProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=MAX_DELETIONS, strict=True, description="这次最多清理多少条，先删除的先清理")


class ImageDeletionFailure(BaseModel):
    image_id: str
    error: str = Field(..., description="错误码")
    detail: str = Field(..., description="给用户看的原因；内部细节只在服务端日志里")


class ImageDeletionProcessResponse(BaseModel):
    ok: Literal[True] = True
    scanned: int = Field(..., description="这次取出的条数")
    processed: list[str] = Field(..., description="清理完成、已移出队列的图片")
    failed: list[ImageDeletionFailure] = Field(..., description="清理失败、留在队列里的图片")
    remaining: int = Field(..., description="清理后队列里还剩多少条")
