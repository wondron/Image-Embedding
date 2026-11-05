"""Image-search retrieval domain: pure rules and ports (SSOT-aligned).

Use cases live in app.application.retrieval (DDD D1).

DDD D6: retrieval must not depend on domain.food_service / domain.pipeline.
Predict island must not import application.retrieval or pg_metadata.
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
