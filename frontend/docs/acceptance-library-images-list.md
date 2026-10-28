# 图库 list · GET /api/images · 交互说明与验收标准

> 角色：**规范归前端设计**；**实现归前端架构**；**验收归「测试」bot**；**PM 归 boboji**  
> 范围：管理页「图库」（`ManagementViews` · `view === 'library'`）对接 **`GET /api/images`**；**不重做整站**；**本文档不写业务代码**。  
> 契约对齐：`docs/image-search-mvp-data-contract.md` **§3.5**（终稿）。下文若与契约冲突以契约为准；实现前仍须核对字段。  
> 文档版本：v0.1 · 2026-10-28（Asia/Shanghai）  
> 相关：入库 + 搜图见 `frontend/docs/acceptance-index-search-mvp.md`（API 落地 `/api/index`、`/api/search`）；本刀**仅**图库 list。

---

## 1. 背景与范围

### 1.1 背景

现有管理页「图片数据 / 图库」已具备工具栏与空壳：

- 搜索框（`libraryQuery`）、类别下拉（`libraryCategory`）、网格/列表切换（`libraryLayout`）
- 指标卡占位（图片总量 / 食材类别 / 待入库任务）
- 空态 `EmptyState` + 底部分页文案「每页 24 张」（按钮 disabled）
- 状态条：「等待数据接口」；info：「后续接入图片详情、类别筛选与分页」

本轮把该壳接到真数据 **`GET /api/images`**（契约 §3.5）。

### 1.2 In Scope（本轮）

- [ ] 图库 list 交互说明（线框级）+ 可勾选验收条
- [ ] Query：`scene` 默认 `normal`、`page` / `page_size=24`、`q`、`category`
- [ ] 响应认 **`image_url`**（及契约列出的列表字段）；卡片无图时降级可验收
- [ ] 空态 / 加载态 / 错误态验收卡
- [ ] 明确复用现有 `mg-*` 组件与样式；**不做软删**

### 1.3 Out of Scope（非目标）

- **不做软删 / 硬删 API 与 UI**（契约 §3.5 / 清单写死；响应永不含 `status=deleted`）
- 不做图库写库 CRUD、批量编辑、详情抽屉完整产品化（点击预览若壳已有可轻量接 `image_url`，非本刀必做重交互）
- 不重做侧栏导航、导入 / 任务 / 食材页
- 不实现 Vue/TS 业务代码、不装依赖、不 git commit（实现归前端架构）
- 不改 `POST /api/index` / `POST /api/search` 验收文档本体（仅交叉引用）
- 不做跨 scene 默认混列、不做「全场景」筛选项（须显式产品另议）

---

## 2. 契约对齐 · §3.5 `GET /api/images`

**出处**：仓根 `docs/image-search-mvp-data-contract.md` → **§3.5**（图库 list）。  
**用途**：管理页图库列表真数据；对齐 FE library：名称搜索、类别筛选、每页 24、网格/列表由 FE 负责。

### 2.1 Query 参数

| 字段 | 类型 | 默认 | FE 行为 / 验收 |
| --- | --- | --- | --- |
| `scene` | string | **`normal`** | 与 search **同字段**。FE **必须**传或依赖缺省；**禁止**为「列全库」而省略语义上的混场景。未知短串 → 后端 `422` `LIST_UNKNOWN_SCENE`。MVP 若 UI 无 scene 切换，请求固定 `scene=normal`（或省略令服务端默认）。 |
| `page` | int | `1` | 1-based；`<1` 勿发。翻页控件驱动。 |
| `page_size` | int | **`24`** | **写死 24**，与壳文案「每页 24 张」一致；勿擅自改成 20/50（除非契约改默认且同步文案）。范围后端 1～100。 |
| `q` | string \| omit | — | 对接搜索框；**trim 后空则不传**。匹配 title 子串或 tags 元素子串（契约）；不匹配 path。 |
| `category` | string \| omit | — | 对接类别下拉。**「全部类别」= 不传本参数**（**勿传空串**）。精确匹配 `images.category`。 |
| `trace_id` | string \| omit | 服务端生成 | FE 可选透传；错误展示可带出 **待契约**/可选 |

### 2.2 服务端固定语义（FE 须知）

- 固定过滤：`status = 'active'` **且** `scenes` JSON **包含**请求 `scene`
- 排序：`created_at DESC`，再 `id DESC`
- **只读 list**；响应**永不**含 deleted 行 → FE **不**做软删入口、不展示 status 切换删库

### 2.3 成功响应字段（列表项）

| 响应字段 | 已知 | FE 用法 |
| --- | --- | --- |
| `ok` | 有 | `true` 才渲染 items |
| `trace_id` | 有 | 错误/调试展示可选 |
| `scene` / `page` / `page_size` / `total` | 有 | 分页文案、指标「图片总量」可用 `total` |
| `items[]` | 有 | 网格/列表数据源 |
| `items[].id` | 有 | key；预览/文件网关 |
| `items[].image_url` | **有 · 必认** | 缩略图 / 预览主字段；须可过现网 `safeHttpUrl` 同类校验。优先展示；`null` → 卡片无图占位（不崩） |
| `items[].title` | 有 | 名称列 / 卡标题；搜索框语义对齐 |
| `items[].category` | 有 | 类别列 / chip |
| `items[].tags` | 有 | 列表「属性」位可展 tags 拼接或首个；空则「—」 |
| `items[].scenes` | 有 | 完整标签数组；MVP 可小徽章展示，非必显 |
| `items[].created_at` | 有 | 「入库时间」列 |
| `items[].width` / `height` | 有 | 可选次要信息 |
| `items[].path` / `url` / `qdrant_point_id` / `status` | 有 | path **不能**当 `<img src>`；`status` 恒 `active`；详情调试用 |
| `items[].updated_at` | 有 | 非列表主列 |

**空页**：`200` + `items: []` + `total: 0`（或 `total>0` 但 page 超出 → `items: []`，`total` 仍为全集计数）→ FE 走空态或「本页无数据」文案（见 §5）。

**错误（契约）**：

| 情况 | 码 / 语义 | FE |
| --- | --- | --- |
| 未知 `scene` | `422` `LIST_UNKNOWN_SCENE` | 错误态 + 可读说明 |
| 非法 `page` / `page_size` | `VALIDATION_ERROR` | 错误态；修正参数后重试 |
| MySQL 失败 | `LIST_MYSQL_FAILED`（502） | 错误态 + 重试 CTA |

（错误 envelope 形状若与 search/index 统一，实现时对齐现有 API client；细节 **待契约**/对照契约 §4 错误码表。）

---

## 3. 现有壳复用说明

路径相对 `frontend/src/`（探索副本亦见 box `_explore/ManagementViews.tsx`）。

### 3.1 页面与导航

| 现有 | 路径 / 符号 | 本轮建议 |
| --- | --- | --- |
| 图库 | `ManagementViews.tsx` · `view === 'library'` | **主落点**：接 `GET /api/images`；替换永久空态为真数据 |
| 导入 | `view === 'import'` | 工具栏「导入图片」CTA 保留；入库仍见 index 验收卡 |
| 任务 / 食材 | `tasks` / `ingredients` | 本刀不动 |

### 3.2 可复用 UI

| 能力 | 复用点 | 说明 |
| --- | --- | --- |
| 搜索 | `.mg-search` + `libraryQuery` | 防抖后映射为 `q`；清空按钮已有 |
| 类别 | `.mg-select` + `categories` 常量 | 「全部类别」→ **omit** `category` |
| 布局切换 | `.mg-view-toggle` grid/list | 纯 FE；list 表头 `.mg-library-head` 已有 |
| 指标 | `.mg-metrics` / `Metric` | 「图片总量」→ `total`；其余可暂「—」或后续刀 |
| 空态 | `EmptyState` | 区分「库空」vs「筛选无命中」 |
| 分页壳 | `.mg-pagination` | 文案已写「每页 24 张」；启用上下页，绑定 `page` |
| 预览 | `Dialog` + import 侧 `mg-dialog-image` 模式 | 有 `image_url` 时可点卡预览（可选增强） |
| 样式 | `ManagementViews.css` + `styles.css` token | 不新开设计体系 |
| 结果卡气质 | 搜图 `.result-card` **可参考**，优先用 `mg-upload-card` / 新建 `mg-library-card` 保持管理页一致 | 勿把整站搜图工作台搬进图库 |

### 3.3 壳内已写死、须对齐契约的点

- 分页文案：**每页 24 张** → `page_size=24`
- 类别选项：蔬菜 / 水果 / … / 其他（与入库壳同一 `categories`）
- 搜索 placeholder：「搜索图片名称或食材…」→ 映射契约 `q`（title / tags）
- **无**软删按钮、无 status=deleted 展示位 → 保持不做

---

## 4. 交互说明（线框级）

### 4.1 布局

```
┌─ 图片数据 / 图库 工具栏 ─────────────────────────────────────┐
│ [🔍 搜索…] [类别▾] [网格|列表]  [+ 导入图片]   状态: 已连接…   │
├─ 指标 ──────────────────────────────────────────────────────┤
│  图片总量 total     食材类别 —/n     待入库任务 —              │
├─ 面板 ──────────────────────────────────────────────────────┤
│  [网格: 卡 = 图(image_url) + title + category chip]          │
│  或 [列表: 图片/名称 | 类别 | 属性(tags) | 入库时间]           │
│  加载中 → skeleton / 面板内 loading                          │
│  空 → EmptyState + CTA「准备第一批图片」→ import              │
│  错 → 错误条 + [重试]                                        │
├─ 分页 ──────────────────────────────────────────────────────┤
│  共 total 张 · 每页 24 张 · [<] page [>]                     │
└─────────────────────────────────────────────────────────────┘
（可选）点击卡片 → Dialog 大图预览（src=image_url）
```

### 4.2 请求时机

| 触发 | 行为 |
| --- | --- |
| 进入 `library` 视图 | 请求 `GET /api/images?page=1&page_size=24`（`scene` 默认 normal） |
| 搜索输入 | 防抖（建议 300ms）→ `q`；`page` 重置为 1 |
| 改类别 | 更新 `category` 或 omit；`page` 重置为 1 |
| 翻页 | 仅改 `page`；保持当前 `q`/`category`/`scene` |
| 重试 | 用**当前** query 再请求 |

### 4.3 Scene（MVP）

- UI **可不**暴露 scene 切换；请求固定缺省 / 显式 `scene=normal`
- 若后续加 scene Tab，须与 search 同枚举；未知值不发或发后按错误态处理
- **禁止**为省事不传并期望「全场景」——契约禁止缺省混列

### 4.4 点击预览（可选、壳已有 Dialog）

- 有合法 `image_url`：Dialog 展示大图 + `title`
- `image_url` 为空：卡上占位，点击可提示「暂无预览」或禁用，**不**用 `path` 当 URL

---

## 5. 空态 / 加载态 / 错误态 · 验收标准

### 5.1 加载态

- [ ] 首次进入图库：面板内可见 loading 或 skeleton（复用 `.loading-state` / `.skeleton-card` 或 `mg-*` 等价），**不**闪永久空态文案后跳变无过渡
- [ ] 翻页 / 改筛选：有进行中反馈（面板级或分页旁），避免误以为无数据
- [ ] 加载中禁用或忽略重复连点翻页导致乱序（后发覆盖或 abort 前次）

### 5.2 空态

- [ ] `total === 0` 且无 `q`、类别为「全部」：标题类「暂无已入库图片」；说明引导去导入；CTA → `import`
- [ ] `total === 0` 但有 `q` 或非全部类别：说明「无匹配」类文案；提供「清空筛选」类 CTA（重置 q + 全部类别）
- [ ] `total > 0` 但当前 `page` 超出致 `items: []`：不假装「库空」；提示页码无效或自动夹回最后一页（实现二选一，验收时行为稳定即可）
- [ ] 空态下分页：「每页 24 张」可保留；上一页/下一页合理 disabled

### 5.3 错误态

- [ ] 网络失败 / 5xx / `LIST_MYSQL_FAILED`：可见错误条（`role="alert"` 或等价）；提供 **重试**
- [ ] `422` `LIST_UNKNOWN_SCENE` / `VALIDATION_ERROR`：可读原因；不静默空列表冒充无数据
- [ ] 错误时指标与列表不展示陈旧成功数据冒充当前筛选结果（或明确标注「上次成功结果」——推荐直接清空并只显示错误）
- [ ] 工具栏状态从「等待数据接口」改为能反映已接入（如「已连接」/最后同步——文案实现自定，但不得仍写「等待数据接口」在成功拉取后）

### 5.4 成功列表

- [ ] 每页最多 24 条；请求带 `page_size=24`
- [ ] 卡片/行主图使用 **`image_url`**，不用 `path`
- [ ] `image_url` 为 null：占位不白屏、不抛错
- [ ] 展示 `title`、`category`；列表布局含入库时间（`created_at`）
- [ ] 「全部类别」请求**不出现** `category=` 空串
- [ ] 搜索 trim 后空不传 `q`
- [ ] `scene` 行为符合默认 `normal`（抓包或日志可证）
- [ ] **无**删除 / 软删按钮与确认框
- [ ] 网格 ↔ 列表切换不重新丢筛选条件（可同页数据本地切换布局）

---

## 6. 字段与映射速查（给前端架构）

| UI | API |
| --- | --- |
| 搜索框 | `q` |
| 类别「全部类别」 | omit `category` |
| 类别具体项 | `category=<选项文案>` |
| 每页 24 | `page_size=24` |
| 页码 | `page` |
| （隐藏）场景 | `scene=normal` 或缺省 |
| 缩略图 / 预览 | `image_url` |
| 名称 | `title` |
| 类别 chip | `category` |
| 属性列 | `tags` 展示约定（join / 首个） |
| 入库时间 | `created_at` |
| 总量指标 | `total` |

---

## 7. 非目标再确认

- 软删 / 回收站 / status 切换 UI
- 图库内编辑 title/category 写回
- 多 scene 切换产品（可预留，本刀不验收）
- 整站视觉重做、新设计 token
- git commit / 业务代码（本文档作者不提交）

---

## 8. 角色分工

| 角色 | 职责 |
| --- | --- |
| **前端设计**（本文档） | 交互说明、验收标准、契约字段对齐标注 |
| **前端架构** | 按本文档接 `GET /api/images`；复用 `ManagementViews` library 壳 |
| **测试** | 按 §5 勾选三态与 §2 约束（scene/24/q/category/image_url/无软删） |
| **boboji（PM）** | 派单、验收路径回传、再派前端架构 |
| **后端** | §3.5 实现与错误码；文件网关 §3.4 保障 `image_url` |

---

## 9. 给前端架构的注意点（≤3）

1. **`page_size` 固定 24**，与壳文案一致；「全部类别」**省略** `category`，勿传 `""`。  
2. **认 `image_url`** 做 `<img>`；`path` 禁止当 URL；null 要占位。  
3. **默认 `scene=normal`**，不做软删 UI；成功后去掉「等待数据接口」永久空壳。

---

## 10. 修订记录

| 日期 | 说明 |
| --- | --- |
| 2026-10-28 | v0.1 初稿：对齐契约 §3.5；覆盖 library 壳复用与三态验收 |
