"""枚举：数据库、接口和 Qdrant payload 共用同一套取值。

数据库里存的是 value（VARCHAR + CHECK，见 app.db.types.str_enum）；
改取值时同步改迁移里的 CHECK 约束。
"""

from __future__ import annotations

from enum import StrEnum  # SQLAlchemy 2.1 要求 Python>=3.11，直接用标准库


class CategoryDomain(StrEnum):
    """类别树的四个根节点（域）。"""

    INGREDIENT = "ingredient"  # 食材
    DISH = "dish"  # 菜品
    DESSERT = "dessert"  # 甜点烘焙
    BEVERAGE = "beverage"  # 饮品


class NameKind(StrEnum):
    """类别名称的种类。"""

    PRIMARY = "primary"  # 主名，每个类别每种语言只能有一个
    ALIAS = "alias"  # 别名


class SourceType(StrEnum):
    """图片来源大类。"""

    CRAWLED = "crawled"  # 爬取（含 TikTok、Instagram）
    DEVICE = "device"  # 设备采集
    OPEN_DATASET = "open_dataset"  # 开源数据集
    USER_UPLOAD = "user_upload"  # 用户上传
    EXTERNAL = "external"  # 外部图搜审核通过


class License(StrEnum):
    """许可标签；确认不能用的数据按标签整批剔除。"""

    COMMERCIAL = "commercial"  # 可商用
    RESEARCH_ONLY = "research_only"  # 仅研究
    UNKNOWN = "unknown"  # 未知


class FoodState(StrEnum):
    """食物状态。"""

    RAW = "raw"  # 生
    COOKED = "cooked"  # 熟
    FROZEN = "frozen"  # 冷冻


class LabelStatus(StrEnum):
    """标注状态。"""

    UNLABELED = "unlabeled"  # 未标注，一定没有类别
    AUTO = "auto"  # 自动标注
    VERIFIED = "verified"  # 已核验


class SceneOrigin(StrEnum):
    """图片进入场景的方式。"""

    RULE = "rule"  # 按规则分配
    MANUAL = "manual"  # 人工调整，优先于规则


class Distance(StrEnum):
    """向量距离。"""

    COSINE = "cosine"
    DOT = "dot"
    EUCLID = "euclid"


class ModelStatus(StrEnum):
    """模型版本状态。"""

    BUILDING = "building"  # 正在写入向量
    READY = "ready"  # 已补齐，可以切换
    CURRENT = "current"  # 当前使用，同一时间只有一个
    RETIRED = "retired"  # 已下线


class ReviewStatus(StrEnum):
    """外部候选的审核状态。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
