"""类别树的写入规则：接口（app.schemas.categories）和校验（app.services.retrieval.category_fields）共用。

库里这些列是不限长的 text / text[]，下面是接口这一侧的上限和写法。
"""

from __future__ import annotations

from app.core.enums import ENUM_VALUE_MAX_LEN, ENUM_VALUE_PATTERN

# 编码：小写字母、数字、下划线，用点分段，字母开头，如 dish.pizza。入库请求按它引用类别
CATEGORY_CODE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"
CATEGORY_CODE_MAX_LEN = 128

# 名称（category_names）
CATEGORY_NAME_MAX_LEN = 128
CATEGORY_LANG_PATTERN = r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$"  # zh、en、zh-hant
CATEGORY_LANG_MAX_LEN = 16
CATEGORY_MAX_NAMES = 50
# 检索结果和图库列表的 category 字段取这个语言的主名，所以每个类别都要有
CATEGORY_DISPLAY_LANG = "zh"

# 菜系（categories.cuisines）：写法同枚举取值，场景规则按它匹配
CATEGORY_CUISINE_PATTERN = ENUM_VALUE_PATTERN
CATEGORY_CUISINE_MAX_LEN = ENUM_VALUE_MAX_LEN
CATEGORY_MAX_CUISINES = 32

# categories.sort_order 是 integer 列
CATEGORY_SORT_ORDER_MIN = -2_147_483_648
CATEGORY_SORT_ORDER_MAX = 2_147_483_647
