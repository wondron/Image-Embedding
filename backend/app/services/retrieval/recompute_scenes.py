"""Recompute image_scenes membership (write-time materialize).

Thin application wrapper over MetadataStore.recompute_image_scenes so workers
and HTTP go through the composition root instead of constructing adapters.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.repositories.ports import MetadataStorePort

logger = logging.getLogger("retrieval.recompute_scenes")


class RecomputeScenesUseCase:
    """Materialize effective scene membership for one image."""

    def __init__(self, metadata: MetadataStorePort) -> None:
        self._metadata = metadata

    def execute(
        self,
        image_id: UUID | str,
        *,
        bump_version: bool = True,
    ) -> dict[str, Any]:
        out = self._metadata.recompute_image_scenes(
            image_id, bump_version=bump_version
        )
        logger.info(
            "recompute_image_scenes id=%s bump=%s",
            image_id,
            bump_version,
        )
        return out
