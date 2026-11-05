"""Image-search retrieval domain: pure rules and ports (SSOT-aligned).

Use cases live in app.application.retrieval (DDD D1).
"""

from app.domain.retrieval.scenes import (
    DEFAULT_SCENE,
    REGISTERED_SCENES,
    canonicalize_scene_code,
    normalize_search_scene,
)

__all__ = [
    "DEFAULT_SCENE",
    "REGISTERED_SCENES",
    "canonicalize_scene_code",
    "normalize_search_scene",
]
