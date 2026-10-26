import json
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapter.http.endpoints import predict
from app.domain.common.flybSearch import FlyBSearch


class PredictUpstreamErrorTests(unittest.TestCase):
    def setUp(self):
        self.search = FlyBSearch.__new__(FlyBSearch)
        self.search.url = 'https://search.invalid/global_search?token=private-token'
        self.search.key = 'private-api-key'
        self.search.doc_num = 5
        self.search.max_lenth = 300
        self.search.enable = False
        self.search.timeout = (10, 90)
        self.runtime = Mock()
        self.runtime.max_image_bytes = 15 * 1024 * 1024
        self.service = self.runtime.require_food_service.return_value
        self.service.detect.side_effect = lambda *args, **kwargs: self.search.get_result('private-image-base64')
        app = FastAPI()
        app.include_router(predict.router)
        self.client = TestClient(app)
        state_patch = patch.object(predict, 'get_application_state', return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.addCleanup(self.client.close)

    def upstream_response(self, status, body=b'<html>Bad Gateway</html>'):
        response = requests.Response()
        response.status_code = status
        response.url = self.search.url
        response.headers['X-Request-Id'] = 'controlled-upstream-request'
        response._content = body
        return response

    def upload(self):
        return self.client.post('/api/predict/searchImage_file',
                                files={'file': ('query.png', b'controlled-query', 'image/png')})

    def test_upstream_502_is_reported_as_502_with_safe_detail_and_request_log(self):
        with patch('app.domain.common.flybSearch.requests.post', return_value=self.upstream_response(502)) as post:
            with self.assertLogs('flyB', level='WARNING') as logs:
                response = self.upload()
        post.assert_called_once()
        self.assertEqual(response.status_code, 502)
        self.assertIn('global服务检索失败', response.json()['detail'])
        self.assertIn('HTTP 502', response.json()['detail'])
        self.assertIn('controlled-upstream-request', logs.output[0])
        for secret in ('private-api-key', 'private-image-base64', 'private-token'):
            self.assertNotIn(secret, response.text)
            self.assertNotIn(secret, str(logs.output))

    def test_gateway_statuses_are_preserved_without_retry(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                with patch('app.domain.common.flybSearch.requests.post', return_value=self.upstream_response(status)) as post:
                    response = self.upload()
                self.assertEqual(response.status_code, status)
                post.assert_called_once()

    def test_upstream_auth_failure_does_not_look_like_user_auth_failure(self):
        with patch('app.domain.common.flybSearch.requests.post', return_value=self.upstream_response(401)):
            response = self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertIn('HTTP 401', response.json()['detail'])

    def test_timeout_and_connection_failure_have_distinct_http_errors(self):
        for error, status, message in ((requests.Timeout('private-token'), 504, '响应超时'),
                                       (requests.ConnectionError('private-token'), 502, '无法连接')):
            with self.subTest(error=type(error)):
                with patch('app.domain.common.flybSearch.requests.post', side_effect=error) as post:
                    response = self.upload()
                self.assertEqual(response.status_code, status)
                self.assertIn(message, response.json()['detail'])
                self.assertNotIn('private-token', response.text)
                post.assert_called_once()

    def test_successful_http_response_with_invalid_json_is_a_gateway_error(self):
        with patch('app.domain.common.flybSearch.requests.post', return_value=self.upstream_response(200)):
            response = self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertIn('无效响应', response.json()['detail'])

    def test_successful_search_still_returns_normal_result(self):
        body = json.dumps({'Result': {'ErrorCode': 0, 'Documents': []}}).encode()
        with patch('app.domain.common.flybSearch.requests.post', return_value=self.upstream_response(200, body)) as post:
            response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result'], [])
        post.assert_called_once()

    def test_local_model_error_is_not_mislabeled_as_upstream_error(self):
        self.service.detect.side_effect = ValueError('controlled local failure')
        with self.assertLogs('svc.predict', level='ERROR'):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertIn('controlled local failure', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
