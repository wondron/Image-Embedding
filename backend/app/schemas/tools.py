"""工具的 wire：底层模型诊断。"""

from pydantic import BaseModel, Field

# /api/tools/recipe 每张图列出的候选数
RECIPE_TOP_K_DEFAULT = 5
RECIPE_TOP_K_MAX = 10


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


class RecipeCandidate(BaseModel):
    """底库里和一张图相近的一道菜。"""

    label: str = Field(description="菜名")
    score: float = Field(description="余弦相似度，-1～1：这道菜在底库里最相近的那条特征")


class RecipeRegion(BaseModel):
    """一个检测框和它的识别结果：业务搜图里菜谱识别用到的框，顺序和业务搜图一样。"""

    label: str = Field(description="GroundingDINO 匹配到的 caption 里的词")
    score: float = Field(description="检测置信度，0～1")
    box: list[int] = Field(description="原图上的像素坐标 [x1, y1, x2, y2]，已裁到图内；识别的是这一块裁剪图")
    candidates: list[RecipeCandidate] = Field(
        description="裁剪图最相近的几道菜（菜名不重复），从高到低；第一个是业务搜图用的那个"
    )
    accepted: bool = Field(description="第一个候选的相似度高于 threshold（不含等于）：业务搜图会给出这个菜名")


class RecipeToolResponse(BaseModel):
    ok: bool = True
    filename: str
    width: int
    height: int
    caption: str = Field(description="实际使用的 caption（没传 text_range 时取 AUTO_SEARCH_REGION_CONFIG.caption）")
    box_threshold: float = Field(description="实际使用的检测框阈值（没传时取 AUTO_SEARCH_REGION_CONFIG.box_threshold）")
    text_threshold: float = Field(description="实际使用的文本阈值（没传时取 AUTO_SEARCH_REGION_CONFIG.text_threshold）")
    max_regions: int = Field(description="实际使用的检测框数上限（没传时取 AUTO_SEARCH_REGION_CONFIG.max_regions）")
    threshold: float = Field(description="实际使用的菜谱阈值（没传时取 RECIPE_DETECT_CONFIG.threshold）")
    top_k: int = Field(description="每张图列出的候选数")
    detected: int = Field(
        description="GroundingDINO 给出的框数（去重之后）；边长小于 16 像素的丢掉，再按置信度取前 max_regions 个，就是 regions"
    )
    regions: list[RecipeRegion] = Field(description="识别的检测框，按检测置信度从高到低，最多 max_regions 个")
    names: list[str] = Field(
        description="按 threshold 业务搜图会给出的菜名：通过的框的菜名，同名只留一个，按相似度从高到低"
    )
    whole_image: list[RecipeCandidate] = Field(
        description="整张图最相近的几道菜，只用来对比：业务搜图只识别检测框，不识别整张图"
    )
    latency_ms: float = Field(description="处理耗时：从排队等 GroundingDINO 开始，含解码、检测和识别")
    classify_ms: float = Field(description="其中识别（各检测框和整张图的菜谱推理）的耗时")


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
