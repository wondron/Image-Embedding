import copy
import unittest
from unittest.mock import Mock, patch

from app.domain.pipeline.b_getGlobalData import GetGlobalData


class GlobalDataTests(unittest.TestCase):
    def setUp(self):
        self.loader = GetGlobalData.__new__(GetGlobalData)
        self.loader.searchTool = Mock()

    def test_detect_returns_original_documents_without_downloading_or_changing_urls(self):
        rows = [
            {'title': 'unverified original', 'image_url': 'https://images.invalid/original.png', 'details': {'images': [
                {'thumbnail_url': {'large': 'https://images.invalid/large.png'}},
            ]}},
            {'title': 'thumbnail dictionary', 'image_url': {'large': 'https://images.invalid/large.png'}},
            {'title': 'no image', 'image_url': None},
        ]
        before = copy.deepcopy(rows)
        self.loader.searchTool.detect.return_value = rows

        with patch('requests.get') as download:
            result = self.loader.detect(b'controlled-query-image')

        self.assertIs(result, rows)
        self.assertEqual(result, before)
        self.loader.searchTool.detect.assert_called_once_with(b'controlled-query-image', None, None, None)
        download.assert_not_called()

    def test_detect_returns_envelope_unchanged_without_recounting_or_downloading(self):
        envelope = {'request_id': 'controlled-request', 'result_count': 9, 'results': [
            {'image_url': 'https://images.invalid/not-yet-checked.png'},
        ]}
        before = copy.deepcopy(envelope)
        self.loader.searchTool.detect.return_value = envelope

        with patch('requests.get') as download:
            result = self.loader.detect(b'controlled-query-image')

        self.assertIs(result, envelope)
        self.assertEqual(envelope, before)
        download.assert_not_called()

    def test_detect_preserves_empty_upstream_status_without_network_validation(self):
        envelope = {'status': 'upstream_empty_response', 'results': [], 'upstream_error': {'code': 500, 'message': 'empty'}}
        before = copy.deepcopy(envelope)
        self.loader.searchTool.detect.return_value = envelope

        with patch('requests.get') as download:
            result = self.loader.detect(b'controlled-query-image')

        self.assertIs(result, envelope)
        self.assertEqual(result, before)
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
