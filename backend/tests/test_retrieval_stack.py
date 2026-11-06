"""Composition root (bootstrap.retrieval_stack) with every adapter faked."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from app.bootstrap import retrieval_stack as rs
from app.bootstrap.state import ApplicationState
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.errors import RetrievalError

ROW = {
    "id": 7,
    "name": "dino",
    "version": "v2",
    "collection": "c_dino_v2",
    "dim": 1024,
    "vector_dim": 1024,
    "distance": "cosine",
    "input_size": 336,
    "weights_uri": "s3://w",
    "preprocess": {},
}

SETTINGS = SimpleNamespace(
    postgres=SimpleNamespace(
        dsn=lambda: "postgresql+psycopg://u:p@h/db", host="h", port=5432, user="u", password="p", database="db"
    ),
    qdrant=SimpleNamespace(url="http://q:6333", host="q", port=6333, collection="", prefer_grpc=True),
    s3=SimpleNamespace(),
    paths=SimpleNamespace(save_images_dir="save_images"),
)


class ActiveModelTests(unittest.TestCase):
    def test_from_row_and_space_id(self):
        m = ActiveModel.from_row(ROW)
        self.assertEqual((m.id, m.collection, m.dim, m.distance), (7, "c_dino_v2", 1024, "cosine"))
        self.assertEqual(m.space_id, "dino:v2")

    def test_bad_row_is_not_ready(self):
        with self.assertRaises(RetrievalError) as ctx:
            ActiveModel.from_row({"id": 1})
        self.assertEqual(ctx.exception.code, "NOT_READY")

    def test_encoder_dim_must_match(self):
        m = ActiveModel.from_row(ROW)
        m.check_encoder_dim(1024)
        with self.assertRaises(RetrievalError) as ctx:
            m.check_encoder_dim(768)
        self.assertEqual(ctx.exception.code, "VECTOR_DIM_MISMATCH")
        self.assertIn("dino:v2", ctx.exception.detail)


class BuildStackTests(unittest.TestCase):
    def setUp(self):
        self.metadata = MagicMock()
        self.metadata.get_current_embedding_model.return_value = dict(ROW)
        self.vectors = MagicMock()
        self.qstore = MagicMock()
        self.storage = MagicMock()
        self.embedder = MagicMock()
        self.embedder.probe_dim.return_value = 1024
        self.embedder_cls = Mock(return_value=self.embedder)
        for name, value in (
            ("PgMetadataStore", Mock(return_value=self.metadata)),
            ("QdrantStore", Mock(return_value=self.qstore)),
            ("QdrantVectorStore", Mock(return_value=self.vectors)),
            ("build_object_storage", Mock(return_value=self.storage)),
            ("Embedder", self.embedder_cls),
        ):
            p = patch.object(rs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_stack_wires_the_active_model_into_collection_and_use_cases(self):
        stack = rs.build_retrieval_stack({"QDRANT_CONFIG": {"api_key": "k", "url": "http://ignored:1"}}, SETTINGS)
        self.assertEqual(stack.model.collection, "c_dino_v2")
        args, kwargs = rs.QdrantStore.call_args
        self.assertEqual((kwargs["collection"], kwargs["vector_dim"], kwargs["distance"]), ("c_dino_v2", 1024, "cosine"))
        connection = args[0]
        self.assertEqual((connection.url, connection.api_key, connection.prefer_grpc), ("http://q:6333", "k", True))
        rs.QdrantVectorStore.assert_called_once_with(self.qstore)
        self.vectors.ensure_collection.assert_called_once_with(recreate=False)
        self.assertIs(stack.index_uc._model, stack.model)
        self.assertIs(stack.search_uc._model, stack.model)
        self.assertIs(stack.index_uc._vectors, self.vectors)  # no hidden decorator
        self.embedder.probe_dim.assert_called_once()

    def test_shared_embedder_is_used_instead_of_loading_a_second_model(self):
        shared = MagicMock()
        shared.probe_dim.return_value = 1024
        stack = rs.build_retrieval_stack({}, SETTINGS, embedder=shared)
        self.assertIs(stack.embedding, shared)
        self.assertIs(stack.index_uc._embedding, shared)
        self.assertIs(stack.search_uc._embedding, shared)
        self.embedder_cls.assert_not_called()

    def test_encoder_for_another_model_fails_startup_and_releases_connections(self):
        self.embedder.probe_dim.return_value = 768
        with self.assertRaises(RetrievalError) as ctx:
            rs.build_retrieval_stack({}, SETTINGS)
        self.assertEqual(ctx.exception.code, "VECTOR_DIM_MISMATCH")
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()

    def test_infra_failure_closes_what_was_opened(self):
        self.vectors.ensure_collection.side_effect = RetrievalError("NOT_READY", "qdrant down", http_status=503)
        with self.assertRaises(RetrievalError):
            rs.build_retrieval_infra(SETTINGS, {})
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()

    def test_no_current_model_closes_pg(self):
        self.metadata.get_current_embedding_model.side_effect = RetrievalError("NOT_READY", "no current", http_status=503)
        with self.assertRaises(RetrievalError):
            rs.build_retrieval_infra(SETTINGS, {})
        self.metadata.close.assert_called_once()
        rs.QdrantVectorStore.assert_not_called()

    def test_infra_alone_never_loads_the_onnx_encoder(self):
        infra = rs.build_retrieval_infra(SETTINGS, {})
        self.assertEqual(infra.model.id, 7)
        self.embedder_cls.assert_not_called()

    def test_ping_and_close(self):
        self.metadata.ping.return_value = {"ok": True}
        self.vectors.ping.return_value = {"ok": False, "detail": "down"}
        self.storage.ping.return_value = {"ok": True}
        stack = rs.build_retrieval_stack({}, SETTINGS)
        self.assertEqual(set(stack.infra.ping()), {"postgres", "qdrant", "storage"})
        self.vectors.close.side_effect = RuntimeError("boom")  # one failure must not skip PG
        stack.close()
        self.metadata.close.assert_called_once()

    def test_application_shutdown_closes_the_stack(self):
        stack = rs.build_retrieval_stack({}, SETTINGS)
        http_client = MagicMock()
        http_client.aclose = Mock(return_value=asyncio.sleep(0))
        runtime = ApplicationState(
            config={}, config_info={}, http_client=http_client, settings=SimpleNamespace(), retrieval=stack
        )
        asyncio.run(runtime.close())
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()
        self.assertIsNone(runtime.retrieval)


if __name__ == "__main__":
    unittest.main()
