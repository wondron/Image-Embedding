"""Shared ONNX embedding encoder: one session per model spec per process.

The composition root (app.bootstrap.lifespan) builds one Embedder from
EMBEDDING_CONFIG and injects it into predict (FoodService / GlobalFilter) and
retrieval (index / search use cases). A config reload keeps the same instance
unless the EmbeddingSpec changed, so the model is never loaded twice for the
same weights.

Embedder.fingerprint() says which embedding space an encoder writes: its preprocessing, the
SHA-256 of its weight files (the .onnx and the external data it names) and its vector for one
fixed test image (probe_image). embedding_models.fingerprint keeps it per model version, so
retrieval never pairs an encoder with a collection another model wrote
(services.retrieval.model_versions.same_encoder compares two of them), and a version's
encoder can be loaded again from it (config_for_fingerprint). weights_status() tells whether a
recorded version's weight files are still where they were, unchanged.
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
_hashes: dict[tuple[str, int, int], str] = {}  # (absolute path, size, mtime_ns) -> sha256
_hashes_lock = threading.Lock()


def _sha256(path: str, stat: os.stat_result) -> str:
    """SHA-256 of a file, remembered while its size and mtime stay the same (1.2 GB: ~1 s)."""
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
    """The external data files the ONNX graph names (relative to its folder), without loading them."""
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
    """[{name, size, sha256}] of the weight files: the .onnx first, then its external data."""
    folder = os.path.dirname(os.path.abspath(model_path))
    names = [os.path.basename(model_path), *_external_data_names(model_path)]
    out = []
    for name in names:
        path = os.path.join(folder, name)
        stat = os.stat(path)
        out.append({"name": name, "size": int(stat.st_size), "sha256": _sha256(path, stat)})
    return out


def artifact_hash(files: list[Mapping[str, Any]]) -> str:
    """One hash for the weights: the file's SHA-256 for a single-file model, else the SHA-256 of
    the sorted file hashes (names left out: renaming a file keeps the weights)."""
    hashes = sorted(str(f["sha256"]) for f in files)
    if len(hashes) == 1:
        return hashes[0]
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def weights_status(fingerprint: Mapping[str, Any] | None) -> str | None:
    """Whether a version's recorded weight files are still there, unchanged: ok / missing /
    changed; None when its fingerprint recorded no files (nothing to compare)."""
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
    """(path, size, mtime_ns) of the weight files, to notice one replaced in place; None when
    they cannot be listed (the encoder is then never reused on a reload)."""
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

# [height, width] of the fingerprint's test image: never an encoder's input size, so the
# resize (scale, interpolation) is part of what the fingerprint sees
PROBE_SHAPE = (240, 320)
# Rounding of the stored probe vector (keeps the JSON small; far below what tells models apart)
PROBE_DECIMALS = 6


def probe_image() -> np.ndarray:
    """The fixed BGR test image of the fingerprint: built from formulas only, so it is the same
    on every machine and numpy version (no random generator, no file)."""
    height, width = PROBE_SHAPE
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    blue = 127.5 + 127.5 * np.sin(x / 11.0) * np.cos(y / 17.0)
    green = x * 255.0 / (width - 1)
    red = ((x // 16 + y // 16) % 2) * 200.0 + 27.0 + 20.0 * np.sin((x + y) / 7.0)
    image = np.stack([blue, green, red], axis=-1)
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    """Everything that makes two encoders interchangeable.

    Defaults mirror OnnxEmbedding.load_config so equal specs mean equal sessions.
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
        """None when EMBEDDING_CONFIG is malformed (building the encoder will say why)."""
        try:
            return cls.from_config(config)
        except (TypeError, ValueError):
            return None


def config_for_fingerprint(spec: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """The config that loads the encoder a fingerprint's spec describes (an older model version),
    with this process's runtime flags (GPU, io-binding, device) taken from base."""
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
    """One ONNX embedding session shared by predict and retrieval.

    ORT ``Session.run`` is thread-safe; the cached io-binding path in
    OnnxEmbedding is not, so calls are serialized only when io-binding is on.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._model = OnnxEmbedding(dict(config))  # validates config with precise messages
        self.spec = EmbeddingSpec.from_config(config)
        self._lock: Any = threading.Lock() if self._model.use_iobinding else contextlib.nullcontext()
        self._fingerprint: dict[str, Any] | None = None
        # the weight files as loaded: a file replaced in place since is a different model
        self._loaded_files = _file_stats(self.spec.model_path)

    def files_unchanged(self) -> bool:
        """True while the weight files at model_path are the ones this session loaded (same
        size and mtime): a reload may reuse it. A file replaced in place needs a new session."""
        return self._loaded_files is not None and _file_stats(self.spec.model_path) == self._loaded_files

    # -- diagnostics ----------------------------------------------------------

    @property
    def model_path(self) -> str:
        return self._model.model_path

    @property
    def sess(self) -> Any:
        """Underlying ORT session (provider diagnostics in /api/readyz)."""
        return self._model.sess

    def providers(self) -> list[str]:
        sess = self._model.sess
        return list(sess.get_providers() or []) if sess is not None else []

    # -- predict island (OnnxEmbedding-compatible) -----------------------------

    def detect(self, img_data: Union[np.ndarray, bytes]) -> np.ndarray:
        """L2-normalized embedding ``(dim,)`` from a BGR array or encoded image bytes."""
        with self._lock:
            return self._model.detect(img_data)

    def warmup(self, runs: int = 3) -> None:
        with self._lock:
            self._model.warmup(runs)

    def probe_dim(self) -> int:
        """Run one inference on a gray image and return the output dim (also warms up)."""
        dummy = np.full((self._model.input_h, self._model.input_w, 3), 127, dtype=np.uint8)
        with self._lock:
            out = self._model.infer(dummy)
        return int(np.asarray(out).shape[-1])

    def fingerprint(self) -> dict[str, Any]:
        """{spec, files, artifact, probe}: the embedding space this encoder writes (computed once
        per instance).

        spec: what loads it again (model_path) and how images are prepared (the effective input
        size, mean, std, interpolation, output); files: name, size and SHA-256 of the weight files
        as they are now (the .onnx and its external data); artifact: one hash over them
        (artifact_hash); probe: its L2-normalized vector for probe_image(). Runtime flags (GPU,
        io-binding, device) are left out: they do not change the space. files / artifact are
        missing when the files cannot be read (the probe still identifies the encoder then).
        """
        if self._fingerprint is None:
            vector = self.detect(probe_image())
            fingerprint: dict[str, Any] = {
                "spec": {
                    # forward slashes: a version recorded on Windows still loads in the Linux image
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

    # -- retrieval EmbeddingPort -------------------------------------------------

    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise ValueError("image_bytes is empty")
        return self.detect(image_bytes).astype(np.float32).tolist()
