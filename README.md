# Image-Embedding
图像检索

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

目录选择需要支持该功能的 Chrome、Edge 等浏览器及 HTTPS 或本机 localhost 页面，页面只显示目录名称，不显示完整本地路径；普通 HTTP 局域网访问可使用 ZIP。图片库、入库任务和食材管理仍为前端预留界面，本地草稿不会写入数据库。

本次新增 `GET /api/images/download?url=…`，用于第三方图片跨域读取失败时的下载回退。接口仅访问公网 HTTP(S) 图片、80/443 端口，逐次校验重定向，限制单图 15 MB 并验证图片格式。**需要重启包含本次代码的后端；Docker 部署需使用重新构建或已包含新代码的镜像。** 该接口不提供图片入库功能。

## 配置来源

后端参数只有两个来源，不读 `.env`：

- `backend/app/core/config/constant.py`：项目定下来后基本不变的值（服务名、端口、Nacos 接入点、PG 驱动名等）；
- 生效配置：本地 `backend/config/param/config.yaml` 打底，所选 Nacos 环境的 dataId 逐键覆盖。密码、连接参数、模型等都在这里。

连接相关的配置段（API、workers、Alembic 共用）：

| 配置段 | 内容 |
|---|---|
| `POSTGRES_CONFIG` | `host`、`user`、`database` 必填；`password`、`port`（默认 5432）、`connect_timeout`（默认 5 秒） |
| `QDRANT_CONFIG` | `url`（或 `host` + `port`）必填；`api_key`、`timeout`、`prefer_grpc` 等 |
| `IMAGE_STORAGE_CONFIG` | `use_boto3`（默认 false，即 mock 不上传）、`endpoint_url`、`bucket`、`public_base_url`、`region`、`access_key_id`、`secret_access_key`、`mock_mirror_dir` |
| `TOTAL_CONFIG.retrieval_enabled` | 设为 `false` 时只跑 predict，不初始化 PG / Qdrant |
| `IMAGE_DOWNLOAD_CONFIG.trusted_networks` | 远程图片下载只允许公网地址；这里列出的网段也放行。生产留空，本机代理为 fake-IP 模式时填 `198.18.0.0/15`、`2001:2::/48` |

这些连接参数只在启动时读取，Nacos 里修改后需要重启服务；predict 的模型配置仍支持热更新。

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

