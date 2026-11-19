"""所有适配器都是假的组合根（bootstrap.retrieval_stack）测试。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy.engine import make_url

from app.bootstrap import lifespan, retrieval_stack as rs
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

# 生效配置（Nacos 叠加在本地 YAML 上）：连接参数的唯一来源
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
        self.assertIs(stack.index_uc._vectors, self.vectors)  # 没有隐藏的装饰器
        self.assertIs(stack.scene_sync_uc._vectors, self.vectors)
        self.assertIs(stack.scene_sync_uc._model, stack.model)  # payload 写到当前 collection
        self.embedder.probe_dim.assert_called_once()

    def test_a_borrowed_store_is_used_but_neither_opened_nor_closed(self):
        borrowed = MagicMock()
        borrowed.get_current_embedding_model.return_value = dict(ROW)
        stack = rs.build_retrieval_stack(CONFIG, SETTINGS, metadata=borrowed)
        rs.PgMetadataStore.assert_not_called()  # 没有第二个连接池
        self.assertIs(stack.recompute_scenes_uc._metadata, borrowed)
        stack.close()
        self.vectors.close.assert_called_once()
        borrowed.close.assert_not_called()  # 由目录持有
        # ……构建中途失败时也是
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
        url = make_url(kwargs["url"].render_as_string(hide_password=False))  # 往返一次后保持不变
        self.assertEqual(
            (url.drivername, url.host, url.port, url.username, url.password, url.database),
            ("postgresql+psycopg", "pg.internal", 5432, "u", "p@ss/w:rd", "meta"),
        )
        self.assertEqual(kwargs["connect_timeout"], 5.0)
        # 没有 IMAGE_STORAGE_CONFIG：使用 mock 驱动和常量默认值
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

    def test_postgres_connection_failure_logs_only_its_config_and_preserves_error(self):
        failure = AppError("INDEX_POSTGRES_FAILED", "connection timeout expired", http_status=502)
        self.metadata.ensure_schema.side_effect = failure
        with self.assertLogs("retrieval", level="ERROR") as logs, self.assertRaises(AppError) as ctx:
            rs.build_retrieval_infra(CONFIG)
        message = "\n".join(logs.output)
        self.assertIs(ctx.exception, failure)
        for value in ("PostgreSQL", "message=", "connection timeout expired", "POSTGRES_CONFIG", "pg.internal", '"port": 5432'):
            self.assertIn(value, message)
        self.assertNotIn("QDRANT_CONFIG", message)
        self.assertNotIn(CONFIG["POSTGRES_CONFIG"]["password"], message)
        self.metadata.close.assert_called_once()
        rs.QdrantStore.assert_not_called()

    def test_qdrant_connection_failure_logs_endpoint_and_collection(self):
        failure = AppError("INDEX_QDRANT_FAILED", "connection refused", http_status=502)
        self.vectors.collection_exists.side_effect = failure
        with self.assertLogs("retrieval", level="ERROR") as logs, self.assertRaises(AppError) as ctx:
            rs.build_retrieval_infra(CONFIG)
        message = "\n".join(logs.output)
        self.assertIs(ctx.exception, failure)
        for value in ("Qdrant", "message=", "connection refused", "QDRANT_CONFIG", "http://q:6333", "c_dino_v2", '"dim": 1024'):
            self.assertIn(value, message)
        self.assertNotIn("POSTGRES_CONFIG", message)
        self.assertIn('"api_key": "***"', message)
        self.vectors.close.assert_called_once()
        self.metadata.close.assert_called_once()

    def test_qdrant_collection_mismatch_logs_its_message_and_config(self):
        self.vectors.ensure_collection.side_effect = AppError("VECTOR_DIM_MISMATCH", "size=768 expected 1024", http_status=503)
        with self.assertLogs("retrieval", level="ERROR") as logs, self.assertRaises(AppError):
            rs.build_retrieval_infra(CONFIG)
        self.assertIn("size=768 expected 1024", logs.output[0])
        self.assertIn("QDRANT_CONFIG", logs.output[0])

    def test_qdrant_invalid_timeout_logs_supplied_fields_before_opening_connections(self):
        config = {**CONFIG, "QDRANT_CONFIG": {"url": "http://q:6333", "timeout": "bad-timeout"}}
        with self.assertLogs("retrieval", level="ERROR") as logs, self.assertRaises(ValueError):
            rs.build_retrieval_infra(config)
        self.assertIn("QDRANT_CONFIG", logs.output[0])
        self.assertIn('"timeout": "bad-timeout"', logs.output[0])
        rs.PgMetadataStore.assert_not_called()

    def test_retrieval_startup_masks_credentials_and_keeps_the_existing_error_code(self):
        config = {**CONFIG, "QDRANT_CONFIG": {"url": "http://q:6333", "api_key": "private-api-key"}}
        runtime = SimpleNamespace(config=config, settings=SETTINGS, catalog=None)
        self.vectors.collection_exists.side_effect = AppError(
            "INDEX_QDRANT_FAILED", "connection refused api_key=private-api-key", http_status=502,
        )
        with self.assertLogs(level="WARNING") as logs:
            lifespan.initialize_retrieval(runtime, self.embedder)
        message = "\n".join(logs.output)
        self.assertIn("QDRANT_CONFIG", message)
        self.assertIn("connection refused", message)
        self.assertNotIn("private-api-key", message)
        self.assertIsNone(runtime.retrieval)
        self.assertEqual(runtime.retrieval_error_code, "INDEX_QDRANT_FAILED")

    def test_missing_configs_are_logged_before_any_connections_are_opened(self):
        for section in ("POSTGRES_CONFIG", "QDRANT_CONFIG"):
            config = {key: value for key, value in CONFIG.items() if key != section}
            with self.subTest(section=section), self.assertLogs("retrieval", level="ERROR") as logs:
                with self.assertRaises(ValueError):
                    rs.build_retrieval_infra(config)
            self.assertIn(section, logs.output[0])
            self.assertIn('"status": "missing"', logs.output[0])
        rs.PgMetadataStore.assert_not_called()

    def test_no_active_model_is_not_logged_as_a_database_connection_failure(self):
        self.metadata.get_current_embedding_model.side_effect = AppError("NOT_READY", "no current", http_status=503)
        with patch.object(rs.logger, "error") as log, self.assertRaises(AppError):
            rs.build_retrieval_infra(CONFIG)
        log.assert_not_called()

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
        self.vectors.close.side_effect = RuntimeError("boom")  # 一个失败不能跳过 PG
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
