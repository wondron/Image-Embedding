# Scene 当前实现独立验收（仅测试）

2026-11-10T10:01:22.760949-07:00 America/Denver (MST)

**结论：后端 scene 链路在明确替身边界内 PASS；前端动态配置/候选缺口仍在，GUI未测，不能称全部开发完成。**

HEAD `08dea28228cad79da67eefb3aadbc6ef55078a42`，前后两阶段相同。初始干净；最终仅新建.qa产物，无tracked应用改动。

## 分层实测数量
|层次|结果|退出码|
|---|---|---|
|BE membership (pure unit)|5/5 PASS|0|
|BE http-mocked (TestClient fake stack)|23/23 PASS|0|
|BE stack (all adapters mocked)|15/15 PASS|0|
|BE qdrant-mocked (unit/fake/in-memory)|11/11 PASS|0|
|BE pg-live (real isolated PostgreSQL; sync Qdrant :memory:)|8/8 PASS|0|
|FE Vitest (非浏览器/mock fetch)|123/123 PASS|0|
|真实FastAPI HTTP +真实PG +实际Qdrant HTTP|95/95明确断言，53 API请求|0|

已有用例共185；真实HTTP的95是独立断言数，不与用例数量混算。

HTTP状态分布：200=29, 201=5, 403=3, 409=3, 422=13。

实际测试目标：127.0.0.1:57045，PID50880；新C源码；PG127.0.0.1:52316/qa_scene_http_0957；Qdrant127.0.0.1:56339，collection qa_scene_http_0957。**这些测试服务均已退出/移除，不是当前可用URL。**

编码器为确定性4维stub，OSS为纯内存MockImageStorage，未上传/镜像写盘。实际路由/服务/仓储/Qdrant客户端和服务器均执行；不是TestClient冒充联调。未验证真实模型、OSS、生产启动/Nacos。

## 六组
### 1. scene list/create/edit/toggle and FE candidates
PASS actual HTTP201 create,200 list/edit/toggle/delete;403 default protection;409 duplicate;422 blank/null/immutable/empty patch
INCOMPLETE: six hard-coded seed options, no discovered dynamic scene client/admin page

### 2. default/aliases/unknown/inactive/dynamic
PASS actual HTTP general defaults (incl blank), normal->general/device->oven; new qa_dynamic accepted; unknown/inactive list/search/index422; GET config includes inactive flag
INCOMPLETE dynamic candidates; mock API defaults/aliases/error tests pass

### 3. index/include_all/manual/rule/image_scenes update
PASS actual multipart index, real PG rule row/nonmaterialized general, hints not authoritative, repository manual add/exclude then actual public sync; manual survives removed rule; include_all ignores existing manual exclusion
Manual operation invoked existing repository directly; no public manual API fabricated, public contract still TBD and UI apiReady=false

### 4. list/search filtering and Qdrant scenes
PASS actual list/search HTTP against actual Qdrant HTTP+PG; raw server point payload checked after index/manual/sync/inactive/include_all/delete; code sets agree
Query encoder is deterministic stub, not semantic image quality test

### 5. heating/labels response contract
PASS actual HTTP dynamic heating scene returns envelope outputs_heating=true, scene_label string, HeatingGroup object and cooked food_state; general omits heating/food keys. PG deepest-active-label test also passes
existing mapper/API units PASS; visual display UNTESTED

### 6. page switching/echo/blank/error/refresh and real FE requests
HTTP blank/default/invalid/echo behaviors covered
static + mocked units only; GUI and real browser traffic NOT TESTED

## 缺口与复现
- SCENE-FE-1（frontend integration/completeness gap）：Backend GET /api/scenes supports dynamic rows, while SceneSelect maps SCENE_SEEDS only; newly created qa_dynamic cannot be chosen by existing UI and disabled seed would remain offered. Static verified; not browser reproduction. Older M6 minimum seed selection is not equivalent to full dynamic scene product completion.
- SCENE-TBD-1（explicit design/product gap）：ManagementViews.tsx:512 ImageLabelPanel apiReady=false; public manual membership API not in current OpenAPI. Repository manual operations tested but not a public endpoint. Not labeled implemented-feature regression.

本轮未发现新增后端scene实现BUG。首次HTTP准备因Qdrant继承代理502阻塞，记录保留attempt1；仅QA进程设置loopback NO_PROXY后95/95通过，未改应用/已有进程。

## 隔离与清理
恢复后inspect两只精确ID确认mounts=[]与tmpfs后复用；所有迁移/测试写仅QA容器。实际HTTP服务PID50880已正常结束，线程停止。两只QA容器均已stop+rm：
- PG 1e150574b29c62252acb5191e05badbb6225ad09abd4502ea1832e8208d05743
- Qdrant 5049d6c23f80b6c2a62b5a78d0a72a213a333ec5e3644ed4423add83c63e82c5
没有自建进程/容器留运行，无待清理项。业务roki容器及既有Vite不动。

## GUI交接（未做）
Only user-machine loopback endpoints were authorized; no confirmed box-reachable GUI path. No tunnel/public exposure. Did not retain speculative servers. Prior backend 127.0.0.1:57045 now stopped; old Vite5199/5299/5399 provenance unconfirmed and untouched.
frontend/vite.config.ts supports VITE_API_PROXY_TARGET in a separately started QA Vite process, no app config edit needed.
- Parent arrange authorized browser running on wondron itself, or separately establish legitimate reachability; do not treat its loopback as box loopback.
- Create fresh isolated QA PG/Qdrant and a separately identified HTTP fixture service from new C checkout; explicitly retain encoder/OSS substitutes. Existing QA containers/data now removed.
- Start new-C frontend on its own unused loopback port with QA-process VITE_API_PROXY_TARGET=http://127.0.0.1:<QA HTTP port>; no global env or user config change.
- Exercise search general/oven/dynamic new/disabled candidates; list switch/page reset/refresh; import membership intent; heating/label display; blank/unknown/error. Capture actual requests/screenshots.

## 文件与状态
初期离线报告保留在scene-current-acceptance-report-initial.json，正式JSON保留previous_phase。详细HTTP状态/请求体/断言见scene-current-real-http-results.json；所有响应仅QA对象，无密钥。
新建文件：
- .qa/scene-current-acceptance-report-initial.json
- .qa/scene-current-acceptance-report.json
- .qa/scene-current-acceptance-report.md
- .qa/scene-current-frontend-vitest.json
- .qa/scene-current-http-mocked-tests.log
- .qa/scene-current-membership-tests.log
- .qa/scene-current-pg-live-tests.log
- .qa/scene-current-qdrant-mocked-tests.log
- .qa/scene-current-real-http-attempt1.json
- .qa/scene-current-real-http-attempt1.log
- .qa/scene-current-real-http-results.json
- .qa/scene-current-real-http-tests.log
- .qa/scene-current-stack-tests.log
- .qa/scene_current_real_http_qa.py
- .qa/scene_current_report_qa.py

最终git status：
```
?? .qa/scene-current-acceptance-report-initial.json
?? .qa/scene-current-acceptance-report.json
?? .qa/scene-current-acceptance-report.md
?? .qa/scene-current-frontend-vitest.json
?? .qa/scene-current-http-mocked-tests.log
?? .qa/scene-current-membership-tests.log
?? .qa/scene-current-pg-live-tests.log
?? .qa/scene-current-qdrant-mocked-tests.log
?? .qa/scene-current-real-http-attempt1.json
?? .qa/scene-current-real-http-attempt1.log
?? .qa/scene-current-real-http-results.json
?? .qa/scene-current-real-http-tests.log
?? .qa/scene-current-stack-tests.log
?? .qa/scene_current_real_http_qa.py
?? .qa/scene_current_report_qa.py
```
