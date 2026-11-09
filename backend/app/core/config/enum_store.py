"""枚举文件 config/param/enums.yaml 的读写（两类枚举的约定见 app.core.enums）。

读：EnumStore.catalog() 返回当前快照（不可变，多线程直接用）。每次调用先比较文件的修改时间和
大小，变了就重新读，所以手工改文件不用重启。新内容读不出来或不合规时继续用上一份，原因记在
EnumStore.error 里并打 ERROR 日志；一份都没读到时抛 ENUM_FILE_INVALID（503）。

写：EnumStore.add_value() 在锁里重新读一遍磁盘上的文件再追加，不会覆盖刚手工改过的内容；
文件当时读不出来就拒绝写。写入按 dump_catalog 的固定格式重写整个文件，只保留文件头注释。

同一进程内的写是串行的；多个进程（多 worker、多个容器挂同一个文件）同时追加时，后写的会
覆盖先写的。容器里的文件随镜像走，要保留接口追加的取值，需要把这个文件挂载出来。
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.core import errors
from app.core.config.settings import settings
from app.core.enums import (
    EDITABLE_ENUMS,
    ENUM_DESCRIPTION_MAX_LEN,
    ENUM_LABEL_MAX_LEN,
    ENUM_MAX_VALUES,
    ENUM_VALUE_MAX_LEN,
    ENUM_VALUE_PATTERN,
    SYSTEM_ENUMS,
)

logger = logging.getLogger("config.enums")

FILE_HEADER = """\
# 枚举取值：数据库、接口和 Qdrant payload 共用这里的 value。后端读写见 app/core/config/enum_store.py；
# 接口 GET /api/enums 查询全部，POST /api/enums/{name}/values 追加取值。
#
# editable: true   可编辑枚举，取值以本文件为准：可以直接改文件（不用重启，下次用到时自动重新读取），
#                  也可以调接口追加。
# editable: false  接口不能追加。系统枚举（代码按取值分支、库里有 CHECK 约束）只能是 false，
#                  取值必须和 app/core/enums.py 一致，这里只能改 label / description。
#
# value 只用小写字母、数字和下划线，字母开头，最长 32 个字符。库里存的就是 value，用过的取值不要删、不要改名。
# 接口追加时会按固定格式重写整个文件，只保留这段文件头；补充说明写进 description，不要写成注释。
"""

_VALUE_RE = re.compile(ENUM_VALUE_PATTERN)
_ENUM_KEYS = ("label", "description", "editable", "values")
_VALUE_KEYS = ("value", "label", "description")
_KNOWN_ENUMS = (*SYSTEM_ENUMS, *EDITABLE_ENUMS)
_YAML_WIDTH = 10_000  # one line per value: never fold
_UNREAD = object()  # EnumStore._stamp before the first read


class EnumFileError(ValueError):
    """The enums file cannot be read or breaks a rule; the message says where."""


@dataclass(frozen=True, slots=True)
class EnumValue:
    value: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class EnumDef:
    name: str
    label: str
    values: tuple[EnumValue, ...]
    editable: bool = False
    description: str = ""

    def value_list(self) -> list[str]:
        return [v.value for v in self.values]

    def __contains__(self, value: object) -> bool:
        return any(v.value == value for v in self.values)


@dataclass(frozen=True, slots=True)
class EnumCatalog:
    """One consistent version of the file; enums keep the file's order."""

    enums: tuple[EnumDef, ...]

    def __iter__(self) -> Iterator[EnumDef]:
        return iter(self.enums)

    def get(self, name: str) -> EnumDef | None:
        return next((d for d in self.enums if d.name == name), None)

    def names(self) -> list[str]:
        return [d.name for d in self.enums]

    def replacing(self, definition: EnumDef) -> EnumCatalog:
        """A copy with the enum of the same name replaced by definition."""
        return EnumCatalog(tuple(definition if d.name == definition.name else d for d in self.enums))


# -- rules shared by the file and the API ------------------------------------------


def value_problem(value: Any) -> str | None:
    """Why value cannot be an enum value; None when it can."""
    if not isinstance(value, str):
        return f"must be a string, got {value!r} (quote yes / no / on / off / null in YAML)"
    if len(value) > ENUM_VALUE_MAX_LEN or not _VALUE_RE.fullmatch(value):
        return (
            "must be lowercase letters, digits or '_', start with a letter and be at most "
            f"{ENUM_VALUE_MAX_LEN} characters, got {value!r}"
        )
    return None


def _text_problem(value: Any, *, required: bool, max_len: int) -> str | None:
    if value is None:
        return "is required" if required else None
    if not isinstance(value, str):
        return f"must be a string, got {value!r}"
    if required and not value.strip():
        return "is required"
    if len(value.strip()) > max_len:
        return f"must be at most {max_len} characters"
    return None


# -- parse / dump ------------------------------------------------------------------


def parse_catalog(data: Any, source: str) -> EnumCatalog:
    """Check a loaded YAML document against the file rules and the code (app.core.enums)."""
    if not isinstance(data, Mapping) or not data:
        raise EnumFileError(f"{source}: must be a mapping of enum name -> definition")
    enums = []
    for name, body in data.items():
        if name not in _KNOWN_ENUMS:
            raise EnumFileError(
                f"{source}: unknown enum {name!r}; enums are declared in app/core/enums.py: {list(_KNOWN_ENUMS)}"
            )
        enums.append(_parse_enum(name, body, f"{source}: {name}"))
    missing = [name for name in _KNOWN_ENUMS if name not in data]
    if missing:
        raise EnumFileError(f"{source}: missing enums {missing}")
    return EnumCatalog(tuple(enums))


def _parse_enum(name: str, body: Any, where: str) -> EnumDef:
    if not isinstance(body, Mapping):
        raise EnumFileError(f"{where}: must be a mapping with label / editable / values")
    _reject_unknown_keys(body, _ENUM_KEYS, where)
    editable = body.get("editable", False)
    if not isinstance(editable, bool):
        raise EnumFileError(f"{where}.editable: must be true or false, got {editable!r}")
    raw_values = body.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        raise EnumFileError(f"{where}.values: must be a non-empty list")
    if len(raw_values) > ENUM_MAX_VALUES:
        raise EnumFileError(f"{where}.values: at most {ENUM_MAX_VALUES} values")
    values: list[EnumValue] = []
    for i, item in enumerate(raw_values):
        value = _parse_value(item, f"{where}.values[{i}]")
        if value.value in (v.value for v in values):
            raise EnumFileError(f"{where}.values[{i}]: duplicate value {value.value!r}")
        values.append(value)
    definition = EnumDef(
        name=name,
        label=_text(body, "label", where, required=True, max_len=ENUM_LABEL_MAX_LEN),
        description=_text(body, "description", where, required=False, max_len=ENUM_DESCRIPTION_MAX_LEN),
        editable=editable,
        values=tuple(values),
    )
    _check_against_code(definition, where)
    return definition


def _parse_value(item: Any, where: str) -> EnumValue:
    if not isinstance(item, Mapping):
        raise EnumFileError(f"{where}: must be a mapping like {{value: raw, label: 生}}")
    _reject_unknown_keys(item, _VALUE_KEYS, where)
    problem = value_problem(item.get("value"))
    if problem:
        raise EnumFileError(f"{where}.value: {problem}")
    return EnumValue(
        value=item["value"],
        label=_text(item, "label", where, required=True, max_len=ENUM_LABEL_MAX_LEN),
        description=_text(item, "description", where, required=False, max_len=ENUM_DESCRIPTION_MAX_LEN),
    )


def _reject_unknown_keys(body: Mapping[Any, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = [key for key in body if key not in allowed]
    if unknown:
        raise EnumFileError(f"{where}: unknown keys {unknown}; allowed: {list(allowed)}")


def _text(body: Mapping[str, Any], key: str, where: str, *, required: bool, max_len: int) -> str:
    value = body.get(key)
    problem = _text_problem(value, required=required, max_len=max_len)
    if problem:
        raise EnumFileError(f"{where}.{key}: {problem}")
    return (value or "").strip()


def _check_against_code(definition: EnumDef, where: str) -> None:
    system = SYSTEM_ENUMS.get(definition.name)
    if system is not None:
        code_ref = f"app.core.enums.{system.__name__}"
        if definition.editable:
            raise EnumFileError(f"{where}.editable: a system enum ({code_ref}) must be false")
        expected = [m.value for m in system]
        if set(definition.value_list()) != set(expected):
            raise EnumFileError(
                f"{where}.values: a system enum takes its values from {code_ref}: "
                f"expected {expected}, got {definition.value_list()}"
            )
        return
    missing = sorted(EDITABLE_ENUMS[definition.name] - set(definition.value_list()))
    if missing:
        raise EnumFileError(f"{where}.values: {missing} are used by the code and cannot be removed")


def read_catalog(path: Path) -> EnumCatalog:
    """Read and check the file (no caching)."""
    where = path.as_posix()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EnumFileError(f"{where}: cannot read ({exc.strerror or exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EnumFileError(f"{where}: invalid YAML: {exc}") from exc
    return parse_catalog(data, where)


def dump_catalog(catalog: EnumCatalog) -> str:
    """The file text for a catalog: FILE_HEADER, then one block per enum and one line per value."""
    blocks = [
        yaml.safe_dump(
            {d.name: _enum_doc(d)},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=None,  # value mappings hold only scalars -> {value: raw, label: 生}
            width=_YAML_WIDTH,
        )
        for d in catalog
    ]
    return FILE_HEADER + "\n" + "\n".join(blocks)


def _enum_doc(definition: EnumDef) -> dict[str, Any]:
    doc: dict[str, Any] = {"label": definition.label}
    if definition.description:
        doc["description"] = definition.description
    doc["editable"] = definition.editable
    doc["values"] = [_value_doc(v) for v in definition.values]
    return doc


def _value_doc(value: EnumValue) -> dict[str, str]:
    doc = {"value": value.value, "label": value.label}
    if value.description:
        doc["description"] = value.description
    return doc


def _write_text(path: Path, text: str) -> None:
    """Write through a temp file in the same directory + os.replace, so readers never see half a file.

    Falls back to overwriting in place when the replace is impossible: a file bind-mounted on its
    own into a container (rename onto a mount point fails with EBUSY) or a read-only directory.
    """
    data = text.encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        _write_bytes(tmp, data)
        os.replace(tmp, path)
        return
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        logger.warning("cannot replace %s atomically (%s); overwriting it in place", path.as_posix(), exc)
    _write_bytes(path, data)


def _write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


# -- the store ---------------------------------------------------------------------


class EnumStore:
    """The enums file, re-read when it changes on disk; see the module docstring."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()  # serializes reloads and writes
        self._catalog: EnumCatalog | None = None  # last version that passed the checks
        self._stamp: object = _UNREAD  # (mtime_ns, size) of the version last read; None = unreadable
        self._error: str | None = None  # why that version is not in use

    @property
    def where(self) -> str:
        return self.path.as_posix()

    @property
    def error(self) -> str | None:
        """Why the file now on disk is not the version in use; None when it is."""
        self._refresh()
        return self._error

    def catalog(self) -> EnumCatalog:
        self._refresh()
        catalog = self._catalog
        if catalog is None:
            raise errors.enum_file_invalid(self._error or f"{self.where}: not loaded")
        return catalog

    def get(self, name: str) -> EnumDef:
        catalog = self.catalog()
        definition = catalog.get(name)
        if definition is None:
            raise errors.enum_not_found(f"unknown enum {name!r}; enums: {catalog.names()}")
        return definition

    def add_value(self, name: str, value: Any, label: Any, description: Any = "") -> EnumDef:
        """Append one value to an editable enum and rewrite the file; returns the updated enum."""
        with self._lock:
            stamp = self._file_stamp()
            try:
                current = read_catalog(self.path)  # what is on disk now, not the cached snapshot
            except EnumFileError as exc:
                self._remember_error(stamp, str(exc))
                raise errors.enum_file_invalid(f"{exc}; fix the file before adding values") from exc
            definition = current.get(name)
            if definition is None:
                raise errors.enum_not_found(f"unknown enum {name!r}; enums: {current.names()}")
            if not definition.editable:
                raise errors.enum_not_editable(_not_editable_reason(name, self.where))
            new = _new_value(value, label, description)
            if new.value in definition:
                raise errors.enum_value_exists(f"{name} already has the value {new.value!r}")
            if len(definition.values) >= ENUM_MAX_VALUES:
                raise errors.validation_error(f"{name} already has {ENUM_MAX_VALUES} values (the maximum)")
            updated_def = replace(definition, values=(*definition.values, new))
            updated = current.replacing(updated_def)
            try:
                _write_text(self.path, dump_catalog(updated))
            except OSError as exc:
                raise errors.enum_file_write_failed(f"{self.where}: {type(exc).__name__}: {exc}") from exc
            self._catalog, self._stamp, self._error = updated, self._file_stamp(), None
        logger.info("enum value added | enum=%s | value=%s | label=%s | file=%s", name, new.value, new.label, self.where)
        return updated_def

    def _refresh(self) -> None:
        stamp = self._file_stamp()
        if stamp == self._stamp:
            return
        with self._lock:
            stamp = self._file_stamp()
            if stamp == self._stamp:
                return
            try:
                catalog = read_catalog(self.path)
            except EnumFileError as exc:
                self._remember_error(stamp, str(exc))
                return
            first = self._catalog is None
            self._catalog, self._stamp, self._error = catalog, stamp, None
        logger.info("enums %s | file=%s | enums=%d", "loaded" if first else "reloaded", self.where, len(catalog.enums))

    def _remember_error(self, stamp: object, message: str) -> None:
        """Called under the lock; logs once per file version."""
        if stamp == self._stamp and message == self._error:
            return
        self._stamp, self._error = stamp, message
        if self._catalog is None:
            logger.error("enum file unusable: %s", message)
        else:
            logger.error("enum file changed but cannot be used; keeping the previous version: %s", message)

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size


def _not_editable_reason(name: str, where: str) -> str:
    system = SYSTEM_ENUMS.get(name)
    if system is not None:
        return f"{name} is a system enum: its values are defined in app.core.enums.{system.__name__}"
    return f"{name} is locked (editable: false in {where})"


def _new_value(value: Any, label: Any, description: Any) -> EnumValue:
    value = value.strip() if isinstance(value, str) else value
    for field, problem in (
        ("value", value_problem(value)),
        ("label", _text_problem(label, required=True, max_len=ENUM_LABEL_MAX_LEN)),
        ("description", _text_problem(description, required=False, max_len=ENUM_DESCRIPTION_MAX_LEN)),
    ):
        if problem:
            raise errors.validation_error(f"{field} {problem}")
    return EnumValue(value=value, label=label.strip(), description=(description or "").strip())


# -- process-wide store ------------------------------------------------------------

_store: EnumStore | None = None
_store_lock = threading.Lock()


def get_enum_store() -> EnumStore:
    """The store on settings.config.enums_path, created on first use."""
    global _store
    store = _store
    if store is None:
        with _store_lock:
            if _store is None:
                _store = EnumStore(settings.config.enums_path)
            store = _store
    return store


def set_enum_store(store: EnumStore | None) -> EnumStore | None:
    """Replace the process-wide store (tests); None goes back to the default path. Returns the previous one."""
    global _store
    with _store_lock:
        previous, _store = _store, store
    return previous
