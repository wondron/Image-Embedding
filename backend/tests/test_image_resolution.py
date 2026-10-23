import copy
import unittest
from unittest.mock import call, patch

import cv2
import numpy as np
import requests

from app.domain.common import image_utils


class ImageResolutionTests(unittest.TestCase):
    def setUp(self):
        self.original = 'https://images.invalid/original.png'
        self.large = 'https://images.invalid/large.png'
        self.medium = 'https://images.invalid/medium.png'
        self.small = 'https://images.invalid/small.png'
        self.item = {'title': 'example', 'image_url': self.original, 'details': {'images': [
            {'thumbnail_url': {'small': self.small, 'large': self.large, 'medium': self.medium}},
        ]}}
        self.bgr = np.array([[[19, 77, 231], [255, 0, 0]], [[0, 255, 0], [0, 0, 255]]], dtype=np.uint8)
        ok, encoded = cv2.imencode('.png', self.bgr)
        self.assertTrue(ok)
        self.png = encoded.tobytes()
        self.request_patch = patch('app.domain.common.image_utils.requests.get')
        self.get = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)

    def response(self, body, status=200):
        response = requests.Response()
        response.status_code = status
        response.url = self.original
        response._content = body
        response._content_consumed = True
        return response

    def test_original_then_thumbnails_are_ordered_by_size_without_mutation(self):
        before = copy.deepcopy(self.item)
        self.assertEqual(list(image_utils.iter_image_urls(self.item)), [self.original, self.large, self.medium, self.small])
        self.assertEqual(self.item, before)
        self.get.assert_not_called()

    def test_multiple_images_exhaust_large_before_medium_and_small(self):
        second_large = 'https://images.invalid/second-large.png'
        second_medium = 'https://images.invalid/second-medium.png'
        self.item['details']['images'].append({'thumbnail_url': {'large': second_large, 'medium': second_medium}})

        self.assertEqual(list(image_utils.iter_image_urls(self.item)), [self.original, self.large, second_large, self.medium, second_medium, self.small])

    def test_string_and_top_level_thumbnail_urls_are_supported_and_deduplicated(self):
        item = {'image_url': f'  {self.original}  ', 'thumbnail_url': self.large, 'details': {'images': [
            {'thumbnail_url': self.large},
            {'thumbnail_url': {'large': self.original, 'medium': self.medium, 'small': self.small}},
        ]}}
        self.assertEqual(list(image_utils.iter_image_urls(item)), [self.original, self.large, self.medium, self.small])

    def test_primary_url_may_itself_be_a_thumbnail_dictionary(self):
        item = {'image_url': {'small': self.small, 'medium': self.medium, 'large': self.large, 'tiny': 'https://images.invalid/tiny.png'}}
        before = copy.deepcopy(item)
        self.assertEqual(list(image_utils.iter_image_urls(item)), [self.large, self.medium, self.small, 'https://images.invalid/tiny.png'])
        self.assertEqual(item, before)

    def test_invalid_thumbnail_fields_and_empty_urls_are_ignored(self):
        for item in ({}, {'image_url': None, 'details': None}, {'image_url': 123, 'details': 'invalid'}, {'details': {'images': 'invalid'}}, {'details': {'images': [None, 42, {}, {'thumbnail_url': {'large': None, 'medium': '', 'small': '  ', 'bad': []}}]}}):
            with self.subTest(item=item):
                self.assertEqual(list(image_utils.iter_image_urls(item)), [])
        self.get.assert_not_called()

    def test_download_decodes_real_png_as_uint8_hwc_bgr(self):
        self.get.return_value = self.response(self.png)

        actual = image_utils.download_image_bgr(self.original, timeout=3)

        self.get.assert_called_once_with(self.original, timeout=3)
        self.assertIsInstance(actual, np.ndarray)
        self.assertEqual(actual.dtype, np.uint8)
        self.assertEqual(actual.shape, (2, 2, 3))
        np.testing.assert_array_equal(actual, self.bgr)
        self.assertEqual(actual[0, 0].tolist(), [19, 77, 231])

    def test_grayscale_png_is_decoded_as_three_channel_bgr(self):
        ok, encoded = cv2.imencode('.png', np.array([[20, 120]], dtype=np.uint8))
        self.assertTrue(ok)
        self.get.return_value = self.response(encoded.tobytes())

        actual = image_utils.download_image_bgr(self.original)

        np.testing.assert_array_equal(actual, np.array([[[20, 20, 20], [120, 120, 120]]], dtype=np.uint8))

    def test_download_rejects_invalid_url_values_without_request(self):
        for value in (None, '', '  ', 123, {}, []):
            with self.subTest(value=value):
                self.assertIsNone(image_utils.download_image_bgr(value))
        self.get.assert_not_called()

    def test_timeout_http_error_empty_or_corrupt_image_returns_none(self):
        for failure in (requests.Timeout('controlled timeout'), requests.ConnectionError('controlled failure')):
            with self.subTest(failure=failure):
                self.get.side_effect = failure
                self.assertIsNone(image_utils.download_image_bgr(self.original))
        self.get.side_effect = None
        for response in (self.response(self.png, status=404), self.response(b''), self.response(b'<html>not an image</html>'), self.response(self.png[:16])):
            with self.subTest(response=response):
                self.get.return_value = response
                self.assertIsNone(image_utils.download_image_bgr(self.original))

    def test_resolve_stops_at_original_success_without_modifying_item(self):
        before = copy.deepcopy(self.item)
        with patch.object(image_utils, 'download_image_bgr', return_value=self.bgr) as download:
            url, actual = image_utils.resolve_image_bgr(self.item, timeout=4)
        self.assertEqual(url, self.original)
        self.assertIs(actual, self.bgr)
        download.assert_called_once_with(self.original, timeout=4)
        self.assertEqual(self.item, before)

    def test_resolve_falls_back_to_medium_and_stops_before_small(self):
        before = copy.deepcopy(self.item)
        with patch.object(image_utils, 'download_image_bgr', side_effect=[None, None, self.bgr]) as download:
            url, actual = image_utils.resolve_image_bgr(self.item, timeout=4)
        self.assertEqual(url, self.medium)
        self.assertIs(actual, self.bgr)
        self.assertEqual(download.call_args_list, [call(self.original, timeout=4), call(self.large, timeout=4), call(self.medium, timeout=4)])
        self.assertEqual(self.item, before)

    def test_resolve_falls_back_after_real_corrupt_original_and_http_failure(self):
        self.get.side_effect = [self.response(b'not an image'), self.response(self.png, status=404), self.response(self.png)]
        before = copy.deepcopy(self.item)

        url, actual = image_utils.resolve_image_bgr(self.item)

        self.assertEqual(url, self.medium)
        np.testing.assert_array_equal(actual, self.bgr)
        self.assertEqual([request.args[0] for request in self.get.call_args_list], [self.original, self.large, self.medium])
        self.assertEqual(self.item, before)

    def test_resolve_all_failures_returns_none_pair_and_preserves_original_url(self):
        before = copy.deepcopy(self.item)
        with patch.object(image_utils, 'download_image_bgr', return_value=None) as download:
            self.assertEqual(image_utils.resolve_image_bgr(self.item), (None, None))
        self.assertEqual(download.call_count, 4)
        self.assertEqual(self.item, before)

    def test_resolve_without_candidates_never_downloads(self):
        with patch.object(image_utils, 'download_image_bgr') as download:
            self.assertEqual(image_utils.resolve_image_bgr({}), (None, None))
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
