"""Drain image_deletions once (contract §2.5).

Needs only PG + Qdrant + object storage, so it builds RetrievalInfra (no ONNX
model, no GPU) and closes it when done.

Invoke from backend/:

  python -m app.workers.process_deletions
  python -m app.workers.process_deletions --limit 20

Config: the same effective config as the API (Nacos over the local YAML); the
PG / Qdrant / storage connections come from it.
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
        worker = DeletionWorker(infra.metadata, infra.vectors, infra.storage)
        result = worker.run_once(limit=args.limit)
    finally:
        infra.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
