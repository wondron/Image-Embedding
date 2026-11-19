"""清理一遍 image_deletions（契约 §2.5）。

只需要 PG、Qdrant 和对象存储，所以构建 RetrievalInfra（不加载 ONNX 模型、
不用 GPU），用完后关闭。

在 backend/ 下执行：

  python -m app.workers.process_deletions
  python -m app.workers.process_deletions --limit 20

配置：和 API 相同的生效配置（Nacos 覆盖本地 YAML）；PG / Qdrant / 对象存储的连接都取自它。
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
    parser = argparse.ArgumentParser(description="Process image_deletions queue")
    parser.add_argument("--limit", type=int, default=50, help="max rows this pass")
    args = parser.parse_args(argv)

    from app.bootstrap.retrieval_stack import build_retrieval_infra
    from app.services.retrieval.delete_image import DeletionWorker
    from app.workers._config import load_worker_config

    infra = build_retrieval_infra(load_worker_config())
    try:
        worker = DeletionWorker(infra.metadata, infra.vectors, infra.storage, others=infra.other_vectors)
        result = worker.run_once(limit=args.limit)
    finally:
        infra.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
