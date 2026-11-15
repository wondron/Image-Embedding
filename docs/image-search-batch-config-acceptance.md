# 多图搜索与配置传输验收

日期：2026-11-15（America/Denver）。对应[契约 §6.7 多图搜索](image-search-pg-v01-data-contract.md#batch-search)、[§6.8 配置下载与上传](image-search-pg-v01-data-contract.md#config-transfer)，以及本轮搜索结果卡、图库卡片调整。

这些步骤已执行，结果、修复、环境限制和清理证据见[本轮验收记录](image-search-batch-config-verification-2026-11-15.md)。

自动化检查使用内存仓储、模拟检索和受控 HTTP 响应；真实 PostgreSQL、Qdrant、模型、对象存储和 Global 的联调按下文单独执行。自动化通过不代表真实环境验收完成。

## 1. 自动化检查

以下为 PowerShell 命令。从仓库根目录开始，后端使用已安装 `backend/requirements.txt` 依赖的 Python 环境。每条自动化命令都应成功退出；失败时先处理失败，不能把最后一条成功当作整轮通过。

```powershell
Set-Location backend
python -m unittest discover -s tests -p test_batch_search_http.py -v
python -m unittest discover -s tests -p test_config_import.py -v
python -m unittest discover -s tests -p test_retrieval_http.py -v
python -m unittest discover -s tests -p test_categories_http.py -v
python -m unittest discover -s tests -p test_scenes_http.py -v
python -m unittest discover -s tests -p test_search_cards.py -v
python -m unittest discover -s tests -p test_retrieval_stack.py -v
Set-Location ../frontend
npm.cmd test
npm.cmd run build
```

前端依赖未安装时先运行 `npm.cmd ci`。浏览器回归默认使用已安装的 Google Chrome；本机只有 Edge 时可指定浏览器通道：

```powershell
# 在 frontend 目录执行；安装 Chrome 时可省略这个环境变量
$env:PLAYWRIGHT_BROWSER_CHANNEL = 'msedge'
# 本轮专项 + 下载与图片搜索回归
npm.cmd run test:e2e -- e2e/config-transfer-search.spec.ts e2e/downloads.spec.ts e2e/search-images.spec.ts
# 本轮完整相关回归（七个文件）
npm.cmd run test:e2e -- e2e/config-transfer-search.spec.ts e2e/downloads.spec.ts e2e/search-images.spec.ts e2e/category-config.spec.ts e2e/library-annotation.spec.ts e2e/scene-sync.spec.ts e2e/workbench.spec.ts
# 全量浏览器回归
npm.cmd run test:e2e
```

Playwright 自动启动或复用 `http://127.0.0.1:5173`。E2E 的 `/api` 响应由测试控制；失败截图和 trace 在 `frontend/test-results/`。构建产物在 `frontend/dist/`。

## 2. 真实联调准备

1. 使用测试环境的 PostgreSQL、Qdrant、对象存储和匹配当前 collection 的 embedding 模型。沿用项目的配置和初始化流程；本轮没有新增数据库迁移，已有数据库仍需处于当前 Alembic head。
2. 在后端启动终端设置 `$env:NACOS_ENV = 'test'`，检查 `/api/version` 的 `app_env` 及 `/api/config_info` 的配置来源。若使用本地 YAML，设置 `$env:NACOS_ENABLED = '0'`，并确认本地 YAML 指向测试依赖。
3. 在 `backend` 目录运行 `python main.py`；使用镜像时更新为包含本轮代码的镜像并重启。通过 `GET /api/health` 确认检索依赖可用；Swagger `/docs` 中应出现两个 batch 和两个 import 接口。
4. 在另一个终端进入 `frontend`，运行 `npm.cmd run dev`，打开 `http://localhost:5173`。开发代理默认转发到 `http://127.0.0.1:8320`；自定义端口按前端 README 配置。静态部署和 `npm run preview` 需要另外配置 `/api` 代理。
5. 准备两张可解码图片（`a.jpg` 在测试图库中有已知 ID 的匹配），以及空文件、非图片文件。先下载完整类别树、场景配置备份，备份命令退出失败时先修复、不要继续导入；后续写入及 prune 用专用测试节点、测试场景。
6. 使用 fake-IP 代理的本机若把公网域名解析到保留地址，默认下载策略会返回 `FORBIDDEN_HOST`。URL 成功链路需使用与本机代理匹配的 `IMAGE_DOWNLOAD_CONFIG.trusted_networks`；本轮仅对临时验收进程设定了实际解析地址，详见验收记录。

## 3. 多图搜索接口

在放有 `a.jpg`、`b.jpg` 的测试目录运行，PowerShell 显式使用 `curl.exe`：

```powershell
$api = 'http://127.0.0.1:8320'
curl.exe -i -sS -X POST "$api/api/search/batch/files" -H 'X-Trace-Id: acceptance-batch-001' -F 'files=@a.jpg' -F 'files=@b.jpg' -F 'scene=general' -F 'top_k=5' -F 'score_threshold=0'
```

预期 HTTP 200，`total=2`、`succeeded=2`、`failed=0`，且 `1 <= matched <= succeeded`。`results` 与上传顺序一致，`index` 从 0 开始；首项应包含 `a.jpg` 对应的已知图库 ID，并与相同参数的单张自建库检索对比。`source=empty`、`items=[]` 是搜索成功但没有匹配，仍计入 `succeeded`。`-i` 显示状态与响应头，响应体 `trace_id` 应与 `X-Trace-Id` 响应头一致。

| 检查 | 操作 | 预期 |
|---|---|---|
| 部分失败 | API 上传中混入空文件、非图片文件 | 仍返回 200；对应项为 `ok=false`，有 `EMPTY_FILE` / `SEARCH_BAD_IMAGE` 等错误，其余继续；`succeeded + failed = total` |
| 共享参数 | 分别传未知场景、`top_k=0`、`score_threshold=0.2` | 整个请求 422；场景和阈值分别为 `SEARCH_UNKNOWN_SCENE`、`SEARCH_BAD_SCORE_THRESHOLD`；不开始逐图读取与检索 |
| 数量边界 | 分别请求 1、30、31 张；或 JSON `image_urls=[]` | 1–30 张合法；31 张和空列表 422 |
| URL 输入 | 向 `/api/search/batch/urls` 发送 `image_urls` 数组，混入无效 URL / 无法下载链接 | 输入顺序保留，下载错误落在单项；未知 JSON 字段使整个请求 422 |
| 同一服务故障 | 在隔离测试环境令所有图遇到同一 Qdrant/PG 故障 | 整个请求返回对应 502；检索栈未就绪为 `NOT_READY` 503；不调用 Global |

URL 示例保存为 UTF-8 的 `batch-urls.json`，将地址换成可公开访问的测试图片，再提交：

```json
{"image_urls":["https://example.com/a.jpg","https://example.com/b.jpg"],"scene":"general","top_k":5,"score_threshold":0}
```

```powershell
curl.exe -i -sS -X POST "$api/api/search/batch/urls" -H 'Content-Type: application/json' --data-binary '@batch-urls.json'
```

## 4. 类别树下载与导入

先验证直接 GET 的平铺文件可以往返，再验证页面下载的嵌套文件。

```powershell
curl.exe --fail -sS "$api/api/categories" -o categories.json
curl.exe -i -sS -X POST "$api/api/categories/import" -H 'Content-Type: application/json' --data-binary '@categories.json'
```

未修改文件的预期：`dry_run=true`，没有 create / update / delete，`summary.error=0`，数据库不变。页面「食材管理 → 下载类别树」分别下载 JSON、TXT、SVG、PNG、ZIP；确认 JSON 的 `schema_version=1`、`kind=category_tree`，即使设置筛选、折叠或隐藏停用类别，文件也包含全部类别。TXT / SVG / PNG 层级一致、中文可读；ZIP 包含数据、拓扑图及说明，PNG 无法生成时其余文件仍保留并有提示。

在测试库已存在 `ingredient` 根节点、尚不存在 `ingredient.acceptance_demo` 的前提下，保存以下文件为 `category-change.json`；重复验收时换一个尚未使用的末级编码。

```json
{"schema_version":1,"kind":"category_tree","categories":[{"code":"ingredient","children":[{"code":"ingredient.acceptance_demo","name":"验收样例","sort_order":5,"is_active":false}]}]}
```

```powershell
# 预览：将新建 1 项，不能在 GET 中看到新节点
curl.exe -i -sS -X POST "$api/api/categories/import" -H 'Content-Type: application/json' --data-binary '@category-change.json'
# 确认写入后，GET 应能查到测试节点
curl.exe -i -sS -X POST "$api/api/categories/import?dry_run=false" -H 'Content-Type: application/json' --data-binary '@category-change.json'
# 同一份文件再提交：新建 / 更新均为 0，不产生重复节点
curl.exe -i -sS -X POST "$api/api/categories/import?dry_run=false" -H 'Content-Type: application/json' --data-binary '@category-change.json'
```

再检查以下分支：

- 修改已有测试节点的 `sort_order` 或 `names`，预览显示 before / after；只给出的字段被更新。
- 将一个非法节点与合法节点放在同一份文件中：非法项进入 `rows`，合法项照常导入；判断部分成功需看 `summary.error`，不能只看顶层 `ok`。
- 已有节点换父级、重复编码、父节点缺失或父级循环得到逐项错误；类别不移动、不删、不通过改编码完成重命名。平铺文件仅保留子节点时，保留上级节点或显式提供 `parent_code`。
- 菜系重算另用一个启用类别，在它或其子树下先入库测试图，并配置匹配该菜系的测试场景规则。修改 `cuisines` 前后核对 `scene_sync.images > 0`、预期变化图片的归属及搜索过滤；有 `pending` 时核对后台对账后的向量状态。上面的停用空节点不能代替这个用例。
- 页面上传空文件、坏 JSON、超过 20 MiB 的文件时在本地提示错误；选择合法文件后先显示预览，取消不写入，点「确认导入」才写入并刷新树。

## 5. 场景配置往返、prune 与同步

```powershell
curl.exe --fail -sS "$api/api/scenes" -o scenes.json
curl.exe -i -sS -X POST "$api/api/scenes/import" -H 'Content-Type: application/json' --data-binary '@scenes.json'
```

原样文件预览应无变更、无错误。再通过页面「场景管理 → 下载配置」下载，确认 `kind=scene_config`，场景类别及规则齐全。类别规则按类别编码关联，换数据库时先导入类别树；加热组需在目标库中预先存在。

| 检查 | 操作 | 预期 |
|---|---|---|
| 新建与幂等 | 用测试场景文件含一个 label 和一条已有测试类别的规则；预览后导入、再导入 | 先看到三类对象的变更；确认后写入；再次提交不重复创建 |
| 部分字段 | 不传 `labels` / `rules`，只修改测试场景名称 | 保留原列表，即使 `prune=true` 也不处理省略的列表 |
| 默认场景保护 | 预览导入 `general` 的 `is_active=false` 或 `include_all=false` | 该项 `SCENE_PROTECTED`；其他合法项按报告处理 |
| prune 预览 | 测试场景已有规则及 label；文件显式传 `rules=[]`、`labels=[]`，勾选删除开关 | 重新请求预览，列出将删的规则和 label，尚未写入 |
| prune 写入 | 确认上一项导入 | 先删规则再删 label；不删场景，不改文件未列出的场景 |
| 引用保护 | 另用仍有规则引用 label 的测试场景，文件只传 `labels=[]`、省略 `rules`，预览 `prune=true` | 规则列表保留，被引用 label 的删除项报 `SCENE_IN_USE` |
| 成员同步 | 导入改变成员关系的规则，观察 `sync_required` | 提示「去同步」；执行页面全量同步直到完成，再核对已入库图片场景及搜索过滤；仅改 label 映射不要求全量同步 |
| 导入失败 | 在受控故障环境让写入中途失败，再恢复并上传原文件 | 已提交条目保留；页面刷新目录；重新导入时已完成项无变化，继续剩余内容 |

API 确认写入使用 `POST /api/scenes/import?dry_run=false&prune=false`；需要 prune 时先用 `dry_run=true&prune=true` 看报告，再明确以 `dry_run=false&prune=true` 提交。API 全量同步为 `POST /api/scenes/sync`，首次必须带 JSON 请求体 `{}`，后续批次带 `{"after":"上一批 next_after 的值"}`，直到 `done=true`；不能只执行一批就视为完成。

## 6. 前端搜索与卡片回归

1. 添加至少 6 张查询图，选择自建库：网络面板应看到 `/api/search/batch/files` 按最多 5 张分组，结果与队列逐图对应；API 坏图分支用上文直接上传验证，前端会先拒绝不支持的文件类型。
2. 进行中点「停止」：立即停止前端等待，保留已完成结果；服务端已开始的工作可能继续。继续/重试只处理未完成项；改参数后点「全部重新搜索」才替换全部旧结果。
3. 切到现网 Global、业务搜图：分别走 `/api/search/global/file`、`/api/search/auto/file`。业务搜图自建命中时不兜底，自建空时按开关兜底；返回数量对两个通道都生效。
4. 搜索网格和列表核对类别/标题、场景标签、标签、来源；只有输出加热信息的场景显示对应行。检查选图、详情原图、单张/批量下载仍可用。
5. 图库分别查看大图、小图、列表，核对标注状态、描述/ID、场景、标签以及待同步/没有向量提示；检查类别筛选、分页、跨页选择。窄屏复查按钮与对话框可操作、无水平溢出。

记录真实验收时的代码版本、后端配置环境、浏览器、输入文件和预期/实际结果；发生错误时保留响应里的 `trace_id`。自动化通过和上述真实联调均完成后，再标记验收完成。
