"""工具的 wire：底层模型诊断。"""

from pydantic import BaseModel, Field


class SegmentResponse(BaseModel):
    ok: bool = True
    filename: str
    width: int
    height: int
    latency_ms: float
    mask_png_base64: str
    visual_png_base64: str | None = None


class EmbeddingResponse(BaseModel):
    ok: bool = True
    filename: str
    embedding_dim: int
    latency_ms: float
    embedding: list[float]


class GdinoDetection(BaseModel):
    """GroundingDINO 找到的一个目标。"""

    label: str = Field(description="caption 里和这个框匹配的词")
    score: float = Field(description="检测置信度，0～1")
    box: list[int] = Field(description="原图上的像素坐标 [x1, y1, x2, y2]，已裁到图内")


class GdinoResponse(BaseModel):
    ok: bool = True
    filename: str
    width: int
    height: int
    caption: str = Field(description="实际使用的 caption（没传 text_range 时取 GROUNDINGDINO_CONFIG.caption）")
    box_threshold: float = Field(description="实际使用的检测框阈值（没传时取 GROUNDINGDINO_CONFIG.box_threshold）")
    text_threshold: float = Field(description="实际使用的文本阈值（没传时取 GROUNDINGDINO_CONFIG.text_threshold）")
    latency_ms: float = Field(description="处理耗时：从排队等 GroundingDINO 开始，含解码和检测")
    detections: list[GdinoDetection] = Field(
        description="按置信度从高到低；重叠的框已按 GROUNDINGDINO_CONFIG.nms_threshold 去重"
        "（nms_agnostic 为 false 时只在同一个词的框之间去重）"
    )
