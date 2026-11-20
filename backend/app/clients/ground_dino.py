import logging
import threading, time
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Union
from app.core import errors as err
from app.utils.timer import timer_tool
import numpy as np
from app.clients.groundingdino.postprocess import postprocess
from app.clients.groundingdino.preprocess import prepare_inputs
from app.clients.groundingdino.tokenizer import BertTokenizer
import onnxruntime as ort


logger = logging.getLogger("g-dino")


class GdinoDetect:
    def __init__(self, config):
        self.sess: ort.InferenceSession | None = None
        self._cached_io = None
        self._cached_shape: tuple[int, ...] | None = None
        self.load_config(config)
        self.load_model()
        logger.info("g-dino 模型初始化完成！")
    
    
    def load_config(self, config: Dict[str, Any]) -> None:
        total_config = config.get('TOTAL_CONFIG', {})
        cfg = config.get('GROUNDINGDINO_CONFIG', {}) 
        
        self.model_path = cfg["model_path"]
        self.tokenizer_path = cfg["tokenizer_path"]
        self.use_gpu = bool(cfg.get("use_gpu", True))
        self.device_id = total_config.get("device_id", 0)
        self.threads = cfg.get("threads", 4)
        self.short_side = cfg.get("short_side", 800)
        self.max_size = cfg.get("max_size", 1333)
        self.max_text_len = cfg.get("max_text_len", 256)
        self.mean = tuple(cfg.get("mean", (0.485, 0.456, 0.406)))
        self.std = tuple(cfg.get("std", (0.229, 0.224, 0.225)))
        self.caption = cfg.get("caption", "object .")
        self.box_threshold = cfg.get("box_threshold", 0.35)
        self.text_threshold = cfg.get("text_threshold", 0.25)
        self.nms_threshold = cfg.get("nms_threshold", 0.5)
        self.nms_agnostic = cfg.get("nms_agnostic", False)
        # NMS 之后去掉「容器框」：装着至少 container_min_children 个更小的框、这些小框又盖住它至少
        # container_min_coverage 的大框（见 drop_containers）；container_min_children 为 0 关闭
        self.container_min_children = int(cfg.get("container_min_children", 2))
        self.container_contain_ratio = float(cfg.get("container_contain_ratio", 0.9))
        self.container_min_coverage = float(cfg.get("container_min_coverage", 0.5))
        if self.container_min_children < 0:
            raise ValueError("GROUNDINGDINO_CONFIG.container_min_children 不能小于 0（0 表示不去掉大框）")
        if not 0 < self.container_contain_ratio <= 1:
            raise ValueError("GROUNDINGDINO_CONFIG.container_contain_ratio 要在 0～1 之间（不含 0）")
        if not 0 <= self.container_min_coverage <= 1:
            raise ValueError("GROUNDINGDINO_CONFIG.container_min_coverage 要在 0～1 之间")
        self.lock = threading.Lock()


    def load_model(self):
        started = perf_counter()
        self.tokenizer = BertTokenizer(self.tokenizer_path)
        self.sess = create_session(self.model_path, "cuda" if self.use_gpu else "cpu", self.threads, device_id=self.device_id)
        self.provider = "cuda" if self.sess.get_providers()[0] == "CUDAExecutionProvider" else "cpu"
        self.input_shapes = {item.name: item.shape for item in self.sess.get_inputs()}
        self.ini_flag = True
        logger.info("模型加载完成 | path=%s | providers=%s | cost_ms=%.2f", self.model_path, self.sess.get_providers(), (perf_counter() - started) * 1000)


    def warmup(self, runs=3) -> None:
        logger.info("g-dino 模型预热 %d 次", max(1, runs))
        started_at = time.time()
        _, _, h, w = self.input_shapes["images"]
        h = h if isinstance(h, int) and h > 0 else self.short_side
        w = w if isinstance(w, int) and w > 0 else self.short_side
        dummy = np.zeros((h, w, 3), dtype=np.uint8)
        for _ in range(max(1, runs)):
            self.detect(dummy)
        logger.info("g-dino 模型预热完成，耗时 %.2fs", time.time() - started_at)


    def prepare_caption(self, text_range):
        """detect 用的 caption：None 时取 GROUNDINGDINO_CONFIG.caption；字符串原样使用（去掉首尾空白）；
        类别列表用「 . 」连起来，末尾加「 .」。
        """
        if text_range is None:
            return self.caption
        if isinstance(text_range, str):
            return text_range.strip()
        return " . ".join(t.strip().rstrip(".").strip() for t in text_range) + " ."

    def preprocess(self, image, caption):
        """从 BGR 数组、RGB PIL 图像或图片路径构建计算图的输入。"""
        return prepare_inputs(
            image, caption, self.tokenizer, self.input_shapes,
            short_side=self.short_side, max_size=self.max_size, max_text_len=self.max_text_len,
            mean=self.mean, std=self.std,
        )

    def detect(self, imgbgr, text_range=None, box_threshold=None, text_threshold=None):
        if self.sess is None:
            raise err.not_ready("g-dino 模型尚未加载！")
        result, _ = self.predict_with_metadata(
            imgbgr, self.prepare_caption(text_range),
            self.box_threshold if box_threshold is None else box_threshold,
            self.text_threshold if text_threshold is None else text_threshold,
        )
        boxes = np.asarray(result["boxes"], dtype=np.float32).reshape(-1, 4)
        logits = np.asarray(result["logits"], dtype=np.float32)
        phrases = result["phrases"]

        iou = self.nms_threshold
        if iou is not None and iou < 1 and len(boxes) > 1:
            keep = nms(boxes, logits, iou, labels=None if self.nms_agnostic else phrases)
            boxes, logits, phrases = boxes[keep], logits[keep], [phrases[i] for i in keep]
        if self.container_min_children and len(boxes) > self.container_min_children:
            keep = drop_containers(boxes, self.container_min_children, self.container_contain_ratio, self.container_min_coverage)
            if len(keep) < len(boxes):
                logger.info("去掉容器框 | dropped=%d | kept=%d | min_children=%d | contain_ratio=%.2f | min_coverage=%.2f",
                            len(boxes) - len(keep), len(keep), self.container_min_children,
                            self.container_contain_ratio, self.container_min_coverage)
                boxes, logits, phrases = boxes[keep], logits[keep], [phrases[i] for i in keep]
        return boxes, logits, phrases

    def predict(self, image_path, caption, box_threshold, text_threshold):
        """HTTP 入口（保留原来不校验阈值的策略，不做 NMS）。"""
        result, _ = self.predict_with_metadata(image_path, caption, box_threshold, text_threshold, validate_thresholds=False)
        return result

    def predict_with_metadata(self, image, caption, box_threshold, text_threshold, *, validate_thresholds=True):
        """为 HTTP / 命令行调用方返回（检测结果, 预处理元数据）（不做 NMS）。"""
        timer = timer_tool()
        with self.lock:
            feeds, metadata = self.preprocess(image, caption)
            timer.mark("pres")
            logits, boxes = self.sess.run(["pred_logits", "pred_boxes"], feeds)
            timer.mark("infer")
            result = postprocess(logits, boxes, self.tokenizer, metadata, box_threshold, text_threshold, validate_thresholds=validate_thresholds)
            timer.mark("post")
        time_string = timer.summary()
        logger.info("推理完成 | image=%s | detections=%d | %s", feeds["images"].shape, len(result["boxes"]), time_string)
        return result, metadata


def nms(boxes, scores, iou_threshold, labels=None):
    boxes = np.asarray(boxes, dtype=np.float64)
    lt = boxes[:, :2] - boxes[:, 2:] / 2
    rb = boxes[:, :2] + boxes[:, 2:] / 2
    area = np.prod(np.clip(rb - lt, 0, None), axis=1)
    labels = None if labels is None else np.asarray(labels, dtype=object)
    order = np.argsort(-np.asarray(scores), kind="stable")
    keep = []
    while order.size:
        i, order = order[0], order[1:]
        keep.append(i)
        wh = np.clip(np.minimum(rb[i], rb[order]) - np.maximum(lt[i], lt[order]), 0, None)
        inter = wh[:, 0] * wh[:, 1]
        suppress = inter / np.maximum(area[i] + area[order] - inter, 1e-12) > iou_threshold
        if labels is not None:
            suppress &= labels[order] == labels[i]
        order = order[~suppress]
    return np.sort(np.asarray(keep, dtype=np.int64))


def _union_area(rects):
    """几个矩形（x1, y1, x2, y2）合起来的面积，重叠的部分只算一次（坐标压缩，精确）。"""
    xs = np.unique(rects[:, [0, 2]])
    ys = np.unique(rects[:, [1, 3]])
    if len(xs) < 2 or len(ys) < 2:
        return 0.0
    cx = (xs[:-1] + xs[1:]) / 2  # 格子中心
    cy = (ys[:-1] + ys[1:]) / 2
    in_x = (rects[:, 0:1] <= cx) & (cx < rects[:, 2:3])
    in_y = (rects[:, 1:2] <= cy) & (cy < rects[:, 3:4])
    covered = (in_x[:, :, None] & in_y[:, None, :]).any(axis=0)
    return float((covered * np.diff(xs)[:, None] * np.diff(ys)[None, :]).sum())


def container_stats(boxes, contain_ratio=0.9):
    """每个框里装着几个更小的框，以及这些小框（裁到框内的部分）合起来盖住它的比例。

    小框有 ≥ contain_ratio 的面积落在框里，才算装在里面。boxes：归一化的 cx, cy, w, h
    （比例和像素坐标下一样）。返回（个数, 覆盖比例）两个数组。
    """
    boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    lt = boxes[:, :2] - boxes[:, 2:] / 2
    rb = boxes[:, :2] + boxes[:, 2:] / 2
    area = np.prod(np.clip(rb - lt, 0, None), axis=1)
    wh = np.clip(np.minimum(rb[:, None], rb[None]) - np.maximum(lt[:, None], lt[None]), 0, None)
    inter = wh[..., 0] * wh[..., 1]  # inter[i, j]：框 i 和框 j 重叠的面积
    child = area[None, :]
    # inside[i, j]：框 j 比框 i 小，且 ≥ contain_ratio 的面积在框 i 里（对角线上 area 不比自己小，自然是 False）
    inside = (child > 0) & (child < area[:, None]) & (inter >= contain_ratio * child)
    counts = inside.sum(axis=1)
    coverage = np.zeros(len(boxes))
    for i in np.flatnonzero(counts):
        rects = np.concatenate([np.maximum(lt[inside[i]], lt[i]), np.minimum(rb[inside[i]], rb[i])], axis=1)
        coverage[i] = _union_area(rects) / area[i]
    return counts, coverage


def drop_containers(boxes, min_children=2, contain_ratio=0.9, min_coverage=0.5):
    """去掉「容器框」之后留下的框的下标（从小到大）。

    容器框：装着至少 min_children 个更小的框（见 container_stats），而且这些小框合起来盖住它
    至少 min_coverage 的框。它多半是把一组目标框在了一起（一整堆、一整盘），裁出来和整张图差不多，
    留下里面的小框。小框只盖住一小部分的大框留着：那是一个大目标，小框是它上面的配菜、点缀之类。
    所有容器框按原来的框一起判定、一起去掉，套在一起的几层大框都会去掉。min_children 为 0 时
    什么都不去掉。
    """
    boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    if min_children < 1 or len(boxes) <= min_children:
        return np.arange(len(boxes))
    counts, coverage = container_stats(boxes, contain_ratio)
    containers = (counts >= min_children) & (coverage >= min_coverage)
    return np.flatnonzero(~containers)


def create_session(model, provider="cpu", intra_op_num_threads=0, *, device_id=0, log_severity_level=None):
    if log_severity_level is None:
        from app.core.config.constant import ORT_LOG_SEVERITY_LEVEL
        log_severity_level = ORT_LOG_SEVERITY_LEVEL
    ort.set_default_logger_severity(log_severity_level)

    options = ort.SessionOptions()
    options.log_severity_level = log_severity_level
    if intra_op_num_threads:
        options.intra_op_num_threads = intra_op_num_threads

    providers = ["CPUExecutionProvider"]
    if provider == "cuda":
        providers.insert(0, ("CUDAExecutionProvider", {"device_id": str(device_id), "use_tf32": "0"}))
    session = ort.InferenceSession(str(model), sess_options=options, providers=providers)
    if provider == "cuda" and session.get_providers()[0] != "CUDAExecutionProvider":
        logger.warning("CUDA 不可用，已回退到 CPU 推理 | active=%s", session.get_providers())
    return session


if __name__ == "__main__":
    from app.core.config.loader import load_config
    from app.core.observability.logging import setup_logger

    setup_logger()
    config = load_config()
    detector = GdinoDetect(config)
    image = config["GROUNDINGDINO_CONFIG"].get("test_image_path", "config/images/test.jpg")
    # logger.info("检测结果：%s", detector.detect(_project_path(image)))
