"""Small-gallery correctness smoke for image-search MVP (算法逻辑).

SSOT: docs/image-search-mvp-data-contract.md

Usage (from backend/, sam env):
  python scripts/smoke_gallery_correctness.py
  python scripts/smoke_gallery_correctness.py --root "E:\\dataset\\红薯叶" --limit 30 --qdrant-url http://127.0.0.1:6333

Does NOT touch MySQL / HTTP. Uses temporary integer point ids 1..N.
Does NOT commit. Not a formal 验收 run (that is 测试 bot).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.domain.common.qdrant_store import VECTOR_DIM, QdrantStore
from app.domain.pipeline.d_vector_index import VectorIndexPipeline

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def imread_unicode(path: Path):
    """OpenCV imread fails on non-ASCII Windows paths; use fromfile+imdecode."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def list_images(root: Path, limit: int) -> list[Path]:
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    return files[:limit]


def build_config(qdrant_url: str, collection: str, score_threshold: float | None) -> dict:
    return {
        "TOTAL_CONFIG": {"device_id": 0},
        "EMBEDDING_CONFIG": {
            "model_path": "config/model/dino/multitask_cls.onnx",
            "output_name": "cls_emb",
            "scale": [224, 224],
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
            "img_interp": "cubic",
            "use_gpu": False,
            "use_iobinding": False,
        },
        "QDRANT_CONFIG": {
            "url": qdrant_url,
            "collection": collection,
            "vector_dim": VECTOR_DIM,
            "vector_size": VECTOR_DIM,
            "distance": "Cosine",
            "score_threshold": score_threshold,
            "top_k": 5,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="红薯叶 gallery correctness smoke")
    parser.add_argument("--root", default=r"E:\dataset\红薯叶")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", default="image_embeddings_smoke")
    parser.add_argument("--score-threshold", type=float, default=None)
    parser.add_argument("--recreate", action="store_true", default=True)
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"FAIL dataset missing: {root}")
        return 2

    paths = list_images(root, max(1, args.limit))
    if len(paths) < 2:
        print(f"FAIL need >=2 images under {root}, found {len(paths)}")
        return 2

    cfg = build_config(args.qdrant_url, args.collection, args.score_threshold)
    pipe = VectorIndexPipeline(cfg)
    print(f"embedding_dim={pipe.embedding_dim} images={len(paths)} qdrant={args.qdrant_url}")

    try:
        pipe.ensure_ready(recreate=bool(args.recreate))
    except Exception as exc:
        print(f"FAIL qdrant ensure_collection: {exc}")
        print("Hint: start local Qdrant on :6333 (后端 compose) then retry.")
        return 3

    # Index with temporary ids 1..N (MySQL not involved in this smoke).
    t0 = time.perf_counter()
    for i, path in enumerate(paths, start=1):
        img = imread_unicode(path)
        if img is None:
            print(f"SKIP decode fail: {path}")
            continue
        pipe.index_image(img, point_id=i, payload={"image_id": i})
    print(f"indexed ok in {time.perf_counter() - t0:.1f}s; count={pipe.store.count()}")

    # Same-image top-1
    same_hits = 0
    near_checked = 0
    for i, path in enumerate(paths, start=1):
        img = imread_unicode(path)
        if img is None:
            continue
        out = pipe.search_image(img, top_k=5, score_threshold=None)
        hits = out["results"]
        top = hits[0] if hits else None
        ok = top is not None and int(top["id"]) == i
        same_hits += int(ok)
        print(
            f"same[{i}] {path.name}: top_id={None if top is None else top['id']} "
            f"score={None if top is None else round(top['score'], 4)} ok={ok}"
        )

    # Near-image: slight JPEG recompress / resize should still top-1 same id
    probe = paths[0]
    raw = imread_unicode(probe)
    h, w = raw.shape[:2]
    near = cv2.resize(raw, (max(8, w * 9 // 10), max(8, h * 9 // 10)))
    ok, buf = cv2.imencode(".jpg", near, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    assert ok
    near_bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    near_out = pipe.search_image(near_bgr, top_k=3)
    near_top = near_out["results"][0] if near_out["results"] else None
    near_ok = near_top is not None and int(near_top["id"]) == 1
    near_checked = 1
    print(
        f"near[1] recompress: top_id={None if near_top is None else near_top['id']} "
        f"score={None if near_top is None else round(near_top['score'], 4)} ok={near_ok}"
    )

    # Out-of-gallery reject: random noise should be low score or empty under threshold
    noise = np.random.randint(0, 255, size=(224, 224, 3), dtype=np.uint8)
    thr = args.score_threshold if args.score_threshold is not None else 0.55
    rej = pipe.search_image(noise, top_k=5, score_threshold=thr)
    rejected = len(rej["results"]) == 0
    print(
        f"reject noise @thr={thr}: hits={len(rej['results'])} "
        f"top_score={rej['results'][0]['score'] if rej['results'] else None} ok={rejected}"
    )

    total = same_hits
    print(
        f"SUMMARY same_top1={same_hits}/{len(paths)} near_ok={near_ok} "
        f"reject_ok={rejected} VECTOR_DIM={VECTOR_DIM}"
    )
    if same_hits < len(paths) or not near_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())