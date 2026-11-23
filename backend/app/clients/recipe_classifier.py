"""菜谱识别模型（RECIPE_DETECT_CONFIG）：一张菜品图 -> 底库里和它最相近的菜品，以及余弦相似度。

模型和底库在 config/model/recipe（robam_recipe_classifier.py 是模型自带的独立推理脚本，这里的
预处理和比对和它一致，tests/test_recipe_classifier.py 用它核对）：

    BGR 图 -> 保持比例缩放到 224×224 以内（INTER_AREA，长边 224），居中，其余部分补黑
           -> RGB，(x - 127.5) / 57.375，CHW -> ONNX 推理：256 维特征 -> L2 归一化
           -> 和底库的每条特征算余弦相似度，取最高的一条：它的菜名和相似度（match；业务搜图用它）。
              rank 给出最相近的几道不同的菜（模型工具 /api/tools/recipe 用它列出候选）

底库（labels_path）是一个 pickle：robam_base_featrures（N × 256 的特征，键名就是这么拼的）和
robam_base_labels（每条特征的菜名，一个菜名可以有几条特征）。读取时只还原 numpy 数组，别的对象
一律拒绝（pickle 能执行任意代码）。

一次只推理一张图：模型支持动态 batch，但在 CUDA 上每种新的 batch 大小第一次运行要多花 3～4 秒
（cuDNN 选算法），而一次推理 5 张和逐张推理 5 次一样快（开发机上都是约 75 ms）。所以固定 batch
为 1，预热时就把它跑热。

app.bootstrap.lifespan 在 RECIPE_DETECT_CONFIG.enabled 开启时创建并预热它，放在
ApplicationState.recipe_classifier。配置的 model_path、labels_path、use_gpu 或 TOTAL_CONFIG.device_id
变了，就加载、预热一个新实例，成功后再替换。加载失败直接抛出异常，由调用方记录原因。
多个线程可以同时调用 classify（ONNX Runtime 的 session 支持并发推理）。
"""

from __future__ import annotations

import logging
import pickle
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.clients.ground_dino import create_session
from app.core.config.settings import config_flag

logger = logging.getLogger("recipe")

SECTION = "RECIPE_DETECT_CONFIG"
DEFAULT_INPUT_SIZE = 224  # 模型输入的高宽是动态维度时用（robam_model.onnx 固定为 224×224）
# 模型自己的归一化（robam_recipe_classifier.py 的 MEAN / STD），作用在 0～255 的 RGB 上
MEAN = np.asarray((127.5, 127.5, 127.5), dtype=np.float32)
STD = np.asarray((57.375, 57.375, 57.375), dtype=np.float32)
# 底库文件里的两个键，原样（featrures 是文件里的拼写）
FEATURES_KEY = "robam_base_featrures"
LABELS_KEY = "robam_base_labels"


@dataclass(frozen=True, slots=True)
class RecipeMatch:
    """一张图在底库里最相近的菜品。"""

    label: str  # 菜名
    score: float  # 余弦相似度，-1～1，越大越像


class _ArraysOnly(pickle.Unpickler):
    """只还原 numpy 数组的 unpickler（dict、list、str 这些内置类型不经过 find_class，照常还原）。"""

    _ALLOWED = frozenset({
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "scalar"),
        ("numpy._core.multiarray", "_reconstruct"),  # numpy 2 写的文件（现在的底库就是）
        ("numpy._core.multiarray", "scalar"),
    })

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in self._ALLOWED:
            raise pickle.UnpicklingError(f"底库文件里有不允许的对象 {module}.{name}：只能包含 numpy 数组")
        return super().find_class(module, name)


def load_gallery(path: str | Path) -> tuple[np.ndarray, list[str]]:
    """底库：L2 归一化的特征（N × D，float32）和每条特征的菜名（N 个，去掉首尾空白）。

    文件不存在时抛出 FileNotFoundError，内容不对时抛出 ValueError / pickle.UnpicklingError。
    现在的底库本来就是归一化的，这里再归一化一次，相似度就一定是余弦相似度。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"菜谱识别底库文件不存在: {path}")
    with path.open("rb") as file:
        data = _ArraysOnly(file).load()
    if not isinstance(data, dict) or FEATURES_KEY not in data or LABELS_KEY not in data:
        raise ValueError(f"菜谱识别底库文件 {path} 要是一个包含 {FEATURES_KEY} 和 {LABELS_KEY} 的 dict")
    features = np.asarray(data[FEATURES_KEY], dtype=np.float32)
    labels = np.asarray(data[LABELS_KEY])
    if features.ndim != 2 or not features.size:
        raise ValueError(f"{FEATURES_KEY} 要是非空的二维数组，实际形状为 {features.shape}")
    if labels.shape != (len(features),):
        raise ValueError(f"{LABELS_KEY} 要是和特征一样多（{len(features)} 条）的一维数组，实际形状为 {labels.shape}")
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    if not np.all(np.isfinite(norms) & (norms > 0)):
        raise ValueError(f"{FEATURES_KEY} 里有全零或不是有限值的特征")
    return features / norms, [str(label).strip() for label in labels]


def letterbox(image: np.ndarray, size: int) -> np.ndarray:
    """保持比例缩放到 size × size 以内（长边等于 size，INTER_AREA），居中，其余部分补黑。
    和模型自带脚本的 resize_with_padding 一样（短边按 int 截断，至少 1 像素）。
    """
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("图片尺寸无效")
    aspect_ratio = width / height
    if aspect_ratio > 1:
        new_width, new_height = size, int(size / aspect_ratio)
    else:
        new_width, new_height = int(size * aspect_ratio), size
    new_width, new_height = max(1, new_width), max(1, new_height)
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
    padded = np.zeros((size, size, 3), dtype=np.uint8)
    x, y = (size - new_width) // 2, (size - new_height) // 2
    padded[y:y + new_height, x:x + new_width] = resized
    return padded


def _required_path(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{SECTION}.{key} 缺失")
    return text


def _static_dim(shape: list[Any], index: int) -> int | None:
    value = shape[index] if len(shape) > index else None
    return value if isinstance(value, int) and value > 0 else None


class RecipeClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        """config：RECIPE_DETECT_CONFIG（model_path、labels_path 必填，use_gpu 默认 false）和
        TOTAL_CONFIG.device_id（默认 0）。
        """
        section = config.get(SECTION) or {}
        self.model_path = _required_path(section, "model_path")
        self.labels_path = _required_path(section, "labels_path")
        self.use_gpu = config_flag(config, SECTION, "use_gpu", False)
        self.device_id = int((config.get("TOTAL_CONFIG") or {}).get("device_id", 0))

        started = time.perf_counter()
        self.features, self.labels = load_gallery(self.labels_path)
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"菜谱识别模型文件不存在: {self.model_path}")
        self.sess = create_session(self.model_path, "cuda" if self.use_gpu else "cpu", device_id=self.device_id)
        model_input, model_output = self.sess.get_inputs()[0], self.sess.get_outputs()[0]
        self.input_name, self.output_name = model_input.name, model_output.name
        shape = list(model_input.shape)
        height, width = _static_dim(shape, 2), _static_dim(shape, 3)
        if height != width:
            raise ValueError(f"菜谱识别模型的输入要是正方形，实际为 {shape}")
        self.input_size = height or DEFAULT_INPUT_SIZE
        dim = _static_dim(list(model_output.shape), 1)
        if dim is not None and dim != self.features.shape[1]:
            raise ValueError(f"菜谱识别模型输出 {dim} 维特征，底库是 {self.features.shape[1]} 维")
        logger.info(
            "菜谱识别模型加载完成 | model=%s | labels=%s | 底库特征=%d | 菜名=%d | input=%dx%d | providers=%s | cost_ms=%.2f",
            self.model_path, self.labels_path, len(self.labels), len(set(self.labels)), self.input_size,
            self.input_size, self.sess.get_providers(), (time.perf_counter() - started) * 1000,
        )

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """一张 BGR 图（uint8，HWC）-> 模型输入（1 × 3 × size × size，float32）。"""
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError(f"菜谱识别的输入要是 HWC 三通道 uint8 的 BGR 图，实际为 {getattr(image, 'shape', type(image))}")
        rgb = letterbox(image, self.input_size)[:, :, ::-1].astype(np.float32)
        return np.ascontiguousarray(((rgb - MEAN) / STD).transpose(2, 0, 1)[None])

    def scores(self, image: np.ndarray) -> np.ndarray:
        """一张 BGR 图和底库每条特征的余弦相似度（N 个，顺序同 labels）。"""
        output = self.sess.run([self.output_name], {self.input_name: self.preprocess(image)})[0]
        feature = np.asarray(output, dtype=np.float32)
        if feature.shape != (1, self.features.shape[1]):
            raise RuntimeError(f"菜谱识别模型输出的形状不对：{feature.shape}，应为 (1, {self.features.shape[1]})")
        feature = feature / max(float(np.linalg.norm(feature)), float(np.finfo(np.float32).eps))
        return np.dot(feature, self.features.T)[0]

    def match(self, image: np.ndarray) -> RecipeMatch:
        """一张 BGR 图在底库里最相近的菜品。"""
        scores = self.scores(image)
        best = int(np.argmax(scores))
        return RecipeMatch(self.labels[best], float(scores[best]))

    def rank(self, image: np.ndarray, k: int) -> list[RecipeMatch]:
        """一张 BGR 图在底库里最相近的 k 道菜，从高到低：菜名不重复，每道菜按它最相近的那条特征算
        （一个菜名可以有几条特征）。第一个就是 match 给出的那个（相似度相同时取底库里靠前的，
        和 argmax 一样）；底库里的菜名不到 k 个时少于 k 个。/api/tools/recipe 用它列出候选。
        """
        scores = self.scores(image)
        ranked: list[RecipeMatch] = []
        seen: set[str] = set()
        for index in np.argsort(-scores, kind="stable"):
            label = self.labels[index]
            if label in seen:
                continue
            seen.add(label)
            ranked.append(RecipeMatch(label, float(scores[index])))
            if len(ranked) >= k:
                break
        return ranked

    def classify(self, images: Sequence[np.ndarray]) -> list[RecipeMatch]:
        """每张图最相近的菜品，顺序和传入的一样；逐张推理（见模块说明）。"""
        return [self.match(image) for image in images]

    def warmup(self, runs: int = 3) -> None:
        """用模型输入尺寸的空白图跑完整的 match：输出形状和底库对不上时在启动阶段就报错。"""
        runs = max(1, int(runs))
        logger.info("菜谱识别模型预热 %d 次", runs)
        started = time.perf_counter()
        dummy = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        for _ in range(runs):
            self.match(dummy)
        logger.info("菜谱识别模型预热完成，耗时 %.2fs", time.perf_counter() - started)
