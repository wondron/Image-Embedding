"""Scene registry + resolve helpers (SSOT §2 / §3 / §4 · 2026-10-28).

MVP registered: normal, device. Domain owns validation; adapters only persist/filter.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.domain.retrieval import errors as err

REGISTERED_SCENES = frozenset({"normal", "device"})
DEFAULT_SCENE = "normal"
DEFAULT_SCENES: list[str] = [DEFAULT_SCENE]


def is_registered(scene: str) -> bool:
    return scene in REGISTERED_SCENES


def _as_short_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise err.validation_error(f"{where} must be a string, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise err.validation_error(f"{where} must be a non-empty short string")
    return s


def _validate_registered(scenes: Sequence[str], *, code_for_unknown: str) -> list[str]:
    """Deduplicate preserving order; raise on unknown."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in scenes:
        s = _as_short_str(raw, where="scene")
        if s not in REGISTERED_SCENES:
            detail = f"unknown scene: {s!r}; registered={sorted(REGISTERED_SCENES)}"
            if code_for_unknown == "INDEX_UNKNOWN_SCENE":
                raise err.index_unknown_scene(detail)
            if code_for_unknown == "SEARCH_UNKNOWN_SCENE":
                raise err.search_unknown_scene(detail)
            if code_for_unknown == "LIST_UNKNOWN_SCENE":
                raise err.list_unknown_scene(detail)
            raise err.validation_error(detail)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def resolve_index_scenes(
    scene: Optional[str] = None,
    scenes: Optional[Sequence[Any]] = None,
) -> list[str]:
    """Resolve index write set per SSOT section 3.1.

    Priority: scenes array if provided; else scene single; else default [normal].
    Both provided must be consistent (scenes normalizes to [scene]); else 422.
    Empty array / unknown -> INDEX_UNKNOWN_SCENE or VALIDATION_ERROR.
    """
    has_scene = scene is not None and str(scene).strip() != ""
    has_scenes = scenes is not None

    if has_scenes:
        if not isinstance(scenes, (list, tuple)):
            raise err.validation_error("scenes must be a JSON array of strings")
        if len(scenes) == 0:
            raise err.validation_error("scenes must be a non-empty array")
        resolved_from_array = _validate_registered(
            [str(x) for x in scenes],
            code_for_unknown="INDEX_UNKNOWN_SCENE",
        )
        if has_scene:
            single = _as_short_str(scene, where="scene")
            if single not in REGISTERED_SCENES:
                raise err.index_unknown_scene(
                    f"unknown scene: {single!r}; registered={sorted(REGISTERED_SCENES)}"
                )
            # Consistent means scenes == [scene] after normalize
            if set(resolved_from_array) != {single}:
                raise err.validation_error(
                    f"scene and scenes inconsistent: scene={single!r} scenes={resolved_from_array!r}"
                )
            return [single]
        return resolved_from_array

    if has_scene:
        single = _as_short_str(scene, where="scene")
        return _validate_registered([single], code_for_unknown="INDEX_UNKNOWN_SCENE")

    return list(DEFAULT_SCENES)


def normalize_search_scene(scene: Optional[str] = None) -> str:
    """Search filter scene; omit/blank -> normal. Unknown -> SEARCH_UNKNOWN_SCENE."""
    if scene is None or (isinstance(scene, str) and scene.strip() == ""):
        return DEFAULT_SCENE
    s = _as_short_str(scene, where="scene")
    if s not in REGISTERED_SCENES:
        raise err.search_unknown_scene(
            f"unknown scene: {s!r}; registered={sorted(REGISTERED_SCENES)}"
        )
    return s


def normalize_list_scene(scene: Optional[str] = None) -> str:
    """List filter scene; omit/blank -> normal. Unknown -> LIST_UNKNOWN_SCENE."""
    if scene is None or (isinstance(scene, str) and scene.strip() == ""):
        return DEFAULT_SCENE
    s = _as_short_str(scene, where="scene")
    if s not in REGISTERED_SCENES:
        raise err.list_unknown_scene(
            f"unknown scene: {s!r}; registered={sorted(REGISTERED_SCENES)}"
        )
    return s


def allows_flyb_fallback(scene: str) -> bool:
    """FlyB only when scene is normal (after normalize)."""
    return scene == DEFAULT_SCENE
