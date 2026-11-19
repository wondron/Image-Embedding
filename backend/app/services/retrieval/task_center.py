"""任务中心：入库记录和删除队列（GET/DELETE /api/index-tasks、GET /api/image-deletions）。

只用 PostgreSQL（和图库管理一样由 app.bootstrap.catalog 构建），所以编码器或 Qdrant
挂了时历史记录也能读。处理删除队列需要向量库和对象存储：为此 HTTP 层运行检索服务的
DeletionWorker（POST /api/image-deletions/process）。

记录由入库用例写入（services.retrieval.index_image），每个文件一条。

删除图片会保留它的记录（它们说明什么时候入库了什么）：这样的成功记录显示
image_state=deleted，可以单独列出（image_state 过滤）和单独清空
（DELETE ?image_state=deleted），也可以逐条删除（DELETE /api/index-tasks/{id}）。
清空只删除记录，从不删除图片。

一条记录对应一次上传，不是一张图片：再次上传同样的字节会再次成功，但不存任何东西
（new_image 为 false）。counts.repeated 说明仍存在的图片有多少这样的记录，new_image
过滤把它们和存了新图片的上传分开列出，这样能从记录里读出新图片的数量。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from app.core import errors as err
from app.core.enums import IndexTaskStatus
from app.repositories.ports import CatalogStorePort
from app.core.rules.enum_fields import coerce_optional_enum

MAX_PAGE_SIZE = 100
MAX_DELETIONS = 200
MAX_TASK_ID = 2**63 - 1  # index_tasks.id 是 bigint

# 成功记录的图片是否仍然存在：读取时推导（没有列，也没有 CHECK），
# 所以不是 app.core.enums 里的系统枚举
IndexTaskImageState = Literal["present", "deleted"]
IMAGE_STATES: tuple[str, ...] = ("present", "deleted")


def _image_state(value: Any) -> str | None:
    if value is None:
        return None
    if value not in IMAGE_STATES:
        raise err.validation_error(f"image_state must be one of {', '.join(IMAGE_STATES)}, got {value!r}")
    return value


def _new_image(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise err.validation_error(f"new_image must be true or false, got {value!r}")


def index_task_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """wire 记录。`image` 是图片现在的样子：文件失败时为 null，或者图片之后被删除了时也为 null
    （成功记录保留它的 image_id；image_state 说明是哪种）。
    """
    image = None
    image_state = None
    if row.get("status") == IndexTaskStatus.SUCCEEDED.value:
        image_state = "present" if row.get("image_exists") else "deleted"
    if row.get("image_exists"):
        image = {
            "id": row.get("image_id"),
            "thumb_url": row.get("thumb_url"),
            "category_id": row.get("category_id"),
            "category": row.get("category_name"),
            "label_status": row.get("image_label_status"),
        }
    return {
        "id": row.get("id"),
        "filename": row.get("filename"),
        "status": row.get("status"),
        "image_id": row.get("image_id"),
        "new_image": row.get("new_image"),
        "error_code": row.get("error_code"),
        "error_detail": row.get("error_detail"),
        "duration_ms": row.get("duration_ms"),
        "trace_id": row.get("trace_id"),
        "created_at": row.get("created_at"),
        "image_state": image_state,
        "image": image,
    }


def deletion_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "image_id": str(row.get("image_id")),
        "original_url": row.get("original_url"),
        "thumb_url": row.get("thumb_url"),
        "deleted_at": row.get("deleted_at"),
    }


def _bounded(value: Any, *, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise err.validation_error(f"{field} must be an integer in {low}..{high}, got {value!r}")
    return value


class TaskCenterUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def list_index_tasks(
        self, *, status: Any = None, image_state: Any = None, new_image: Any = None, page: Any = 1, page_size: Any = 50
    ) -> dict[str, Any]:
        """最新的在前；`status` 过滤（succeeded / failed），`image_state` 过滤成功的记录
        （present / deleted），`new_image` 也过滤成功的记录（True：存了一张新图片 /
        False：重复上传已存的图片）。counts 按全部记录统计。
        """
        wanted = coerce_optional_enum(IndexTaskStatus, status, field="status")
        state = _image_state(image_state)
        fresh = _new_image(new_image)
        page = _bounded(page, field="page", low=1, high=1_000_000)
        page_size = _bounded(page_size, field="page_size", low=1, high=MAX_PAGE_SIZE)
        result = self._catalog.list_index_tasks(
            status=wanted.value if wanted else None, image_state=state, new_image=fresh, page=page, page_size=page_size
        )
        counts = dict(result.get("counts") or {})
        return {
            "status": wanted.value if wanted else None,
            "image_state": state,
            "new_image": fresh,
            "page": page,
            "page_size": page_size,
            "total": int(result.get("total") or 0),
            "counts": {
                **{member.value: int(counts.get(member.value) or 0) for member in IndexTaskStatus},
                "image_deleted": int(counts.get("image_deleted") or 0),
                "repeated": int(counts.get("repeated") or 0),
            },
            "items": [index_task_item(row) for row in result.get("items") or []],
        }

    def clear_index_tasks(self, *, status: Any = None, image_state: Any = None) -> dict[str, Any]:
        """删除一个过滤条件下的记录：状态和 / 或图片状态，至少一个（永远不会意外删光）。
        只删记录；不碰图片。
        """
        wanted = coerce_optional_enum(IndexTaskStatus, status, field="status")
        state = _image_state(image_state)
        if wanted is None and state is None:
            raise err.validation_error("status or image_state is required (clearing never removes every record)")
        deleted = self._catalog.clear_index_tasks(status=wanted.value if wanted else None, image_state=state)
        return {"status": wanted.value if wanted else None, "image_state": state, "deleted": deleted}

    def delete_index_task(self, task_id: Any) -> dict[str, Any]:
        """删除一条记录（不碰它的图片）：deleted 为 1，不存在时为 0。"""
        task_id = _bounded(task_id, field="task_id", low=1, high=MAX_TASK_ID)
        return {"id": task_id, "deleted": self._catalog.delete_index_task(task_id)}

    def list_deletions(self, *, limit: Any = 50) -> dict[str, Any]:
        """排队的清理，最早的在前，以及总数。"""
        limit = _bounded(limit, field="limit", low=1, high=MAX_DELETIONS)
        rows = self._catalog.list_pending_deletions(limit=limit)
        return {"total": self._catalog.count_pending_deletions(), "items": [deletion_item(row) for row in rows]}

    def count_deletions(self) -> int:
        return self._catalog.count_pending_deletions()
