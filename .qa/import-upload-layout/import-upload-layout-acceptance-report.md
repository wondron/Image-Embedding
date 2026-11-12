# 上传与导入页布局优化 · 独立验收报告（I1–I9）

- **总体判定：ACCEPTED**（PASS 9 / FAIL 0 / SKIP 0）
- 记录时间：2026-11-12 01:48:21 America/Denver (MST, UTC-7)
- 仓库：`C:\Users\47202\PROJECT\Image-Embedding`（machineId `fada9197-1d47-44f2-8e45-fdb157d8e181`）
- 规格：`/workspace/deliverables/import-upload-layout-refinement.md`
- 实施交接：`/workspace/deliverables/import-upload-layout-implementation.md`

判定全部来自 wondron 本机 **有头 Chromium 真实窗口** 的几何测量与截图（Playwright 1.64.1 / chromium-1248），不是实现方源码映射。未使用 box MCP Playwright 打 loopback。

## 九点判定

| ID | 项 | 判定 | 窗口 | 摘要 |
|----|----|------|------|------|
| I1 | 宽屏空队列同一行 | **PASS** | 1440×1000 | 两选择按钮与拖拽区同一行；格式说明与「自建入库已接入」在上方 meta。副文案为「或使用左侧…」，无「顶部的」。 |
| I2 | 宽屏加长队列只滚左列 | **PASS** | 1440×1000 | 左列独立滚到底（scrollTop 4666 / 5540）；main overflow hidden、窗口不滚；右栏 sticky，标题与「开始入库」完整可见且可点；表单区单独滚动时开始入库不移动。 |
| I3 | 人为让 main 成为滚动容器 | **PASS** | 1440×1000 | `page.addStyleTag` 临时让 main 滚动（scrollTop 4666）；右栏仍 sticky + max-height 916px（100dvh−56−28，不是 100%）；开始入库仍在 main 可见区。测完已去掉试验样式，未写入工作树。 |
| I4 | 入库进行中 | **PASS**（证据有边界） | 1440×1000 | 无隔离后端、未 POST `/api/index`。用 React fiber 注入 `indexing`。meta 文案变为「入库进行中」且仍在 meta；拖拽区 `is-disabled`，`pointer-events: none`，选择图片 disabled。 |
| I5 | ≤860px 底栏 | **PASS** | 860×1000 | 长队列后底栏仍贴在 main 可见区底部；表单可滚，开始入库钉在面板底。7.5rem 核对见下。无新抽屉/Dialog。 |
| I6 | 极窄折行 | **PASS** | 580×900 与补测 400×800 | 580px 时一行仍放得下（约 523px > 438px），两按钮与拖拽区同行，状态在 meta。400px 时仅拖拽区换到下一行，两按钮仍并排，状态不回按钮行。 |
| I7 | 窄高 | **PASS** | 1440×600 | 剩余主区 544px（<560），表单可视 45px（<160）可滚；开始入库不被裁切；html/窗口不滚动，无双滚动条把右栏带出视窗。拖拽副文案按 644px 断点隐藏。 |
| I8 | 业务回归 | **PASS**（证据有边界） | 1440×1000 | 本地临时 PNG：拖拽入队、选择图片入队、选择目录可用、单独标注入口、开始入库可见、注入后取消入库恢复 meta、跳转任务中心成功。未对业务库做真实入库。Dialog 源码不在本刀，未把焦点回归当作失败。 |
| I9 | 其它页与品牌 | **PASS** | 1440×1000 | `--primary` 仍为 `#2c56a0`，侧栏 200px。图库 / 入库任务 / 搜索未带上 `import-page`；任务页 main 仍为 `overflow: auto`。 |

## 7.5rem 观察（I5，左列 scrollTop=0）

实施用 `7.5rem` 估计 meta + 紧凑 pickup + 间距。根字号 15px，故 7.5rem = **112.5px**。

在 860×1000、左列未滚动时实测：

- meta 高 30.83px，与 pickup 间距 22.67px，pickup 高 56px
- 从 `.mg-page-import` 顶到 pickup 底 = **109.5px**（比 7.5rem 估计仅少 3px）
- 底栏 aside：`position: sticky`，高 / `max-height` **520px**（此时绑定值是 `52dvh`，紧于 `100dvh − 56 − 28 − 7.5rem`）
- 与 pickup **无重叠**（间距 286.5px），未遮挡 pickup，底栏也未过高把开始入库挤出 main

正式套件里第一次 7.5rem 取样发生在左列滚到底之后，pickup 已离开视窗，占用数字不可用。上表以补测 `I5-860-scrolltop0.png` / `supplemental-i5-i6.json` 为准。

## Git / 哈希 / 保护

- **HEAD** `18f3d652ddad846b6815cd478c1206a31574ca85`，与实施方声称一致。提交说明：`修改云端上传配置`，2026-11-12 01:16:48 America/Denver。本刀四文件仍未提交。本验收 **未 commit / push / stash / reset**。
- 四文件 SHA256 与交接完全一致：
  - `App.tsx` `86F4062D822C00B5FA1CAD1F24A12A9692A592A5CE276A5FC8CC2078DEAEFFC4`
  - `ManagementViews.css` `A26CAD0BE1B5265A62145BC8F32FF0165DD02B1BD8C72FF7F3021791EC4639AB`
  - `ManagementViews.tsx` `E40AC93D24419E732DC5B71BBC8C36798C3D78EC88EF018C6DA433167B0BE2D0`
  - `styles.css` `6ADDFCC7F958F4F287F1EC983A8922F02B59D58FC57AD643F0249CC79774348B`
- 并行后端 5 个已改文件与 `Dialog.tsx`、`test_index_concurrency.py` 收尾哈希与交接（或本验收开始时）一致，未回滚、未改写。
- `backend/tests/test_index_repository.py` 在验收开始时已是 `CB2074F6…8200EF`，与交接写后值 `E707C7F7…361D` 不同；收尾仍为该值，不是本验收改的。
- `test_index_pg_concurrency.py` 收尾 `F3F32061…2494`。既有 `.qa`（scene / ingest-tasks / oss-key-prefix 等）未改；本验收只新增 `.qa/import-upload-layout/`。

## 服务与命令

用户已有 Vite 在 `:5173`（PID 11904，`--host 0.0.0.0`），**未切换、未停止、未改其 baseUrl**。本验收在隔离地址启动：

```
cwd: frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:57522
npx vite --host 127.0.0.1 --port 57521 --strictPort
```

PID：wrapper 36720，npx 40848，vite **31188**。`GET http://127.0.0.1:57521/` 返回 200。未改 `config.yaml` / Nacos / `vite.config.ts`。

正式套件（第三次，前两次分别为 `?view=import` 无效与 580px 侧栏离屏，已修正导航方式）：

```
cwd: .qa\import-upload-layout
frontend\node_modules\.bin\playwright.cmd test --config playwright.config.ts
```

退出码 0，**9 passed (17.6s)**，有头窗口。日志：`.qa/import-upload-layout/playwright-run-3.log`。

补测：`playwright.cmd test --config .\remeasure.config.ts`，退出码 0，用于 I5 scrollTop=0 占用与 I6 400px 折行。

说明：`App.initialView()` 只认 `?view=enums|categories|scenes`，`/?view=import` 会落到搜索页。验收改为点击侧栏「上传与导入」进入；≤760px 时先拓宽再点侧栏，然后恢复目标视口。

## 清理

哈希核对完成后，第一次尝试结束自建 Vite 时 wondron 不可达（`Your computer "wondron" is temporarily unreachable`），当时按规则停止、未轮询。

随后于 2026-11-12 01:52:56 America/Denver 再核：PID 36720（cmd / npx）、40848（npx-cli）、31188（vite --port 57521）仍在，57521 由 31188 监听。已只结束这三个进程并复核已退出；57521 不再监听。用户 `:5173` PID 11904 未动，仍在监听。

未自建容器。未改业务源码或生效配置。Playwright 试验样式仅存在于页面并已在用例内移除。

截图、`measurements.json`、`supplemental-i5-i6.json` 与 Playwright 日志在测试成功时已写在：

`C:\Users\47202\PROJECT\Image-Embedding\.qa\import-upload-layout\`

## 报告路径

- 盒内：`/workspace/deliverables/import-upload-layout-acceptance-report.json` 与 `.md`
- C 仓证据目录（测试过程已写入）：`C:\Users\47202\PROJECT\Image-Embedding\.qa\import-upload-layout\`
