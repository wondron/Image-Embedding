"""离线验证图像导入：使用本机假 HTTP 服务，不连接真实后端或数据库。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

import index_folder as importer


def response(body: object, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.encoding = "utf-8"
    result._content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return result


class IndexFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.image = self.root / "红薯叶.PNG"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nexample-image-bytes")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        # Every test starts with the same configuration, regardless of local edits.
        self.enterContext(patch.multiple(
            importer, IMAGE_FILE="", IMAGE_DIR=str(self.root), BACKEND_DIR=self.root,
            BASE_URL="http://127.0.0.1:8320", RECURSIVE=True,
            IMAGE_EXTS=(".jpg", ".jpeg", ".png", ".webp"), FORM_FIELDS={},
            DRY_RUN=False, LIMIT=None, TIMEOUT_SECONDS=2, RETRIES=2, MAX_SERVER_FAILURES=3,
        ))

    def test_real_multipart_upload_preserves_bytes_chinese_metadata_and_duplicate_result(self) -> None:
        seen: list[tuple[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def send_json(self, body: dict) -> None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                seen.append((self.path, None))
                if self.path == "/api/health":
                    self.send_json({"ok": True, "components": {"embedding": {"ok": True}}})
                else:
                    self.send_json({"ok": True, "categories": [{
                        "code": "ingredient.HongSu", "name": "红薯叶", "is_active": True,
                    }]})

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                message = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
                )
                parts = {part.get_param("name", header="content-disposition"): part
                         for part in message.iter_parts()}
                seen.append((self.path, parts))
                self.send_json({
                    "ok": True, "indexed": [{"id": "image-1", "created": sum(
                        path == "/api/index" for path, _ in seen
                    ) == 1}], "failed": [],
                })

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)
        with patch.multiple(importer, BASE_URL=f"http://127.0.0.1:{server.server_port}/",
                            FORM_FIELDS={"category": "ingredient.hongsu", "tags": ["红薯叶", "新鲜"],
                                         "scenes": ["general"], "description": "一盘红薯叶", "water_mass_g": 0}):
            self.assertEqual(importer.main(), 0)
            self.assertEqual(importer.main(), 0)

        self.assertEqual([path for path, _ in seen], ["/api/health", "/api/categories", "/api/index"] * 2)
        parts = seen[2][1]
        self.assertEqual(parts["file"].get_payload(decode=True), self.image.read_bytes())
        self.assertEqual(parts["file"].get_filename(), self.image.name)
        self.assertEqual(parts["file"].get_content_type(), "image/png")
        text = lambda key: parts[key].get_payload(decode=True).decode("utf-8")
        self.assertEqual(json.loads(text("tags")), ["红薯叶", "新鲜"])
        self.assertEqual(json.loads(text("scenes")), ["general"])
        self.assertEqual(text("description"), "一盘红薯叶")
        self.assertEqual(text("category"), "ingredient.hongsu")
        self.assertEqual(text("water_mass_g"), "0")
        self.assertIn("新入库 1", self.output.getvalue())
        self.assertIn("已存在 1", self.output.getvalue())

    def test_input_can_be_a_relative_file_or_sorted_recursive_folder(self) -> None:
        nested = self.root / "子目录"
        nested.mkdir()
        second = nested / "a.jpg"
        second.write_bytes(b"second")
        (self.root / "ignore.txt").write_text("not an image", encoding="utf-8")
        with patch.multiple(importer, IMAGE_DIR="", IMAGE_FILE=self.image.name):
            self.assertEqual(importer.resolve_input(), (self.root, [self.image]))
        self.assertEqual(importer.resolve_input(), (self.root, sorted([self.image, second])))
        with patch.object(importer, "RECURSIVE", False):
            self.assertEqual(importer.resolve_input(), (self.root, [self.image]))
        with patch.object(importer, "LIMIT", 1):
            self.assertEqual(importer.resolve_input()[1], sorted([self.image, second])[:1])

    def test_invalid_configuration_or_input_stops_before_contacting_backend(self) -> None:
        cases = [
            {"IMAGE_DIR": ""}, {"IMAGE_FILE": str(self.image)},
            {"IMAGE_DIR": "missing"}, {"IMAGE_DIR": "", "IMAGE_FILE": "missing.png"},
            {"IMAGE_DIR": "", "IMAGE_FILE": "wrong.txt"},
            {"LIMIT": 0}, {"LIMIT": -1}, {"LIMIT": True},
            {"RETRIES": -1}, {"RETRIES": 0.5}, {"MAX_SERVER_FAILURES": 0},
            {"TIMEOUT_SECONDS": 0}, {"TIMEOUT_SECONDS": float("nan")},
            {"BASE_URL": "localhost:8320"}, {"BASE_URL": "http://localhost:99999"},
            {"FORM_FIELDS": {"tags": {"not": "a list"}}},
            {"FORM_FIELDS": {"scenes": [1]}},
        ]
        (self.root / "wrong.txt").write_bytes(b"not an image")
        for changes in cases:
            with self.subTest(changes=changes), patch.multiple(importer, **changes), \
                    patch.object(importer.requests, "Session") as session:
                self.assertEqual(importer.main(), 2)
                session.assert_not_called()

    def test_dry_run_lists_images_and_serializes_metadata_without_network(self) -> None:
        with patch.multiple(importer, DRY_RUN=True, FORM_FIELDS={"tags": " 红薯叶 ", "scenes": ("general",),
                                                               "description": None, "license": " "}), \
                patch.object(importer.requests, "Session") as session:
            self.assertEqual(importer.main(), 0)
            session.assert_not_called()
            form = importer.build_form(importer.FORM_FIELDS)
        self.assertEqual(json.loads(form["tags"]), ["红薯叶"])
        self.assertEqual(json.loads(form["scenes"]), ["general"])
        self.assertNotIn("description", form)
        self.assertNotIn("license", form)
        self.assertIn(self.image.name, self.output.getvalue())

    def test_http_200_failure_and_malformed_responses_are_not_successes(self) -> None:
        session = Mock(spec=requests.Session)
        session.post.return_value = response({"ok": True, "indexed": [], "failed": [{
            "error": "INDEX_BAD_IMAGE", "detail": "cannot decode",
        }]})
        outcome = importer.post_image(session, "bad.png", b"bad", {})
        self.assertEqual((outcome.status, outcome.error, outcome.server_side), ("failed", "INDEX_BAD_IMAGE", False))
        for body in ({}, [], {"ok": True, "indexed": ["wrong"]},
                     {"ok": True, "indexed": [{"created": True}], "failed": []},
                     {"ok": False, "indexed": [{"id": "fake"}], "failed": []}):
            with self.subTest(body=body):
                session.post.return_value = response(body)
                outcome = importer.post_image(session, "bad.png", b"bad", {})
                self.assertEqual((outcome.status, outcome.error), ("failed", "INVALID_RESPONSE"))

    def test_transient_failures_retry_identical_bytes_and_metadata(self) -> None:
        session = Mock(spec=requests.Session)
        session.post.side_effect = [
            requests.Timeout("timed out"), response({"error": "RATE_LIMIT", "detail": "busy"}, 429),
            response({"ok": True, "indexed": [], "failed": [{"error": "INDEX_POSTGRES_FAILED", "detail": "busy"}]}),
            response({"ok": True, "indexed": [{"id": "same-image", "created": False}], "failed": []}),
        ]
        with patch.object(importer, "RETRIES", 3), patch.object(importer.time, "sleep") as sleep:
            outcome = importer.upload(session, "a.png", b"same bytes", {"tags": '["中文"]'})
        self.assertEqual((outcome.status, outcome.image_id), ("existed", "same-image"))
        self.assertEqual(sleep.call_args_list, [call(2), call(4), call(8)])
        self.assertEqual(len(session.post.call_args_list), 4)
        self.assertTrue(all(item == session.post.call_args_list[0] for item in session.post.call_args_list))

    def test_permanent_http_failure_is_not_retried_and_transient_failure_has_a_limit(self) -> None:
        session = Mock(spec=requests.Session)
        session.post.return_value = response({"error": "VALIDATION_ERROR", "detail": "bad tags"}, 422)
        with patch.object(importer.time, "sleep") as sleep:
            outcome = importer.upload(session, "a.png", b"data", {})
            self.assertEqual(outcome.error, "VALIDATION_ERROR")
            session.post.assert_called_once()
            sleep.assert_not_called()
        session.reset_mock()
        session.post.return_value = response({"error": "NOT_READY", "detail": "busy"}, 503)
        with patch.object(importer.time, "sleep"):
            outcome = importer.upload(session, "a.png", b"data", {})
        self.assertTrue(outcome.server_side)
        self.assertEqual(session.post.call_count, importer.RETRIES + 1)

    def test_failed_preflight_never_uploads(self) -> None:
        for health, categories in (
            ({"ok": False, "components": {"postgres": {"ok": False, "detail": "offline"}}}, []),
            ({"ok": True, "components": {}}, [{"code": "another.category"}]),
        ):
            with self.subTest(health=health, categories=categories):
                session = Mock(spec=requests.Session)
                session.get.side_effect = [response(health), response({"ok": True, "categories": categories})]
                with patch.object(importer, "FORM_FIELDS", {"category": "ingredient.missing"}), \
                        patch.object(importer.requests, "Session") as factory:
                    factory.return_value.__enter__.return_value = session
                    self.assertEqual(importer.main(), 2)
                session.post.assert_not_called()

    def test_file_failure_continues_but_form_and_repeated_server_failures_stop(self) -> None:
        paths = []
        for number in range(4):
            path = self.root / f"{number}.png"
            path.write_bytes(b"image")
            paths.append(path)
        cases = [
            ([importer.Outcome("failed", error="INDEX_BAD_IMAGE"), importer.Outcome("created"),
              importer.Outcome("existed"), importer.Outcome("created")], 4),
            ([importer.Outcome("failed", error="VALIDATION_ERROR")], 1),
            ([importer.Outcome("failed", error="NOT_READY", server_side=True)] * 3, 3),
        ]
        for outcomes, count in cases:
            with self.subTest(outcomes=outcomes), patch.object(importer, "upload", side_effect=outcomes) as upload:
                self.assertEqual(importer.run(Mock(spec=requests.Session), self.root, paths, {}), 1)
                self.assertEqual(upload.call_count, count)


if __name__ == "__main__":
    unittest.main()
