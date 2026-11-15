"""What importing a configuration file answers (the category tree, the scene configuration).

A file is applied item by item through the admin use cases, so every item gets the checks and the
transaction a request from the admin page would get. The report has one row per item that was
created, updated or deleted (in a dry run: would be), or that could not be imported; items that
already match what is stored are only counted. An import is not one transaction: a row that fails
leaves the rows before it applied, and running the same file again continues where it stopped
(what is already stored is then unchanged).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core import errors as err

SCHEMA_VERSION = 1
# Rows a response carries at most; rows that failed are kept first
ROW_LIMIT = 1000
# Stored items the file does not list, named in the response at most this many (all are counted)
LEFTOVER_LIMIT = 200

_ACTIONS = ("create", "update", "delete", "unchanged", "error")


def check_envelope(document: Any, *, kind: str, items_key: str) -> list[Any]:
    """The file's item list. schema_version and kind are optional (GET bodies have neither); when
    present they must be this server's version and the kind this endpoint imports."""
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
    """A reference to a coded row: its code, or an object carrying it ({code, ...}). None stays None."""
    if value is None:
        return None
    code = value.get("code") if isinstance(value, Mapping) else value
    if not isinstance(code, str) or not code.strip():
        raise err.validation_error(f"{field} must be a code, or an object with a code")
    return code.strip()


def text_of(value: Any) -> str | None:
    """A value as a report shows it; None for nothing (null, an empty list)."""
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
    """raw[key] when it is non-blank text (for naming a row before the item is validated)."""
    value = raw.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


class ImportReport:
    """Collects the rows and the counts of one import."""

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
        """kind: category / scene / label / rule. key: what identifies the item (its code or
        target). where: its place in the file, e.g. categories[0].children[2]."""
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
        """An item that is not imported: an AppError from the rules or the store, or a reason about
        the file itself (then VALIDATION_ERROR)."""
        if isinstance(reason, err.AppError):
            self.add(kind, key, "error", error=reason.code, detail=err.client_detail(reason), **context)
        else:
            self.add(kind, key, "error", error="VALIDATION_ERROR", detail=reason, **context)

    def body(self, *, total: int, not_in_file: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """{dry_run, summary, rows, rows_truncated, not_in_file}. total: items the file lists."""
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
