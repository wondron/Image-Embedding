import os
import cv2
import time, logging
import numpy as np
import onnxruntime as ort
from typing import Optional, Tuple, Union, List, Dict, Any
from app.domain.common.image_utils import normalize_to_u8, overlay_mask

logger = logging.getLogger("segment")


class OnnxSegmentPy:
    def __init__( self, config ):
        self.load_config(config)
        self.load_model(self.model_path)
        logger.info("模型初始化完成！")


    def load_config(self, config: Dict[str, Any]) -> None:
        seg_config = config.get("SEGMENT_CONFIG", {})
        mean = seg_config.get("mean", [0.485, 0.456, 0.406])
        std = seg_config.get("std", [0.229, 0.224, 0.225])

        self.mean = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
        self.std = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        self.use_gpu = seg_config.get("use_gpu", False)
        self.model_path = seg_config.get("model_path")
        self.use_iobinding = seg_config.get("use_iobinding", False)     # 开启 iobinding
        self.resize_mode = seg_config.get("resize_mode", 'resize') 



    def load_model(self, model_path: str) -> None:
        try:
            logger.info("读取模型: %s", model_path)
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"模型文件不存在: {model_path}")

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
            logger.info("onnxruntime 可用 provider: %s", avail)

            # 先构造一个“理想”的 provider 列表
            cuda_options = {
                "device_id": 0,
                "arena_extend_strategy": "kNextPowerOfTwo",
                "cudnn_conv_use_max_workspace": "1",
                "cudnn_conv1d_pad_to_nc1d": "1",
                "do_copy_in_default_stream": "1",
                "tunable_op_enable": "1",
                "tunable_op_tuning_enable": "1",
            }

            use_cuda = ("CUDAExecutionProvider" in avail) and self.use_gpu

            # 1. 优先尝试 CUDA + CPU
            if use_cuda:
                logger.info("------使用 CUDAExecutionProvider 加速-------")
                providers_try = [("CUDAExecutionProvider", cuda_options), ("CPUExecutionProvider", {})]
                logger.info("尝试使用 CUDAExecutionProvider + CPUExecutionProvider: %s", providers_try)
                try:
                    self.sess = ort.InferenceSession(
                        model_path,
                        sess_options=so,
                        providers=providers_try,
                    )
                    self.providers = providers_try
                except Exception as e:
                    # 这里很关键：不要让异常把服务干死，回落到 CPU
                    logger.warning("CUDAExecutionProvider 初始化失败，将回退到 CPUExecutionProvider。错误: %s", e)
                    use_cuda = False  # 标记 CUDA 不可用

            # 2. 如果没用 CUDA 或 CUDA 失败，则走纯 CPU
            if not use_cuda:
                providers_cpu = [("CPUExecutionProvider", {})]
                logger.info("使用 CPUExecutionProvider: %s", providers_cpu)
                self.sess = ort.InferenceSession(
                    model_path,
                    sess_options=so,
                    providers=providers_cpu,
                )
                self.providers = providers_cpu

            # ---- 初始化 IO 信息 ----
            input_names = [i.name for i in self.sess.get_inputs()]
            output_names = [o.name for o in self.sess.get_outputs()]
            self.input_name = input_names[0]
            self.output_name = output_names[0]
            logger.info("模型输入输出名称: %s, %s", self.input_name, self.output_name)

            in_meta = self.sess.get_inputs()[0]
            shape = list(in_meta.shape)
            self.input_h = shape[2] if len(shape) >= 4 and isinstance(shape[2], int) and shape[2] > 0 else None
            self.input_w = shape[3] if len(shape) >= 4 and isinstance(shape[3], int) and shape[3] > 0 else None

            self._cached_io = None
            self._cached_shape = None
            logger.info("segment 模型加载完毕，输入尺寸: H=%s, W=%s", self.input_h, self.input_w)
        except Exception as e:
            logger.error("模型加载失败: %s", e)


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
        if self.input_w is None or self.input_h is None:
            # 保险：没设就给个默认
            self.input_w, self.input_h = 512, 512

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
        logger.info('开始加热模型！！！')
        t0 = time.time()
        if self.input_w is None or self.input_h is None:
            self.set_input_size(640, 640)
        dummy = np.zeros((self.input_h, self.input_w, 3), dtype=np.uint8)
        for _ in range(max(1, runs)):
            _ = self.infer(dummy)
        logger.info("segment 加速器预热完成，耗时 %.2fs", time.time() - t0)

    # -------- 推理（统一在 GPU 绑定输入与输出，末尾一次回拷） ----------
    def infer(self, img_bgr: np.ndarray):
        if self.sess is None:
            raise RuntimeError("请先调用 load_model()")
        if img_bgr is None or img_bgr.size == 0:
            raise ValueError("输入图像为空或读取失败")

        x = self.preprocess(img_bgr)
        providers = set(self.sess.get_providers() or [])
        using_cuda = "CUDAExecutionProvider" in providers
        # logger.info("infer 使用的 providers=%s, using_cuda=%s", providers, using_cuda)
        use_iob = bool(self.use_iobinding and using_cuda and self._supports_bind_ortvalue_input())

        if use_iob:
            gpu_id = int(os.environ.get("ORT_CUDA_DEVICE_ID", "0"))
            x_ov = ort.OrtValue.ortvalue_from_numpy(x, "cuda", gpu_id)

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
                    io.bind_output(o.name, "cuda", gpu_id)   # 统一绑定到 CUDA
                self._cached_io = io
                self._cached_shape = tuple(x.shape)

            self.sess.run_with_iobinding(self._cached_io)
            outs = self._cached_io.copy_outputs_to_cpu()  # 末尾一次性回拷到 CPU
            name2arr = {o.name: arr for o, arr in zip(self.sess.get_outputs(), outs)}
            return name2arr.get(self.output_name, outs[0])

        # CPU 回退路径
        return self.sess.run([self.output_name], {self.input_name: x})[0]

    
    def detect(self, img_data: np.ndarray, output_vis: bool = False):
        img_bgr = img_data
        # ---------- 推理 ----------
        H, W = img_bgr.shape[:2]
        out = self.infer(img_bgr)
        if not isinstance(out, np.ndarray):
            raise TypeError(f"模型输出类型异常: {type(out)}")

        # ---------- 后处理 ----------
        if out.ndim == 4:
            n, c, h, w = out.shape
            if c > 1:
                mask = np.argmax(out, axis=1)[0].astype(np.uint8)
                vis = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
            else:
                m = out[0, 0]
                vis = cv2.resize(normalize_to_u8(m), (W, H), interpolation=cv2.INTER_NEAREST)
        elif out.ndim == 3:
            c_or_n, h, w = out.shape
            if c_or_n > 1:
                mask = np.argmax(out, axis=0).astype(np.uint8)
                vis = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
            else:
                m = out[0]
                vis = cv2.resize(normalize_to_u8(m), (W, H), interpolation=cv2.INTER_NEAREST)
        elif out.ndim in (1, 2):
            vis = np.squeeze(out).astype(np.float32)
            vis = cv2.resize(normalize_to_u8(vis), (W, H), interpolation=cv2.INTER_NEAREST)
        else:
            raise ValueError(f"不支持的输出维度: {out.shape}")
        
        visual = overlay_mask(img_bgr, vis, 0.3) if output_vis else None
        return vis, visual



if __name__ == "__main__":
    from app.core.config.store import get_cfg
    from app.core.observability.logging import setup_logger
    
    setup_logger()
    config = get_cfg()
    seg = OnnxSegmentPy(config)
    img = cv2.imread("/data/wangzhuo/00-dataset/03-chengshudu/chengshudu/segment/segment_0902_0892.jpg")
    for i in range(10):
        t0 = time.time()
        mask, vis = seg.detect(img, True)
        print("mask:", mask.shape, "time(s):", round(time.time() - t0, 4))
        # break
    logger.info(f"msk size: {mask.shape}, vis size: {vis.shape}, ori size: {bgr_ori.shape}")
    cv2.imwrite("01-msk.jpg", mask)
    cv2.imwrite("00-vis.jpg", vis)

    

