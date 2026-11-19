"""重算 image_scenes 归属（写入时物化）。

MetadataStore.recompute_image_scenes 之上的薄应用层包装，让后台任务和 HTTP 通过
组合根调用，而不是自己构造适配器。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.repositories.ports import MetadataStorePort

logger = logging.getLogger("retrieval.recompute_scenes")


class RecomputeScenesUseCase:
    """为一张图物化生效的场景归属。"""

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
