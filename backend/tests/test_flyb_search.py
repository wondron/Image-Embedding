import unittest
from unittest.mock import Mock, patch

import requests

from app.domain.common.flybSearch import FlyBSearch


EMPTY_RESPONSE_MESSAGE = (
    "Search engine response is empty, no results returned for this query. "
    "Do not call this tool again during this conversation. "
    "Use existing results or another tool."
)


class FlyBSearchTests(unittest.TestCase):
    def setUp(self):
        self.search = FlyBSearch({
            "FLAB_CONFIG": {
                "API_URL": "https://search.invalid/global_search",
                "API_KEY": "test-only-key",
                "DOC_COUNT": 5,
                "MAX_SNIPPET_LENGTH": 300,
                "THRESHOLD": 0.8,
                "EnableWaiting": False,
                "Timeout": (10, 90),
            }
        })

    def empty_engine_response(self):
        return {
            "ResponseMetadata": {"RequestId": "test-request"},
            "Result": {"ErrorCode": 500, "ErrorMsg": EMPTY_RESPONSE_MESSAGE},
        }

    def test_empty_engine_response_returns_marked_empty_result_without_retry(self):
        response = Mock(spec=requests.Response)
        response.json.return_value = self.empty_engine_response()
        with patch(
            "app.domain.common.flybSearch.requests.post", return_value=response
        ) as post:
            with self.assertLogs("flyB", level="WARNING") as logs:
                result = self.search.get_result("test-image-base64")

        post.assert_called_once()
        response.raise_for_status.assert_called_once()
        self.assertEqual(result["request_id"], "test-request")
        self.assertEqual(result["total_doc_count"], 0)
        self.assertEqual(result["result_count"], 0)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "upstream_empty_response")
        self.assertEqual(result["upstream_error"], {
            "code": 500,
            "message": EMPTY_RESPONSE_MESSAGE,
        })
        self.assertIn("test-request", logs.output[0])

    def test_normal_empty_search_keeps_existing_response_schema(self):
        result = self.search.parse_results({
            "ResponseMetadata": {"RequestId": "test-request"},
            "Result": {"ErrorCode": 0, "TotalDocCount": 0, "Documents": []},
        })
        self.assertEqual(result, {
            "request_id": "test-request",
            "total_doc_count": 0,
            "result_count": 0,
            "results": [],
        })

    def test_successful_documents_are_preserved(self):
        result = self.search.parse_results({
            "ResponseMetadata": {"RequestId": "test-request"},
            "Result": {
                "ErrorCode": 0,
                "TotalDocCount": 1,
                "Documents": [{
                    "Rank": 1,
                    "Title": "Example dish",
                    "Url": "https://search.invalid/dish",
                    "HostInfo": {"Name": "Example site"},
                    "DocumentInfo": {"Id": "example-document"},
                    "Snippet": [
                        {"Type": "text", "Text": "Example description"},
                        {"Type": "image", "Image": {
                            "ImageUrl": "https://search.invalid/dish.jpg",
                        }},
                    ],
                }],
            },
        })
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0], {
            "rank": 1,
            "title": "Example dish",
            "image_url": "https://search.invalid/dish.jpg",
            "details": {
                "page_url": "https://search.invalid/dish",
                "site": {"Name": "Example site"},
                "text_snippets": ["Example description"],
                "images": [{
                    "image_url": "https://search.invalid/dish.jpg",
                    "thumbnail_url": None,
                    "alt": None,
                    "width": None,
                    "height": None,
                }],
                "document_info": {"Id": "example-document"},
            },
        })
        self.assertNotIn("upstream_error", result)

    def test_primary_image_prefers_original_url_over_thumbnail(self):
        snippets = [
            {"Type": "image", "Image": None},
            {"Type": "image", "Image": {
                "ThumbnailUrl": "https://search.invalid/first-thumb.jpg",
            }},
            {"Type": "image", "Image": {
                "ImageUrl": "https://search.invalid/second.jpg",
                "ThumbnailUrl": "https://search.invalid/second-thumb.jpg",
                "Alt": "Example dish",
                "Width": 800,
                "Height": 600,
            }},
            {"Type": "image", "Image": {
                "ImageUrl": "https://search.invalid/third.jpg",
            }},
        ]
        result = self.search.parse_results({
            "Result": {"Documents": [{"Snippet": snippets}]},
        })
        document = result["results"][0]
        self.assertEqual(document["image_url"], "https://search.invalid/second.jpg")
        self.assertEqual(len(document["details"]["images"]), 4)
        self.assertEqual(document["details"]["images"][2], {
            "image_url": "https://search.invalid/second.jpg",
            "thumbnail_url": "https://search.invalid/second-thumb.jpg",
            "alt": "Example dish",
            "width": 800,
            "height": 600,
        })

    def test_primary_image_falls_back_to_thumbnail(self):
        result = self.search.parse_results({
            "Result": {"Documents": [{"Snippet": [
                {"Type": "image", "Image": {}},
                {"Type": "image", "Image": {
                    "ThumbnailUrl": "https://search.invalid/thumb.jpg",
                }},
            ]}]},
        })
        self.assertEqual(result["results"][0]["image_url"],
                         "https://search.invalid/thumb.jpg")

    def test_documents_without_images_keep_shape_and_order(self):
        result = self.search.parse_results({
            "Result": {"TotalDocCount": 3, "Documents": [
                {"Rank": 2, "Title": "Text result", "Snippet": [
                    {"Type": "text", "Text": "Description"},
                ]},
                {"Rank": 1, "Title": "No snippets"},
                {"Rank": 3, "Title": "Null snippets", "Snippet": None},
            ]},
        })
        self.assertEqual(result["result_count"], 3)
        self.assertEqual([doc["rank"] for doc in result["results"]], [2, 1, 3])
        for doc in result["results"]:
            self.assertEqual(set(doc), {"rank", "title", "image_url", "details"})
            self.assertIsNone(doc["image_url"])
            self.assertEqual(doc["details"]["images"], [])
        self.assertEqual(result["results"][0]["details"]["text_snippets"], ["Description"])

    def test_other_business_errors_still_raise(self):
        for code, message in (
            (401, "Unauthorized"),
            (429, "Too many requests"),
            (500, "Internal search engine error"),
            (503, EMPTY_RESPONSE_MESSAGE),
        ):
            with self.subTest(code=code, message=message):
                with self.assertRaises(RuntimeError):
                    self.search.parse_results({
                        "Result": {"ErrorCode": code, "ErrorMsg": message},
                    })

    def test_metadata_error_still_raises(self):
        data = self.empty_engine_response()
        data["ResponseMetadata"]["Error"] = {"Code": "Unauthorized"}
        with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
            self.search.parse_results(data)

    def test_error_with_documents_is_not_treated_as_empty(self):
        data = self.empty_engine_response()
        data["Result"]["Documents"] = [{"Title": "Partial result"}]
        with self.assertRaises(RuntimeError):
            self.search.parse_results(data)

    def test_http_failure_still_raises(self):
        response = requests.Response()
        response.status_code = 503
        response.url = "https://search.invalid/global_search"
        with patch(
            "app.domain.common.flybSearch.requests.post", return_value=response
        ) as post:
            with self.assertRaises(requests.HTTPError):
                self.search.get_result("test-image-base64")
        post.assert_called_once()

    def test_timeout_still_raises_without_retry(self):
        with patch(
            "app.domain.common.flybSearch.requests.post",
            side_effect=requests.Timeout("test timeout"),
        ) as post:
            with self.assertRaises(requests.Timeout):
                self.search.get_result("test-image-base64")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
