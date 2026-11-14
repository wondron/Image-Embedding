"""Offline reproducer: a finished deletion is undone by an in-flight reembed."""
from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import errors as err
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.delete_image import DeleteImageUseCase, DeletionWorker
from app.services.retrieval.reembed_image import ReembedImageUseCase
from app.services.retrieval.build_embeddings import BuildEmbeddingsUseCase
from app.services.retrieval.reconcile import ReconcileUseCase
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
MODEL = ActiveModel(id=3, name="test", version="v1", collection="offline", dim=4, distance="cosine")
events = []


class Store:
    def __init__(self):
        self.row = {
            "id": IMAGE, "original_url": "https://offline/original.png", "thumb_url": "https://offline/thumb.webp",
            "category_id": 12, "scenes": ["general", "oven"], "food_state": "cooked", "label_status": "auto",
            "payload_version": 7,
        }
        self.queue = {}
        self.ledger = {}

    def get_by_id(self, image_id):
        return dict(self.row) if self.row else None

    def enqueue_image_deletion(self, image_id):
        self.queue = {"image_id": IMAGE, "original_url": self.row["original_url"], "thumb_url": self.row["thumb_url"]}
        self.row = None
        self.ledger.clear()
        events.append("DELETE committed: image and embedding ledger removed, deletion queued")
        return dict(self.queue)

    def complete_deletion(self, image_id):
        self.queue.clear()
        events.append("DELETE cleanup completed: queue row removed")

    def upsert_image_embedding(self, *, model_id, image_id, synced_version=None):
        if self.row is None:
            events.append("reembed ledger write rejected: images FK target is absent")
            raise err.index_postgres_failed("image_embeddings FK: image no longer exists")
        self.ledger[str(image_id)] = synced_version

    def try_maintenance_lock(self, key):
        return nullcontext(True)

    def list_unsynced_payload_ids(self, **kwargs):
        return []

    def list_embedded_ids(self, model_id, **kwargs):
        return []

    def list_embedding_backlog(self, model_id, **kwargs):
        return []

    def list_pending_deletions(self, **kwargs):
        return [self.queue] if self.queue else []

    def count_pending_deletions(self):
        return int(bool(self.queue))


class Vectors:
    def __init__(self):
        self.points = {IMAGE: {"payload": "old"}}

    def upsert(self, image_id, vector, payload):
        self.points[str(image_id)] = {"vector": vector, "payload": payload}
        events.append("reembed upsert: deleted point recreated")

    def delete(self, image_id):
        self.points.pop(str(image_id), None)
        events.append("DELETE cleanup: vector point removed")


class Storage:
    def read_by_url_or_key(self, url):
        events.append("reembed read original bytes into memory")
        return b"offline-original"

    def delete_by_url_or_key(self, url):
        events.append("DELETE cleanup: object removed")


store, vectors, storage = Store(), Vectors(), Storage()
deletion = DeleteImageUseCase(store)
worker = DeletionWorker(store, vectors, storage)


class Encoder:
    def embed_bytes(self, data):
        # Deterministic interleaving: the other request finishes while inference runs.
        queued = deletion.execute(IMAGE)
        assert worker.clean_up(queued)
        assert IMAGE not in vectors.points and not store.queue
        return [0.5, 0.5, 0.5, 0.5]


try:
    ReembedImageUseCase(Encoder(), vectors, store, model=MODEL, storage=storage).execute(IMAGE)
except err.AppError as exc:
    failure = exc.code
else:
    failure = None

# Use the real reconciliation flow. With no images, ledger rows or deletion queue rows,
# none of its queries can discover this vector-only orphan.
reconciler = ReconcileUseCase(
    store,
    sync=SyncSceneMembershipsUseCase(store, vectors, model=MODEL),
    deletions=worker,
    ledger=(MODEL.id, vectors, store),
    fill=BuildEmbeddingsUseCase(model=MODEL, encoder=Encoder(), vectors=vectors,
                               metadata=store, models=store, storage=storage),
)
round_result = reconciler.run_once().as_dict()

result = {
    "offline": True,
    "events": events,
    "reembed_error": failure,
    "image_exists": store.row is not None,
    "ledger": store.ledger,
    "deletion_queue": store.queue,
    "point_exists_after_completed_delete": IMAGE in vectors.points,
    "expected_point_exists": False,
    "reconcile_round": round_result,
    "point_exists_after_reconcile": IMAGE in vectors.points,
    "reproduced": store.row is None and not store.ledger and not store.queue and IMAGE in vectors.points,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
assert result["reproduced"], "the race was not reproduced"
