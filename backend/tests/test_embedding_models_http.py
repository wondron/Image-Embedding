"""Model versions end to end without a database: the real routes, adapter and registry (with its
build thread) over an in-memory store that keeps the SQL's rules (test_pg_live runs the SQL).

The sequence a model update goes through: the configured encoder is another model -> register
it (own empty collection) -> build (every stored image embedded into it, failures listed, the
version becomes ready) -> switch (a new stack swapped in, the previous version kept ready) ->
roll back the same way -> retire the old version (collection and ledger gone).
"""

import contextlib
import math
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import embedding_models as model_routes
from app.bootstrap import model_registry as registry_module
from app.bootstrap.model_registry import ModelRegistry
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.active_model import ActiveModel

SPEC = {"model_path": "config/model/dino/a.onnx", "output_name": "cls_emb", "scale": [224, 224],
        "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225], "img_interp": "cubic"}


def unit(values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


PROBE_OLD = unit([0.9, 0.1, 0.3, 0.2])
PROBE_NEW = unit([0.1, 0.9, 0.2, 0.4])
IMAGES = [f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 6)]


def fingerprint(probe, path):
    return {"spec": {**SPEC, "model_path": path}, "probe": list(probe)}


class FakeEncoder:
    def __init__(self, probe, path):
        self.model_path = path
        self._fp = fingerprint(probe, path)
        self.seen = []

    def fingerprint(self):
        return self._fp

    def embed_bytes(self, data):
        self.seen.append(data)
        return list(self._fp["probe"])


class FakeVectors:
    """One collection in a shared fake Qdrant."""

    def __init__(self, qdrant, row):
        self.qdrant, self.collection, self.dim = qdrant, str(row["collection"]), int(row["dim"])

    def collection_exists(self):
        return self.collection in self.qdrant

    def ensure_collection(self, *, recreate=False):
        self.qdrant.setdefault(self.collection, {})

    def drop_collection(self):
        return self.qdrant.pop(self.collection, None) is not None

    def upsert(self, point_id, vector, payload):
        if self.qdrant.get("__down__"):
            raise err.index_qdrant_failed("qdrant down")
        self.qdrant[self.collection][str(point_id)] = {"vector": vector, "payload": dict(payload)}

    def delete(self, point_id):
        self.qdrant.get(self.collection, {}).pop(str(point_id), None)

    def set_payloads(self, payloads):
        points = self.qdrant.get(self.collection, {})
        for pid, payload in payloads.items():
            if pid in points:
                points[pid]["payload"].update(payload)
        return sorted(pid for pid in payloads if pid not in points)

    def existing_ids(self, ids):
        return {str(i) for i in ids if str(i) in self.qdrant.get(self.collection, {})}

    def point_count(self):
        if self.collection not in self.qdrant:
            raise err.search_qdrant_failed("no such collection")
        return len(self.qdrant[self.collection])


class FakeModelStore:
    """embedding_models + image_embeddings + images, with the repository's rules."""

    def __init__(self):
        self.images = {i: {"payload_version": 1, "original_url": f"https://cdn/o/{i}.jpg"} for i in IMAGES}
        self.models = {}
        self.ledger = {}  # (model_id, image_id) -> synced_version
        self.locked = set()

    def add_model(self, **row):
        row = {"dim": 4, "distance": "cosine", "input_size": 224, "preprocess": {}, "weights_uri": "w",
               "fingerprint": None, "created_at": "2026-11-01T00:00:00+00:00", "activated_at": None, **row}
        self.models[row["id"]] = row
        return row

    # -- EmbeddingModelStorePort
    def list_embedding_models(self):
        out = []
        for row in sorted(self.models.values(), key=lambda r: r["id"]):
            mine = [i for (m, i) in self.ledger if m == row["id"]]
            behind = sum(1 for i in mine if self.ledger[(row["id"], i)] < self.images[i]["payload_version"])
            out.append({**row, "embedded": len(mine), "behind": behind})
        return {"images": len(self.images), "models": out}

    def list_embedding_model_rows(self, *, statuses=None):
        return [dict(r) for r in sorted(self.models.values(), key=lambda r: r["id"]) if statuses is None or r["status"] in statuses]

    def get_embedding_model(self, model_id):
        row = self.models.get(int(model_id))
        return dict(row) if row else None

    def register_embedding_model(self, fields):
        for row in self.models.values():
            if (row["name"], row["version"]) == (fields["name"], fields["version"]) or row["collection"] == fields["collection"]:
                raise err.embedding_model_exists("taken")
        return dict(self.add_model(id=max(self.models, default=0) + 1, status="building", **fields))

    def record_model_fingerprint(self, model_id, fp):
        row = self.models[int(model_id)]
        if row["fingerprint"] is not None:
            return False
        row["fingerprint"] = fp
        return True

    def set_embedding_model_status(self, model_id, status, *, expected):
        row = self.models[int(model_id)]
        if row["status"] not in expected:
            return False
        row["status"] = status
        return True

    def activate_embedding_model(self, model_id):
        row = self.models.get(int(model_id))
        if row is None:
            raise err.embedding_model_not_found("x")
        if row["status"] == "retired":
            raise err.embedding_model_state("retired")
        previous = None
        if row["status"] != "current":
            for other in self.models.values():
                if other["status"] == "current":
                    other["status"], previous = "ready", other["id"]
            row["status"], row["activated_at"] = "current", "2026-11-12T10:00:00+00:00"
        return {"model": dict(row), "previous_id": previous}

    def retire_embedding_model(self, model_id):
        row = self.models[int(model_id)]
        if row["status"] == "current":
            raise err.embedding_model_state("current")
        gone = [k for k in self.ledger if k[0] == row["id"]]
        for key in gone:
            del self.ledger[key]
        row["status"] = "retired"
        return {"model": dict(row), "ledger_rows": len(gone)}

    def count_embedding_backlog(self, model_id):
        return sum(1 for i in self.images if (int(model_id), i) not in self.ledger)

    def list_embedding_backlog(self, model_id, *, after=None, limit=16, exclude=None):
        ids = [i for i in sorted(self.images) if (int(model_id), i) not in self.ledger
               and (after is None or i > after) and i not in (exclude or [])]
        return [{"id": i, "original_url": self.images[i]["original_url"]} for i in ids[:limit]]

    def record_embeddings(self, *, model_id, versions):
        recorded = []
        for image_id, version in sorted(versions.items()):
            if image_id in self.images:
                self.ledger[(int(model_id), image_id)] = int(version)
                recorded.append(image_id)
        return recorded

    def sample_embedded_images(self, model_id, *, limit=3):
        return []

    def list_embedded_ids(self, model_id, *, after=None, limit=256):
        return sorted(i for (m, i) in self.ledger if m == int(model_id) and (after is None or i > after))[:limit]

    def forget_embeddings(self, model_id, image_ids):
        gone = [i for i in image_ids if self.ledger.pop((int(model_id), i), None) is not None]
        return len(gone)

    def clear_embeddings(self, model_id):
        return self.forget_embeddings(model_id, [i for (m, i) in list(self.ledger) if m == int(model_id)])

    def refresh_model_fingerprint(self, model_id, fingerprint):
        row = self.models[int(model_id)]
        row["fingerprint"], row["weights_uri"] = fingerprint, fingerprint["spec"]["model_path"]
        return True

    @contextlib.contextmanager
    def try_maintenance_lock(self, key):
        held = key not in self.locked
        self.locked.add(key)
        try:
            yield held
        finally:
            if held:
                self.locked.discard(key)

    # -- what the build and payload push read
    def get_payload_rows(self, ids):
        return [{"id": i, "payload_version": self.images[i]["payload_version"], "category_id": None,
                 "label_status": "unlabeled", "food_state": None, "scenes": ["general"]} for i in ids if i in self.images]

    def list_unsynced_payload_ids(self, *, after=None, until=None, limit=200, model_id=None):
        return sorted(i for (m, i), v in self.ledger.items() if m == model_id and v < self.images[i]["payload_version"]
                      and (after is None or i > after))[:limit]

    def mark_payloads_pushed(self, *, model_id, versions):
        recorded = []
        for image_id, version in versions.items():
            if self.images.get(image_id, {}).get("payload_version") == version:
                self.ledger[(model_id, image_id)] = version
                recorded.append(image_id)
        return sorted(recorded)


class Storage:
    def __init__(self, unreadable=()):
        self.unreadable = set(unreadable)

    def read_by_url_or_key(self, url):
        if any(i in url for i in self.unreadable):
            raise err.image_file_unavailable(f"NoSuchKey {url}")
        return url.encode()


def stack_for(model, encoder, storage, qdrant=None):
    vectors = FakeVectors(qdrant, {"collection": model.collection, "dim": model.dim}) if qdrant is not None else None
    if vectors is not None:
        vectors.ensure_collection()
    return SimpleNamespace(model=model, embedding=encoder, infra=SimpleNamespace(storage=storage, vectors=vectors), close=Mock())


class ModelVersionsTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeModelStore()
        self.store.add_model(id=1, name="dino", version="v1", collection="dino_v1", status="current",
                             fingerprint=fingerprint(PROBE_OLD, "config/model/dino/old.onnx"))
        for image_id in IMAGES:
            self.store.ledger[(1, image_id)] = 1
        self.qdrant = {"dino_v1": {i: {"vector": PROBE_OLD, "payload": {}} for i in IMAGES}}
        self.old_encoder = FakeEncoder(PROBE_OLD, "config/model/dino/old.onnx")
        self.new_encoder = FakeEncoder(PROBE_NEW, "config/model/dino/new.onnx")
        self.storage = Storage()
        current = ActiveModel.from_row(self.store.models[1])
        self.runtime = ApplicationState(
            config={"QDRANT_CONFIG": {"url": "http://q:6333"}}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(store=self.store, file_storage=lambda: self.storage),
            embedder=self.new_encoder,  # EMBEDDING_CONFIG now names the new model
            retrieval=stack_for(current, self.old_encoder, self.storage),  # search kept the old pair
            reconciler=Mock(),
        )
        # weights still on disk, as pair_encoder would load them from a fingerprint's spec
        self.loadable = {"config/model/dino/old.onnx": self.old_encoder}
        self.registry = ModelRegistry(self.runtime)
        self.runtime.models = self.registry
        p = patch.object(ModelRegistry, "_vectors", lambda _self, row: FakeVectors(self.qdrant, row))
        p.start()
        self.addCleanup(p.stop)
        self.built = []

        def build_stack(config, settings, *, embedder, metadata, model, candidates):
            encoder = next((e for e in (embedder, *candidates) if e.fingerprint()["probe"] == model.fingerprint["probe"]), None)
            if encoder is None:
                encoder = self.loadable.get(model.fingerprint["spec"]["model_path"])
            if encoder is None:
                raise err.embedding_model_mismatch(f"the weights of {model.space_id} could not be loaded")
            stack = stack_for(model, encoder, self.storage, self.qdrant)
            self.built.append(stack)
            return stack

        p = patch("app.bootstrap.retrieval_stack.build_retrieval_stack", side_effect=build_stack)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(registry_module, "RETIRED_STACK_GRACE_SECONDS", 0.01)
        p.start()
        self.addCleanup(p.stop)

        def load_from_spec(config):  # weights still in the image, as model_registry loads them
            def load(spec):
                encoder = self.loadable.get(spec["model_path"])
                if encoder is None:
                    raise FileNotFoundError(f"could not be loaded: {spec['model_path']}")
                return encoder
            return load

        p = patch("app.bootstrap.retrieval_stack.load_encoder_from_spec", side_effect=load_from_spec)
        p.start()
        self.addCleanup(p.stop)

        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(model_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(model_routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.registry.close)

    # -- helpers
    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        self.assertNotIn("trace_id", response.json())
        return response.json()

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"], code)
        return response.json()

    def wait_build(self):
        thread = self.registry._thread
        if thread is not None:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        return self.ok(self.client.get("/api/embedding-models"))

    def model(self, body, model_id):
        return next(m for m in body["models"] if m["id"] == model_id)

    # -- the sequence
    def test_the_overview_says_the_configured_encoder_is_a_new_unregistered_model(self):
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual(body["images"], 5)
        self.assertEqual(body["configured"], {"loaded": True, "model_path": "config/model/dino/new.onnx", "dim": 4,
                                              "artifact": None, "model_id": None, "detail": None})
        self.assertEqual(body["retrieval"]["model_id"], 1)
        self.assertEqual(body["retrieval"]["encoder"], "own")  # search keeps the encoder that wrote dino_v1
        current = self.model(body, 1)
        self.assertEqual((current["status"], current["embedded"], current["missing"], current["points"]), ("current", 5, 0, 5))
        self.assertEqual((current["weights"], current["artifact"]), (None, None))  # this fake records no weight files
        self.assertEqual((current["serving"], current["configured"], current["encoder"]), (True, False, "serving"))
        self.assertIsNone(body["build"])
        self.assertNotIn("probe", str(body))  # the fingerprint itself stays on the server

    def test_a_model_update_from_register_to_retire(self):
        # register: its own empty collection, search untouched
        registered = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]
        self.assertEqual((registered["name"], registered["status"], registered["collection"]), ("new", "building", "new_v2"))
        self.assertEqual((registered["embedded"], registered["missing"], registered["configured"]), (0, 5, True))
        self.assertEqual(self.qdrant["new_v2"], {})
        self.error(self.client.post("/api/embedding-models", json={}), 409, "EMBEDDING_MODEL_EXISTS")  # same encoder twice
        new_id = registered["id"]
        # switching to it now would hide more images than a switch tops up itself
        with patch.object(registry_module, "FINAL_CATCHUP_MAX", 2):
            body = self.error(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}), 409, "EMBEDDING_MODEL_INCOMPLETE")
        self.assertIn("5 stored images", body["detail"])

        # build: every image embedded with the new encoder, the version becomes ready
        build = self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))["build"]
        self.assertEqual((build["model_id"], build["state"]), (new_id, "running"))
        body = self.wait_build()
        self.assertEqual(body["build"]["state"], "done")
        self.assertEqual((body["build"]["embedded"], body["build"]["failed"], body["build"]["remaining"]), (5, 0, 0))
        self.assertEqual(self.model(body, new_id)["status"], "ready")
        self.assertEqual(self.model(body, new_id)["missing"], 0)
        self.assertEqual(sorted(self.qdrant["new_v2"]), IMAGES)
        self.assertEqual(self.qdrant["new_v2"][IMAGES[0]]["vector"], PROBE_NEW)
        self.assertEqual(len(self.new_encoder.seen), 5)
        self.assertEqual(self.old_encoder.seen, [])  # the old pair never wrote the new collection

        # switch: a stack on (new version, new encoder) replaces the old one; the old stays ready
        old_stack = self.runtime.retrieval
        switched = self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        self.assertEqual((switched["switched"], switched["previous_id"]), (True, 1))
        self.assertEqual(switched["model"]["status"], "current")
        self.assertIs(self.runtime.retrieval, self.built[-1])
        self.assertIs(self.runtime.retrieval.embedding, self.new_encoder)
        self.assertEqual(self.runtime.retrieval.model.collection, "new_v2")
        self.assertEqual(self.store.models[1]["status"], "ready")
        threading.Event().wait(0.1)
        old_stack.close.assert_called_once()  # after the grace period
        again = self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        self.assertFalse(again["switched"])

        # roll back: the same call on the previous version; its encoder is loaded again from its fingerprint
        back = self.ok(self.client.post("/api/embedding-models/1/activate", json={}))
        self.assertEqual((back["previous_id"], self.runtime.retrieval.model.id), (new_id, 1))
        self.assertIs(self.runtime.retrieval.embedding, self.old_encoder)
        self.assertEqual(self.ok(self.client.get("/api/embedding-models"))["retrieval"]["encoder"], "own")
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        self.assertIs(self.runtime.retrieval.embedding, self.new_encoder)

        # retire the old version: its collection and ledger go, it cannot come back
        self.error(self.client.post(f"/api/embedding-models/{new_id}/retire"), 409, "EMBEDDING_MODEL_STATE")  # current
        retired = self.ok(self.client.post("/api/embedding-models/1/retire"))
        self.assertEqual((retired["dropped"], retired["ledger_rows"], retired["model"]["status"]), (True, 5, "retired"))
        self.assertNotIn("dino_v1", self.qdrant)
        self.assertEqual(retired["model"]["missing"], 0)
        self.error(self.client.post("/api/embedding-models/1/activate", json={}), 409, "EMBEDDING_MODEL_STATE")
        self.error(self.client.post("/api/embedding-models/1/build"), 409, "EMBEDDING_MODEL_STATE")
        self.ok(self.client.post("/api/embedding-models/1/retire"))  # already retired: as it is

    def test_images_that_cannot_be_embedded_are_listed_and_need_an_explicit_switch(self):
        self.storage.unreadable = {IMAGES[1]}
        new_id = self.ok(self.client.post("/api/embedding-models", json={"name": "dino", "version": "v2"}), 201)["model"]["id"]
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        body = self.wait_build()
        build = body["build"]
        self.assertEqual((build["state"], build["embedded"], build["failed"], build["remaining"]), ("done", 4, 1, 1))
        self.assertEqual(build["failures"][0]["image_id"], IMAGES[1])
        self.assertEqual(build["failures"][0]["code"], "IMAGE_FILE_UNAVAILABLE")
        self.assertNotIn("NoSuchKey", build["failures"][0]["detail"])
        self.assertEqual(self.model(body, new_id)["status"], "building")  # not complete: not ready
        self.error(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}), 409, "EMBEDDING_MODEL_INCOMPLETE")
        switched = self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={"allow_missing": True}))
        self.assertTrue(switched["switched"])
        self.assertEqual(switched["model"]["missing"], 1)
        # the image readable again: a build of the current version fills the gap with the new encoder
        self.storage.unreadable = set()
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        body = self.wait_build()
        self.assertEqual((body["build"]["embedded"], self.model(body, new_id)["missing"]), (1, 0))

    def test_a_storage_outage_stops_the_build_instead_of_failing_every_image(self):
        self.store.images.update({f"{n:08d}-0000-4000-8000-000000000000": {"payload_version": 1, "original_url": "https://cdn/o/x"}
                                  for n in range(10, 40)})
        self.storage.unreadable = {"cdn"}
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        build = self.wait_build()["build"]
        self.assertEqual(build["state"], "failed")
        self.assertIn("IMAGE_FILE_UNAVAILABLE", build["error"])
        self.assertEqual(build["failed"], registry_module.FAILURE_STREAK_LIMIT + 7)  # stopped after the batch that crossed it
        self.assertLessEqual(len(build["failures"]), registry_module.MAX_FAILURES_SHOWN)

    def test_a_vector_store_failure_fails_the_build_with_a_reason(self):
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        self.qdrant["__down__"] = True
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        build = self.wait_build()["build"]
        self.assertEqual(build["state"], "failed")
        self.assertTrue(build["error"].startswith("INDEX_QDRANT_FAILED"))

    def test_one_build_at_a_time_and_it_can_be_stopped(self):
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        gate = threading.Event()
        original = self.store.list_embedding_backlog

        def slow(*args, **kwargs):
            gate.wait(5)
            return original(*args, **kwargs)

        self.store.list_embedding_backlog = slow
        p = patch.object(registry_module, "BUILD_BATCH", 2)
        p.start()
        self.addCleanup(p.stop)
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        self.error(self.client.post("/api/embedding-models/1/build"), 409, "EMBEDDING_BUILD_RUNNING")
        self.error(self.client.post(f"/api/embedding-models/{new_id}/activate", json={"allow_missing": True}), 409, "EMBEDDING_BUILD_RUNNING")
        self.error(self.client.post(f"/api/embedding-models/{new_id}/retire"), 409, "EMBEDDING_BUILD_RUNNING")
        stopping = self.ok(self.client.post(f"/api/embedding-models/{new_id}/build/stop"))["build"]
        self.assertEqual(stopping["state"], "stopping")
        gate.set()
        body = self.wait_build()
        self.assertEqual(body["build"]["state"], "stopped")
        self.assertEqual(body["build"]["embedded"], 2)  # ended after the batch in flight
        self.assertEqual(self.model(body, new_id)["missing"], 3)
        # starting again goes on where it stopped
        self.store.list_embedding_backlog = original
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        body = self.wait_build()
        self.assertEqual(self.model(body, new_id)["missing"], 0)

    def test_another_process_building_the_version_is_reported(self):
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        self.store.locked.add(registry_module.BUILD_LOCK_KEY_BASE + new_id)
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        build = self.wait_build()["build"]
        self.assertEqual(build["state"], "failed")
        self.assertIn("another process", build["error"])

    def test_a_version_whose_encoder_is_not_loaded_cannot_be_built_or_switched_to(self):
        self.store.add_model(id=7, name="other", version="v7", collection="other_v7", status="ready",
                             fingerprint=fingerprint(unit([0.3, 0.3, 0.9, 0.1]), "config/model/gone.onnx"))
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual(self.model(body, 7)["encoder"], "recorded")
        body = self.error(self.client.post("/api/embedding-models/7/build"), 409, "EMBEDDING_ENCODER_UNAVAILABLE")
        self.assertIn("could not be loaded", body["detail"])
        self.store.ledger.update({(7, i): 1 for i in IMAGES})
        body = self.error(self.client.post("/api/embedding-models/7/activate", json={}), 409, "EMBEDDING_ENCODER_UNAVAILABLE")
        self.assertIn("could not be loaded", body["detail"])
        self.assertEqual(self.store.models[1]["status"], "current")  # nothing switched

    # -- the switch window, the collection's real points, the old version's own weights --------------

    def built_new_version(self):
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        self.assertEqual(self.model(self.wait_build(), new_id)["status"], "ready")
        return new_id

    def index_one_more(self, image_id, model_id):
        """An image indexed through the stack in place: in PostgreSQL, the point and ledger row of model_id only."""
        self.store.images[image_id] = {"payload_version": 1, "original_url": f"https://cdn/o/{image_id}.jpg"}
        collection = self.store.models[model_id]["collection"]
        self.qdrant[collection][image_id] = {"vector": [1.0, 0, 0, 0], "payload": {}}
        self.store.ledger[(model_id, image_id)] = 1

    def test_a_switch_tops_up_what_was_indexed_after_the_build(self):
        new_id = self.built_new_version()
        late = "00000009-0000-4000-8000-000000000000"
        self.index_one_more(late, 1)  # went into the old collection after the build ended
        self.assertEqual(self.model(self.ok(self.client.get("/api/embedding-models")), new_id)["missing"], 1)
        switched = self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        self.assertEqual((switched["switched"], switched["caught_up"], switched["remaining"]), (True, 1, 0))
        self.assertEqual(switched["model"]["missing"], 0)
        self.assertEqual(self.qdrant["new_v2"][late]["vector"], PROBE_NEW)  # embedded with the new encoder

    def test_a_switch_holds_index_writes_back_until_the_new_stack_is_in(self):
        new_id = self.built_new_version()
        in_flight = self.runtime.vector_writes.shared()
        in_flight.__enter__()  # an index request writing into the old collection right now
        done = threading.Event()
        outcome = {}

        def switch():
            outcome["response"] = self.client.post(f"/api/embedding-models/{new_id}/activate", json={})
            done.set()

        threading.Thread(target=switch, daemon=True).start()
        self.assertFalse(done.wait(0.3))  # waits for the write in flight
        self.assertTrue(self.runtime.vector_writes.closed)  # and new writes wait for the switch
        late = "00000009-0000-4000-8000-000000000000"
        self.index_one_more(late, 1)  # what the write in flight stored
        in_flight.__exit__(None, None, None)
        self.assertTrue(done.wait(5))
        switched = self.ok(outcome["response"])
        self.assertEqual((switched["caught_up"], switched["remaining"]), (1, 0))  # not left behind
        self.assertFalse(self.runtime.vector_writes.closed)
        self.assertEqual(self.runtime.retrieval.model.id, new_id)

    def test_a_version_far_behind_needs_its_build_or_an_explicit_switch(self):
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        with patch.object(registry_module, "FINAL_CATCHUP_MAX", 2):
            body = self.error(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}), 409, "EMBEDDING_MODEL_INCOMPLETE")
            self.assertIn("5 stored images", body["detail"])
            switched = self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={"allow_missing": True}))
        self.assertEqual((switched["caught_up"], switched["remaining"]), (0, 5))  # left to builds and the reconciler
        self.assertFalse(self.runtime.vector_writes.closed)

    def test_points_lost_from_the_collection_are_noticed_and_embedded_again(self):
        new_id = self.built_new_version()
        self.qdrant["new_v2"].clear()  # Qdrant lost the points; the ledger still lists 5
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual((self.model(body, new_id)["embedded"], self.model(body, new_id)["points"]), (5, 0))
        with patch.object(registry_module, "FINAL_CATCHUP_MAX", 2):  # the ledger is not trusted for a switch
            self.error(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}), 409, "EMBEDDING_MODEL_INCOMPLETE")
        self.qdrant["new_v2"].clear()
        self.store.ledger.update({(new_id, i): 1 for i in IMAGES})  # the ledger claims them again
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        build = self.wait_build()["build"]
        self.assertEqual((build["verified"], build["repaired"], build["embedded"], build["remaining"]), (5, 5, 5, 0))
        self.assertEqual(sorted(self.qdrant["new_v2"]), IMAGES)

    def test_a_dropped_collection_is_made_again_and_filled_by_a_build(self):
        new_id = self.built_new_version()
        del self.qdrant["new_v2"]
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertIsNone(self.model(body, new_id)["points"])
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        build = self.wait_build()["build"]
        self.assertEqual((build["verified"], build["embedded"], build["remaining"]), (0, 5, 0))  # ledger void: all again
        self.assertEqual(sorted(self.qdrant["new_v2"]), IMAGES)

    def test_the_old_version_is_topped_up_from_its_own_weights_before_a_roll_back(self):
        new_id = self.built_new_version()
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        for n in range(6, 9):  # indexed after the switch: only the new collection has them
            self.index_one_more(f"0000000{n}-0000-4000-8000-000000000000", new_id)
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual((self.model(body, 1)["missing"], self.model(body, 1)["encoder"]), (3, "recorded"))
        self.ok(self.client.post("/api/embedding-models/1/build"))  # loads the old weights from the image
        body = self.wait_build()
        self.assertEqual((body["build"]["embedded"], self.model(body, 1)["missing"]), (3, 0))
        self.assertEqual(self.qdrant["dino_v1"]["00000006-0000-4000-8000-000000000000"]["vector"], PROBE_OLD)
        back = self.ok(self.client.post("/api/embedding-models/1/activate", json={}))
        self.assertEqual((back["caught_up"], back["remaining"]), (0, 0))

    def test_weights_gone_from_the_image_cannot_be_loaded(self):
        gone = {**fingerprint(unit([0.3, 0.3, 0.9, 0.1]), "config/model/embedding/embeddingcls-0000/model.onnx"),
                "files": [{"name": "model.onnx", "size": 10, "sha256": "ab" * 32}], "artifact": "ab" * 32}
        self.store.add_model(id=7, name="other", version="v7", collection="other_v7", status="ready", fingerprint=gone)
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual((self.model(body, 7)["weights"], self.model(body, 7)["artifact"]), ("missing", "ab" * 32))
        body = self.error(self.client.post("/api/embedding-models/7/build"), 409, "EMBEDDING_ENCODER_UNAVAILABLE")
        self.assertIn("are not in this image", body["detail"])

    def test_a_retrieval_stack_that_could_not_pair_is_fixed_by_switching(self):
        self.runtime.retrieval = None  # EMBEDDING_CONFIG changed and the old weights are gone
        self.runtime.retrieval_error_code = "EMBEDDING_MODEL_MISMATCH"
        self.runtime.retrieval_error = "EMBEDDING_MODEL_MISMATCH: the weights are gone"
        self.runtime.reconciler = None
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual(body["retrieval"], {"ready": False, "model_id": None, "encoder": None,
                                             "code": "EMBEDDING_MODEL_MISMATCH", "detail": "EMBEDDING_MODEL_MISMATCH: the weights are gone"})
        new_id = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]["id"]
        self.ok(self.client.post(f"/api/embedding-models/{new_id}/build"))
        self.wait_build()
        with patch("app.bootstrap.reconciler.start_reconciler", return_value="reconciler") as start:
            self.ok(self.client.post(f"/api/embedding-models/{new_id}/activate", json={}))
        start.assert_called_once()
        self.assertEqual(self.runtime.retrieval.model.id, new_id)
        self.assertIsNone(self.runtime.retrieval_error)
        self.assertEqual(self.runtime.reconciler, "reconciler")

    def test_register_validation_and_collection_names(self):
        self.error(self.client.post("/api/embedding-models", json={"version": "a b"}), 422, "VALIDATION_ERROR")
        self.error(self.client.post("/api/embedding-models", json={"nope": 1}), 422, "VALIDATION_ERROR")
        self.qdrant["new_v2"] = {"left": {}}  # someone else's collection: never reused
        model = self.ok(self.client.post("/api/embedding-models", json={"version": "v2"}), 201)["model"]
        self.assertEqual(model["collection"], "new_v2_2")
        self.assertEqual(self.qdrant["new_v2"], {"left": {}})

    def test_a_failed_registration_drops_the_collection_it_made(self):
        self.store.add_model(id=9, name="new", version="v2", collection="elsewhere", status="retired")
        self.error(self.client.post("/api/embedding-models", json={"version": "v2"}), 409, "EMBEDDING_MODEL_EXISTS")
        self.assertNotIn("new_v2", self.qdrant)

    def test_unknown_ids_and_unavailable_registry(self):
        self.error(self.client.post("/api/embedding-models/99/build"), 404, "EMBEDDING_MODEL_NOT_FOUND")
        self.error(self.client.post("/api/embedding-models/99/activate", json={}), 404, "EMBEDDING_MODEL_NOT_FOUND")
        self.error(self.client.post("/api/embedding-models/99/retire"), 404, "EMBEDDING_MODEL_NOT_FOUND")
        self.error(self.client.post("/api/embedding-models/0/retire"), 422, "VALIDATION_ERROR")
        self.ok(self.client.post("/api/embedding-models/99/build/stop"))  # nothing to stop: as it is
        self.runtime.models = None
        self.runtime.retrieval_error = "TOTAL_CONFIG.retrieval_enabled is false"
        self.error(self.client.get("/api/embedding-models"), 503, "NOT_READY")
        self.runtime.models = self.registry
        self.runtime.catalog = None
        self.runtime.catalog_error = "postgres down"
        self.error(self.client.get("/api/embedding-models"), 503, "NOT_READY")

    def test_no_configured_encoder_means_nothing_to_register(self):
        self.runtime.embedder = None
        self.runtime.initialization_error = "FileNotFoundError: no onnx"
        body = self.ok(self.client.get("/api/embedding-models"))
        self.assertEqual(body["configured"]["loaded"], False)
        self.assertIn("no onnx", body["configured"]["detail"])
        self.error(self.client.post("/api/embedding-models", json={}), 503, "NOT_READY")


if __name__ == "__main__":
    unittest.main()
