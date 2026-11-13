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

工作台支持单张图片／URL 搜索，也可一次添加最多 30 张图片逐张查询，查看各自结果、失败记录并停止、继续或重试。搜索结果支持跨页多选、ZIP 批量下载，以及在支持目录选择的浏览器中保存到指定目录；每批图片数据合计上限 200 MB，并附带来源清单。

Global 搜索阶段直接保留上游 `image_url`，不下载或验证候选图片。开启 `GLOBAL_FILTER.switch` 后，embedding 计算时才通过 `image_utils.resolve_image_bgr(item)` 获取 `(可用 URL, BGR 图像)`，按原图、large、medium、small、其他缩略图依次尝试；全部失败返回 `(None, None)`，工具不修改输入 item。关闭过滤时由前端在加载或下载图片时回退。

目录选择需要支持该功能的 Chrome、Edge 等浏览器及 HTTPS 或本机 localhost 页面，页面只显示目录名称，不显示完整本地路径；普通 HTTP 局域网访问可使用 ZIP。「食材管理」就是类别树（`/api/categories`，原「类别配置」页已并入），上传标注、图库筛选和场景规则都从这里选类别。

本次新增 `GET /api/images/download?url=…`，用于第三方图片跨域读取失败时的下载回退。接口仅访问公网 HTTP(S) 图片、80/443 端口，逐次校验重定向，限制单图 15 MB 并验证图片格式。**需要重启包含本次代码的后端；Docker 部署需使用重新构建或已包含新代码的镜像。** 该接口不提供图片入库功能。

## 配置来源

类别编码允许大小写并保留原样（例如 `ingredient.HongSu`），按小写判重；`ingredient.hongsu` 与它视为同一编码。
更新到此版本后，先在 `backend` 目录用启动后端的 Python 环境执行 `python -m alembic upgrade head`（迁移 `20261113_0010` 新增大小写不敏感的唯一索引），再启动服务。已有编码转小写后重复时，迁移会报告冲突并停止，需要手动处理后重试。

后端参数只有两个来源，不读 `.env`：

- `backend/app/core/config/constant.py`：项目定下来后基本不变的值（服务名、端口、Nacos 接入点、PG 驱动名等）；
- 生效配置：本地 `backend/config/param/config.yaml` 打底，所选 Nacos 环境的 dataId 逐键覆盖。密码、连接参数、模型等都在这里。

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
| `IMAGE_STORAGE_CONFIG` | `use_boto3`（默认 false，即 mock 不上传）、`endpoint_url`、`bucket`、`public_base_url`、`region`、`access_key_id`、`secret_access_key`、`mock_mirror_dir`、`key_prefix`（可选对象键前缀，与 `bucket` / `public_base_url` 同层；省略或空则新写入键与现网一致，非空如 `ImageEmbedding` 时为 `ImageEmbedding/original/{id}.jpg`。`public_base_url` 仍是桶/CDN URL 根，不要再拼同一前缀。已有对象与 URL 不迁移）。mock 驱动生成的 URL 是 `public_base_url` 下的占位地址，浏览器打不开，前端改用 `GET /api/images/{id}/file` 从后端读回（字节在内存里，配了 `mock_mirror_dir` 才能在重启后读到）；要让图片 URL 本身可用，需配置真实对象存储（`use_boto3: true`、`endpoint_url`、`bucket`、`public_base_url`） |
| `TOTAL_CONFIG.retrieval_enabled` | 设为 `false` 时只跑 predict，不初始化 PG / Qdrant |
| `IMAGE_DOWNLOAD_CONFIG.trusted_networks` | 远程图片下载只允许公网地址；这里列出的网段也放行。生产留空，本机代理为 fake-IP 模式时填 `198.18.0.0/15`、`2001:2::/48` |
| `RECONCILE_CONFIG` | API 进程里的后台对账：`enabled`（默认 true）、`interval_seconds`（默认 300，至少 10）、`initial_delay_seconds`（默认 30）、`max_payloads`（每轮最多补推的向量 payload，默认 2000）、`max_deletions`（每轮最多清理的删除队列条数，默认 200）、`max_verifications`（每轮对照向量库核对的写入记录条数，默认 2000，0 关闭）、`max_embeddings`（每轮为当前模型版本补上的缺向量图片数，默认 100，0 关闭）。每轮把数据库里比向量库新的过滤信息补推到 Qdrant，核对一段写入记录（向量库里点已不在的作废），为没有向量的图片生成向量，再清理已删除图片留下的向量和文件；多个进程同时开着时只有一个在跑。关掉后可以用 `python -m app.workers.reconcile` 定时执行（没有编码器，只核对不补向量） |

这些连接参数只在启动时读取，Nacos 里修改后需要重启服务；predict 的模型配置仍支持热更新。

### 换 embedding 模型

模型权重随镜像发布（不挂载目录），所以换模型时镜像里要同时带新旧两个版本：

1. 把新导出的模型打包成独立目录：`cd backend && python scripts/package_embedding_model.py /path/to/导出的.onnx`，得到 `config/model/embedding/embeddingcls-<hash>/`（.onnx 和外部权重文件原样放在里面，目录名是权重内容的 hash），脚本打印要填的 `EMBEDDING_CONFIG.model_path`。旧版本的目录保留在镜像里。现在在用的模型已经按这个规则放在 `config/model/embedding/embeddingcls-7c11052d2dc4/`（用 `--move` 从原来的 `config/model/dino/` 搬过来，只留一份；`dino/` 里仍是分割模型）。
2. 构建镜像、把 `EMBEDDING_CONFIG.model_path` 改成新目录、部署。predict 立即用新模型；自建库检索继续用旧版本自己的权重（从镜像里旧目录加载）查旧向量库，不会拿新模型去查旧向量。
3. 前端「模型版本」页（或 `/api/embedding-models`）：登记为新版本（新建它自己的 collection）→ 生成向量（服务端后台从存储里的原图生成，可停止、续做）→ 切换（不用重启；切换时入库请求排队几秒，生成之后新入库的图片会先补上；原版本保留，可以切回，切回前它缺的图片也能先补齐）。
4. 确定不再回滚后「下线」旧版本（删除它的 collection）；下一次构建镜像才能去掉旧目录。页面上「镜像里没有它的权重」的版本不能再切换过去。

同一个模型的判断看预处理、权重文件 hash 和固定测试图输出，挪目录、改名不算换模型。重启时如果当前版本的权重已经不在镜像里（或被覆盖），检索会暂停并报 `EMBEDDING_MODEL_MISMATCH`，同样在这个页面登记、生成、切换即可恢复。升级到这一版先执行 `alembic upgrade head`（迁移 `0009` 加 `embedding_models.fingerprint`），首次启动会核对并记录当前版本的编码器指纹。细节见契约 §6.5。

Alembic 和 workers 也按 `NACOS_ENV` 取库，而它默认是 prod：本机执行 `alembic upgrade head` 前先设
`NACOS_ENV=test`，或用 `NACOS_ENABLED=0` 只读本地 YAML。Alembic 会在日志里打印目标库（密码已隐去）。

## Nacos 配置环境

通过环境变量 `NACOS_ENV` 选择 Nacos 环境，这是部署时唯一需要指定的变量：未设置或为空时默认 `prod`，
设置为 `test` 时只读取 test 配置。启动时如果所选 Nacos 环境不可用、配置为空或
配置解析失败，则只使用本地 `backend/config/param/config.yaml`，不再尝试其他环境。
本地 YAML 也必须存在且有效。轮询同样只读取所选环境。日志里的 `env` 和 `/version` 的 `app_env` 与它一致。

Docker Compose 中直接修改 `backend/docker-compose.yml` 的 `NACOS_ENV`（默认 `test`），
或在执行 compose 的终端里设置该变量。修改后重新创建容器：

```shell
docker compose -f backend/docker-compose.yml up -d --force-recreate
```

使用包含这些代码的镜像后，环境选择逻辑才会生效。
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

