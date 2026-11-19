"""在命令行里让向量库与 PostgreSQL 对账（cron，或单独的服务）。

一轮会补推台账落后的 payload，并清理删除队列
（app.services.retrieval.reconcile）。RECONCILE_CONFIG.enabled 为 true 时 API 已经在后台
定期对账；关掉时用这个，或者需要立刻补齐时用。发现另一个进程正在对账的一轮
会跳过（advisory lock）。

和其他后台任务一样，需要 PG、Qdrant 和对象存储的配置；不加载 ONNX 模型。

在 backend/ 下执行：

  python -m app.workers.reconcile                # 每轮有进展就继续（最多 --rounds 轮）
  python -m app.workers.reconcile --rounds 1     # 只跑一轮
  python -m app.workers.reconcile --loop         # 每隔 RECONCILE_CONFIG.interval_seconds 跑一轮，直到 Ctrl+C
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
            limits=ReconcileLimits(
                max_payloads=settings.max_payloads, max_deletions=settings.max_deletions,
                max_verifications=settings.max_verifications,
            ),
            # 这个后台任务里没有编码器：只检查台账，缺失的向量等 API 来补
            ledger=(infra.model.id, infra.vectors, infra.metadata),
        )
        cursor = None
        while True:
            result = use_case.run_once(cursor=cursor, should_stop=stop.is_set)
            cursor = result.cursor
            rounds.append(result.as_dict())
            # 只有一轮有进展时再跑一轮才有用（失败的只会重复失败）
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
