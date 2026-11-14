# Image-Embedding 测试报告

日期：2026-11-13。测试对象：当前工作区，重点覆盖类别管理和类别选择器的近期改动，同时运行后端完整离线测试。前端浏览器检查时的提交为 `eb5691de0efd9690390e932b1069e48bcb187af9`。

本次未修改业务源码。新增内容为本目录下的测试日志、最小复现脚本和截图。

## 已确认问题

### 1. P1：旧测试字段阻断前端构建

- 位置：`frontend/src/features/category-config/tableModel.test.ts:64-66`；类型定义见 `frontend/src/features/category-config/tableModel.ts:9`。
- `CategoryTableFilter.domainId` 已更名为 `scopeId`，测试仍传入 `domainId`。
- `npm.cmd test`：275 项中 274 项通过，1 项失败；域筛选得到 `[1, 7, 8]`，断言期望 `[7, 8]`。
- `npm.cmd run build`：三处 `TS2353`，因此无法产出新的生产构建。`tsconfig.json` 的 `include` 包含 `src`，测试文件也参与类型检查。
- 判断：这是未同步测试导致的构建问题；不能据此认定页面实际使用的 `scopeId` 筛选失效。
- 修复方向：将这三处测试输入同步为 `scopeId`，补上分组范围筛选的行为覆盖，然后重新运行单测和构建。

### 2. P2：中文输入法确认候选时误选类别

- 位置：`frontend/src/features/category-picker/CategorySearch.tsx:230-249`，尤其 Enter 分支第 245 行。
- 复现：打开“新建类别”的上级类别搜索框，输入“菜”；中文输入法用 Enter 确认候选，键盘事件 `isComposing=true`。
- 预期：确认输入法候选，继续保留搜索框和候选列表。
- 实际：组件把 Enter 当成选择当前类别，“菜品”被误选；输入从“菜”变为空，编码从空变为 `dish.`，下拉关闭。
- 已在本机 Chromium、完全 mock API 的真实页面中复现，未写入真实后端。
- 原因：键盘处理没有排除输入法组合输入事件。
- 修复方向：组合输入期间跳过类别导航、Enter 选择和 Escape 关闭处理；增加中文输入法事件回归用例。
- 证据：`bugcheck-20261113-ime.json`、`bugcheck-20261113-ime.png`、`bugcheck-20261113-ime.mjs`。事件级最小复现另见 `bugcheck-20261113-category-picker-probe.cjs`。

### 3. P2：重生成向量与删除并发会留下孤儿向量

- 位置：`backend/app/services/retrieval/reembed_image.py:71-82`。HTTP 入口见 `backend/app/api/services/images.py:115-145`。
- 确定性复现顺序：
  1. reembed 读取图片记录和原图到内存，开始推理。
  2. 另一请求删除图片，并完成向量、文件及删除队列的清理。
  3. reembed 推理完成，重新 upsert 已删除图片的向量。
  4. 写入 `image_embeddings` 时因图片已不存在而失败，返回 `INDEX_POSTGRES_FAILED`，但没有补偿删除刚写入的向量。
- 实际结果：图片不存在、ledger 为空、删除队列为空，向量点仍存在。再执行真实 `ReconcileUseCase.run_once()`，扫描 0 条，该点仍存在。
- HTTP 的共享写入门只协调模型切换，没有阻止同图 DELETE 并发。
- 影响：留下不能被现有对账逻辑发现的孤儿向量。搜索会再用 PostgreSQL 过滤，因此不会直接让已删图片重新显示，但孤儿点可能占用 Top-K 召回名额。
- 验证方式：真实 use case 与删除 worker、对账 use case，加内存 fake 适配器；不连接真实 PG/Qdrant/OSS。尚未用真实数据库做并发集成验证。
- 修复方向：协调同一图片的 reembed/删除操作，并在向量写入后图片已删除或 ledger 写入失败时做可靠补偿。`build_embeddings.py` 已有类似补偿逻辑可参考。
- 证据：`bugcheck-20261113-reembed-delete-race.py`、`bugcheck-20261113-reembed-delete-race.json`。

### 4. P2：下载端到端测试的 API 拦截规则误伤前端模块

- 位置：`frontend/e2e/downloads.spec.ts:43`。
- `page.route('**/api/**', ...)` 同时匹配 Vite 开发模块 `/src/shared/api/*`，将其返回为 404，导致页面无法加载、用例超时。
- 这是测试夹具问题，不能将其判定为下载功能本身损坏。
- 修复方向：按 `new URL(request.url()).pathname.startsWith('/api/')` 判断后端 API，避免拦截模块路径。类别管理 e2e 的 `open()` 已采用该方式。
- 类别管理 e2e 另有旧选择器未同步：原生 select 已换为 combobox，“类别域筛选”已改为“范围筛选”；旧 `selectOption`/标签查找因此失败。
- 上传/编辑等测试仍查找 `role=option`，组件已使用 `role=treeitem`；图库大数据用例仍断言“找到 550 个”，当前默认隐藏无图类别，实际显示“找到 407 个，另有 143 个被上面的选项隐藏”。这些失败同样不能直接当作业务功能损坏。
- `enum-labels.spec.ts` 的 3 个超时缺少 `/api/scenes` mock；当前默认自建库搜索依赖场景，开始搜索按钮因此处于禁用状态。

### 5. P3：类别组件禁用时仍可通过列表回调选择

- 位置：`frontend/src/features/category-picker/CategorySearch.tsx:202-220`。
- 在 `alwaysOpen=true, disabled=true` 的组件输入下，列表仍渲染并响应点击，`choose()` / `pick()` 没有禁用判断，仍触发 `onPick`；只有搜索 input 被禁用。
- 事件级最小复现结果为 `disabledClick.picked=[1]`，见 `bugcheck-20261113-category-picker-probe.cjs`。
- 此项已验证组件事件行为，尚未在真实页面中复现提交过程中修改选择的业务后果。修复时应在选择入口统一检查禁用状态，并保证可点击列表元素同步禁用。

## 自动检查

| 检查 | 结果 |
| --- | --- |
| 后端 `python -m unittest discover -s tests -v` | 585 项：526 通过、59 跳过、0 失败 |
| 脚本 `python -m unittest discover -s scripts -p 'test_*.py' -v` | 34 项全部通过 |
| 前端 `npm.cmd test` | 275 项：274 通过、1 失败 |
| 前端 `npm.cmd run build` | 失败，3 处 TS2353，见问题 1 |
| 类别管理浏览器用例 | 19 项：8 通过、11 失败；失败集中于旧选择器 |
| 下载浏览器用例 | 连续 5 项因问题 4 超时后中止该组 |
| 其余选定浏览器回归 | 49 项：41 通过、8 失败；日志见 `bugcheck-20261113-frontend-e2e.json` |

后端使用 `D:/99-tools/anaconda/envs/onnxgpu/python.exe`（Python 3.11），显式清空 `TEST_DATABASE_URL`。测试 Nacos 行为使用测试自身的 mock；全局设置 `NACOS_ENABLED=0` 会改变相关断言，因此最终后端运行没有设置此开关。日志：`bugcheck-20261113-backend.log`、`bugcheck-20261113-scripts.log`。前端单测和构建日志分别为 `bugcheck-20261113-frontend-unit.log`、`bugcheck-20261113-frontend-build.log`。

## 测试边界

- 59 项跳过测试需要可丢弃的 PostgreSQL 测试数据库；没有对现有业务库运行会重建 schema 的用例。
- 浏览器使用本机 Chromium 和 mock API。仓库默认 Playwright 指定 Chrome，本机缺少 Chrome，使用本目录临时配置覆盖浏览器，未修改仓库配置。
- 浏览器全套没有跑完，也没有全量通过；反复遇到同类夹具超时的测试组已中止，补充回归使用 12 秒单用例超时、最多 12 个失败的限制。测试夹具和旧选择器需要先同步。各轮测试的集合与配置不同，不宜将计数合并成一次完整通过率。
- 未做真实 ONNX 模型、GPU、PG/Qdrant/OSS/Nacos 的全链路或压力测试；现有单测通过不代表这些部署链路已经验证。

## 复现命令

在仓库根目录执行后端竞态最小复现：

```powershell
& 'D:/99-tools/anaconda/envs/onnxgpu/python.exe' '.qa/bugcheck-20261113-reembed-delete-race.py'
```

在仓库根目录执行类别事件最小复现：

```powershell
node .qa/bugcheck-20261113-category-picker-probe.cjs
```

IME 浏览器脚本需要本机前端开发服务运行，详见 `bugcheck-20261113-ime.mjs`。
