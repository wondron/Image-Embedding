"""对象 key 的单元检查：SHA-256 分片目录和 IMAGE_STORAGE_CONFIG.key_prefix
（只针对实现，不是验收）。
"""

from __future__ import annotations

import hashlib
import unittest
from unittest.mock import Mock
from uuid import UUID

from app.clients.image_storage import (
    MockImageStorage,
    S3ImageStorage,
    _key_from_url_or_key,
)
from app.core.config.key_prefix import KEY_PREFIX_MAX_LEN, normalize_key_prefix
from app.core.config.settings import ImageStorageSettings
from app.core.errors import AppError
from app.core.rules.image_ids import uuid_from_sha256
from app.utils.storage_keys import (
    apply_key_prefix,
    different_objects,
    original_key,
    public_url,
    relative_key,
    shard_dir,
    staging_key,
    thumb_key,
)

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
SHARD = "0c"  # IMAGE[:2]
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


class ShardDirectoryTests(unittest.TestCase):
    def test_the_shard_is_the_first_two_hex_characters_of_the_sha256(self):
        for data in (b"", b"one image", bytes(range(256)) * 9):
            sha = hashlib.sha256(data)
            image_id = uuid_from_sha256(sha.digest())
            shard = sha.hexdigest()[:2]
            with self.subTest(shard=shard):
                self.assertEqual(shard_dir(image_id), shard)
                self.assertEqual(original_key(image_id, "png"), f"original/{shard}/{image_id}.png")
                self.assertEqual(thumb_key(image_id), f"thumb/{shard}/{image_id}.webp")

    def test_every_first_byte_has_its_own_lowercase_directory(self):
        shards = {shard_dir(UUID(bytes=bytes([first]) + bytes(15))) for first in range(256)}
        self.assertEqual(shards, {f"{first:02x}" for first in range(256)})
        self.assertEqual(shard_dir(IMAGE.upper()), SHARD)
        self.assertEqual(shard_dir(UUID(IMAGE)), SHARD)
        self.assertEqual(original_key(IMAGE.upper(), ".JPG"), f"original/{SHARD}/{IMAGE}.jpg")

    def test_staging_has_no_shard_and_bad_ids_are_rejected(self):
        self.assertEqual(staging_key(IMAGE, "png"), f"staging/{IMAGE}.png")
        for bad in ("", "   ", "not-a-uuid"):
            with self.subTest(bad=bad), self.assertRaises((AppError, ValueError)):
                original_key(bad, "jpg")


class StorageKeyCompositionTests(unittest.TestCase):
    def test_empty_prefix_keeps_current_relative_keys(self):
        self.assertEqual(original_key(IMAGE, "jpg"), f"original/{SHARD}/{IMAGE}.jpg")
        self.assertEqual(thumb_key(IMAGE), f"thumb/{SHARD}/{IMAGE}.webp")
        self.assertEqual(staging_key(IMAGE, "png"), f"staging/{IMAGE}.png")
        self.assertEqual(apply_key_prefix("", f"original/{IMAGE}.jpg"), f"original/{IMAGE}.jpg")
        self.assertEqual(apply_key_prefix("", f"original/{SHARD}/{IMAGE}.jpg"), original_key(IMAGE, "jpg"))

    def test_non_empty_prefix_is_prepended_once(self):
        prefix = "ImageEmbedding"
        self.assertEqual(
            original_key(IMAGE, "jpg", key_prefix=prefix),
            f"ImageEmbedding/original/{SHARD}/{IMAGE}.jpg",
        )
        self.assertEqual(
            thumb_key(IMAGE, key_prefix=prefix),
            f"ImageEmbedding/thumb/{SHARD}/{IMAGE}.webp",
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
            f"{BASE}/ImageEmbedding/original/{SHARD}/{IMAGE}.jpg",
        )
        self.assertEqual(
            public_url(BASE, original_key(IMAGE, "jpg")),
            f"{BASE}/original/{SHARD}/{IMAGE}.jpg",
        )
        # 如果 public_base_url 已经以相同的路径 + key_prefix 结尾，会重复两次；
        # 实现本身不能再把 key_prefix 也拼进基础 URL。
        doubled = public_url(f"{BASE}/ImageEmbedding", key)
        self.assertEqual(doubled, f"{BASE}/ImageEmbedding/ImageEmbedding/original/{SHARD}/{IMAGE}.jpg")


class RelativeKeyTests(unittest.TestCase):
    def test_the_key_from_its_directory_on_whatever_the_url_root_and_prefix(self):
        sharded = f"original/{SHARD}/{IMAGE}.jpg"
        for stored in (
            sharded,
            f"/{sharded}",
            f"{BASE}/{sharded}",
            f"{BASE}/ImageEmbedding/foo/{sharded}",
            f"{BASE}/{sharded}?versionId=3#x",
            f"{BASE}/original/{SHARD}/{IMAGE}%2Ejpg",
            f"https://cdn.example/thumb/{sharded}",  # 包含布局目录名的 URL 根
        ):
            with self.subTest(stored=stored):
                self.assertEqual(relative_key(stored), sharded)
        self.assertEqual(relative_key(f"{BASE}/thumb/{IMAGE}.webp"), f"thumb/{IMAGE}.webp")  # 扁平，在分片之前
        for unknown in (None, "", "   ", f"{BASE}/o/{IMAGE}.jpg", f"{BASE}/original/", "original", "https://cdn/Original/a.jpg"):
            with self.subTest(unknown=unknown):
                self.assertIsNone(relative_key(unknown))

    def test_only_different_relative_keys_are_certainly_different_objects(self):
        sharded = f"{BASE}/original/{SHARD}/{IMAGE}.jpg"
        self.assertTrue(different_objects(sharded, f"{BASE}/original/{IMAGE}.jpg"))
        self.assertTrue(different_objects(f"{BASE}/thumb/{SHARD}/{IMAGE}.webp", f"{BASE}/thumb/{IMAGE}.webp"))
        self.assertTrue(different_objects(sharded, f"{BASE}/thumb/{SHARD}/{IMAGE}.webp"))
        # 另一个 URL 根下的同一个 key，或者没有布局目录的 URL 形式：可能是同一个
        self.assertFalse(different_objects(sharded, f"https://old-cdn.example/bucket/original/{SHARD}/{IMAGE}.jpg"))
        self.assertFalse(different_objects(sharded, f"{BASE}/ImageEmbedding/original/{SHARD}/{IMAGE}.jpg"))
        self.assertFalse(different_objects(sharded, sharded))
        self.assertFalse(different_objects(sharded, "https://cdn/o.jpg"))
        self.assertFalse(different_objects(sharded, None))
        self.assertFalse(different_objects(None, None))


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
        key = f"ImageEmbedding/original/{SHARD}/{IMAGE}.jpg"
        self.assertEqual(url, f"{BASE}/{key}")
        self.assertIn(key, mock._objects)
        thumb = mock.put_thumb(IMAGE, b"thumb")
        self.assertEqual(thumb, f"{BASE}/ImageEmbedding/thumb/{SHARD}/{IMAGE}.webp")
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
        old_key = f"original/{IMAGE}.jpg"  # 在 key_prefix 和分片目录之前存的
        old_url = f"{BASE}/{old_key}"
        storage._objects[old_key] = b"legacy-bytes"
        self.assertEqual(storage.read_by_url_or_key(old_url), b"legacy-bytes")
        self.assertEqual(_key_from_url_or_key(old_url, BASE), old_key)
        storage.delete_by_url_or_key(old_url)
        self.assertNotIn(old_key, storage._objects)
        new_url = storage.put_original(IMAGE, b"new-bytes", ext="jpg")
        self.assertEqual(new_url, f"{BASE}/ImageEmbedding/original/{SHARD}/{IMAGE}.jpg")
        self.assertEqual(_key_from_url_or_key(new_url, BASE), f"ImageEmbedding/original/{SHARD}/{IMAGE}.jpg")
        self.assertEqual(storage.read_by_url_or_key(new_url), b"new-bytes")
        # 重新读取旧的 URL 时，不能把它改写到当前的前缀或分片上。
        self.assertEqual(_key_from_url_or_key(old_url, BASE), old_key)

    def test_a_url_of_another_root_keeps_its_shard_directory(self):
        foreign = f"https://old-cdn.example/original/{SHARD}/{IMAGE}.jpg"
        self.assertEqual(_key_from_url_or_key(foreign, BASE), f"original/{SHARD}/{IMAGE}.jpg")


if __name__ == "__main__":
    unittest.main()
