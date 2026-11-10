"""Recompute the scene memberships of every stored image and rewrite their vector payloads.

Run after changing scene rules, enabling / disabling a scene, or moving categories, when the
admin page's 「重算场景归属」 is not convenient (e.g. a large library, from cron). Needs PG +
Qdrant + object storage config, like the other workers; no ONNX model is loaded.

Invoke from backend/:

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
