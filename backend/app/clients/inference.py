"""Shared ONNX embedding encoder: one session per model spec per process.

The composition root (app.bootstrap.lifespan) builds one Embedder from
EMBEDDING_CONFIG and injects it into predict (FoodService / GlobalFilter) and
retrieval (index / search use cases). A config reload keeps the same instance
unless the EmbeddingSpec changed, so the model is never loaded twice for the
same weights.

Embedder.fingerprint() says which embedding space an encoder writes: its preprocessing and
its vector for one fixed test image (probe_image). embedding_models.fingerprint keeps it per
model version, so retrieval never pairs an encoder with a collection another model wrote
(services.retrieval.model_versions.same_encoder compares two of them), and a version's
encoder can be loaded again from it (config_for_fingerprint).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Union

import numpy as np

from app.clients.inference_onnx import OnnxEmbedding

logger = logging.getLogger("embedder")

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
        """{spec, probe}: the embedding space this encoder writes (computed once per instance).

        spec: what loads it again (model_path) and how images are prepared (the effective input
        size, mean, std, interpolation, output); probe: its L2-normalized vector for
        probe_image(). Runtime flags (GPU, io-binding, device) are left out: they do not change
        the space.
        """
        if self._fingerprint is None:
            vector = self.detect(probe_image())
            self._fingerprint = {
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
        return self._fingerprint

    # -- retrieval EmbeddingPort -------------------------------------------------

    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise ValueError("image_bytes is empty")
        return self.detect(image_bytes).astype(np.float32).tolist()
