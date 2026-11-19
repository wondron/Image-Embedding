"""验证特征编码器的异常会立即抛出，并返回有效的归一化向量。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from app.clients.inference_onnx import OnnxEmbedding
from app.core import errors as err


class OnnxEmbeddingValidationTests(unittest.TestCase):
    def model_without_session(self):
        model = OnnxEmbedding.__new__(OnnxEmbedding)
        model.sess = None
        model.load_config({"EMBEDDING_CONFIG": {"model_path": "test.onnx"}})
        return model

    def assert_app_error(self, action, code="VALIDATION_ERROR", status=422):
        with self.assertRaises(err.AppError) as caught:
            action()
        self.assertEqual((caught.exception.code, caught.exception.http_status), (code, status))
        return caught.exception

    def test_invalid_config_is_rejected_before_loading_model(self):
        cases = [
            {},
            {"model_path": "test.onnx", "mean": [0.5, 0.5]},
            {"model_path": "test.onnx", "std": [0.2, 0.2]},
            {"model_path": "test.onnx", "std": [0.2, 0, 0.2]},
            {"model_path": "test.onnx", "std": [-0.2, 0.2, 0.2]},
            {"model_path": "test.onnx", "scale": []},
            {"model_path": "test.onnx", "scale": [224]},
            {"model_path": "test.onnx", "scale": [224, 0]},
            {"model_path": "test.onnx", "scale": [True, 224]},
            {"model_path": "test.onnx", "scale": "224"},
        ]
        for embedding_config in cases:
            with self.subTest(config=embedding_config), patch.object(OnnxEmbedding, "load_model") as load:
                self.assert_app_error(lambda: OnnxEmbedding({"EMBEDDING_CONFIG": embedding_config}))
                load.assert_not_called()

    def test_unsupported_interpolation_reports_the_actual_value(self):
        exc = self.assert_app_error(lambda: OnnxEmbedding._resolve_interpolation("unsupported-mode"))
        self.assertIn("unsupported-mode", exc.detail)
        self.assertNotIn("{value}", exc.detail)

    def test_missing_model_is_reported_before_creating_session(self):
        model = self.model_without_session()
        with patch("app.clients.inference_onnx.os.path.isfile", return_value=False), \
                patch("app.clients.inference_onnx.ort.InferenceSession") as create_session:
            exc = self.assert_app_error(
                lambda: model.load_model("missing-model.onnx"), "IMAGE_FILE_UNAVAILABLE", 502,
            )
        self.assertIn("missing-model.onnx", exc.detail)
        create_session.assert_not_called()

    def test_inference_without_a_session_reports_not_ready(self):
        model = self.model_without_session()
        self.assert_app_error(lambda: model.infer(np.zeros((2, 2, 3), dtype=np.uint8)), "NOT_READY", 503)

    def test_invalid_preprocessing_input_is_rejected(self):
        model = self.model_without_session()
        for image in (None, np.empty((0, 0, 3), dtype=np.uint8)):
            with self.subTest(image=image):
                self.assert_app_error(lambda: model.preprocess(image), "EMPTY_FILE", 400)
        for image in (np.zeros((2, 2), dtype=np.uint8), np.zeros((2, 2, 4), dtype=np.uint8)):
            with self.subTest(shape=image.shape):
                self.assert_app_error(lambda: model.preprocess(image))

    def test_unsupported_image_input_reports_type_without_running_inference(self):
        model = self.model_without_session()
        model.infer = Mock()
        exc = self.assert_app_error(lambda: model.detect("image.jpg"))
        self.assertIn("str", exc.detail)
        self.assertNotIn("{type(img_data)}", exc.detail)
        model.infer.assert_not_called()

    def test_invalid_outputs_are_rejected_with_a_validation_error(self):
        model = self.model_without_session()
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        outputs = [
            [3.0, 4.0],
            np.asarray(1.0),
            np.ones((2, 3)),
            np.ones((1, 2, 3)),
            np.asarray([np.nan, 1.0]),
            np.asarray([np.inf, 1.0]),
            np.zeros(3),
            np.asarray([1e-13, 0.0]),
        ]
        for output in outputs:
            with self.subTest(output=repr(output)):
                model.infer = Mock(return_value=output)
                self.assert_app_error(lambda: model.detect(image))

    def test_valid_outputs_return_a_float32_unit_vector(self):
        model = self.model_without_session()
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        for output in (np.asarray([3.0, 4.0]), np.asarray([[3.0, 4.0]])):
            with self.subTest(shape=output.shape):
                model.infer = Mock(return_value=output)
                embedding = model.detect(image)
                self.assertIsInstance(embedding, np.ndarray)
                self.assertEqual(embedding.shape, (2,))
                self.assertEqual(embedding.dtype, np.dtype(np.float32))
                np.testing.assert_allclose(embedding, [0.6, 0.8], atol=1e-7)
                self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
