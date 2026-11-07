"""Recompute image_scenes for one image (write-time materialize).

Needs only PG, but builds RetrievalInfra like the other workers so the schema
and current model are checked the same way; no ONNX model is loaded.

Invoke from backend/:

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
