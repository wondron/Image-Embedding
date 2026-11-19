"""重新计算所有已存图片的场景归属，并重写它们的向量 payload。

在修改场景规则、启用 / 停用场景或移动类别之后执行，适用于管理页的「重算场景归属」
不方便用的时候（例如图库很大，或从 cron 调用）。和其他后台任务一样，需要 PG、
Qdrant 和对象存储的配置；不加载 ONNX 模型。

在 backend/ 下执行：

  python -m app.workers.sync_scenes
  python -m app.workers.sync_scenes --batch 500
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT, SCENE_SYNC_BATCH_MAX


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Recompute scene memberships and vector payloads of all images")
    parser.add_argument(
        "--batch", type=int, default=SCENE_SYNC_BATCH_DEFAULT, help=f"images per batch (1..{SCENE_SYNC_BATCH_MAX})"
    )
    args = parser.parse_args(argv)

    from app.bootstrap.retrieval_stack import build_retrieval_infra
    from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
    from app.workers._config import load_worker_config

    infra = build_retrieval_infra(load_worker_config())
    try:
        totals = SyncSceneMembershipsUseCase(infra.metadata, infra.vectors, model=infra.model).run_all(limit=args.batch)
    finally:
        infra.close()
    print(json.dumps(totals, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
