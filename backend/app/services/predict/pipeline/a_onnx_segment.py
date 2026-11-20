"""分割模型（SEGMENT_CONFIG）：每个进程一个 ONNX session，供 /api/tools/segment 使用。

app.bootstrap.lifespan 在启动时创建并预热它，放在 ApplicationState.segmentor。重新加载配置时，
SEGMENT_CONFIG 和 TOTAL_CONFIG.device_id 都没变就继续用这个实例；变了就先加载、预热新实例，
成功后再替换。加载失败直接抛出异常，由调用方记录原因。
"""

import os
import threading
import cv2
import time, logging
import numpy as np
import onnxruntime as ort
from typing import Any, Dict, Union
from app.utils.image import normalize_to_u8, overlay_mask

logger = logging.getLogger("segment")

# 模型输入的高宽是动态维度时用的预处理尺寸（现在的 multitask_seg.onnx 固定为 640x640）
DEFAULT_INPUT_SIZE = 640


def _static_dim(shape: list, index: int) -> int | None:
    value = shape[index] if len(shape) > index else None
    return value if isinstance(value, int) and value > 0 else None


def _to_bgr(img_data: Union[np.ndarray, bytes]) -> np.ndarray:
    """把编码后的图片字节或 BGR 数组变成 HWC 三通道 BGR；图片无效时抛出 ValueError。"""
    if isinstance(img_data, (bytes, bytearray, memoryview)):
        if not len(img_data):
            raise ValueError("输入图片为空")
        try:
            img_bgr = cv2.imdecode(np.frombuffer(img_data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError(f"图片解码失败: {exc}") from exc
    elif isinstance(img_data, np.ndarray):
        img_bgr = img_data
    else:
        raise TypeError(f"不支持的图片输入类型: {type(img_data)}")

    if img_bgr is None or img_bgr.size == 0:
        raise ValueError("图片解码失败或图片为空")
    if img_bgr.ndim != 3 or img_bgr.shape[2] != 3:
        raise ValueError(f"输入图片应为 HWC 三通道 BGR，实际为 {img_bgr.shape}")
    return img_bgr


class OnnxSegmentPy:
    def __init__( self, config ):
        self.sess: ort.InferenceSession | None = None
        self._cached_io = None
        self._cached_shape: tuple[int, ...] | None = None
        self._io_binding_lock = threading.Lock()
        self.load_config(config)
        self.load_model(self.model_path)
        logger.info("segment 模型初始化完成")


    def load_config(self, config: Dict[str, Any]) -> None:
        seg_config = config.get("SEGMENT_CONFIG") or {}
        self.model_path = seg_config.get("model_path")
        if not self.model_path:
            raise ValueError("SEGMENT_CONFIG.model_path 缺失")

        mean = seg_config.get("mean", [0.485, 0.456, 0.406])
        std = seg_config.get("std", [0.229, 0.224, 0.225])
        if len(mean) != 3 or len(std) != 3:
            raise ValueError("SEGMENT_CONFIG.mean / std 必须各包含 3 个值")
        self.mean = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
        self.std = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        if np.any(self.std <= 0):
            raise ValueError("SEGMENT_CONFIG.std 必须全部大于 0")

        self.use_gpu = bool(seg_config.get("use_gpu", False))
        self.use_iobinding = bool(seg_config.get("use_iobinding", False))     # 开启 iobinding
        self.resize_mode = seg_config.get("resize_mode", 'resize')
        # 和 embedding 模型一样取 TOTAL_CONFIG.device_id；CUDA provider 和 iobinding 用同一张卡
        self.device_id = int((config.get("TOTAL_CONFIG") or {}).get("device_id", 0))


    def load_model(self, model_path: str) -> None:
        """创建 ORT session；模型文件不存在或 session 创建失败时抛出异常。CUDA 不可用时回退到 CPU。"""
        started = time.perf_counter()
        logger.info("读取 segment 模型: %s", model_path)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"segment 模型文件不存在: {model_path}")

        # sess option 配置
        os.environ.setdefault("ORT_CUDA_UNAVAILABLE_TENSOR_OP_FALLBACK", "1")
        os.environ.setdefault("CUDA_DEVICE_MAX_CONNECTIONS", "1")

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.enable_mem_pattern = True
        so.enable_cpu_mem_arena = True
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.intra_op_num_threads = 1

        avail = ort.get_available_providers()
        use_cuda = self.use_gpu and "CUDAExecutionProvider" in avail
        if self.use_gpu and not use_cuda:
            logger.warning("CUDAExecutionProvider 不可用，segment 模型回退到 CPU | available=%s", avail)

        sess = None
        if use_cuda:
            cuda_options = {
                "device_id": self.device_id,
                "arena_extend_strategy": "kNextPowerOfTwo",
                "cudnn_conv_use_max_workspace": "1",
                "cudnn_conv1d_pad_to_nc1d": "1",
                "do_copy_in_default_stream": "1",
                "tunable_op_enable": "1",
                "tunable_op_tuning_enable": "1",
            }
            try:
                sess = ort.InferenceSession(
                    model_path,
                    sess_options=so,
                    providers=[("CUDAExecutionProvider", cuda_options), ("CPUExecutionProvider", {})],
                )
            except Exception as e:
                logger.warning("CUDA 会话创建失败，segment 模型回退到 CPU: %s", e)
        if sess is None:
            sess = ort.InferenceSession(model_path, sess_options=so, providers=[("CPUExecutionProvider", {})])

        # ---- 初始化 IO 信息 ----
        in_meta = sess.get_inputs()[0]
        shape = list(in_meta.shape)
        self.input_name = in_meta.name
        self.output_name = sess.get_outputs()[0].name
        # 动态尺寸的模型在这里定下预处理尺寸，推理时不再修改（多个线程共用这个实例）
        self.input_h = _static_dim(shape, 2) or DEFAULT_INPUT_SIZE
        self.input_w = _static_dim(shape, 3) or DEFAULT_INPUT_SIZE
        self._cached_io = None
        self._cached_shape = None
        self.sess = sess
        logger.info(
            "segment 模型加载完成 | path=%s | input=%s %s | size=%dx%d | output=%s | providers=%s | cost_ms=%.2f",
            model_path, self.input_name, shape, self.input_h, self.input_w, self.output_name,
            sess.get_providers(), (time.perf_counter() - started) * 1000,
        )


    def set_input_size(self, width: int, height: int) -> None:
        """当模型为动态尺寸时，可手动指定输入宽高"""
        self.input_w = int(width)
        self.input_h = int(height)


    def _supports_bind_ortvalue_input(self) -> bool:
        if self.sess is None:
            return False
        io = self.sess.io_binding()
        return hasattr(io, "bind_ortvalue_input")


    def preprocess(self, img_bgr: np.ndarray) -> np.ndarray:
        # 缩小时 INTER_AREA 更平滑，放大用 INTER_LINEAR
        inter = cv2.INTER_AREA if (img_bgr.shape[1] > self.input_w or img_bgr.shape[0] > self.input_h) else cv2.INTER_LINEAR
        img = cv2.resize(img_bgr, (self.input_w, self.input_h), interpolation=inter)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # BGR->RGB

        img = img.astype(np.float32) / 255.0
        img = (img - self.mean) / self.std                  # (H, W, C)
        img = np.transpose(img, (2, 0, 1))                  # (C, H, W)
        img = np.expand_dims(img, axis=0)                   # (1, C, H, W)
        return np.ascontiguousarray(img, dtype=np.float32)


    def warmup(self, runs: int = 3) -> None:
        """用模型输入尺寸的空白图跑完整的 detect（含后处理），输出形状不支持时在启动阶段就报错。"""
        runs = max(1, int(runs))
        logger.info("segment 模型预热 %d 次", runs)
        started = time.perf_counter()
        dummy = np.zeros((self.input_h, self.input_w, 3), dtype=np.uint8)
        for _ in range(runs):
            self.detect(dummy)
        logger.info("segment 模型预热完成，耗时 %.2fs", time.perf_counter() - started)

    # -------- 推理（统一在 GPU 绑定输入与输出，末尾一次回拷） ----------
    def infer(self, img_bgr: np.ndarray):
        if self.sess is None:
            raise RuntimeError("segment 模型尚未加载")
        if img_bgr is None or img_bgr.size == 0:
            raise ValueError("输入图像为空或读取失败")

        x = self.preprocess(img_bgr)
        providers = set(self.sess.get_providers() or [])
        using_cuda = "CUDAExecutionProvider" in providers
        use_iob = bool(self.use_iobinding and using_cuda and self._supports_bind_ortvalue_input())

        if use_iob:
            x_ov = ort.OrtValue.ortvalue_from_numpy(x, "cuda", self.device_id)

            # 缓存的绑定及其输出同一时间只属于一次推理。
            with self._io_binding_lock:
                need_rebind = True
                if self._cached_io is not None and self._cached_shape == tuple(x.shape):
                    try:
                        io = self._cached_io
                        io.bind_ortvalue_input(self.input_name, x_ov)
                        need_rebind = False
                    except Exception:
                        need_rebind = True

                if need_rebind:
                    io = self.sess.io_binding()
                    io.bind_ortvalue_input(self.input_name, x_ov)
                    for o in self.sess.get_outputs():
                        io.bind_output(o.name, "cuda", self.device_id)   # 统一绑定到 CUDA
                    self._cached_io = io
                    self._cached_shape = tuple(x.shape)

                self.sess.run_with_iobinding(self._cached_io)
                outs = self._cached_io.copy_outputs_to_cpu()  # 末尾一次性回拷到 CPU
            name2arr = {o.name: arr for o, arr in zip(self.sess.get_outputs(), outs)}
            return name2arr.get(self.output_name, outs[0])

        # CPU 回退路径
        return self.sess.run([self.output_name], {self.input_name: x})[0]


    def detect(self, img_data: Union[np.ndarray, bytes], output_vis: bool = False):
        """返回 (mask, visual)。

        img_data：编码后的图片字节或 BGR 数组。mask：和原图同尺寸的 uint8 单通道图，多类别输出是
        每个像素的类别号，单通道输出是归一化到 0～255 的概率图；visual：output_vis 为 True 时是
        叠加了 mask 的 BGR 图，否则为 None。图片无效时抛出 ValueError，模型输出不对时抛出 RuntimeError。
        """
        img_bgr = _to_bgr(img_data)
        # ---------- 推理 ----------
        H, W = img_bgr.shape[:2]
        out = self.infer(img_bgr)
        if not isinstance(out, np.ndarray):
            raise RuntimeError(f"segment 模型输出类型异常: {type(out)}")

        # ---------- 后处理 ----------
        if out.ndim == 4:
            n, c, h, w = out.shape
            if c > 1:
                mask = np.argmax(out, axis=1)[0].astype(np.uint8)
                mask = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
            else:
                m = out[0, 0]
                mask = cv2.resize(normalize_to_u8(m), (W, H), interpolation=cv2.INTER_NEAREST)
        elif out.ndim == 3:
            c_or_n, h, w = out.shape
            if c_or_n > 1:
                mask = np.argmax(out, axis=0).astype(np.uint8)
                mask = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
            else:
                m = out[0]
                mask = cv2.resize(normalize_to_u8(m), (W, H), interpolation=cv2.INTER_NEAREST)
        elif out.ndim == 2:
            mask = cv2.resize(normalize_to_u8(out), (W, H), interpolation=cv2.INTER_NEAREST)
        else:
            raise RuntimeError(f"segment 模型输出维度不支持: {out.shape}")

        visual = overlay_mask(img_bgr, mask, 0.3) if output_vis else None
        return mask, visual



if __name__ == "__main__":
    import sys
    from app.core.config.store import get_cfg
    from app.core.observability.logging import setup_logger

    setup_logger()
    config = get_cfg()
    seg = OnnxSegmentPy(config)
    seg.warmup()
    img = cv2.imread(sys.argv[1] if len(sys.argv) > 1 else "config/images/segment_0902_0892.jpg")
    for i in range(10):
        t0 = time.time()
        mask, vis = seg.detect(img, True)
        print("mask:", mask.shape, "time(s):", round(time.time() - t0, 4))
    logger.info("msk size: %s, vis size: %s, ori size: %s", mask.shape, vis.shape, img.shape)
    cv2.imwrite("01-msk.jpg", mask * 255 if mask.max() <= 1 else mask)
    cv2.imwrite("00-vis.jpg", vis)
