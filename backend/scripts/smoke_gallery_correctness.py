"""Small-gallery correctness smoke for the embedding model + Qdrant (算法逻辑).

Indexes N gallery images into a scratch collection, then checks:
  - same image          -> top-1 is itself
  - resized + JPEG q85  -> top-1 is the original
  - random noise        -> no hit at or above --score-threshold

Uses the service's own Embedder (app.clients.inference) and QdrantStore
(app.clients.qdrant). Does NOT touch PostgreSQL / HTTP; point ids are uuid5(path).

Usage (from backend/):
  python scripts/smoke_gallery_correctness.py --root "E:\\dataset\\红薯叶" --limit 30
  python scripts/smoke_gallery_correctness.py --root <dir> --qdrant-url :memory:   # no server
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.clients.inference import Embedder  # noqa: E402
from app.clients.qdrant import QdrantConnection, QdrantStore  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def imread_unicode(path: Path):
    """OpenCV imread fails on non-ASCII Windows paths; use fromfile+imdecode."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def list_images(root: Path, limit: int) -> list[Path]:
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    return files[:limit]


def embedding_config(model_path: str, *, use_gpu: bool) -> dict:
    return {
        "TOTAL_CONFIG": {"device_id": 0},
        "EMBEDDING_CONFIG": {
            "model_path": model_path,
            "output_name": "cls_emb",
            "scale": [224, 224],
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
            "img_interp": "cubic",
            "use_gpu": use_gpu,
            "use_iobinding": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="gallery correctness smoke (embedding + Qdrant)")
    parser.add_argument("--root", default=r"E:\dataset\红薯叶")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333", help='":memory:" = in-process')
    parser.add_argument("--collection", default="image_embeddings_smoke")
    parser.add_argument("--score-threshold", type=float, default=0.55)
    parser.add_argument("--model-path", default="config/model/dino/multitask_cls.onnx")
    parser.add_argument("--cpu", action="store_true", help="force CPU inference")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"FAIL dataset missing: {root}")
        return 2
    paths = list_images(root, max(1, args.limit))
    if len(paths) < 2:
        print(f"FAIL need >=2 images under {root}, found {len(paths)}")
        return 2

    embedder = Embedder(embedding_config(args.model_path, use_gpu=not args.cpu))
    dim = embedder.probe_dim()
    store = QdrantStore(
        QdrantConnection(url=args.qdrant_url),
        collection=args.collection,
        vector_dim=dim,
        distance="cosine",
    )
    print(f"embedding_dim={dim} providers={embedder.providers()} images={len(paths)} qdrant={args.qdrant_url}")
    try:
        store.ensure_collection(recreate=True)
    except Exception as exc:
        print(f"FAIL qdrant ensure_collection: {exc}")
        print('Hint: start Qdrant on :6333 (docker compose up -d qdrant) or use --qdrant-url :memory:')
        return 3

    try:
        t0 = time.perf_counter()
        point_ids: dict[Path, str] = {}
        for path in paths:
            img = imread_unicode(path)
            if img is None:
                print(f"SKIP decode fail: {path}")
                continue
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)))
            store.upsert(pid, embedder.detect(img), {"name": path.name})
            point_ids[path] = pid
        print(f"indexed {len(point_ids)} in {time.perf_counter() - t0:.1f}s; count={store.count()}")

        same_hits = 0
        for path, pid in point_ids.items():
            hits = store.search(embedder.detect(imread_unicode(path)), top_k=5)
            top = hits[0] if hits else None
            ok = top is not None and top["id"] == pid
            same_hits += int(ok)
            print(f"same {path.name}: score={None if top is None else round(top['score'], 4)} ok={ok}")

        probe, probe_id = next(iter(point_ids.items()))
        raw = imread_unicode(probe)
        h, w = raw.shape[:2]
        near = cv2.resize(raw, (max(8, w * 9 // 10), max(8, h * 9 // 10)))
        encoded, buf = cv2.imencode(".jpg", near, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        assert encoded
        near_hits = store.search(embedder.detect(cv2.imdecode(buf, cv2.IMREAD_COLOR)), top_k=3)
        near_ok = bool(near_hits) and near_hits[0]["id"] == probe_id
        print(f"near {probe.name}: score={near_hits[0]['score'] if near_hits else None} ok={near_ok}")

        noise = np.random.default_rng(0).integers(0, 255, size=(224, 224, 3), dtype=np.uint8)
        rej = store.search(embedder.detect(noise), top_k=5, score_threshold=args.score_threshold)
        reject_ok = not rej
        print(f"reject noise @thr={args.score_threshold}: hits={len(rej)} ok={reject_ok}")

        print(f"SUMMARY same_top1={same_hits}/{len(point_ids)} near_ok={near_ok} reject_ok={reject_ok}")
        return 0 if same_hits == len(point_ids) and near_ok else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
