"""请求指的是哪个场景（契约 §2.6 / A5）。

场景是 scenes 表里的行，通过 /api/scenes 配置，不是代码里的列表。请求用 code 指明场景：
为空表示 DEFAULT_SCENE，MVP 的 code normal / device 仍然映射到 general / oven
（app.core.scene_rules）。场景必须存在且启用；否则用请求所在接口的错误码拒绝
（SEARCH_ / LIST_ / INDEX_UNKNOWN_SCENE）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

from app.core import errors as err
from app.core.scene_rules import DEFAULT_SCENE, SCENE_ALIASES
from app.repositories.ports import MetadataStorePort

ErrorFactory = Callable[[str], err.AppError]


def canonicalize_scene_code(raw: str) -> str:
    """去掉首尾空白并映射 MVP code；不检查场景是否存在。"""
    code = raw.strip()
    return SCENE_ALIASES.get(code, code)


def requested_scene_code(value: Any, *, field: str = "scene") -> str:
    """请求要的 code：没传 / 为空 -> DEFAULT_SCENE。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_SCENE
    if not isinstance(value, str):
        raise err.validation_error(f"{field} must be a string, got {type(value).__name__}")
    return canonicalize_scene_code(value)


def _active_codes_text(metadata: MetadataStorePort) -> str:
    """只用于错误信息：不会把一次拒绝变成数据库失败。"""
    try:
        return ", ".join(metadata.list_scene_codes(active_only=True)) or "none"
    except Exception:
        return "unavailable"


def lookup_scene(
    metadata: MetadataStorePort,
    code: str,
    *,
    unknown: ErrorFactory,
    failed: ErrorFactory,
) -> dict[str, Any]:
    """有这个 code 的启用的 scenes 行：{id, code, name, include_all, outputs_heating, ...}。

    unknown：该接口的 *_UNKNOWN_SCENE 构造函数（场景不存在或停用）；
    failed：它的 *_POSTGRES_FAILED 构造函数（查询本身失败）。
    """
    try:
        row = metadata.get_scene_by_code(code)
    except err.AppError:
        raise
    except Exception as exc:
        raise failed(f"scene lookup {code!r}: {type(exc).__name__}: {exc}") from exc
    if row is None:
        raise unknown(f"unknown scene {code!r}; active scenes: {_active_codes_text(metadata)}")
    if not row.get("is_active"):
        raise unknown(f"scene {code!r} is inactive; active scenes: {_active_codes_text(metadata)}")
    return row


def resolve_index_scenes(
    metadata: MetadataStorePort,
    scene: Optional[str] = None,
    scenes: Optional[Sequence[Any]] = None,
) -> list[str]:
    """检查入库请求可能带的场景提示；返回规范化、去重后的提示。

    提示不决定归属：归属在插入后按规则和 image_scenes 重算。但仍然会检查它们，
    让客户端知道传了不存在的 code。
    没有提示 -> [DEFAULT_SCENE]，不访问数据库。
    """
    hints: list[str] = []
    if scenes is not None:
        if not isinstance(scenes, (list, tuple)):
            raise err.validation_error("scenes must be a JSON array of strings")
        if len(scenes) == 0:
            raise err.validation_error("scenes must be a non-empty array")
        for i, value in enumerate(scenes):
            if not isinstance(value, str) or not value.strip():
                raise err.validation_error(f"scenes[{i}] must be a non-empty string")
            code = canonicalize_scene_code(value)
            if code not in hints:
                hints.append(code)
    single: str | None = None
    if scene is not None and str(scene).strip():
        single = requested_scene_code(scene)
        if hints and set(hints) != {single}:
            raise err.validation_error(f"scene and scenes inconsistent: scene={single!r} scenes={hints!r}")
        hints = [single]
    if not hints:
        return [DEFAULT_SCENE]
    try:
        active = set(metadata.list_scene_codes(active_only=True))
    except err.AppError:
        raise
    except Exception as exc:
        raise err.index_postgres_failed(f"scene lookup: {type(exc).__name__}: {exc}") from exc
    for code in hints:
        if code not in active:
            raise err.index_unknown_scene(f"unknown or inactive scene {code!r}; active scenes: {', '.join(sorted(active)) or 'none'}")
    return hints
