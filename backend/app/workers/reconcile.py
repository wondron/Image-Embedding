"""Reconcile the vector store with PostgreSQL from the command line (cron, or a separate service).

One round pushes the payloads whose ledger row is behind and drains the deletion queue
(app.services.retrieval.reconcile). The API already runs rounds in the background while
RECONCILE_CONFIG.enabled is true; use this where it is off, or to catch up right away. A round
that finds another process mid-round skips (advisory lock).

Needs PG + Qdrant + object storage config, like the other workers; no ONNX model is loaded.

Invoke from backend/:

  python -m app.workers.reconcile                # rounds while they get something done (at most --rounds)
  python -m app.workers.reconcile --rounds 1     # exactly one round
  python -m app.workers.reconcile --loop         # every RECONCILE_CONFIG.interval_seconds until Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Push ledger-behind vector payloads and drain the deletion queue")
    parser.add_argument("--rounds", type=int, default=20, help="rounds at most while each still finds work (default 20)")
    parser.add_argument("--loop", action="store_true", help="keep going every RECONCILE_CONFIG.interval_seconds")
    args = parser.parse_args(argv)
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")

    from app.bootstrap.retrieval_stack import build_retrieval_infra
    from app.core.config.settings import ReconcileSettings
    from app.services.retrieval.delete_image import DeletionWorker
    from app.services.retrieval.reconcile import ReconcileLimits, ReconcileUseCase
    from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
    from app.workers._config import load_worker_config

    config = load_worker_config()
    settings = ReconcileSettings.from_config(config)
    infra = build_retrieval_infra(config)
    stop = threading.Event()
    rounds: list[dict] = []
    try:
        use_case = ReconcileUseCase(
            infra.metadata,
            sync=SyncSceneMembershipsUseCase(infra.metadata, infra.vectors, model=infra.model),
            deletions=DeletionWorker(infra.metadata, infra.vectors, infra.storage, others=infra.other_vectors),
            limits=ReconcileLimits(max_payloads=settings.max_payloads, max_deletions=settings.max_deletions),
        )
        cursor = None
        while True:
            result = use_case.run_once(cursor=cursor, should_stop=stop.is_set)
            cursor = result.cursor
            rounds.append(result.as_dict())
            # Another round only helps while one gets something done (failures would just repeat)
            progressed = result.payloads.pushed > 0 or result.deletions.processed > 0
            if args.loop:
                if stop.wait(settings.interval_seconds):
                    break
            elif result.skipped or not progressed or len(rounds) >= args.rounds:
                break
    except KeyboardInterrupt:
        stop.set()
    finally:
        infra.close()
    print(json.dumps(rounds[-1] if len(rounds) == 1 else {"rounds": len(rounds), "last": rounds[-1] if rounds else None},
                     ensure_ascii=False, indent=2, default=str))
    last = rounds[-1] if rounds else None
    return 0 if last is not None and not last["deletions"]["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
