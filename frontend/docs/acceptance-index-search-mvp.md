# 图搜图 MVP · 入库 + 搜图结果 · 交互说明与验收标准

> 角色：**规范归前端设计**；**实现归前端架构**；**验收归「测试」bot**；**PM 归 boboji**  
> 范围：在现有 Image-Embedding 前端壳上做入库面板与搜图结果增强；**不重做整站**；**本文档不写业务代码**。  
> API 字段以「数据规范」终稿为准；下文未定字段一律标 **「待契约」**。  
> 文档版本：v0.1 · 2026-10-27（America/Denver）

---

## 1. 背景与范围

### 1.1 背景

现有前端（React + TypeScript + Vite，仓：`D:\python\Image-Embedding\frontend`）已具备：

- 图搜图工作台：单张 / 批量查询、结果网格·列表、详情、下载
- 数据管理壳：图片数据 / 上传与导入 / 入库任务 / 食材管理（多为本地草稿或「等待接口」空态）
- 现网搜索走 `POST /api/predict/searchImage_file|url`（FlyB 外搜 + 可选 embedding 过滤）

MVP 目标：对接即将落地的 **自建向量入库 / 检索**（`POST /index`、`POST /search`），在壳上补齐：

1. **入库页/面板**：选图或目录 → 进度 → 成功 / 失败列表  
2. **搜图结果**：自建有结果时展示；若 API 返回来源，标注 **自建 / FlyB 兜底**  
3. **空态 / 加载态 / 错误态** 可勾选验收

### 1.2 In Scope（本轮）

- [ ] 入库交互说明（线框级）+ 验收条目  
- [ ] 搜图结果交互说明（含来源标签）+ 验收条目  
- [ ] 三态（空 / 加载 / 错误）验收卡  
- [ ] `POST /index` / `POST /search` 字段表（已知 + 待契约）  
- [ ] 明确复用哪些现有组件 / 样式，避免整站重做  

### 1.3 Out of Scope（非目标）

- 不重做侧栏导航、设置、帮助、下载 ZIP / 目录保存整套逻辑  
- 不实现业务 Vue/TS 代码、不装依赖、不开 PR（实现归前端架构）  
- 不定义 Qdrant / MySQL / DINOv3 运维细节（归后端 / 数据规范）  
- 不做完整「图片数据」库浏览 CRUD、食材管理写库  
- 不做 ERP / 训练一体机 / ImageRecall 填坑  
- 不强制本轮必须上线 FlyB 兜底；若后端未返回 `source`，前端可不展示标签，但需预留展示位（见 §4）

---

## 2. 现有壳复用说明

以下路径均相对 `frontend/src/`。

### 2.1 页面与导航

| 现有 | 路径 / 符号 | MVP 建议 |
| --- | --- | --- |
| 图搜主页 | `App.tsx` · `view === 'search'` | **主搜图结果区继续用右侧 `results-panel`**；查询侧可增加「自建库搜索」入口或与现有单张搜并存（实现时二选一，见风险） |
| 上传与导入 | `ManagementViews.tsx` · `view === 'import'` | **入库面板主落点**：现有拖拽区、待导入队列、侧栏「整理入库信息」已具备壳；将「提交入库 · 待接入」接上 `POST /index` |
| 入库任务 | `ManagementViews.tsx` · `view === 'tasks'` | **进度 / 成功失败列表主落点**：现有状态 Tab + 空态；接真实任务或本批会话进度 |
| 图片数据 / 食材 | `library` / `ingredients` | 本轮仅保持空态提示，不扩 CRUD |

### 2.2 可复用组件与模式

| 能力 | 复用点 | 说明 |
| --- | --- | --- |
| 多图选择 | `components/ImageFilePicker.tsx` | 支持 `multiple`；accept 与现有一致（入库可对齐 import：JPG/PNG/WebP，是否含 BMP **待契约**） |
| 批量队列气质 | `components/BatchSearchPanel.tsx` + `BatchSearchPanel.css` | 进度条、逐项 pending/running/success/error、停止 / 继续 / 重试、消息条；**入库进度与失败列表对齐此气质** |
| 导入壳 | `ManagementViews` 的 `mg-dropzone` / `mg-upload-grid` / `mg-import-aside` | 选图、队列卡、侧栏汇总；目录选择若浏览器支持可增量加，不替换拖拽 |
| 空态 | `EmptyResults`（`App.tsx`）、`EmptyState`（`ManagementViews`） | 统一「图标 + 标题 + 说明 + CTA」 |
| 加载态 | `.loading-state` + `.skeleton-card`（`styles.css` / `App.tsx`） | 搜图结果等待时复用 |
| 结果卡 | `.result-card` / `ResultImage` / `Score` / 网格·列表切换 | 搜图结果继续用；**新增来源徽章**，不改卡骨架 |
| 错误条 | `.message-banner.message-error` | 全局错误；单项错误用队列行内文案（对齐 batch-job-error） |
| Dialog | `components/Dialog.tsx` | 失败详情、确认取消批量入库 |
| 样式 token | `styles.css` `:root`（`--primary` `#2c56a0` 等）+ `ManagementViews.css` | 不新开设计体系 |

### 2.3 现有 API（对照，非本轮主路径）

| 方法 | 用途 |
| --- | --- |
| `POST /api/predict/searchImage_file` | 现网单张/批量文件搜（FlyB 主路径） |
| `POST /api/predict/searchImage_url` | URL 搜 |
| `GET /api/images/download` | 下载代理 |
| `GET /api/ping`（前端探测） | 连接状态 |

MVP 新增：`POST /index`、`POST /search`（完整前缀是否带 `/api` **待契约**；建议与现网一致挂在 `/api/...`）。

---

## 3. 入库面板 · 交互说明

### 3.1 入口与布局（线框）

建议落在导航 **「上传与导入」**（`import`），必要时在「入库任务」展示本批会话摘要。

```
┌─ 上传与导入 ─────────────────────────────────────────────┐
│ [选择图片] [选择目录·可选]   JPG/PNG/WebP · ≤15MB · 上限N   │
├──────────────────────────────┬────────────────────────────┤
│ 拖拽 / 点击选图区            │ 整理入库信息               │
│                              │ 类别 / 标签【待契约】        │
│ 校验失败提示列表             │ 已选 N 张 / 总大小          │
│                              │ [开始入库] [取消]           │
│ 待入库队列（缩略图卡）       │ 本批进度  done/total        │
│  · 文件名 · 大小 · 状态      │ ▓▓▓░░ 成功 a · 失败 b       │
└──────────────────────────────┴────────────────────────────┘
┌─ 本批结果列表 ───────────────────────────────────────────┐
│ Tab: 全部 | 成功 | 失败                                    │
│ 行: 缩略图 | 文件名 | 状态 | 耗时 | pointId/错误 | [重试] │
└──────────────────────────────────────────────────────────┘
```

### 3.2 选图 / 目录

| 步骤 | 行为 | 验收要点 |
| --- | --- | --- |
| 选多图 | 按钮或拖拽；复用 `ImageFilePicker` / `mg-dropzone` | 与现 import 一致；非法格式 / 超大 / 重复给出行级原因 |
| 选目录 | 浏览器支持时用目录选择（对齐下载侧 File System Access 能力探测）；不支持则隐藏或禁用并文案说明 | **不得**伪装成功；不支持环境仍可用多选文件 |
| 上限 | 现 import 草稿上限 100；批量搜 30 | 入库上限 N **待契约**（建议先沿用 100 或与后端批大小对齐，文档写死前以契约为准） |
| 清空 / 移除 | 未开始或已结束可清空；进行中禁止改队列（对齐 BatchSearchPanel） | 进行中移除按钮 disabled |

### 3.3 进度与控制

| 行为 | 说明 |
| --- | --- |
| 开始入库 | 调用 `POST /index`（单张循环或批量端点 **待契约**） |
| 进度 | 展示 `已完成/总数` + `<progress>` + 成功数 / 失败数（气质对齐 `batch-progress`） |
| 取消 | 中止未发出或可 abort 的请求；已成功项保留；进行中项标「已取消，可继续」 |
| 重试 | 「重试失败」仅针对 error（及可选 cancelled）；成功项不重传 |
| 离开页 | 进行中需确认或阻止（实现可选）；草稿不持久化与现壳一致，除非契约要求任务落库 |

### 3.4 成功 / 失败列表

| 字段（UI） | 来源 |
| --- | --- |
| 文件名 / 预览 | 本地 File + object URL |
| 状态 | pending / running / success / error / cancelled |
| 成功标识 | **待契约**：`point_id` / `image_id` / `id` |
| 失败原因 | API `message` / `detail` 或前端校验文案 |
| 耗时 | 前端计时（秒，一位小数，对齐批量搜） |

成功摘要可链到「图片数据」页（本轮可仅文案「已入库」，不强制打开库）。

### 3.5 入库空 / 加载 / 错误（摘要）

详见 §6 验收卡。入库页默认空态沿用：「选好的图片会在这里显示」；无任务时 tasks 页保持「暂无入库任务」。

---

## 4. 搜图结果 · 交互说明

### 4.1 与现有批量搜气质对齐

继续使用 `App.tsx` 右侧：

- 工具栏：结果数徽章、耗时、关键词筛选、类别、排序、网格/列表  
- 选择条与批量下载（若自建结果含可下载 URL；无 URL 则禁用下载并提示 **待契约**）  
- 卡片：`ResultImage` + `Score` + 标题 + meta  

**增量**：每条结果增加 **来源标签**（若 API 提供）。

### 4.2 线框（结果卡）

```
┌──────────────┐
│ ☑  [缩略图]  │
│   92.3% 相似 │
│   ┌自建┐     │  ← 或 ┌FlyB 兜底┐
└──────────────┘
  标题标题…
  类别 / 站点     ⬇
```

列表模式在「来源」列展示同一徽章（现表头已有「来源」列，今日多为 `siteName`；**区分「站点来源」与「检索通道来源」**，见下）。

### 4.3 来源标签规则

| 展示文案 | 条件 | 字段 |
| --- | --- | --- |
| `自建` | 命中自建向量库 | **待契约** 建议 `source: "local" \| "self" \| "index"` |
| `FlyB 兜底` | 自建无命中或不足时走外搜 | **待契约** 建议 `source: "flyb" \| "global"` |
| （不展示） | 字段缺失 | 不虚构标签；可在工具栏用 `response.source_mode` 总述 **待契约** |

混排时：自建结果优先排序 **待契约**（若后端已排好，前端保持默认序）。

### 4.4 查询入口（MVP）

两种实现择一（前端架构定，设计约束如下）：

**A. 推荐**：在现有「单张搜索」上增加「搜索目标」分段：`自建库` | `现网 Global（旧）`；选自建库时改调 `POST /search`。  
**B.** 独立轻量入口（仍在 search 页 query-panel 内），结果仍写入同一 `results-panel`。

批量搜 MVP：**首轮可不做自建批量**（Out of scope 可勾）；若做，复用 `BatchSearchPanel` 仅换 API。

### 4.5 自建有结果时

- `items.length > 0` → 正常网格/列表  
- 展示相似度（0–1 → 百分比）；缺失则「未返回分数」（现有 `Score`）  
- `traceId` 若有则底部展示（现有）

### 4.6 搜图空 / 加载 / 错误

沿用现有文案气质，针对自建微调，见 §6。

---

## 5. API 字段表（已知 + 待契约）

> 终稿以「数据规范」为准。路径是否统一前缀 `/api`、snake_case vs camelCase **待契约**。下表用建议名，实现前必须与终稿对齐。

### 5.1 `POST /index`（入库）

**请求（建议）**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File (multipart) | 与 url 二选一 | 图片二进制；字段名 **待契约**（现网搜图用 `file`） |
| `url` / `img_url` | string | 与 file 二选一 | HTTP(S) 图地址；**待契约** |
| `category` | string | 否 | 与 import 侧栏类别对齐；**待契约** |
| `tags` / `label` / `model` | string / string[] | 否 | 元数据；**待契约** |
| `client_request_id` | string | 否 | 幂等 / 追踪；**待契约** |

**批量形态**：单请求多文件 vs 前端逐张 **待契约**。UI 按「队列逐项」设计，两种后端都能接。

**响应（建议）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` / `point_id` / `image_id` | string | 入库主键；**待契约** 三选一写终稿 |
| `status` | string | 如 `ok` / `duplicate` / `rejected`；**待契约** |
| `message` | string | 可读说明 |
| `trace_id` | string | 可选 |
| `duplicate_of` | string | 若去重；**待契约** |

**错误**：HTTP 4xx/5xx；body `detail` / `message`（对齐现 `readableApiError`）。413/503/504 文案可复用搜图侧。

### 5.2 `POST /search`（自建检索）

**请求（建议）**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File | 与 url 二选一 | 同现网 |
| `url` / `img_url` | string | 与 file 二选一 | **待契约** 命名 |
| `top_k` | number | 否 | 默认 **待契约**（前端可用 pageSize=12 做本地分页） |
| `threshold` | number | 否 | 拒识阈值；**待契约** |
| `fallback_flyb` | boolean | 否 | 是否允许 FlyB 兜底；**待契约** |

**响应 item（对齐现 `SearchImage`，便于复用结果卡）**

| 字段 | 与现前端 | 说明 |
| --- | --- | --- |
| `id` | `SearchImage.id` | 稳定 id；自建应用库内 id，勿仅用数组下标（现网用 `image-${index}`，自建应升级） |
| `title` | 有 | 文件名或元数据名 |
| `image_url` / `thumbnail_url` | 有 | 可展图；本地文件服务 URL **待契约** |
| `similarity` | 有 | [-1,1] 或 [0,1] **待契约**（现 UI 按 0–1 显示百分比） |
| `category` | 有 | 可选 |
| `page_url` / `site_name` / `snippets` / `width` / `height` | 有 | FlyB 项更常见；自建可空 |
| **`source`** | **新增** | **`local` \| `flyb`（名待契约）** → UI「自建」「FlyB 兜底」 |
| `raw` | 有 | 保留原始行便于调试 |

**响应级字段**

| 字段 | 说明 |
| --- | --- |
| `items` / `result` / `results` | 列表容器形态 **待契约**；建议兼容现 `normalizeSearchResponse` |
| `trace_id` | 可选 |
| `message` | 如「已启用 FlyB 兜底」 |
| `source_mode` | 整次：`local_only` / `mixed` / `flyb_fallback`；**待契约** |
| `status` | 如 `upstream_empty_response` 同类；**待契约** |

### 5.3 已知可对齐的现网约定（来自 frontend README）

- 文件搜：`multipart` 字段 `file`  
- URL 搜：JSON `img_url`  
- 分数缺失不显示虚构 0 分  
- Global 候选 ≠ 已入库图  

---

## 6. 验收标准（可勾选）

### 6.1 入库面板

- [ ] 可通过「选择图片」或拖拽加入队列；非法项有明确原因且不进入队列  
- [ ] （若做目录）支持目录多选图片；不支持的浏览器有明确禁用/隐藏说明，无报错中断  
- [ ] 展示已选数量、总大小；超过上限时拒绝多余文件并提示  
- [ ] 点击「开始入库」后出现总进度（done/total）与成功/失败计数  
- [ ] 进行中可取消；取消后已成功项保留，未完成可继续或重试  
- [ ] 成功列表可见稳定 id（字段名以契约为准）  
- [ ] 失败列表可见原因；支持「仅重试失败」  
- [ ] 进行中不可清空队列、不可改 API 地址（或改地址时自动停止并提示，对齐批量搜）  
- [ ] 不使用假进度 / 假成功数据  

### 6.2 搜图结果

- [ ] 自建 `POST /search` 返回非空时，结果进入现有 `results-panel` 网格/列表  
- [ ] 相似度、标题、缩略图展示与现卡一致；图挂了走现有回退  
- [ ] API 提供 `source` 时，卡片与列表显示「自建」或「FlyB 兜底」徽章  
- [ ] API 未提供 `source` 时不展示伪标签，页面不崩  
- [ ] 筛选 / 排序 / 分页仅作用于本批结果（与现行为一致）  
- [ ] 详情弹窗仍可用；无 pageUrl 时隐藏外链按钮  

### 6.3 空态

- [ ] 入库：队列为空时显示引导空态（非错误样式）  
- [ ] 任务页：无任务时「暂无入库任务」类空态 + 去准备图片 CTA  
- [ ] 搜图：未搜索过显示「等待查询图片」三步引导（现 `EmptyResults`）  
- [ ] 搜图：已搜索 0 条显示「暂未找到相关图片」；有数据但筛空显示「没有符合筛选条件的结果」+ 清除筛选  

### 6.4 加载态

- [ ] 入库进行中：进度区 + 当前 running 项有 spinner/状态文案；`aria-live` 友好  
- [ ] 搜图等待：现有 `loading-state`（文案可改为「正在自建库中检索…」）+ skeleton  
- [ ] 加载期间主操作按钮防重复提交（disabled 或 in-flight）  

### 6.5 错误态

- [ ] 网络不可达：可读中文错误，可重试  
- [ ] HTTP 4xx/5xx：展示后端 message/detail 或统一映射（413/503/504）  
- [ ] 超时：入库/搜图均有超时上限（建议对齐现 120s）与文案  
- [ ] 单项失败不影响队列中其他项继续（若采用逐张模式）  
- [ ] 全局 `message-banner` 可关闭；不遮挡主操作  

### 6.6 回归（壳不被破坏）

- [ ] 现网 Global 单张/批量搜、下载、设置、帮助仍可用  
- [ ] 管理页未接接口的部分仍显示「等待接入」，无假数据  
- [ ] 视觉：主色 / 卡片圆角 / 间距与现壳一致，无第三套 UI  

---

## 7. 角色与协作

| 角色 | 职责 |
| --- | --- |
| 前端设计（本文） | 交互说明、线框、验收标准、待契约标注 |
| 前端架构 | 按本文 + 数据规范终稿实现；选型 A/B 查询入口；接 `POST /index` `/search` |
| 测试 bot | 按 §6 checkbox 验收；出缺陷单 |
| boboji（PM） | 排期、催数据规范终稿、跨角色对齐 |
| 数据规范 / 后端 | 冻结字段名、路径、批量语义、source 枚举 |

---

## 8. 前端架构注意（≤3）

1. **契约未冻结**：`id`/`point_id`、`source` 枚举、`/api` 前缀、相似度值域、入库批量形态均为 **待契约**；建议先做 thin adapter（类似 `normalizeSearchResponse`），避免结果卡绑死字段名。  
2. **双搜并存**：现网 `searchImage_*` 与 `POST /search` 并存时，应用显式「搜索目标」切换，避免静默改调用导致批量搜/单张行为不一致。  
3. **来源 vs 站点**：列表列「来源」今日是 `siteName`；通道标签（自建/FlyB）需另加徽章或列，避免把 hostname 当成 FlyB/自建。

---

## 9. 附录 · 关键文件清单（只读摸底）

```
frontend/package.json
frontend/src/App.tsx
frontend/src/types.ts
frontend/src/styles.css
frontend/src/lib/search.ts
frontend/src/lib/batch-search.ts
frontend/src/components/BatchSearchPanel.tsx
frontend/src/components/BatchSearchPanel.css
frontend/src/components/ManagementViews.tsx
frontend/src/components/ManagementViews.css
frontend/src/components/ImageFilePicker.tsx
frontend/src/components/Dialog.tsx
frontend/README.md
```

（探索日期：2026-10-27；未修改任何业务实现代码。）
