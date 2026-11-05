"""Drain image_deletions once (contract §2.5).

Goes through composition root (build_retrieval_stack → deletion_worker),
not adapter ctors.

Invoke from backend/:

  python -m app.worker.process_deletions
  python -m app.worker.process_deletions --limit 20

Uses same Settings + Nacos/config bootstrap path as the API when available;
falls back to Settings() defaults + empty config dict.
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
    result = stack.deletion_worker.run_once(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
