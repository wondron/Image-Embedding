# 后端部署到服务器（PostgreSQL / Qdrant / OSS 走远程）

服务器上只跑一个 API 容器（用 GPU），PostgreSQL、Qdrant、OSS 都是远程服务。连接参数和密码由 Nacos 提供：
Nacos 的开关、地址、namespace、账号是写在 `docker-compose.server.yml` 里的环境变量（prod / test 各一套），
读的 dataId 在 `backend/app/core/config/constant.py` 的 `NACOS_DATA_ID_LIST`（现在是 `roki-ai-image-search`），
按键覆盖镜像里的 `config/param/config.yaml`。

| 文件 | 作用 |
|---|---|
| `backend/Dockerfile` | 构建镜像：CUDA 12.4 + cuDNN 9 + Python 3.11，代码、模型、配置都打进去 |
| `backend/.dockerignore` | 不打进镜像的内容（日志、验收截图、测试等） |
| `backend/docker-compose.server.yml` | 服务器用：只有 `api`，不带 postgres / qdrant；Nacos 接入参数写在里面 |
| `backend/docker-compose.yml` | 本地开发用（带 postgres / qdrant 容器），服务器不用 |
| `backend/config/`（不在 git 里） | 模型权重（约 3.5 GB）、`config.yaml`、`enums.yaml`；构建镜像的机器上必须有 |
| 服务器上的 `.env` | 只放 `IMAGE`、`GPU_ID`，供 docker compose 替换变量（后端只认 compose `environment` 里的值） |

## 1. 先定下来的事

- **配置走 Nacos 还是挂载 YAML。** 默认走 Nacos（项目原本的设计）。服务器连不了 Nacos 时，把填好远程地址的
  `config.yaml` 挂进容器、compose 里把 `NACOS_ENABLED` 设为 `"false"`（compose 里有注释好的挂载行）。
- **这台机器是 prod 还是 test。** compose 里默认 `<<: *nacos-prod`；测试机改成 `<<: *nacos-test`，
  `NACOS_ENV` 和 namespace、账号一起切换。
- **镜像怎么到服务器。** `backend/config/` 不在 git 里，服务器上 `git clone` 后直接构建会缺模型。
  推荐在有 `config/` 的机器上构建，再推镜像仓库或 `docker save` 拷过去（第 5 步）。
- **远程库是新库，还是要带上现有数据。** 新库按第 7 步初始化。要带本地数据：PostgreSQL 用 `pg_dump` / `pg_restore`，
  Qdrant 用 collection 快照迁移；OSS 里的文件本来就在远程，不用动。
- **前端。** `npm run build` 得到 `frontend/dist/`，需要一个 Web 服务（如 nginx）托管，并把 `/api` 反代到
  `http://<后端>:8320`（见 `frontend/README.md`「验证与部署」）。

## 2. 服务器准备

- NVIDIA 驱动要支持 CUDA 12.4（`nvidia-smi` 右上角 CUDA Version ≥ 12.4），装好 NVIDIA Container Toolkit、
  Docker 和 compose v2（`docker compose version`）。
- 显存：分割和 embedding 两个模型一起加载。开发机运行时整卡占用约 3.5 GB（含桌面），服务器建议 ≥ 8 GB。
- 磁盘：当前镜像约 15 GB，再留一个旧版本用于回滚，预留 ≥ 40 GB。
- 出站网络要能到：
  - Nacos `nacos.myroki.com:8848`
  - PostgreSQL 5432
  - Qdrant 6333（REST）**和 6334（gRPC）**：`QDRANT_CONFIG.prefer_grpc` 默认 true，大部分请求走 6334；只开放 6333 时把它设为 false
  - OSS 443
- 入站只对需要的来源开放 8320：接口没有鉴权，能访问到就能入库、删除。

## 3. 远程服务准备

- **PostgreSQL**：建库和用户，白名单加上服务器 IP。迁移 0001 会执行 `CREATE EXTENSION IF NOT EXISTS ltree`，
  云数据库要确认允许 ltree、账号有权限（或请 DBA 先建好扩展）。
- **Qdrant**：客户端是 qdrant-client 1.20，服务端建议 ≥ 1.13（本地 compose 用的是 v1.13.2），版本尽量接近；
  设置 API key；跨公网时用 https，否则 API key 是明文传输。
- **OSS**：AK/SK 要有这个桶的 put / get / delete 权限。`public_base_url` 是浏览器打开图片用的地址（公共读或 CDN）。
  用 `key_prefix` 区分环境，不要和测试环境共用前缀。服务器是同地域的阿里云 ECS 时，`endpoint_url` 可以用内网地址
  `https://s3.oss-cn-hangzhou-internal.aliyuncs.com`，`public_base_url` 仍填公网地址。

## 4. 在 Nacos 里填配置

所选环境的 namespace 见 `docker-compose.server.yml` 的 `x-nacos-prod` / `x-nacos-test`，dataId `roki-ai-image-search`
（`NACOS_DATA_ID_LIST`），group `ROKI_AI_GROUP`。按 `config.yaml` 的结构只写要覆盖的键，没写的键沿用镜像里的 `config.yaml`：

```yaml
POSTGRES_CONFIG:
  host: <PG 地址>
  port: 5432
  user: <用户>
  password: <密码>
  database: <库名>
QDRANT_CONFIG:
  url: "http://<Qdrant 地址>:6333"
  api_key: <API key>
  prefer_grpc: true            # 6334 不通时改成 false
IMAGE_STORAGE_CONFIG:
  use_boto3: true
  endpoint_url: "https://s3.oss-cn-hangzhou.aliyuncs.com"
  bucket: <桶名>
  public_base_url: "https://<桶名>.oss-cn-hangzhou.aliyuncs.com"
  region: cn-hangzhou
  access_key_id: <AK>
  secret_access_key: <SK>
  key_prefix: "ImageEmbedding"
```

- 这些连接参数只在启动时读一次，Nacos 里改了要重启容器（predict 的模型配置能热更新，连接参数不能）。
- **启动时 Nacos 连不上，服务不会退出，而是只用镜像里的 `config.yaml`**，里面是 `localhost`，检索起不来，
  Nacos 之后恢复了也不会自己好。所以每次部署后都要看第 7 步的 `/api/config_info`。

## 5. 构建镜像（在有 `backend/config/` 的机器上）

1. 检查 `backend/config/`：
   - `model/embedding/embeddingcls-7c11052d2dc4/` 和 `model/dino/` 里以 `.` 开头、带 `.tmp.` 的 `.onnx.data`
     文件**就是权重本身**（`.onnx` 按这个文件名引用它），不是临时文件：不能删，拷贝时不能漏。
   - `param/config.yaml` 会原样打进镜像。镜像推到共享仓库时，里面写的密码、AK/SK 也跟着出去了：
     真实值放 Nacos，这里留空或只放本地调试值。
2. 构建，标签用日期或 commit，方便回滚：
   ```bash
   cd backend
   docker build -t roki-ai-image-embedding:20261115 .
   ```
   本机现有的 `roki-ai-image-embedding:local` 是 2026-10-28 构建的，早于 PG v0.1（11-04）和之后所有改动，不能用。
3. 送到服务器，二选一：
   - 镜像仓库：`docker tag` + `docker push`，服务器上 `docker pull`；
   - 直接拷：`docker save roki-ai-image-embedding:20261115 | gzip > image.tar.gz`，传到服务器后 `docker load -i image.tar.gz`。

## 6. 服务器上的目录

```
/opt/image-embedding/
├── docker-compose.server.yml   # 从 backend/ 拷过来；测试机把 <<: *nacos-prod 改成 *nacos-test
├── .env
└── output/                     # 日志，对应容器里的 /app/output
```

`.env`（只给 compose 替换变量用，Nacos 参数不放这里）：

```
IMAGE=roki-ai-image-embedding:20261115
# GPU_ID=0          # 多卡时选用哪张
```

下面的命令都在这个目录执行，先设一个别名：

```bash
alias dc='docker compose -f docker-compose.server.yml'
docker run --rm --gpus all roki-ai-image-embedding:20261115 nvidia-smi   # 容器里能看到 GPU
```

## 7. 首次部署

数据库要在启动 API **之前**初始化：类别、场景和检索只在启动时初始化一次，启动时表还没建好的话，建完表也要重启 API。

```bash
# 1) 连通性：Nacos 检查参数直接修改 check_nacos_connectivity.py 顶部的环境及配置
# Nacos、PG、Qdrant 只做只读查询；OSS 会写一个探测对象再删掉
dc run --rm api python scripts/check_nacos_connectivity.py
dc run --rm api python scripts/check_postgres_connectivity.py
dc run --rm api python scripts/check_qdrant_connectivity.py
dc run --rm api python scripts/check_oss_connectivity.py
# 2) 建表：日志里会打印目标库（密码已隐去），确认是远程库再往下
dc run --rm api python -m alembic upgrade head
# 3) 基础数据：默认场景和类别根节点
dc run --rm api python -m scripts.seed_data
# 4) 启动，等到 healthy（模型加载 + 预热，最多约 3 分钟）
dc up -d
dc logs -f api
```

检查，然后登记 embedding 模型（空库只需要做一次，也可以在前端「模型版本」页操作）：

```bash
curl -s http://127.0.0.1:8320/api/config_info   # 要是 "source": "nacos"；是 "yaml" 说明 Nacos 没连上，先排查
curl -s http://127.0.0.1:8320/api/health        # 空库这时是 503：还没有 current 的 embedding 模型
curl -s -X POST http://127.0.0.1:8320/api/embedding-models               # 登记，记下返回的 id
curl -s -X POST http://127.0.0.1:8320/api/embedding-models/<id>/activate # 空库没有缺向量的图片，可以直接切换
curl -s http://127.0.0.1:8320/api/health        # 200，embedding / qdrant / postgres 都是 ok
```

冒烟：用 `backend/scripts/index_folder.py`，把 `BASE_URL` 改成 `http://<服务器>:8320`、`LIMIT = 3`，
传几张图，再到前端搜一下。

## 8. 以后升级

1. 构建新标签的镜像送到服务器，改 `.env` 里的 `IMAGE`。
2. 这次有新迁移时，先用新镜像执行 `dc run --rm api python -m alembic upgrade head`。
3. `dc up -d`（会用新镜像重建容器），再做第 7 步的两个检查。
4. 回滚：`IMAGE` 改回上一个标签，`dc up -d`。这次执行过迁移的话，先确认旧版本代码能用新表结构。

`enums.yaml`：不挂载时，接口追加的枚举取值存在容器里，`dc up -d` 重建容器就会丢。升级前先取出来提交到 git：
`docker cp roki-imageembedding:/app/config/param/enums.yaml ./enums.yaml`。
或者打开 compose 里那行挂载，一直用服务器上的这份；以后仓库里的 `enums.yaml` 有改动，要手动合进来。

## 9. 排查

| 现象 | 先看 |
|---|---|
<<<<<<< HEAD
| `/api/config_info` 的 `source` 是 `yaml` | 跑 `check_nacos_connectivity.py`（先核对脚本顶部环境及配置）；`output/logs/app.log` 里搜「Nacos 不可用」 |
| `/api/health` 503，qdrant 不 ok | 跑 `check_qdrant_connectivity.py`；6333 / 6334 是否放通，`api_key`，`prefer_grpc` |
=======
| `/api/config_info` 的 `source` 是 `yaml` | 服务器到 Nacos 8848 不通，或账号、namespace 不对；`output/logs/app.log` 里搜「Nacos 不可用」 |
| 容器一启动就退出，日志里有 `NACOS_ENABLED=true 但没有设置 …` | compose 的 `environment` 里缺了列出的变量 |
| `/api/config_info` 的 `app_env` 和 `nacos_namespace` 对不上 | compose 里单独改了 `NACOS_ENV`：用 `<<: *nacos-prod` / `*nacos-test` 成套切换 |
| `/api/health` 503，qdrant 不 ok | 6333 / 6334 是否放通，`api_key`，`prefer_grpc` |
>>>>>>> 3d45480 (修改nacos读取逻辑)
| `/api/health` 503，postgres 不 ok | 白名单、账号；跑 `check_postgres_connectivity.py` |
| `NOT_READY`：没有 current 的 embedding 模型 | 第 7 步登记模型 |
| `EMBEDDING_MODEL_MISMATCH` | 镜像里没有当前版本的权重（用了缺模型的镜像，或权重文件被换过） |
| 容器反复重启、模型加载失败 | `nvidia-smi`、驱动版本、GPU 是否进了容器；`dc logs api` |
| 入库报存储错误 | 跑 `check_oss_connectivity.py`；AK/SK 权限、`endpoint_url` |

## 10. 建议但还没改的地方

- Dockerfile 的 `COPY . /app` 把代码和 3.5 GB 模型放在同一层，改一行代码，整层都要重新拷贝、推送。
  把 `config/model` 单独一条 `COPY` 放在前面，其余目录再分别 `COPY`，模型层就能一直命中缓存。
- `config/model/dino/multitask_patchtokens.onnx` 和它的权重（约 1.2 GB）代码里没有引用（`SEGMENT_CONFIG` 用的是
  `multitask_seg.onnx`）。确认 Nacos 里也没有指向它之后，可以在 `.dockerignore` 里排除，镜像小 1.2 GB。
- 本地 `docker-compose.yml` 现在起不来：它引用的 `docker-entrypoint-compose.sh` 不在仓库里（git 里也从来没有过）；
  另外 `./save_images:/app/save_imagesoutput` 的容器路径多了 `output`。
- prod / test 的 Nacos 密码从 `constant.py` 挪到了 `docker-compose.server.yml`：镜像里不再有，但仍在 git 里
  （git 历史里也有）。不想放 git：把 `NACOS_PASSWORD` 改成 `${NACOS_PASSWORD}`，值放服务器的 `.env`；并考虑换掉现有密码。
- README「Docker 本地后台构建」一节说 compose 用的是远端 Harbor 镜像，和现在的 `docker-compose.yml` 不一致。
