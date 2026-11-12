"""Unit checks for IMAGE_STORAGE_CONFIG.key_prefix (implementation only, not acceptance)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.clients.image_storage import (
    MockImageStorage,
    S3ImageStorage,
    _key_from_url_or_key,
)
from app.core.config.key_prefix import KEY_PREFIX_MAX_LEN, normalize_key_prefix
from app.core.config.settings import ImageStorageSettings
from app.services.retrieval.storage_keys import (
    apply_key_prefix,
    original_key,
    public_url,
    staging_key,
    thumb_key,
)

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
BASE = "https://cdn.example.example"


class NormalizeKeyPrefixTests(unittest.TestCase):
    def test_omit_null_blank_and_slash_only_are_empty(self):
        for raw in (None, "", "   ", "/", "//", " /// "):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_key_prefix(raw), "")

    def test_strips_slashes_collapses_and_preserves_case(self):
        cases = {
            "ImageEmbedding": "ImageEmbedding",
            "ImageEmbedding/": "ImageEmbedding",
            "/ImageEmbedding": "ImageEmbedding",
            " /ImageEmbedding/ ": "ImageEmbedding",
            "ImageEmbedding//foo": "ImageEmbedding/foo",
            "ImageEmbedding\\bar": "ImageEmbedding/bar",
            "foo／bar": "foo/bar",
            "foo＼bar": "foo/bar",
            "a/b/c": "a/b/c",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_key_prefix(raw), expected)
        self.assertEqual(normalize_key_prefix("imageembedding"), "imageembedding")
        self.assertNotEqual(normalize_key_prefix("ImageEmbedding"), "imageembedding")

    def test_illegal_values_raise_without_repair(self):
        illegal = [
            "foo/../bar",
            "../x",
            "foo/./bar",
            "original",
            "thumb",
            "staging",
            "foo/original",
            "Image Embedding",
            "a" * (KEY_PREFIX_MAX_LEN + 1),
            123,
            True,
            ["ImageEmbedding"],
            {"k": "ImageEmbedding"},
        ]
        for raw in illegal:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as ctx:
                    normalize_key_prefix(raw)
                self.assertIn("IMAGE_STORAGE_CONFIG.key_prefix", str(ctx.exception))
        self.assertEqual(len(normalize_key_prefix("a" * KEY_PREFIX_MAX_LEN)), KEY_PREFIX_MAX_LEN)

    def test_reserved_segments_are_exact_case(self):
        self.assertEqual(normalize_key_prefix("Original"), "Original")
        with self.assertRaises(ValueError):
            normalize_key_prefix("original")


class StorageKeyCompositionTests(unittest.TestCase):
    def test_empty_prefix_keeps_current_relative_keys(self):
        self.assertEqual(original_key(IMAGE, "jpg"), f"original/{IMAGE}.jpg")
        self.assertEqual(thumb_key(IMAGE), f"thumb/{IMAGE}.webp")
        self.assertEqual(staging_key(IMAGE, "png"), f"staging/{IMAGE}.png")
        self.assertEqual(apply_key_prefix("", f"original/{IMAGE}.jpg"), f"original/{IMAGE}.jpg")
        self.assertEqual(apply_key_prefix("", f"original/{IMAGE}.jpg"), original_key(IMAGE, "jpg"))

    def test_non_empty_prefix_is_prepended_once(self):
        prefix = "ImageEmbedding"
        self.assertEqual(
            original_key(IMAGE, "jpg", key_prefix=prefix),
            f"ImageEmbedding/original/{IMAGE}.jpg",
        )
        self.assertEqual(
            thumb_key(IMAGE, key_prefix=prefix),
            f"ImageEmbedding/thumb/{IMAGE}.webp",
        )
        self.assertEqual(
            staging_key(IMAGE, "jpg", key_prefix=prefix),
            f"ImageEmbedding/staging/{IMAGE}.jpg",
        )
        self.assertEqual(
            apply_key_prefix("a/b", f"original/{IMAGE}.jpg"),
            f"a/b/original/{IMAGE}.jpg",
        )

    def test_public_url_does_not_reappend_the_prefix(self):
        key = original_key(IMAGE, "jpg", key_prefix="ImageEmbedding")
        self.assertEqual(
            public_url(BASE, key),
            f"{BASE}/ImageEmbedding/original/{IMAGE}.jpg",
        )
        self.assertEqual(
            public_url(BASE, original_key(IMAGE, "jpg")),
            f"{BASE}/original/{IMAGE}.jpg",
        )
        # public_base_url already ending in the same path + key_prefix would double;
        # the implementation must not itself also fold key_prefix into the base.
        doubled = public_url(f"{BASE}/ImageEmbedding", key)
        self.assertEqual(doubled, f"{BASE}/ImageEmbedding/ImageEmbedding/original/{IMAGE}.jpg")


class ImageStorageSettingsKeyPrefixTests(unittest.TestCase):
    def test_omitted_null_and_empty_store_empty_string(self):
        self.assertEqual(ImageStorageSettings.from_config({}).key_prefix, "")
        self.assertEqual(ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {}}).key_prefix, "")
        self.assertEqual(
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"key_prefix": None}}).key_prefix,
            "",
        )
        self.assertEqual(
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"key_prefix": ""}}).key_prefix,
            "",
        )
        self.assertEqual(
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"key_prefix": " /// "}}).key_prefix,
            "",
        )
        self.assertEqual(ImageStorageSettings().key_prefix, "")
        self.assertNotEqual(ImageStorageSettings().key_prefix, "ImageEmbedding")

    def test_from_config_normalizes_and_rejects_illegal_values(self):
        settings = ImageStorageSettings.from_config(
            {"IMAGE_STORAGE_CONFIG": {"key_prefix": " /ImageEmbedding//foo/ "}}
        )
        self.assertEqual(settings.key_prefix, "ImageEmbedding/foo")
        with self.assertRaises(ValueError) as ctx:
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"key_prefix": "foo/../bar"}})
        self.assertIn("IMAGE_STORAGE_CONFIG.key_prefix", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            ImageStorageSettings.from_config({"IMAGE_STORAGE_CONFIG": {"key_prefix": 1}})
        self.assertIn("IMAGE_STORAGE_CONFIG.key_prefix", str(ctx.exception))
        self.assertIn("字符串", str(ctx.exception))


class StoragePutAndLegacyUrlTests(unittest.TestCase):
    def test_mock_and_s3_puts_share_prefixed_keys(self):
        settings = ImageStorageSettings(public_base_url=BASE, key_prefix="ImageEmbedding")
        self.assertEqual(settings.key_prefix, "ImageEmbedding")
        mock = MockImageStorage(settings)
        url = mock.put_original(IMAGE, b"data", ext="jpg")
        key = f"ImageEmbedding/original/{IMAGE}.jpg"
        self.assertEqual(url, f"{BASE}/{key}")
        self.assertIn(key, mock._objects)
        thumb = mock.put_thumb(IMAGE, b"thumb")
        self.assertEqual(thumb, f"{BASE}/ImageEmbedding/thumb/{IMAGE}.webp")
        staged = mock.put_staging(IMAGE, b"stg", ext="png")
        self.assertEqual(staged, f"{BASE}/ImageEmbedding/staging/{IMAGE}.png")

        s3 = S3ImageStorage.__new__(S3ImageStorage)
        s3.settings = settings
        s3.public_base_url = BASE
        s3.bucket = "example-bucket"
        s3._client = Mock()
        s3_url = S3ImageStorage.put_original(s3, IMAGE, b"data", ext="jpg", content_type="image/jpeg")
        self.assertEqual(s3_url, url)
        s3._client.put_object.assert_called_once_with(
            Bucket="example-bucket",
            Key=key,
            Body=b"data",
            ContentType="image/jpeg",
        )

    def test_read_and_delete_use_the_stored_url_not_the_current_prefix(self):
        settings = ImageStorageSettings(public_base_url=BASE, key_prefix="ImageEmbedding")
        storage = MockImageStorage(settings)
        old_key = f"original/{IMAGE}.jpg"
        old_url = f"{BASE}/{old_key}"
        storage._objects[old_key] = b"legacy-bytes"
        self.assertEqual(storage.read_by_url_or_key(old_url), b"legacy-bytes")
        self.assertEqual(_key_from_url_or_key(old_url, BASE), old_key)
        storage.delete_by_url_or_key(old_url)
        self.assertNotIn(old_key, storage._objects)
        new_url = storage.put_original(IMAGE, b"new-bytes", ext="jpg")
        self.assertEqual(new_url, f"{BASE}/ImageEmbedding/original/{IMAGE}.jpg")
        self.assertEqual(_key_from_url_or_key(new_url, BASE), f"ImageEmbedding/original/{IMAGE}.jpg")
        self.assertEqual(storage.read_by_url_or_key(new_url), b"new-bytes")
        # Re-reading the legacy URL must not be rewritten onto the current prefix.
        self.assertEqual(_key_from_url_or_key(old_url, BASE), old_key)


if __name__ == "__main__":
    unittest.main()
