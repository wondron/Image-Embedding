import json
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from app.domain.food_service import FoodService
from app.domain.pipeline.b_getGlobalData import GetGlobalData
from app.domain.pipeline.c_embedding import GlobalFilter


class GlobalFilterTests(unittest.TestCase):
    def setUp(self):
        self.model_patch = patch('app.domain.pipeline.c_embedding.OnnxEmbedding')
        self.model_type = self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.model = self.model_type.return_value
        self.resolver_patch = patch('app.domain.pipeline.c_embedding.resolve_image_bgr', return_value=(None, None))
        self.resolve = self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)
        self.filter = GlobalFilter({'GLOBAL_FILTER': {'threshold': 0.7, 'switch': True}})
        self.image = np.zeros((2, 3, 3), dtype=np.uint8)

    def test_filters_candidates_in_original_order_and_embeds_query_once(self):
        candidates = [{'title': str(i), 'image_bgr': self.image.copy()} for i in range(3)]
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]),
            np.array([1.0, 0.0]),
            np.array([0.0, 1.0]),
            np.array([0.8, 0.6]),
        ]

        result = self.filter.detect(self.image, candidates)

        self.assertEqual([item['title'] for item in result], ['0', '2'])
        self.assertTrue(all('image_bgr' not in item for item in result))
        self.assertTrue(all('image_bgr' in item for item in candidates))
        self.assertEqual(jsonable_encoder(result), [
            {'title': '0', 'similarity': 1.0},
            {'title': '2', 'similarity': 0.8},
        ])
        self.assertEqual(len(candidates), 3)
        self.assertEqual([item['similarity'] for item in result], [1.0, 0.8])
        self.assertTrue(all(isinstance(item['similarity'], float) for item in result))
        self.assertEqual(self.model.detect.call_count, 4)
        self.assertIs(self.model.detect.call_args_list[0].args[0], self.image)
        self.resolve.assert_not_called()

    def test_empty_candidates_skip_inference(self):
        self.assertEqual(self.filter.detect(self.image, []), [])
        self.model.detect.assert_not_called()
        self.resolve.assert_not_called()

    def test_disabled_filter_returns_all_candidates_without_images_or_inference(self):
        self.filter.switch = False
        candidates = [
            {'title': 'first', 'image_url': 'https://images.invalid/original.png', 'image_bgr': self.image, 'details': {'rank': 1}},
            {'title': 'second', 'image_url': 'https://images.invalid/unverified.png'},
        ]

        result = self.filter.detect(self.image, candidates)
        response = JSONResponse(content=jsonable_encoder({'result': result}))

        self.assertEqual(json.loads(response.body)['result'], [
            {'title': 'first', 'image_url': 'https://images.invalid/original.png', 'details': {'rank': 1}},
            {'title': 'second', 'image_url': 'https://images.invalid/unverified.png'},
        ])
        self.assertIs(candidates[0]['image_bgr'], self.image)
        self.model.detect.assert_not_called()
        self.resolve.assert_not_called()

    def test_failed_candidate_log_does_not_reuse_previous_similarity(self):
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]), np.array([1.0, 0.0]),
        ]
        with self.assertLogs('embedding', level='INFO') as logs:
            self.filter.detect(self.image, [{'image_bgr': self.image}, {'image_bgr': None}])

        timings = [line for line in logs.output if 'processing--item=' in line]
        self.assertEqual(len(timings), 2)
        self.assertIn('--val=1.00', timings[0])
        self.assertIn('--val=N/A', timings[1])
        self.assertEqual(self.model.detect.call_count, 2)

    def test_similarity_equal_to_threshold_is_excluded(self):
        self.filter.threshold = 0.0
        self.model.detect.side_effect = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        self.assertEqual(self.filter.detect(self.image, [{'image_bgr': self.image}]), [])

    def test_invalid_candidate_does_not_discard_other_results(self):
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]),
            np.array([1.0, 0.0]),
        ]
        good = {'image_bgr': self.image}
        with self.assertLogs('embedding', level='WARNING'):
            result = self.filter.detect(self.image, [{}, {'image_bgr': None}, good])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {'similarity': 1.0})
        self.assertIs(good['image_bgr'], self.image)
        self.assertEqual(self.model.detect.call_count, 2)
        self.assertEqual(self.resolve.call_count, 2)

    def test_missing_candidate_image_is_resolved_only_when_filtering(self):
        original_url = 'https://images.invalid/original.png'
        usable_url = 'https://images.invalid/medium.png'
        candidate = {'title': 'fallback', 'image_url': original_url, 'details': {'images': [
            {'thumbnail_url': {'large': 'https://images.invalid/large.png', 'medium': usable_url}},
        ]}}
        self.resolve.return_value = (usable_url, self.image)
        self.model.detect.side_effect = [np.array([1.0, 0.0]), np.array([1.0, 0.0])]

        result = self.filter.detect(self.image, [candidate])

        self.resolve.assert_called_once_with(candidate)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['image_url'], usable_url)
        self.assertEqual(candidate['image_url'], original_url)
        self.assertEqual(result[0]['similarity'], 1.0)
        self.assertNotIn('image_bgr', result[0])
        self.assertIs(self.model.detect.call_args_list[1].args[0], self.image)
        self.assertEqual(jsonable_encoder(result), result)

    def test_disabled_food_service_preserves_search_url_without_image_requests(self):
        original_url = 'https://images.invalid/not-yet-validated.png'
        documents = [{'title': 'raw search result', 'image_url': original_url}]
        loader = GetGlobalData.__new__(GetGlobalData)
        loader.searchTool = Mock()
        loader.searchTool.detect.return_value = documents
        service = FoodService.__new__(FoodService)
        service.searchTool = loader
        self.filter.switch = False
        service.searchFilter = self.filter
        _, query_png = cv2.imencode('.png', self.image)

        with patch('app.domain.common.image_utils.requests.get') as download:
            result = service.detect(query_png.tobytes())

        self.assertEqual(result, documents)
        self.assertEqual(result[0]['image_url'], original_url)
        download.assert_not_called()
        self.resolve.assert_not_called()
        self.model.detect.assert_not_called()

    def test_all_failed_candidate_urls_skip_candidate_embedding(self):
        candidate = {'title': 'unavailable', 'image_url': 'https://images.invalid/broken.png'}
        self.model.detect.return_value = np.array([1.0, 0.0])

        with self.assertLogs('embedding', level='WARNING'):
            result = self.filter.detect(self.image, [candidate])

        self.assertEqual(result, [])
        self.resolve.assert_called_once_with(candidate)
        self.model.detect.assert_called_once_with(self.image)

    def test_invalid_decoded_candidate_can_still_be_skipped_without_losing_good_result(self):
        good = {'image_bgr': self.image}
        invalid = {'image_bgr': self.image.copy()}
        self.model.detect.side_effect = [np.array([1.0, 0.0]), ValueError('invalid candidate shape'), np.array([1.0, 0.0])]

        with self.assertLogs('embedding', level='WARNING'):
            result = self.filter.detect(self.image, [invalid, good])

        self.assertEqual(result, [{'similarity': 1.0}])
        self.assertEqual(self.model.detect.call_count, 3)
        self.resolve.assert_not_called()

    def test_query_errors_and_model_runtime_errors_propagate(self):
        self.model.detect.side_effect = ValueError('invalid query')
        with self.assertRaisesRegex(ValueError, 'invalid query'):
            self.filter.detect(self.image, [{'image_bgr': self.image}])

        self.model.detect.side_effect = [np.array([1.0, 0.0]), RuntimeError('model failed')]
        with self.assertRaisesRegex(RuntimeError, 'model failed'):
            self.filter.detect(self.image, [{'image_bgr': self.image}])

    def test_food_service_returns_filtered_json_without_mutating_internal_images(self):
        candidates = [
            {'title': 'keep', 'image_bgr': self.image},
            {'title': 'drop', 'image_bgr': self.image},
        ]
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, 1.0]),
        ]
        service = FoodService.__new__(FoodService)
        service.searchTool = Mock()
        service.searchTool.detect.return_value = candidates
        service.searchFilter = self.filter
        encoded_ok, encoded = cv2.imencode('.png', self.image)
        self.assertTrue(encoded_ok)

        result = service.detect(encoded.tobytes())

        payload = {'result': result, 'trace_id': 'test-trace'}
        response = JSONResponse(content=jsonable_encoder(payload))
        self.assertEqual(json.loads(response.body), {
            'result': [{'title': 'keep', 'similarity': 1.0}],
            'trace_id': 'test-trace',
        })
        self.assertIs(candidates[0]['image_bgr'], self.image)

    def test_envelope_results_are_filtered_and_json_serializable(self):
        candidates = [
            {'title': 'keep', 'image_bgr': self.image},
            {'title': 'drop', 'image_bgr': self.image},
        ]
        envelope = {'request_id': 'test-request', 'results': candidates, 'result_count': 2}
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, 1.0]),
        ]
        service = FoodService.__new__(FoodService)
        service.searchTool = Mock()
        service.searchTool.detect.return_value = envelope
        service.searchFilter = self.filter
        _, encoded = cv2.imencode('.png', self.image)

        result = service.detect(encoded.tobytes())
        response = JSONResponse(content=jsonable_encoder({'result': result}))

        self.assertEqual(json.loads(response.body)['result'], {
            'request_id': 'test-request',
            'results': [{'title': 'keep', 'similarity': 1.0}],
            'result_count': 1,
        })
        self.assertIs(candidates[0]['image_bgr'], self.image)

    def test_empty_upstream_envelope_preserves_status_without_inference(self):
        envelope = {
            'status': 'upstream_empty_response',
            'results': [],
            'result_count': 0,
            'upstream_error': {'code': 500, 'message': 'empty'},
        }
        loader = GetGlobalData.__new__(GetGlobalData)
        loader.searchTool = Mock()
        loader.searchTool.detect.return_value = envelope
        service = FoodService.__new__(FoodService)
        service.searchTool = loader
        service.searchFilter = self.filter
        _, encoded = cv2.imencode('.png', self.image)

        with patch('app.domain.common.image_utils.requests.get') as download:
            result = service.detect(encoded.tobytes())

        self.assertEqual(result, envelope)
        self.assertEqual(result['status'], 'upstream_empty_response')
        self.model.detect.assert_not_called()
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
