"""导入配置文件（类别树、场景配置）的返回结果。

文件通过管理用例逐项应用，所以每一项都经过和管理页请求相同的检查和事务。
报告里每一项占一行：创建、更新或删除的（dry run 时：将要的），或者无法导入的；
和已存内容一致的项只计数。导入不是一个事务：某一行失败时，它前面的行已经生效，
再运行同一个文件会从停下的地方继续（已经存好的保持不变）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core import errors as err

SCHEMA_VERSION = 1
# 响应最多带的行数；失败的行排在前面
ROW_LIMIT = 1000
# 文件里没有列出的已存项，响应里最多列出这么多个（全部都会计数）
LEFTOVER_LIMIT = 200

_ACTIONS = ("create", "update", "delete", "unchanged", "error")


def check_envelope(document: Any, *, kind: str, items_key: str) -> list[Any]:
    """文件的条目列表。schema_version 和 kind 是可选的（GET 的响应体两个都没有）；
    出现时必须是本服务的版本，以及本接口导入的类型。
    """
    if not isinstance(document, Mapping):
        raise err.validation_error("the file must be a JSON object")
    version = document.get("schema_version")
    if "schema_version" in document and (type(version) is not int or version != SCHEMA_VERSION):
        raise err.validation_error(f"unsupported schema_version {version!r}; this server reads {SCHEMA_VERSION}")
    file_kind = document.get("kind")
    if "kind" in document and file_kind != kind:
        raise err.validation_error(f"this is a {file_kind!r} file; this endpoint imports {kind!r}")
    items = document.get(items_key)
    if not isinstance(items, list):
        raise err.validation_error(f"the file has no {items_key!r} array")
    return items


def ref_code(value: Any, field: str) -> str | None:
    """对带 code 的行的引用：它的 code，或者带 code 的对象（{code, ...}）。None 保持 None。"""
    if value is None:
        return None
    code = value.get("code") if isinstance(value, Mapping) else value
    if not isinstance(code, str) or not code.strip():
        raise err.validation_error(f"{field} must be a code, or an object with a code")
    return code.strip()


def text_of(value: Any) -> str | None:
    """报告里显示的值；没有内容时为 None（null、空列表）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or None
    return str(value)


def change(field: str, before: Any, after: Any) -> dict[str, Any]:
    return {"field": field, "before": text_of(before), "after": text_of(after)}


def text_field(raw: Mapping[str, Any], key: str) -> str | None:
    """raw[key] 是非空文本时返回它（用于在条目校验之前给行命名）。"""
    value = raw.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


class ImportReport:
    """收集一次导入的各行和计数。"""

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self._counts = dict.fromkeys(_ACTIONS, 0)
        self._rows: list[dict[str, Any]] = []

    def unchanged(self) -> None:
        self._counts["unchanged"] += 1

    def add(
        self,
        kind: str,
        key: str,
        action: str,
        *,
        name: str | None = None,
        scene: str | None = None,
        where: str | None = None,
        changes: Sequence[Mapping[str, Any]] = (),
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        """kind：category / scene / label / rule。key：识别这一项的内容（它的 code 或对象）。
        where：它在文件里的位置，例如 categories[0].children[2]。
        """
        self._counts[action] += 1
        self._rows.append({
            "kind": kind,
            "key": key,
            "action": action,
            "name": name,
            "scene": scene,
            "where": where,
            "changes": [dict(entry) for entry in changes],
            "error": error,
            "detail": detail,
        })

    def fail(self, kind: str, key: str, reason: err.AppError | str, **context: Any) -> None:
        """没有导入的一项：规则或存储抛出的 AppError，或者关于文件本身的原因（这时为
        VALIDATION_ERROR）。
        """
        if isinstance(reason, err.AppError):
            self.add(kind, key, "error", error=reason.code, detail=err.client_detail(reason), **context)
        else:
            self.add(kind, key, "error", error="VALIDATION_ERROR", detail=reason, **context)

    def body(self, *, total: int, not_in_file: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """{dry_run, summary, rows, rows_truncated, not_in_file}。total：文件列出的项数。"""
        rows = self._rows
        truncated = len(rows) > ROW_LIMIT
        if truncated:
            failed = [index for index, row in enumerate(rows) if row["action"] == "error"][:ROW_LIMIT]
            room = ROW_LIMIT - len(failed)
            others = [index for index, row in enumerate(rows) if row["action"] != "error"][:room]
            rows = [rows[index] for index in sorted([*failed, *others])]
        return {
            "dry_run": self.dry_run,
            "summary": {"total": total, **self._counts, "not_in_file": len(not_in_file)},
            "rows": rows,
            "rows_truncated": truncated,
            "not_in_file": [dict(entry) for entry in not_in_file[:LEFTOVER_LIMIT]],
        }
