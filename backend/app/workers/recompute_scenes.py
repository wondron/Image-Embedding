"""重新计算一张图的 image_scenes（写入时物化）。

只需要 PG，但和其他后台任务一样构建 RetrievalInfra，以便用同样的方式检查表结构
和当前模型；不加载 ONNX 模型。
只有图片的场景归属变了才递增 payload_version；这里不写向量的 payload
（app.workers.sync_scenes 会重算所有图片并写入 payload）。

在 backend/ 下执行：

  python -m app.workers.recompute_scenes --image-id <uuid>
  python -m app.workers.recompute_scenes --image-id <uuid> --no-bump
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Recompute image_scenes for one image")
    parser.add_argument("--image-id", required=True)
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="do not bump images.payload_version",
    )
    args = parser.parse_args(argv)

    from app.bootstrap.retrieval_stack import build_retrieval_infra
    from app.services.retrieval.recompute_scenes import RecomputeScenesUseCase
    from app.workers._config import load_worker_config

    infra = build_retrieval_infra(load_worker_config())
    try:
        out = RecomputeScenesUseCase(infra.metadata).execute(
            args.image_id, bump_version=not args.no_bump
        )
    finally:
        infra.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
