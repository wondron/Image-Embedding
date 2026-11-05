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
| `width` | `integer` | YES | — | 原图像素宽 |
| `height` | `integer` | YES | — | 原图像素高 |
| `source_type` | `text` | NO | CHECK ∈ 见下 | `crawled` / `device` / `open_dataset` / `user_upload` / `external` |
| `license` | `text` | NO | CHECK ∈ 见下 | `commercial` / `research_only` / `unknown` |
| `category_id` | `integer` | YES | FK → `categories.id` | 可标到任意层级；未标注为空 |
| `food_state` | `text` | YES | CHECK ∈ `raw`/`cooked`/`frozen` 或 NULL | 生/熟/冷冻 |
| `label_status` | `text` | NO | CHECK ∈ `unlabeled`/`auto`/`verified` | **未标注时一定没有类别**（应用约束：`label_status='unlabeled' ⇒ category_id IS NULL`） |
| `payload_version` | `bigint` | NO | 默认 `1` | 类别/状态/所属场景变化时递增；与 Qdrant payload 同步 |
| `created_at` | `timestamptz` | NO | 默认 `now()` | |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

**明确取消**：本地 `path`、软删 `status` 枚举挂本表（删除走 `image_deletions` + 物理删行，见 §2.5）。

**索引**：`UNIQUE (sha256)`；`INDEX (category_id)`；`INDEX (label_status)`；`INDEX (source_type)`；`INDEX (created_at DESC)`。

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
| `scene_id` | `integer` | NO | FK → `scenes.id` | |
| `origin` | `text` | NO | CHECK ∈ `rule`/`manual` | |
| `is_member` | `boolean` | NO | — | 人工排除时为 `false` |
| `updated_at` | `timestamptz` | NO | 默认 `now()` | |

**PK**：`(image_id, scene_id)`。  
**语义**：有效成员 = 规则推导结果，再被本表 `origin='manual'` 行覆盖（含排除）。物化策略（写时展开 vs 读时算）**TBD · 实现刀**；Qdrant payload `scenes` 必须与「有效成员」一致，并在变更时 bump `images.payload_version`。

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

**结果卡形状**：本刀不复活 §9 统一 wire 草案；search/list 最小字段以能展示 url + 类别/场景为准，**细字段表 TBD（FE 对齐刀）**。

**错误码**：MVP 已定码能复用的复用（如 `SEARCH_BAD_SCORE_THRESHOLD`）；uuid/sha 冲突、未知 scene code、无 current 模型等 **TBD 补表**。

---

## 7. 明确不作本刀范围（含 M0 OUT OF SCOPE）

| 项 | 归属 |
|---|---|
| MySQL→PG 数据迁移脚本 / 旧数导入 | **OUT OF SCOPE（M0）**；空库重建；附录 M 仅历史对照 |
| FlyB 空兜底工作流 / 接口编排 | **OUT OF SCOPE（M0）**；search 空即 empty |
| 真传 S3/OSS SDK 与桶策略 | 另刀 |
| FE 结果卡像素级对齐 / 旧 §9 wire | 另刀；§9 已作废 |
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

---

*文档结束 · 待 PM/总裁审后派后端/算法*
