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
> **2026-11-05 起表结构权威 = `backend/app/models/`（ORM）**，迁移 `0001` + `0002` 把库变成该结构（`alembic check` 可核对）。枚举列存 `varchar(32)` + CHECK，取值见 `backend/app/core/enums.py`；约束/索引命名 `pk_/fk_/uq_/ck_/ix_`。本节表格与 ORM 冲突时以 ORM 为准。

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
| `source_type` | `text` | NO | CHECK ∈ 见下 | `crawled` / `device` / `open_dataset` / `user_upload` / `external` |
| `license` | `text` | NO | CHECK ∈ 见下 | `commercial` / `research_only` / `unknown` |
| `category_id` | `integer` | YES | FK → `categories.id` ON DELETE RESTRICT | 可标到任意层级；未标注为空 |
| `food_state` | `text` | YES | CHECK ∈ `raw`/`cooked`/`frozen` 或 NULL | 生/熟/冷冻 |
| `label_status` | `text` | NO | CHECK ∈ `unlabeled`/`auto`/`verified` | **未标注 ⇔ 没有类别**（库约束 `ck_images_label_matches_category`：`(label_status = 'unlabeled') = (category_id IS NULL)`）；默认 `unlabeled` |
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
| `food_state` | keyword / string（可 null） | `images.food_state` |
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

| 对象键 | 内容 | 落库字段 |
|---|---|---|
| `original/{image_id}.{ext}` | 原图 | `images.original_url` |
| `thumb/{image_id}.webp` | 缩略图，长边 512 | `images.thumb_url` |
| `staging/{candidate_id}.{ext}` | 外部候选暂存 | `external_candidates.staged_url` |

**ASSUMPTION A8**：bucket/CDN 前缀由部署配置拼进完整 URL；契约不绑死域名。  
真传 OSS/S3 SDK、凭证与生命周期 **另刀**（本契约只定键与字段）。

---

## 6. API 差量提纲（资源边界 · 细路径多标 TBD）

> 前缀仍建议 `/api/...`。下列为相对 MVP 的资源边界；细 path/body **TBD**，实现前须回写本契约一小节。

| 资源 | MVP | PG v0.1 差量 |
|---|---|---|
| **Index** `POST /api/index` | 本地 path 入库、scenes JSON、BIGINT id | 收字节或可拉 URL → 算 sha256/uuid → 写 S3 → 写 `images` → embed → upsert **current** collection；场景经规则/`image_scenes`；**无 path** |
| **Search** `POST /api/search` | `scene` 默认 `normal`；拒识；仅 normal 空→FlyB | `scene` 改为默认 **`general`**（A5）；按 payload `scenes` 过滤；场景标签投票；`outputs_heating` 附加字段；**`score_threshold` 沿用 MVP（A6）**；自建空 → **`source=empty`**；**FlyB 编排 OUT OF SCOPE（A4 / M0）** |
| **List** `GET /api/images` | 分页、scene、title/category、仅 active | 无 soft-status：未删即在库；scene 成员过滤；返回 `original_url`/`thumb_url`、类别树字段、场景 codes；category 改 ID/code |
| **类别** | 无独立 API | CRUD/树查询 `categories` + `category_names`；加热组只读或管理 **TBD** |
| **场景** | 写死登记 | `scenes` / `scene_rules` / `scene_labels` 管理；图片成员人工覆盖 **TBD** |
| **审核候选** | 无 | `external_candidates` list/accept/reject；accept → 入库流水线 |
| **模型** | 配置 + meta 表 | `embedding_models` 状态机；切 `current`；重建进度 **TBD** |
| **删除** | 软删 status | 删图 API → `image_deletions` 流水线 |
| **Health** | 聚合 Qdrant+MySQL | 改探 PG + Qdrant(current collection) +（可选）S3；回报 current model/collection |

**结果卡形状**：旧 SSOT §9 统一 wire **仍作废**（见 §0.1）。前后端统一 wire 见 **§6.1（已拍 2026-11-05 · 一刀切）**；实现须按已拍定案改 BE hit/list 与 FE 适配层。

**错误码**：MVP 已定码能复用的复用（如 `SEARCH_BAD_SCORE_THRESHOLD`）；uuid/sha 冲突、未知 scene code、无 current 模型等 **TBD 补表**。

**错误响应（2026-11-06 起全部接口统一）**：`{"ok": false, "error": "<CODE>", "detail": "<说明>", "trace_id": "<id>"}`，请求参数校验失败另带 `errors`（字段级列表）；响应头 `X-Trace-Id` 与 `trace_id` 相同（客户端可用请求头 `X-Trace-Id` 指定）。通用码：`VALIDATION_ERROR` 422、`NOT_READY` 503、`EMPTY_FILE` 400、`FILE_TOO_LARGE` 413、`UNSUPPORTED_MEDIA_TYPE` 415、`INVALID_URL` 422、`FORBIDDEN_HOST` 403、`DOWNLOAD_FAILED` 502（或上游状态码）、`DOWNLOAD_TIMEOUT` 504、`UPSTREAM_ERROR` 502、`UPSTREAM_TIMEOUT` 504、`INTERNAL_ERROR` 500（不回传异常原文）；检索专用码见 `backend/app/services/retrieval/errors.py`，定义见 `backend/app/core/errors.py`。

---

---

## 6.1 前后端 Wire / 结果卡（**已拍 · 2026-11-05 · 一刀切**）

> **状态**：**已拍 · 2026-11-05 · 一刀切**（P1–P12 全按建议）。  
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
| **P1** | Search/List 权威 URL 字段名 | **已拍**：`original_url` / `thumb_url` 为唯一权威（完整 https） | 与 §2.4 / §5 一致 · 拍板日期 2026-11-05 |
| **P2** | 是否一期保留 `image_url` / `thumbnail_url` 别名 | **已拍**：**不保留**（一刀切）；无兼容窗 | 现状 BE search 只吐别名、list 双吐 → 实现刀须同发删 |
| **P3** | Search envelope 数组键名 | **已拍**：权威键 = `items`（始终 array）；**禁**把对象塞进 `result` | 现状 BE 用 `result`；FE 须只读 `items` |
| **P4** | 空结果形态 | **已拍**：`source=empty` + `items: []`；可选顶层 `message`；**禁**嵌套空壳 | 对齐 A4；FE 可简化 |
| **P5** | `title` 是否出现在 wire | **已拍**：**禁**（`images` 无 title 列）；展示名用 `category` / `scene_label` | 现状 BE 用 category_name 冒充 title |
| **P6** | `description` | **已拍**：结果卡 **禁**（仅 string 也不放） | 表侧 description 属 `scenes`/`heating_groups`，非结果卡 |
| **P7** | `site` / `site_name` / `page_url` / `snippets` / `text_snippets` / `details.*` | **已拍**：**全禁**（FlyB 壳） | 本轮无外搜 |
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

#### Hit 顶层字段表（`items[]` 元素）

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | 是 | `string`（uuid） | = `images.id` = Qdrant point id |
| `original_url` | 是 | `string`（https） | 原图；**权威** |
| `thumb_url` | 是 | `string`（https） | 缩略图；**权威** |
| `similarity` | 是（`source=self`） | `number` | 相似度；合法有限数；**禁止**拼写 `similiar` |
| `category_id` | 否 | `integer \| null` | |
| `category` | 否 | `string \| null` | 通用类别**展示名**（由 `category_names` 解析；非 code） |
| `scenes` | 否 | `string[]` | 有效场景 code 列表 |
| `scene_label` | 否 | `string \| null` | 当前检索 scene 下的场景类别展示名（有 `scene_labels` 时） |
| `label_status` | 否 | `string` | `unlabeled`/`auto`/`verified` |
| `width` | 否 | `integer \| null` | |
| `height` | 否 | `integer \| null` | |
| `food_state` | 条件 | `"raw"\|"cooked"\|"frozen"\|null` | **仅当**该 scene `outputs_heating=true` 时可出现 |
| `heating_group` | 条件 | `string \| null` | 加热组**展示名或 code**（见 W2）；仅加热场景 |

**禁字段（Search hit）**——出现即视为违约（实现刀须删）：

| 禁字段 | 原因 |
|---|---|
| `image_url` / `thumbnail_url` | 非权威 URL；**P2 已拍禁**（一刀切，无别名窗） |
| `text_snippets` / `snippets` | FlyB 旧壳 |
| `details` / `details.*` | FlyB 嵌套壳 |
| `page_url` | 外链页；本轮无 |
| `site` / `site_name` | 站点壳；本轮无 |
| `similiar` | 错误拼写 |
| `description` | 结果卡不承载；见 **P6** |
| `title` | 表无列；**P5 已拍禁** |
| `source`（hit 级） | 渠道只在 envelope；禁止 hit 自报 flyb/self |

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

| 字段 | Search | List | 差量说明 |
|---|---|---|---|
| `id` | ✓ | ✓ | 同 |
| `original_url` / `thumb_url` | ✓ 权威 | ✓ 权威 | 同；https-only |
| `similarity` | ✓ | **禁** | List 非检索打分 |
| `category_id` / `category` | ✓ | ✓ | 同 |
| `scenes` | ✓ | ✓ | 同 |
| `scene_label` | ✓（按检索 scene） | **通常不出现** | List 无「当前投票 scene 标签」语义；若产品要展示另刀 |
| `label_status` | 可选 | 可选 | 同 |
| `width` / `height` | 可选 | 可选 | 同 |
| `food_state` / `heating_group` | 加热 scene 条件出现 | 同（`outputs_heating`） | 同规则 |
| `created_at` / `updated_at` | **禁**（非检索所需） | 可选 `string`（ISO-8601 / timestamptz 序列化） | List 可展示入库时间 |
| `image_url` / `thumbnail_url` | **禁**（P2 已拍） | **禁**（P2 已拍） | 双边同时删除（一刀切） |
| `qdrant_point_id` | 禁 | **禁**（P11 已拍） | 与 `id` 重复 |
| `title` / `tags` | 见 P5；tags 表无列 | 同 | 附录 M：title/tags 无对等列 |

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
3. Library 与 Search 对 URL 的映射必须一致：展示缩略图 ← `thumb_url`；打开原图 ← `original_url`。

#### Search 一一对应

| Wire（snake） | UI（camel） | 备注 |
|---|---|---|
| `items[]` | `SearchResponse.items` | envelope |
| `source` | `SearchResponse.source` | 仅 `self`/`empty`；缺省不发明 |
| `scene` | `SearchResponse.scene` | |
| `outputs_heating` | `SearchResponse.outputsHeating` | |
| `trace_id` | `SearchResponse.traceId` | |
| `message` | `SearchResponse.message` | |
| `id` | `SearchImage.id` | |
| `original_url` | → 适配为 `imageUrl`（原图） | **勿**再读 `image_url`（P2 已拍禁） |
| `thumb_url` | → 适配为 `thumbnailUrl` | **勿**再读 `thumbnail_url`/`details.images` |
| `similarity` | `similarity` | 禁读 `similiar` |
| `category` | `category` | |
| `category_id` | （若 UI 需要）`categoryId` | 现状类型可补 |
| `scenes` | （若 UI 需要）`scenes` | |
| `scene_label` | `sceneLabel` | string |
| `heating_group` | `heatingGroup` | |
| `food_state` | `foodState` | |
| `width` / `height` | `width` / `height` | |
| — | `pageUrl` / `siteName` / `snippets` | **恒为 null / []**；适配层可删字段（产品刀） |

#### List 一一对应

| Wire（snake） | UI（camel） | 备注 |
|---|---|---|
| `items[]` | `ListImagesResponse.items` | |
| `page` / `page_size` / `total` | `page` / `pageSize` / `total` | |
| `thumb_url` | → `LibraryImageItem.imageUrl`（展示） | 与现 `pickImageDisplayUrl` 目标一致，但**最终只认** `thumb_url` |
| `original_url` | `originalUrl` | |
| `created_at` | `createdAt` | |
| `food_state` | `foodState` | |
| `scenes` | `scenes` / `sceneMemberships` | 若未来提供 `scene_memberships` 再扩展；本草案不强制 |

### 6.1.5 相对现状的删除 / 改名清单（破坏性）

| 对象 | 现状（本机核实） | 定案目标（已拍） | 动作 |
|---|---|---|---|
| Search hit URL | 只吐 `image_url`/`thumbnail_url` | `original_url`/`thumb_url` | **改名 + 删旧键**（P2 一刀切） |
| Search hit 壳 | `text_snippets`、`details.*`、`page_url`、`site_name` | 无 | **删除**（P7） |
| Search hit `title` | 用 category_name 冒充 | 无 title（P5 已拍禁） | **删除**（展示走 category/scene_label） |
| Search envelope 键 | `result`（空时可为对象） | `items` 恒 array（P3/P4 已拍） | **改名 + 形态修复**；禁 `result` |
| Search `source` | `self`/`empty` | 同；禁 flyb | **保持**；文档冻结 |
| List URL | 双吐权威+别名 | 仅权威（P2 已拍） | **删除别名** |
| List `qdrant_point_id` | 有 | 无（P11 已拍禁） | **删除** |
| FE `normalizeItem` | 读 `details`/`text_snippets`/`similiar`、多 URL 回落 | 单源映射表 | **删除回落枝**（实现刀） |
| FE `pickSearchImageUrls` | 仍回落 `thumbnail_url`/`image_url` | 只读 thumb/original | **收紧** |
| 旧 §9 结果卡 | 已作废 | 不复活 | **保持作废** |

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
| W2 | `heating_group` 一期先用**展示名 string**；若需稳定 code 另加 `heating_group_code` 字段刀 | 避免与表 `heating_groups.code`  silently 混用；与 P9 不冲突 |
| W3 | `scene_label` 类型 **已由 P8 拍为** `string \| null`；若要 `{id,name,code}` 对象须**另开拍板**，不得默默改类型 | 对象形态仍属 ASSUMPTION / 未拍；string 本身已是已拍 |
| W4 | `source=empty` 时 **HTTP 仍 200** + `ok=true`（与现 BE 一致）；真正校验错误仍走既有 4xx 码 | 与 P4 不冲突 |
| W5 | 本轮 Search `source` 枚举**不包含** `flyb`；FE 类型里遗留 `flyb` 仅历史兼容，不得新写 | A4 / M0；与已拍不冲突 |

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
| `images.title` / `tags` | **无对等列** | 产品是否另表 **TBD** |
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
| 2026-11-04 | **M1 草案**：按《图搜图数据结构设计 v0.1》落 12 表字典、Qdrant/S3/API 差量、作废旧 §9/双轨；ASSUMPTION A1–A8 |
| 2026-11-04 | **M0 总裁已拍**：空库重建（无迁移脚本）；FlyB 本轮 OUT OF SCOPE；`scene_labels` 纳入冻结；ASSUMPTION A2–A4 改写 |
| 2026-11-05 | **§6.1 Wire/结果卡草案（FE 对齐刀）**：Search/List 字段表、external_candidates 最小 wire、FE snake→camel 映射、破坏性清单与 P1–P12 待拍板；更新 §6/§7 交叉引用；未 commit |
| 2026-11-05 | **§6.1 P1–P12 冻结**：全按建议已拍；**一刀切**；禁 `image_url`/`thumbnail_url` 别名与 FlyB 壳（site/page_url/snippets/details 等）；envelope 权威 `items`；禁 title/description/qdrant_point_id；`external_candidates` 仅定最小 wire（落地 TBD）；未 commit |
| 2026-11-05 | **表结构以 ORM 为准**：`app/models/` 按域拆分（category/image/scene/embedding/external）+ `app/core/enums.py`；迁移 `0002` 补齐约束（images 宽高非空且 >0、unlabeled⇔无类别、sha256 32 字节；scene_rules 每场景类别/菜系唯一、label 必须属于同场景（组合外键）；image_scenes 只有人工行可排除；embedding_models dim/input_size >0；external_candidates 审核时间/入库图一致性）、ON DELETE 行为、默认值、索引与注释；`include_all` 场景不再物化到 `image_scenes`。Wire（§6.1）不变 |

---

*文档结束 · §6.1 Wire **已拍 2026-11-05 · 一刀切**（P1–P12）；实现刀派后端/前端适配*
