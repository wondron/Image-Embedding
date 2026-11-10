"""场景配置的规则和默认值：接口（app.schemas.scenes）、校验（app.services.retrieval.scene_fields）
和按场景检索（app.services.retrieval.scenes）共用。

场景本身是 scenes 表里的行，经 /api/scenes 配置；这里只放代码这一侧的约定。
"""

from __future__ import annotations

from app.core.enums import ENUM_VALUE_MAX_LEN, ENUM_VALUE_PATTERN

# 场景编码：接口按它选场景，它也写进 Qdrant payload 的 scenes，所以和枚举取值同样的写法，建好后不能改
SCENE_CODE_PATTERN = ENUM_VALUE_PATTERN
SCENE_CODE_MAX_LEN = ENUM_VALUE_MAX_LEN
SCENE_NAME_MAX_LEN = 64
SCENE_DESCRIPTION_MAX_LEN = 256

# 场景自己的类别（scene_labels）：编码在场景内唯一，检索结果输出名称
SCENE_LABEL_CODE_PATTERN = ENUM_VALUE_PATTERN
SCENE_LABEL_CODE_MAX_LEN = 64
SCENE_LABEL_NAME_MAX_LEN = 64

# 请求没给场景时用它（契约 A5）。它包含全部图片，所以不能删除、停用或改成按规则分配
DEFAULT_SCENE = "general"
# MVP 的旧编码 → v0.1 编码（契约 A5），请求里仍然认；新场景不能占用这些编码
SCENE_ALIASES: dict[str, str] = {"normal": "general", "device": "oven"}

# 重算场景归属（POST /api/scenes/sync）每批处理的图片数
SCENE_SYNC_BATCH_DEFAULT = 200
SCENE_SYNC_BATCH_MAX = 1000
