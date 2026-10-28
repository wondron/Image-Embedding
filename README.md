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

## Nacos 配置环境

通过环境变量 `NACOS_ENV` 选择 Nacos 环境：未设置或为空时默认 `prod`，
设置为 `test` 时只读取 test 配置。启动时如果所选 Nacos 环境不可用、配置为空或
配置解析失败，则直接读取本地 `backend/config/param/config.yaml`，不再尝试其他环境。
本地 YAML 也必须存在且有效。轮询同样只读取所选环境。

Docker Compose 已透传该变量，可在 `backend/.env` 中设置：

```dotenv
NACOS_ENV=test
```

也可直接将 `backend/docker-compose.yml` 中的 `NACOS_ENV` 值改为 `test`。
未设置时保留默认 prod。修改后重新创建容器：

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
docker compose -f backend/docker-compose.yml up -d qdrant mysql
```

若要让 compose 使用刚构建的本地镜像，把 `api` 的 `image:` 改成 `roki-ai-image-embedding:local`，或为 `api` 增加 `build: .` 后再执行：

```bat
docker compose -f backend/docker-compose.yml up -d --build
```

