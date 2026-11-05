"""Recompute image_scenes for one image (write-time materialize).

  python -m app.worker.recompute_scenes --image-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--no-bump", action="store_true")
    args = parser.parse_args(argv)

    from app.core.config.settings import Settings
    from app.adapter.persistence.pg_metadata import PgMetadataStore

    pg = Settings().postgres
    meta = PgMetadataStore(
        dsn=pg.dsn(),
        host=pg.host,
        port=pg.port,
        user=pg.user,
        password=pg.password,
        database=pg.database,
    )
    out = meta.recompute_image_scenes(args.image_id, bump_version=not args.no_bump)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
