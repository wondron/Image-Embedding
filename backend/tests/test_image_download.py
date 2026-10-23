import http.client
import socket
import ssl
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.adapter.http.endpoints import image_download as downloads


def address_record(address="93.184.216.34"):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))


class FakeResponse:
    def __init__(self, data=b"", *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = BytesIO(data)
        self.closed = False

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, size):
        return self.body.read(size)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.sock = None
        self.closed = False
        self.requests = []

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ImageDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, "PNG")
        cls.png = output.getvalue()

    def setUp(self):
        resolver = patch.object(downloads.socket, "getaddrinfo", return_value=[address_record()])
        self.dns = resolver.start()
        self.addCleanup(resolver.stop)

    def assert_http_error(self, status, function, *args):
        with self.assertRaises(HTTPException) as caught:
            function(*args)
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def fetch(self, response):
        connection = FakeConnection(response)
        with patch.object(downloads, "_connection", return_value=connection):
            result = downloads._fetch_image("https://images.example/photo.png")
        self.assertTrue(connection.closed)
        self.assertTrue(response.closed)
        return result

    def test_rejects_invalid_urls_credentials_and_nonstandard_ports(self):
        for url in ("file:///tmp/image", "ftp://images.example/a", "https://user:secret@images.example/a", "https://images.example:8080/a", "https://images.example:0/a", "https://images.example:invalid/a", "https://images.example/\r\nx", "https://[fe80::1%25eth0]/a", "https://bad host/a"):
            with self.subTest(url=url):
                self.assert_http_error(422, downloads._resolve_target, url)
        self.dns.assert_not_called()

    def test_rejects_nonpublic_ip_literals_before_network_access(self):
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.1.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "[::1]", "[fc00::1]", "[fe80::1]", "[::ffff:127.0.0.1]", "[2002:7f00:1::]", "[64:ff9b::7f00:1]"):
            with self.subTest(ip=ip):
                self.assert_http_error(403, downloads._resolve_target, f"http://{ip}/a")
        self.dns.assert_not_called()

    def test_rejects_host_if_any_dns_answer_is_nonpublic(self):
        self.dns.return_value = [address_record(), address_record("127.0.0.1")]
        self.assert_http_error(403, downloads._resolve_target, "https://images.example/a")

    def test_dns_failure_returns_generic_error_without_secret_url(self):
        self.dns.side_effect = socket.gaierror("resolver detail")
        error = self.assert_http_error(502, downloads._fetch_image, "https://images.example/a?token=secret")
        self.assertNotIn("secret", error.detail)

    def test_connection_pins_dns_address_and_preserves_https_sni_verification(self):
        self.dns.side_effect = [[address_record()], [address_record("127.0.0.1")]]
        target = downloads._resolve_target("https://images.example/a.png")
        connection = downloads._connection(target, 10)
        self.assertIsInstance(connection, http.client.HTTPSConnection)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        context = Mock()
        raw_socket = Mock()
        connection._context = context
        with patch.object(downloads.socket, "create_connection", return_value=raw_socket) as connect:
            connection.connect()
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
        self.assertGreater(connect.call_args.args[1], 0)
        self.assertLessEqual(connect.call_args.args[1], 10)
        self.assertIsNone(connect.call_args.args[2])
        self.assertEqual(connection.host, "images.example")
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="images.example")
        self.dns.assert_called_once()
        connection.close()

    def test_public_http_connection_keeps_original_host_header(self):
        target = downloads._resolve_target("http://images.example/a.png?token=fixture")
        connection = downloads._connection(target, 10)
        fake_socket = Mock()
        with patch.object(downloads.socket, "create_connection", return_value=fake_socket) as connect:
            connection.request("GET", target.path)
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 80))
        request = b"".join(call.args[0] for call in fake_socket.sendall.call_args_list)
        self.assertIn(b"Host: images.example\r\n", request)
        self.assertIn(b"GET /a.png?token=fixture HTTP/1.1", request)
        connection.close()

    def test_valid_png_returns_verified_type_not_upstream_content_type(self):
        content, media_type = self.fetch(FakeResponse(self.png, headers={"Content-Type": "text/html"}))
        self.assertEqual(content, self.png)
        self.assertEqual(media_type, "image/png")

    def test_dns_address_fallback_stays_within_validated_addresses(self):
        self.dns.return_value = [address_record("2606:4700::1111"), address_record()]
        connection = downloads._connection(downloads._resolve_target("https://images.example/a"), 10)
        with patch.object(downloads.socket, "create_connection", side_effect=[OSError("No IPv6 route"), Mock()]) as connect:
            raw_socket = connection._create_connection(("images.example", 443))
        self.assertIsNotNone(raw_socket)
        self.assertEqual([call.args[0] for call in connect.call_args_list], [("2606:4700::1111", 443), ("93.184.216.34", 443)])
        self.assertLessEqual(connect.call_args_list[1].args[1], connect.call_args_list[0].args[1])

    def test_total_deadline_expiry_returns_timeout_before_connection(self):
        with patch.object(downloads.time, "monotonic", side_effect=[0, downloads.TOTAL_TIMEOUT + 1]), patch.object(downloads, "_connection") as connect:
            self.assert_http_error(504, downloads._fetch_image, "https://images.example/a")
        connect.assert_not_called()

    def test_public_redirect_is_followed_and_connections_are_closed(self):
        responses = [FakeResponse(status=302, headers={"Location": "https://cdn.example/final.png"}), FakeResponse(self.png)]
        connections = [FakeConnection(response) for response in responses]
        with patch.object(downloads, "_connection", side_effect=connections) as connect:
            self.assertEqual(downloads._fetch_image("http://images.example/start")[0], self.png)
        self.assertEqual([call.args[0].host for call in connect.call_args_list], ["images.example", "cdn.example"])
        self.assertEqual(self.dns.call_count, 2)
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertTrue(all(response.closed for response in responses))

    def test_relative_redirect_keeps_path_and_is_revalidated(self):
        connections = [FakeConnection(FakeResponse(status=307, headers={"Location": "../final.png"})), FakeConnection(FakeResponse(self.png))]
        with patch.object(downloads, "_connection", side_effect=connections) as connect:
            downloads._fetch_image("https://images.example/path/start.png")
        self.assertEqual(connect.call_args_list[1].args[0].path, "/final.png")
        self.assertEqual(self.dns.call_count, 2)

    def test_redirect_to_private_literal_never_connects_to_it(self):
        connection = FakeConnection(FakeResponse(status=302, headers={"Location": "http://169.254.169.254/metadata"}))
        with patch.object(downloads, "_connection", return_value=connection) as connect:
            self.assert_http_error(403, downloads._fetch_image, "https://images.example/a")
        connect.assert_called_once()
        self.assertTrue(connection.closed)

    def test_redirect_dns_is_checked_for_nonpublic_addresses(self):
        self.dns.side_effect = [[address_record()], [address_record("10.0.0.1")]]
        connection = FakeConnection(FakeResponse(status=301, headers={"Location": "https://private.example/a"}))
        with patch.object(downloads, "_connection", return_value=connection) as connect:
            self.assert_http_error(403, downloads._fetch_image, "https://images.example/a")
        connect.assert_called_once()

    def test_redirect_limit_and_missing_location_are_rejected(self):
        for location, expected_calls in (("/again", 4), (None, 1)):
            with self.subTest(location=location):
                response = FakeResponse(status=302, headers={"Location": location} if location else {})
                connection = FakeConnection(response)
                with patch.object(downloads, "_connection", return_value=connection) as connect:
                    self.assert_http_error(502, downloads._fetch_image, "https://images.example/a")
                self.assertEqual(connect.call_count, expected_calls)
                self.assertTrue(connection.closed)

    def test_content_length_limit_rejects_before_read(self):
        response = FakeResponse(self.png, headers={"Content-Length": str(downloads.MAX_IMAGE_BYTES + 1)})
        connection = FakeConnection(response)
        with patch.object(downloads, "_connection", return_value=connection):
            self.assert_http_error(413, downloads._fetch_image, "https://images.example/a")
        self.assertEqual(response.body.tell(), 0)
        self.assertTrue(connection.closed)

    def test_chunked_or_underreported_image_cannot_exceed_limit(self):
        for headers in ({}, {"Content-Length": "1"}):
            with self.subTest(headers=headers), patch.object(downloads, "MAX_IMAGE_BYTES", 10):
                connection = FakeConnection(FakeResponse(b"x" * 100, headers=headers))
                with patch.object(downloads, "_connection", return_value=connection):
                    self.assert_http_error(413, downloads._fetch_image, "https://images.example/a")
                self.assertEqual(connection.response.body.tell(), 11)
                self.assertTrue(connection.closed)

    def test_nonimage_and_corrupt_images_are_rejected(self):
        for data in (b"", b"<html>not an image</html>", self.png[:40]):
            with self.subTest(data=data):
                connection = FakeConnection(FakeResponse(data, headers={"Content-Type": "image/png"}))
                with patch.object(downloads, "_connection", return_value=connection):
                    self.assert_http_error(415, downloads._fetch_image, "https://images.example/a")

    def test_invalid_content_length_and_upstream_error_are_generic(self):
        for response in (FakeResponse(headers={"Content-Length": "-1"}), FakeResponse(headers={"Content-Length": "oops"}), FakeResponse(status=403)):
            with self.subTest(response=response):
                with patch.object(downloads, "_connection", return_value=FakeConnection(response)):
                    error = self.assert_http_error(502, downloads._fetch_image, "https://images.example/a?token=secret")
                self.assertNotIn("secret", error.detail)

    def test_socket_timeout_and_connection_failure_map_to_http_errors(self):
        for cause, expected in ((socket.timeout("detail"), 504), (OSError("detail"), 502), (http.client.IncompleteRead(b""), 502)):
            with self.subTest(cause=cause):
                connection = FakeConnection(FakeResponse())
                connection.request = Mock(side_effect=cause)
                with patch.object(downloads, "_connection", return_value=connection):
                    self.assert_http_error(expected, downloads._fetch_image, "https://images.example/a")
                self.assertTrue(connection.closed)

    def test_response_deadline_interrupts_slow_headers_and_maps_to_timeout(self):
        connection = FakeConnection(FakeResponse())
        transport = Mock()
        connection.sock = transport
        with patch.object(downloads.threading, "Timer") as watchdog, patch.object(downloads, "_connection", return_value=connection):
            def expire_headers():
                # The real timer closes the socket while getresponse is blocked.
                watchdog.call_args.args[1]()
                raise http.client.RemoteDisconnected("closed by deadline")

            connection.getresponse = Mock(side_effect=expire_headers)
            self.assert_http_error(504, downloads._fetch_image, "https://images.example/a")
        transport.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        transport.close.assert_called_once()
        watchdog.return_value.start.assert_called_once()
        watchdog.return_value.cancel.assert_called_once()
        self.assertTrue(connection.closed)

    def test_response_deadline_treats_truncated_body_as_timeout_not_invalid_image(self):
        response = FakeResponse(self.png)
        connection = FakeConnection(response)
        transport = Mock()
        connection.sock = transport
        with patch.object(downloads.threading, "Timer") as watchdog, patch.object(downloads, "_connection", return_value=connection):
            def expire_body(_size):
                # HTTPResponse may already own the transport after detachment.
                connection.sock = None
                watchdog.call_args.args[1]()
                return b""

            response.read = Mock(side_effect=expire_body)
            self.assert_http_error(504, downloads._fetch_image, "https://images.example/a")
        transport.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        transport.close.assert_called_once()
        watchdog.return_value.cancel.assert_called_once()
        self.assertTrue(response.closed)

    def test_response_watchdog_is_cancelled_on_normal_completion(self):
        with patch.object(downloads.threading, "Timer") as watchdog:
            self.assertEqual(self.fetch(FakeResponse(self.png))[0], self.png)
        watchdog.return_value.cancel.assert_called_once()

    def test_route_returns_image_headers_and_validates_query_without_models(self):
        app = FastAPI()
        app.include_router(downloads.router)
        with TestClient(app) as client, patch.object(downloads, "_fetch_image", return_value=(self.png, "image/png")) as fetch:
            response = client.get("/api/images/download", params={"url": "https://images.example/photo.png"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, self.png)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            fetch.assert_called_once_with("https://images.example/photo.png")
            self.assertEqual(client.get("/api/images/download").status_code, 422)


if __name__ == "__main__":
    unittest.main()
