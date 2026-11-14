"""Search result card shared by both search channels (contract §6.1.1).

Self-built search (POST /api/search → SearchHit) and FlyB global search
(POST /api/search/global/* → PredictHit) answer with the same card: the four
fields below at the top level, every other field in ``details`` (its content depends on
the channel, see the two subclasses).
"""

from pydantic import BaseModel, Field

from app.core.rules.text_fields import TAG_SEPARATOR


class SearchHitBase(BaseModel):
    tags: str = Field(
        default="",
        description=f"标签文字：自建库为 images.tags 用「{TAG_SEPARATOR}」连接；FlyB 为结果标题（Title）；没有时为空字符串",
    )
    description: str = Field(
        default="",
        description="描述：自建库为 images.description；FlyB 为结果的文字片段（空格连接）；没有时为空字符串",
    )
    original_url: str | None = Field(
        default=None,
        description=(
            "原图地址：自建库为存储里的原图；FlyB 为结果里的第一张原图，没有原图时为最大的缩略图，"
            "开了相似度过滤时为实际下载、比对过的那张"
        ),
    )
    similarity: float | None = Field(
        default=None,
        description="相似度：自建库为向量检索分数；FlyB 为与查询图的 embedding 相似度，未开相似度过滤时为 null",
    )
