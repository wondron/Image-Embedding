from __future__ import annotations

import logging, os, time, cv2
from typing import Any, Dict, Union
import numpy as np
import onnxruntime as ort

logger = logging.getLogger("embedding")

class OnnxEmbedding:
    def __init__(self, config: Dict[str, Any]):
        self.sess: ort.InferenceSession | None = None
        self._cached_io = None
        self._cached_shape: tuple[int, ...] | None = None
        self.load_config(config)
        self.load_model(self.model_path)
        logger.info("embedding 模型初始化完成")

    def load_config(self, config: Dict[str, Any]) -> None:
        total_config = config.get('TOTAL_CONFIG', {})
        embedding_config = config.get("EMBEDDING_CONFIG", {})

        self.model_path = embedding_config.get("model_path")
        if not self.model_path:
            raise ValueError("EMBEDDING_CONFIG.model_path 缺失")

        mean = embedding_config.get("mean", [0.485, 0.456, 0.406])
        std = embedding_config.get("std", [0.229, 0.224, 0.225])
        if len(mean) != 3 or len(std) != 3:
            raise ValueError("EMBEDDING_CONFIG.mean/std 必须各包含 3 个值")

        self.mean = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
        self.std = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        if np.any(self.std <= 0):
            raise ValueError("EMBEDDING_CONFIG.std 必须全部大于 0")

        scale = embedding_config.get("scale", [224, 224])
        if (
            not isinstance(scale, (list, tuple))
            or len(scale) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in scale)
        ):
            raise ValueError("EMBEDDING_CONFIG.scale 必须是 [height, width] 两个正整数")

        self.input_h = int(scale[0])
        self.input_w = int(scale[1])
        self.interpolation = self._resolve_interpolation(embedding_config.get("img_interp", "cubic"))
        self.output_name = str(embedding_config.get("output_name", "cls_emb"))
        self.use_gpu = bool(embedding_config.get("use_gpu", False))
        self.device_id = int(total_config.get("device_id", 0))
        self.use_iobinding = bool(embedding_config.get("use_iobinding", False))

    @staticmethod
    def _resolve_interpolation(value: Any) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value

        name = str(value).strip().lower()
        interpolation_map = {
            "nearest": cv2.INTER_NEAREST,
            "linear": cv2.INTER_LINEAR,
            "bilinear": cv2.INTER_LINEAR,
            "area": cv2.INTER_AREA,
            "cubic": cv2.INTER_CUBIC,
            "bicubic": cv2.INTER_CUBIC,
            "lanczos": cv2.INTER_LANCZOS4,
            "lanczos4": cv2.INTER_LANCZOS4,
        }
        if name not in interpolation_map:
            raise ValueError(f"不支持的 EMBEDDING_CONFIG.img_interp: {value}")
        return interpolation_map[name]

    def load_model(self, model_path: str) -> None:
        logger.info("加载 embedding 模型: %s", model_path)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"embedding 模型不存在: {model_path}")

        os.environ.setdefault("ORT_CUDA_UNAVAILABLE_TENSOR_OP_FALLBACK", "1")
        os.environ.setdefault("CUDA_DEVICE_MAX_CONNECTIONS", "1")
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session_options.enable_mem_pattern = True
        session_options.enable_cpu_mem_arena = True
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session_options.intra_op_num_threads = 1
        available = ort.get_available_providers()
        use_cuda = self.use_gpu and "CUDAExecutionProvider" in available

        if self.use_gpu and not use_cuda:
            logger.warning("CUDAExecutionProvider 不可用，embedding 模型回退到 CPU")

        if use_cuda:
            cuda_options = {
                "device_id": self.device_id,
                "arena_extend_strategy": "kNextPowerOfTwo",
                "cudnn_conv_use_max_workspace": "1",
                "do_copy_in_default_stream": "1",
            }
            try:
                self.sess = ort.InferenceSession(
                    model_path,
                    sess_options=session_options,
                    providers=[
                        ("CUDAExecutionProvider", cuda_options),
                        ("CPUExecutionProvider", {}),
                    ],
                )
            except Exception as exc:
                logger.warning("CUDA 会话创建失败，embedding 模型回退到 CPU: %s", exc)

        if self.sess is None:
            self.sess = ort.InferenceSession(model_path, sess_options=session_options, providers=[("CPUExecutionProvider", {})],)

        inputs = self.sess.get_inputs()
        if len(inputs) != 1:
            raise ValueError(f"embedding ONNX 应只有一个输入，实际为 {len(inputs)} 个")
        input_meta = inputs[0]
        if input_meta.type != "tensor(float)":
            raise ValueError(f"embedding ONNX 输入类型应为 tensor(float)，实际为 {input_meta.type}")
        if len(input_meta.shape) != 4:
            raise ValueError(f"embedding ONNX 输入应为 NCHW，实际为 {input_meta.shape}")
        if isinstance(input_meta.shape[1], int) and input_meta.shape[1] != 3:
            raise ValueError(f"embedding ONNX 输入通道数应为 3，实际为 {input_meta.shape[1]}")

        self.input_name = input_meta.name
        if isinstance(input_meta.shape[2], int) and input_meta.shape[2] > 0:
            self.input_h = input_meta.shape[2]
        if isinstance(input_meta.shape[3], int) and input_meta.shape[3] > 0:
            self.input_w = input_meta.shape[3]

        output_names = [output.name for output in self.sess.get_outputs()]
        if self.output_name not in output_names:
            raise ValueError(f"embedding ONNX 缺少输出 {self.output_name!r}，实际输出为 {output_names}")

        self._cached_io = None
        self._cached_shape = None
        logger.info(
            "embedding 模型加载成功: input=%s, output=%s, size=%sx%s, providers=%s",
            self.input_name, self.output_name, self.input_h, self.input_w, self.sess.get_providers(),
        )

    def set_input_size(self, width: int, height: int) -> None:
        """Set the preprocessing size for a dynamic-input ONNX model."""
        if width <= 0 or height <= 0:
            raise ValueError("embedding 输入宽高必须大于 0")
        self.input_w = int(width)
        self.input_h = int(height)

    def _supports_bind_ortvalue_input(self) -> bool:
        if self.sess is None:
            return False
        return hasattr(self.sess.io_binding(), "bind_ortvalue_input")

    def preprocess(self, img_bgr: np.ndarray) -> np.ndarray:
        if img_bgr is None or img_bgr.size == 0:
            raise ValueError("输入图片为空")
        if img_bgr.ndim != 3 or img_bgr.shape[2] != 3:
            raise ValueError(f"输入图片应为 HWC 三通道 BGR，实际为 {img_bgr.shape}")

        image_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        image_rgb = cv2.resize(image_rgb, (self.input_w, self.input_h), interpolation=self.interpolation,)
        image_rgb = image_rgb.astype(np.float32) / 255.0
        image_rgb = (image_rgb - self.mean) / self.std
        image_chw = np.transpose(image_rgb, (2, 0, 1))[None]
        return np.ascontiguousarray(image_chw, dtype=np.float32)

    def warmup(self, runs: int = 3) -> None:
        logger.info("embedding 模型预热 %d 次", max(1, runs))
        started_at = time.time()
        dummy = np.zeros((self.input_h, self.input_w, 3), dtype=np.uint8)
        for _ in range(max(1, runs)):
            self.infer(dummy)
        logger.info("embedding 模型预热完成，耗时 %.2fs", time.time() - started_at)

    def infer(self, img_bgr: np.ndarray) -> np.ndarray:
        if self.sess is None:
            raise RuntimeError("embedding 模型尚未加载")

        model_input = self.preprocess(img_bgr)
        using_cuda = "CUDAExecutionProvider" in (self.sess.get_providers() or [])
        use_iobinding = bool( self.use_iobinding and using_cuda and self._supports_bind_ortvalue_input() )

        if use_iobinding:
            input_ortvalue = ort.OrtValue.ortvalue_from_numpy( model_input, "cuda", self.device_id)
            need_rebind = True
            if self._cached_io is not None and self._cached_shape == tuple(model_input.shape):
                try:
                    self._cached_io.bind_ortvalue_input(self.input_name, input_ortvalue)
                    need_rebind = False
                except Exception:
                    need_rebind = True

            if need_rebind:
                io_binding = self.sess.io_binding()
                io_binding.bind_ortvalue_input(self.input_name, input_ortvalue)
                io_binding.bind_output(self.output_name, "cuda", self.device_id)
                self._cached_io = io_binding
                self._cached_shape = tuple(model_input.shape)

            self.sess.run_with_iobinding(self._cached_io)
            return self._cached_io.copy_outputs_to_cpu()[0]

        return self.sess.run( [self.output_name], {self.input_name: model_input}, )[0]

    def detect(self, img_data: Union[np.ndarray, bytes]) -> np.ndarray:
        """Return one L2-normalized global embedding with shape ``(C,)``."""
        if isinstance(img_data, bytes):
            image_buffer = np.frombuffer(img_data, dtype=np.uint8)
            img_bgr = cv2.imdecode(image_buffer, cv2.IMREAD_COLOR)
        elif isinstance(img_data, np.ndarray):
            img_bgr = img_data
        else:
            raise TypeError(f"不支持的图片输入类型: {type(img_data)}")

        if img_bgr is None or img_bgr.size == 0:
            raise ValueError("图片解码失败或图片为空")

        output = self.infer(img_bgr)
        if not isinstance(output, np.ndarray):
            raise TypeError(f"embedding ONNX 输出类型错误: {type(output)}")
        if output.ndim == 2 and output.shape[0] == 1:
            embedding = output[0]
        elif output.ndim == 1:
            embedding = output
        else:
            raise ValueError(f"全局 embedding 输出形状应为 (1, C) 或 (C,)，实际为 {output.shape}")

        embedding = np.asarray(embedding, dtype=np.float32)
        if not np.isfinite(embedding).all():
            raise ValueError("全局 embedding 包含 NaN 或 Inf")

        norm = float(np.linalg.norm(embedding))
        if norm <= 1e-12:
            raise ValueError("全局 embedding 是零向量")
        return np.ascontiguousarray(embedding / norm, dtype=np.float32)


if __name__ == "__main__":
    import argparse

    from app.core.config.store import get_cfg
    from app.core.observability.logging import setup_logger

    parser = argparse.ArgumentParser(description="Generate a global image embedding")
    parser.add_argument("image", help="图片路径")
    args = parser.parse_args()

    setup_logger()
    embedding_model = OnnxEmbedding(get_cfg())
    image = cv2.imread(args.image, cv2.IMREAD_COLOR)
    vector = embedding_model.detect(image)
    logger.info("embedding shape=%s, norm=%.6f", vector.shape, np.linalg.norm(vector))
