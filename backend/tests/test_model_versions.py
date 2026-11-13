"""Model versions without a database: the fingerprint (which encoder writes which embedding
space), naming, the status rules, one build batch, deletions across versions, and how the
retrieval stack pairs an encoder with the current version.

The SQL is in test_pg_live; the registry and its HTTP routes in test_embedding_models_http.
"""

import contextlib
import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from app.bootstrap import retrieval_stack as rs
from app.clients.inference import PROBE_SHAPE, Embedder, EmbeddingSpec, config_for_fingerprint, probe_image
from app.core import errors as err
from app.core.errors import AppError
from app.services.retrieval import model_versions as mv
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.build_embeddings import BuildEmbeddingsUseCase
from app.services.retrieval.delete_image import DeletionWorker

SPEC = {"model_path": "config/model/dino/a.onnx", "output_name": "cls_emb", "scale": [224, 224],
        "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225], "img_interp": "cubic"}


def unit(values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def fingerprint(probe, **spec):
    return {"spec": {**SPEC, **spec}, "probe": list(probe)}


PROBE_A = unit([0.9, 0.1, 0.3, 0.2])
PROBE_B = unit([0.1, 0.9, 0.2, 0.4])  # other weights


class FingerprintTests(unittest.TestCase):
    def test_the_same_encoder_on_other_hardware_is_the_same_space(self):
        a = fingerprint(PROBE_A)
        jitter = [v + 1e-5 * (-1) ** i for i, v in enumerate(PROBE_A)]  # GPU vs CPU arithmetic
        self.assertTrue(mv.same_encoder(a, fingerprint(jitter)))
        self.assertTrue(mv.same_encoder(a, fingerprint(PROBE_A, model_path="/elsewhere/a.onnx")))  # moved file
        self.assertTrue(mv.same_encoder(a, fingerprint(PROBE_A, img_interp="CUBIC")))

    def test_other_weights_or_preprocessing_are_another_space(self):
        a = fingerprint(PROBE_A)
        self.assertFalse(mv.same_encoder(a, fingerprint(PROBE_B)))
        tuned = unit([0.9, 0.2, 0.3, 0.2])  # a light fine-tune of the same weights
        self.assertLess(mv.cosine(PROBE_A, tuned), mv.SAME_MODEL_MIN_COSINE)
        self.assertGreater(mv.cosine(PROBE_A, tuned), 0.98)
        self.assertFalse(mv.same_encoder(a, fingerprint(tuned)))
        for change in ({"scale": [336, 336]}, {"mean": [0.5, 0.5, 0.5]}, {"img_interp": "linear"}, {"output_name": "x"}):
            with self.subTest(change=change):
                self.assertFalse(mv.same_encoder(a, fingerprint(PROBE_A, **change)))
        self.assertFalse(mv.same_encoder(a, fingerprint(PROBE_A + [0.0])))  # other dim

    def test_missing_or_malformed_fingerprints_never_match(self):
        a = fingerprint(PROBE_A)
        for other in (None, {}, {"spec": SPEC}, {"probe": PROBE_A}, {"spec": SPEC, "probe": "x"},
                      {"spec": SPEC, "probe": [float("nan")] * 4}, MagicMock()):
            with self.subTest(other=other):
                self.assertFalse(mv.same_encoder(a, other))
                self.assertFalse(mv.same_encoder(other, a))
        self.assertEqual(mv.fingerprint_dim(a), 4)
        self.assertIsNone(mv.fingerprint_dim(None))

    def test_the_probe_image_is_fixed(self):
        image = probe_image()
        self.assertEqual(image.shape, (*PROBE_SHAPE, 3))
        self.assertEqual(image.dtype, np.uint8)
        np.testing.assert_array_equal(image, probe_image())
        self.assertGreater(len(np.unique(image.reshape(-1, 3), axis=0)), 1000)  # not a flat image

    def test_an_encoder_fingerprints_its_effective_preprocessing_once(self):
        model = SimpleNamespace(input_h=256, input_w=192, detect=Mock(return_value=np.asarray([3.0, 4.0], dtype=np.float32)))
        encoder = Embedder.__new__(Embedder)
        encoder._model, encoder._lock, encoder._fingerprint = model, contextlib.nullcontext(), None
        encoder.spec = EmbeddingSpec.from_config({"EMBEDDING_CONFIG": {"model_path": "config\\model\\x.onnx"}})
        fp = encoder.fingerprint()
        self.assertEqual(fp["spec"]["model_path"], "config/model/x.onnx")  # loads on Linux too
        self.assertEqual(fp["spec"]["scale"], [256, 192])  # what the ONNX input forced, not the config default
        self.assertEqual(fp["probe"], [3.0, 4.0])
        self.assertIs(encoder.fingerprint(), fp)
        model.detect.assert_called_once()
        self.assertEqual(model.detect.call_args.args[0].shape, (*PROBE_SHAPE, 3))

    def test_a_recorded_spec_loads_with_this_process_runtime_flags(self):
        base = {"EMBEDDING_CONFIG": {"model_path": "new.onnx", "use_gpu": True, "use_iobinding": True, "scale": [9, 9]},
                "TOTAL_CONFIG": {"device_id": 2}}
        config = config_for_fingerprint(SPEC, base)
        self.assertEqual(config["EMBEDDING_CONFIG"]["model_path"], SPEC["model_path"])
        self.assertEqual(config["EMBEDDING_CONFIG"]["scale"], [224, 224])
        self.assertTrue(config["EMBEDDING_CONFIG"]["use_gpu"])
        self.assertEqual(config["TOTAL_CONFIG"]["device_id"], 2)


class NamingAndStatusTests(unittest.TestCase):
    def test_labels_and_defaults(self):
        self.assertEqual(mv.label(" dino-v3.1_lora ", "name"), "dino-v3.1_lora")
        for bad in ("", "-x", "a b", "中文", "x" * 65, None):
            with self.subTest(bad=bad), self.assertRaises(AppError) as ctx:
                mv.label(bad, "version")
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(mv.default_name("config/model/dino/multitask_cls.onnx"), "multitask_cls")
        self.assertEqual(mv.default_name(""), "model")
        from datetime import datetime
        self.assertEqual(mv.default_version(datetime(2026, 11, 12, 15, 30)), "20261112-1530")

    def test_collection_names_are_qdrant_safe_and_unique(self):
        self.assertEqual(mv.collection_name("Dino.V3", "20261112-1530", set()), "dino_v3_20261112_1530")
        self.assertEqual(mv.collection_name("a", "b", {"a_b"}), "a_b_2")
        self.assertEqual(mv.collection_name("a", "b", {"a_b", "a_b_2"}), "a_b_3")

    def test_registration_fields_come_from_the_fingerprint(self):
        fields = mv.registration_fields(name="n", version="v", collection="c", fingerprint=fingerprint(PROBE_A, scale=[224, 336]))
        self.assertEqual((fields["dim"], fields["distance"], fields["input_size"]), (4, "cosine", 336))
        self.assertEqual(fields["weights_uri"], SPEC["model_path"])
        self.assertEqual(set(fields["preprocess"]), set(mv.SPACE_KEYS))

    def test_what_each_status_allows(self):
        row = {"id": 2, "collection": "c2"}
        mv.require_activatable({**row, "status": "building"}, backlog=0, allow_missing=False)
        with self.assertRaises(AppError) as ctx:
            mv.require_activatable({**row, "status": "ready"}, backlog=3, allow_missing=False)
        self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_INCOMPLETE")
        self.assertIn("3 stored images", ctx.exception.detail)
        mv.require_activatable({**row, "status": "ready"}, backlog=3, allow_missing=True)
        for check in (lambda: mv.require_activatable({**row, "status": "retired"}, backlog=0, allow_missing=True),
                      lambda: mv.require_retirable({**row, "status": "current"}),
                      lambda: mv.require_buildable({**row, "status": "retired"})):
            with self.assertRaises(AppError) as ctx:
                check()
            self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_STATE")
        mv.require_retirable({**row, "status": "ready"})
        mv.require_buildable({**row, "status": "current"})  # filling the current version's gaps

    def test_checking_a_collection_against_an_encoder(self):
        samples = [{"id": "a", "original_url": "u/a"}, {"id": "b", "original_url": "u/b"}, {"id": "c", "original_url": None}]
        stored = {"a": PROBE_A, "b": PROBE_B}
        same = mv.check_collection_encoder(samples=samples, stored=stored, read_original=lambda url: url,
                                           embed=lambda data: PROBE_A if data == "u/a" else PROBE_B)
        self.assertEqual((same["checked"], same["matches"]), (2, True))
        other = mv.check_collection_encoder(samples=samples, stored=stored, read_original=lambda url: url,
                                            embed=lambda data: PROBE_B)
        self.assertFalse(other["matches"])
        self.assertLess(other["min_similarity"], 0.9)

        def unreadable(url):
            raise err.image_file_unavailable("gone")
        none = mv.check_collection_encoder(samples=samples, stored=stored, read_original=unreadable, embed=lambda d: d)
        self.assertEqual((none["checked"], none["matches"]), (0, None))


# -- one build batch ---------------------------------------------------------------------

IMG = [f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 6)]
MODEL = ActiveModel(id=2, name="dino", version="v2", collection="c2", dim=4, distance="cosine")


class BuildBatchTests(unittest.TestCase):
    def setUp(self):
        self.backlog = [{"id": i, "original_url": f"https://cdn/o/{i}.jpg"} for i in IMG[:4]]
        self.models = Mock()
        self.models.list_embedding_backlog.return_value = self.backlog
        self.models.record_embeddings.side_effect = lambda *, model_id, versions: sorted(versions)
        self.metadata = Mock()
        self.metadata.get_payload_rows.return_value = [
            {"id": i, "payload_version": n + 1, "category_id": 7, "label_status": "auto", "food_state": None, "scenes": ["oven"]}
            for n, i in enumerate(IMG[:4])
        ]
        self.storage = Mock()
        self.storage.read_by_url_or_key.side_effect = lambda url: url.encode()
        self.encoder = Mock()
        self.encoder.embed_bytes.return_value = PROBE_A
        self.vectors = Mock()
        self.uc = BuildEmbeddingsUseCase(model=MODEL, encoder=self.encoder, vectors=self.vectors,
                                         metadata=self.metadata, models=self.models, storage=self.storage)

    def test_a_batch_embeds_the_next_images_and_records_them(self):
        batch = self.uc.run_batch(after=None, limit=4, exclude=["x"])
        self.models.list_embedding_backlog.assert_called_once_with(2, after=None, limit=4, exclude=["x"])
        self.assertEqual(batch.embedded, sorted(IMG[:4]))
        self.assertEqual((batch.last_id, batch.done, batch.failures, batch.gone), (IMG[3], False, [], []))
        point_id, vector, payload = self.vectors.upsert.call_args_list[0].args
        self.assertEqual((point_id, vector), (IMG[0], PROBE_A))
        self.assertEqual(payload["scenes"], ["oven"])
        self.assertEqual(payload["category_id"], 7)
        self.models.record_embeddings.assert_called_once_with(model_id=2, versions={i: n + 1 for n, i in enumerate(IMG[:4])})

    def test_one_image_failing_does_not_stop_the_others(self):
        def read(url):
            if IMG[1] in url:
                raise err.image_file_unavailable("NoSuchKey: secret bucket detail")
            return b"x"
        self.storage.read_by_url_or_key.side_effect = read
        self.encoder.embed_bytes.side_effect = [PROBE_A, RuntimeError("cv2 decode"), [0.0, 0.0, 0.0, 0.0]]
        batch = self.uc.run_batch(limit=16)
        self.assertEqual(batch.embedded, [IMG[0]])
        self.assertEqual([(f["image_id"], f["code"]) for f in batch.failures],
                         [(IMG[1], "IMAGE_FILE_UNAVAILABLE"), (IMG[2], "INDEX_BAD_IMAGE"), (IMG[3], "INDEX_BAD_EMBEDDING")])
        self.assertNotIn("secret", batch.failures[0]["detail"])  # internal detail stays in the log
        self.assertTrue(batch.done)  # fewer than the limit: nothing after

    def test_images_deleted_meanwhile_leave_no_point_behind(self):
        self.metadata.get_payload_rows.return_value = self.metadata.get_payload_rows.return_value[1:]  # IMG[0] gone already
        self.models.record_embeddings.side_effect = lambda *, model_id, versions: sorted(set(versions) - {IMG[2]})
        batch = self.uc.run_batch(limit=16)
        self.assertEqual(sorted(batch.gone), sorted([IMG[0], IMG[2]]))
        self.vectors.delete.assert_called_once_with(IMG[2])  # written, then its image went away
        self.assertNotIn(IMG[0], [c.args[0] for c in self.vectors.upsert.call_args_list])

    def test_a_vector_store_failure_stops_the_build(self):
        self.vectors.upsert.side_effect = err.index_qdrant_failed("down")
        with self.assertRaises(AppError) as ctx:
            self.uc.run_batch(limit=16)
        self.assertEqual(ctx.exception.code, "INDEX_QDRANT_FAILED")
        self.models.record_embeddings.assert_not_called()

    def test_an_empty_backlog_is_done(self):
        self.models.list_embedding_backlog.return_value = []
        batch = self.uc.run_batch()
        self.assertTrue(batch.done)
        self.assertIsNone(batch.last_id)

    def test_payloads_that_moved_on_are_pushed_to_the_version_collection(self):
        rows = {row["id"]: row for row in self.metadata.get_payload_rows.return_value}
        self.metadata.get_payload_rows.side_effect = lambda ids: [rows[i] for i in ids if i in rows]
        self.metadata.list_unsynced_payload_ids.side_effect = [[IMG[0], IMG[1]], []]
        self.metadata.mark_payloads_pushed.side_effect = lambda *, model_id, versions: sorted(versions)
        self.vectors.set_payloads.return_value = []
        pushed = self.uc.push_payloads(limit=2)
        self.assertEqual(pushed, 2)
        self.assertEqual(self.metadata.list_unsynced_payload_ids.call_args_list[0].kwargs["model_id"], 2)
        self.assertEqual(self.metadata.mark_payloads_pushed.call_args.kwargs["model_id"], 2)


# -- deletions reach every version still in use ----------------------------------------------

class DeletionAcrossVersionsTests(unittest.TestCase):
    def test_a_deleted_image_leaves_every_live_collection(self):
        metadata, current, storage = Mock(), Mock(), Mock()
        metadata.get_by_id.return_value = None
        metadata.list_pending_deletions.return_value = [{"image_id": IMG[0], "original_url": "o", "thumb_url": "t"}]
        other_a, other_b = Mock(), Mock()
        worker = DeletionWorker(metadata, current, storage, others=lambda: [other_a, other_b])
        result = worker.run_once(limit=5)
        self.assertEqual(result["processed"], [IMG[0]])
        current.delete.assert_called_once_with(IMG[0])
        other_a.delete.assert_called_once_with(IMG[0])
        other_b.delete.assert_called_once_with(IMG[0])

    def test_a_failure_in_another_collection_keeps_the_row_queued(self):
        metadata, current, storage, other = Mock(), Mock(), Mock(), Mock()
        metadata.get_by_id.return_value = None
        other.delete.side_effect = err.index_qdrant_failed("down")
        worker = DeletionWorker(metadata, current, storage, others=lambda: [other])
        self.assertFalse(worker.clean_up({"image_id": IMG[0], "original_url": "o", "thumb_url": "t"}))
        metadata.complete_deletion.assert_not_called()

    def test_the_infra_lists_the_other_live_collections_it_can_reach(self):
        metadata, vectors = Mock(), Mock()
        metadata.list_embedding_model_rows.return_value = [
            {"id": 1, "collection": "c1", "dim": 4, "distance": "cosine", "status": "ready"},
            {"id": 2, "collection": "c2", "dim": 4, "distance": "cosine", "status": "current"},
            {"id": 3, "collection": "c3", "dim": 4, "distance": "cosine", "status": "building"},
        ]
        siblings = {name: Mock(collection_exists=Mock(return_value=name != "c3")) for name in ("c1", "c3")}
        vectors.sibling.side_effect = lambda *, collection, dim, distance: siblings[collection]
        infra = rs.RetrievalInfra(metadata=metadata, vectors=vectors, storage=Mock(),
                                  model=ActiveModel(id=2, name="d", version="v", collection="c2", dim=4, distance="cosine"))
        self.assertEqual(infra.other_vectors(), [siblings["c1"]])  # c3 was dropped from Qdrant by hand
        self.assertEqual(metadata.list_embedding_model_rows.call_args.kwargs["statuses"], ("building", "ready"))
        infra.other_vectors()
        self.assertEqual(vectors.sibling.call_count, 2)  # one store per collection


# -- pairing the encoder with the current version --------------------------------------------

class FakeEncoder:
    def __init__(self, probe, path="config/model/dino/a.onnx", **spec):
        self.model_path = path
        self._fingerprint = fingerprint(probe, model_path=path, **spec)
        self.embed_bytes = Mock(return_value=list(probe))

    def fingerprint(self):
        return self._fingerprint

    def probe_dim(self):
        return len(self._fingerprint["probe"])


CONFIG = {"POSTGRES_CONFIG": {"host": "pg", "user": "u", "password": "p", "database": "d"},
          "QDRANT_CONFIG": {"url": "http://q:6333"}}
SETTINGS = SimpleNamespace(paths=SimpleNamespace(save_images_dir="save_images"))


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.row = {"id": 1, "name": "dino", "version": "v1", "collection": "c1", "dim": 4, "vector_dim": 4,
                    "distance": "cosine", "input_size": 224, "weights_uri": "w", "preprocess": {}}
        self.metadata = MagicMock()
        self.metadata.get_current_embedding_model.side_effect = lambda: dict(self.row)
        self.metadata.sample_embedded_images.return_value = []
        self.vectors = MagicMock()
        self.storage = MagicMock()
        for name, value in (("QdrantStore", Mock()), ("QdrantVectorStore", Mock(return_value=self.vectors)),
                            ("build_image_storage", Mock(return_value=self.storage))):
            p = patch.object(rs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.load = Mock()

    def build(self, configured, **kwargs):
        return rs.build_retrieval_stack(CONFIG, SETTINGS, embedder=configured, metadata=self.metadata, load=self.load, **kwargs)

    def test_the_configured_encoder_is_used_when_it_wrote_the_collection(self):
        self.row["fingerprint"] = fingerprint(PROBE_A)
        configured = FakeEncoder(PROBE_A)
        stack = self.build(configured)
        self.assertIs(stack.embedding, configured)
        self.load.assert_not_called()
        self.metadata.record_model_fingerprint.assert_not_called()

    def test_another_configured_model_makes_retrieval_load_the_version_own_encoder(self):
        self.row["fingerprint"] = fingerprint(PROBE_A)
        own = FakeEncoder(PROBE_A)
        self.load.return_value = own
        stack = self.build(FakeEncoder(PROBE_B, path="config/model/dino/b.onnx"))
        self.assertIs(stack.embedding, own)  # search keeps the encoder that wrote c1
        self.assertEqual(self.load.call_args.args[0]["model_path"], SPEC["model_path"])
        self.assertIs(stack.index_uc._embedding, own)

    def test_an_encoder_already_in_memory_is_preferred_to_loading_one(self):
        self.row["fingerprint"] = fingerprint(PROBE_A)
        serving = FakeEncoder(PROBE_A)
        stack = self.build(FakeEncoder(PROBE_B), candidates=[serving])
        self.assertIs(stack.embedding, serving)
        self.load.assert_not_called()

    def test_retrieval_refuses_to_start_when_the_version_encoder_is_gone(self):
        self.row["fingerprint"] = fingerprint(PROBE_A)
        for outcome in (FileNotFoundError("no onnx"), FakeEncoder(PROBE_B)):  # missing, or overwritten in place
            with self.subTest(outcome=type(outcome).__name__):
                self.load.reset_mock()
                self.load.side_effect = outcome if isinstance(outcome, Exception) else None
                self.load.return_value = None if isinstance(outcome, Exception) else outcome
                self.vectors.close.reset_mock()
                with self.assertRaises(AppError) as ctx:
                    self.build(FakeEncoder(PROBE_B, path="config/model/dino/b.onnx"))
                self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_MISMATCH")
                self.vectors.close.assert_called_once()  # what was opened is released

    def test_a_version_without_fingerprint_adopts_the_encoder_its_images_agree_with(self):
        samples = [{"id": IMG[0], "original_url": "https://cdn/o/1.jpg"}]
        self.metadata.sample_embedded_images.return_value = samples
        self.vectors.get_vectors.return_value = {IMG[0]: PROBE_A}
        self.storage.read_by_url_or_key.return_value = b"original"
        configured = FakeEncoder(PROBE_A)
        stack = self.build(configured)
        self.assertIs(stack.embedding, configured)
        self.metadata.record_model_fingerprint.assert_called_once_with(1, configured.fingerprint())
        self.assertEqual(stack.model.fingerprint, configured.fingerprint())
        configured.embed_bytes.assert_called_once_with(b"original")

    def test_a_version_without_fingerprint_refuses_an_encoder_its_images_disagree_with(self):
        self.metadata.sample_embedded_images.return_value = [{"id": IMG[0], "original_url": "https://cdn/o/1.jpg"}]
        self.vectors.get_vectors.return_value = {IMG[0]: PROBE_B}  # written by the old model
        with self.assertRaises(AppError) as ctx:
            self.build(FakeEncoder(PROBE_A))
        self.assertEqual(ctx.exception.code, "EMBEDDING_MODEL_MISMATCH")
        self.assertIn("lowest similarity", ctx.exception.detail)
        self.metadata.record_model_fingerprint.assert_not_called()

    def test_an_unverifiable_version_keeps_working_but_records_nothing(self):
        self.metadata.sample_embedded_images.return_value = [{"id": IMG[0], "original_url": "https://cdn/o/1.jpg"}]
        self.vectors.get_vectors.return_value = {IMG[0]: PROBE_A}
        self.storage.read_by_url_or_key.side_effect = err.image_file_unavailable("down")
        configured = FakeEncoder(PROBE_A)
        stack = self.build(configured)
        self.assertIs(stack.embedding, configured)
        self.metadata.record_model_fingerprint.assert_not_called()  # the next start checks again
        self.assertIsNone(stack.model.fingerprint)

    def test_an_empty_version_without_fingerprint_adopts_directly(self):
        configured = FakeEncoder(PROBE_A)
        self.build(configured)
        self.metadata.record_model_fingerprint.assert_called_once()

    def test_a_given_version_is_built_without_reading_the_current_one(self):
        version = ActiveModel(id=5, name="dino", version="v5", collection="c5", dim=4, distance="cosine",
                              fingerprint=fingerprint(PROBE_B))
        configured = FakeEncoder(PROBE_B)
        stack = self.build(configured, model=version)
        self.assertEqual(stack.model.collection, "c5")
        self.metadata.get_current_embedding_model.assert_not_called()
        kwargs = rs.QdrantStore.call_args.kwargs
        self.assertEqual(kwargs["collection"], "c5")


if __name__ == "__main__":
    unittest.main()
