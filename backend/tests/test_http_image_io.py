import base64
import unittest
from io import BytesIO
from types import SimpleNamespace

import httpx
from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from app.adapter.http.services.image_io import clean_url, encode_png_base64, read_file_bytes, read_url_bytes


class ImageInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_limit_is_enforced_without_reading_the_entire_file(self):
        stream = BytesIO(b"x" * 100)
        file = UploadFile(stream, filename="large.png")
        with self.assertRaises(HTTPException) as caught:
            await read_file_bytes(file, 10)
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(stream.tell(), 11)
        self.assertFalse(stream.closed)

    async def test_upload_accepts_exact_limit_and_rejects_empty(self):
        self.assertEqual(await read_file_bytes(UploadFile(BytesIO(b"image")), 5), b"image")
        with self.assertRaises(HTTPException) as caught:
            await read_file_bytes(UploadFile(BytesIO()), 5)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_mime_validation_is_optional_and_precedes_reading(self):
        stream = BytesIO(b"image")
        file = UploadFile(stream, headers=Headers({"content-type": "text/html"}))
        with self.assertRaises(HTTPException) as caught:
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
            with self.subTest(status=status), self.assertRaises(HTTPException) as caught:
                await read_file_bytes(
                    UploadFile(BytesIO(data)), 5,
                    empty_detail="uploaded file is empty",
                    too_large_detail="file too large (> 5 bytes)",
                )
            self.assertEqual(caught.exception.status_code, status)
            self.assertEqual(caught.exception.detail, detail)

    async def download(self, handler, url="https://images.example/a.png", limit=10):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await read_url_bytes(url, client=client, max_image_bytes=limit, timeout_s=7)

    async def test_url_download_normalizes_and_uses_provided_client(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://images.example/a.png")
            return httpx.Response(200, content=b"image")
        self.assertEqual(await self.download(handler, " //images.example/a.png \n"), b"image")

    async def test_declared_oversize_is_rejected_before_stream_read_and_closes_response(self):
        class UnreadStream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                raise AssertionError("oversize response must not be read")
                yield b""

            async def aclose(self):
                self.closed = True

        stream = UnreadStream()
        with self.assertRaises(HTTPException) as caught:
            await self.download(lambda request: httpx.Response(200, headers={"Content-Length": "11"}, stream=stream))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertTrue(stream.closed)

    async def test_stream_limit_cannot_be_bypassed_by_missing_or_underreported_content_length(self):
        class OversizeStream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b"x" * 11
                raise AssertionError("the stream should stop as soon as the limit is exceeded")

            async def aclose(self):
                self.closed = True

        for headers in ({}, {"Content-Length": "1"}, {"Content-Length": "invalid"}):
            stream = OversizeStream()
            with self.subTest(headers=headers), self.assertRaises(HTTPException) as caught:
                await self.download(lambda request: httpx.Response(200, headers=headers, stream=stream))
            self.assertEqual(caught.exception.status_code, 413)
            self.assertTrue(stream.closed)

    async def test_url_empty_body_and_upstream_status_are_preserved(self):
        for upstream, body, expected in ((200, b"", 400), (403, b"denied", 403), (404, b"missing", 404)):
            with self.subTest(upstream=upstream), self.assertRaises(HTTPException) as caught:
                await self.download(lambda request: httpx.Response(upstream, content=body))
            self.assertEqual(caught.exception.status_code, expected)

    async def test_url_transport_failures_map_to_http_errors(self):
        for error, status in ((httpx.ReadTimeout, 504), (httpx.ConnectError, 502), (httpx.UnsupportedProtocol, 422)):
            def handler(request):
                raise error("controlled transport failure", request=request)
            with self.subTest(error=error), self.assertRaises(HTTPException) as caught:
                await self.download(handler)
            self.assertEqual(caught.exception.status_code, status)
            if status == 504:
                self.assertIn("7s", caught.exception.detail)


class ImageFormatTests(unittest.TestCase):
    def test_search_url_normalization_and_validation(self):
        for url in (" images.example/a.png\n", "//images.example/a.png", "https://images.example/a.png"):
            self.assertEqual(clean_url(url), "https://images.example/a.png")
        for url in ("", " \n", "file:///tmp/a.png", "ftp://images.example/a.png", "https:///a.png", "https://[invalid"):
            with self.subTest(url=url), self.assertRaises(HTTPException) as caught:
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
