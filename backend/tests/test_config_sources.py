"""Where parameters come from: constant.py and the effective config (Nacos over the local YAML).

No database, no Nacos: connection sections are parsed from plain dicts, and
alembic/env.py is exercised in offline (--sql) mode.
"""

import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.engine import make_url

from app.core.config.settings import ApplicationSettings, ImageStorageSettings, PostgresSettings, config_flag
from app.core.errors import AppError
from app.repositories.metadata import PgMetadataStore  # importing app.db.session first hits an import cycle

BACKEND = Path(__file__).resolve().parents[1]
PG = {"host": "pg.internal", "user": "image_emb", "database": "meta"}


class PostgresSettingsTests(unittest.TestCase):
    def test_missing_required_keys_are_reported_together(self):
        for config in ({}, {"POSTGRES_CONFIG": {}}, {"POSTGRES_CONFIG": {"host": " ", "port": 5432}}):
            with self.subTest(config=config), self.assertRaises(ValueError) as ctx:
                PostgresSettings.from_config(config)
            for key in ("POSTGRES_CONFIG.host", "POSTGRES_CONFIG.user", "POSTGRES_CONFIG.database"):
                self.assertIn(key, str(ctx.exception))

    def test_yaml_scalars_are_coerced_and_defaults_come_from_constants(self):
        pg = PostgresSettings.from_config({"POSTGRES_CONFIG": {**PG, "password": 123456, "port": "6543"}})
        self.assertEqual((pg.password, pg.port, pg.connect_timeout), ("123456", 6543, 5.0))
        pg = PostgresSettings.from_config({"POSTGRES_CONFIG": PG})
        self.assertEqual((pg.password, pg.port), ("", 5432))

    def test_bad_number_names_the_key(self):
        with self.assertRaises(ValueError) as ctx:
            PostgresSettings.from_config({"POSTGRES_CONFIG": {**PG, "port": "54x2"}})
        self.assertIn("POSTGRES_CONFIG.port", str(ctx.exception))

    def test_url_escapes_credentials_and_nothing_prints_the_password(self):
        pg = PostgresSettings.from_config({"POSTGRES_CONFIG": {**PG, "password": "p@ss/w:rd#1"}})
        url = make_url(pg.url().render_as_string(hide_password=False))
        self.assertEqual(
            (url.drivername, url.host, url.port, url.username, url.password, url.database),
            ("postgresql+psycopg", "pg.internal", 5432, "image_emb", "p@ss/w:rd#1", "meta"),
        )
        self.assertNotIn("p@ss", repr(pg))
        self.assertNotIn("p@ss", str(pg.url()))


class PgConnectionTests(unittest.TestCase):
    def test_reported_host_and_database_are_those_of_the_url(self):
        store = PgMetadataStore(url="postgresql+psycopg://u:p@db.example:6543/metadata")
        self.assertEqual((store.host, store.database), ("db.example", "metadata"))

        pg = PostgresSettings.from_config({"POSTGRES_CONFIG": {**PG, "password": "p@ss"}})
        store = PgMetadataStore(url=pg.url(), connect_timeout=pg.connect_timeout)
        self.assertEqual((store.host, store.database, store.url.password), ("pg.internal", "meta", "p@ss"))


class ImageStorageSettingsTests(unittest.TestCase):
    def test_missing_section_is_the_mock_driver_with_constant_defaults(self):
        storage = ImageStorageSettings.from_config({})
        self.assertEqual(storage, ImageStorageSettings())
        self.assertFalse(storage.use_boto3)
        self.assertEqual(storage.bucket, "image-embedding")

    def test_values_flags_and_secrets(self):
        storage = ImageStorageSettings.from_config(
            {
                "IMAGE_STORAGE_CONFIG": {
                    "use_boto3": "true",
                    "bucket": "prod-images",
                    "public_base_url": "https://cdn.example/img/",
                    "access_key_id": "ak-123",
                    "secret_access_key": "secret-xyz",
                }
            }
        )
        self.assertEqual(
            (storage.use_boto3, storage.bucket, storage.public_base_url, storage.secret_access_key),
            (True, "prod-images", "https://cdn.example/img", "secret-xyz"),
        )
        self.assertNotIn("secret-xyz", repr(storage))
        with self.assertRaises(ValueError) as ctx:
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"use_boto3": "maybe"}})
        self.assertIn("IMAGE_STORAGE_CONFIG.use_boto3", str(ctx.exception))

    @unittest.skipUnless(importlib.util.find_spec("boto3"), "boto3 not installed")
    def test_s3_driver_takes_its_credentials_from_the_config_section(self):
        from app.clients.image_storage import S3ImageStorage

        with self.assertRaises(AppError) as ctx:
            S3ImageStorage(ImageStorageSettings(use_boto3=True))
        self.assertIn("IMAGE_STORAGE_CONFIG.access_key_id", ctx.exception.detail)
        storage = S3ImageStorage(ImageStorageSettings(use_boto3=True, access_key_id="ak", secret_access_key="sk"))
        self.assertEqual(storage.bucket, "image-embedding")


class SwitchAndEnvironmentTests(unittest.TestCase):
    def test_config_flag(self):
        self.assertTrue(config_flag({}, "TOTAL_CONFIG", "retrieval_enabled", True))
        self.assertFalse(config_flag({"TOTAL_CONFIG": {"retrieval_enabled": False}}, "TOTAL_CONFIG", "retrieval_enabled", True))
        self.assertFalse(config_flag({"TOTAL_CONFIG": {"retrieval_enabled": "off"}}, "TOTAL_CONFIG", "retrieval_enabled", True))
        with self.assertRaises(ValueError):
            config_flag({"TOTAL_CONFIG": "yes"}, "TOTAL_CONFIG", "retrieval_enabled", True)

    def test_env_is_the_nacos_profile(self):
        with patch.dict(os.environ, {"NACOS_ENV": " Test "}):
            self.assertEqual(ApplicationSettings().env, "test")
        with patch.dict(os.environ):
            os.environ.pop("NACOS_ENV", None)
            self.assertEqual(ApplicationSettings().env, "prod")


class AlembicEnvTests(unittest.TestCase):
    """alembic/env.py picks the database like the application does."""

    def render(self, **attributes) -> str:
        from alembic import command
        from alembic.config import Config

        buffer = io.StringIO()
        cfg = Config(output_buffer=buffer)  # no ini file, so env.py leaves logging alone
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        cfg.set_main_option("path_separator", "os")
        cfg.attributes.update(attributes)
        command.upgrade(cfg, "base:head", sql=True)
        return buffer.getvalue()

    def test_explicit_database_url_skips_the_config_lookup(self):
        with patch("app.core.config.nacos.load_config_once", side_effect=AssertionError("config must not be loaded")):
            sql = self.render(database_url="postgresql+psycopg://u:p@h/db")
        self.assertIn("CREATE TABLE images", sql)

    def test_default_is_postgres_config_from_the_effective_config(self):
        with patch("app.core.config.nacos.load_config_once", return_value={"POSTGRES_CONFIG": PG}) as load:
            sql = self.render()
        load.assert_called_once()
        self.assertIn("CREATE TABLE images", sql)


if __name__ == "__main__":
    unittest.main()
