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

    def test_empty_candidates_skip_inference(self):
        self.assertEqual(self.filter.detect(self.image, []), [])
        self.model.detect.assert_not_called()

    def test_disabled_filter_returns_all_candidates_without_images_or_inference(self):
        self.filter.switch = False
        candidates = [
            {'title': 'first', 'image_bgr': self.image, 'details': {'rank': 1}},
            {'title': 'second', 'image_bgr': self.image},
        ]

        result = self.filter.detect(self.image, candidates)
        response = JSONResponse(content=jsonable_encoder({'result': result}))

        self.assertEqual(json.loads(response.body)['result'], [
            {'title': 'first', 'details': {'rank': 1}},
            {'title': 'second'},
        ])
        self.assertTrue(all('image_bgr' in item for item in candidates))
        self.model.detect.assert_not_called()

    def test_failed_candidate_log_does_not_reuse_previous_similarity(self):
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]), np.array([1.0, 0.0]), ValueError('invalid image'),
        ]
        with self.assertLogs('embedding', level='INFO') as logs:
            self.filter.detect(self.image, [{'image_bgr': self.image}, {'image_bgr': None}])

        timings = [line for line in logs.output if 'processing--item=' in line]
        self.assertEqual(len(timings), 2)
        self.assertIn('--val=1.00', timings[0])
        self.assertIn('--val=N/A', timings[1])

    def test_similarity_equal_to_threshold_is_excluded(self):
        self.filter.threshold = 0.0
        self.model.detect.side_effect = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        self.assertEqual(self.filter.detect(self.image, [{'image_bgr': self.image}]), [])

    def test_invalid_candidate_does_not_discard_other_results(self):
        self.model.detect.side_effect = [
            np.array([1.0, 0.0]),
            TypeError('missing image'),
            ValueError('invalid image'),
            np.array([1.0, 0.0]),
        ]
        good = {'image_bgr': self.image}
        with self.assertLogs('embedding', level='WARNING'):
            result = self.filter.detect(self.image, [{}, {'image_bgr': None}, good])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {'similarity': 1.0})
        self.assertIs(good['image_bgr'], self.image)

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

        with patch.object(loader, '_download_image') as download:
            result = service.detect(encoded.tobytes())

        self.assertEqual(result, envelope)
        self.assertEqual(result['status'], 'upstream_empty_response')
        self.model.detect.assert_not_called()
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
