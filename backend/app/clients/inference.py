"""共用的 ONNX embedding 编码器：每个进程里每种模型规格一个 session。

组合根（app.bootstrap.lifespan）用 EMBEDDING_CONFIG 构建一个 Embedder，注入到预测
（FoodService / GlobalFilter）和检索（入库 / 搜索用例）。重新加载配置时，只要
EmbeddingSpec 没变就保留同一个实例，所以同一份权重永远不会加载两次。

Embedder.fingerprint() 说明编码器写入哪个 embedding 空间：它的预处理参数、权重文件
（.onnx 以及它引用的外部数据）的 SHA-256，以及它对一张固定测试图（probe_image）的向量。
embedding_models.fingerprint 按模型版本保存它，所以检索永远不会把编码器和另一个模型写的
collection 配对（services.retrieval.model_versions.same_encoder 比较两个指纹），并且可以
根据它重新加载一个版本的编码器（config_for_fingerprint）。weights_status() 说明一个已记录
版本的权重文件是否还在原处、没有被改动。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Union

import numpy as np

from app.clients.inference_onnx import OnnxEmbedding

logger = logging.getLogger("embedder")

_HASH_CHUNK = 8 << 20
_hashes: dict[tuple[str, int, int], str] = {}  # （绝对路径, 大小, mtime_ns）-> sha256
_hashes_lock = threading.Lock()


def _sha256(path: str, stat: os.stat_result) -> str:
    """文件的 SHA-256，在大小和 mtime 不变期间记住它（1.2 GB 约 1 秒）。"""
    key = (os.path.abspath(path), stat.st_size, stat.st_mtime_ns)
    with _hashes_lock:
        known = _hashes.get(key)
    if known is not None:
        return known
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _hashes_lock:
        _hashes[key] = value
    return value


def _external_data_names(model_path: str) -> list[str]:
    """ONNX 计算图引用的外部数据文件（相对于它所在的目录），不加载它们。"""
    import onnx
    from onnx.external_data_helper import _get_all_tensors, uses_external_data

    model = onnx.load(model_path, load_external_data=False)
    names = {
        entry.value
        for tensor in _get_all_tensors(model)
        if uses_external_data(tensor)
        for entry in tensor.external_data
        if entry.key == "location"
    }
    return sorted(names)


def model_files(model_path: str) -> list[dict[str, Any]]:
    """权重文件的 [{name, size, sha256}]：先是 .onnx，然后是它的外部数据。"""
    folder = os.path.dirname(os.path.abspath(model_path))
    names = [os.path.basename(model_path), *_external_data_names(model_path)]
    out = []
    for name in names:
        path = os.path.join(folder, name)
        stat = os.stat(path)
        out.append({"name": name, "size": int(stat.st_size), "sha256": _sha256(path, stat)})
    return out


def artifact_hash(files: list[Mapping[str, Any]]) -> str:
    """权重的总哈希：单文件模型为文件的 SHA-256，否则为排序后的各文件哈希的 SHA-256
    （不含文件名：重命名文件仍是同一份权重）。
    """
    hashes = sorted(str(f["sha256"]) for f in files)
    if len(hashes) == 1:
        return hashes[0]
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def weights_status(fingerprint: Mapping[str, Any] | None) -> str | None:
    """一个版本记录的权重文件是否还在、没有被改动：ok / missing / changed；
    它的指纹没有记录文件时为 None（没有可比较的）。
    """
    if not isinstance(fingerprint, Mapping):
        return None
    files = fingerprint.get("files")
    spec = fingerprint.get("spec")
    if not isinstance(files, list) or not files or not isinstance(spec, Mapping):
        return None
    folder = os.path.dirname(os.path.abspath(str(spec.get("model_path") or "")))
    for entry in files:
        path = os.path.join(folder, str(entry.get("name") or ""))
        try:
            stat = os.stat(path)
        except OSError:
            return "missing"
        if int(stat.st_size) != int(entry.get("size") or -1):
            return "changed"
        try:
            if _sha256(path, stat) != entry.get("sha256"):
                return "changed"
        except OSError:
            return "missing"
    return "ok"


def _file_stats(model_path: str) -> tuple[tuple[str, int, int], ...] | None:
    """权重文件的（路径, 大小, mtime_ns），用来发现被原地替换的文件；列不出来时为 None
    （这时重新加载配置时从不复用这个编码器）。
    """
    try:
        folder = os.path.dirname(os.path.abspath(model_path))
        names = [os.path.basename(model_path), *_external_data_names(model_path)]
        stats = []
        for name in names:
            path = os.path.join(folder, name)
            stat = os.stat(path)
            stats.append((path, int(stat.st_size), int(stat.st_mtime_ns)))
        return tuple(stats)
    except Exception:
        return None

# 指纹测试图的 [高, 宽]：从来不是编码器的输入尺寸，所以缩放（尺寸、插值方式）
# 也在指纹能看到的范围内
PROBE_SHAPE = (240, 320)
# 保存的测试向量的取整精度（让 JSON 保持小；远小于区分模型所需的精度）
PROBE_DECIMALS = 6


def probe_image() -> np.ndarray:
    """指纹用的固定 BGR 测试图：只用公式生成，所以在每台机器、每个 numpy 版本上都一样
    （不用随机数，不读文件）。
    """
    height, width = PROBE_SHAPE
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    blue = 127.5 + 127.5 * np.sin(x / 11.0) * np.cos(y / 17.0)
    green = x * 255.0 / (width - 1)
    red = ((x // 16 + y // 16) % 2) * 200.0 + 27.0 + 20.0 * np.sin((x + y) / 7.0)
    image = np.stack([blue, green, red], axis=-1)
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    """决定两个编码器能否互换的全部内容。

    默认值和 OnnxEmbedding.load_config 一致，所以规格相同就意味着 session 相同。
    """

    model_path: str
    output_name: str
    scale: tuple[int, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    img_interp: str
    use_gpu: bool
    use_iobinding: bool
    device_id: int

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "EmbeddingSpec":
        emb = dict(config.get("EMBEDDING_CONFIG") or {})
        total = dict(config.get("TOTAL_CONFIG") or {})
        return cls(
            model_path=os.path.normpath(str(emb.get("model_path") or "")),
            output_name=str(emb.get("output_name", "cls_emb")),
            scale=tuple(int(v) for v in emb.get("scale", (224, 224))),
            mean=tuple(float(v) for v in emb.get("mean", (0.485, 0.456, 0.406))),
            std=tuple(float(v) for v in emb.get("std", (0.229, 0.224, 0.225))),
            img_interp=str(emb.get("img_interp", "cubic")).strip().lower(),
            use_gpu=bool(emb.get("use_gpu", False)),
            use_iobinding=bool(emb.get("use_iobinding", False)),
            device_id=int(total.get("device_id", 0)),
        )

    @classmethod
    def try_from_config(cls, config: Mapping[str, Any]) -> "EmbeddingSpec | None":
        """EMBEDDING_CONFIG 格式不对时为 None（构建编码器时会说明原因）。"""
        try:
            return cls.from_config(config)
        except (TypeError, ValueError):
            return None


def config_for_fingerprint(spec: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """加载指纹 spec 所描述的编码器（一个较早的模型版本）用的配置，运行时开关（GPU、
    io-binding、设备）取自 base，即本进程的设置。
    """
    emb = dict(base.get("EMBEDDING_CONFIG") or {})
    total = dict(base.get("TOTAL_CONFIG") or {})
    return {
        "TOTAL_CONFIG": {"device_id": total.get("device_id", 0)},
        "EMBEDDING_CONFIG": {
            "model_path": str(spec.get("model_path") or ""),
            "output_name": spec.get("output_name", "cls_emb"),
            "scale": list(spec.get("scale") or (224, 224)),
            "mean": list(spec.get("mean") or (0.485, 0.456, 0.406)),
            "std": list(spec.get("std") or (0.229, 0.224, 0.225)),
            "img_interp": spec.get("img_interp", "cubic"),
            "use_gpu": bool(emb.get("use_gpu", False)),
            "use_iobinding": bool(emb.get("use_iobinding", False)),
        },
    }


class Embedder:
    """预测和检索共用的一个 ONNX embedding session。

    ORT 的 ``Session.run`` 是线程安全的；OnnxEmbedding 里缓存的 io-binding 路径不是，
    所以只在开启 io-binding 时才串行调用。
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._model = OnnxEmbedding(dict(config))  # 用准确的信息校验配置
        self.spec = EmbeddingSpec.from_config(config)
        self._lock: Any = threading.Lock() if self._model.use_iobinding else contextlib.nullcontext()
        self._fingerprint: dict[str, Any] | None = None
        # 加载时的权重文件：之后被原地替换的文件就是另一个模型
        self._loaded_files = _file_stats(self.spec.model_path)

    def files_unchanged(self) -> bool:
        """model_path 上的权重文件仍是这个 session 加载的那些（大小和 mtime 相同）时为 True：
        重新加载配置时可以复用。被原地替换的文件需要新的 session。
        """
        return self._loaded_files is not None and _file_stats(self.spec.model_path) == self._loaded_files

    # -- 诊断 -----------------------------------------------------------------

    @property
    def model_path(self) -> str:
        return self._model.model_path

    @property
    def sess(self) -> Any:
        """底层的 ORT session（用于 /api/readyz 里的 provider 诊断）。"""
        return self._model.sess

    def providers(self) -> list[str]:
        sess = self._model.sess
        return list(sess.get_providers() or []) if sess is not None else []

    # -- 预测模块（兼容 OnnxEmbedding）---------------------------------------

    def detect(self, img_data: Union[np.ndarray, bytes]) -> np.ndarray:
        """从 BGR 数组或编码后的图片字节得到 L2 归一化的 embedding ``(dim,)``。"""
        with self._lock:
            return self._model.detect(img_data)

    def warmup(self, runs: int = 3) -> None:
        with self._lock:
            self._model.warmup(runs)

    def probe_dim(self) -> int:
        """在一张灰色图上推理一次并返回输出维度（同时也起到预热作用）。"""
        dummy = np.full((self._model.input_h, self._model.input_w, 3), 127, dtype=np.uint8)
        with self._lock:
            out = self._model.infer(dummy)
        return int(np.asarray(out).shape[-1])

    def fingerprint(self) -> dict[str, Any]:
        """{spec, files, artifact, probe}：这个编码器写入的 embedding 空间（每个实例只计算一次）。

        spec：重新加载它需要的信息（model_path）以及图片怎样预处理（生效的输入尺寸、mean、
        std、插值方式、输出）；files：权重文件现在的名称、大小和 SHA-256（.onnx 及其外部
        数据）；artifact：它们的总哈希（artifact_hash）；probe：它对 probe_image() 的 L2 归一化
        向量。不包含运行时开关（GPU、io-binding、设备）：它们不改变 embedding 空间。文件读不了
        时没有 files / artifact（这时仍然可以用 probe 识别编码器）。
        """
        if self._fingerprint is None:
            vector = self.detect(probe_image())
            fingerprint: dict[str, Any] = {
                "spec": {
                    # 使用正斜杠：在 Windows 上记录的版本在 Linux 镜像里也能加载
                    "model_path": self.spec.model_path.replace("\\", "/"),
                    "output_name": self.spec.output_name,
                    "scale": [int(self._model.input_h), int(self._model.input_w)],
                    "mean": [float(v) for v in self.spec.mean],
                    "std": [float(v) for v in self.spec.std],
                    "img_interp": self.spec.img_interp,
                },
                "probe": [round(float(v), PROBE_DECIMALS) for v in vector],
            }
            try:
                files = model_files(self.spec.model_path)
                fingerprint["files"] = files
                fingerprint["artifact"] = artifact_hash(files)
            except Exception as exc:
                logger.warning("权重文件 hash 计算失败（只用测试图向量识别这个编码器）：%s", exc)
            self._fingerprint = fingerprint
        return self._fingerprint

    # -- 检索的 EmbeddingPort -------------------------------------------------------

    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise ValueError("image_bytes is empty")
        return self.detect(image_bytes).astype(np.float32).tolist()
