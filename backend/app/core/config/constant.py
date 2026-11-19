"""项目常量：项目定下来后基本不变的值。

参数有三个来源：
- 这里：不随环境变化的值。Nacos 只在这里定读哪些 dataId（NACOS_DATA_ID_LIST）；
- 环境变量：NACOS_ENV 和 Nacos 接入参数 NACOS_*（开关、地址、namespace、账号……），
  见 app.core.config.settings.NacosSettings。本地调试写在 backend/.env，docker compose 写在 compose 文件里；
- 生效配置：Nacos 逐键覆盖本地 config/param/config.yaml。数据库密码、连接参数、模型等都在这里。
"""

from __future__ import annotations


SERVICE_NAME = "image-embedding"
VERSION = "1.0.0"
DEFAULT_PORT = 8320
DEFAULT_YAML_PATH = "config/param/config.yaml"
DEFAULT_ENUMS_PATH = "config/param/enums.yaml"  # 枚举取值（app.core.config.enum_store），不走 Nacos
LOGGER_NAME = 'roki_ai_image_embedding'
DEFAULT_TIME_OUT = 15.0

# PostgreSQL 元数据库：host / user / password / database 在配置的 POSTGRES_CONFIG 里
PG_DRIVER = "postgresql+psycopg"
PG_DEFAULT_PORT = 5432
PG_CONNECT_TIMEOUT_SECONDS = 5.0

# 对象存储：IMAGE_STORAGE_CONFIG 里没给的项用这些值（默认是 mock 驱动，不真正上传）
IMAGE_STORAGE_DEFAULT_BUCKET = "image-embedding"
IMAGE_STORAGE_DEFAULT_PUBLIC_BASE_URL = "https://cdn.example.local/image-embedding"
IMAGE_STORAGE_DEFAULT_REGION = "us-east-1"

# 菜谱识别（业务搜图的并行步骤）：开关和地址在配置的 RECIPE_DETECT_CONFIG 里，没写 timeout 时用这个值
RECIPE_DETECT_TIMEOUT_SECONDS = 5.0

# GroundingDINO 的 onnxruntime 日志级别（app.clients.ground_dino）：0 verbose、1 info、2 warning、3 error、4 fatal。
# 用 3：CUDA 上它的图会刷大量 Memcpy 节点之类的 warning
ORT_LOG_SEVERITY_LEVEL = 3

# Nacos 读哪些 dataId：按顺序逐个覆盖本地 YAML，后面的覆盖前面的。
# 开关、地址、namespace、group、账号等都是环境变量 NACOS_*（app.core.config.settings.NacosSettings）
NACOS_DATA_ID_LIST = ["roki-ai-image-search"]
