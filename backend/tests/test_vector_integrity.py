"""What keeps a model version's vectors honest, without a database or a real Qdrant:

- the weight files' SHA-256 (the .onnx and the external data it names), on a real tiny ONNX;
- the identity rule (preprocessing + file hashes + test vector) and weights that moved;
- the ledger checked against the collection's real points (verify_ledger);
- the write gate a model version switch closes;
- the reconciler's vector pass (ledger slices, missing vectors embedded behind the gate);
- the composition root clearing the ledger of a collection made anew, following moved weights,
  and refusing at once when a version's weights are gone;
- a hot reload never reusing a session whose weight files were replaced in place.
"""

import contextlib
import copy
import hashlib
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from app.bootstrap import lifespan
from app.bootstrap import retrieval_stack as rs
from app.bootstrap.state import ApplicationState
from app.bootstrap.write_gate import VectorWriteGate
from app.clients import inference
from app.clients.inference import Embedder, artifact_hash, model_files, weights_status
from app.core.config.settings import ReconcileSettings
from app.core.errors import AppError
from app.services.retrieval import model_versions as mv
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.build_embeddings import BuildBatch, verify_ledger
from app.services.retrieval.reconcile import ReconcileLimits, ReconcileUseCase
from app.services.retrieval.sync_scenes import PushResult

SPEC = {"model_path": "config/model/embedding/embeddingcls-aaaa/model.onnx", "output_name": "cls_emb",
        "scale": [224, 224], "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225], "img_interp": "cubic"}
PROBE = [0.6, 0.8, 0.0, 0.0]


def ids(*numbers):
    return [f"00000000-0000-4000-8000-{n:012d}" for n in numbers]


def fingerprint(*, path=SPEC["model_path"], hashes=None, probe=PROBE, **spec):
    fp = {"spec": {**SPEC, "model_path": path, **spec}, "probe": list(probe)}
    if hashes is not None:
        fp["files"] = [{"name": f"f{i}", "size": 1, "sha256": h} for i, h in enumerate(hashes)]
        fp["artifact"] = hashes[0]
    return fp


def tiny_onnx(folder, *, name="model.onnx", data="model.onnx.data", value=1.0):
    """A one-MatMul ONNX whose weight lives in an external data file, like the production encoder."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weight = numpy_helper.from_array(np.full((4, 4), value, dtype=np.float32), name="W")
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["x", "W"], ["y"])], "g",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 4])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 4])],
        initializer=[weight],
    )
    path = os.path.join(folder, name)
    onnx.save_model(helper.make_model(graph), path, save_as_external_data=True, all_tensors_to_one_file=True,
                    location=data, size_threshold=0)
    return path


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class WeightFilesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.path = tiny_onnx(self.dir)

    def test_the_onnx_and_the_external_data_it_names_are_hashed(self):
        files = model_files(self.path)
        self.assertEqual([f["name"] for f in files], ["model.onnx", "model.onnx.data"])
        self.assertEqual([f["sha256"] for f in files], [sha(self.path), sha(os.path.join(self.dir, "model.onnx.data"))])
        self.assertEqual([f["size"] for f in files], [os.path.getsize(os.path.join(self.dir, f["name"])) for f in files])
        self.assertEqual(artifact_hash(files), hashlib.sha256("\n".join(sorted(f["sha256"] for f in files)).encode()).hexdigest())
        self.assertEqual(artifact_hash(files[:1]), files[0]["sha256"])  # a single-file model: its own hash

    def test_a_recorded_version_knows_whether_its_weights_are_still_there(self):
        recorded = {"spec": {"model_path": self.path.replace("\\", "/")}, "files": model_files(self.path)}
        self.assertEqual(weights_status(recorded), "ok")
        data = os.path.join(self.dir, "model.onnx.data")
        with open(data, "r+b") as fh:  # other weights of the same size, written in place
            fh.seek(0)
            fh.write(b"\x00\x00\x80\x40")
        os.utime(data, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        self.assertEqual(weights_status(recorded), "changed")
        os.remove(data)
        self.assertEqual(weights_status(recorded), "missing")
        self.assertIsNone(weights_status({"spec": {"model_path": self.path}}))  # no files recorded
        self.assertIsNone(weights_status(None))

    def test_a_session_notices_its_weights_replaced_in_place(self):
        encoder = Embedder.__new__(Embedder)
        encoder.spec = SimpleNamespace(model_path=self.path)
        encoder._loaded_files = inference._file_stats(self.path)
        self.assertTrue(encoder.files_unchanged())
        tiny_onnx(self.dir, value=2.0)  # a new export at the same path
        os.utime(os.path.join(self.dir, "model.onnx.data"), ns=(time.time_ns() + 2 * 10**9,) * 2)
        self.assertFalse(encoder.files_unchanged())
        encoder._loaded_files = None  # files that could not be listed: never reused
        self.assertFalse(encoder.files_unchanged())


class IdentityTests(unittest.TestCase):
    def test_the_file_hashes_tell_weights_apart_whatever_the_test_vector_says(self):
        recorded = fingerprint(hashes=["a" * 64, "b" * 64])
        self.assertTrue(mv.same_encoder(recorded, fingerprint(hashes=["b" * 64, "a" * 64])))
        self.assertFalse(mv.same_encoder(recorded, fingerprint(hashes=["a" * 64, "c" * 64])))  # a light fine-tune
        self.assertTrue(mv.same_encoder(recorded, fingerprint()))  # recorded before files: the test vector decides
        self.assertFalse(mv.same_encoder(recorded, fingerprint(hashes=["a" * 64, "b" * 64], probe=[0.8, 0.6, 0, 0])))
        self.assertEqual(mv.file_hashes(recorded), ["a" * 64, "b" * 64])
        self.assertEqual(mv.artifact_of(recorded), "a" * 64)
        self.assertIsNone(mv.file_hashes({"files": [{"name": "x"}]}))

    def test_weights_found_at_another_path_move_the_record(self):
        recorded = fingerprint(hashes=["a" * 64])
        self.assertTrue(mv.moved(recorded, fingerprint(path="config/model/embedding/embeddingcls-bbbb/model.onnx", hashes=["a" * 64])))
        self.assertFalse(mv.moved(recorded, fingerprint(hashes=["a" * 64])))
        self.assertTrue(mv.moved(fingerprint(), fingerprint(hashes=["a" * 64])))  # the record learns the hashes
        self.assertFalse(mv.moved(recorded, fingerprint(path="elsewhere.onnx", hashes=["c" * 64])))  # another model


class FakeLedger:
    def __init__(self, embedded):
        self.rows = sorted(embedded)
        self.forgotten = []

    def list_embedded_ids(self, model_id, *, after=None, limit=256):
        return [i for i in self.rows if after is None or i > after][:limit]

    def forget_embeddings(self, model_id, image_ids):
        self.forgotten.extend(image_ids)
        self.rows = [i for i in self.rows if i not in image_ids]
        return len(image_ids)


class Points:
    def __init__(self, present):
        self.present = set(present)

    def existing_ids(self, image_ids):
        return {i for i in image_ids if i in self.present}


class VerifyLedgerTests(unittest.TestCase):
    def test_rows_whose_point_is_gone_are_forgotten(self):
        ledger = FakeLedger(ids(1, 2, 3, 4, 5))
        check = verify_ledger(7, Points(ids(1, 3, 5)), ledger, batch=2)
        self.assertEqual((check.checked, check.forgotten, check.done, check.last_id), (5, 2, True, None))
        self.assertEqual(ledger.forgotten, ids(2, 4))

    def test_a_slice_starts_where_the_last_one_stopped(self):
        ledger = FakeLedger(ids(1, 2, 3, 4, 5))
        first = verify_ledger(7, Points(ids(1, 2, 3, 4, 5)), ledger, limit=2, batch=2)
        self.assertEqual((first.checked, first.last_id, first.done), (2, ids(2)[0], False))
        rest = verify_ledger(7, Points(ids(1, 2, 3, 4, 5)), ledger, after=first.last_id, limit=10)
        self.assertEqual((rest.checked, rest.last_id, rest.done), (3, None, True))


class WriteGateTests(unittest.TestCase):
    def test_writers_run_together_and_a_switch_waits_for_them(self):
        gate = VectorWriteGate(wait_seconds=2)
        order = []
        with gate.shared(), gate.shared():  # two writes at once
            switch = threading.Thread(target=lambda: (gate.exclusive().__enter__(), order.append("switch")))
            switch.start()
            time.sleep(0.1)
            self.assertEqual(order, [])  # waiting for the writes in flight
            self.assertTrue(gate.closed)
            order.append("writes done")
        switch.join(2)
        self.assertEqual(order, ["writes done", "switch"])

    def test_a_write_waits_for_the_switch_and_then_runs(self):
        gate = VectorWriteGate(wait_seconds=2)
        order = []
        with gate.exclusive():
            writer = threading.Thread(target=lambda: gate.shared().__enter__() or order.append("write"))
            writer.start()
            time.sleep(0.1)
            self.assertEqual(order, [])
            order.append("switched")
        writer.join(2)
        self.assertEqual(order, ["switched", "write"])
        self.assertFalse(gate.closed)

    def test_waits_end_with_not_ready_and_the_gate_opens_again(self):
        gate = VectorWriteGate(wait_seconds=0.05)
        with gate.shared():
            with self.assertRaises(AppError) as ctx, gate.exclusive():
                pass
            self.assertEqual(ctx.exception.code, "NOT_READY")
        self.assertFalse(gate.closed)
        with gate.exclusive():
            with self.assertRaises(AppError) as ctx, gate.shared():
                pass
            self.assertEqual(ctx.exception.code, "NOT_READY")


class FakeFill:
    """The current model's BuildEmbeddingsUseCase: backlog ids, `failing` ones cannot be read."""

    def __init__(self, backlog, failing=()):
        self.backlog = sorted(backlog)
        self.failing = set(failing)
        self.calls = []

    def run_batch(self, *, after=None, limit=16, exclude=None):
        self.calls.append((after, limit))
        rows = [i for i in self.backlog if after is None or i > after][:limit]
        embedded = [i for i in rows if i not in self.failing]
        self.backlog = [i for i in self.backlog if i not in embedded]
        failures = [{"image_id": i, "code": "IMAGE_FILE_UNAVAILABLE", "detail": "x"} for i in rows if i in self.failing]
        return BuildBatch(embedded=embedded, failures=failures, last_id=rows[-1] if rows else None, done=len(rows) < limit)


class RoundMetadata:
    def __init__(self, behind=()):
        self.behind = list(behind)

    def list_unsynced_payload_ids(self, *, after=None, until=None, limit=200):
        return [i for i in self.behind if after is None or i > after][:limit]

    @contextmanager
    def try_maintenance_lock(self, key):
        yield True

    def count_pending_deletions(self):
        return 0


class ReconcileVectorPassTests(unittest.TestCase):
    def round(self, *, ledger, points, fill, behind=(), missing=(), **limits):
        metadata = RoundMetadata(behind)
        entered = []

        @contextmanager
        def gate():
            entered.append(1)
            yield

        sync = SimpleNamespace(push_images=lambda image_ids: PushResult(
            pushed={i: 2 for i in image_ids if i not in missing}, missing=[i for i in image_ids if i in missing]))
        deletions = SimpleNamespace(run_once=lambda limit=50: {"scanned": 0, "processed": [], "failed": []})
        use_case = ReconcileUseCase(metadata, sync=sync, deletions=deletions, limits=ReconcileLimits(**limits),
                                    ledger=(1, points, ledger), fill=fill, write_gate=gate)
        return use_case, entered

    def test_a_round_checks_a_slice_and_embeds_what_has_no_vector(self):
        ledger = FakeLedger(ids(1, 2, 3))
        fill = FakeFill(ids(4, 5, 6, 7, 8), failing=ids(5))
        use_case, entered = self.round(ledger=ledger, points=Points(ids(1, 3)), fill=fill, max_verifications=2, max_embeddings=3)
        result = use_case.run_once()
        self.assertEqual((result.vectors.verified, result.vectors.forgotten), (2, 1))  # ids(2) lost its point
        self.assertEqual(result.verify_cursor, ids(2)[0])  # the next round checks from there
        self.assertEqual((result.vectors.embedded, result.vectors.failed), (2, 1))
        self.assertEqual(result.embed_cursor, ids(6)[0])
        self.assertEqual(len(entered), len(fill.calls))  # every batch behind the write gate
        again = use_case.run_once(verify_cursor=result.verify_cursor, embed_cursor=result.embed_cursor)
        self.assertEqual((again.vectors.verified, again.verify_cursor), (1, None))  # the end: start over next time
        self.assertEqual(again.vectors.embedded, 2)
        self.assertIsNone(again.embed_cursor)

    def test_a_pushed_payload_without_its_point_forgets_the_row(self):
        ledger = FakeLedger(ids(1, 2))
        use_case, _ = self.round(ledger=ledger, points=Points(ids(1, 2)), fill=None, behind=ids(1, 2), missing=ids(2),
                                 max_verifications=0)
        result = use_case.run_once()
        self.assertEqual(result.payloads.missing, 1)
        self.assertEqual(ledger.forgotten, ids(2))

    def test_without_an_encoder_the_ledger_is_still_checked(self):
        ledger = FakeLedger(ids(1, 2))
        use_case, entered = self.round(ledger=ledger, points=Points(ids(1)), fill=None)
        result = use_case.run_once()
        self.assertEqual((result.vectors.forgotten, result.vectors.embedded, entered), (1, 0, []))

    def test_settings_turn_the_passes_off_with_zero_and_refuse_negatives(self):
        settings = ReconcileSettings.from_config({"RECONCILE_CONFIG": {"max_verifications": 0, "max_embeddings": 0}})
        self.assertEqual((settings.max_verifications, settings.max_embeddings), (0, 0))
        self.assertEqual((ReconcileSettings().max_verifications, ReconcileSettings().max_embeddings), (2000, 100))
        with self.assertRaises(ValueError):
            ReconcileSettings.from_config({"RECONCILE_CONFIG": {"max_embeddings": -1}})


class CompositionRootTests(unittest.TestCase):
    CONFIG = {"POSTGRES_CONFIG": {"host": "pg", "user": "u", "password": "p", "database": "d"},
              "QDRANT_CONFIG": {"url": "http://q:6333"}}

    def setUp(self):
        self.row = {"id": 1, "name": "dino", "version": "v1", "collection": "c1", "dim": 4, "vector_dim": 4,
                    "distance": "cosine", "input_size": 224, "weights_uri": "w", "preprocess": {}}
        self.metadata = MagicMock()
        self.metadata.get_current_embedding_model.side_effect = lambda: dict(self.row)
        self.vectors = MagicMock()
        for name, value in (("QdrantStore", Mock()), ("QdrantVectorStore", Mock(return_value=self.vectors)),
                            ("build_image_storage", Mock(return_value=MagicMock()))):
            p = patch.object(rs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_a_collection_made_anew_voids_its_ledger(self):
        self.vectors.collection_exists.return_value = False
        self.metadata.clear_embeddings.return_value = 5
        rs.build_retrieval_infra(self.CONFIG, metadata=self.metadata)
        self.metadata.clear_embeddings.assert_called_once_with(1)
        self.vectors.collection_exists.return_value = True
        self.metadata.clear_embeddings.reset_mock()
        rs.build_retrieval_infra(self.CONFIG, metadata=self.metadata)
        self.metadata.clear_embeddings.assert_not_called()

    def encoder(self, fp):
        return SimpleNamespace(model_path=fp["spec"]["model_path"], fingerprint=lambda: fp, probe_dim=lambda: 4)

    def test_the_configured_encoder_at_a_new_path_moves_the_record(self):
        self.row["fingerprint"] = fingerprint(hashes=["a" * 64])
        moved_to = fingerprint(path="config/model/embedding/embeddingcls-bbbb/model.onnx", hashes=["a" * 64])
        configured = self.encoder(moved_to)
        stack = rs.build_retrieval_stack(self.CONFIG, SimpleNamespace(paths=SimpleNamespace(save_images_dir="s")),
                                         embedder=configured, metadata=self.metadata, load=Mock())
        self.assertIs(stack.embedding, configured)
        self.metadata.refresh_model_fingerprint.assert_called_once_with(1, moved_to)
        self.assertEqual(stack.model.weights_uri, moved_to["spec"]["model_path"])

    def test_a_version_whose_weights_left_the_image_is_refused_without_loading(self):
        self.row["fingerprint"] = {**fingerprint(hashes=["a" * 64]), "files": [{"name": "gone.onnx", "size": 1, "sha256": "a" * 64}]}
        load = Mock()
        with self.assertRaises(AppError) as ctx:
            rs.build_retrieval_stack(self.CONFIG, SimpleNamespace(paths=SimpleNamespace(save_images_dir="s")),
                                     embedder=self.encoder(fingerprint(hashes=["c" * 64], probe=[0.8, 0.6, 0, 0])),
                                     metadata=self.metadata, load=load)
        self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_MISMATCH")
        self.assertIn("are not in this image", ctx.exception.detail)
        load.assert_not_called()


class HotReloadTests(unittest.TestCase):
    def test_a_session_whose_weights_were_replaced_is_not_reused(self):
        config = {"EMBEDDING_CONFIG": {"model_path": "config/model/x.onnx"}}
        spec = inference.EmbeddingSpec.from_config(config)
        same = SimpleNamespace(spec=spec, files_unchanged=lambda: True)
        replaced = SimpleNamespace(spec=spec, files_unchanged=lambda: False)
        runtime = ApplicationState(config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(), embedder=same)
        self.assertIs(lifespan._loaded_embedder_for(runtime, config), same)
        runtime.embedder = replaced
        with self.assertLogs("lifespan", level="WARNING"):
            self.assertIsNone(lifespan._loaded_embedder_for(runtime, config))


if __name__ == "__main__":
    unittest.main()
