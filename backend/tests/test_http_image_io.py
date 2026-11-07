import base64
import ipaddress
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image
from starlette.datastructures import Headers

from app.api.uploads import read_upload as read_file_bytes
from app.clients import image_fetch, public_image
from app.clients.image_fetch import clean_url, read_url_bytes
from app.core.errors import AppError
from app.utils.image import encode_png_base64


class ImageInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_limit_is_enforced_without_reading_the_entire_file(self):
        stream = BytesIO(b"x" * 100)
        file = UploadFile(stream, filename="large.png")
        with self.assertRaises(AppError) as caught:
            await read_file_bytes(file, 10)
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(stream.tell(), 11)
        self.assertFalse(stream.closed)

    async def test_upload_accepts_exact_limit_and_rejects_empty(self):
        self.assertEqual(await read_file_bytes(UploadFile(BytesIO(b"image")), 5), b"image")
        with self.assertRaises(AppError) as caught:
            await read_file_bytes(UploadFile(BytesIO()), 5)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_mime_validation_is_optional_and_precedes_reading(self):
        stream = BytesIO(b"image")
        file = UploadFile(stream, headers=Headers({"content-type": "text/html"}))
        with self.assertRaises(AppError) as caught:
            await read_file_bytes(file, 10, allowed_content_types={"image/png"})
        self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(await read_file_bytes(file, 10), b"image")
        # Missing MIME declarations remain accepted by the tools API.
        self.assertEqual(
            await read_file_bytes(UploadFile(BytesIO(b"image")), 10, allowed_content_types={"image/png"}),
            b"image",
        )

    async def test_tool_upload_error_details_are_preserved(self):
        for data, status, detail in ((b"", 400, "uploaded file is empty"), (b"123456", 413, "file too large (> 5 bytes)")):
            with self.subTest(status=status), self.assertRaises(AppError) as caught:
                await read_file_bytes(
                    UploadFile(BytesIO(data)), 5,
                    empty_detail="uploaded file is empty",
                    too_large_detail="file too large (> 5 bytes)",
                )
            self.assertEqual(caught.exception.status_code, status)
            self.assertEqual(caught.exception.detail, detail)

    async def test_url_download_normalizes_and_uses_the_public_image_fetcher(self):
        with patch.object(image_fetch.public_image, "fetch_image", return_value=(b"image", "image/png")) as fetch:
            data = await read_url_bytes(" //images.example/a.png \n", max_image_bytes=10, timeout_s=7)
        self.assertEqual(data, b"image")
        fetch.assert_called_once_with("https://images.example/a.png", max_bytes=10, total_timeout=7)

    async def test_private_and_loopback_hosts_are_refused_before_any_connection(self):
        urls = (
            "http://127.0.0.1/a.png",
            "http://10.0.0.8/a.png",
            "http://192.168.1.10/a.png",
            "http://[::1]/a.png",
            "http://169.254.169.254/latest/meta-data",
        )
        with patch.object(public_image, "_connection", side_effect=AssertionError("must not connect")):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(AppError) as caught:
                    await read_url_bytes(url, max_image_bytes=10, timeout_s=7)
                self.assertEqual((caught.exception.code, caught.exception.status_code), ("FORBIDDEN_HOST", 403))

    async def test_fake_ip_proxy_addresses_need_an_explicit_trust_entry(self):
        fake_ip = "http://198.18.0.32/a.png"  # where a fake-IP proxy resolves every public name
        with patch.object(public_image, "_connection", side_effect=AssertionError("must not connect")), \
                self.assertRaises(AppError) as caught:
            await read_url_bytes(fake_ip, max_image_bytes=10, timeout_s=7)
        self.assertEqual(caught.exception.code, "FORBIDDEN_HOST")

        public_image.set_trusted_networks([ipaddress.ip_network("198.18.0.0/15")])
        self.addCleanup(public_image.set_trusted_networks, ())
        with patch.object(public_image, "_connection", side_effect=OSError("reached the connect step")), \
                self.assertRaises(AppError) as caught:
            await read_url_bytes(fake_ip, max_image_bytes=10, timeout_s=7)
        self.assertEqual(caught.exception.code, "DOWNLOAD_FAILED")  # allowed, then the fake connect fails
        # ports stay restricted to 80 / 443 even for a trusted network
        with self.assertRaises(AppError) as caught:
            await read_url_bytes("http://198.18.0.32:6333/a.png", max_image_bytes=10, timeout_s=7)
        self.assertEqual(caught.exception.code, "INVALID_URL")

    async def test_remote_status_is_never_passed_through(self):
        class Response:
            def __init__(self, status):
                self.status = status

            def getheader(self, name):
                return None

            def close(self):
                pass

        class Connection:
            sock = None

            def __init__(self, status):
                self.response = Response(status)

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                return self.response

            def close(self):
                pass

        target = SimpleNamespace(path="/a.png")
        for status in (403, 404, 500):
            with self.subTest(status=status), patch.object(public_image, "_resolve_target", return_value=target), \
                    patch.object(public_image, "_connection", return_value=Connection(status)), \
                    self.assertRaises(AppError) as caught:
                await read_url_bytes("https://images.example/a.png", max_image_bytes=10, timeout_s=7)
            self.assertEqual((caught.exception.code, caught.exception.status_code), ("DOWNLOAD_FAILED", 502))
            self.assertIn(f"upstream HTTP {status}", caught.exception.detail)


class ImageFormatTests(unittest.TestCase):
    def test_search_url_normalization_and_validation(self):
        for url in (" images.example/a.png\n", "//images.example/a.png", "https://images.example/a.png"):
            self.assertEqual(clean_url(url), "https://images.example/a.png")
        for url in ("", " \n", "file:///tmp/a.png", "ftp://images.example/a.png", "https:///a.png", "https://[invalid"):
            with self.subTest(url=url), self.assertRaises(AppError) as caught:
                clean_url(url)
            self.assertEqual(caught.exception.status_code, 422)

    def test_png_encoding_preserves_grayscale_and_bgr_color_channels(self):
        for array, bgr, mode, pixel in (
            (SimpleNamespace(ndim=2, shape=(1, 1), tobytes=lambda: bytes([173])), False, "L", 173),
            (SimpleNamespace(ndim=3, shape=(1, 1, 3), tobytes=lambda: bytes([10, 20, 30])), True, "RGB", (30, 20, 10)),
        ):
            with self.subTest(mode=mode), Image.open(BytesIO(base64.b64decode(encode_png_base64(array, bgr=bgr)))) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.mode, mode)
                self.assertEqual(image.getpixel((0, 0)), pixel)
