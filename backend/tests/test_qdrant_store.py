"""QdrantStore (mechanism) and QdrantVectorStore (retrieval rules) on in-process Qdrant."""

import unittest
import uuid

import numpy as np

from app.clients.qdrant import (
    BadEmbeddingError,
    CollectionMismatchError,
    QdrantConnection,
    QdrantStore,
    VectorDimMismatchError,
)
from app.repositories.vectors import PAYLOAD_INDEXES, QdrantVectorStore
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.errors import RetrievalError
from app.services.retrieval.search_image import SearchImageUseCase

DIM = 4
MEMORY = QdrantConnection(url=":memory:")


def unit(*xs):
    v = np.asarray(xs, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


def new_store(**kw):
    store = QdrantStore(MEMORY, collection=kw.pop("collection", "c"), vector_dim=kw.pop("dim", DIM), **kw)
    store.ensure_collection()
    return store


class QdrantConnectionTests(unittest.TestCase):
    def test_from_mapping_host_port_and_overrides(self):
        conn = QdrantConnection.from_mapping({"host": "q", "port": 6334, "api_key": "k", "timeout": 5})
        self.assertEqual((conn.url, conn.api_key, conn.timeout), ("http://q:6334", "k", 5.0))
        conn = QdrantConnection.from_mapping({"url": "http://a:1/"}, url="http://b:2", prefer_grpc=False)
        self.assertEqual((conn.url, conn.prefer_grpc), ("http://b:2", False))


class QdrantStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.addCleanup(self.store.close)

    def test_upsert_search_count_delete(self):
        a, b = str(uuid.uuid4()), 7  # uuid and unsigned-int ids are both valid Qdrant ids
        self.store.upsert(a, unit(1, 0, 0, 0), {"scenes": ["general", "oven"], "label_status": "auto"})
        self.store.upsert(b, unit(0, 1, 0, 0), {"scenes": ["general"], "label_status": "unlabeled"})
        self.assertEqual(self.store.count(), 2)

        hits = self.store.search(unit(1, 0.1, 0, 0), top_k=5)
        self.assertEqual([h["id"] for h in hits], [a, "7"])  # None threshold: low scores kept
        self.assertEqual(hits[0]["payload"]["label_status"], "auto")

        self.assertEqual([h["id"] for h in self.store.search(unit(1, 0.1, 0, 0), score_threshold=0.5)], [a])
        self.assertEqual([h["id"] for h in self.store.search(unit(0, 1, 0, 0), must={"scenes": "oven"})], [a])
        self.assertEqual(self.store.search(unit(1, 0, 0, 0), must={"label_status": "verified"}), [])

        self.store.delete(a)
        self.assertEqual(self.store.count(), 1)

    def test_rejects_bad_vectors_and_ids(self):
        with self.assertRaises(VectorDimMismatchError):
            self.store.upsert(1, [1.0, 0.0], {})
        with self.assertRaises(BadEmbeddingError):
            self.store.upsert(1, [float("nan"), 0, 0, 1], {})
        with self.assertRaises(BadEmbeddingError):
            self.store.upsert(1, [0, 0, 0, 0], {})
        with self.assertRaises(TypeError):
            self.store.upsert(-1, unit(1, 0, 0, 0), {})
        with self.assertRaises(ValueError):
            self.store.upsert("not-a-uuid", unit(1, 0, 0, 0), {})

    def test_existing_collection_must_match_the_model(self):
        client = self.store.client  # same in-process instance
        with self.assertRaises(VectorDimMismatchError):
            QdrantStore(MEMORY, collection="c", vector_dim=8, client=client).ensure_collection()
        with self.assertRaises(CollectionMismatchError):
            QdrantStore(MEMORY, collection="c", vector_dim=DIM, distance="dot", client=client).ensure_collection()
        # recreate is the explicit way to change shape
        other = QdrantStore(MEMORY, collection="c", vector_dim=8, client=client)
        other.ensure_collection(recreate=True)
        self.assertEqual(other.count(), 0)

    def test_ping(self):
        self.store.upsert(1, unit(1, 0, 0, 0))
        ping = self.store.ping()
        self.assertTrue(ping["ok"])
        self.assertEqual((ping["collection"], ping["points_count"], ping["vector_size"]), ("c", 1, DIM))


class QdrantVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = QdrantStore(MEMORY, collection="c", vector_dim=DIM)
        self.vectors = QdrantVectorStore(self.store)
        self.vectors.ensure_collection()
        self.addCleanup(self.vectors.close)

    def test_points_are_keyed_by_image_uuid(self):
        with self.assertRaises(RetrievalError) as ctx:
            self.vectors.upsert(42, unit(1, 0, 0, 0), {})
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_scene_filter_and_error_codes(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        self.vectors.upsert(a, unit(1, 0, 0, 0), {"scenes": ["general", "oven"], "category_id": 3})
        self.vectors.upsert(b, unit(1, 0.2, 0, 0), {"scenes": ["general"], "category_id": 4})
        self.assertEqual([h["id"] for h in self.vectors.search(unit(1, 0, 0, 0), scene="oven")], [str(a)])
        self.assertEqual([h["id"] for h in self.vectors.search(unit(1, 0, 0, 0), filters={"category_id": 4})], [str(b)])
        with self.assertRaises(RetrievalError) as ctx:
            self.vectors.search([1.0, 0.0])
        self.assertEqual(ctx.exception.code, "SEARCH_QDRANT_FAILED")
        self.vectors.delete(a)
        self.assertEqual(self.store.count(), 1)

    def test_shape_mismatch_is_vector_dim_mismatch(self):
        wrong = QdrantVectorStore(QdrantStore(MEMORY, collection="c", vector_dim=8, client=self.store.client))
        with self.assertRaises(RetrievalError) as ctx:
            wrong.ensure_collection()
        self.assertEqual(ctx.exception.code, "VECTOR_DIM_MISMATCH")

    def test_payload_index_fields_are_the_filter_fields(self):
        self.assertEqual(set(PAYLOAD_INDEXES), {"scenes", "label_status", "category_id"})


class FakeMetadata:
    def __init__(self, ids):
        self.rows = {str(i): {"id": str(i), "category_id": None, "scenes": ["general"]} for i in ids}

    def get_active_by_ids(self, ids, scene=None):
        return {i: self.rows[i] for i in ids if i in self.rows}

    def get_scene_by_code(self, code):
        return {"code": code, "outputs_heating": False}

    def map_category_to_scene_label(self, scene, category_id):
        return None


class ScoreThresholdEndToEndTests(unittest.TestCase):
    """score_threshold policy lives in services only; 0 must disable rejection."""

    def setUp(self):
        self.store = QdrantStore(MEMORY, collection="c", vector_dim=DIM)
        vectors = QdrantVectorStore(self.store)
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        self.near, self.far = uuid.uuid4(), uuid.uuid4()
        vectors.upsert(self.near, unit(1, 0, 0, 0), {"scenes": ["general"]})
        vectors.upsert(self.far, unit(0.2, 1, 0, 0), {"scenes": ["general"]})  # cos ~0.2 to the query
        embedding = type("E", (), {"embed_bytes": lambda self, b: unit(1, 0, 0, 0)})()
        model = ActiveModel(id=1, name="m", version="v", collection="c", dim=DIM, distance="cosine")
        self.uc = SearchImageUseCase(embedding, vectors, FakeMetadata([self.near, self.far]), model=model)

    def ids(self, **kw):
        return {item["id"] for item in self.uc.execute(image_bytes=b"x", **kw)["items"]}

    def test_default_threshold_rejects_dissimilar_hits(self):
        self.assertEqual(self.ids(), {str(self.near)})

    def test_zero_disables_rejection(self):
        self.assertEqual(self.ids(score_threshold=0), {str(self.near), str(self.far)})


if __name__ == "__main__":
    unittest.main()
