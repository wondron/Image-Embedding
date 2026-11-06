"""EmbeddingPort implementation via existing OnnxEmbedding (do not rewrite)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

import cv2
import numpy as np
import requests

from app.clients.inference_onnx import OnnxEmbedding
from app.repositories.ports import EmbeddingPort

logger = logging.getLogger("embedder_port")

DEFAULT_MODEL_PATH = "config/model/dino/multitask_cls.onnx"
# Measured: multitask_cls.onnx output cls_emb -> (1024,) L2-normalized
EMBEDDING_DIM = 1024


def _default_embedding_config(model_path: str) -> dict[str, Any]:
    return {
        "TOTAL_CONFIG": {"device_id": 0},
        "EMBEDDING_CONFIG": {
            "model_path": model_path,
            "output_name": "cls_emb",
            "scale": [224, 224],
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
            "img_interp": "cubic",
            "use_gpu": True,
            "use_iobinding": False,
        },
    }


class EmbedderPortImpl:
    """ONNX global embedding; satisfies EmbeddingPort.

    Calls are serialized: OnnxEmbedding may reuse one cached io-binding, and
    HTTP handlers run use cases in a threadpool.
    """

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        config: Mapping[str, Any] | None = None,
        *,
        url_timeout: float = 15.0,
    ) -> None:
        self.model_path = model_path
        self.url_timeout = float(url_timeout)
        if config is None:
            cfg = _default_embedding_config(model_path)
        else:
            cfg = dict(config)
            emb = dict(cfg.get("EMBEDDING_CONFIG") or {})
            emb.setdefault("model_path", model_path)
            cfg["EMBEDDING_CONFIG"] = emb
            cfg.setdefault("TOTAL_CONFIG", {"device_id": 0})
        self._model = OnnxEmbedding(cfg)
        self.model_path = self._model.model_path
        self._lock = threading.Lock()

    def probe_dim(self) -> int:
        """Run one inference on a gray image and return the output dim (also warms up)."""
        dummy = np.full((self._model.input_h, self._model.input_w, 3), 127, dtype=np.uint8)
        with self._lock:
            out = self._model.infer(dummy)
        return int(np.asarray(out).shape[-1])

    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise ValueError("image_bytes is empty")
        with self._lock:
            vector = self._model.detect(image_bytes)
        return vector.astype(np.float32).tolist()

    def embed_url(self, image_url: str) -> list[float]:
        url = (image_url or "").strip()
        if not url:
            raise ValueError("image_url is empty")
        response = requests.get(url, timeout=self.url_timeout)
        response.raise_for_status()
        return self.embed_bytes(response.content)


def _assert_protocol() -> None:
    _: EmbeddingPort = EmbedderPortImpl()  # type: ignore[assignment]