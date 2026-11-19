"""index_tasks：入库请求中每个文件的结果（app.models.task.IndexTask）。

由入库用例在处理完每个文件后写入（尽力而为：写入失败只写日志，入库结果照常有效）；
由任务中心读取和清空（GET / DELETE /api/index-tasks）。

成功的记录在图片删除后仍保留 image_id（没有外键）：和 images 关联后，这样的记录已经
没有图片了（"deleted"），其他的仍然有（"present"）。

记录统计的是上传，不是图片：同样的字节再次入库也会再次成功（new_image 为 false：没有
存任何新东西），例如客户端放弃了第一次请求、但服务端已经处理完之后，文件又发了一次。
所以图片仍存在的成功记录分成存了它的（new_image 为 true）和重复的（new_image 为 false，
计为 "repeated"）：只有前一种说明上传往图库里放了多少张图片。
"""

from __future__ import annotations

from typing import Any

from app.core import errors as err
from app.core.enums import IndexTaskStatus, NameKind
from app.db.common import _as_uuid, _row_to_dict

# 写进记录的内容的长度上限（这些列是 text）：异常长的文件名或信息不能撑大这张表
_MAX_FILENAME = 255
_MAX_DETAIL = 1000
_MAX_CODE = 64
_MAX_TRACE = 128


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _filter(
    status: str | None, image_state: str | None, new_image: bool | None = None
) -> tuple[str, dict[str, Any]]:
    """基于 `index_tasks t LEFT JOIN images i ON i.id = t.image_id` 的 WHERE 条件，列表和清空共用，
    这样清空删除的正好是列表显示的内容。image_state：present（图片仍存在的成功记录）/
    deleted（图片之后被删除了的）。new_image：True（存了新图片的成功记录）/ False
    （重复：同样的字节之前就存过）。_matching 在计数分组上表达同样的条件。
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if status:
        clauses.append("t.status = :status")
        params["status"] = IndexTaskStatus(status).value
    if image_state:
        if image_state not in ("present", "deleted"):
            raise ValueError(f"unknown image_state {image_state!r}")
        clauses.append("t.status = :succeeded AND i.id IS " + ("NOT NULL" if image_state == "present" else "NULL"))
        params["succeeded"] = IndexTaskStatus.SUCCEEDED.value
    if new_image is not None:
        # IS [NOT] FALSE：没有这个标志的成功记录不能认定为重复
        clauses.append("t.status = :succeeded AND t.new_image IS " + ("NOT FALSE" if new_image else "FALSE"))
        params["succeeded"] = IndexTaskStatus.SUCCEEDED.value
    return ("WHERE " + " AND ".join(clauses) + " ") if clauses else "", params


# 记录的一个计数分组：(status, image_gone, repeated, 数量)。image_gone：没有哪一行图片的 id
# 是这条记录的 image_id（失败的记录总是如此）；repeated：new_image IS FALSE。
_Group = tuple[str, bool, bool, int]

_GROUPS_SQL = (
    "SELECT t.status, i.id IS NULL AS image_gone, t.new_image IS FALSE AS repeated, count(*) "
    "FROM index_tasks t LEFT JOIN images i ON i.id = t.image_id GROUP BY 1, 2, 3"
)


def _counts(groups: list[_Group]) -> dict[str, int]:
    """按全部记录统计的 {succeeded, failed, image_deleted, repeated}。image_deleted 和 repeated
    都是 succeeded 的一部分，并且从不重叠：图片之后被删除了的重复记录计为 image_deleted，
    所以 succeeded - image_deleted - repeated 就是存了一张至今仍在图库里的图片的记录数。
    """
    succeeded = IndexTaskStatus.SUCCEEDED.value
    counts = {member.value: 0 for member in IndexTaskStatus}
    counts["image_deleted"] = 0
    counts["repeated"] = 0
    for status, image_gone, repeated, number in groups:
        counts[status] += number
        if status != succeeded:
            continue
        if image_gone:
            counts["image_deleted"] += number
        elif repeated:
            counts["repeated"] += number
    return counts


def _matching(
    groups: list[_Group], status: str | None, image_state: str | None, new_image: bool | None = None
) -> int:
    """一个过滤条件选中多少条记录，从计数分组得出（计数和总数一次读取）。
    条件和 _filter 相同。
    """
    succeeded = IndexTaskStatus.SUCCEEDED.value
    wanted = IndexTaskStatus(status).value if status else None
    total = 0
    for group_status, image_gone, repeated, number in groups:
        if wanted is not None and group_status != wanted:
            continue
        if image_state and not (group_status == succeeded and image_gone == (image_state == "deleted")):
            continue
        if new_image is not None and not (group_status == succeeded and repeated != new_image):
            continue
        total += number
    return total


def _record_params(row: dict[str, Any]) -> dict[str, Any]:
    status = IndexTaskStatus(row["status"])
    image_id = row.get("image_id")
    duration = row.get("duration_ms")
    return {
        "filename": _clip(row.get("filename") or "unknown", _MAX_FILENAME),
        "status": status.value,
        "image_id": str(_as_uuid(image_id)) if image_id and status is IndexTaskStatus.SUCCEEDED else None,
        "new_image": row.get("new_image") if status is IndexTaskStatus.SUCCEEDED else None,
        "error_code": _clip(row.get("error_code"), _MAX_CODE) if status is IndexTaskStatus.FAILED else None,
        "error_detail": _clip(row.get("error_detail"), _MAX_DETAIL) if status is IndexTaskStatus.FAILED else None,
        "duration_ms": max(0, int(duration)) if duration is not None else None,
        "trace_id": _clip(row.get("trace_id"), _MAX_TRACE),
    }


class PgIndexTasksMixin:
    """任务中心的记录。"""

    def record_index_tasks(self, rows: list[dict[str, Any]]) -> None:
        """每个文件插入一条记录：{filename, status, image_id?, new_image?, error_code?,
        error_detail?, duration_ms?, trace_id?}。成功的记录必须有 image_id。
        """
        if not rows:
            return
        self.ensure_schema()
        from sqlalchemy import text

        params = [_record_params(row) for row in rows]
        try:
            with self._get_engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO index_tasks "
                        "(filename, status, image_id, new_image, error_code, error_detail, duration_ms, trace_id) "
                        "VALUES (:filename, :status, CAST(:image_id AS uuid), :new_image, :error_code, "
                        ":error_detail, :duration_ms, :trace_id)"
                    ),
                    params,
                )
        except Exception as exc:
            raise err.index_postgres_failed(f"record_index_tasks: {type(exc).__name__}: {exc}") from exc

    def list_index_tasks(
        self,
        *,
        status: str | None = None,
        image_state: str | None = None,
        new_image: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """最新的在前，可选按一个状态、图片状态和 / 或新图片标志过滤：
        {items, total, counts: {succeeded, failed, image_deleted, repeated}}。image_deleted：图片之后
        被删除了的成功记录；repeated：图片仍存在、但在这次上传之前就已经存过的（new_image 为
        false）。两者也都计入 succeeded。图片仍存在时，每一项带上它的缩略图和类别。
        """
        self.ensure_schema()
        from sqlalchemy import text

        size = max(1, min(int(page_size), 100))
        offset = (max(1, int(page)) - 1) * size
        where, filter_params = _filter(status, image_state, new_image)
        params: dict[str, Any] = {"lim": size, "off": offset, "primary_kind": NameKind.PRIMARY.value, **filter_params}
        try:
            with self._get_engine().connect() as conn:
                groups: list[_Group] = [
                    (str(row[0]), bool(row[1]), bool(row[2]), int(row[3]))
                    for row in conn.execute(text(_GROUPS_SQL))
                ]
                rows = conn.execute(
                    text(
                        "SELECT t.id, t.filename, t.status, t.image_id::text AS image_id, t.new_image, "
                        "t.error_code, t.error_detail, t.duration_ms, t.trace_id, t.created_at, "
                        "i.id IS NOT NULL AS image_exists, i.thumb_url, i.category_id, "
                        "i.label_status AS image_label_status, cn.name AS category_name "
                        "FROM index_tasks t "
                        "LEFT JOIN images i ON i.id = t.image_id "
                        "LEFT JOIN category_names cn ON cn.category_id = i.category_id "
                        "  AND cn.lang = 'zh' AND cn.kind = :primary_kind "
                        + where
                        + "ORDER BY t.created_at DESC, t.id DESC LIMIT :lim OFFSET :off"
                    ),
                    params,
                ).mappings().fetchall()
        except Exception as exc:
            raise err.list_postgres_failed(f"list_index_tasks: {type(exc).__name__}: {exc}") from exc
        return {
            "items": [_row_to_dict(row) for row in rows],
            "total": _matching(groups, status, image_state, new_image),
            "counts": _counts(groups),
        }

    def clear_index_tasks(self, *, status: str | None = None, image_state: str | None = None) -> int:
        """删除列表在这个过滤条件下显示的记录（两者至少一个：永远不会意外删光）；
        返回删除了多少。只删记录：不碰图片。
        """
        if not status and not image_state:
            raise ValueError("clear_index_tasks needs a status or an image_state")
        self.ensure_schema()
        from sqlalchemy import text

        where, params = _filter(status, image_state)
        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text(
                        "DELETE FROM index_tasks WHERE id IN (SELECT t.id FROM index_tasks t "
                        "LEFT JOIN images i ON i.id = t.image_id " + where + ")"
                    ),
                    params,
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"clear_index_tasks: {type(exc).__name__}: {exc}") from exc

    def delete_index_task(self, task_id: int) -> int:
        """删除一条记录；存在时返回 1，不存在时返回 0。不碰图片。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text("DELETE FROM index_tasks WHERE id = :id"), {"id": int(task_id)}
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"delete_index_task: {type(exc).__name__}: {exc}") from exc
