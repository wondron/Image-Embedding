"""Scene registry + resolve helpers (PG v0.1 contract §2.6 / A5).

Registered seeds: general, oven, range_hood, us_diet, cn_diet, jp_diet.
MVP aliases (map+document): normal→general, device→oven.
Default search/list/index scene = general.
FlyB: OUT OF SCOPE (A4).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.core import errors as err

# Contract §2.6 seed codes
REGISTERED_SCENES = frozenset(
    {"general", "oven", "range_hood", "us_diet", "cn_diet", "jp_diet"}
)
DEFAULT_SCENE = "general"
DEFAULT_SCENES: list[str] = [DEFAULT_SCENE]

# MVP → v0.1 product mapping (A5)
SCENE_ALIASES: dict[str, str] = {
    "normal": "general",
    "device": "oven",
}


def canonicalize_scene_code(raw: str) -> str:
    """Strip + apply MVP alias map. Does not validate registration."""
    s = raw.strip()
    return SCENE_ALIASES.get(s, s)


def is_registered(scene: str) -> bool:
    return canonicalize_scene_code(scene) in REGISTERED_SCENES


def _as_short_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise err.validation_error(
            f"{where} must be a string, got {type(value).__name__}"
        )
    s = value.strip()
    if not s:
        raise err.validation_error(f"{where} must be a non-empty short string")
    return s


def _raise_unknown(s: str, *, code_for_unknown: str) -> None:
    detail = (
        f"unknown scene: {s!r}; registered={sorted(REGISTERED_SCENES)}; "
        f"aliases={SCENE_ALIASES}"
    )
    if code_for_unknown == "INDEX_UNKNOWN_SCENE":
        raise err.index_unknown_scene(detail)
    if code_for_unknown == "SEARCH_UNKNOWN_SCENE":
        raise err.search_unknown_scene(detail)
    if code_for_unknown == "LIST_UNKNOWN_SCENE":
        raise err.list_unknown_scene(detail)
    raise err.validation_error(detail)


def _validate_registered(scenes: Sequence[str], *, code_for_unknown: str) -> list[str]:
    """Alias-map, dedupe preserving order; raise on unknown."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in scenes:
        s = canonicalize_scene_code(_as_short_str(raw, where="scene"))
        if s not in REGISTERED_SCENES:
            _raise_unknown(s, code_for_unknown=code_for_unknown)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def resolve_index_scenes(
    scene: Optional[str] = None,
    scenes: Optional[Sequence[Any]] = None,
) -> list[str]:
    """Resolve optional client hint scenes for index.

    PG v0.1: effective membership is recomputed from rules/image_scenes after insert.
    Client-supplied scene(s) remain validated hints (legacy multipart); empty → [general].
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
            single = canonicalize_scene_code(_as_short_str(scene, where="scene"))
            if single not in REGISTERED_SCENES:
                _raise_unknown(single, code_for_unknown="INDEX_UNKNOWN_SCENE")
            if set(resolved_from_array) != {single}:
                raise err.validation_error(
                    f"scene and scenes inconsistent: scene={single!r} "
                    f"scenes={resolved_from_array!r}"
                )
            return [single]
        return resolved_from_array

    if has_scene:
        single = canonicalize_scene_code(_as_short_str(scene, where="scene"))
        return _validate_registered([single], code_for_unknown="INDEX_UNKNOWN_SCENE")

    return list(DEFAULT_SCENES)


def normalize_search_scene(scene: Optional[str] = None) -> str:
    """Search filter scene; omit/blank → general. Unknown → SEARCH_UNKNOWN_SCENE."""
    if scene is None or (isinstance(scene, str) and scene.strip() == ""):
        return DEFAULT_SCENE
    s = canonicalize_scene_code(_as_short_str(scene, where="scene"))
    if s not in REGISTERED_SCENES:
        _raise_unknown(s, code_for_unknown="SEARCH_UNKNOWN_SCENE")
    return s


def normalize_list_scene(scene: Optional[str] = None) -> str:
    """List filter scene; omit/blank → general. Unknown → LIST_UNKNOWN_SCENE."""
    if scene is None or (isinstance(scene, str) and scene.strip() == ""):
        return DEFAULT_SCENE
    s = canonicalize_scene_code(_as_short_str(scene, where="scene"))
    if s not in REGISTERED_SCENES:
        _raise_unknown(s, code_for_unknown="LIST_UNKNOWN_SCENE")
    return s
