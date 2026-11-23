"""测 Qdrant 检索耗时：每个新的查询向量连着查两次，看「第一次慢、第二次快」慢在 Qdrant 服务端还是网络上。

和 check_qdrant_connectivity.py 一样读取后端生效的 QDRANT_CONFIG（本地 YAML，再由 Nacos 逐键覆盖），
用后端同一个客户端（QdrantConnection.create_client：同样的地址、API Key、超时、prefer_grpc）。
只读：读 collection 信息、随机取几个已有的向量、发检索请求；不写入、不删除。

Qdrant 的响应里（gRPC 和 REST 都有）带它自己算的处理时间 time，和客户端量到的耗时对照着看：
  服务端耗时就是首次慢、重复快     -> 慢在 Qdrant 那台机器上：新查询要读的数据还不在内存里
  服务端耗时一直很小，只有客户端慢 -> 慢在网络、代理或连接上
查询和业务搜图的一样：命名向量 global、按 scenes 过滤、score_threshold、limit=top_k、带 payload。
查询向量是库里随机两个点的向量按随机比例相加再归一化：落在真实数据的分布里，又不正好是哪个已有的点，
每个查询也都不一样。

在 backend 目录、用后端同一 Python 环境运行（collection 名见 GET /api/health 里 qdrant 的 collection）：
  python scripts/check_qdrant_latency.py --collection <名字>
  python scripts/check_qdrant_latency.py --collection <名字> --queries 10 --pause 60
服务器上：dc run --rm api python scripts/check_qdrant_latency.py --collection <名字>

--pause：两个新查询之间等几秒（重复查询总是紧跟着首次）。连着查时只有第一个新查询慢、加了 --pause
每个都慢，说明数据是隔一会儿没用就被挤出内存，而不是每个查询要读的数据不一样。
退出码：0 完成；2 配置或参数错误；3 连接失败；4 collection 读取或检索失败。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPTS_DIR.parent
for _path in (BACKEND_ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import check_qdrant_connectivity as base  # noqa: E402  配置加载、脱敏、连接参数检查和它共用
from app.clients.qdrant import NAMED_VECTOR  # noqa: E402

SLOW = 0.05  # 秒：一次查询超过它算慢
FIRST, REPEAT = "首次", "重复"
# 客户端按这几个环境变量走代理（no_grpc_proxy / no_proxy 里的地址除外）
PROXY_VARIABLES = ("grpc_proxy", "https_proxy", "http_proxy", "all_proxy")


class UsageError(Exception):
    """参数不够确定要测什么（比如有多个 collection 又没指定）。"""


@dataclass(frozen=True)
class Timing:
    query: int
    kind: str
    client: float  # 秒：客户端量到的，含网络
    server: float  # 秒：Qdrant 响应里的 time
    hits: int


def proxy_note() -> str | None:
    """环境里设了代理变量时的提示（只列变量名：值里可能有账号密码）。"""
    found = sorted({name for name in os.environ if name.lower() in PROXY_VARIABLES})
    if not found:
        return None
    return (
        f"注意：环境里设了 {', '.join(found)}。后端连 Qdrant 时（本脚本也一样）会按它们走代理"
        "（no_grpc_proxy / no_proxy 里的地址除外），量到的延迟包括代理这一段。"
    )


def shown(value: Any) -> str:
    """配置值；没有这一项时写「未设」，即用 Qdrant 的默认值。"""
    if value is None:
        return "未设"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def describe(info: dict[str, Any]) -> list[str]:
    """collection 信息（CollectionInfo 的 JSON 形式）里和检索耗时有关的几项，以及由它们看出来的提示。"""
    config = info.get("config") or {}
    params = config.get("params") or {}
    vectors = params.get("vectors") or {}
    vector = (vectors.get(NAMED_VECTOR) if isinstance(vectors, dict) else None) or {}
    hnsw = config.get("hnsw_config") or {}
    optimizer = config.get("optimizer_config") or {}
    indexes = {name: (item or {}).get("data_type") for name, item in (info.get("payload_schema") or {}).items()}
    lines = [
        f"状态：status={shown(info.get('status'))} optimizer_status={shown(info.get('optimizer_status'))} "
        f"points={info.get('points_count')} indexed_vectors={info.get('indexed_vectors_count')} "
        f"segments={info.get('segments_count')}",
        f"向量 {NAMED_VECTOR}：size={vector.get('size')} distance={shown(vector.get('distance'))} "
        f"on_disk={shown(vector.get('on_disk'))} hnsw_config={shown(vector.get('hnsw_config'))} "
        f"quantization_config={shown(vector.get('quantization_config'))}",
        f"HNSW：m={hnsw.get('m')} ef_construct={hnsw.get('ef_construct')} "
        f"full_scan_threshold={hnsw.get('full_scan_threshold')} on_disk={shown(hnsw.get('on_disk'))}",
        f"存储：on_disk_payload={shown(params.get('on_disk_payload'))} "
        f"memmap_threshold={shown(optimizer.get('memmap_threshold'))} "
        f"indexing_threshold={shown(optimizer.get('indexing_threshold'))} "
        f"quantization_config={shown(config.get('quantization_config'))}",
        "payload 索引：" + (json.dumps(indexes, ensure_ascii=False) if indexes else "无"),
    ]
    vector_hnsw = vector.get("hnsw_config") or {}
    if vector.get("on_disk") or hnsw.get("on_disk") or vector_hnsw.get("on_disk") or optimizer.get("memmap_threshold"):
        lines.append("提示：向量或 HNSW 图放在磁盘上（mmap），没在内存里的部分第一次要从磁盘读")
    if "scenes" not in indexes:
        lines.append("提示：没有 scenes 的 payload 索引，按场景过滤要逐个读 payload")
    if info.get("points_count") and not info.get("indexed_vectors_count"):
        lines.append("提示：还没有建 HNSW（各段点数没到 indexing_threshold），每次查询都扫全部向量")
    return lines


def only_collection(client: Any) -> str:
    """没传 --collection 时：库里只有一个 collection 就用它。"""
    names = [item.name for item in client.get_collections().collections]
    if len(names) != 1:
        raise UsageError(
            f"Qdrant 里有 {len(names)} 个 collection，用 --collection 指定一个"
            f"（当前模型的见 GET /api/health 里 qdrant 的 collection）：{', '.join(names) or '无'}"
        )
    return names[0]


def sample_vectors(client: Any, collection: str, count: int) -> list[np.ndarray]:
    """库里随机 count 个点的向量（Qdrant ≥ 1.11 能随机取样；更早的版本从头 scroll）。"""
    from qdrant_client import models

    try:
        points = client.query_points(
            collection_name=collection,
            query=models.SampleQuery(sample=models.Sample.RANDOM),
            limit=count,
            with_vectors=[NAMED_VECTOR],
            with_payload=False,
        ).points
    except Exception:
        points, _ = client.scroll(
            collection_name=collection, limit=count, with_vectors=[NAMED_VECTOR], with_payload=False
        )
    found = []
    for point in points:
        vector = point.vector.get(NAMED_VECTOR) if isinstance(point.vector, dict) else point.vector
        if vector:
            found.append(np.asarray(vector, dtype=np.float32))
    return found


def make_queries(samples: list[np.ndarray], count: int, dim: int, rng: np.random.Generator) -> list[np.ndarray]:
    """count 个各不相同的单位向量：随机两个已有点按随机比例相加；库里不到 2 个点时用随机向量。"""
    queries = []
    while len(queries) < count:
        if len(samples) >= 2:
            i, j = rng.choice(len(samples), size=2, replace=False)
            weight = rng.uniform(0.3, 0.7)
            vector = weight * samples[i] + (1.0 - weight) * samples[j]
        else:
            vector = rng.standard_normal(dim).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 1e-6:
            queries.append(vector / norm)
    return queries


class Searcher:
    """和业务搜图（QdrantStore.search）一样的查询，走客户端配置的那条路（gRPC 或 REST）。
    直接调底层接口，因为高层的 query_points 把响应里的 time 丢掉了。
    """

    def __init__(
        self, client: Any, collection: str, *, grpc: bool, scene: str, top_k: int, threshold: float, timeout: float
    ) -> None:
        from qdrant_client import models

        self._client = client
        self._collection = collection
        self._grpc = grpc
        self._top_k = top_k
        self._threshold = threshold if threshold > 0 else None
        self._timeout = timeout
        self._filter = (
            models.Filter(must=[models.FieldCondition(key="scenes", match=models.MatchValue(value=scene))])
            if scene
            else None
        )

    def __call__(self, vector: np.ndarray) -> tuple[int, float]:
        """（结果数，Qdrant 响应里的处理时间秒）。"""
        from qdrant_client import models

        if self._grpc:
            from qdrant_client import grpc
            from qdrant_client.conversions.conversion import RestToGrpc

            response = self._client.grpc_points.Query(
                grpc.QueryPoints(
                    collection_name=self._collection,
                    query=RestToGrpc.convert_query(models.NearestQuery(nearest=vector.tolist())),
                    using=NAMED_VECTOR,
                    filter=RestToGrpc.convert_filter(self._filter) if self._filter else None,
                    limit=self._top_k,
                    score_threshold=self._threshold,
                    with_payload=grpc.WithPayloadSelector(enable=True),
                ),
                timeout=self._timeout,
            )
            return len(response.result), float(response.time)
        response = self._client.http.search_api.query_points(
            collection_name=self._collection,
            query_request=models.QueryRequest(
                query=vector.tolist(),
                using=NAMED_VECTOR,
                filter=self._filter,
                limit=self._top_k,
                score_threshold=self._threshold,
                with_payload=True,
            ),
        )
        return len(response.result.points), float(response.time or 0.0)


def verdict(timings: list[Timing]) -> list[str]:
    firsts = [t for t in timings if t.kind == FIRST]
    repeats = [t for t in timings if t.kind == REPEAT]
    cf = statistics.median(t.client for t in firsts)
    sf = statistics.median(t.server for t in firsts)
    sr = statistics.median(t.server for t in repeats)
    nf = statistics.median(t.client - t.server for t in firsts)  # 网络和客户端自己的开销
    nr = statistics.median(t.client - t.server for t in repeats)
    lines = [
        f"中位数：首次 客户端 {cf * 1000:.1f} ms / 服务端 {sf * 1000:.1f} ms；"
        f"重复 客户端 {statistics.median(t.client for t in repeats) * 1000:.1f} ms / 服务端 {sr * 1000:.1f} ms"
    ]
    if cf < SLOW:
        if firsts[0].client >= SLOW:
            lines.append(
                "结论：只有第一个新查询慢，后面连着查的新查询都快：第一次查询把要用的数据读进了内存，之后都在内存里。"
                "是隔一段时间没用才变冷，不是每个查询要读的数据不一样；加 --pause 60 再跑一次确认。"
            )
        else:
            lines.append(
                "结论：首次和重复都快，这次没有复现，数据可能都已经在内存里了。隔一段时间、或者业务搜图又慢的时候"
                "再跑一次，也可以加 --pause 60 看隔一会儿会不会变慢。"
            )
    elif sf >= SLOW and sf >= 3 * max(sr, 0.001):
        lines.append(
            "结论：服务端首次慢、重复快，慢在 Qdrant 那台机器上。新查询要用到的向量 / 索引不在内存里，要从磁盘读"
            "（或者从 swap 换回来）；重复查询读的是同一批数据，已经在内存里了。看上面的存储配置，以及 Qdrant 所在"
            "机器的可用内存、swap 和容器内存限制。"
        )
    elif nf >= SLOW and nf >= 3 * max(nr, 0.001):
        lines.append("结论：服务端一直快，客户端首次慢，慢在网络或连接上（DNS、建连、代理、丢包重传），不在 Qdrant。")
    elif sf >= SLOW:
        lines.append("结论：服务端首次和重复都慢，不是数据冷热的问题：看 Qdrant 的 CPU、负载和 collection 大小。")
    else:
        lines.append("结论：服务端一直快，客户端首次和重复都慢：每次都慢在网络上（距离、代理、带宽）。")
    return lines


def measure(client: Any, connection: Any, config: dict[str, Any], args: argparse.Namespace) -> int:
    transport = "gRPC" if connection.prefer_grpc else "REST"
    try:
        version = client.info().version
        collection = args.collection or only_collection(client)
    except UsageError as exc:
        from app.bootstrap.dependency_logging import redact_text

        print(f"FAIL {redact_text(str(exc), config)}")
        return base.EXIT_CONFIG
    except Exception as exc:
        print(f"FAIL 连不上 Qdrant: {base.format_exception(exc, config)}")
        print(base.failure_hint(exc))
        return base.EXIT_CONNECT
    print(f"Qdrant version={version} collection={collection} 走 {transport}（和后端一样）")

    scene = args.scene.strip()
    try:
        info = client.get_collection(collection_name=collection).model_dump(mode="json")
        for line in describe(info):
            print(line)
        vectors = ((info.get("config") or {}).get("params") or {}).get("vectors") or {}
        dim = int(vectors[NAMED_VECTOR]["size"])
        samples = sample_vectors(client, collection, 2 * args.queries)
    except Exception as exc:
        print(f"FAIL 读取 collection 失败: {base.format_exception(exc, config)}")
        return base.EXIT_COLLECTION
    search = Searcher(
        client, collection, grpc=connection.prefer_grpc, scene=scene,
        top_k=args.top_k, threshold=args.score_threshold, timeout=connection.timeout,
    )

    queries = make_queries(samples, args.queries, dim, np.random.default_rng(args.seed))
    source = f"{len(samples)} 个随机点的向量两两混合" if len(samples) >= 2 else "随机向量（库里不到 2 个点）"
    print(
        f"\n查询：{args.queries} 个新向量（{source}），各连查两次 | scene={scene or '不过滤'} "
        f"top_k={args.top_k} score_threshold={args.score_threshold:g} pause={args.pause:g}s"
    )
    print(f"{'#':>3}  查询  {'客户端ms':>9}  {'服务端ms':>9}  结果数")
    timings: list[Timing] = []
    for number, vector in enumerate(queries, 1):
        if number > 1 and args.pause > 0:
            time.sleep(args.pause)
        for kind in (FIRST, REPEAT):
            started = time.perf_counter()
            try:
                hits, server = search(vector)
            except Exception as exc:
                print(f"FAIL 检索失败: {base.format_exception(exc, config)}")
                return base.EXIT_COLLECTION
            timing = Timing(number, kind, time.perf_counter() - started, server, hits)
            timings.append(timing)
            print(f"{number:>3}  {kind}  {timing.client * 1000:>9.1f}  {server * 1000:>9.1f}  {hits:>6}")

    print()
    for line in verdict(timings):
        print(line)
    return base.EXIT_OK


def run(connection: Any, config: dict[str, Any], args: argparse.Namespace) -> int:
    note = proxy_note()
    if note:
        print(note)
    try:
        client = connection.create_client()
    except Exception as exc:
        print(f"FAIL 无法按生产 QdrantConnection 创建客户端: {base.format_exception(exc, config)}")
        return base.EXIT_CONFIG
    try:
        return measure(client, connection, config, args)
    finally:
        try:
            client.close()
        except Exception as exc:
            print(f"WARN Qdrant 客户端关闭失败: {base.format_exception(exc, config)}")


def main(argv: list[str] | None = None) -> int:
    base.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--collection", help="要测的 collection；不传时库里只有一个就用它")
    parser.add_argument("--scene", default="general", help="按哪个场景过滤（payload scenes）；空字符串表示不过滤；默认 general")
    parser.add_argument("--top-k", type=int, default=10, help="每次取几条，1～50；默认 10")
    parser.add_argument("--score-threshold", type=float, default=0.55, help="分数阈值，0 表示不设；默认 0.55")
    parser.add_argument("--queries", type=int, default=6, help="测几个新的查询向量，1～50；默认 6")
    parser.add_argument("--pause", type=float, default=0.0, help="两个新查询之间等几秒；默认 0")
    parser.add_argument("--seed", type=int, help="随机种子（取哪些点、怎么混合）")
    args = parser.parse_args(argv)
    if args.collection is not None:
        args.collection = args.collection.strip()
        if not args.collection:
            parser.error("--collection 不能为空")
    if not 1 <= args.top_k <= 50:
        parser.error("--top-k 要在 1～50")
    if not 1 <= args.queries <= 50:
        parser.error("--queries 要在 1～50")
    if not 0 <= args.score_threshold <= 1:
        parser.error("--score-threshold 要在 0～1")
    if args.pause < 0:
        parser.error("--pause 不能小于 0")
    os.chdir(BACKEND_ROOT)
    try:
        config, source = base.load_effective_qdrant_config()
        for line in base.summarize_effective(config, source):
            print(line)
    except Exception as exc:
        # YAML 错误可能回显包含凭证的完整源文件行。
        print(f"FAIL 无法加载生效配置或依赖（{type(exc).__name__}），请检查配置文件、Nacos 和后端 Python 环境。")
        return base.EXIT_CONFIG
    try:
        connection = base.parse_connection(config)
    except Exception as exc:
        print(f"FAIL Qdrant 配置前置检查失败: {base.format_exception(exc, config)}")
        return base.EXIT_CONFIG
    return run(connection, config, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消 Qdrant 耗时检查。")
        raise SystemExit(130)
