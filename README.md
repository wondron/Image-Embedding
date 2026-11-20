# Image-Embedding
图像检索

后端共用字段校验与场景成员规则位于 `backend/app/core/rules/`，服务和仓储直接引用该层；对象键工具位于 `backend/app/utils/storage_keys.py`。图片元数据编辑统一经仓储 `patch_image()`，先校验字段，再在同一事务内完成标注状态联动、场景重算与版本更新。

## 前端工作台

在 PowerShell 中启动：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

打开 `http://localhost:5173`，后端需要单独启动。开发服务默认把 `/api` 转发到 `http://127.0.0.1:8320`；配置方式见 [前端说明](frontend/README.md)。

工作台支持单张图片／URL 搜索，也可一次添加最多 30 张图片查询：自建库每次请求最多 5 张，现网 Global、业务搜图逐张请求。可以查看各自结果、失败记录并停止、继续或重试。搜索结果支持跨页多选、ZIP 批量下载，以及在支持目录选择的浏览器中保存到指定目录；每批图片数据合计上限 200 MB，并附带来源清单。

「现网 Global」的两个输入接口明确命名为 `POST /api/search/global/file`（上传图片文件）和 `POST /api/search/global/url`（图片链接），前端使用这两个地址；两者共用同一套 Global 搜图逻辑。旧 `/api/predict/searchImage_file`、`/api/predict/searchImage_url` 保留为兼容别名，不在 Swagger 中展示，参数和响应保持一致。

给业务方的搜图接口是 `POST /api/search/auto/file`（上传文件）和 `POST /api/search/auto/url`（JSON 图片链接）：先查自建库；配置 `RECIPE_DETECT_CONFIG.enabled` 开启时同时并行调用菜谱识别，识别出的菜名作为一条 `source=recipe` 的结果卡（菜名在 `tags` 里）排在自建库结果之后；两者都没有结果且 `global_fallback=true` 时用现网 Global 兜底。各通道返回同一种结果卡，每条结果卡的 `source`（`self` / `recipe` / `global`）说明它来自哪里，自建库结果卡的 `details.outputs_heating` 说明它是否带加热信息；没有结果时 `items` 为空，原因见 `fallback.status`。参数、响应和错误见 [数据契约 §6.6](docs/image-search-pg-v01-data-contract.md)。

多图搜索接口是 `POST /api/search/batch/files`（multipart，字段名 `files` 重复 1–30 次）和 `POST /api/search/batch/urls`（JSON `image_urls`）：一次请求多张查询图，逐张查自建库，整批共用 `scene`、`top_k`、`score_threshold`；`results[]` 每张图一项，某张图读不出或无法解码只影响它自己。不调用 Global。见 [数据契约 §6.7](docs/image-search-pg-v01-data-contract.md#batch-search)。

类别树和场景配置可以用文件修改和备份：`GET /api/categories`、`GET /api/scenes` 的响应体（或页面上「下载类别树」「下载配置」得到的 JSON）编辑后，用 `POST /api/categories/import`、`POST /api/scenes/import` 传回。按编码 upsert，`dry_run` 默认 `true` 只返回将要发生的变更，`dry_run=false` 才写入；类别不移动、不改编码、不删除，场景可用 `prune=true` 删除文件里没列出的规则和场景类别。导入逐项提交，响应需检查 `summary.error`，中途服务故障时已提交部分保留。见 [数据契约 §6.8](docs/image-search-pg-v01-data-contract.md#config-transfer)。类别树下载还提供 TXT、SVG、PNG 和 ZIP。

本轮的自动化检查、接口调用示例和页面验收步骤见[批量搜索与配置传输验收说明](docs/image-search-batch-config-acceptance.md)，已执行结果及环境限制见[验收记录](docs/image-search-batch-config-verification-2026-11-15.md)。更新后需要重启包含新路由的后端；本轮未新增数据库迁移。

Global 搜索阶段直接保留上游 `image_url`，不下载或验证候选图片。开启 `GLOBAL_FILTER.switch` 后，embedding 计算时才通过 `image_utils.resolve_image_bgr(item)` 获取 `(可用 URL, BGR 图像)`，按原图、large、medium、small、其他缩略图依次尝试；全部失败返回 `(None, None)`，工具不修改输入 item。关闭过滤时由前端在加载或下载图片时回退。

目录选择需要支持该功能的 Chrome、Edge 等浏览器及 HTTPS 或本机 localhost 页面，页面只显示目录名称，不显示完整本地路径；普通 HTTP 局域网访问可使用 ZIP。「食材管理」就是类别树（`/api/categories`，原「类别配置」页已并入），上传标注、图库筛选和场景规则都从这里选类别。

本次新增 `GET /api/images/download?url=…`，用于第三方图片跨域读取失败时的下载回退。接口仅访问公网 HTTP(S) 图片、80/443 端口，逐次校验重定向，限制单图 15 MB 并验证图片格式。**需要重启包含本次代码的后端；Docker 部署需使用重新构建或已包含新代码的镜像。** 该接口不提供图片入库功能。

## 脚本调用接口入库（无需前端）

使用 `backend/scripts/index_folder.py`，参数全部在脚本顶部设置，不使用命令行 args：

```python
IMAGE_FILE = r""  # 单张图片时填写文件路径，IMAGE_DIR 留空
IMAGE_DIR = r"E:\dataset\红薯叶"  # 文件夹入库时填写，IMAGE_FILE 留空
BASE_URL = "http://127.0.0.1:8320"  # 已启动的后端；可改为远程地址
RECURSIVE = True  # 包含子文件夹
DRY_RUN = False  # True 只预览文件列表，不访问后端
LIMIT = None  # 调试时可设为 1，只上传一张
```

需要标注时修改 `FORM_FIELDS` 中的 `category`（类别编码）、`tags`（如 `["红薯叶"]`）、
`description` 等字段；不需要的字段保留 `None`。相对文件路径统一相对于 `backend` 目录。

在 VS Code 中先运行 `.vscode/launch.json` 的 **Python: Backend**（后端已启动时跳过），
再选择 **Python: Index Folder Images** 按 F5，可在脚本中打断点。也可以从项目根目录直接运行：

```powershell
python backend/scripts/index_folder.py
```

脚本使用 `requests`（已列入后端依赖），逐张向 `POST /api/index` 发送 multipart 文件字节，
不要求图片位于后端所在机器。上传前检查后端健康状态和配置的类别编码；打印每张图片的入库结果、ID 和失败汇总，
网络异常及服务故障会自动重试。字节相同的图片由后端去重，重复上传保留首次入库的标注。
退出码：`0` 全部成功或已存在，`1` 有失败或中断，`2` 配置错误或后端未就绪。

## 配置来源

类别编码允许大小写并保留原样（例如 `ingredient.HongSu`），按小写判重；`ingredient.hongsu` 与它视为同一编码。
更新到此版本后，先在 `backend` 目录用启动后端的 Python 环境执行 `python -m alembic upgrade head`（迁移 `20261113_0010` 新增大小写不敏感的唯一索引），再启动服务。已有编码转小写后重复时，迁移会报告冲突并停止，需要手动处理后重试。

后端参数有三个来源：

- `backend/app/core/config/constant.py`：项目定下来后基本不变的值（服务名、端口、PG 驱动名等）；Nacos 只在这里定读哪些 dataId（`NACOS_DATA_ID_LIST`）；
- 环境变量：`NACOS_ENV` 和 Nacos 接入参数 `NACOS_*`（开关、地址、namespace、账号），见下面「Nacos 配置」；
- 生效配置：本地 `backend/config/param/config.yaml` 打底，Nacos 开启时 dataId 逐键覆盖。数据库密码、连接参数、模型等都在这里。

枚举取值（数据库、接口、Qdrant payload 共用）单独放在 `backend/config/param/enums.yaml`，不走 Nacos，规则见文件头。
`source_type` / `license` / `food_state` / `food_processing`（加工状态）可以直接改文件（不用重启），也可以调
`POST /api/enums/{name}/values` 追加；`GET /api/enums` 返回全部取值和中文名。这个文件随镜像进容器，不挂载：接口追加的取值
只保存在容器里，重建容器（重新构建镜像、`down` 后再 `up`）就会丢。重建前先把它取出来提交到 git，例如
`docker cp roki-imageembedding:/app/config/param/enums.yaml backend/config/param/enums.yaml`。升级到这一版要先执行 `alembic upgrade head`（迁移 `0004` 去掉可编辑枚举列的 CHECK；
`0005`、`0006` 把食物标注放进单独的表 `image_foods`：`food_state`、`food_processing`、`food_count`、总质量、
含水质量、主食材质量，都是非必填）。

连接相关的配置段（API、workers、Alembic 共用）：

| 配置段 | 内容 |
|---|---|
| `POSTGRES_CONFIG` | `host`、`user`、`database` 必填；`password`、`port`（默认 5432）、`connect_timeout`（默认 5 秒） |
| `QDRANT_CONFIG` | `url`（或 `host` + `port`）必填；`api_key`、`timeout`、`prefer_grpc` 等 |
| `IMAGE_STORAGE_CONFIG` | `use_boto3`（默认 false，即 mock 不上传）、`endpoint_url`、`bucket`、`public_base_url`、`region`、`access_key_id`、`secret_access_key`、`mock_mirror_dir`、`key_prefix`（可选对象键前缀，与 `bucket` / `public_base_url` 同层；省略或空则不加前缀，非空如 `ImageEmbedding` 时为 `ImageEmbedding/original/{shard}/{id}.jpg`。`public_base_url` 仍是桶/CDN URL 根，不要再拼同一前缀。已有对象与 URL 不迁移）。新写入的原图、缩略图按分片目录存放：`original/{shard}/{id}.{ext}`、`thumb/{shard}/{id}.webp`，`{shard}` 是原图 SHA-256 的前 2 位十六进制（即图片 id 的前 2 个字符，如 `original/a3/{id}.jpg`）；分片前入库的图仍在 `original/{id}.{ext}`，按库里的 URL 读写，不迁移。mock 驱动生成的 URL 是 `public_base_url` 下的占位地址，浏览器打不开，前端改用 `GET /api/images/{id}/file` 从后端读回（字节在内存里，配了 `mock_mirror_dir` 才能在重启后读到）；要让图片 URL 本身可用，需配置真实对象存储（`use_boto3: true`、`endpoint_url`、`bucket`、`public_base_url`） |
| `TOTAL_CONFIG.retrieval_enabled` | 设为 `false` 时只跑 predict，不初始化 PG / Qdrant |
| `IMAGE_DOWNLOAD_CONFIG.trusted_networks` | 远程图片下载只允许公网地址；这里列出的网段也放行。生产留空，本机代理为 fake-IP 模式时填 `198.18.0.0/15`、`2001:2::/48` |
| `RECONCILE_CONFIG` | API 进程里的后台对账：`enabled`（默认 true）、`interval_seconds`（默认 300，至少 10）、`initial_delay_seconds`（默认 30）、`max_payloads`（每轮最多补推的向量 payload，默认 2000）、`max_deletions`（每轮最多清理的删除队列条数，默认 200）、`max_verifications`（每轮对照向量库核对的写入记录条数，默认 2000，0 关闭）、`max_embeddings`（每轮为当前模型版本补上的缺向量图片数，默认 100，0 关闭）。每轮把数据库里比向量库新的过滤信息补推到 Qdrant，核对一段写入记录（向量库里点已不在的作废），为没有向量的图片生成向量，再清理已删除图片留下的向量和文件；多个进程同时开着时只有一个在跑。关掉后可以用 `python -m app.workers.reconcile` 定时执行（没有编码器，只核对不补向量） |
| `RECIPE_DETECT_CONFIG` | 业务搜图（`/api/search/auto/*`）里和自建库搜索并行的菜谱识别：`enabled`（默认 false，没有这一节也不调用）、`url`（识别接口地址，开启时必填，如 `http://<host>:8866/detect_recipe_onnx`）、`timeout`（一次调用的总时限，默认 5 秒；超时或出错按没有识别到处理，不影响搜索）。每次请求都读，改了不用重启 |

这些连接参数只在启动时读取，Nacos 里修改后需要重启服务（`RECIPE_DETECT_CONFIG` 除外）；predict 的模型配置仍支持热更新。

GroundingDINO 在启动时按 `GROUNDINGDINO_CONFIG` 加载，`model_path` 指向 ONNX，`tokenizer_path` 指向包含 `tokenizer.json` 的目录，`use_gpu` 控制是否请求 CUDA。缺少或为空的配置段会跳过加载；已有配置段默认启用，也可设 `enabled: false` 关闭。预热使用 `Settings.warmup.enabled` 和 `groundingdino_runs`（默认 3 次）。实例保存在 `ApplicationState.detector`，业务入口可通过 `runtime.require_detector()` 获取；加载或预热失败记录在 `detector_error`，原有 embedding 服务继续运行。Nacos 更新时，检测配置及 `TOTAL_CONFIG.device_id` 未变则复用实例，变化时先加载、预热成功再替换，失败保留旧配置和旧实例。当前只接入模型生命周期，检索仍按整图提取特征。

检测结果按 `nms_threshold` 去重之后，还会去掉「容器框」：一个框装着至少 `container_min_children`（默认 2）个更小的框（小框有 ≥ `container_contain_ratio`，默认 0.9，的面积在它里面），而且这些小框合起来盖住它至少 `container_min_coverage`（默认 0.5）。这种框多半是把一堆、一盘目标框在了一起，裁出来和整张图差不多，所以只留里面的小框；小框只盖住一小部分的大框（大目标上的配菜、点缀）照样保留。`container_min_children: 0` 关闭。区域搜索和 `/api/tools/gdino` 都经过这一步；这几项在 `GROUNDINGDINO_CONFIG` 里，改了会重新加载检测模型。

分割模型（`/api/tools/segment`）按同样的规则管理：启动时按 `SEGMENT_CONFIG` 加载（`model_path` 必填；缺少或为空的配置段跳过加载，`enabled: false` 关闭），预热次数为 `Settings.warmup.segment_runs`（默认 3 次）。实例保存在 `ApplicationState.segmentor`，接口通过 `runtime.require_segmentor()` 获取；加载或预热失败记录在 `segmentor_error`，该接口返回 503（detail 为失败原因），其他服务继续运行。Nacos 更新时，`SEGMENT_CONFIG` 及 `TOTAL_CONFIG.device_id` 未变则复用实例（不重新读 1.2 GB 权重、不重新预热），变化时先加载、预热成功再替换，失败保留旧配置和旧实例。路径不变、权重文件被原地替换时需要重启。

### 换 embedding 模型

模型权重随镜像发布（不挂载目录），所以换模型时镜像里要同时带新旧两个版本：

1. 把新导出的模型打包成独立目录：`cd backend && python scripts/package_embedding_model.py /path/to/导出的.onnx`，得到 `config/model/embedding/embeddingcls-<hash>/`（.onnx 和外部权重文件原样放在里面，目录名是权重内容的 hash），脚本打印要填的 `EMBEDDING_CONFIG.model_path`。旧版本的目录保留在镜像里。现在在用的模型已经按这个规则放在 `config/model/embedding/embeddingcls-7c11052d2dc4/`（用 `--move` 从原来的 `config/model/dino/` 搬过来，只留一份；`dino/` 里仍是分割模型）。
2. 构建镜像、把 `EMBEDDING_CONFIG.model_path` 改成新目录、部署。predict 立即用新模型；自建库检索继续用旧版本自己的权重（从镜像里旧目录加载）查旧向量库，不会拿新模型去查旧向量。
3. 前端「模型版本」页（或 `/api/embedding-models`）：登记为新版本（新建它自己的 collection）→ 生成向量（服务端后台从存储里的原图生成，可停止、续做）→ 切换（不用重启；切换时入库请求排队几秒，生成之后新入库的图片会先补上；原版本保留，可以切回，切回前它缺的图片也能先补齐）。
4. 确定不再回滚后「下线」旧版本（删除它的 collection）；下一次构建镜像才能去掉旧目录。页面上「镜像里没有它的权重」的版本不能再切换过去。

同一个模型的判断看预处理、权重文件 hash 和固定测试图输出，挪目录、改名不算换模型。重启时如果当前版本的权重已经不在镜像里（或被覆盖），检索会暂停并报 `EMBEDDING_MODEL_MISMATCH`，同样在这个页面登记、生成、切换即可恢复。升级到这一版先执行 `alembic upgrade head`（迁移 `0009` 加 `embedding_models.fingerprint`），首次启动会核对并记录当前版本的编码器指纹。细节见契约 §6.5。

Alembic 和 workers 也按同一套 `NACOS_*` 环境变量取库（本机读 `backend/.env`）。执行 `alembic upgrade head` 前
确认 `.env` 指向要迁移的环境，或临时 `$env:NACOS_ENABLED='false'` 只读本地 YAML。Alembic 会在日志里打印目标库（密码已隐去）。

## 数据库初始化与数据重置

Alembic 管表结构，Seed 管基础数据，Reset 管业务数据清理。三条命令都在 `backend` 目录、用启动后端的 Python 环境执行，
连接的库和后端、Alembic 一样（本机看 `backend/.env` 里的 `NACOS_*`；`NACOS_ENABLED=false` 时只读本地 YAML）：

| 命令 | 作用 | 什么时候用 |
|---|---|---|
| `python -m alembic upgrade head` | 创建、升级表结构（迁移只建表，不插数据） | 首次部署、数据库升级 |
| `python -m scripts.seed_data` | 补齐基础数据：场景 通用 / 一体机 / 抽油烟机，类别根节点 食材 / 菜品 / 甜点烘焙 / 饮品 / 混合 / 其他 | 首次部署（紧跟 upgrade）、基础数据被误删 |
| `python -m scripts.reset_data` | 清空业务数据（图片、类别树、场景配置、任务记录）和向量库里的点，再补齐基础数据 | 开发 / 测试环境重新开始 |

- 基础数据定义在 `backend/scripts/seed_data.py` 顶部（`BASE_SCENES`、`ROOT_CATEGORIES`），要增减就改那里。Seed 可以重复执行：
  按编码判断，缺的插入，已有的不改（和定义不同只提示），不删除其他数据；`--dry-run` 只列出会补哪些。
- Reset 先列出目标库、每张表的行数和要清的 Qdrant collection，输入数据库名确认后执行（`--yes` 跳过确认，`--dry-run` 只看不改）。
  PostgreSQL 的清空和补齐在一个事务里，失败则什么都不改。`NACOS_ENV=prod`（未设置时的默认值）时拒绝执行（`--allow-prod` 才放行）。
  保留模型版本登记（`embedding_models`）和加热组；对象存储里的文件不删除。`--keep-catalog` 只清图片和任务记录，保留类别树和场景配置；
  `--keep-vectors` 不动 Qdrant。执行前先停掉连着这个库的后端，否则可能等锁超时（10 秒后放弃、不做修改）。
- 迁移 `0001` 以前会插入六个场景，现在不再插入；已经迁移过的库保留原有场景。空库部署要在 `upgrade head` 之后执行一次 seed。

## Nacos 配置

读哪些 dataId 写在 `backend/app/core/config/constant.py` 的 `NACOS_DATA_ID_LIST`（按顺序覆盖本地 YAML，后面的覆盖前面的），
其余都是环境变量（完整列表和默认值见 `backend/.env.example`）：

| 变量 | 说明 |
|---|---|
| `NACOS_ENABLED` | 是否读 Nacos，默认 `false`：只用本地 `config.yaml`，其余 `NACOS_*` 都可以不设 |
| `NACOS_SERVER_ADDR`、`NACOS_NAMESPACE`、`NACOS_GROUP` | `NACOS_ENABLED=true` 时必填，缺了启动直接报错并列出缺哪几个 |
| `NACOS_USERNAME`、`NACOS_PASSWORD` | Nacos 账号；没开鉴权时留空 |
| `NACOS_ENV` | 环境标识 `prod` / `test`，未设置时 `prod`。日志里的 `env`、`/version` 的 `app_env` 显示它，`reset_data` 在 prod 下拒绝执行。它只是标识，连哪个环境由 `NACOS_NAMESPACE` 和账号决定，切换时成套修改 |
| `NACOS_POLL_ENABLED` 等 | 可选：轮询开关、轮询间隔、超时，默认值见 `.env.example` |

- **本地调试**：把 `backend/.env.example` 复制成 `backend/.env` 填好（`.env` 不提交）。API、Alembic、workers、scripts
  启动时都会读它；已经设置的环境变量优先，临时切换可以直接 `$env:NACOS_ENABLED='false'`。
- **docker compose 部署**：变量直接写在 compose 文件的 `environment` 里，镜像里没有 `.env`。
  `backend/docker-compose.server.yml` 里有 prod / test 两套（`x-nacos-prod` / `x-nacos-test`），`<<: *nacos-prod` 换成
  `*nacos-test` 就切到测试环境；`backend/docker-compose.yml`（本地 postgres / qdrant）默认不读 Nacos。改完重新创建容器：

```shell
docker compose -f backend/docker-compose.server.yml up -d --force-recreate
```

启动时 Nacos 不可用、配置为空或解析失败，只使用本地 `backend/config/param/config.yaml`（必须存在且有效）。
之后轮询到 Nacos 恢复，predict 的配置会切过去，但连接参数（PG / Qdrant / OSS）只在启动时读，要重启才生效。

## Docker 本地后台构建

在 `backend` 目录用 Dockerfile 后台构建本地镜像（不阻塞当前终端）：

```bat
cd backend

docker build -t roki-ai-image-embedding:local

```

查看进度：`type docker-build.log`（PowerShell 可用 `Get-Content docker-build.log -Wait`）。

说明：当前 `backend/docker-compose.yml` 的 `api` 使用远端 Harbor 镜像，不会自动走本地 Dockerfile。仅起本地依赖：

```bat
docker compose -f backend/docker-compose.yml up -d qdrant postgres
```

若要让 compose 使用刚构建的本地镜像，把 `api` 的 `image:` 改成 `roki-ai-image-embedding:local`，或为 `api` 增加 `build: .` 后再执行：

```bat
docker compose -f backend/docker-compose.yml up -d --build
```

