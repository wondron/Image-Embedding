"""Recompute image_scenes for one image (write-time materialize).

Goes through composition root (build_retrieval_stack), not adapter ctors.

Invoke from backend/:

  python -m app.worker.recompute_scenes --image-id <uuid>
  python -m app.worker.recompute_scenes --image-id <uuid> --no-bump
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
    parser = argparse.ArgumentParser(
        description="Recompute image_scenes via RetrievalStack"
    )
    parser.add_argument("--image-id", required=True)
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="do not bump images.payload_version",
    )
    args = parser.parse_args(argv)

    from app.core.config.settings import Settings
    from app.bootstrap.retrieval_stack import build_retrieval_stack

    settings = Settings()
    config: dict = {}
    try:
        from app.core.config.loader import load_config  # type: ignore

        config = load_config() or {}
    except Exception:
        config = {}

    stack = build_retrieval_stack(config, settings)
    out = stack.recompute_scenes_uc.execute(
        args.image_id, bump_version=not args.no_bump
    )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
