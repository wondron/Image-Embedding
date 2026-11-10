"""Composition root (bootstrap.retrieval_stack) with every adapter faked."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy.engine import make_url

from app.bootstrap import retrieval_stack as rs
from app.bootstrap.state import ApplicationState
from app.core.config.settings import ImageStorageSettings
from app.services.retrieval.active_model import ActiveModel
from app.core.errors import AppError

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

SETTINGS = SimpleNamespace(paths=SimpleNamespace(save_images_dir="save_images"))

# Effective config (Nacos over local YAML): the only source of connection parameters
CONFIG = {
    "POSTGRES_CONFIG": {"host": "pg.internal", "user": "u", "password": "p@ss/w:rd", "database": "meta"},
    "QDRANT_CONFIG": {"url": "http://q:6333", "api_key": "k"},
}


class ActiveModelTests(unittest.TestCase):
    def test_from_row_and_space_id(self):
        m = ActiveModel.from_row(ROW)
        self.assertEqual((m.id, m.collection, m.dim, m.distance), (7, "c_dino_v2", 1024, "cosine"))
        self.assertEqual(m.space_id, "dino:v2")

    def test_bad_row_is_not_ready(self):
        with self.assertRaises(AppError) as ctx:
            ActiveModel.from_row({"id": 1})
        self.assertEqual(ctx.exception.code, "NOT_READY")

    def test_encoder_dim_must_match(self):
        m = ActiveModel.from_row(ROW)
        m.check_encoder_dim(1024)
        with self.assertRaises(AppError) as ctx:
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
            ("build_image_storage", Mock(return_value=self.storage)),
            ("Embedder", self.embedder_cls),
        ):
            p = patch.object(rs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_stack_wires_the_active_model_into_collection_and_use_cases(self):
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS)
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
        self.assertIs(stack.scene_sync_uc._vectors, self.vectors)
        self.assertIs(stack.scene_sync_uc._model, stack.model)  # payloads go to the current collection
        self.embedder.probe_dim.assert_called_once()

    def test_a_borrowed_store_is_used_but_neither_opened_nor_closed(self):
        borrowed = MagicMock()
        borrowed.get_current_embedding_model.return_value = dict(ROW)
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS, metadata=borrowed)
        rs.PgMetadataStore.assert_not_called()  # no second pool
        self.assertIs(stack.list_uc._metadata, borrowed)
        stack.close()
        self.vectors.close.assert_called_once()
        borrowed.close.assert_not_called()  # the catalog owns it
        # ... also when building fails half way
        self.vectors.ensure_collection.side_effect = AppError("NOT_READY", "qdrant down", http_status=503)
        with self.assertRaises(AppError):
            rs.build_retrieval_infra(CONFIG, metadata=borrowed)
        borrowed.close.assert_not_called()

    def test_a_borrowed_store_needs_no_postgres_config(self):
        borrowed = MagicMock()
        borrowed.get_current_embedding_model.return_value = dict(ROW)
        infra = rs.build_retrieval_infra({"QDRANT_CONFIG": CONFIG["QDRANT_CONFIG"]}, metadata=borrowed)
        self.assertIs(infra.metadata, borrowed)
        self.assertFalse(infra.owns_metadata)

    def test_connections_come_from_the_effective_config(self):
        rs.build_retrieval_infra(CONFIG)
        kwargs = rs.PgMetadataStore.call_args.kwargs
        url = make_url(kwargs["url"].render_as_string(hide_password=False))  # survives a round trip
        self.assertEqual(
            (url.drivername, url.host, url.port, url.username, url.password, url.database),
            ("postgresql+psycopg", "pg.internal", 5432, "u", "p@ss/w:rd", "meta"),
        )
        self.assertEqual(kwargs["connect_timeout"], 5.0)
        # No IMAGE_STORAGE_CONFIG: the mock driver with the constant defaults
        rs.build_image_storage.assert_called_once_with(ImageStorageSettings())

    def test_missing_connection_config_fails_before_opening_anything(self):
        broken = {
            "no postgres": {"QDRANT_CONFIG": CONFIG["QDRANT_CONFIG"]},
            "no qdrant endpoint": {"POSTGRES_CONFIG": CONFIG["POSTGRES_CONFIG"], "QDRANT_CONFIG": {"api_key": "k"}},
        }
        for case, config in broken.items():
            with self.subTest(case), self.assertRaises(ValueError) as ctx:
                rs.build_retrieval_infra(config)
            self.assertIn("missing from the configuration", str(ctx.exception))
        rs.PgMetadataStore.assert_not_called()

    def test_shared_embedder_is_used_instead_of_loading_a_second_model(self):
        shared = MagicMock()
        shared.probe_dim.return_value = 1024
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS, embedder=shared)
        self.assertIs(stack.embedding, shared)
        self.assertIs(stack.index_uc._embedding, shared)
        self.assertIs(stack.search_uc._embedding, shared)
        self.embedder_cls.assert_not_called()

    def test_encoder_for_another_model_fails_startup_and_releases_connections(self):
        self.embedder.probe_dim.return_value = 768
        with self.assertRaises(AppError) as ctx:
            rs.build_retrieval_stack(CONFIG, SETTINGS)
        self.assertEqual(ctx.exception.code, "VECTOR_DIM_MISMATCH")
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()

    def test_infra_failure_closes_what_was_opened(self):
        self.vectors.ensure_collection.side_effect = AppError("NOT_READY", "qdrant down", http_status=503)
        with self.assertRaises(AppError):
            rs.build_retrieval_infra(CONFIG)
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()

    def test_no_current_model_closes_pg(self):
        self.metadata.get_current_embedding_model.side_effect = AppError("NOT_READY", "no current", http_status=503)
        with self.assertRaises(AppError):
            rs.build_retrieval_infra(CONFIG)
        self.metadata.close.assert_called_once()
        rs.QdrantVectorStore.assert_not_called()

    def test_infra_alone_never_loads_the_onnx_encoder(self):
        infra = rs.build_retrieval_infra(CONFIG)
        self.assertEqual(infra.model.id, 7)
        self.embedder_cls.assert_not_called()

    def test_ping_and_close(self):
        self.metadata.ping.return_value = {"ok": True}
        self.vectors.ping.return_value = {"ok": False, "detail": "down"}
        self.storage.ping.return_value = {"ok": True}
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS)
        self.assertEqual(set(stack.infra.ping()), {"postgres", "qdrant", "storage"})
        self.vectors.close.side_effect = RuntimeError("boom")  # one failure must not skip PG
        stack.close()
        self.metadata.close.assert_called_once()

    def test_application_shutdown_closes_the_stack(self):
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS)
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
