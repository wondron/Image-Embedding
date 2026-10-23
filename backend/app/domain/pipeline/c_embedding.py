from __future__ import annotations

import logging
import time
from typing import Any, Dict

import numpy as np

from app.domain.common.embedding import OnnxEmbedding

logger = logging.getLogger("embedding")

class GlobalFilter:
    def __init__(self, config: Dict[str, Any]):
        self.load_config(config)

    def load_config(self, config: Dict[str, Any]) -> None:
        conf = config["GLOBAL_FILTER"]
        self.threshold = float(conf["threshold"])
        self.switch = bool(conf["switch"])
        self.embedding = OnnxEmbedding(config)

    def get_similar(self, input_vect: np.ndarray, refer_bgr: np.ndarray) -> float:
        refer_vect = self.embedding.detect(refer_bgr)
        return float(np.dot(input_vect, refer_vect))

    def detect(self, input_bgr: np.ndarray, input_res: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not input_res:
            return []
        
        if not self.switch:
            return [{key: value for key, value in item.items() if key != "image_bgr"} for item in input_res]
        
        embedd_vect = self.embedding.detect(input_bgr)

        final_data = []
        for index, item in enumerate(input_res, start=1):
            started_at = time.perf_counter()
            similarity = None
            try:
                similarity = self.get_similar(embedd_vect, item.get("image_bgr"))
                item["similarity"] = similarity
                if similarity > self.threshold:
                    final_data.append({key: value for key, value in item.items() if key != "image_bgr"})
            except (TypeError, ValueError) as exc:
                logger.warning("Skipping invalid candidate image %s: %s", item.get("image_url"), exc)
                continue
            finally:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.info(
                    "processing--item=%d/%d--elapsed_ms=%.2f--val=%s",
                    index, len(input_res), elapsed_ms,
                    f"{similarity:.2f}" if similarity is not None else "N/A",
                )
        return final_data
