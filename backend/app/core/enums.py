"""枚举：数据库、接口和 Qdrant payload 共用同一套取值，全部列在 config/param/enums.yaml。

文件由 app.core.config.enum_store 读写（中文名、说明也在文件里），这里只定义代码这边的约定。
枚举分两类：

- 系统枚举（下面的 StrEnum，登记在 SYSTEM_ENUMS）：代码按取值分支，数据库有 CHECK 约束
  （app.db.types.str_enum）。取值以代码为准，文件里必须一样（读文件时校验），文件里只能改
  label / description；改取值要同时改代码和迁移。
- 可编辑枚举（登记在 EDITABLE_ENUMS）：取值以文件为准，可以手工改文件，也可以调接口
  POST /api/enums/{name}/values 追加。数据库只存 VARCHAR(32)、不建 CHECK，写入前由
  app.core.rules.enum_fields 按文件校验。代码只直接用到少数几个取值（如默认值），
  它们必须一直留在文件里。
"""

from __future__ import annotations

from enum import StrEnum  # SQLAlchemy 2.1 要求 Python>=3.11，直接用标准库

# 取值规则（文件和接口共用）。库里的枚举列都是 VARCHAR(32)
ENUM_VALUE_MAX_LEN = 32
ENUM_VALUE_PATTERN = r"^[a-z][a-z0-9_]*$"
ENUM_LABEL_MAX_LEN = 64
ENUM_DESCRIPTION_MAX_LEN = 256
ENUM_MAX_VALUES = 100  # 每个枚举最多多少个取值，防止接口被刷


# -- 系统枚举 ----------------------------------------------------------------------


class CategoryDomain(StrEnum):
    """类别树的六个根节点（域）；每个编码唯一，最多六个根节点。"""

    INGREDIENT = "ingredient"  # 食材
    DISH = "dish"  # 菜品
    DESSERT = "dessert"  # 甜点烘焙
    BEVERAGE = "beverage"  # 饮品
    MIXED = "mixed"  # 混合：多食材、多菜品、饮品等混合在一起
    OTHER = "other"  # 其他：不属于其余类别域的内容


class NameKind(StrEnum):
    """类别名称的种类。"""

    PRIMARY = "primary"  # 主名，每个类别每种语言只能有一个
    ALIAS = "alias"  # 别名


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


class IndexTaskStatus(StrEnum):
    """入库任务的结果。入库是同步的，记录写在请求结束时，没有「进行中」。"""

    SUCCEEDED = "succeeded"  # 成功：得到一张图片（新入库或已存在）
    FAILED = "failed"  # 失败：没有写入任何东西


# 枚举名（文件里的键、接口路径里的 {name}）-> 类
SYSTEM_ENUMS: dict[str, type[StrEnum]] = {
    "category_domain": CategoryDomain,
    "name_kind": NameKind,
    "label_status": LabelStatus,
    "scene_origin": SceneOrigin,
    "distance": Distance,
    "model_status": ModelStatus,
    "review_status": ReviewStatus,
    "index_task_status": IndexTaskStatus,
}


# -- 可编辑枚举 --------------------------------------------------------------------

SOURCE_TYPE = "source_type"  # images.source_type：图片来源大类
LICENSE = "license"  # images.license：许可标签，确认不能用的数据按标签整批剔除
FOOD_STATE = "food_state"  # image_foods.food_state：食物状态（生 / 熟 / 冷冻），可为空
FOOD_PROCESSING = "food_processing"  # image_foods.food_processing：加工状态（切片 / 切块 / 整个……），可为空

DEFAULT_SOURCE_TYPE = "user_upload"  # 请求没给来源时
DEFAULT_LICENSE = "unknown"  # 请求没给许可时

# 枚举名 -> 代码直接用到的取值（文件里不能删）
EDITABLE_ENUMS: dict[str, frozenset[str]] = {
    SOURCE_TYPE: frozenset({DEFAULT_SOURCE_TYPE}),
    LICENSE: frozenset({DEFAULT_LICENSE}),
    FOOD_STATE: frozenset(),
    FOOD_PROCESSING: frozenset(),
}
