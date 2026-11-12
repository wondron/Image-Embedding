# 图搜图 · PostgreSQL 数据契约 v0.1（M1 草案）

> **状态**：草案 · 待 PM/总裁审（文档 only；不写业务代码、不自测、不 commit）  
> **仓**：`D:\01-code\Image-Embedding`  
> **权威设计**：《图搜图数据结构设计 v0.1》（Nov 4, 2026 · @Dady）  
> **本文件**：`docs/image-search-pg-v01-data-contract.md`  
> **相对旧 SSOT**：取代 `docs/image-search-mvp-data-contract.md` 中 **MySQL 元数据 / 单 collection / path 唯一** 方向；`score_threshold` 等拒识数字**沿用 MVP 直至另刀**；**FlyB 空兜底本轮 OUT OF SCOPE**（M0 总裁已拍）。  
> **编码**：UTF-8（无 BOM）

---

## 0. 文首 ASSUMPTION（**M0 总裁已拍 · 冻结**）

| # | ASSUMPTION | 说明 |
|---|---|---|
| A1 | **`scene_labels` 纳入本轮** | 设计原文「待确认」项按建议做；一体机等场景可有自己的类别划分（**未否决 → 冻结纳入**） |
| A2 | **空库重建** | **旧 MySQL 数据清零**；以空 PG + 新 Qdrant collections + 新 S3 前缀重建；现网样图若要用须**重嵌重标** |
| A3 | **本轮不做数据迁移脚本** | MySQL→PG **OUT OF SCOPE**（无 M7 交付）；附录 M 仅作历史字段对照参考，**不**作为迁移工单 |
| A4 | **FlyB 本轮不工作** | 空结果兜底工作流 / 接口编排 **OUT OF SCOPE**；`POST /api/search` 自建空 → `source=empty`，**不**调 FlyB；旧 predict/FlyB 路径是否物理删除另刀，本契约不编排 |
| A5 | **场景 code 以 v0.1 为准** | 初始：`general` / `oven` / `range_hood` / `us_diet` / `cn_diet` / `jp_diet`。MVP 的 `normal`→应对 `general`，`device`→应对 `oven`（产品映射；实现另刀） |
| A6 | **检索拒识数字沿用 MVP** | v0.1 未重定 `score_threshold`；缺省 **0.55**、显式 `0` 关拒识、合法 `{0}∪[0.30,1]`、越界 `SEARCH_BAD_SCORE_THRESHOLD` **沿用**直至另刀 |

---

## 0.1 作废声明（必读）

**本契约明确作废**以下方向（不得再按此实现入库/元数据）：

1. 旧 SSOT **§9 · 入库/元数据优化草案**（path + OSS 双轨、按 scene 业务字段回落、统一结果卡 wire 草案等）——**已被本 pg-v01 契约取代（草案）**。
2. 相关 **ALIGNMENT-ASK** / 待拍板项中「本地 `path` + 云端 OSS `url` 双轨并存、展示优先 url、允许日后不落盘但仍保留 path 权威」的方案。
3. MVP **`images.path` UNIQUE**、**`scenes` JSON 列挂在 images 上**、**单表 `embedding_collection_meta` + 单 collection `image_embeddings`**、**point id = BIGINT images.id** 作为目标态（仅作历史对照；**不做**本轮数据迁移）。

**v0.1 目标态要点（取代上述）**：

- 元数据权威在 **PostgreSQL**；**无本地 path 列**；只存完整 **https**（`original_url` / `thumb_url`）。
- 场景可配置（`scenes` + `scene_rules` + `image_scenes` [+ `scene_labels`]）；一张图多场景。
- **每个 `embedding_models` 行对应一个 Qdrant collection**；point id = **`images.id`（uuid）**。
- 删除经 **`image_deletions`** 驱动清 Qdrant + S3。

旧文件 `docs/image-search-mvp-data-contract.md` 文首应有交叉引用一行（见本刀对旧 SSOT 的单行补丁）。

---

## 1. 总览

| 层 | 职责 | 键对齐 |
|---|---|---|
| **PostgreSQL** | 类别、图片、场景、模型注册、嵌入同步账、外部候选 | `images.id` = uuid |
| **Qdrant** | 每模型版本一个 collection；向量 + 过滤用 payload | point id = `images.id` |
| **S3** | 原图 / 缩略图 / 外部候选暂存 | 对象名含同一 `image_id`（或候选 id） |

共 **12 张表**，5 组：

| 组 | 表 |
|---|---|
| 类别 | `categories` · `category_names` · `heating_groups` |
| 图片 | `images` · `image_deletions` |
| 场景 | `scenes` · `scene_rules` · `scene_labels` · `image_scenes` |
| 模型与向量账 | `embedding_models` · `image_embeddings` |
| 外部候选 | `external_candidates` |

**扩展（PG）**：须启用 **`ltree`**（`CREATE EXTENSION IF NOT EXISTS ltree;`）。

---

## 2. 十二表字段字典（PG 类型 / 约束 / 索引）

> 类型与设计原文一致；下列补齐 **NULL / PK / FK / UNIQUE / 推荐索引** 与实现约束。Wire 字段名英文。时间一律 **`timestamptz`**。
>
> **2026-11-05 起表结构权威 = `backend/app/models/`（ORM）**，迁移 `0001` + `0002` 把库变成该结构（`alembic check` 可核对）。枚举列存 `varchar(32)`，取值见 `backend/config/param/enums.yaml`（系统枚举另有 CHECK，以 `backend/app/core/enums.py` 为准；`images.source_type`/`license` 与 `image_foods.food_state`/`food_processing` 为可编辑枚举，可经 `POST /api/enums/{name}/values` 追加，库里无 CHECK，见迁移 `0004`/`0006`）；约束/索引命名 `pk_/fk_/uq_/ck_/ix_`。本节表格与 ORM 冲突时以 ORM 为准。

### 2.1 `heating_groups`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, generated identity / serial | 加热组 |
| `code` | `text` | NO | UNIQUE | 稳定编码 |
| `name` | `text` | NO | — | 展示名 |
| `description` | `text` | YES | — | 说明 |

**索引**：`UNIQUE (code)`。

---

### 2.2 `categories`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, identity | 自增 |
| `code` | `text` | NO | UNIQUE | 如 `dish.pizza`；配置/规则引用 |
| `parent_id` | `integer` | YES | FK → `categories.id`；根为空 | 四根：食材、菜品、甜点烘焙、饮品 |
| `path` | `ltree` | NO | UNIQUE | ID 链如 `2.15.137`；**系统自动维护，禁止手写** |
| `cuisines` | `text[]` | NO | 默认 `'{}'` | 如 `{chinese}`；子孙沿用祖先（应用层规则） |
| `heating_group_id` | `integer` | YES | FK → `heating_groups.id` | 可挂任意节点；子孙未设则沿用最近祖先 |
| `sort_order` | `integer` | NO | 默认 `0` | 同级排序 |
| `is_active` | `boolean` | NO | 默认 `true` | 停用后不再用于标注 |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

**约束 / 行为**：

- 插入或移动时重算 `path`，整棵子树更新；**禁止**挂到自己的子孙下（触发器/应用校验）。
- 深度、是否叶子、所属域 **不存列**，由 `path` 推导。
- 名称不在本表 → `category_names`。

**索引**：`UNIQUE (code)`；`UNIQUE (path)`；`INDEX (parent_id)`；`INDEX (heating_group_id)`；`INDEX (is_active)`；推荐 `INDEX USING GIST (path)`（ltree 祖先/子孙查询）。

---

### 2.3 `category_names`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `category_id` | `integer` | NO | FK → `categories.id` ON DELETE CASCADE | |
| `lang` | `text` | NO | 如 `zh`/`en`/`fr`/`ja` | |
| `name` | `text` | NO | — | |
| `kind` | `text` | NO | CHECK `kind IN ('primary','alias')` | 主名或别名 |

**约束**：每个类别每种语言 **至多一个** `kind='primary'` → `UNIQUE (category_id, lang) WHERE kind = 'primary'`（部分唯一索引）。  
**PK 建议**：`(category_id, lang, name)` 或代理 `id` + `UNIQUE (category_id, lang, name)`。  
**索引**：`INDEX (category_id)`；`INDEX (lang, name)`（按名搜可选）。

---

### 2.4 `images`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `uuid` | NO | PK | **原图 SHA-256 的前 16 字节**；兼 S3 文件名与 Qdrant point id |
| `sha256` | `bytea` | NO | UNIQUE | 全 32 字节；校验与去重 |
| `original_url` | `text` | NO | — | 完整 https |
| `thumb_url` | `text` | NO | — | 完整 https；长边 512 WebP |
| `width` | `integer` | NO | CHECK `> 0` | 原图像素宽 |
| `height` | `integer` | NO | CHECK `> 0` | 原图像素高 |
| `source_type` | `text` | NO | 可编辑枚举，应用层按 `enums.yaml` 校验（无 CHECK，迁移 `0004`） | 初始 `crawled` / `device` / `open_dataset` / `user_upload` / `external` |
| `license` | `text` | NO | 可编辑枚举，同上 | 初始 `commercial` / `research_only` / `unknown` |
| `category_id` | `integer` | YES | FK → `categories.id` ON DELETE RESTRICT | 可标到任意层级；未标注为空 |
| `label_status` | `text` | NO | CHECK ∈ `unlabeled`/`auto`/`verified` | **未标注 ⇔ 没有类别**（库约束 `ck_images_label_matches_category`：`(label_status = 'unlabeled') = (category_id IS NULL)`）；默认 `unlabeled` |
| `description` | `text` | NO | 默认 `''` | 图片描述（迁移 `0003`）；API 侧上限 1000 字符；不进 Qdrant payload，修改不 bump `payload_version`；同图重复上传保留首次的描述 |
| `tags` | `text[]` | NO | 默认 `'{}'`；GIN 索引 `ix_images_tags_gin` | 图片标签，如 `{红薯叶,带皮}`（迁移 `0007`）；写入时去首尾空白、丢空串、去重，API 侧最多 32 个、每个最长 64 字符；不进 Qdrant payload，修改不 bump `payload_version`；同图重复上传保留首次的标签 |
| `payload_version` | `bigint` | NO | 默认 `1` | 类别/状态/所属场景变化时递增；与 Qdrant payload 同步 |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

**明确取消**：本地 `path`、软删 `status` 枚举挂本表（删除走 `image_deletions` + 物理删行，见 §2.5）。

**索引**：`UNIQUE (sha256)`；`INDEX (category_id)`；`INDEX (label_status)`；`INDEX (source_type)`；`INDEX (license)`；`INDEX (created_at)`（btree 反向扫描服务倒序分页）。另有 CHECK `octet_length(sha256) = 32`。

**id 生成（写死）**：

```
id = uuid_from_bytes(sha256(original_bytes)[0:16])  # RFC 4122 变体/版本位是否改写：实现须固定一种并文档化；推荐保留内容哈希字节、不强制 RFC version nibble，以免破坏去重
```

**ASSUMPTION A7**：同一字节图重复入库 → 同一 `id`/`sha256` 行；**来源只记第一次**（与设计确认记录一致）。冲突策略：`ON CONFLICT (sha256) DO NOTHING` 或返回已有 id（API 另刀）。

#### 食物标注 `image_foods`（2026-11-09 新增 · 迁移 `0006`；第 13 张表）

食物标注和 `image_embeddings` 一样单独成表：**每张图最多一行**（PK = `image_id`，FK → `images.id` ON DELETE CASCADE），**整行与每个字段都非必填**，没有标注的图没有这一行（读时按 NULL）。原 `images.food_state` 迁入本表；同图重复上传保留首次的标注。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `image_id` | `uuid` | NO | PK；FK → `images.id` ON DELETE CASCADE | |
| `food_state` | `varchar(32)` | YES | 可编辑枚举（无 CHECK） | 食物状态，初始 `raw`/`cooked`/`frozen`（生/熟/冷冻）；**唯一进 Qdrant payload 的字段**，修改 bump `images.payload_version` |
| `food_processing` | `varchar(32)` | YES | 可编辑枚举（无 CHECK） | 加工状态，初始 `sliced`/`chunked`/`whole`/`shredded`（切片/切块/整个/切丝） |
| `food_count` | `integer` | YES | CHECK `> 0` | 食物个数 |
| `total_mass_g` | `integer` | YES | CHECK `> 0` | 总质量（克） |
| `water_mass_g` | `integer` | YES | CHECK `>= 0` | 含水质量（克），可以为 0 |
| `main_ingredient_mass_g` | `integer` | YES | CHECK `> 0` | 主食材质量（克） |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

除 `food_state` 外不进 Qdrant payload，修改不 bump `payload_version`。共用规则见 `backend/app/core/rules/food_fields.py`。

---

### 2.5 `image_deletions`

剔除任务写入本表；清理 worker 删 Qdrant 点 + S3 对象；处理完再删本行。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `image_id` | `uuid` | NO | PK | 被删图片（原 `images.id`） |
| `original_url` | `text` | NO | — | 待清 S3 |
| `thumb_url` | `text` | NO | — | 待清 S3 |
| `deleted_at` | `timestamptz` | NO | 默认 `now()` | |

**索引**：`INDEX (deleted_at)`（清理队列扫描）。

**流程（契约层）**：事务内从 `images`（及依赖行）删除 → 插入 `image_deletions` → 异步清外部存储 → 删本行。失败可重试（本行仍在）。

---

### 2.6 `scenes`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, identity | |
| `code` | `text` | NO | UNIQUE | 写入 Qdrant payload，如 `general`、`oven` |
| `name` | `text` | NO | — | 如 通用、一体机 |
| `description` | `text` | YES | — | |
| `include_all` | `boolean` | NO | 默认 `false` | 通用场景为 `true`（含全部图片） |
| `outputs_heating` | `boolean` | NO | 默认 `false` | 一体机为 `true`：结果另带加热组与食物状态 |
| `is_active` | `boolean` | NO | 默认 `true` | 停用后不再分配新图片 |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |

**初始场景（种子数据 · 契约要求）**：

| name | code | 分配方式 |
|---|---|---|
| 通用（日常） | `general` | `include_all=true` |
| 一体机 | `oven` | 按类别规则 + `scene_labels`；`outputs_heating=true` |
| 抽油烟机 | `range_hood` | 待配置 |
| 美国饮食 | `us_diet` | 菜系含 `american` |
| 中国饮食 | `cn_diet` | 菜系含 `chinese` |
| 日本饮食 | `jp_diet` | 菜系含 `japanese` |

---

### 2.7 `scene_labels`（ASSUMPTION A1：本轮纳入）

场景自有类别（如一体机）；经 `scene_rules.label_id` 与通用类别对应。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, identity | |
| `scene_id` | `integer` | NO | FK → `scenes.id` | |
| `code` | `text` | NO | — | 场景内唯一，如 `steak` |
| `name` | `text` | NO | — | 如 牛排 |
| `sort_order` | `integer` | NO | 默认 `0` | |
| `is_active` | `boolean` | NO | 默认 `true` | |

**约束**：`UNIQUE (scene_id, code)`。  
**索引**：`INDEX (scene_id)`。

无自有类别的场景：检索结果直接给通用 `category_id` / 名称。

---

### 2.8 `scene_rules`

每条规则在「按类别 / 按菜系」二选一：`category_id` 与 `cuisine` **只填一个**。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, identity | |
| `scene_id` | `integer` | NO | FK → `scenes.id` | |
| `category_id` | `integer` | YES | FK → `categories.id` | 该类及其所有子孙 |
| `cuisine` | `text` | YES | — | 菜系包含它的类别 |
| `label_id` | `integer` | YES | FK → `scene_labels.id` | 映射到场景自有类别；可空 |

**CHECK**：`(category_id IS NOT NULL AND cuisine IS NULL) OR (category_id IS NULL AND cuisine IS NOT NULL)`。  
一个场景多条规则：**满足任意一条**即候选属于该场景。多规则覆盖同一图且带 `label_id` 时：取 **类别最具体**（path 最深）的那条。  
**索引**：`INDEX (scene_id)`；`INDEX (category_id)`；`INDEX (cuisine)`。

---

### 2.9 `image_scenes`

人工优先于规则；删掉人工记录则恢复按规则分配。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `image_id` | `uuid` | NO | FK → `images.id` ON DELETE CASCADE | |
| `scene_id` | `integer` | NO | FK → `scenes.id` ON DELETE CASCADE | |
| `origin` | `text` | NO | CHECK ∈ `rule`/`manual` | |
| `is_member` | `boolean` | NO | 默认 `true`；CHECK `is_member OR origin = 'manual'` | 人工排除时为 `false`（只有人工行能排除） |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

**PK**：`(image_id, scene_id)`。  
**语义**：有效成员 = 规则推导结果，再被本表 `origin='manual'` 行覆盖（含排除）。**物化策略：写时展开**（`recompute_image_scenes`）。**`include_all` 场景（`general`）启用时包含全部图片，不在本表写记录，也不接受人工覆盖**（迁移 `0002` 删除了这类行）。Qdrant payload `scenes` = 启用的 include_all 场景 ∪ 本表成员，必须与「有效成员」一致，并在变更时 bump `images.payload_version`。部分索引 `ix_image_scenes_scene_member (scene_id) WHERE is_member`。

---

### 2.10 `embedding_models`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `integer` | NO | PK, identity | |
| `name` | `text` | NO | — | 如 `dinov3_vith16plus_lora` |
| `version` | `text` | NO | — | 与 name 组合唯一 |
| `dim` | `integer` | NO | — | 如 1024 |
| `distance` | `text` | NO | CHECK ∈ `cosine`/`dot`/`euclid` | |
| `input_size` | `integer` | NO | — | 如 336 |
| `preprocess` | `jsonb` | NO | 默认 `'{}'` | 均值方差、缩放等 |
| `weights_uri` | `text` | NO | — | 权重位置（含 LoRA） |
| `collection` | `text` | NO | UNIQUE | 对应 Qdrant collection 名 |
| `status` | `text` | NO | CHECK ∈ `building`/`ready`/`current`/`retired` | **同一时间只有一个 `current`** |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |
| `activated_at` | `timestamptz` | YES | — | 切为 current 的时间 |

**约束**：`UNIQUE (name, version)`；`UNIQUE (collection)`；部分唯一 `UNIQUE (status) WHERE status = 'current'`（或等价应用门禁）。  
**切换**：新版本补齐 → `ready` → `current`；旧 `current` → `ready`；回滚即旧版再设 `current`。

**相对 MVP**：取代 `embedding_collection_meta` 单行 + 配置里写死单 collection；`embedding_space_id` 语义由 **`(name, version, dim, preprocess, weights_uri/fingerprint)`** 表达——是否另增 `embedding_space_id` 列 **TBD**（建议可派生，不强制与 MVP 同名列）。

---

### 2.11 `image_embeddings`

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `model_id` | `integer` | NO | FK → `embedding_models.id` | |
| `image_id` | `uuid` | NO | FK → `images.id` ON DELETE CASCADE | |
| `synced_version` | `bigint` | NO | — | 写入 Qdrant 时的 `images.payload_version` |
| `embedded_at` | `timestamptz` | NO | 默认 `now()` | |

**PK**：`(model_id, image_id)`。  
**同步规则**：若 `synced_version <> images.payload_version` → 须重推该点 payload（向量可不变）。缺行 → 须（重新）embed + upsert 点。

**后台对账**（2026-11-11，`app/services/retrieval/reconcile.py`）：API 进程按 `RECONCILE_CONFIG`（默认开启；每 `interval_seconds`=300 秒一轮，首轮在启动 `initial_delay_seconds`=30 秒后）在后台执行上面的规则：① 当前模型下记账落后（有记账行且 `synced_version < payload_version`）的图片补推 payload，每轮至多 `max_payloads`（2000）张，按 id 从上一轮停下的位置接着走、走完一圈再从头，一直推不上的（点已不在 collection，要重新生成向量）不会挡住其他图片；② 清理删除队列 `image_deletions`（§2.5），每轮至多 `max_deletions`（200）条，某批一条也没清掉就停（最早的几条一直失败，留给下一轮）。缺记账行的（当前模型下没有向量）推 payload 补不上，不在此列。多进程靠 PG 会话级咨询锁互斥，拿不到锁的进程跳过这一轮；停服时进行中的一轮在下一批之前结束。不开 API 或关掉后台对账时，用 `python -m app.workers.reconcile`（cron，或 `--loop`）。

---

### 2.12 `external_candidates`

外部图搜结果先入本表人工审核，**不写 Qdrant**；通过后以 `source_type='external'` 普通图片入库。

| 字段 | 类型 | Null | 约束 | 说明 |
|---|---|---|---|---|
| `id` | `uuid` | NO | PK | 候选 id（建议随机 uuid，非内容哈希） |
| `provider` | `text` | NO | — | 外部服务名 |
| `source_url` | `text` | NO | — | 结果原链接 |
| `staged_url` | `text` | NO | — | S3 `staging/` 完整 https |
| `external_label` | `text` | YES | — | 外部标签文字 |
| `query_ref` | `text` | YES | — | 触发查询引用（请求 ID 等） |
| `review_status` | `text` | NO | CHECK ∈ `pending`/`accepted`/`rejected`；默认 `pending` | |
| `reviewed_at` | `timestamptz` | YES | — | 待审为空 |
| `image_id` | `uuid` | YES | FK → `images.id` | 入库后回填 |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |

**约束**：`UNIQUE (provider, source_url)`。  
**索引**：`INDEX (review_status, created_at)`。

---

### 2.13 DDL 草案骨架（非可执行定稿 · 供后端对照）

```sql
CREATE EXTENSION IF NOT EXISTS ltree;

-- heating_groups, categories, category_names,
-- images, image_deletions,
-- scenes, scene_labels, scene_rules, image_scenes,
-- embedding_models, image_embeddings,
-- external_candidates
-- （字段与 §2.1–2.12 一致；触发器：categories.path 维护、current 模型唯一等 → 实现刀）
```

完整可跑 DDL **不在 M1 契约必交**；后端落地时以本字典为准出 migration。

**已落地（2026-11-05）**：`backend/app/models/`（ORM，SSOT）+ `backend/alembic/versions/` `0001`（建表 + ltree + path 触发器 + 场景种子）/ `0002`（对齐 ORM：命名、ON DELETE、CHECK、默认值、索引、注释）。

---

## 3. 与现网 MVP SSOT 差量表

| 维度 | MVP SSOT（现网目标态） | PG v0.1（本契约） |
|---|---|---|
| 元数据库 | MySQL InnoDB | **PostgreSQL** + `ltree` |
| 图片主键 | `BIGINT UNSIGNED` 自增 | **`uuid`** = SHA-256 前 16 字节 |
| 去重 | `path` UNIQUE（前缀 768） | **`sha256` UNIQUE**；无 path |
| 文件定位 | `path` + 可选 `url` | **仅** `original_url` / `thumb_url`（完整 https） |
| 软删 | `status active/deleted` | **`image_deletions` + 删行**；清 Qdrant/S3 |
| 场景存储 | `images.scenes` JSON 数组 | **`scenes` 登记表 + `image_scenes` + 规则** |
| 场景取值 | 登记 `normal`/`device` | **`general`/`oven`/…**（可配置加行） |
| 类别 | `images.category` VARCHAR | **`categories` 树 + 多语言名 + heating** |
| 向量注册 | `embedding_collection_meta` + 配置单 collection | **`embedding_models` 一行一 collection** |
| Qdrant collection | 固定 `image_embeddings` | **`embedding_models.collection`**（current 模型） |
| Point id | = BIGINT `images.id` | = **uuid `images.id`** |
| Payload | MVP 几乎不落业务 payload（权威 MySQL） | **`category_id`/`scenes`/`food_state`/`label_status` + 索引** |
| 外部图 | FlyB 同步进结果；无候选表 | **`external_candidates` 审核后入库**；**FlyB 检索兜底本轮不做** |
| 入库优化 §9 | path+OSS 双轨草案 | **作废**，见 §0.1 |

---

## 4. Qdrant

### 4.1 Collection

| 项 | 约定 |
|---|---|
| 数量 | **每个 `embedding_models` 行一个** collection |
| 名称 | `embedding_models.collection`（如 `food_dinov3h_v1`） |
| 向量 | 命名向量 **`global`**；`size`=`dim`；距离映射：`cosine`→Cosine，`dot`→Dot，`euclid`→Euclid |
| 点 ID | **`images.id`（uuid）** |
| V2 patch | 以后新 collection 再加；本契约不建 |

### 4.2 Payload（过滤权威副本；元数据权威仍在 PG）

| 字段 | 类型（Qdrant） | 来源 |
|---|---|---|
| `category_id` | integer（可 null） | `images.category_id` |
| `scenes` | string[] | 有效场景 **code** 列表（非 id） |
| `food_state` | keyword / string（可 null） | `image_foods.food_state`（2026-11-09 起；无标注行时为 null） |
| `label_status` | keyword | `images.label_status` |

**Payload 索引**：

- `scenes` → **keyword**
- `label_status` → **keyword**
- `category_id` → **integer**

### 4.3 检索时（与设计一致）

1. API 传入场景 **code**（如 `oven`）。
2. 只在 `scenes` 含该 code 的点中搜 top-k（`include_all` 场景的成员集合由规则/账表保证写入 payload）。
3. 若场景有 `scene_labels`：将命中图的通用类别映射为场景类别再投票；否则按通用类别投票。
4. `outputs_heating=true` 时结果另带加热组与 `food_state`。

### 4.4 与 `image_embeddings` / `payload_version`

- Upsert 点成功后写/更新 `image_embeddings (model_id, image_id, synced_version=images.payload_version)`。
- 元数据变 → `payload_version++` → worker 重推 payload。
- 删图 → 各 collection 删同 uuid 点（由 `image_deletions` 驱动）。

---

## 5. S3 路径约定

数据库只存 **完整 https**；对象键 **不** 含场景/类别（改标签不搬文件）。

下表是相对键（无对象键前缀时的完整对象键；有前缀时为后缀）。扩展名、id 规则不因前缀改变。

| 对象键（相对） | 内容 | 落库字段 |
|---|---|---|
| `original/{image_id}.{ext}` | 原图 | `images.original_url` |
| `thumb/{image_id}.webp` | 缩略图，长边 512 | `images.thumb_url` |
| `staging/{candidate_id}.{ext}` | 外部候选暂存 | `external_candidates.staged_url` |

**可选对象键前缀 `key_prefix`**（本刀实施规范 · 本地已实现，待独立验收，非「总裁已拍」细字）：`IMAGE_STORAGE_CONFIG` 顶层可选字符串，与 `bucket` / `public_base_url` 同层。省略、`null`、空串或规范化后为空则不上前缀，键即上表，行为与现网一致。非空时去首尾空白与斜杠、把反斜杠/全角斜杠换成 `/` 并压缩重复斜杠，作为所有 `original` / `thumb` / `staging` 键的前缀，例如 `ImageEmbedding/original/{image_id}.jpg`。规范化拒绝 `.` / `..` 段、段名恰好为 `original`/`thumb`/`staging`、中间空白与非法字符、以及过长值（实施默认 128，报错不裁剪）；允许多级目录；以 `/` 开头只去掉首斜杠，不当文件系统绝对路径。大小写保持原样。不要把桶名、账号或密钥写进默认值。

**ASSUMPTION A8**：bucket/CDN 前缀由部署配置拼进完整 URL；契约不绑死域名。  
此处「bucket/CDN 前缀」指 `public_base_url`（桶或 CDN 的 URL 根），**不是**对象键前缀。公开 URL 为 `public_base_url.rstrip("/") + "/" + key.lstrip("/")`，其中 `key` 已是可能带 `key_prefix` 的对象键。同一前缀不要既写进 `public_base_url` 又写进 `key_prefix`，否则 URL 路径会加倍。真传 OSS/S3 SDK、凭证与生命周期 **另刀**（本契约只定键与字段）。

已落盘对象与已入库的 `original_url` / `thumb_url` / `staged_url` **不迁移**；`key_prefix` 只影响新写入键。读取、删除、reembed 走存库 URL（或已记录的键），不得用当前前缀回推旧键。前端只消费接口已有完整 URL，本轮不改 FE，不加签名 URL。

---

## 6. API 差量提纲（资源边界 · 细路径多标 TBD）

> 前缀仍建议 `/api/...`。下列为相对 MVP 的资源边界；细 path/body **TBD**，实现前须回写本契约一小节。

| 资源 | MVP | PG v0.1 差量 |
|---|---|---|
| **Index** `POST /api/index` | 本地 path 入库、scenes JSON、BIGINT id | 收字节或可拉 URL → 算 sha256/uuid → 写 S3 → 写 `images` → embed → upsert **current** collection；场景经规则/`image_scenes`；**无 path**。**2026-11-11**：multipart / JSON 另可传 `label_status`（`auto`/`verified`，要有类别）、`source_type`、`license`（取值见 `/api/enums`，不传为 `user_upload` / `unknown`）；`indexed[].created` 为 `false` 表示相同图片此前已入库、沿用首次的标注 |
| **Search** `POST /api/search` | `scene` 默认 `normal`；拒识；仅 normal 空→FlyB | `scene` 改为默认 **`general`**（A5）；按 payload `scenes` 过滤；场景标签投票；`outputs_heating` 附加字段；**`score_threshold` 沿用 MVP（A6）**；自建空 → **`source=empty`**；**FlyB 编排 OUT OF SCOPE（A4 / M0）** |
| **List** `GET /api/images` | 分页、scene、title/category、仅 active | 无 soft-status：未删即在库；scene 成员过滤；返回 `original_url`/`thumb_url`、类别树字段、场景 codes；category 改 ID/code。**2026-11-11**：新增 `category_id`（正整数，这个类别及其所有下级类别，ltree 子树；未标注的图片不匹配）和 `label_status`（`unlabeled`/`auto`/`verified`，非法值 422）筛选；原 `category`（编码 / 名称 / ID，只匹配一个类别）保留 |
| **单图** `/api/images/{image_id}`（**已落地 2026-11-11**） | 无 | `GET` 详情（全部字段 + 向量写入记录 + 当前 collection 里的向量状态 `synced`/`stale`/`missing`/`unavailable`）、`PATCH` 改类别 / 标注状态 / 描述 / 标签 / 食物标注（提交后推 payload）、`POST …/vector-sync` 重推这张图的 payload、`DELETE` 删除、`GET …/file` 读回存储里的缩略图或原图。见 §6.3。新错误码 `IMAGE_NOT_FOUND` 404 |
| **类别** `/api/categories`（**已落地 2026-11-10**） | 无独立 API | `GET /api/categories` 返回整棵树（不分页）`{ ok, categories: CategoryItem[], heating_groups: [{id, code, name}], root_codes: string[] }`，节点按「父在子前、同级 `sort_order`、`id`」排好；`POST /api/categories`（201）新建；`PATCH /api/categories/{id}` 只改请求里给了的字段（`parent_id` 换父节点，整棵子树一起移动，`null` 移到根；`names` 给了就整体替换；`heating_group_id: null` 清空）；`DELETE /api/categories/{id}` 返回 `{ ok, deleted: {id, code} }`。`CategoryItem` = `{ id, code, parent_id, path, name, names: [{lang, name, kind}], cuisines, heating_group, sort_order, is_active, image_count, scene_rule_count, created_at, updated_at }`（`name` 是中文主名；`cuisines` / `heating_group` 是节点自己设的，不含沿用祖先的）。规则：`code` 匹配 `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$`、全局唯一；根节点只能用四个域的编码（新建、移到根、给根改编码时校验）；`names` 必须含中文（`zh`）主名，每种语言至多一个主名；不能移到自己或子孙下面；还有子类别、图片或场景规则引用时不能删除（场景规则不再被级联悄悄删掉）。错误码 `CATEGORY_NOT_FOUND` 404、`CATEGORY_CODE_EXISTS` 409、`CATEGORY_IN_USE` 409、`CATEGORY_POSTGRES_FAILED` 502、`VALIDATION_ERROR` 422。成功体不带 `trace_id`（在响应头 `X-Trace-Id`）。字段和上限见 `backend/app/schemas/categories.py`、`backend/app/core/category_rules.py`。加热组只随列表只读返回，管理仍 **TBD**。**2026-11-11 起**移动类别（`parent_id` 变化）或改菜系（集合有变化）时，同一事务里重算标在该节点及其子孙上的图片的 `image_scenes`，有效场景集合变化的图片 `payload_version` +1；提交后检索栈可用时把这些图片的 payload 推到当前 collection（一次请求至多 2000 张，分批），结果在 `PATCH` 成功体的 `scene_sync`：`{ images, changed, pushed, pending, vector_sync: pushed\|partial\|not_ready\|failed, sync_required }`，其余修改为 `null`；推送失败不影响 200，没推上的由后台对账补上（§2.11 同步规则下的「后台对账」） |
| **场景** | 写死登记 | `scenes` / `scene_rules` / `scene_labels` 管理（`/api/scenes`，字段见 `backend/app/schemas/scenes.py`）；图片成员人工覆盖三接口见 §6.2（本地实现，待独立验收）。**2026-11-10 链路优化**：配置写接口的成功体除 `scene`（删除时 `deleted`）外另带 `sync_required: boolean`——`true` 表示改动要经 `POST /api/scenes/sync` 才作用到已入库图片（规则增删、规则换目标、场景 `is_active` / `include_all` 实际变化、新建启用的 `include_all` 场景、删除有成员的场景），`false` 表示已生效（场景类别、名称、描述、`outputs_heating`、只改规则映射的类别，均在请求时读取）。`PATCH /api/scenes/{id}/rules/{rule_id}` 现可原地换目标：给 `category_id` 或 `cuisine`（恰好一个非空，另一个清空）即替换，单事务、规则 id 不变；与本场景其他规则重复 `SCENE_RULE_EXISTS` 409；按菜系的规则不能带 `label_id`（换成菜系时同请求传 `label_id: null`，否则 422）。不再需要「删旧建新」两次请求 |
| **审核候选** | 无 | `external_candidates` list/accept/reject；accept → 入库流水线 |
| **模型** | 配置 + meta 表 | `embedding_models` 状态机；切 `current`；重建进度 **TBD** |
| **删除** | 软删 status | `DELETE /api/images/{image_id}`（**已落地 2026-11-11**，§6.3）→ `image_deletions` 流水线；检索栈可用时同一请求里清掉当前 collection 的点和两个文件 |
| **Health** | 聚合 Qdrant+MySQL | 改探 PG + Qdrant(current collection) +（可选）S3；回报 current model/collection |
| **枚举** `/api/enums`（**已落地 2026-11-09**） | 写死在代码 | `GET /api/enums`、`GET /api/enums/{name}` 返回取值 + 中文名 + `editable`；`POST /api/enums/{name}/values` `{value, label, description?}` 给可编辑枚举追加取值（201，返回整个枚举），写回 `backend/config/param/enums.yaml`。错误码 `ENUM_NOT_FOUND` 404、`ENUM_NOT_EDITABLE` 403（系统枚举）、`ENUM_VALUE_EXISTS` 409、`ENUM_FILE_INVALID` 503、`VALIDATION_ERROR` 422。字段见 `backend/app/schemas/enums.py` |

**结果卡形状**：旧 SSOT §9 统一 wire **仍作废**（见 §0.1）。前后端统一 wire 见 **§6.1（已拍 2026-11-05 · 一刀切；2026-11-08 更正 `description` 允许、`heating_group` 为对象；2026-11-12 更正：自建 hit 与 FlyB 结果同为一张结果卡，见 §6.1.1）**；实现须按已拍定案改 BE hit/list 与 FE 适配层。

**错误码**：MVP 已定码能复用的复用（如 `SEARCH_BAD_SCORE_THRESHOLD`）；uuid/sha 冲突、未知 scene code、无 current 模型等 **TBD 补表**。

**成功响应（2026-11-06 起）**：每个 JSON 响应体带 `ok`；predict / 检索的响应体同时带 `trace_id`（与响应头 `X-Trace-Id` 一致，客户端未传时由服务端生成）。Index 结果字段与 List 对齐：`filename` 取代 `title`，去掉 `qdrant_point_id`；失败项为 `{filename, error, detail}`。字段定义见 `backend/app/schemas/`。

**错误响应（2026-11-06 起全部接口统一）**：`{"ok": false, "error": "<CODE>", "detail": "<说明>", "trace_id": "<id>"}`，请求参数校验失败另带 `errors`（字段级列表）；响应头 `X-Trace-Id` 与 `trace_id` 相同（客户端可用请求头 `X-Trace-Id` 指定）。通用码：`VALIDATION_ERROR` 422、`NOT_READY` 503、`EMPTY_FILE` 400、`FILE_TOO_LARGE` 413、`UNSUPPORTED_MEDIA_TYPE` 415、`INVALID_URL` 422、`FORBIDDEN_HOST` 403、`DOWNLOAD_FAILED` 502（或上游状态码）、`DOWNLOAD_TIMEOUT` 504、`UPSTREAM_ERROR` 502、`UPSTREAM_TIMEOUT` 504、`INTERNAL_ERROR` 500（不回传异常原文）；检索专用码见 `backend/app/services/retrieval/errors.py`，定义见 `backend/app/core/errors.py`。

---

---

## 6.1 前后端 Wire / 结果卡（**已拍 · 2026-11-05 · 一刀切**；**2026-11-08 更正**）

> **状态**：**已拍 · 2026-11-05 · 一刀切**（P1–P12）。**2026-11-08 总裁拍更正**（以 `backend/app/schemas/retrieval.py` 为准）：`description` 允许（更正 P6）；`heating_group` 改为对象 `{id, code, name}`（更正 W2）。**2026-11-12 更正（结果卡统一）**：自建检索 hit 与 FlyB 结果（`POST /api/predict/searchImage_*`）改为同一张结果卡——顶层只有 `tags` / `description` / `original_url` / `similarity`，其余字段一律放进 `details`；Search hit 的 `tags` 改为字符串（更正 P1 缩略图位置、P7、`tags` 类型）。以 `backend/app/schemas/search_hit.py` 为准，见 §6.1.1。  
> **本刀交付**：补齐 §6「细字段 TBD（FE 对齐刀）」——只定 **HTTP JSON wire**（snake_case），不复活旧 §9，不写业务代码。  
> **摸底基线（2026-11-05 本机核实）**：  
> - BE Search hit（`application/retrieval/search_image.py`）仍吐 FlyB 旧壳：`image_url`/`thumbnail_url`、`site_name`、`text_snippets:[]`、嵌套 `details.{page_url,site,text_snippets,images[]}`；加热时另带 `scene_label`/`heating_group`/`food_state`；envelope 用 `result` + `source=self|empty`（空时 `result` 竟为嵌套 `{results,status,message}` 对象）。  
> - BE List（`list_images.py`）权威已偏 PG：`original_url`/`thumb_url`，同时兼容吐 `image_url`/`thumbnail_url`。  
> - FE Search（`frontend/src/lib/search.ts` + `entities/search-result`）：UI camel；`normalize` 仍读 `text_snippets`/`details`；`pickSearchImageUrls` 优先 `thumb_url`/`original_url`。  
> - FE Library（`entities/image`）：已偏 PG https-only（`thumb_url`→`original_url`→遗留别名）。  
> - 表侧 `external_candidates` 已有（§2.12）；API 差量已列（§6），细 wire 本小节补形状，**实现标 TBD 刀**。  
> **M0 冻结不回退**：空库重建；FlyB OUT OF SCOPE；`scene_labels` 纳入；空搜 → `source=empty` 不调外搜。

### 6.1.0 已拍定案清单（P1…Pn）

> **拍板日期**：2026-11-05 · **迁移**：**一刀切**（P10）· P1–P12 **全按建议**。

| # | 议题 | 已拍定案 | 备注 |
|---|---|---|---|
| **P1** | Search/List 权威 URL 字段名 | **已拍**：`original_url` / `thumb_url` 为唯一权威（完整 https） | 与 §2.4 / §5 一致 · 拍板日期 2026-11-05。**2026-11-12**：Search hit 顶层只留 `original_url`，缩略图在 `details.images[0].thumbnail_url`（§6.1.1）；List 不变 |
| **P2** | 是否一期保留 `image_url` / `thumbnail_url` 别名 | **已拍**：**不保留**（一刀切）；无兼容窗 | 现状 BE search 只吐别名、list 双吐 → 实现刀须同发删。**2026-11-12**：顶层别名仍禁；`details.images[]` 里的 `image_url` / `thumbnail_url` 是图片条目的键名（与 FlyB 同名），不是别名 |
| **P3** | Search envelope 数组键名 | **已拍**：权威键 = `items`（始终 array）；**禁**把对象塞进 `result` | 现状 BE 用 `result`；FE 须只读 `items` |
| **P4** | 空结果形态 | **已拍**：`source=empty` + `items: []`；可选顶层 `message`；**禁**嵌套空壳 | 对齐 A4；FE 可简化 |
| **P5** | `title` 是否出现在 wire | **已拍**：**禁**（`images` 无 title 列）；展示名用 `category` / `scene_label` | 现状 BE 用 category_name 冒充 title |
| **P6** | `description` | **已拍更正 2026-11-08**：**允许**。类型 `string`，默认 `""`。Index 请求（`IndexJsonRequest.description`）、Index 响应（`IndexedImage.description`）、Search hit（`SearchHit.description`）、List item（`ListImageItem.description`）均带此字段 | 对应 `images.description`（§2.4 / 迁移 `0003`）；**不进** Qdrant payload；改描述不 bump `payload_version` |
| **P7** | `site` / `site_name` / `page_url` / `snippets` / `text_snippets` / `details.*` | **已拍**：**全禁**（FlyB 壳）。**2026-11-12 更正**：`details` 是结果卡的必有字段（两通道同名，内容按通道）；`page_url` / `site` 只出现在 FlyB 结果的 `details` 里；`snippets` / `text_snippets` 仍禁（FlyB 文字片段并入 `description`） | 见 §6.1.1 |
| **P8** | `scene_label` 类型 | **已拍**：`string \| null`（场景展示名）；禁止随意 object | 若需 id+name 对象另开拍板（见 ASSUMPTION W3） |
| **P9** | `outputs_heating` 放哪 | **已拍**：**仅 envelope** 布尔；hit 上仅当 true 出现 `heating_group`/`food_state` | 避免双源 |
| **P10** | 迁移策略 | **已拍**：**一刀切**（BE 改吐 + FE 适配同发）；B 兼容窗 **未采纳** | 见 §6.1.6 |
| **P11** | List 是否保留 `qdrant_point_id` | **已拍**：**禁**（恒等于 `id`） | 现状 list 仍吐 |
| **P12** | `external_candidates` API 本轮是否实现 | **已拍**：本契约**只定最小 wire**；落地 **TBD 实现刀** | 见 §6.1.3 |

> P1–P12 已全部冻结。未出现在上表且未标「已定 / M0 / 已拍」的条款，一律视为 **ASSUMPTION / 草案**，不得当已拍板实现。

### 6.1.1 Search 候选 / 结果卡（`POST /api/search`）

#### Envelope（响应顶层）

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ok` | 是 | `boolean` | 成功为 `true`（业务空结果仍 `true` + `source=empty`） |
| `trace_id` | 否 | `string \| null` | 请求追踪 |
| `source` | 是 | `"self" \| "empty"` | **本轮禁止 `"flyb"`**（A4 / M0）；有命中 → `self`；无命中 → `empty` |
| `scene` | 是 | `string` | 实际检索场景 code（默认 `general`，A5） |
| `outputs_heating` | 否 | `boolean` | **仅 envelope**（P9 已拍）；回显该 scene 是否加热输出；缺省时 FE 不得猜 |
| `items` | 是 | `array` | **始终为数组**；`source=empty` 时必须 `[]` |
| `message` | 否 | `string` | 给人看的空结果说明；仅建议在 `empty` 时出现 |

**禁（envelope）**：把空结果做成非 array 的 `result` 对象；顶层再挂一套 `results`/`status`/`upstream_error` 双轨（旧 FlyB 空壳）。  
**`result` 键**：**禁**（P3 已拍 · 一刀切）。不得再吐 `result`/`results` 别名；空结果亦不得把对象塞进 `result`。B 兼容窗 **未采纳**。

请求侧沿用 §6 / 附录 Q：`scene`、`top_k`、`score_threshold`（A6）、file 或 `img_url`；细 path **已有实现者不在本刀重开**。

#### Hit = 结果卡（`items[]` 元素；**2026-11-12 起与 FlyB 结果同形**）

自建检索 hit 与 FlyB 结果（`POST /api/predict/searchImage_*` 的 `result[]`）是同一张结果卡：顶层只有下面四个字段和 `details`，其余字段一律在 `details` 里（`backend/app/schemas/search_hit.py` `SearchHitBase`；自建 `SearchHit`、FlyB `PredictHit` 都继承它）。

| 字段 | 必填 | 类型 | 自建（`POST /api/search`） | FlyB（`POST /api/predict/searchImage_*`） |
|---|---|---|---|---|
| `tags` | 是 | `string` | `images.tags` 用「、」连接；无标签为 `""`。仅供展示：标签本身可能含「、」，要标签数组请读单图 / List | 结果标题（`Title`）；没有为 `""` |
| `description` | 是 | `string` | `images.description`；默认 `""`；不进 Qdrant payload | 文字片段（`Snippet` 中 `Type=text`）去首尾空白后用空格连接；没有为 `""` |
| `original_url` | 是 | `string \| null` | 原图（https）；**权威** | 第一张图的原图；没有原图时为最大的缩略图（总是 URL 字符串，不会是尺寸字典）；开了相似度过滤时为实际下载、比对过的那张 |
| `similarity` | 是 | `number \| null` | 向量检索分数；合法有限数；**禁止**拼写 `similiar` | 与查询图的 embedding 相似度；没开相似度过滤（`GLOBAL_FILTER.switch=false`）时为 `null` |
| `details` | 是 | `object` | 见「自建 `details`」 | 见「FlyB `details`」 |

**自建 `details`**（`SearchHitDetails`）：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | 是 | `string`（uuid） | = `images.id` = Qdrant point id |
| `category_id` | 否 | `integer \| null` | |
| `category` | 否 | `string \| null` | 通用类别**展示名**（由 `category_names` 解析；非 code） |
| `scenes` | 否 | `string[]` | 有效场景 code 列表 |
| `scene_label` | 条件 | `string \| null` | 当前检索 scene 下的场景类别展示名；做了场景标签映射才出现（映射到无标签时为 `null`） |
| `label_status` | 否 | `string` | `unlabeled`/`auto`/`verified` |
| `images` | 是 | `array` | 一项 `{image_url, thumbnail_url, width, height}`：原图（同顶层 `original_url`）、缩略图（512px WebP；**权威**，即 List 的 `thumb_url`）、宽高。键名与 FlyB `details.images[]` 相同 |
| `food_state` | 条件 | `string \| null` | 食物状态，取值见 `GET /api/enums/food_state`（初始 `raw`/`cooked`/`frozen`）。**仅当**该 scene `outputs_heating=true` 时可出现 |
| `food_processing` | 条件 | `string \| null` | 加工状态，取值见 `GET /api/enums/food_processing`；出现条件同 `food_state`（2026-11-09 新增） |
| `food_count` | 条件 | `integer \| null` | 食物个数；出现条件同 `food_state`（2026-11-09 新增） |
| `total_mass_g` / `water_mass_g` / `main_ingredient_mass_g` | 条件 | `integer \| null` | 总质量 / 含水质量 / 主食材质量（克）；出现条件同 `food_state`（2026-11-09 新增）。六个食物字段存于 `image_foods`（§2.4） |
| `heating_group` | 条件 | `{ "id": int, "code": string, "name": string }` | `HeatingGroup` 对象。**仅当**该次检索对应 scene 的 `outputs_heating=true` 且用例已设置时出现（实现 `exclude_unset`，嵌套的 `details` 同样生效：未设置则键不出现）；`outputs_heating=false` 时不出现 |

**FlyB `details`**（`PredictHitDetails`，值按上游原样）：`rank`（`Rank`）、`page_url`（`Url`）、`site`（`HostInfo`，目前为 `{Hostname, IconUrl}`，`Hostname` 是站点名）、`images[]`（`{image_url, thumbnail_url, alt, width, height}`；`thumbnail_url` 是按尺寸给的字典，目前为 `{Tiny, Small, Medium, Large}`，BE / FE 选图时尺寸名不区分大小写，按 large → medium → small → 其他）、`document_info`（`DocumentInfo`）。

**FlyB envelope**（不变）：`{ ok, trace_id, result }`，`result` 为结果卡数组；上游返回空结果时为 `{ request_id, total_doc_count, result_count, results: [], status: "upstream_empty_response", upstream_error: {code, message} }`。

**禁字段（结果卡顶层）**——出现即视为违约：

| 禁字段 | 原因 |
|---|---|
| `id` / `thumb_url` / `category_id` / `category` / `scenes` / `scene_label` / `label_status` / `width` / `height` / 六个食物字段 / `heating_group` | 2026-11-12 起都在 `details` 里 |
| `image_url` / `thumbnail_url` | 非权威 URL；**P2 已拍禁**（图片条目的这两个键只在 `details.images[]` 里） |
| `text_snippets` / `snippets` | FlyB 文字片段并入 `description` |
| `page_url` / `site` / `site_name` / `rank` | FlyB 的在 `details` 里；自建没有 |
| `similiar` | 错误拼写 |
| `title` | 表无列；**P5 已拍禁**；FlyB 的标题放在 `tags` |
| `source`（hit 级） | 渠道只在 envelope；禁止 hit 自报 flyb/self |

**`description` 三接口口径（2026-11-08 已拍更正）**：

- Index 请求 `IndexJsonRequest.description`：`string`，默认 `""`。
- Index 响应 `IndexedImage.description`：`string`，默认 `""`。
- Search `SearchHit.description` 与 List `ListImageItem.description`：`string`，默认 `""`。
- 列存 `images.description`（§2.4）；**不进** Qdrant payload；修改不 bump `payload_version`。

**`tags` 口径（2026-11-09 新增）**：

- Index 请求：JSON `IndexJsonRequest.tags` 为 `string[]`；multipart 的 `tags` 是 JSON 字符串数组文本，如 `["红薯叶"]`。不传即 `[]`。不是字符串数组 → 422 `VALIDATION_ERROR`。
- Index 响应 `IndexedImage.tags`、List `ListImageItem.tags`：`string[]`，默认 `[]`，总是出现。
- Search hit（结果卡）`tags`：**2026-11-12 起为 `string`**，`images.tags` 用「、」连接，无标签为 `""`，总是出现（与 FlyB 结果的标题同一个字段）。
- List 查询：`q` 的子串匹配范围加上标签（任一标签含 `q` 即命中）；新增 `tag`，精确匹配 `tags` 中的一个元素（区分大小写，走 GIN 索引），trim 后为空视为未传。
- Search 不按标签过滤（标签不在 Qdrant payload 里）。
- 列存 `images.tags`（§2.4）；**不进** Qdrant payload；修改不 bump `payload_version`。

**`heating_group` 对象口径（2026-11-08 已拍更正）**：形状为 `{ "id": <int>, "code": <string>, "name": <string> }`（`HeatingGroup`）。Search hit（2026-11-12 起在 `details` 里）与 List `ListImageItem` 仅当该次请求所针对 scene 的 `outputs_heating=true` 且用例已赋值时出现；实现用 `exclude_unset`，未设置则 JSON 键不出现。`outputs_heating=false` 时不出现。Search envelope 的 `outputs_heating` 布尔仍仅在 envelope（P9 不变）。

### 6.1.2 List item 与 Search 对齐 / 差量（`GET /api/images`）

#### List envelope

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ok` | 是 | `boolean` | |
| `trace_id` | 否 | `string \| null` | |
| `scene` | 是 | `string` | 过滤场景 code（默认 `general`） |
| `page` | 是 | `integer` | ≥1 |
| `page_size` | 是 | `integer` | 1..100 |
| `total` | 是 | `integer` | |
| `items` | 是 | `array` | |

#### List item vs Search hit

> 2026-11-12 起 Search hit 是结果卡（§6.1.1）：下表 Search 列写了字段在卡里的位置；List item 仍是扁平结构。

| 字段 | Search | List | 差量说明 |
|---|---|---|---|
| `id` | ✓ `details.id` | ✓ | 同值 |
| `original_url` / `thumb_url` | `original_url` 顶层；缩略图 `details.images[0].thumbnail_url` | ✓ 权威 | 同值；https-only |
| `similarity` | ✓ 顶层 | **禁** | List 非检索打分 |
| `category_id` / `category` | ✓ `details.*` | ✓ | 同值 |
| `scenes` | ✓ `details.scenes` | ✓ | 同值 |
| `scene_label` | ✓ `details.scene_label`（按检索 scene） | **通常不出现** | List 无「当前投票 scene 标签」语义；若产品要展示另刀 |
| `label_status` | 可选 `details.label_status` | 可选 | 同值 |
| `width` / `height` | 可选 `details.images[0].*` | 可选 | 同值 |
| `description` | ✓ 顶层，默认 `""` | ✓ 默认 `""` | 同；Index `IndexedImage` 亦同 |
| `tags` | ✓ 顶层 `string`（「、」连接），默认 `""` | ✓ `string[]`，默认 `[]` | **类型不同**（2026-11-12）；Index `IndexedImage` 同 List；List 可按 `tag` 精确筛选、`q` 子串命中标签 |
| `food_state` / `food_processing` / `food_count` / `total_mass_g` / `water_mass_g` / `main_ingredient_mass_g` / `heating_group` | 加热 scene 条件出现，在 `details` 里 | 同（该 scene `outputs_heating=true`） | `heating_group` 为对象 `{id,code,name}`，非 string；出现规则见 §6.1.1；Index `IndexedImage` 总是带六个食物字段 |
| `created_at` / `updated_at` | **禁**（非检索所需） | 可选 `string`（ISO-8601 / timestamptz 序列化） | List 可展示入库时间 |
| `image_url` / `thumbnail_url` | **禁**（P2 已拍） | **禁**（P2 已拍） | 双边同时删除（一刀切） |
| `qdrant_point_id` | 禁 | **禁**（P11 已拍） | 与 `id` 重复 |
| `title` | 见 P5；表无列 | 同 | 附录 M：title 无对等列（`tags` 见上，迁移 `0007` 起有列） |

### 6.1.3 `external_candidates` 最小 wire（**形状已拍 · 实现 TBD · P12**）

> 表字典见 §2.12。下列 API **本轮只定 JSON 形状**，路由落地、鉴权、入库流水线 → **TBD 实现刀**（P12）。

#### `GET` 列表（建议路径 TBD，如 `/api/external-candidates`）

**Query（建议）**：`review_status`（默认 `pending`）、`page`、`page_size`。

**Envelope**：同 List 风格 — `ok`、`page`、`page_size`、`total`、`items`。

**Item**：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | 是 | `string`（uuid） | 候选 id |
| `provider` | 是 | `string` | |
| `source_url` | 是 | `string`（https） | 外部原链 |
| `staged_url` | 是 | `string`（https） | S3 staging |
| `external_label` | 否 | `string \| null` | |
| `query_ref` | 否 | `string \| null` | |
| `review_status` | 是 | `"pending"\|"accepted"\|"rejected"` | |
| `reviewed_at` | 否 | `string \| null` | |
| `image_id` | 否 | `string \| null` | 入库后回填 |
| `created_at` | 是 | `string` | |

#### `POST …/{id}/accept`（路径 TBD）

**Request body（最小）**：可空对象 `{}`；可选标注字段（`category_id` / `food_state` 等）**TBD 实现刀**补表。  
**Response（最小）**：

```json
{ "ok": true, "id": "<candidate_uuid>", "review_status": "accepted", "image_id": "<images.uuid>" }
```

#### `POST …/{id}/reject`（路径 TBD）

**Request body（最小）**：`{}` 或 `{ "reason": "…" }`（reason 是否落库 TBD）。  
**Response（最小）**：

```json
{ "ok": true, "id": "<candidate_uuid>", "review_status": "rejected", "image_id": null }
```

**说明**：accept 成功后须走普通入库（`source_type='external'`），写 `images` + embed + Qdrant；**不**在候选表写向量。FlyB **自动**灌入本表的编排仍 OUT OF SCOPE（A4）；本 API 供人工/日后导入通道使用。

### 6.1.4 FE 映射规则（wire snake → UI camel）

**硬规则**：

1. **Wire 一律 snake_case**；UI 层 camelCase **只**由 FE 适配层转换。  
2. **禁止双源猜字段**：某一 UI 属性只对应一张映射表中的**一个** wire 字段；不得再回落读 `details.*` / 旧别名（P2/P10 已拍一刀切，**无**兼容窗）。  
3. Library 与 Search 对 URL 的映射必须一致：展示缩略图 ← List `thumb_url` / Search `details.images[0].thumbnail_url`；打开原图 ← `original_url`。
4. 两个搜索通道的结果卡字段（`tags` / `description` / `original_url` / `similarity`）用同一个读法（FE `readCardFields`）；只有 `details` 按通道各读各的（2026-11-12）。

#### Search 一一对应

| Wire（snake） | UI（camel） | 备注 |
|---|---|---|
| `items[]` | `SearchResponse.items` | envelope |
| `source` | `SearchResponse.source` | 仅 `self`/`empty`；缺省不发明 |
| `scene` | `SearchResponse.scene` | |
| `outputs_heating` | `SearchResponse.outputsHeating` | |
| `trace_id` | `SearchResponse.traceId` | |
| `message` | `SearchResponse.message` | |
| `tags` | `tags`；`title` 依次取 `details.scene_label` → `details.category` → `tags` → 「未命名图片 N」 | string |
| `description` | `description` | 默认 `""`；一对一，勿猜其它字段 |
| `original_url` | → 适配为 `imageUrl`（原图） | **勿**再读 `image_url`（P2 已拍禁） |
| `similarity` | `similarity` | 禁读 `similiar` |
| `details.id` | `SearchImage.id` | |
| `details.images[0].thumbnail_url` | → 适配为 `thumbnailUrl` | 512px WebP |
| `details.images[0].width` / `.height` | `width` / `height` | |
| `details.category` | `category` | |
| `details.category_id` | `categoryId` | |
| `details.scenes` | `scenes` | |
| `details.scene_label` | `sceneLabel` | string |
| `details.heating_group` | `heatingGroup` | **对象**；子字段 `id` / `code` / `name` 保持，勿把 `name` 抽成顶层 string |
| `details.food_state` | `foodState` | |
| `details.food_processing` / `food_count` / `total_mass_g` / `water_mass_g` / `main_ingredient_mass_g` | `foodProcessing` / `foodCount` / `totalMassG` / `waterMassG` / `mainIngredientMassG` | 2026-11-09 新增；FE 适配 TBD |
| — | `pageUrl` / `siteName` | 自建恒为 `null` |

**现网 Global（predict）**：同一张卡、同样的四个字段读法；`title` 取 `tags`（FlyB 标题）→ 图片 `alt` → 「未命名图片 N」；`id` 为「image-N」；`imageUrl` ← `original_url`（不是安全 https 时取第一张有安全原图的 `details.images[]`）；`thumbnailUrl` / `alternateUrls` ← `details.images[].thumbnail_url` 按 large → medium → small → 其他（如 tiny）排，尺寸名不区分大小写；`pageUrl` ← `details.page_url`；`siteName` ← `details.site`（字符串，或 `{Hostname}` / `{name/Name/host/Host}`）→ 来源网页域名；类别、场景、食物字段恒为空。

#### List 一一对应

| Wire（snake） | UI（camel） | 备注 |
|---|---|---|
| `items[]` | `ListImagesResponse.items` | |
| `page` / `page_size` / `total` | `page` / `pageSize` / `total` | |
| `thumb_url` | → `LibraryImageItem.imageUrl`（展示） | 与现 `pickImageDisplayUrl` 目标一致，但**最终只认** `thumb_url` |
| `original_url` | `originalUrl` | |
| `description` | `description` | 默认 `""`；一对一 |
| `created_at` | `createdAt` | |
| `food_state` | `foodState` | |
| `food_processing` / `food_count` / `total_mass_g` / `water_mass_g` / `main_ingredient_mass_g` | `foodProcessing` / `foodCount` / `totalMassG` / `waterMassG` / `mainIngredientMassG` | 仅加热 scene；2026-11-09 新增，FE 适配 TBD |
| `heating_group` | `heatingGroup` | 对象 `{id,code,name}`；仅加热 scene |
| `scenes` | `scenes` / `sceneMemberships` | 若未来提供 `scene_memberships` 再扩展；本草案不强制 |

### 6.1.5 相对现状的删除 / 改名清单（破坏性）

| 对象 | 现状（本机核实） | 定案目标（已拍） | 动作 |
|---|---|---|---|
| Search hit URL | 只吐 `image_url`/`thumbnail_url` | `original_url`/`thumb_url` | **改名 + 删旧键**（P2 一刀切） |
| Search hit 壳 | `text_snippets`、`details.*`、`page_url`、`site_name` | 无 | **删除**（P7） |
| Search hit `title` | 用 category_name 冒充 | 无 title（P5 已拍禁） | **删除**（展示走 category/scene_label） |
| Search/List/Index `description` | 原 P6 结果卡禁 | 允许 `string`，默认 `""`；三接口都有 | **更正 2026-11-08**（以 `retrieval.py` 为准） |
| Search/List `heating_group` | W2 一期展示名 string | 对象 `{id, code, name}` | **更正 2026-11-08** |
| Search envelope 键 | `result`（空时可为对象） | `items` 恒 array（P3/P4 已拍） | **改名 + 形态修复**；禁 `result` |
| Search `source` | `self`/`empty` | 同；禁 flyb | **保持**；文档冻结 |
| List URL | 双吐权威+别名 | 仅权威（P2 已拍） | **删除别名** |
| List `qdrant_point_id` | 有 | 无（P11 已拍禁） | **删除** |
| 元数据库错误码（2026-11-07） | `INDEX_/SEARCH_/LIST_MYSQL_FAILED` | `INDEX_/SEARCH_/LIST_POSTGRES_FAILED`（HTTP 502 不变） | **改名** |
| `/api/health` 元数据组件（2026-11-07） | `components.mysql` | `components.postgres` | **改名** |
| FE `normalizeItem` | 读 `details`/`text_snippets`/`similiar`、多 URL 回落 | 单源映射表 | **删除回落枝**（实现刀） |
| FE `pickSearchImageUrls` | 仍回落 `thumbnail_url`/`image_url` | 只读 thumb/original | **收紧** |
| 旧 §9 结果卡 | 已作废 | 不复活 | **保持作废** |
| Search hit 形状（2026-11-12） | 扁平：`id` / `thumb_url` / `category` / 食物字段 … 都在顶层 | 结果卡：顶层 `tags` / `description` / `original_url` / `similarity`，其余进 `details`（§6.1.1） | **改形**（BE 改吐 + FE 适配同发） |
| Search hit `tags`（2026-11-12） | `string[]` | `string`（「、」连接） | **改类型**；Index / List 仍为 `string[]` |
| FlyB 结果（predict，2026-11-12） | `title` / `image_url` / `similiar`，`details.text_snippets` | 同一张结果卡：`tags` = 标题、`description` = 文字片段、`original_url`（总是 URL 字符串）、`similarity`；`details.{rank, page_url, site, images, document_info}` | **改名 + 改形**；直接调 `/api/predict/searchImage_*` 的设备端须同步改读法 |

### 6.1.6 迁移策略（**已拍 · P10 · 一刀切**）

| 方案 | 做法 | 定案 |
|---|---|---|
| **A · 一刀切** | 同一发布：BE 按 §6.1 吐字段；FE 适配层按映射表改；测试 bot 验搜索+图库 | **已拍采纳**（2026-11-05） |
| **B · 兼容窗** | BE 同时吐权威+别名 N 天；FE 只读权威；到期 BE 删别名 | **未采纳** |

**定案 = A · 一刀切**：BE 改吐与 FE 适配同发；无 URL 别名窗、无 `result` 别名窗。

### 6.1.7 本节 ASSUMPTION（**与已拍不冲突**；P1–P12 本身不再标 ASSUMPTION）

> 下列 W1–W5 **不是**对 P1–P12 的再开放；P1–P12 已于 2026-11-05 冻结。此处仅保留实现细节假设，且与已拍定案不冲突。

| # | ASSUMPTION | 说明 |
|---|---|---|
| W1 | Wire 编码 snake_case；时间字段 ISO-8601 / timestamptz JSON 字符串 | 与全文「Wire 字段名英文」一致；与已拍不冲突 |
| W2 | **已拍更正 2026-11-08**：`heating_group` 为对象 `{id: int, code: string, name: string}`（`HeatingGroup`）；不再使用展示名 string | 以 `backend/app/schemas/retrieval.py` 为准；出现条件见 §6.1.1；与 P9 不冲突 |
| W3 | `scene_label` 类型 **已由 P8 拍为** `string \| null`；若要 `{id,name,code}` 对象须**另开拍板**，不得默默改类型 | 对象形态仍属 ASSUMPTION / 未拍；string 本身已是已拍 |
| W4 | `source=empty` 时 **HTTP 仍 200** + `ok=true`（与现 BE 一致）；真正校验错误仍走既有 4xx 码 | 与 P4 不冲突 |
| W5 | 本轮 Search `source` 枚举**不包含** `flyb`；FE 类型里遗留 `flyb` 仅历史兼容，不得新写 | A4 / M0；与已拍不冲突 |

---

## 6.2 图片场景人工覆盖（2026-11-10 MST · 本地实现，待独立测试验收）

> 本节补细路径及 wire，不改变 Index/Search/List。无新增表/迁移/DB 枚举/CAS/权限系统。**2026-11-10 链路优化**：写成功后立即把这张图的 payload 推进当前向量库（见下方「写后推送」），不再必须跑全库同步。

| method + path | 请求 | 成功 |
|---|---|---|
| GET `/api/images/{image_id}/scene-memberships` | 无 | 200，下面的快照，不带 sync 字段 |
| PUT `/api/images/{image_id}/scene-memberships/{scene_id}` | `{is_member:boolean}` 必填、严格布尔、extra forbid | 200，同快照另带 `vector_sync`、`sync_required` |
| DELETE 同 PUT 路径 | 无 | 200，同快照另带 `vector_sync`、`sync_required`，只清该对 manual 覆盖并恢复当前规则 |

`image_id` 为 UUID，`scene_id` 为正整数且 ≤2147483647，使用目录 ID，不用 alias。成功体所有业务键必返，trace 在 `X-Trace-Id`；错误沿 §6 统一信封。

```text
{
  ok:true, image_id:string(uuid), payload_version:integer, scenes:string[],
  memberships:[{
    scene_id:integer, scene_code:string, is_active:boolean, include_all:boolean,
    is_member:boolean, origin:"implicit"|"rule"|"manual"|null,
    manual_override:boolean|null
  }]
}
```

- memberships 是全目录（含 inactive/include_all/无归属项），与有效 scenes code[] 均按 scene id 排序。name/outputs_heating 从共享场景配置按 ID 关联，不新增在本快照里。
- include_all：origin=implicit、manual_override=null、成员取 is_active，忽略历史行；implicit 不落 DB。普通场景有 manual 行：origin=manual、override=行值；否则 rule 行为 rule、override=null，无行为两个 null。所有普通场景成员受 active 门控；inactive 仍展示旧 manual_override。
- GET 用单条 PG 查询的一致读视图，只读已物化行，不预演新规则、不写库。PUT/DELETE 同事务按 catalog 共享锁→image 行锁，锁内核 scene 存在/active/include_all，保存修改前有效集合，再 upsert manual/仅删 manual、复用 planner 重算及条件 bump，最后锁内返回一致快照；数据库失败全回滚。
- manual 优先规则；同值 PUT 不重复行，无 manual DELETE 仍重算并 200。include_all 禁 PUT/DELETE；inactive 普通 scene 禁 PUT、可 DELETE 清旧覆盖。无目标 scene 不算幂等成功。
- 仅有效 scene 集合变化 bump payload_version；来源切换但集合相同不 bump，保留原有其它 payload 字段版本逻辑。无 CAS，现有锁范围内串行、最后提交生效；直接绕仓储写库不在锁保障内。
- UUID/path/请求校验、无图片、inactive PUT、include_all 写：VALIDATION_ERROR 422；无 scene：SCENE_NOT_FOUND 404；PG 快照/事务失败：INDEX_POSTGRES_FAILED 502；catalog 未就绪：NOT_READY 503；未预期异常：统一 INTERNAL_ERROR 500（不漏内部原文）。
- 写后推送（2026-11-10）：PG 事务提交后，检索栈可用时立即读这张图提交后的 payload（`category_id`/`scenes`/`food_state`/`label_status`）写进当前模型的 collection，并记账 `image_embeddings.synced_version`；不调用编码器/OSS，不启动后台任务。推送是尽力而为，**任何推送失败都不影响 200**（PG 已提交）：

  | `vector_sync` | `sync_required` | 含义 |
  |---|---|---|
  | `pushed` | `false` | 当前 collection 已是新 payload，检索立即可见 |
  | `missing` | `true` | 这张图在当前 collection 没有向量（需重新入库），检索查不到 |
  | `not_ready` | `true` | 检索栈未构建（NOT_READY），只保存了 PG；稍后 `POST /api/scenes/sync` |
  | `failed` | `true` | Qdrant / 记账失败，或推送期间图片一直被别人改（服务端日志有原因）|

  幂等写也会推送（单点 payload，代价很小）。`POST /api/scenes/sync` 仍是配置改动的传播入口，也能补上 `sync_required:true` 的图片；2026-11-11 起后台对账（§2.11）也会定时补推记账落后的图片（`missing` 的补不上，要重新生成向量）。当前部署未补角色鉴权，不宣称管理员授权已存在。
- 记账只记「仍是当前版本」的 payload（2026-11-10，`mark_payloads_pushed`，sync 与写后推送共用）：读 payload 之后图片又被改（`payload_version` 变了）时不记，并重新读、重新推，最多 3 轮；所以后写入的推送总会覆盖先读到的旧 payload，账本不会声称 collection 里有它没有的版本。图片已被删除时也不记（不再因外键失败整批 502）。场景开关（`is_active`/`include_all`）改变有效集合但不改版本号，这类改动仍以全库同步为准。

---

## 6.3 图片单图管理（2026-11-11 · 本地实现，待独立测试验收）

> 图库对单张图片的读、改、删和重推 payload。无新增表/迁移。PostgreSQL 部分走 catalog（只依赖 PG：检索栈未就绪时也能改、能删）；向量库部分在检索栈可用时跟上，失败不影响已提交的 PG 修改。实现：`backend/app/api/v1/images.py`、`backend/app/schemas/images.py`、`backend/app/services/retrieval/{manage_images,vector_status}.py`；共用字段规则为 `backend/app/core/rules/image_fields.py`。元数据编辑统一经仓储 `patch_image()`，直接调用该入口也会执行字段规范化及事务内状态校验。

| method + path | 请求 | 成功 |
|---|---|---|
| GET `/api/images/{image_id}` | 无 | 200 `{ok, image, vector}` |
| PATCH `/api/images/{image_id}` | 只传要改的字段（见下表），extra forbid、整数字段严格 | 200 `{ok, image, vector_sync, sync_required}` |
| POST `/api/images/{image_id}/vector-sync` | 无 | 200 `{ok, image_id, payload_version, vector_sync, sync_required}` |
| DELETE `/api/images/{image_id}` | 无 | 200 `{ok, image_id, deleted_at, cleanup:"done"\|"queued"}` |
| GET `/api/images/{image_id}/file?variant=thumb\|original`（2026-11-11） | 无（`variant` 默认 `thumb`） | 200 图片字节 |

`image_id` 为 UUID（非法 422）；图片不存在 `IMAGE_NOT_FOUND` 404。成功体不带 `trace_id`（在 `X-Trace-Id` 头）；错误沿 §6 统一信封。

`…/file`：从对象存储读回这张图的缩略图（没有缩略图时给原图）或原图，供 `thumb_url` / `original_url` 在浏览器里打不开时使用（mock 存储的占位域名、私有桶）；前端先用 URL，失败再用它，并记住失败的域名、之后直接用它。检索栈可用时用它的存储实例（mock 驱动内存里的字节也读得到），否则按同一份 `IMAGE_STORAGE_CONFIG` 另建一个（mock 驱动只能读 `mock_mirror_dir`）。已删除、文件还在等清理的图片也能读（查 `image_deletions`）。按文件头判断类型，只返回 JPEG / PNG / WebP / GIF / BMP，带 `Cache-Control: public, max-age=31536000, immutable`（id 是内容哈希，字节不会变）、`X-Content-Type-Options: nosniff` 和 sandbox 的 CSP；存储里读不到或不是这些图片类型 `IMAGE_FILE_UNAVAILABLE` 502，对象存储配置不可用 `NOT_READY` 503。

`image`：字段名与 List item（§6.1.2）一致，另加详情专有字段；食物字段和 `heating_group` 总带，不看场景。

```text
{
  id, sha256(hex), original_url, thumb_url, width, height,
  category_id|null, category|null（中文主名）, category_code|null, label_status, scenes:string[],
  description, tags:string[], source_type, license,
  food_state, food_processing, food_count, total_mass_g, water_mass_g, main_ingredient_mass_g,
  heating_group:{id, code, name}|null, payload_version, created_at, updated_at,
  embeddings:[{model_id, name, version, collection, model_status, synced_version, embedded_at}]
}
```

`vector`：按 PG 现值算出这张图应有的 payload（§4.2 四个字段，与入库 / 同步写的是同一份），和当前模型 collection 里这个点的 payload 比较（`scenes` 按集合比）。

| `status` | 含义 |
|---|---|
| `synced` | 有点，payload 与 PG 一致：检索过滤看到的就是库里的状态 |
| `stale` | 有点，payload 落后：`POST …/vector-sync` 修复（或 `POST /api/scenes/sync` 全量） |
| `missing` | 当前 collection 没有这张图的点：检索查不到，需要重新入库 |
| `unavailable` | 检索栈未就绪或读向量库失败；`detail` 给原因（不含内部地址），接口仍 200 |

其余字段：`collection`、`embedding_space_id`、`exists`、`payload`（点上现有）、`expected_payload`（应有）、`payload_matches`、`payload_version`、`synced_version`（当前模型的记账，没有为 null）、`embedded_at`。

PATCH 字段（不传 = 不变）：

| 字段 | `null` 表示 | 进 payload |
|---|---|---|
| `category_id`（正整数，必须存在且启用） | 清空，图片变成未标注 | 是；换类别时同事务重算这张图的规则场景 |
| `label_status`（`unlabeled`/`auto`/`verified`） | 不允许 | 是 |
| `description` | `""` | 否 |
| `tags`（整体替换，规范化同入库） | `[]` | 否 |
| `food_state` | 清空 | 是 |
| `food_processing`、`food_count`、`total_mass_g`、`water_mass_g`、`main_ingredient_mass_g` | 清空 | 否 |

- `label_status` 不传时跟着类别走：清空类别 → `unlabeled`；设置或更换类别 → `verified`（人在图库里选的）；类别不变 → 保持。传了必须满足 unlabeled ⟺ 无类别，否则 422。
- 单事务：catalog 共享锁 → 图片行锁（与其它图片写入同序），锁内校验类别；payload 字段**实际变化**才 bump 一次 `payload_version`（同值不 bump）；锁内读回提交后的 `image`。
- 提交后推送同 §6.2「写后推送」（`vector_sync` / `sync_required` 四个取值含义相同）；每次 PATCH 都推一次（单点，顺带修复漂移）。`POST …/vector-sync` 只做推送。
- DELETE：PG 单事务删 `images`（CASCADE `image_scenes` / `image_embeddings` / `image_foods`）并写 `image_deletions`，图库和检索结果立即看不到（两者都读 PG）；检索栈可用时同一请求删当前 collection 的点和原图、缩略图，再删队列行（`cleanup=done`），否则 `cleanup=queued`，由后台对账（§2.11）定时补删，也可用 `python -m app.workers.process_deletions` 或任务中心「待清理图片」的「立即清理」。已删且仍在队列中再删：返回同一队列行。同一图片（同字节即同 id）在清理前又被入库：再删会重新删除；清理时发现已重新入库则保留点和文件、只删队列行。仍只清当前 collection（旧模型版本的点不动，同 §2.5）。
- 错误：`VALIDATION_ERROR` 422（字段、类别不存在或停用、label 与类别矛盾）；`IMAGE_NOT_FOUND` 404；`SEARCH_POSTGRES_FAILED` / `INDEX_POSTGRES_FAILED` 502；catalog 未就绪 `NOT_READY` 503。未补角色鉴权（同 §6.2）。

---

## 6.4 入库结果历史记录（2026-11-11 America/Denver · 工作区已有接口，FE 待接线，未实测）

> 依据新 C 仓本轮实际 HEAD `54547ad960fed37c9c742fa96d2912953a28ca3d` 及未提交差异。`backend/app/{api/v1/tasks.py,schemas/tasks.py,repositories/tasks.py,services/retrieval/task_center.py}`、`models/task.py`、迁移 `20261111_0008`、FE `entities/task` 为当前工作区成果，**不等于已合并、已迁移或已验收**。本刀只补文档，无应用/DB/服务/测试操作。完整设计/实施交接为共享电脑 `/workspace/deliverables/ingest-tasks-contract.md`，本节压缩记录现有 wire 与最小范围。

### 6.4.1 已有查询与字段

- **GET `/api/index-tasks`**（已在 `api/router.py` 注册，无额外 `/v1`），无 body。query：`status` 可省略（全部），否则仅 `succeeded` / `failed`；`image_state` 可省略，否则仅 `present`（图片仍在图库的成功记录）/ `deleted`（图片之后被删除的成功记录），与 `status` 同时给时取交集（`status=failed&image_state=…` 为空）；`page=1`（1..1000000）、`page_size=50`（1..100）。已有 FE `listIndexTasks` 默认显式发 `page_size=20`，与 BE 缺省 50 区分；本轮页面沿 FE 每页 20。
- 只需 catalog/PG，不依赖编码器/Qdrant/OSS 就绪。HTTP 200：`{ok:true,status:"succeeded"|"failed"|null,image_state:"present"|"deleted"|null,page:int,page_size:int,total:int,counts:{succeeded:int,failed:int,image_deleted:int},items:IndexTaskItem[]}`，业务键必返，空 items 为 []。`total` 是当前筛选**记录行数**，counts 是不受筛选/分页影响的全历史行数（`image_deleted` 是图片已删除的成功记录数，**也计在 `succeeded` 里**），不是图片去重数/请求数/本页数。
- OFFSET/LIMIT，`created_at DESC, id DESC`（id 为同时间 tie-breaker）。counts/items 分开 SQL，无整页/跨页一致快照保证，并发新增或外部清记录可令页偏移；超出总页数不自动纠正。
- `IndexTaskItem`：`id:int`（bigint identity，记录 ID，非图片 UUID）、`filename:string`、`status:"succeeded"|"failed"`；以下键必返且可 null：`image_id:UUID string|null`、`new_image:bool|null`、`error_code:string|null`、`error_detail:string|null`、`duration_ms:int|null`、`trace_id:string|null`、`created_at:string|null`（schema 默认 null）、`image_state:"present"|"deleted"|null`（2026-11-12 新增：成功记录的图片现在在不在图库，按 LEFT JOIN 现算，不是列；失败记录为 null）、`image:IndexTaskImage|null`。其余 nullable 键无 schema 缺省值。
- `IndexTaskImage`：`id:string` 必填，`thumb_url:string|null=null`、`category_id:int|null=null`、`category:string|null=null`（中文主名）、`label_status:"unlabeled"|"auto"|"verified"|null=null`。LEFT JOIN 取得**当前图片投影**，不是入库时快照。成功图删除后历史仍 succeeded、image_id 保留、image=null；同 ID 重新入库后可能重新有投影。无缩略图不等于图已删。
- **无独立 `GET /api/index-tasks/{id}`**。本轮明细直接用列表 item 全字段；图库 `GET /api/images/{image_id}` 不是任务记录详情，本轮不依赖它、不新增详情 API。
- **清理记录（2026-11-12）**：`DELETE /api/index-tasks?status=&image_state=`：删掉同样筛选下列表列出的那些记录，`status` 与 `image_state` **至少给一个**（都不给 422 `VALIDATION_ERROR`，不会一次删光），如 `?image_state=deleted` 清理图片已删除的记录、`?status=failed` 清空失败记录；200 `{ok:true,status:…|null,image_state:…|null,deleted:int}`。`DELETE /api/index-tasks/{id}`（记录 id，1..2^63-1，否则 422）删一条：200 `{ok:true,id:int,deleted:0|1}`，记录本来就不在为 0（幂等，不报 404）。两者都**只删记录、不动图片**，只需 catalog/PG；错误同上（`INDEX_POSTGRES_FAILED` 502、`NOT_READY` 503）。
- 成功列表顶层无 trace_id，当前 GET trace 取 `X-Trace-Id`；item.trace_id 是原入库请求 trace，不能混用。失败沿 §6 信封：`VALIDATION_ERROR` 422、`NOT_READY` 503、`LIST_POSTGRES_FAILED` 502；schema/建连接失败还可 `INDEX_POSTGRES_FAILED` 502；未预期错误 `INTERNAL_ERROR` 500。

### 6.4.2 结果粒度、失败与保留边界

- `POST /api/index` / `/api/index/file` 正常进入执行后，**每文件完成即独立记最终结果**；多文件请求部分成功为不同 succeeded/failed 行，无 request-level partial/running/progress/job 状态。相同图片重复处理可多条成功，`new_image=false` 代表沿用已有图标注，失败为 null。
- `created_at` 为 PG 插入事务时间（timestamptz，正常非空），非请求/文件创建时间；`duration_ms` 是服务端单文件用例耗时（正常非负、可 0），不是上传/排队/后续 manual 场景 PUT 耗时。前端解析自带时区后以 America/Denver（MST）展示，null 显示未提供。
- 成功记录只表示 index 用例成功，FE 后续人工场景调整及 notes 不回写记录。failed 表示用例抛错，**不保证零写入或跨存储完全回滚**（后阶段失败可能已有 PG/OSS/Qdrant 副作用）；不能沿 enum 陈旧注释宣称“失败未写任何东西”。
- 解析/预读上传/整请求格式校验/NOT_READY 在执行前拒绝不产生逐文件记录；已执行失败被记录后，HTTP 适配器仍可能把全失败提升为非 2xx。因此非 200 不等于没历史；浏览器超时/取消不保证服务端停止。
- 历史失败用 item 的 `error_code/error_detail`（非 index 响应 failed[].error/detail），公开说明已净化；未知异常为 INTERNAL_ERROR。filename 仅文本、可能为源路径，不作图片 URL/文件恢复 token。
- 记录写入独立事务、best effort：失败只日志，不改变入库结果，无可靠补偿重试保证，可缺行。裁剪上限 filename 255、error_code 64、error_detail 1000、trace_id 128 字符；不伪造被截原文。
- image_id 无 FK，删图保留历史。**保留政策（2026-11-12）：默认保留、标记、按需清理**——删图不联动删记录（记录用来追溯哪天入过哪些文件、trace 查日志），图片删除后记录 `image_state=deleted`，前端标「图片已删除」、可单独筛选，由人用「清理图片已删除的记录」/「清空失败记录」/ 单条删除处理（均二次确认）。仍无 TTL/自动轮转，不承诺永久或固定天数；如需自动过期（如图片删除 N 天后自动清记录）另刀，需要记录删除时间（加列 + 迁移）。同字节图片删除后又重新入库：同一 image_id 重新有图，旧记录随之回到 `present`（LEFT JOIN 现算）。0008 文件存在不证明实际 DB 已迁移，schema 检查不自动迁移，本刀不执行迁移。

### 6.4.3 最小 FE 接线规范（待实施）

- 历史列表只做全部/成功（图片在库，2026-11-12 起不含图片已删除的）/图片已删除/失败、分页、手动刷新、已有明细；与本会话 jobs（pending/running/success/error/cancelled、File/进度/本地重传）明确分区、计数不混算。刷新/卸载丢本地队列不删除服务端历史；不假称 abort 撤销服务端。
- 复用 `entities/task/listIndexTasks`、DTO 与共享 client/error，映射 `page_size→pageSize`、`image_id→imageId`、`new_image→newImage`、`error_code/detail→errorCode/Detail`、`duration_ms→durationMs`、`trace_id→traceId`、`created_at→createdAt`、`image.thumb_url→image.imageUrl`、`category_id/label_status→categoryId/labelStatus`；其余原名。nullable bool/0 不得变成假默认；现有 mapper 宽松兜底/过滤不是严格协议保障，关键缺字段/整页被滤掉不能伪装正常空页。
- 首入/重新进入/分页筛选或 baseUrl 改变读取，切筛选/页大小回 1；手动刷新保留当前页/筛选。成功后发现页越界只有限一次回合法末页重读，total=0 回第一页；持续并发空页提示刷新，不无限循环。
- 首次加载、全空、筛选空、网络/HTTP/JSON/协议失败明确区分；刷新失败可留旧行但标旧数据，不能归零/回退成本地假历史。带取消信号及请求 token 防迟到覆盖；baseUrl 改变清旧历史/详情；无后台轮询、无页面 fetch、不继续加厚 ManagementViews。
- 明细只读 item；选中行不在刷新返回页时关闭提示重新选择，不推断记录已删除。失败/error/filename 纯文本转义，缺图/缺字段显示占位。容器交设计，不修改受保护 Dialog/.qa。
- ~~既有 `DELETE /api/index-tasks?status=...` 不纳本轮，不展示清空操作~~（2026-11-12 起已接入：「清理图片已删除的记录」`?image_state=deleted`、「清空失败记录」`?status=failed`、详情「删除这条记录」`DELETE /{id}`，均先确认，按钮在对应计数为 0 时不可用）；`GET /api/image-deletions`、`POST /api/image-deletions/process` 已于 2026-11-11 接入「入库任务」的「待清理图片」页签（见变更记录）。process 不是失败入库重试。无服务端 cancel/replay/retry/progress API，不造异步 job；失败可展示，重传需用户重新提供 File。
- 本范围**无需新增 BE API/schema**，列表已有明细；需 FE 接线及独立测试验证未提交实现/迁移部署。测试应覆盖真实分页/统计/trace/null/重复图/混合失败/best-effort 缺行、服务端和本地状态分离、刷新越界/错误/迟到请求。本刀不执行验收，不借旧 scene 测试结论。

---

## 7. 明确不作本刀范围（含 M0 OUT OF SCOPE）

| 项 | 归属 |
|---|---|
| MySQL→PG 数据迁移脚本 / 旧数导入 | **OUT OF SCOPE（M0）**；空库重建；附录 M 仅历史对照 |
| FlyB 空兜底工作流 / 接口编排 | **OUT OF SCOPE（M0）**；search 空即 empty |
| 真传 S3/OSS SDK 与桶策略 | 另刀 |
| FE 结果卡**像素级 UI** / 旧 §9 wire 复活 | 像素级仍另刀；§9 已作废；**字段级 wire 见 §6.1** |
| 业务代码、自测、git commit | **禁止**（本契约刀） |
| 抽油烟机 `range_hood` 规则明细 | 待配置 |

---

## 附录 M · 历史字段对照（**非迁移工单** · M0：不做脚本）

> 仅便于读旧 SSOT / 旧库时对照命名。**禁止**把本表当成 MySQL→PG 迁移范围；本轮空库重建，旧 MySQL 数据清零。

| MySQL / MVP | PG v0.1 | 备注 |
|---|---|---|
| `images.id` BIGINT | 新 `uuid` = hash(文件字节) | **不能**数值对翻 |
| `images.path` | S3 `original/{id}.ext` + 完整 https | 无本地 path 列 |
| `images.url` | `original_url` / `thumb_url` | |
| `images.title` | **无对等列** | 产品是否另表 **TBD** |
| `images.tags` JSON | `images.tags text[]`（迁移 `0007`） | 2026-11-09 起有列；空数组表示未标注 |
| `images.category` 字符串 | `categories` + `category_id` | |
| `images.scenes` JSON | `image_scenes` + 规则；`normal`→`general`，`device`→`oven` | |
| `images.status=deleted` | `image_deletions` 流水线 | |
| `qdrant_point_id` | = `images.id` uuid | 旧点废弃，重嵌 |
| `embedding_collection_meta` | → `embedding_models` 一行一 collection | |
| FlyB 空兜底编排 | **本轮不做** | A4 / M0 |

---

## 附录 Q · 检索语义沿用摘要（v0.1 未改部分）

| 项 | 值 | 来源 |
|---|---|---|
| `score_threshold` 缺省 | `0.55` | MVP §3.2.1 |
| 关拒识 | 显式 `0` | 同上 |
| 合法域 | `{0}∪[0.30,1]` | 同上 |
| 越界 | 422 `SEARCH_BAD_SCORE_THRESHOLD` | 同上 |
| 顺序 | 先 top_k 再阈值过滤 | 算法口径 |
| FlyB | **OUT OF SCOPE**；空结果不调外搜 | A4 / M0 |

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-11-11 | **可选 OSS 对象键前缀 `key_prefix` 本地实现（America/Denver）**：按共享规格落地 `IMAGE_STORAGE_CONFIG.key_prefix`；空则键与现网一致，非空规范化后前缀于相对键。启动期非法值 `ValueError`。S3 与 mock 共用 `storage_keys`。已落盘 URL/对象不迁移；读删/reembed 走存库 URL。不改 FE、不加签名 URL、不改业务 DB、不迁移。`config.yaml` 仅补注释式示例，未把本地调试配置改为生产 OSS。实现核对单测不是验收；独立测试按共享规格 §8。未 commit。 |
| 2026-11-11 | **可选 OSS 对象键前缀 `key_prefix`（America/Denver）**：§5 / A8 增补实施规范（待开发）：`IMAGE_STORAGE_CONFIG` 顶层可选字符串；空则键不变，非空规范化后前缀于 original/thumb/staging（如 `ImageEmbedding/original/{id}.jpg`）。`public_base_url` 仍为桶/CDN URL 根，不与 `key_prefix` 重复书写。已落盘 URL 与对象不迁移；读写删走存库 URL。不改 FE、不加签名 URL、不改运行配置。细字见共享规格，本刀只改文档、未测试、未 commit。 |
| 2026-11-11 | **入库结果历史最小契约 §6.4（America/Denver）**：按本轮 HEAD 54547ad 及未提交任务链核现有 GET /api/index-tasks 的 wire/分页/全历史 counts/列表内明细；明确逐文件同步终态、best-effort 缺行、当前图片投影及本地上传队列边界。FE 只读历史/刷新/分页/明细待接线，无新增 BE API，不纳清记录/删除清理/异步 job/重试。0008 及并行源码存在不证明已合并/迁移/验收。本刀只改文档、未测试、未 commit。 |
| 2026-11-04 | **M1 草案**：按《图搜图数据结构设计 v0.1》落 12 表字典、Qdrant/S3/API 差量、作废旧 §9/双轨；ASSUMPTION A1–A8 |
| 2026-11-04 | **M0 总裁已拍**：空库重建（无迁移脚本）；FlyB 本轮 OUT OF SCOPE；`scene_labels` 纳入冻结；ASSUMPTION A2–A4 改写 |
| 2026-11-05 | **§6.1 Wire/结果卡草案（FE 对齐刀）**：Search/List 字段表、external_candidates 最小 wire、FE snake→camel 映射、破坏性清单与 P1–P12 待拍板；更新 §6/§7 交叉引用；未 commit |
| 2026-11-05 | **§6.1 P1–P12 冻结**：全按建议已拍；**一刀切**；禁 `image_url`/`thumbnail_url` 别名与 FlyB 壳（site/page_url/snippets/details 等）；envelope 权威 `items`；禁 title/description/qdrant_point_id；`external_candidates` 仅定最小 wire（落地 TBD）；未 commit |
| 2026-11-08 | **§6.1 更正（总裁拍：按后端实现）**：`description` 由禁改为允许（`string`，默认 `""`；Index 请求/Index 响应/Search/List 均有；不进 Qdrant payload）；`heating_group` 由展示名 string 改为对象 `{id, code, name}`（仅 `outputs_heating=true` 时出现）。以 `backend/app/schemas/retrieval.py` 为准。未改其它债。未 commit |
| 2026-11-05 | **表结构以 ORM 为准**：`app/models/` 按域拆分（category/image/scene/embedding/external）+ `app/core/enums.py`；迁移 `0002` 补齐约束（images 宽高非空且 >0、unlabeled⇔无类别、sha256 32 字节；scene_rules 每场景类别/菜系唯一、label 必须属于同场景（组合外键）；image_scenes 只有人工行可排除；embedding_models dim/input_size >0；external_candidates 审核时间/入库图一致性）、ON DELETE 行为、默认值、索引与注释；`include_all` 场景不再物化到 `image_scenes`。Wire（§6.1）不变 |
| 2026-11-09 | **枚举改为配置文件**：取值、中文名统一放 `backend/config/param/enums.yaml`（`app/core/config/enum_store.py` 读写，文件改了自动重读）。`source_type`/`license`/`food_state` 为可编辑枚举，新增接口 `/api/enums`（§6）可追加取值；迁移 `0004` 去掉这三列的 CHECK，取值改由应用层校验。系统枚举（`label_status` 等）取值仍以 `app/core/enums.py` 为准、保留 CHECK。Wire（§6.1）不变：`food_state` 仍是 string，可能出现初始三值以外的取值 |
| 2026-11-09 | **食物属性扩充**：`images` 在 `food_state`（生/熟/冷冻）之外新增 `food_processing`（加工状态，可编辑枚举，初始 切片/切块/整个/切丝）、`food_weight_g`（重量，克，CHECK > 0）、`food_count`（个数，CHECK > 0），均可为空（迁移 `0005`）。`POST /api/index`（JSON 与 multipart）可传这四个字段（此前 `food_state` 不能经接口写入）；`IndexedImage` 总带，Search hit / List item 与 `food_state` 同规则只在 `outputs_heating=true` 时出现（§6.1 P9 不变，纯新增字段）。新三项不进 Qdrant payload，修改不 bump `payload_version`。规则见 `backend/app/services/retrieval/food_fields.py` |
| 2026-11-09 | **食物标注改为单独一张表**（取代上一条的 `images` 列方案）：新表 `image_foods`（§2.4，与 `image_embeddings` 同样做法，每图最多一行，ON DELETE CASCADE），六个字段均非必填：`food_state`、`food_processing`、`food_count`、`total_mass_g`（总质量）、`water_mass_g`（含水质量，可为 0）、`main_ingredient_mass_g`（主食材质量）；质量单位克，整数。迁移 `0006` 把 `images` 上的 `food_*` 列连数据搬过去（`food_weight_g` → `total_mass_g`）后删列。Wire：六个字段平铺在 Index 请求/响应、Search hit、List item（后两者仍仅 `outputs_heating=true`）；`food_weight_g` 由三个质量字段取代。Qdrant payload 不变（`food_state` 改从 `image_foods` 取） |
| 2026-11-09 | **图片标签入库**：此前 `POST /api/index` 收 `tags` 但直接丢弃。新增 `images.tags text[] NOT NULL DEFAULT '{}'` + GIN 索引 `ix_images_tags_gin`（迁移 `0007`）；Index 写入（规范化规则见 §2.4），Index 响应 / Search hit / List item 都带 `tags`（默认 `[]`）；List 的 `q` 也匹配标签子串，新增 `tag` 精确筛选。不进 Qdrant payload，Search 不按标签过滤。规则见 `backend/app/services/retrieval/text_fields.py` |
| 2026-11-10 | **类别树管理接口 + 前端「类别配置」页**：新增 `GET`/`POST /api/categories`、`PATCH`/`DELETE /api/categories/{id}`（§6 类别行），读写 `categories` + `category_names`，无表结构变化、无新迁移。写操作各自一个事务并先取事务级咨询锁；`path` 仍由数据库触发器维护。前端新增 `?view=categories` 页面（`frontend/src/{pages/categories,features/category-config,entities/category}`）：树形展示，新建根/子类别、编辑、换上级、删除。未做：加热组与场景规则管理；类别变动后的 `image_scenes` 批量重算与 Qdrant 重推；入库接口仍只按 `code` 解析类别且不看 `is_active`；上传页的类别下拉仍是前端写死的中文名 |
| 2026-11-10 | **场景配置与人工标注链路优化**（无表结构变化、无新迁移）：① 人工成员 PUT/DELETE 在 PG 提交后立即推送该图 payload，响应由固定 `sync_required:true` 改为 `vector_sync` + `sync_required` 布尔（§6.2），标注后不必再跑全库同步；② 配置写接口成功体新增 `sync_required`，只有影响已入库图片归属的改动才为 `true`（§6 场景行），前端据此决定是否提示「待同步」；③ 规则 `PATCH` 可原地换目标（单事务、id 不变），前端去掉「删旧建新」两步和中途失败的半完成状态；④ `mark_payloads_pushed` 只记仍为当前版本的 payload 并对被抢先改动的图重推（§6.2）。前端：场景管理按 `sync_required` 显示「已生效 / 待同步」、规则编辑改为「按类别 / 按菜系」单选；图片标注面板「纳入 / 排除 / 恢复规则」一键生效，不再弹确认框，也不再每次写入后重拉场景目录 |
| 2026-11-11 | **图片单图管理接口（后端）**（无表结构变化、无新迁移）：新增 `GET`/`PATCH`/`DELETE /api/images/{image_id}` 和 `POST /api/images/{image_id}/vector-sync`（§6.3），新错误码 `IMAGE_NOT_FOUND` 404。详情带这张图在当前 collection 里的向量状态（点上的 payload 与 PG 应有值比较）；PATCH 单事务改类别 / 标注状态 / 描述 / 标签 / 食物标注，提交后推 payload（与 §6.2 同一套 `vector_sync`）；DELETE 走 `image_deletions`，检索栈可用时同请求清理点和文件。删除队列两处修正：入队时图片已在队列但又被重新入库会重新删除；清理时图片已被重新入库则保留点和文件。前端未接入 |
| 2026-11-11 | **上传与导入页标注改造**（无表结构变化、无新迁移）：后端 `POST /api/index` 透传 `label_status` / `source_type` / `license`（此前用例支持但接口丢弃），`IndexedImage` 新增 `created`。前端：类别改为从 `GET /api/categories` 搜索选择（只列启用类别，按编码提交；此前是写死的中文名，后端按编码解析不到，一律存成未标注）；「场景意向」改为按所选类别和场景规则预览，和规则不同的勾选在入库后写成人工纳入 / 排除（只写与服务端结果不同的场景），不再发送 `scenes` 提示；新增标签、描述、食物标注六字段、来源与许可、标注状态控件；每张图片可「单独标注」（复制批量设置后独立修改）；`created=false` 的图片提示沿用原有标注、不写场景调整。实现：`frontend/src/features/{index-upload,category-picker}` |
| 2026-11-11 | **图片数据页接入类别树与单图编辑**（无表结构变化、无新迁移）：后端 `GET /api/images` 新增 `category_id`（子树）与 `label_status` 筛选（§6 List 行）。前端：类别筛选改为从 `GET /api/categories` 搜索选择（含下级类别；此前是写死的中文名按名称模糊匹配），新增标注状态筛选；卡片显示描述、类别、标注状态、场景和标签，没有缩略图的图片也能打开；「图片标注」弹窗改用 §6.3 接口：显示全部字段和向量库状态（待同步时列出与数据库不同的字段，可重推 payload），可改类别 / 标注状态 / 标签 / 描述 / 六个食物字段（只提交改过的字段，保存后按 `vector_sync` 提示检索是否已生效），可删除（二次确认），场景成员面板保留；有未保存的修改时弹窗不能关闭，保存或撤销后可关。实现：`frontend/src/features/{image-edit,annotation-fields,category-picker}` |
| 2026-11-11 | **前端「食材管理」与「类别配置」合并**（纯前端，无接口、表结构变化）：导航只保留「食材管理」，它就是类别树（`?view=categories`，深链 `&category=<id>` 不变）；原「食材管理」本地草稿页删除——它的名称 / 类别 / 属性就是类别树节点的名称、上级和属性。页面改成与图片数据页一致的列表：搜索名称 / 别名 / 编码（匹配项连同上级一起显示）、按类别域和启停筛选；按层级缩进的表格列出其他名称、菜系、加热组（沿用上级的标「沿用」）、图片与场景规则引用数和状态，行内新建下级 / 编辑 / 删除。新建类别在弹窗里选上级，编码前缀随上级变化；删除仍被引用的类别时说明引用并可直接改为停用（`PATCH is_active=false`）。前后端核对：前端客户端、解析、校验和错误映射对真实接口逐项一致（字段集合、`X-Trace-Id`、`CATEGORY_*` 错误码、字段级 422 的 `loc` 映射，以及编码 / 名称 / 语言代码 / 菜系 / 排序的边界值两边取舍相同）。仍未做：`POST /api/index` 按编码解析类别时不检查 `is_active`（`PATCH /api/images/{id}` 会拒绝停用类别），编码不存在时只记日志、图片存为未标注 |
| 2026-11-11 | **向量库与数据库状态的展示和修改**（无表结构变化、无新迁移）：① `GET /api/images` 改由 catalog 提供（只读 PostgreSQL），编码器 / Qdrant / 对象存储未就绪时图库照常列出（此前整个接口 503）；每张图新增 `vector_status`（`synced` / `stale` / `missing` / `unavailable`，与详情 `vector.status` 同口径；一页只读一次向量库，读不到时为 `unavailable`，仍返回 200）。② `PATCH /api/images/{image_id}` 与 `POST /api/images/{image_id}/vector-sync` 的成功体新增 `vector`：推送之后重读这张图得到的向量状态（同 GET 详情的 `vector`），页面不必再读一次详情；旧字段不变。前端：图库卡片和列表行标出「待同步」「没有向量」，页脚统计本页数量，向量库读不到时状态栏提示；「图片标注」的向量库卡片用类别路径、场景名、枚举中文名列出向量库与数据库不一致的字段，「按数据库同步到向量库」一键修复并直接用响应里的状态刷新，「没有向量」给出去上传的入口（同一张图片重新上传会补上向量、沿用原标注），collection、版本和写入记录收进「技术信息」；场景管理「全量同步」改为面向使用者的说明、状态和进度（行为不变） |
| 2026-11-11 | **向量修复与筛选**（无表结构变化、无新迁移）：① 新增 `POST /api/images/{image_id}/reembed`：从对象存储读回这张图的原图，用当前模型重新算向量，按 PostgreSQL 现在的数据写 payload 并记账 `image_embeddings`，修复 `vector.status=missing` 而不用重新上传（标注不变）；成功体 `{ ok, image_id, payload_version, vector }`。新错误码 `IMAGE_FILE_UNAVAILABLE` 502（原图读不到：对象已不在、存储失败，或 mock 存储没有配置 `mock_mirror_dir`，重启后不保留字节）；编码器 / 向量库 / 对象存储未就绪 `NOT_READY` 503。为此 `ImageStoragePort` 新增 `read_by_url_or_key`（boto3 `get_object`；mock 先读内存再读 mirror 目录）。② `GET /api/images` 新增 `out_of_sync=true`：只列当前模型写入记录缺失或 `synced_version` 落后于 `payload_version` 的图片（待同步或没有向量）；按记账判断、只读 PostgreSQL，绕过记账删掉的点要靠列表项的 `vector_status` 或全量同步发现。前端：「图片标注」的「没有向量」给出「用存储里的原图重新生成向量」（原图读不到时说明原因、保留重新上传入口）；图片数据新增「待同步或没有向量」筛选；场景管理「全量同步」结束后可对没有向量的图片批量重新生成（逐张请求，可停止，已删除的图片自动略过，失败的列出原因）；场景管理在 800px 以下改为一次只显示列表或详情（此前两栏叠在一起，列表透到详情下面） |
| 2026-11-11 | **PostgreSQL 与 Qdrant 同步补齐 + 缩略图读回**（无表结构变化、无新迁移）：① 移动类别或改菜系时，同一事务里重算其子树图片的 `image_scenes`（有效场景变化的 `payload_version` +1），提交后推 payload；`PATCH /api/categories/{id}` 成功体新增 `scene_sync`（§6 类别行）。此前要靠手动全量同步，忘了就一直不一致、且记账也看不出来。② 后台对账（§2.11 同步规则下）：API 进程按 `RECONCILE_CONFIG` 定时补推记账落后的 payload、清理删除队列，多进程用 PG 咨询锁互斥；另有 `python -m app.workers.reconcile`。③ 前端「入库任务」新增「待清理图片」页签：列出 `GET /api/image-deletions`，「立即清理」连续调 `POST /api/image-deletions/process`（有进展才继续，失败的写明原因）。④ 新增 `GET /api/images/{image_id}/file`（§6.3）：mock 存储（`use_boto3: false`）的 URL 是占位域名，浏览器打不开，所以缩略图一直不显示；前端图库、编辑、批量、任务、检索结果的图片改为先用 URL、失败用这个接口读回，并记住失败的域名。根治仍需配置真实对象存储（`use_boto3: true`、`endpoint_url`、`public_base_url`）；已入库图片的 URL 不会因此改变（同字节重传沿用第一次的 URL），要换成真实地址需另做迁移 |
| 2026-11-12 | **前端图片加载顺序**（纯前端，无接口变化）：搜索结果的网格和列表只加载缩略图（自建库 `thumb_url`，即 512px WebP；现网结果按 large → medium → small，缩略图都失败才用原图），此前先加载原图、失败才退到缩略图，每页 12 张都是原图。点开详情才加载 `original_url`：先显示已缓存的缩略图，原图加载完原位替换，加载失败保留缩略图并提示；详情预览区加高以显示原图清晰度。图片数据的「图片标注」同样处理。下载仍是原图优先 |
| 2026-11-12 | **自建检索与 FlyB 结果卡统一**（无表结构变化、无新迁移；**破坏性 wire 变化**，BE 与 FE 同发）：`POST /api/search` 的 `items[]` 与 `POST /api/predict/searchImage_*` 的 `result[]` 改为同一张结果卡（`backend/app/schemas/search_hit.py`）：顶层只有 `tags` / `description` / `original_url` / `similarity`，其余放进 `details`。自建：`tags` 由 `string[]` 改为「、」连接的字符串；`id`、类别、场景、`scene_label`、标注状态、食物字段、`heating_group` 进 `details`（`exclude_unset` 对嵌套同样生效），原图 / 缩略图 / 宽高进 `details.images[0]`（键名与 FlyB 相同）。FlyB：`title` → `tags`，`details.text_snippets` 并入 `description`，`image_url` → `original_url`（总是 URL 字符串，没有原图时取最大缩略图），`similiar` → `similarity`（相似度过滤写回同一个键，过滤时下载比对的那张写回 `original_url`）；predict 响应改为带类型的 `PredictResponse`（顶层多余的键不再输出）。Index / List / 单图的 `tags` 仍是 `string[]`。FE 两个通道共用一套卡片字段读法，详情新增「标签」，「描述」两个通道都显示。另按日志里的真实 FlyB 返回修正两处读法：缩略图尺寸名实际是 `Large/Medium/Small/Tiny`，BE `iter_image_urls` 与 FE 排序此前只认小写，落到「其他尺寸」按字典顺序先取了 112px 的 Tiny（网格显示最小图、原图下载失败时也用它算相似度），改为不区分大小写；站点名读 `HostInfo.Hostname`（此前退回成网页域名）。见 §6.1.1 / §6.1.2 / §6.1.4 / §6.1.5 |
| 2026-11-12 | **入库记录与删图的平衡**（无表结构变化、无新迁移）：删图仍不删记录（追溯用），但记录不再显示为普通「成功」。BE：`GET /api/index-tasks` 每条加 `image_state`（`present`/`deleted`，LEFT JOIN 现算），`counts` 加 `image_deleted`，新增 `image_state` 筛选；`DELETE /api/index-tasks` 改为 `status` / `image_state` 至少一个（删的正是同筛选下列出的记录）；新增 `DELETE /api/index-tasks/{id}` 删单条（幂等）。均只删记录。FE「服务端入库记录」：指标改为 成功（图片在库）/ 图片已删除 / 失败，筛选 全部 / 成功 / 图片已删除 / 失败（带数量），行内「图片已删除」灰色标记；「清理图片已删除的记录」「清空失败记录」和详情「删除这条记录」都先确认；图库单张、批量删除的确认文案提示记录会保留、可到入库任务清理。自动过期未做（需记录删除时间），见 §6.4.2 |

---

*文档结束 · §6.1 Wire **已拍 2026-11-05 · 一刀切**（P1–P12）；**2026-11-08 更正** `description` 允许、`heating_group` 对象；**2026-11-12 更正** 自建 hit 与 FlyB 结果同为一张结果卡；实现刀派后端/前端适配*
