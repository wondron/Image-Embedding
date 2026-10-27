# Image-Search MVP · 数据契约（SSOT）

> **状态**：契约终稿（文档 only；PM 已拍板编排与旧 predict 双轨）  
> **仓**：`D:\python\Image-Embedding`  
> **权重（权威）**：`backend/config/model/dino/multitask_cls.onnx`（`EMBEDDING_CONFIG.output_name=cls_emb`，L2 归一化）  
> **不改语义**：自建 embed → Qdrant → MySQL 为主路径；**仅当自建 top-k 为空**才兜底 FlyB。  
> **编码**：UTF-8（无 BOM）  
> **范围**：本地 Docker Compose（Qdrant + MySQL）；不 push；实现方不自测验收、不 commit（PM 测 PASS 后统一 commit）

---

## 0. 现网对照摘要（扫仓结论）

| 项 | 现状 | MVP 目标 |
|---|---|---|
| 搜图路径 | **FlyB 主路径**：`FoodService` → `GetGlobalData`/`FlyBSearch` →（可选）`GlobalFilter` 用 ONNX 重排 | **自建主路径**；FlyB 仅空结果兜底 |
| 向量库 / 元数据库 | **无** Qdrant、无 MySQL 元数据表；`backend/docker-compose.yml` 仅 API 容器 | Compose 增 Qdrant + MySQL |
| 入库 API | **无**；FE `ManagementViews`「提交入库 · 待接入」纯本地草稿 | `POST /api/index` |
| 搜图 API | `POST /api/predict/searchImage_file` / `searchImage_url` | 新增 `POST /api/search`（见 §3；旧 predict **保留不动**，见 PM 拍板） |
| 健康检查 | 已有 `GET /api/healthz` / `livez` / `readyz` / `ping` | 增补 `GET /api/health`（MVP 聚合自建依赖；见 §3.3） |
| FE 结果卡 | `SearchImage`（`frontend/src/types.ts`）+ `normalizeSearchResponse` | 响应字段对齐；缺口标 **需适配层** |

### ✅ PM 拍板（2026-10-27）

1. **MVP 编排 = 自建-first**：最终列表长度 == 0 才调 FlyB（按 §4 写死）。**不走**现网 FlyB-first；实现须改编排，不得「先 FlyB 再过滤」。
2. **旧** POST /api/predict/searchImage_*：**保留不动**（前端「现网 Global」仍走它）；新能力只加 POST /api/search。本轮 **不** deprecate、**不**转发伪装。
3. **D4（2026-10-28）`images.path` 唯一索引**：InnoDB utf8mb4 下 `UNIQUE(path)` 落地为 `UNIQUE KEY uk_images_path (path(768))`；列仍 `VARCHAR(1024)`。详见 §2。

> 现网对照见上表；拍板后冲突关闭，实现以本条为准。


---

## 1. Qdrant collection

### 1.1 基本参数

| 项 | 值 | 说明 |
|---|---|---|
| collection name | `image_embeddings` | 仓内无既有 collection；新名 |
| distance | **Cosine** | 与现 `OnnxEmbedding.detect` 的 **L2 归一化**向量一致（点积 ≡ cosine） |
| vectors.size | **1024**（已锁定） | 算法实测 `cls_emb`；见 §1.2 |
| vectors.on_disk | false（MVP 默认可） | 样图约 109 张，内存足够 |
| 点 ID 类型 | **无符号整数**，与 MySQL `images.id` **数值相等** | 见 §1.3 |

### 1.2 向量维度：已锁定与校验规则

**已锁定**：`VECTOR_DIM = 1024`。

算法实测确认：`backend/config/model/dino/multitask_cls.onnx` → 输出 `cls_emb` → L2 归一化后形状 `(1024,)`；distance=**Cosine**（与 L2 归一化点积等价）。**禁止**换 dinov3_machine 预训练。后端配置建议键：`QDRANT_CONFIG.vector_dim: 1024`。

**运行时校验（后端必须实现）**：

| 时机 | 规则 | 失败行为 |
|---|---|---|
| 进程启动 / collection ensure | `collection.vectors.size == 1024` | 拒绝启动或 `503`，错误码 `VECTOR_DIM_MISMATCH` |
| `POST /api/index` 写点前 | `len(embedding) == 1024` 且有限、非零范数 | `422` / `INDEX_BAD_EMBEDDING`，该图不入库 |
| `POST /api/search` 查询前 | `len(query_embedding) == 1024` 且有限、非零范数 | `422` / `SEARCH_BAD_EMBEDDING` |
| 重建 collection | 若 dim 变更必须 **新建 collection 或显式 recreate**，禁止静默截断/填充 | — |

### 1.3 Point id ↔ MySQL 映射

- MySQL 表 `images.id`：`BIGINT UNSIGNED` 自增主键。
- Qdrant point id：`images.id` 的同一整数值（推荐 Qdrant 无符号整型 point id）。
- **禁止**用路径字符串当 point id；**禁止**两侧各生成互不关联的 UUID（若未来要 UUID，须双写同一值 —— MVP 不用）。
- 删除/更新：先更 MySQL 状态，再删/覆写 Qdrant 同 id（MVP 可只做 insert；更新策略后端自定但须保持 id 一致）。

### 1.4 Payload：Qdrant vs MySQL

| 数据 | Qdrant payload | MySQL |
|---|---|---|
| 向量 | ✅（唯一权威） | ❌ 不存 |
| `image_id`（= point id） | 可选冗余 | ✅ PK |
| `path` / 可访问 URL | ❌（避免双源） | ✅ |
| `title` / `category` / tags | ❌ MVP | ✅ |
| `created_at` | ❌ | ✅ |
| 业务展示字段 | ❌ | ✅ |

**原则**：Qdrant 只负责「近邻 id + score」；卡片展示一律 join MySQL（及本地文件服务）。

---

## 2. MySQL 元数据表 · 字段字典

**表名**：`images`  
**引擎**：InnoDB，`utf8mb4` / `utf8mb4_unicode_ci`  
**Wire 权威**：英文字段名（与 Kumori 字典风格一致）；中文仅出现在文档说明。

| 字段 | 类型 | Null | 默认 | 索引 | 说明 |
|---|---|---|---|---|---|
| `id` | `BIGINT UNSIGNED` | NO | AI | PK | 与 Qdrant point id 相同 |
| `path` | `VARCHAR(1024)` | NO | — | UNIQUE(`path`(768)) | 本地相对/绝对路径；入库时写入。验收样图根：`E:\dataset\红薯叶`（只读引用，不改数据集）。**索引前缀见约束 D4** |
| `url` | `VARCHAR(2048)` | YES | NULL | — | 若已通过静态/文件接口暴露的 HTTP(S) URL；无则 null，由适配层用 path 拼 |
| `title` | `VARCHAR(255)` | YES | NULL | — | 展示标题；缺省可用文件名 |
| `category` | `VARCHAR(64)` | YES | NULL | IDX(`category`) | 对应 FE 入库壳「食材类别」 |
| `tags` | `JSON` | YES | NULL | — | 可选标签数组，如 `["红薯叶"]` |
| `width` | `INT UNSIGNED` | YES | NULL | — | 像素宽 |
| `height` | `INT UNSIGNED` | YES | NULL | — | 像素高 |
| `qdrant_point_id` | `BIGINT UNSIGNED` | NO | — | UNIQUE | **MVP = `id`**；显式列便于日后迁移；写入时必须 `qdrant_point_id = id` |
| `status` | `ENUM('active','deleted')` | NO | `'active'` | IDX(`status`) | 软删；搜图只查 `active` |
| `created_at` | `DATETIME(3)` | NO | CURRENT_TIMESTAMP(3) | IDX(`created_at`) | |
| `updated_at` | `DATETIME(3)` | NO | CURRENT_TIMESTAMP(3) ON UPDATE | — | |

**约束**：

- **`path` 唯一（D4 · MySQL 1071）**：`utf8mb4` 下对 `VARCHAR(1024)` 建 `UNIQUE(path)` 会触 **MySQL 1071**（InnoDB 索引键上限 3072 字节 ≈ 768 个 utf8mb4 字符）。
  - **定案（DDL · 已落地）**：列仍 `VARCHAR(1024)`。**InnoDB + utf8mb4 下契约写的 `UNIQUE(path)` 落地为 `UNIQUE KEY uk_images_path (path(768))`**（前缀唯一，避 MySQL 1071）。冲突仍返回 `INDEX_DUPLICATE_PATH`。
  - **语义**：应用层以**完整** `path` 判等入库；前缀唯一在「前 768 字符相同、其后不同」时理论上会误冲突——MVP 路径（样图根下）应远短于 768；入库前建议规范化并 **拒绝 `len(path) > 768`**（`422` / `INDEX_PATH_TOO_LONG`），使前缀唯一 ≡ 全路径唯一。
  - **备选（本轮不采用，仅备案）**：① 缩短列为 `VARCHAR(768)` + 全列 `UNIQUE(path)`；② 另加 `path_hash CHAR(64)`（规范化 path 的 SHA-256 hex）全列 `UNIQUE`，`path` 仅普通索引。
- 搜图 join：Qdrant 返回的 id 集合 → `WHERE id IN (...) AND status='active'`。

**FE 入库壳已有、表已覆盖**：类别 `category`、本地文件队列。  
**FE 图库壳尚未接 API**：列表/筛选后续可用 `title`/`category`/`created_at`；本契约不定义 list API（可延后）。

---

## 3. API 契约

**前缀强制**：全部挂在 `/api/...`。禁止裸 `/index`、`/search`、`/health`。

### 3.1 `POST /api/index`

**用途**：单张或小批量入库（embed → MySQL 行 → Qdrant upsert）。

**Content-Type**：

- `multipart/form-data`：字段 `file`（单文件）或 `files`（多文件，MVP 建议单请求 ≤ 100，与 FE 队列上限对齐）；可选 `category`（string）、`tags`（JSON 字符串数组）。
- 或 `application/json`：`{ "path": "...", "category": "...", "tags": [] }` —— 仅当文件已在服务可达路径上（批处理样图用）；与 multipart **二选一**。

**成功响应** `200`：

```json
{
  "ok": true,
  "trace_id": "uuid-or-null",
  "indexed": [
    {
      "id": 1,
      "qdrant_point_id": 1,
      "path": "relative/or/abs/path.jpg",
      "url": null,
      "title": "path.jpg",
      "category": "蔬菜",
      "tags": ["红薯叶"],
      "width": 1024,
      "height": 768,
      "status": "active",
      "created_at": "2026-10-27T10:00:00.000"
    }
  ],
  "failed": [
    {
      "filename_or_path": "bad.jpg",
      "error": "INDEX_BAD_IMAGE",
      "message": "cannot decode image"
    }
  ]
}
```

**部分成功**：HTTP 200 + `failed` 非空（与「整单失败」区分）；若请求无任何可解码图 → `400` `INDEX_EMPTY_REQUEST`。

### 3.2 `POST /api/search`

**用途**：自建向量检索；**仅当自建 top-k 为空**时触发 FlyB 兜底（§4）。

**请求**（与现 FE 能力对齐，两种互斥）：

A. `multipart/form-data`：`file` = 查询图  
B. `application/json`：`{ "img_url": "https://..." }`

可选 query/body 字段：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `top_k` | int | 10 | 自建召回条数，范围 1～50 |
| `score_threshold` | float \| null | null | 若设，cosine score **严格低于**阈值的点丢弃后再判空 |
| `trace_id` | string \| null | 服务端生成 | |

**成功响应** `200`（有文档时优先返回数组；空时可用 envelope）：

```json
{
  "ok": true,
  "trace_id": "...",
  "source": "self",
  "result": [
    {
      "id": "1",
      "title": "红薯叶_001.jpg",
      "image_url": "http://127.0.0.1:8320/api/images/file?id=1",
      "thumbnail_url": null,
      "similarity": 0.91,
      "category": "蔬菜",
      "page_url": null,
      "site_name": "local",
      "text_snippets": [],
      "width": 1024,
      "height": 768,
      "details": {
        "page_url": null,
        "site": "local",
        "text_snippets": [],
        "images": [
          {
            "image_url": "http://127.0.0.1:8320/api/images/file?id=1",
            "thumbnail_url": null,
            "alt": "红薯叶_001.jpg",
            "width": 1024,
            "height": 768
          }
        ]
      }
    }
  ]
}
```

`source` 取值：

- `self`：自建 Qdrant 命中（非空）
- `flyb`：自建为空后走 FlyB 的结果
- `empty`：自建空且 FlyB 也空 / 上游 empty envelope

双侧皆空示例：

```json
{
  "ok": true,
  "trace_id": "...",
  "source": "empty",
  "result": {
    "results": [],
    "status": "upstream_empty_response",
    "message": "no local matches; flyb also empty",
    "upstream_error": null
  }
}
```

> FE `normalizeSearchResponse` 已支持「数组」或 `{ results, status }` envelope。后端优先返回 **数组**（自建/FlyB 有文档时）；仅空 envelope 时用对象。

#### 与 FE `SearchImage` 对齐 / **需适配层**

| FE 字段（normalize 后） | 自建 API 建议 wire | 缺口 |
|---|---|---|
| `id` | string 化的 MySQL id | 现 FE 用 `image-${index}`；**需适配层**改为稳定业务 id（或暂时兼容 index id） |
| `title` | `title` | OK |
| `imageUrl` | `image_url`（须 http(s)，`safeHttpUrl` 校验） | 本地 path **不能**直接当 URL → **需适配层/文件网关**（建议 `GET /api/images/file?id=`）；否则卡片无图 |
| `thumbnailUrl` | `thumbnail_url` | 可 null |
| `similarity` | `similarity` ∈ [-1,1] | 自建用 cosine score；FlyB 现多为 null |
| `category` | `category` | OK |
| `pageUrl` | `details.page_url` | 自建可 null |
| `siteName` | `details.site` / site.name | 自建建议 `"local"` |
| `snippets` | `details.text_snippets` | 自建 `[]` |
| `width`/`height` | `details.images[0]` | OK |
| `raw` | 整行 | OK |

| 调用面 | 说明 |
|---|---|
| 现 `search.ts` → `/api/predict/searchImage_*` | **需适配层**改为 `/api/search`，或后端把 predict 转发到同一编排（PM 定） |
| 入库按钮「待接入」 | **需适配层**对接 `POST /api/index` |
| 旧 FlyB-only 文案 `upstream_empty_response` | 自建空但 FlyB 有结果时不应再显示「上游暂未返回」；**需适配层**认 `source` |

### 3.3 健康检查 `GET /api/health`

MVP 聚合检查（**不**替代现有 `healthz`/`readyz`；二者可继续给容器探针）。

**响应** `200`（全依赖可用）或 `503`：

```json
{
  "ok": true,
  "ts": "2026-10-27T10:00:00",
  "components": {
    "api": { "ok": true },
    "embedding": { "ok": true, "model_path": "config/model/dino/multitask_cls.onnx", "vector_dim": 1024 },
    "qdrant": { "ok": true, "collection": "image_embeddings", "points_count": 0 },
    "mysql": { "ok": true },
    "flyb": { "ok": true, "optional": true }
  }
}
```

规则：`embedding` / `qdrant` / `mysql` 任一不可用 → 整体 `ok=false` 且 HTTP 503。`flyb` 失败 **不**导致 503（兜底可选；但应在 JSON 里标 `ok=false` 便于排障）。

现网对照：`GET /api/healthz` 轻量存活可保留；`GET /api/readyz` 当前检查 segmentor/clip，与 MVP embedding/qdrant **不一致** —— 后端架构改 ready 语义或让 `/api/health` 专管 MVP，**需实现时对齐，勿静默删旧探针**。

### 3.4 本地文件读取（支撑结果卡，非 PM 原文但为缺口所逼）

建议（后端可微调路径，但须 `/api` 前缀）：

`GET /api/images/file?id={images.id}` → `image/*` 字节流（仅 `status=active`）。

标为 **需适配层**：无此接口则自建结果的 `image_url` 无法过 FE `safeHttpUrl`。

---

## 4. 错误码 / 空结果 → FlyB 语义

### 4.1 「自建 top-k 为空」判定（写死）

在调用 FlyB **之前**，完成：

1. 查询图 embed 成功；
2. Qdrant `search`（collection=`image_embeddings`，limit=`top_k`）；
3. 若配置了 `score_threshold`，过滤后保留 `score >= threshold` 的点；
4. 用保留 id 查 MySQL `status='active'`，去掉库中不存在或已删的 id；
5. **若最终列表长度 == 0** ⇒ 判定 **自建 top-k 为空** ⇒ **唯一**允许触发 FlyB 的条件。

**不是空、禁止 FlyB 的情况**：

- 自建已有 ≥1 条（即使分低）—— **不得**再调 FlyB 混排或替换；
- embed / Qdrant / MySQL 基础设施错误 —— 返回对应 5xx/错误码，**不得**用 FlyB 冒充自建成功；
- 查询图非法 —— `4xx`，不调 FlyB。

FlyB 返回形态沿用现 `FlyBSearch.parse_results`（数组或 `upstream_empty_response` envelope）。契约 **不**发明 FlyB-first 路径。

### 4.2 错误码表

统一 JSON：`{ "ok": false, "error": "<CODE>", "detail": "...", "trace_id": "..." }`（与现 validation handler 的 `error` 字段风格接近）。

| CODE | HTTP | 场景 |
|---|---|---|
| `VALIDATION_ERROR` | 422 | 参数/类型错误 |
| `INDEX_EMPTY_REQUEST` | 400 | 无文件且无 path |
| `INDEX_BAD_IMAGE` | 422 | 解码失败/非图 |
| `INDEX_BAD_EMBEDDING` | 422 | 维数/NaN/零向量 |
| `INDEX_DUPLICATE_PATH` | 409 | path 唯一冲突 |
| `INDEX_PATH_TOO_LONG` | 422 | `path` 规范化后长度 > 768（与 `UNIQUE(path(768))` 语义对齐） |
| `INDEX_QDRANT_FAILED` | 502 | 写向量失败（MySQL 应回滚或标失败） |
| `INDEX_MYSQL_FAILED` | 502 | 写元数据失败 |
| `SEARCH_BAD_IMAGE` | 422 | 查询图无效 |
| `SEARCH_BAD_EMBEDDING` | 422 | 查询向量非法 |
| `SEARCH_QDRANT_FAILED` | 502 | 检索基础设施失败（**不**降级 FlyB） |
| `SEARCH_MYSQL_FAILED` | 502 | 元数据 join 失败 |
| `SEARCH_FLYB_FAILED` | 502 | 已判定自建空，但 FlyB 超时/HTTP/解析失败 |
| `VECTOR_DIM_MISMATCH` | 503 | 配置 dim ≠ collection / 模型输出 |
| `NOT_READY` | 503 | embedding/qdrant/mysql 未就绪 |
| `INTERNAL_ERROR` | 500 | 未分类 |

健康：`GET /api/health` 用组件级 `ok`，不必再拆业务 CODE。

---

## 5. Docker Compose（契约层要求）

本机需可起：

- **Qdrant**（默认端口 6333/6334，collection 由应用 ensure）
- **MySQL 8**（库名建议 `image_embedding`，账号走 env，**勿把密码写入本 SSOT**）
- 现有 **api** 服务继续；通过 env 注入 `QDRANT_URL`、`MYSQL_DSN`、`VECTOR_DIM=1024`

具体 service 名与是否并入 `backend/docker-compose.yml` 由后端架构定；本契约只要求 **本地可一键起 Qdrant+MySQL**。

---

## 6. 开工点（谁可以动）

| 角色 | 可开工项 | 阻塞 |
|---|---|---|
| **算法逻辑** | dim 已锁定 1024；可开工：索引/查询伪代码、阈值建议、样图 embedding 质量抽检 | 无 |
| **后端架构** | Compose Qdrant+MySQL；表 images；collection ensure；POST /api/index、POST /api/search、GET /api/health；空结果 FlyB；可选 GET /api/images/file；编排改为自建-first | 无（旧 predict 保留不动） |
| **前端架构** | 仅 **适配层**：search 改 `/api/search`（或认转发）、入库接 `/api/index`、认 `source`、稳定 `id`、本地 `image_url` | 等 API 形状稳定 |

---

## 7. 已锁定 / 仍可选

- [x] `VECTOR_DIM` = **1024**（`multitask_cls.onnx` / `cls_emb`，L2 后 `(1024,)`）
- [ ] （可选）建议默认 `score_threshold`；无共识则 API 默认 `null`（只按 top_k）

---

## 8. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-10-27 | 初稿：Qdrant / MySQL / `/api/index` `/api/search` `/api/health` / 空→FlyB；标现网 FlyB-first 冲突与 FE 适配缺口 |
| 2026-10-27 | **dim 锁定 1024**（算法实测 cls_emb）；去掉待回填占位；校验仍拒绝 ≠1024 |
| 2026-10-27 | **PM 拍板**：自建-first（空才 FlyB）；旧 predict/searchImage_* 保留不动，新能力只加 /api/search |
