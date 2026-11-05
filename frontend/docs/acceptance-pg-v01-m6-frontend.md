# 图搜图 · PG v0.1 M6 · 前端验收标准与改造范围

> 角色：**规范归前端设计**；**实现归前端架构**；**验收归「测试」bot**；**PM 归 boboji**  
> 范围：管理 / 标注 / 场景适配——对齐 PG 元数据与场景模型，改造图库 list、搜图、入库编辑相关 FE 面；**不重做整站**；**本文档不写业务代码**。  
> 契约对齐：`docs/image-search-pg-v01-data-contract.md`（M1 草案 · M0 总裁已拍冻结 ASSUMPTION）。下文若与契约冲突以契约为准；细 wire 标「待契约」处实现前须回写契约或与后端对表。  
> 仓：`D:\01-code\Image-Embedding` · 后端/算法 M2–M5 本地 commit `faa1efc`（未 push）  
> 文档版本：v0.2 · 2026-11-04（America/Denver）  
> 相关（MVP，部分被本刀取代）：`acceptance-index-search-mvp.md` · `acceptance-library-images-list.md` · `acceptance-score-threshold-control.md`

---

## 1. 背景与范围

### 1.1 背景

现有前端（React + TypeScript + Vite）已落地 MVP 壳：

| 面 | 现状（相对 `frontend/src/`） | PG v0.1 压力点 |
| --- | --- | --- |
| 搜图 | `App.tsx`：自建 `POST /api/search` + 现网 predict；拒识控件；结果卡 `SourceBadge`（self / FlyB） | 默认 scene、`outputs_heating` / `scene_label`、**无 FlyB**、空库形态 |
| 图库 | `ManagementViews` · `library` + `lib/images-api.ts`：`GET /api/images`，**写死 `scene=normal`**，认 `image_url` | scene→`general`；认 S3 `thumb_url`/`original_url`；无 path |
| 入库 | `import` / `tasks` + `lib/index-api.ts`：multipart 入库；响应仍映射 `path`/`url` | 无 path；S3 URL；场景归属（规则/manual）编辑位 |
| 标注/食材 | `ingredients` 本地草稿 | 类别树 / 加热组 / 场景自有标签——本刀标清改造边界 |
| 拒识 | 已按 MVP §3.2.1；契约附录 Q **沿用** | 本刀不改数字；与 scene 正交 |

权威设计：契约 §0 A1–A8、§0.1 作废双轨 path、§2 十二表、§3 差量、§4 Qdrant、§5 S3、§6 API 差量、§7 OUT OF SCOPE。

### 1.2 In Scope（本轮 · M6 FE 规范）

- [ ] **契约对齐表**（scene 迁移、无 path→S3 URL、无 FlyB、空库）
- [ ] **目标分层 + 可复用边界 + 现状越界点名**（§3；与后端 DDD 同级）
- [ ] **改造范围清单**（场景/元数据 **与** 分层收敛同级；R1–R23）
- [ ] 管理 / 标注 / 场景适配交互说明（线框级）
- [ ] 图库 list、搜图结果、入库编辑适配验收条
- [ ] `outputs_heating` / `scene_label` 展示验收
- [ ] 空态 / 加载 / 错误 / **空库产品形态**
- [ ] 可勾选 §验收标准（含 V1–V8 / A1–A5）；给前端架构注意点

### 1.3 Out of Scope（非目标）

| 项 | 出处 |
| --- | --- |
| MySQL→PG 迁移脚本 / 旧数导入 | 契约 §0 A3、§7 |
| **FlyB 空兜底工作流 / 接口编排** | §0 A4、§6 Search、§7、附录 Q |
| 真传 S3 SDK / 桶策略 UI | §5 / §7（FE 只消费完整 https） |
| 像素级结果卡重做 / 旧 MVP §9 wire | §0.1、§7 |
| `range_hood` 规则明细产品配置 | §7（待配置；UI 可列种子场景但规则编辑可后置） |
| 业务 Vue/TS 实现、npm、**git commit/push** | 本刀禁止；实现归前端架构 |
| 软删 UI（PG 为删行 + `image_deletions`；本轮管理面**不做删图入口**除非另刀） | §2.5；与 MVP「不做软删」一致精神 |
| 外部候选审核台（`external_candidates`）完整产品 | §6 审核候选 TBD；本刀仅预留「非本轮」 |
| 模型切换 / collection 运维 UI | §2.10 / §6 模型 TBD |

---

## 2. 契约对齐表（写死）

| # | 主题 | 契约出处 | FE 必须行为 |
| --- | --- | --- | --- |
| C1 | 默认 scene = **`general`** | §0 A5；§2.6 种子；§6 Search/List | 凡请求 `scene` 缺省/固定值：**`general`**。**禁止**再写死 `normal`。 |
| C2 | 旧码映射 | §0 A5；附录 M | 产品映射：`normal`→`general`，`device`→`oven`。UI 文案用中文名（通用 / 一体机）；API 只发新 code。若用户机或缓存仍见旧码，适配层映射到新码，**不**把旧码发给 PG API。 |
| C3 | 场景枚举（种子） | §2.6 | `general`（include_all）/ `oven`（outputs_heating）/ `range_hood` / `us_diet` / `cn_diet` / `jp_diet`。未知 code → 按后端错误态（契约：未登记 scene **TBD**；FE 不静默当空库）。 |
| C4 | 元数据无 path | §0.1；§2.4；§5；§6 Index/List | **禁止** `images.path` / 本地 path 作 `<img src>`。展示与下载认 **完整 https**：优先 `thumb_url`，其次 `original_url`（字段名以终稿 wire 为准；过渡期可兼容旧 `image_url` 若后端仍回，但**目标态以 S3 URL 字段为准**）。 |
| C5 | 主键 uuid | §2.4；§3；§4.1 | 列表/结果 `id` 为 uuid 字符串；勿假定 BIGINT。 |
| C6 | **无 FlyB** | §0 A4；§6；§7；附录 Q | 自建空 → 响应 **`source=empty`**（或等价空结果）。**不**展示「FlyB 兜底」通道为可用产品路径；不引导切 FlyB 补结果。现网 predict Tab 是否物理下线 **另刀**；本刀自建路径验收以无 FlyB 为准。 |
| C7 | 空库重建 | §0 A2 | 产品按**空库**设计文案与 CTA（引导入库），不假设旧 MySQL 样图仍在。 |
| C8 | 拒识沿用 | §0 A6；附录 Q | `score_threshold` 缺省 0.55；`{0}∪[0.30,1]`；与 scene **正交**。控件验收仍见 `acceptance-score-threshold-control.md` v0.2。 |
| C9 | 加热输出 | §2.6 `outputs_heating`；§4.3 步骤 4 | 当前 scene 对应 `outputs_heating=true`（种子：`oven`）时，结果须能展示加热组 + `food_state`（字段 **待契约** wire 名）。 |
| C10 | 场景标签 | §2.7 `scene_labels`；§4.3 步骤 3；A1 | 有 scene 自有类别时展示 **`scene_label`**（或契约终稿名）；无则回落通用 category 名。 |
| C11 | 多场景成员 | §2.8–2.9 | 一图可属多场景；有效成员 = 规则 ∪ manual 覆盖（含排除）。管理/标注面须能看成员来源（rule / manual）——细 API **TBD**，交互位本刀先锁定。 |
| C12 | List 无 soft-status | §6 List | 未删即在库；**不做** status=deleted 筛选 UI。 |

---

## 3. 目标分层 · 可复用边界 · 现状越界（原则 · 与后端 DDD 同级）

> **总裁原则（2026-11-04）**：前端也必须按**最标准架构分层**，做好**可复用**设计。本原则与后端 DDD **同级**；M6 场景/元数据改造不得以「先堆进大文件再以后重构」为由跳过。  
> **选用理由**：摸仓后 `frontend/src` **尚无** `pages/` / `features/` / `entities/` / `shared/`；现为扁平 `App.tsx` + `components/` + `lib/` + `types.ts`（Vite React，无 router）。本轮**目标态采用 Feature-Sliced Design 精简四层**——`pages` / `features` / `entities` / `shared`——与业界 FSD 对齐、词汇稳定、便于与后端限界上下文对照；**不**引入 Nest/Angular 模块体系。实现可渐进搬迁，但**新代码必须落在目标层**，禁止继续加厚 `App.tsx` / `ManagementViews.tsx` 上帝文件。

### 3.1 目标分层（四层）

| 层 | 目录（相对 `frontend/src/`） | 职责 | 可依赖 |
| --- | --- | --- | --- |
| **pages** | `pages/search` · `pages/library` · `pages/import` · `pages/tasks` · `pages/ingredients`（或等价） | 路由级/视图级组装：布局拼装、把 features 接到一起；**薄** | features、entities、shared |
| **features** | `features/search` · `features/library-list` · `features/index-upload` · `features/score-threshold` · `features/scene-select` · `features/image-label` … | **用户动作**：搜一次、列一页、入一批、调拒识、切场景、manual 覆盖成员 | entities、shared；**禁止** features 互为深耦合循环 |
| **entities** | `entities/image` · `entities/scene` · `entities/category` · `entities/search-result` … | 领域模型、归一化 DTO、展示小组件（结果卡字段、scene chip） | shared only |
| **shared** | `shared/ui` · `shared/api` · `shared/lib` · `shared/config` | 无业务语义的 UI（Dialog、EmptyState）、http 基建、`safeHttpUrl`、设计 token 用法 | **不**依赖 pages/features/entities |

**与现壳映射（搬迁指南，非一次拆完也可验收「新码落点」）**：

| 现位置 | 目标落点 |
| --- | --- |
| `App.tsx` 搜图工作台 UI/状态 | `pages/search` + `features/search` + `features/score-threshold` + `features/scene-select` |
| `ManagementViews.tsx` 四视图 | 拆为 `pages/library|import|tasks|ingredients`，共享壳可留 `pages/management-shell` |
| `lib/search.ts` / `images-api.ts` / `index-api.ts` | `shared/api`（client）+ `entities/*/model`（normalize）+ `features/*/api`（若需 feature 专用编排） |
| `components/Dialog.tsx` · `ImageFilePicker.tsx` | `shared/ui` |
| `components/BatchSearchPanel.tsx` | `features/batch-search`（现网） |
| `types.ts` 大杂烩 | 拆入 `entities/*/types`；跨切仅留 `shared/api/types` 基建 |

### 3.2 可复用边界（写死）

| 边界 | 允许 | 禁止 |
| --- | --- | --- |
| **shared/ui** | Dialog、EmptyState、Metric、Skeleton、按钮/输入气质 | 懂 `scene=general`、拒识默认、FlyB/empty 文案分支 |
| **shared/api** | `apiClient`/`request`、baseUrl、错误 envelope 解析、`readableApiError`、Abort 约定 | 页面私有业务分支；在 shared 写死「库空 CTA 文案」 |
| **shared/lib** | `safeHttpUrl`、clamp score、format time | 拼业务默认 scene——默认 scene 属 **entities/scene** 或 **features** 配置 |
| **features/\*** | 该动作的 hook（`useLibraryList`）、面板、提交编排 | 另一 feature 内部深 import；list+import+label 揉成一个 feature |
| **entities/\*** | Image/Scene 类型、`normalizeLibraryItem`（认 thumb_url）、SourceBadge 纯展示 | 直接 `fetch`；持有页面级路由状态 |
| **pages/\*** | 组合 features | **页面文件内直写 `fetch(`**；校验/映射大段内联 |

**硬禁（验收可抓）**：

1. **禁止页面（及现 `App.tsx` / `ManagementViews.tsx`）直写 `fetch(`**——一律走 `shared/api` 或 feature api 模块。  
2. **禁止业务规则堆在单文件**：默认 scene、`normal→general` 映射、S3 URL 选取、拒识合法集、空库文案矩阵不得继续堆进唯一上帝组件。  
3. **可复用优先**：场景选择器、拒识控件、结果卡字段、Empty/Loading/Error 条做成 **shared 或 feature 导出**，搜图与图库**共用**场景选择器，禁止各写一份 divergent 下拉。

### 3.3 现状越界点名（对照现壳 · 可勾选「本轮须收敛」）

摸仓结论（`frontend/src`）：`App.tsx` ~434 行；`ManagementViews.tsx` ~554 行；`lib/search.ts` ~316 行；扁平 `components/` + `lib/`，**无分层目录**。

| ID | 越界点 | 证据（文件/模式） | 本轮须收敛（验收） |
| --- | --- | --- | --- |
| V1 | **上帝页：搜图** | `App.tsx`：导航 + 单张/批量 + 拒识 + 结果 + 详情 + 设置/帮助 + **直写** `fetch` ping | [ ] 新 scene / 加热 / empty 空库逻辑 **不得**再无脑内联加厚；至少抽 `features/scene-select` + search 请求进 api 模块；ping 进 `shared/api` |
| V2 | **上帝页：管理四合一** | `ManagementViews.tsx`：library + import + tasks + ingredients 同文件 | [ ] M6 图库 scene 筛选 / 标注 / 入库场景意向 **新建 feature 或子文件**，禁止只在同文件底部再堆数百行 |
| V3 | **页面直写 fetch** | `App.tsx` → `fetch(.../api/ping)` | [ ] ping/health 迁出页面；页面只调 `shared/api` |
| V4 | **api 分散但无实体层** | `lib/search.ts` · `images-api.ts` · `index-api.ts` · `downloads.ts`；默认 `scene='normal'` 写死在 images-api | [ ] normalize 与默认 scene 收口 **entities/scene** + **entities/image**；禁散落魔法字符串 |
| V5 | **可复用未抽：场景** | 搜图无 scene UI；图库写死 `scene: 'normal'`（`ManagementViews`） | [ ] **单一** `SceneSelect` 供 search + library 复用 |
| V6 | **可复用未抽：三态** | `EmptyResults` 内嵌 App；`EmptyState` 内嵌 ManagementViews | [ ] 空/载/错对齐 shared（或统一 props）；空库文案一处配置 |
| V7 | **类型与展示耦合** | 根 `types.ts` 仅 Search*；Library 类型活在 images-api；SourceBadge 绑在 App | [ ] Image 实体类型上收；徽章进 entities 或 shared 展示 |
| V8 | **业务规则单文件化风险** | 拒识 clamp 在 App；list query 在 images-api——若 scene 映射再在 App 复制一份即越界 | [ ] `normal→general` / `device→oven` **唯一**映射表（entities/scene） |

> **收敛定义（本轮可过线）**：不必一次物理删光 `App.tsx`；但 PR 须证明：(a) 新增 M6 行为落在 features/entities/shared（或明确子路径）；(b) V1–V8 勾选满足；(c) 无新增页面级 `fetch(`；(d) SceneSelect 复用 ≥2 入口（search+library）。完整目录搬家可 follow-up，**不可**零收敛只改字符串。

### 3.4 分层收敛 · 与场景/元数据同级

下列 **A\*** 与后文场景/S3/无 FlyB 条目 **同级**，测试与 PM 验收不得只测字段忽略架构。

| ID | 项 | 验收要点 |
| --- | --- | --- |
| A1 | 目标分层落盘 | 出现 `pages`/`features`/`entities`/`shared`（或 §3.1 等价且树可见）；或本轮新建 M6 文件已落入该树 |
| A2 | 无页面直写 fetch | 对本轮改动 grep `fetch(`：仅允许 `shared/api/**`（及迁入中的 api 模块） |
| A3 | SceneSelect 可复用 | 搜图 + 图库共用同一实现；默认 general |
| A4 | 映射单源 | scene 旧码映射、S3 URL 选取各一处；禁止第三份复制 |
| A5 | 上帝文件不继续膨胀 | `App.tsx` / `ManagementViews.tsx` 本轮以抽出模块为主；M6 主逻辑不继续堆进上帝文件 |

---

## 4. 改造范围清单（给前端架构 / PM）

> **改动项计数摘要**：下文 **R1–R23 = 23 条须改/适配**（含 **分层/可复用收敛 R19–R23**，与场景/元数据 **同级**）；**K1–K8 = 8 条明确不动**。  
> 另见 **§3** 越界 V1–V8、分层验收 A1–A5——测试须勾选，不得只验 API 字段。

### 4.1 须改 / 须适配（R）

| ID | 页面/模块 | 文件（相对 `frontend/src/`） | 要改什么 |
| --- | --- | --- | --- |
| R1 | 图库 list 请求 | `lib/images-api.ts` · `ManagementViews.tsx` | 默认/`scene` 参数：`normal` → **`general`**；注释与 info 文案同步 |
| R2 | 图库缩略图字段 | `images-api.ts` `normalizeItem` | 认 **`thumb_url` / `original_url`**（S3 https）；**禁止** path；可短暂兼容 `image_url` 作 fallback，验收以 S3 字段为准 |
| R3 | 图库场景筛选 UI | `ManagementViews` library 工具栏 | 增加 **scene 选择**（至少单选种子场景；默认 general）。禁止「不传 scene 冒充全库」 |
| R4 | 图库卡片元数据 | library 卡/行 | 展示场景 codes、类别名；uuid id；入库时间；去掉对 path/旧 status 的依赖文案 |
| R5 | 搜图请求 scene | `lib/search.ts` `searchLocalImages` · `App.tsx` | 自建搜显式带 `scene`（默认 **general**）；提供场景切换（与拒识共存） |
| R6 | 搜图结果加热 | 结果卡 / 详情 / 类型 | 当 scene=`oven`（或响应标明 outputs_heating）：展示加热组 + `food_state` |
| R7 | 搜图 scene_label | 结果卡 category 位 | 优先展示映射后的 **scene_label**；无则通用 category |
| R8 | 搜图通道 / 空结果 | `SourceBadge` · 底注 · `EmptyResults`/`no-results` | **本轮无 FlyB**：空结果走 empty 产品文案；弱化或隐藏「FlyB 兜底」为自建主路径结论；不把 flyb 当成功通道验收 |
| R9 | 空库产品形态 | 搜图空态 + 图库空态 | 区分「从未搜」/「库空搜不到」/「筛选无命中」；库空 CTA → 上传与导入 |
| R10 | 入库响应映射 | `lib/index-api.ts` | 成功项主展示 URL 改 S3 https；`path` 仅调试可留 raw，**不上 UI img**；id 按 uuid |
| R11 | 入库编辑 · 场景 | `ManagementViews` import 侧栏 | 入库时可指定目标场景或多选意向（**待契约**字段）；至少能选默认 general，并说明「规则可能再分配」 |
| R12 | 入库编辑 · 类别 | import 侧栏 | 从「写死字符串 categories」演进为对接类别 API（若本轮后端未交 FE API：**待契约**，UI 保留下拉但标注数据源） |
| R13 | 标注 · 场景成员 | library 详情或标注面板（可新建轻量） | 查看/覆盖 scene 成员：manual 加入/排除；展示 origin=rule\|manual（API TBD） |
| R14 | 标注 · 加热相关 | 一体机场景下 | 展示/编辑 `food_state`（raw/cooked/frozen）；加热组只读来自类别沿用（§2.2） |
| R15 | 文案 / 帮助 | `App.tsx` help · library info | 去掉「等待 MySQL / path / FlyB 必经」表述；写清 PG + S3 + 空库重建 |
| R16 | 类型定义 | `types.ts` 等 | 扩展 scene、heating、scene_label、source 以 empty 为主；uuid id |
| R17 | 列表分页语义 | library | 保持 `page_size=24`；过滤改为 PG scene 成员（契约 §6）；`q`/`category` 语义随后端终稿（category 或改 ID/code——**待契约**） |
| R18 | 验收交叉引用 | `frontend/docs/` 旧卡 | 实现时以**本文**覆盖旧卡中 `scene=normal`、FlyB 空兜底必验项；旧卡可不改文件，测试以 M6 为准 |

| R19 | **分层落位** | 新建 `pages`/`features`/`entities`/`shared`（或 §3.1 等价） | M6 新增模块落入目标层；与场景改造 **同级必做** |
| R20 | **消灭页面 fetch** | `App.tsx` ping 等 | 迁 `shared/api`；禁止新页面直写 fetch（§3.2 / V3 / A2） |
| R21 | **SceneSelect 可复用** | 搜图 + 图库 | 单一实现默认 general；禁止两处 divergent 下拉（V5 / A3） |
| R22 | **实体归一化收口** | `lib/*-api` normalize + 默认 scene | `entities/image`·`entities/scene`；映射单源（V4 / V8 / A4） |
| R23 | **上帝文件收敛** | `App.tsx` · `ManagementViews.tsx` | 标注/加热/空库/场景 **抽出** feature；禁止只在同文件堆逻辑（V1 / V2 / A5） |
### 4.2 明确不动（K）

| ID | 项 | 说明 |
| --- | --- | --- |
| K1 | 侧栏导航结构 | search / library / import / tasks / ingredients 信息架构保留 |
| K2 | 拒识控件数字与交互 | 仍按 score-threshold 验收卡；仅确保与 scene 切换共存 |
| K3 | 批量搜现网面板 | `BatchSearchPanel` 本轮不强制改自建批量 |
| K4 | 下载 ZIP / 目录保存 | `DownloadDialog` / downloads 逻辑不重做 |
| K5 | 设计 token / 整站换肤 | 复用 `styles.css` / `ManagementViews.css` |
| K6 | 真 S3 上传凭证 UI | 入库仍走后端 API；FE 不直传密钥 |
| K7 | 外部候选审核台 | 另刀 |
| K8 | git commit / 业务代码（本文档作者） | 规范刀禁止 |

---

## 5. 管理 / 标注 / 场景适配 · 交互说明（线框级）

### 4.1 场景选择器（共用模式）

用于：**搜图 query 面板**、**图库工具栏**（可同一组件气质）。

```
场景 [ 通用(general) ▼ ]
     · 通用（日常）      general     include_all
     · 一体机            oven        加热输出 ON
     · 抽油烟机          range_hood
     · 美国饮食          us_diet
     · 中国饮食          cn_diet
     · 日本饮食          jp_diet
提示：默认「通用」；切换后下次请求带新 scene。
```

- 默认选中 **通用 / general**
- 与拒识控件正交：改 scene 不重置阈值；改阈值不改 scene
- 搜图：改 scene **不自动重搜**（与拒识一致，点「开始搜索」才带新值）——**验收锁定**
- 图库：改 scene → `page=1` 并立即重新 list（与改类别一致）

### 4.2 管理 · 图库（library）

```
┌─ 图片数据 / 图库 ──────────────────────────────────────────┐
│ [搜索 q…] [类别?] [场景: 通用▼] [网格|列表] [+导入]        │
│ 状态: 图库已接入 · scene=general · 每页 24                 │
├─ 指标：总量 total · … ────────────────────────────────────┤
│ 卡： [thumb_url] 标题/—  类别  场景chips  时间             │
│ 空库：暂无已入库图片 → CTA 去导入（空库重建文案）           │
│ 加载 / 错误：同既有三态气质                                │
└────────────────────────────────────────────────────────────┘
（可选）点卡 → 预览 Dialog：大图 original_url/thumb · uuid
        → 「场景成员」：列表 rule/manual · 手动纳入/排除【标注】
```

### 4.3 标注 · 场景成员与食物状态

```
┌─ 图片标注（库内详情或侧抽屉）─────────────────────────────┐
│ 预览（S3 URL）  uuid                                      │
│ 通用类别：…（树/名 · 待契约 API）                          │
│ food_state：[未设|生|熟|冷冻]                              │
│ 场景成员：                                                 │
│   ☑ general   origin=rule    （只读或不可排除若产品定）    │
│   ☑ oven      origin=manual  [移除手动]                    │
│   ☐ cn_diet   （未成员） [手动加入]                        │
│ 说明：去掉手动记录后恢复规则分配（契约 §2.9）               │
└────────────────────────────────────────────────────────────┘
```

- API 未齐时：UI 可先只读展示响应里已有的 `scenes[]` + 来源字段；写操作标「待契约」但布局位保留  
- **不做**本轮物理删图按钮（K 精神 / 另刀）

### 4.4 入库编辑（import）

```
┌─ 上传队列 … ──────────────┬─ 整理入库信息 ───────────────┐
│ 文件卡…                    │ 类别（待对接 categories）    │
│                            │ 场景意向：[general ▼] 可多选? │
│                            │ 说明：最终成员以规则+manual为准│
│                            │ [提交入库]                   │
└────────────────────────────┴─────────────────────────────┘
成功列表：展示 thumb/original URL · uuid · 分配到的 scenes
失败列表：文件名 + 错误码/文案（无 path 当链接）
```

### 4.5 搜图 · 场景 + 加热结果

```
┌─ query-panel ─────────────────┬─ results-panel ──────────┐
│ 自建 | 现网（现网另刀弱化）    │ 加载骨架不变              │
│ 场景 [通用▼]  拒识 [0.55]     │ 有结果：卡 = 图 + 分      │
│ 上传/URL + 开始搜索           │   + 类别或 scene_label    │
│                               │   + 若加热：加热组·状态   │
│                               │ 空(source=empty)：        │
│                               │   「自建库暂无匹配」       │
│                               │   库空时附加导入 CTA      │
│                               │ **不**出现 FlyB 成功徽章  │
└───────────────────────────────┴──────────────────────────┘
```

---

## 6. 分面验收条

### 6.1 图库 list（相对旧 §3.5 / 契约 §6 List）

- [ ] 请求默认或显式 **`scene=general`**（抓包可证）；**无** `scene=normal`
- [ ] `page_size=24`；`page` 1-based；「全部类别」omit category（勿空串）
- [ ] 缩略图 src 仅为 **http(s)**（`thumb_url` / `original_url` / 兼容 `image_url`）；**从不**用 path
- [ ] `thumb_url`/`image_url` 皆空 → 占位不崩
- [ ] 场景切换后 list 刷新且 page 回 1
- [ ] 空库：`total=0` 无筛选 → 空库文案 + CTA 导入（见 §6）
- [ ] 加载 / 错误 / 重试行为不回归（相对现 library 壳）
- [ ] **无**软删/删图入口（本轮）

### 6.2 搜图结果（契约 §6 Search · §4.3）

- [ ] 自建请求带 **`scene`**（默认 general）；切换场景后点搜索带新值
- [ ] 拒识与场景共存；合法集不变
- [ ] 有结果：展示相似分；类别位优先 **scene_label**（当有），否则 category
- [ ] `outputs_heating` 场景（oven）：结果或详情可见加热组与 `food_state`（字段名待契约，但位必须有）
- [ ] 自建空：`source=empty` 或空 items → **空结果产品态**；**不**验收 FlyB 兜底成功路径
- [ ] 不因本刀破坏既有 loading skeleton / 首次未搜 EmptyResults 布局（文案可按 §6 增强）

### 6.3 入库编辑适配（契约 §6 Index）

- [ ] 提交不依赖本地 path 字段展示成功图
- [ ] 成功项可点开的预览 URL 为 https
- [ ] id 按字符串 uuid 渲染不报错
- [ ] 场景意向控件默认 general；说明与规则分配关系（文案可测）
- [ ] 失败项不把文件系统 path 当超链接

### 6.4 `outputs_heating` / `scene_label`

- [ ] 场景选 oven（或 API 回 `outputs_heating=true`）时，至少一条结果或详情展示加热相关信息位（无数据时显示「—」而非隐藏位导致布局跳动——二选一须稳定）
- [ ] `scene_label` 有值时类别主文案用其展示；无值回落通用名
- [ ] general 场景不强制加热位（可隐藏加热块）

### 6.5 场景多选 / 规则 / manual（管理·标注）

- [ ] 只读：能看到图片所属 scene codes
- [ ] 若写 API 已交：manual 加入/排除后列表刷新，origin 可区分
- [ ] API 未交：界面有「待接入」态，**不**假装已写入

---

## 7. 空态 / 加载 / 错误 / 空库产品形态

### 7.1 加载态

- [ ] 图库首屏、翻页、改 scene/筛选：面板 loading/skeleton，不永久闪空文案无过渡
- [ ] 搜图：沿用 `.loading-state`；文案可「正在自建库中检索…」

### 7.2 空态矩阵

| 情境 | 期望 |
| --- | --- |
| 搜图 · 从未点搜索 | 保持「等待查询图片」类 EmptyResults |
| 搜图 · 已搜 · 空 items · 库可能为空 | 标题「暂未找到相关图片」；说明**不**提 FlyB；若产品能知空库（可选 health/total）→ 增加「去入库」CTA |
| 搜图 · 有 items 但前端筛选空 | 「没有符合筛选」+ 清筛选（现有） |
| 图库 · total=0 · 无筛选 | **空库重建**文案：「暂无已入库图片（空库）」+ CTA 导入 |
| 图库 · total=0 · 有 q/类别/非默认场景 | 「无匹配」+ 清空筛选 / 切回通用 |

### 7.3 错误态

- [ ] 网络/5xx：错误条 + 重试；不把错误扮成空库
- [ ] 未知 scene / 校验失败：可读原因（码名若返回则展示）
- [ ] 禁止用 path 相关错误误导用户「文件不在磁盘」作为主文案（对象在 S3）

### 7.4 无 FlyB（写死）

- [ ] 自建主路径验收用例**不得**要求出现「FlyB 兜底」成功徽章
- [ ] UI 若仍保留 SourceBadge 组件：`flyb` 分支可不删代码，但 **M6 测试用例不点亮该路径**；底注不以 FlyB 为未命中时的下一步
- [ ] 空结果下一步：换图 / 降拒识 / 换场景 / 去入库 —— 而非「改走外搜」

---

## 8. 可勾选总验收标准（§汇总）

### 8.1 契约硬约束

- [ ] 默认 scene = **general**（list + search）
- [ ] 旧 normal/device **不**作为请求值；UI 映射到 general/oven
- [ ] 无 path 作图片地址；只用 S3 https 字段
- [ ] 本轮自建空 = empty 产品态；**无 FlyB 编排验收**
- [ ] 空库文案与导入 CTA 就绪

### 8.2 改造清单落地

- [ ] R1–R23 中本轮排入实现的项均有对应用户可见行为、adapter 或**分层收敛**变更（未排入须 PR 说明 defer）
- [ ] **R19–R23 / A1–A5 / V1–V8** 与场景字段验收 **同级**勾选
- [ ] K1–K8 未被误改

### 8.3 三态与加热/标签

- [ ] §6.1–6.5 与 §7 勾选项通过「测试」bot

### 8.4 分层 · 可复用 · 越界收敛

- [ ] §3.1 目标分层被本轮新码遵守（或目录已建）
- [ ] §3.2 无页面直写 fetch；业务规则未堆回上帝单文件
- [ ] §3.3 V1–V8 可勾选项通过
- [ ] §3.4 A1–A5 通过；SceneSelect **可复用**于 search+library

---

## 9. 字段映射速查（目标态 · 细名待契约处已标）

| UI | API / 数据（PG v0.1） | 备注 |
| --- | --- | --- |
| 场景选择器 | `scene=<code>` | 默认 `general` |
| 拒识 | `score_threshold` | 沿用附录 Q |
| 图库缩略图 | `thumb_url` → fallback `original_url` | 兼容旧 `image_url` 可选 |
| 图库/结果 id | `id` uuid | |
| 类别展示 | category 名 / code | 树 API TBD |
| 场景标签 | `scene_label`（名待契约） | §4.3 |
| 加热组 | heating group name/code（待契约） | 随 `outputs_heating` |
| 食物状态 | `food_state` | raw/cooked/frozen |
| 通道 | `source=self\|empty` | **无 flyb 本轮** |
| 入库文件 | multipart 字节 | 无 path SSOT |

---

## 10. 角色分工

| 角色 | 职责 |
| --- | --- |
| **前端设计**（本文档） | 验收标准、改造范围清单、线框、契约对齐 |
| **前端架构** | 按 §3 清单改 adapter/UI；不 commit 与否听 PM；对齐后端终稿 wire |
| **测试** | 按 §5–§7 勾选；重点 scene 迁移、无 path、无 FlyB、空库、加热/标签 |
| **boboji（PM）** | 收刀、派前端架构、协调契约 TBD 回写 |
| **后端** | §6 差量实现；空→empty；S3 URL；scene 默认 general |

---

## 11. 给前端架构的注意点（≤5）

1. **分层优先于堆字段**：按 §3 `pages/features/entities/shared` 落新码；**禁止**在 `App.tsx` / `ManagementViews.tsx` 继续堆 scene/加热/空库；SceneSelect **可复用**，消灭页面 `fetch`（V1–V5 / R19–R23）。  
2. **全仓默认 scene→`general`**：旧码只读映射到唯一 entities 表，不写出 `normal`/`device`。  
3. **图片地址只认 https**：normalize 收口 entities；**禁止** path→`src`；uuid 当 key。  
4. **无 FlyB 产品态**：自建空=`empty`/空库 CTA；不以接 FlyB 为完成标准。  
5. **场景⊥拒识 + 加热位**：搜图改 scene 不自动搜；图库改 scene 立即 list；oven 预留加热/`scene_label`，缺字段显示「—」。

---

## 12. 待契约（不阻塞交互位，阻塞写死字段名时）

| 项 | 说明 |
| --- | --- |
| search/list 结果细 wire | 契约 §6：「细字段表 TBD（FE 对齐刀）」——本刀先锁语义 |
| 场景成员读写 API | §6 场景管理 TBD |
| 类别树 CRUD API | §6 类别 TBD |
| category list 筛选：名 vs code vs id | 实现前与后端对表 |
| 现网 predict Tab 是否下线 | 另刀；本刀自建路径按无 FlyB |

---

## 13. 修订记录

| 日期 | 说明 |
| --- | --- |
| 2026-11-04 | v0.2：总裁原则补强——新增 **§3 目标分层 / 可复用边界 / 现状越界**（V1–V8、A1–A5）；改造清单 R19–R23 与场景/元数据同级；R1–R23 |
| 2026-11-04 | v0.1：PG v0.1 M6 前端验收 + 改造清单（R18/K8）；对齐契约 A4/A5、§2.4–2.9、§4.3、§5、§6、§7；默认 general；无 path；无 FlyB；空库形态 |

---

*文档结束 · 交 boboji 后派前端架构实现*
