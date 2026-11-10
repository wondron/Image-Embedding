"""Which scene a request means (contract §2.6 / A5).

Scenes are rows of the scenes table, configured through /api/scenes, not a list in code. A
request names one by code: blank means DEFAULT_SCENE, and the MVP codes normal / device still
map to general / oven (app.core.scene_rules). The scene must exist and be active; otherwise the
request is refused with the code of its surface (SEARCH_ / LIST_ / INDEX_UNKNOWN_SCENE).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

from app.core import errors as err
from app.core.scene_rules import DEFAULT_SCENE, SCENE_ALIASES
from app.repositories.ports import MetadataStorePort

ErrorFactory = Callable[[str], err.AppError]


def canonicalize_scene_code(raw: str) -> str:
    """Strip and map an MVP code; does not check that the scene exists."""
    code = raw.strip()
    return SCENE_ALIASES.get(code, code)


def requested_scene_code(value: Any, *, field: str = "scene") -> str:
    """The code a request asks for: omitted / blank -> DEFAULT_SCENE."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_SCENE
    if not isinstance(value, str):
        raise err.validation_error(f"{field} must be a string, got {type(value).__name__}")
    return canonicalize_scene_code(value)


def _active_codes_text(metadata: MetadataStorePort) -> str:
    """For error messages only: never turns a refusal into a database failure."""
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
    """The active scenes row with this code: {id, code, name, include_all, outputs_heating, ...}.

    unknown: the surface's *_UNKNOWN_SCENE constructor (missing or inactive scene);
    failed: its *_POSTGRES_FAILED constructor (the lookup itself failed).
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
    """Check the scene hints an index request may carry; returns them canonical and deduplicated.

    The hints do not decide membership: that is recomputed from the rules and image_scenes after
    the insert. They are still checked, so a client learns about a code that does not exist.
    No hint -> [DEFAULT_SCENE] without touching the database.
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
