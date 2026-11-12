"""Independent OSS key_prefix acceptance assertions (frozen contract §8).

Not a substitute for, and not substituted by, backend/tests/test_storage_keys.py.
Implementer unit results are recorded separately and are not §8 PASS.
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

# Repo layout: this file lives in <repo>/.qa/oss-key-prefix/ when executed on the machine.
_FILE = Path(__file__).resolve()
_REPO = _FILE.parents[2] if _FILE.parent.name == "oss-key-prefix" else _FILE.parents[1]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.clients.image_storage import MockImageStorage, S3ImageStorage  # noqa: E402
from app.core.config.key_prefix import KEY_PREFIX_MAX_LEN, normalize_key_prefix  # noqa: E402
from app.core.config.settings import ImageStorageSettings  # noqa: E402
from app.services.retrieval.delete_image import _clean_up  # noqa: E402
from app.services.retrieval.image_ids import uuid_from_sha256  # noqa: E402
from app.services.retrieval.index_image import IndexImageUseCase  # noqa: E402
from app.services.retrieval.reembed_image import ReembedImageUseCase  # noqa: E402
from app.services.retrieval.storage_keys import (  # noqa: E402
    guess_ext_from_bytes_header,
    guess_ext_from_content_type,
    original_key,
    public_url,
    staging_key,
    thumb_key,
    THUMB_EXT,
)
from tests.test_index_image_identity import (  # noqa: E402
    MODEL,
    FakeEmbedding,
    FakeMetadata,
    FakeVectors,
    _png,
)


BASE = "https://cdn.example.local/image-embedding"
IMAGE_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CANDIDATE_ID = UUID("11111111-2222-3333-4444-555555555555")
PREFIX = "ImageEmbedding"
# Isolated observations written for the report (no secrets).
OBSERVATIONS: dict[str, Any] = {
    "doubled_url": None,
    "orphan_keys_after_reupload": [],
    "boto3_importable": None,
    "use_boto3_path": "S3ImageStorage.__new__ + isolated FakeS3Client (no network)",
}


def _storage_section(key_prefix: Any, **overrides: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "use_boto3": False,
        "endpoint_url": "",
        "bucket": "qa-isolated-bucket",
        "public_base_url": BASE,
        "region": "us-east-1",
        "access_key_id": "QA_ISOLATED_NOT_A_SECRET",
        "secret_access_key": "QA_ISOLATED_NOT_A_SECRET",
        "mock_mirror_dir": "",
    }
    section.update(overrides)
    if key_prefix is _OMIT:
        section.pop("key_prefix", None)
    else:
        section["key_prefix"] = key_prefix
    return {"IMAGE_STORAGE_CONFIG": section}


_OMIT = object()


def _settings(key_prefix: Any = _OMIT, **overrides: Any) -> ImageStorageSettings:
    return ImageStorageSettings.from_config(_storage_section(key_prefix, **overrides))


class FakeS3Client:
    """In-memory stand-in for boto3 S3 client. No network."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[dict[str, Any]] = []
        self.deletes: list[tuple[str, str]] = []

    def put_object(self, Bucket: str, Key: str, Body: bytes, **extra: Any) -> dict[str, Any]:
        blob = bytes(Body)
        self.objects[(Bucket, Key)] = blob
        self.puts.append({"Bucket": Bucket, "Key": Key, "bytes": len(blob), **extra})
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise KeyError(f"NoSuchKey {Bucket}/{Key}")
        return {"Body": type("Body", (), {"read": lambda self: self.objects[(Bucket, Key)]})()}  # broken

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.objects.pop((Bucket, Key), None)
        self.deletes.append((Bucket, Key))
        return {}


# Fix FakeS3Client.get_object Body to close over the blob.
class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _fixed_get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
    if (Bucket, Key) not in self.objects:
        raise KeyError(f"NoSuchKey {Bucket}/{Key}")
    return {"Body": _Body(self.objects[(Bucket, Key)])}


FakeS3Client.get_object = _fixed_get_object  # type: ignore[method-assign]


def _s3_storage(settings: ImageStorageSettings) -> tuple[S3ImageStorage, FakeS3Client]:
    """Build S3ImageStorage without boto3.__init__ or any network call."""
    inst = S3ImageStorage.__new__(S3ImageStorage)
    inst.settings = settings
    inst.public_base_url = settings.public_base_url
    inst.bucket = settings.bucket
    client = FakeS3Client()
    inst._client = client
    return inst, client


def _assert_no_double_slash_in_path(url: str) -> None:
    # Scheme // is expected; the path after the host must not contain //
    # and must not glue the prefix to the reserved segment.
    after_scheme = url.split("://", 1)[1]
    host, _, path = after_scheme.partition("/")
    if "//" in path:
        raise AssertionError(f"URL path contains // : {url!r}")
    if "ImageEmbeddingoriginal" in path or "ImageEmbeddingthumb" in path or "ImageEmbeddingstaging" in path:
        raise AssertionError(f"URL missing slash between prefix and reserved segment: {url!r}")


class TestPoint1DefaultCompatibility(unittest.TestCase):
    """§8.1 omit / null / empty / slash-or-whitespace still yield current-production keys."""

    def _expect_default(self, settings: ImageStorageSettings) -> None:
        self.assertEqual(settings.key_prefix, "")
        ok = original_key(IMAGE_ID, "png", key_prefix=settings.key_prefix)
        tk = thumb_key(IMAGE_ID, key_prefix=settings.key_prefix)
        sk = staging_key(CANDIDATE_ID, "jpg", key_prefix=settings.key_prefix)
        self.assertEqual(ok, f"original/{IMAGE_ID}.png")
        self.assertEqual(tk, f"thumb/{IMAGE_ID}.webp")
        self.assertEqual(sk, f"staging/{CANDIDATE_ID}.jpg")
        self.assertEqual(public_url(settings.public_base_url, ok), f"{BASE}/original/{IMAGE_ID}.png")
        self.assertEqual(public_url(settings.public_base_url, tk), f"{BASE}/thumb/{IMAGE_ID}.webp")
        self.assertEqual(public_url(settings.public_base_url, sk), f"{BASE}/staging/{CANDIDATE_ID}.jpg")

    def test_omit_key_prefix(self) -> None:
        self._expect_default(_settings(_OMIT))

    def test_null_key_prefix(self) -> None:
        self._expect_default(_settings(None))

    def test_empty_string(self) -> None:
        self._expect_default(_settings(""))

    def test_slash_and_whitespace_equivalents(self) -> None:
        for raw in ("/", "//", "///", "\\", "\\\\", "  ", "\t", "  /  ", " /\\ ", "／", "＼"):
            with self.subTest(raw=raw):
                self._expect_default(_settings(raw))

    def test_mock_put_uses_unprefixed_keys(self) -> None:
        settings = _settings("")
        storage = MockImageStorage(settings)
        url = storage.put_original(IMAGE_ID, b"abc", ext="png")
        self.assertEqual(url, f"{BASE}/original/{IMAGE_ID}.png")
        self.assertIn(f"original/{IMAGE_ID}.png", storage._objects)
        self.assertEqual(public_url(settings.public_base_url, f"original/{IMAGE_ID}.png"), url)


class TestPoint2ForwardPrefix(unittest.TestCase):
    """§8.2 ImageEmbedding and slash/backslash equivalents; mock and use_boto3 keys match."""

    VARIANTS = (
        "ImageEmbedding",
        "/ImageEmbedding/",
        "\\ImageEmbedding\\",
        "//ImageEmbedding//",
        "  ImageEmbedding  ",
        "／ImageEmbedding／",
        "＼ImageEmbedding＼",
        "ImageEmbedding/",
        "/ImageEmbedding",
    )

    def test_normalized_prefix_and_urls(self) -> None:
        expected_orig = f"{PREFIX}/original/{IMAGE_ID}.png"
        expected_thumb = f"{PREFIX}/thumb/{IMAGE_ID}.webp"
        expected_stage = f"{PREFIX}/staging/{CANDIDATE_ID}.jpg"
        expected_orig_url = f"{BASE}/{PREFIX}/original/{IMAGE_ID}.png"
        expected_thumb_url = f"{BASE}/{PREFIX}/thumb/{IMAGE_ID}.webp"
        for raw in self.VARIANTS:
            with self.subTest(raw=raw):
                settings = _settings(raw)
                self.assertEqual(settings.key_prefix, PREFIX)
                ok = original_key(IMAGE_ID, "png", key_prefix=settings.key_prefix)
                tk = thumb_key(IMAGE_ID, key_prefix=settings.key_prefix)
                sk = staging_key(CANDIDATE_ID, "jpg", key_prefix=settings.key_prefix)
                self.assertEqual(ok, expected_orig)
                self.assertEqual(tk, expected_thumb)
                self.assertEqual(sk, expected_stage)
                ou = public_url(settings.public_base_url, ok)
                tu = public_url(settings.public_base_url, tk)
                self.assertEqual(ou, expected_orig_url)
                self.assertEqual(tu, expected_thumb_url)
                _assert_no_double_slash_in_path(ou)
                _assert_no_double_slash_in_path(tu)
                _assert_no_double_slash_in_path(public_url(settings.public_base_url, sk))

    def test_mock_and_use_boto3_paths_share_keys(self) -> None:
        settings = _settings(PREFIX, use_boto3=False)
        s3_settings = _settings(PREFIX, use_boto3=True)
        self.assertEqual(settings.key_prefix, s3_settings.key_prefix)
        self.assertTrue(s3_settings.use_boto3)
        self.assertFalse(settings.use_boto3)

        mock = MockImageStorage(settings)
        s3, client = _s3_storage(s3_settings)
        orig_bytes, thumb_bytes, stage_bytes = b"orig-qa", b"thumb-qa", b"stage-qa"
        mock_orig = mock.put_original(IMAGE_ID, orig_bytes, ext="png", content_type="image/png")
        mock_thumb = mock.put_thumb(IMAGE_ID, thumb_bytes)
        mock_stage = mock.put_staging(CANDIDATE_ID, stage_bytes, ext="jpg")
        s3_orig = s3.put_original(IMAGE_ID, orig_bytes, ext="png", content_type="image/png")
        s3_thumb = s3.put_thumb(IMAGE_ID, thumb_bytes)
        s3_stage = s3.put_staging(CANDIDATE_ID, stage_bytes, ext="jpg", content_type="image/jpeg")

        self.assertEqual(mock_orig, s3_orig)
        self.assertEqual(mock_thumb, s3_thumb)
        self.assertEqual(mock_stage, s3_stage)
        self.assertEqual(mock_orig, f"{BASE}/{PREFIX}/original/{IMAGE_ID}.png")
        self.assertEqual(set(mock._objects), {
            f"{PREFIX}/original/{IMAGE_ID}.png",
            f"{PREFIX}/thumb/{IMAGE_ID}.webp",
            f"{PREFIX}/staging/{CANDIDATE_ID}.jpg",
        })
        self.assertEqual({key for (_b, key) in client.objects}, set(mock._objects))
        for url in (mock_orig, mock_thumb, mock_stage, s3_orig, s3_thumb, s3_stage):
            _assert_no_double_slash_in_path(url)

        try:
            import boto3  # noqa: F401
            OBSERVATIONS["boto3_importable"] = True
        except ImportError:
            OBSERVATIONS["boto3_importable"] = False
        OBSERVATIONS["use_boto3_path"] = (
            "S3ImageStorage.__new__ + isolated FakeS3Client (no network, no live YAML change)"
        )


class TestPoint3NormalizeErrors(unittest.TestCase):
    """§8.3 illegal values fail load; no silent rewrite. Stricter §1/§2 rules."""

    def _must_fail(self, raw: Any, *, via: str) -> Exception:
        with self.assertRaises(ValueError) as ctx:
            if via == "normalize":
                normalize_key_prefix(raw)
            else:
                _settings(raw)
        return ctx.exception

    def test_dot_dot_segment(self) -> None:
        for raw in ("..", "foo/..", "../ImageEmbedding", "a/../b"):
            with self.subTest(raw=raw):
                err = self._must_fail(raw, via="from_config")
                self.assertIn("..", str(err))

    def test_dot_segment(self) -> None:
        for raw in (".", "foo/.", "./ImageEmbedding"):
            with self.subTest(raw=raw):
                self._must_fail(raw, via="from_config")

    def test_reserved_exact_case_rejected(self) -> None:
        for raw in ("original", "thumb", "staging", "foo/original", "thumb/x", "staging/tmp", "/original/"):
            with self.subTest(raw=raw):
                err = self._must_fail(raw, via="from_config")
                self.assertRegex(str(err), r"original|thumb|staging")

    def test_reserved_other_case_and_Original_are_legal(self) -> None:
        # Exact-case only: Original is legal; STAGING / Thumb are legal.
        for raw, expected in (
            ("Original", "Original"),
            ("STAGING", "STAGING"),
            ("Thumb", "Thumb"),
            ("ORIGINAL", "ORIGINAL"),
        ):
            with self.subTest(raw=raw):
                settings = _settings(raw)
                self.assertEqual(settings.key_prefix, expected)
                self.assertEqual(
                    original_key(IMAGE_ID, "png", key_prefix=settings.key_prefix),
                    f"{expected}/original/{IMAGE_ID}.png",
                )

    def test_mid_spaces_rejected(self) -> None:
        for raw in ("Image Embedding", "ImageEmbedding /foo", "foo/ bar"):
            with self.subTest(raw=raw):
                self._must_fail(raw, via="from_config")

    def test_overlong_rejected_not_truncated(self) -> None:
        raw = "A" * (KEY_PREFIX_MAX_LEN + 1)
        err = self._must_fail(raw, via="from_config")
        self.assertIn(str(KEY_PREFIX_MAX_LEN), str(err))
        # Direct normalize also refuses and does not return a truncated prefix.
        with self.assertRaises(ValueError):
            out = normalize_key_prefix(raw)
            self.fail(f"silently rewrote overlong prefix to {out!r}")

    def test_non_string_scalars_value_error_not_stringified(self) -> None:
        # Stricter rule: do not run key_prefix through _text(); numbers must not become "1".
        for raw in (1, 0, 1.5, True, False, b"ImageEmbedding"):
            with self.subTest(raw=raw):
                err = self._must_fail(raw, via="from_config")
                self.assertIn("必须是字符串", str(err))
                self.assertIn(type(raw).__name__, str(err))
                # Confirm it did not silently become a string prefix.
                with self.assertRaises(ValueError):
                    settings = _settings(raw)
                    self.fail(f"silently accepted {raw!r} as prefix {settings.key_prefix!r}")

    def test_non_string_containers_value_error(self) -> None:
        for raw in ([], {}, ["ImageEmbedding"], {"prefix": "ImageEmbedding"}):
            with self.subTest(raw=raw):
                self._must_fail(raw, via="from_config")

    def test_from_config_does_not_call_text_on_key_prefix(self) -> None:
        src = inspect.getsource(ImageStorageSettings.from_config)
        # key_prefix must be read raw, not via _text(...) which stringifies numbers.
        self.assertNotRegex(src, r"key_prefix\s*=\s*_text\(")
        self.assertIn("not isinstance(raw_prefix, str)", src)

    def test_max_len_boundary_accepted(self) -> None:
        raw = "B" * KEY_PREFIX_MAX_LEN
        self.assertEqual(_settings(raw).key_prefix, raw)


class TestPoint4DoubledUrlObservation(unittest.TestCase):
    """§8.4 observe doubled URL when public_base_url already ends with the same path; intercept is pending."""

    def test_observe_doubled_url_no_required_load_time_intercept(self) -> None:
        base = f"https://cdn.example.local/{PREFIX}"
        settings = _settings(PREFIX, public_base_url=base)
        self.assertEqual(settings.key_prefix, PREFIX)
        self.assertEqual(settings.public_base_url, base)  # from_config rstrips trailing slash only
        key = original_key(IMAGE_ID, "png", key_prefix=settings.key_prefix)
        url = public_url(settings.public_base_url, key)
        doubled = f"{base}/{PREFIX}/original/{IMAGE_ID}.png"
        OBSERVATIONS["doubled_url"] = {
            "public_base_url": base,
            "key_prefix": settings.key_prefix,
            "composed_key": key,
            "actual_public_url": url,
            "expected_doubled_shape": doubled,
            "did_double": url == doubled,
            "load_time_intercepted": False,
            "note": "Load-time intercept is specification-pending; doubling is observed, not a required fail.",
        }
        # Current implementation composes base + key and therefore doubles; that is observed.
        self.assertEqual(url, doubled)
        self.assertIn(f"/{PREFIX}/{PREFIX}/", url)


class TestPoint5OldKeyCompat(unittest.TestCase):
    """§8.5 pre-prefix catalog URLs stay; read/delete/reembed hit the old key, never a recomputed new key."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="oss-key-prefix-qa-")
        self.addCleanup(self.tmp.cleanup)
        self.mirror = Path(self.tmp.name) / "mirror"
        self.mirror.mkdir()
        self.old_settings = _settings("", mock_mirror_dir=str(self.mirror))
        self.new_settings = _settings(PREFIX, mock_mirror_dir=str(self.mirror))
        self.old_store = MockImageStorage(self.old_settings)
        self.new_store = MockImageStorage(self.new_settings)
        self.image_bytes = b"legacy-original-bytes"
        self.thumb_bytes = b"legacy-thumb-bytes"
        self.old_orig_url = self.old_store.put_original(IMAGE_ID, self.image_bytes, ext="png")
        self.old_thumb_url = self.old_store.put_thumb(IMAGE_ID, self.thumb_bytes)
        # Catalog row written before prefix enable, then prefix is enabled on a new storage instance
        # sharing the same isolated mirror (same objects).
        self.catalog: dict[str, Any] = {
            "id": str(IMAGE_ID),
            "original_url": self.old_orig_url,
            "thumb_url": self.old_thumb_url,
            "payload_version": 1,
            "scenes": ["general"],
        }

    def test_catalog_urls_unchanged_after_prefix_enable(self) -> None:
        self.assertEqual(self.old_orig_url, f"{BASE}/original/{IMAGE_ID}.png")
        self.assertEqual(self.old_thumb_url, f"{BASE}/thumb/{IMAGE_ID}.webp")
        # List / detail surface the stored columns; they do not recompute from current prefix.
        list_row = dict(self.catalog)
        detail_row = dict(self.catalog)
        self.assertEqual(list_row["original_url"], self.old_orig_url)
        self.assertEqual(detail_row["thumb_url"], self.old_thumb_url)
        # A wrongly recomputed new URL must not replace the stored one.
        recomputed = public_url(self.new_settings.public_base_url, original_key(IMAGE_ID, "png", key_prefix=PREFIX))
        self.assertEqual(recomputed, f"{BASE}/{PREFIX}/original/{IMAGE_ID}.png")
        self.assertNotEqual(list_row["original_url"], recomputed)

    def test_read_by_stored_url_hits_old_key_on_prefixed_storage(self) -> None:
        # New storage instance (prefix enabled) still reads the legacy object via the stored URL.
        got = self.new_store.read_by_url_or_key(self.catalog["original_url"])
        self.assertEqual(got, self.image_bytes)
        # Confirm the prefixed key does not exist and is not invented for the read.
        prefixed = f"{PREFIX}/original/{IMAGE_ID}.png"
        self.assertNotIn(prefixed, self.new_store._objects)
        self.assertFalse((self.mirror / prefixed).exists())
        self.assertTrue((self.mirror / f"original/{IMAGE_ID}.png").exists())

    def test_delete_by_stored_url_does_not_recompute_new_prefix_key(self) -> None:
        # Plant a decoy at the new prefixed key so a recompute-and-delete bug would remove the wrong object.
        decoy = b"should-not-be-deleted"
        decoy_url = self.new_store.put_original(IMAGE_ID, decoy, ext="png")
        self.assertEqual(decoy_url, f"{BASE}/{PREFIX}/original/{IMAGE_ID}.png")
        self.assertTrue((self.mirror / f"{PREFIX}/original/{IMAGE_ID}.png").exists())
        self.assertTrue((self.mirror / f"original/{IMAGE_ID}.png").exists())

        class _Meta:
            def get_by_id(self, image_id):  # already hard-deleted from catalog
                return None

            def complete_deletion(self, image_id):
                self.completed = image_id

        vectors = FakeVectors()
        _clean_up(_Meta(), vectors, self.new_store, {
            "image_id": str(IMAGE_ID),
            "original_url": self.catalog["original_url"],
            "thumb_url": self.catalog["thumb_url"],
        })
        # Old keys gone; decoy at the new prefix remains. This forbids "delete by current prefix".
        self.assertIn(f"original/{IMAGE_ID}.png", self.new_store._deleted)
        self.assertIn(f"thumb/{IMAGE_ID}.webp", self.new_store._deleted)
        self.assertNotIn(f"{PREFIX}/original/{IMAGE_ID}.png", self.new_store._deleted)
        self.assertFalse((self.mirror / f"original/{IMAGE_ID}.png").exists())
        self.assertTrue((self.mirror / f"{PREFIX}/original/{IMAGE_ID}.png").exists())
        self.assertEqual(self.new_store.read_by_url_or_key(decoy_url), decoy)

    def test_reembed_reads_stored_original_url_not_recomputed_key(self) -> None:
        class _Meta:
            def get_by_id(self, image_id):
                return dict(self.row)

            def upsert_image_embedding(self, *, model_id, image_id, synced_version=None):
                self.ledger = (model_id, str(image_id), synced_version)

        meta = _Meta()
        meta.row = dict(self.catalog)
        vectors = FakeVectors()
        reads: list[str] = []
        inner = self.new_store.read_by_url_or_key

        def _track(url_or_key: str) -> bytes:
            reads.append(url_or_key)
            return inner(url_or_key)

        self.new_store.read_by_url_or_key = _track  # type: ignore[method-assign]
        ReembedImageUseCase(FakeEmbedding(), vectors, meta, model=MODEL, storage=self.new_store).execute(IMAGE_ID)
        self.assertEqual(reads, [self.catalog["original_url"]])
        self.assertNotIn(f"{BASE}/{PREFIX}/original/{IMAGE_ID}.png", reads)


class TestPoint6NewWrites(unittest.TestCase):
    """§8.6 after prefix enable, new ingest response and catalog URLs are prefixed; delete removes prefixed objects."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="oss-key-prefix-qa-")
        self.addCleanup(self.tmp.cleanup)
        self.mirror = Path(self.tmp.name) / "mirror"
        self.mirror.mkdir()
        self.settings = _settings(PREFIX, mock_mirror_dir=str(self.mirror))
        self.storage = MockImageStorage(self.settings)
        self.meta = FakeMetadata()
        self.vectors = FakeVectors()
        self.uc = IndexImageUseCase(
            FakeEmbedding(), self.vectors, self.meta, model=MODEL, storage=self.storage, indexed_dir=self.tmp.name
        )

    def test_new_index_response_and_catalog_urls_are_prefixed(self) -> None:
        data = _png()
        out = self.uc.execute_one(image_bytes=data)
        image_id = out["id"]
        expected_orig = f"{BASE}/{PREFIX}/original/{image_id}.png"
        expected_thumb = f"{BASE}/{PREFIX}/thumb/{image_id}.webp"
        self.assertTrue(out.get("created", True) or "original_url" in out)
        self.assertEqual(out["original_url"], expected_orig)
        self.assertEqual(out["thumb_url"], expected_thumb)
        row = self.meta.get_by_id(image_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["original_url"], expected_orig)
        self.assertEqual(row["thumb_url"], expected_thumb)
        _assert_no_double_slash_in_path(out["original_url"])
        self.assertIn(f"{PREFIX}/original/{image_id}.png", self.storage._objects)
        self.assertIn(f"{PREFIX}/thumb/{image_id}.webp", self.storage._objects)
        self.image_id = image_id
        self.row = row

    def test_delete_new_image_removes_prefixed_objects(self) -> None:
        data = _png((9, 8, 7, 6))
        out = self.uc.execute_one(image_bytes=data)
        image_id = out["id"]
        orig_url = out["original_url"]
        thumb_url = out["thumb_url"]
        self.assertTrue((self.mirror / f"{PREFIX}/original/{image_id}.png").exists())
        self.meta.mark_deleted(image_id)

        class _Meta:
            def __init__(self, parent):
                self.parent = parent

            def get_by_id(self, image_id):
                return None

            def complete_deletion(self, image_id):
                self.completed = image_id

        _clean_up(_Meta(self.meta), self.vectors, self.storage, {
            "image_id": image_id,
            "original_url": orig_url,
            "thumb_url": thumb_url,
        })
        self.assertIn(f"{PREFIX}/original/{image_id}.png", self.storage._deleted)
        self.assertIn(f"{PREFIX}/thumb/{image_id}.webp", self.storage._deleted)
        self.assertFalse((self.mirror / f"{PREFIX}/original/{image_id}.png").exists())
        self.assertFalse((self.mirror / f"{PREFIX}/thumb/{image_id}.webp").exists())


class TestPoint7DuplicateIngest(unittest.TestCase):
    """§8.7 re-upload after prefix switch keeps the first URL; orphan new-key objects are recorded."""

    def test_reupload_keeps_first_url_and_records_orphan(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="oss-key-prefix-qa-")
        self.addCleanup(tmp.cleanup)
        mirror = Path(tmp.name) / "mirror"
        mirror.mkdir()
        data = _png((1, 2, 3, 4))
        meta = FakeMetadata()
        vectors = FakeVectors()

        old_settings = _settings("", mock_mirror_dir=str(mirror))
        old_store = MockImageStorage(old_settings)
        first = IndexImageUseCase(
            FakeEmbedding(), vectors, meta, model=MODEL, storage=old_store, indexed_dir=tmp.name
        ).execute_one(image_bytes=data)
        first_orig, first_thumb, image_id = first["original_url"], first["thumb_url"], first["id"]
        self.assertEqual(first_orig, f"{BASE}/original/{image_id}.png")
        self.assertTrue(first.get("created", True))

        new_settings = _settings(PREFIX, mock_mirror_dir=str(mirror))
        new_store = MockImageStorage(new_settings)
        second = IndexImageUseCase(
            FakeEmbedding(), vectors, meta, model=MODEL, storage=new_store, indexed_dir=tmp.name
        ).execute_one(image_bytes=data)
        # First catalog URL wins (ON CONFLICT DO NOTHING / FakeMetadata keeps first row;
        # index_image responds from get_by_id after put).
        self.assertEqual(second["original_url"], first_orig)
        self.assertEqual(second["thumb_url"], first_thumb)
        self.assertEqual(meta.get_by_id(image_id)["original_url"], first_orig)
        # Put still happened against the new prefix: orphan objects may exist.
        orphan_orig = f"{PREFIX}/original/{image_id}.png"
        orphan_thumb = f"{PREFIX}/thumb/{image_id}.webp"
        orphan_present = [
            key for key in (orphan_orig, orphan_thumb)
            if key in new_store._objects or (mirror / key).exists()
        ]
        OBSERVATIONS["orphan_keys_after_reupload"] = orphan_present
        OBSERVATIONS["orphan_note"] = (
            "Cleaning orphans is not a required pass item; presence is recorded only."
        )
        # Contract: first URL retained. Orphans are expected with current put-then-get_by_id order.
        self.assertTrue(orphan_present, "expected new-prefix objects after re-upload put")
        # Old objects still present (catalog still points at them).
        self.assertTrue((mirror / f"original/{image_id}.png").exists())


class TestPoint8Frontend(unittest.TestCase):
    """§8.8 no FE change in this knife; open-URL check requires a legal storage/CDN."""

    def test_frontend_knife_did_not_change_for_prefix(self) -> None:
        # Frontend diffs in the worktree are parallel library-filter/preference work, not this knife.
        fe_root = _REPO / "frontend"
        hits: list[str] = []
        for path in fe_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".css", ".md", ".json"}:
                continue
            if "node_modules" in path.parts or "dist" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "key_prefix" in text or "ImageEmbedding/" in text:
                hits.append(path.relative_to(_REPO).as_posix())
        self.assertEqual(hits, [], f"frontend unexpectedly mentions key_prefix or ImageEmbedding/ : {hits}")

    def test_open_url_skipped_without_legal_cdn(self) -> None:
        # Live gitignored YAML is use_boto3:false with example public_base_url; this test must not
        # pretend a fetch of https://cdn.example.local/... succeeded, and must not change live YAML.
        OBSERVATIONS["point8_open_url"] = {
            "verdict": "SKIP",
            "reason": (
                "No legal storage/CDN is configured for this acceptance. "
                "The running gitignored config keeps use_boto3: false and an example "
                "public_base_url (cdn.example.local). Fetching that host would not prove "
                "object reachability. Signed URLs are out of scope. No live YAML was changed."
            ),
        }
        self.skipTest(OBSERVATIONS["point8_open_url"]["reason"])


class TestPoint9Regression(unittest.TestCase):
    """§8.9 extension rules, thumb is webp, staging shape, image id algorithm unchanged."""

    def test_extension_rules(self) -> None:
        self.assertEqual(guess_ext_from_bytes_header(b"\xff\xd8\xffXXXX"), "jpg")
        self.assertEqual(guess_ext_from_bytes_header(b"\x89PNG\r\n\x1a\nXXXX"), "png")
        self.assertEqual(guess_ext_from_bytes_header(b"RIFF....WEBPXXXX"), "webp")
        self.assertEqual(guess_ext_from_bytes_header(b"GIF89aXXXX"), "gif")
        self.assertEqual(guess_ext_from_bytes_header(b"GIF87aXXXX"), "gif")
        self.assertEqual(guess_ext_from_bytes_header(b"not-an-image"), "jpg")  # default
        self.assertEqual(guess_ext_from_content_type("image/jpeg"), "jpg")
        self.assertEqual(guess_ext_from_content_type("image/png"), "png")
        self.assertEqual(guess_ext_from_content_type("image/webp"), "webp")
        self.assertEqual(guess_ext_from_content_type("image/gif"), "gif")
        self.assertEqual(guess_ext_from_content_type("application/octet-stream"), "jpg")
        # Header still wins for a real PNG even after prefix composition.
        data = _png()
        self.assertEqual(guess_ext_from_bytes_header(data), "png")
        self.assertEqual(
            original_key(IMAGE_ID, guess_ext_from_bytes_header(data), key_prefix=""),
            f"original/{IMAGE_ID}.png",
        )
        self.assertEqual(
            original_key(IMAGE_ID, guess_ext_from_bytes_header(data), key_prefix=PREFIX),
            f"{PREFIX}/original/{IMAGE_ID}.png",
        )

    def test_thumb_always_webp(self) -> None:
        self.assertEqual(THUMB_EXT, "webp")
        self.assertEqual(thumb_key(IMAGE_ID, key_prefix=""), f"thumb/{IMAGE_ID}.webp")
        self.assertEqual(thumb_key(IMAGE_ID, key_prefix=PREFIX), f"{PREFIX}/thumb/{IMAGE_ID}.webp")
        # Even if someone passes a different ext mentally, the API has no ext argument.
        sig = inspect.signature(thumb_key)
        self.assertNotIn("ext", sig.parameters)

    def test_staging_key_shape(self) -> None:
        self.assertEqual(staging_key(CANDIDATE_ID, "png", key_prefix=""), f"staging/{CANDIDATE_ID}.png")
        self.assertEqual(
            staging_key(CANDIDATE_ID, "JPG", key_prefix=PREFIX),
            f"{PREFIX}/staging/{CANDIDATE_ID}.jpg",
        )

    def test_image_id_algorithm_unchanged(self) -> None:
        import hashlib

        data = _png()
        digest = hashlib.sha256(data).digest()
        self.assertEqual(uuid_from_sha256(digest), UUID(bytes=digest[:16]))
        # Index path still uses the same id when storage is prefixed.
        tmp = tempfile.TemporaryDirectory(prefix="oss-key-prefix-qa-")
        self.addCleanup(tmp.cleanup)
        settings = _settings(PREFIX, mock_mirror_dir=str(Path(tmp.name) / "m"))
        Path(settings.mock_mirror_dir).mkdir()
        storage = MockImageStorage(settings)
        meta = FakeMetadata()
        out = IndexImageUseCase(
            FakeEmbedding(), FakeVectors(), meta, model=MODEL, storage=storage, indexed_dir=tmp.name
        ).execute_one(image_bytes=data)
        self.assertEqual(out["id"], str(uuid_from_sha256(digest)))


def observations() -> dict[str, Any]:
    return dict(OBSERVATIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
