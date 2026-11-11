# 收尾核实（最终追加）
核实时间：2026-11-10T11:14:29.186590-07:00 MST
清理结果：VERIFIED COMPLETE；自然到期，无提前关闭、无延长。
Wait-Process完成后的精确PID16532/47568：均不存在；两只精确QA容器：inspect均No such object，已删除。
结束标记：{"ended_at": "2026-11-10T11:14:13.342603-07:00", "frontend_stopped": true, "api_stopped": true}
GUI最终BLOCKED：78条未执行交互，不变更既有300/300非GUI结果。
parent/computerUse仅可见cursor，无user-Playwright namespace，未navigate/截图/GET；不是网络不通证据。
boboji peer报告Playwright navigate58290返回net::ERR_CONNECTION_REFUSED，无页面/请求/截图；来源为peer，浏览器机器未知，不推断网络原因。
独立wondron本机只读HTTP核查：[{"url": "http://127.0.0.1:58290/", "status": 200, "content_type": "text/html", "bytes": 692, "qa_gui_scene_present": null}, {"url": "http://127.0.0.1:58290/api/scenes", "status": 200, "content_type": "application/json", "bytes": 2386, "qa_gui_scene_present": true}, {"url": "http://127.0.0.1:58283/api/scenes", "status": 200, "content_type": "application/json", "bytes": 2386, "qa_gui_scene_present": true}]。这是HTTP证据，不是GUI证据。
监听端口读取受阻：BLOCKED: Get-NetTCPConnection found in NetTCPIP but module could not autoload. FullyQualifiedErrorId: CouldNotAutoloadMatchingModule. No alternate port inspection attempted. 未绕行其他端口工具。
HEAD初终：08dea28228cad79da67eefb3aadbc6ef55078a42；330基线329项未变，唯一获准测试预期变更，缺失0。业务源码/文档/旧QA保留。
所有GUI URL现为到期历史，不再准备、重启或延长。完整收尾证据scene-completion-cleanup-verification.json、scene-completion-gui-ended.json、scene-completion-final-state.json。
---
# Scene completion 独立新增验收（阶段报告）

阶段报告时间：2026-11-10T11:00:46.091608-07:00 MST

结论：BE真实HTTP/隔离真PG/实际Qdrant验收通过；FE非浏览器回归通过。未执行GUI，78项端到端验收全部BLOCKED，不宣布全部完成。

HEAD：`08dea28228cad79da67eefb3aadbc6ef55078a42`（初终相同）。330基线：329未改，唯一允许变更为OVEN旧测试预期；业务源码、文档、旧QA全部保留。

## 结果
|证据|数量|退出码|
|---|---:|---:|
|新增manual真实HTTP/真PG/Qdrant断言|210/210，78次API请求|0|
|新增目录/default/alias真实HTTP断言|90/90，32次API请求|0|
|BE小范围unit/mock回归（非真实依赖联调）|66/66|0|
|FE原有Vitest（许可修旧预期后）|123/123|0|
|新增FE非浏览器逻辑测试（fetch/React hooks有替身）|15/15|0|
|tsc / build（1663modules）|通过|0 / 0|

实际HTTP状态合计：`{'200': 54, '201': 9, '422': 33, '404': 2, '503': 4, '502': 4, '500': 1, '403': 3}`。其中422/404/403/500/502/503为预期错误断言，不是未经解释的通过。

主目标：`127.0.0.1:64664/qa_scene_completion_b4` + Qdrant `127.0.0.1:64667/qa_scene_completion_b4`；manual验收API60855/PID54164已停。目录补测使用独立GUI数据库/collection `qa_scene_completion_gui_1055`。

边界：实际注册FastAPI路由+真实HTTP；真实PG和Qdrant HTTP客户端；确定性4D编码器与内存OSS显式替身，不测试模型/真OSS。PG触发器回滚、账表后置失败是真DB故障；向量边界故障为注入替身，不冒充物理网络中断。

## BE额外验收
- PASS **BE01-full-directory-nullables**：Full ordered snapshot includes inactive/include_all/history/no row; nullables present, inactive implicitfalse.
- PASS **BE02-readonly**：Audited only one snapshot SELECT; exact DB unchanged; new rule not previewed by GET.
- PASS **BE03-strict-errors**：Strict boolean rejects null/int/string/extra; UUID/int32 paths; missing image422/scene404; envelope/header trace.
- PASS **BE04-idempotence-version**：Unique row, repeated PUT/DELETE stable timestamps/version; only effective member-set change bumps; source-only no bump.
- PASS **BE05-manual-matrix**：Active plain include/exclude; inactive PUT forbidden, DELETE clears; any include_all PUT/DELETE forbidden; historical override ignored.
- PASS **BE06-clear-to-rule**：DELETE even without override computes current rule; clear manual returns rule, not blanket exclusion.
- PASS **BE07-rollback-errors**：Genuine isolated PG trigger failure rolls back membership/image/version; injected query/unavailable/unexpected failures map502/503/500 with redaction.
- PASS **BE08-lock-concurrency**：Observed catalog shared advisory -> image FOR UPDATE -> scene revalidation. Catalog/image waits; two real HTTP writes serialize, unique final intent/max payload_version.
- PASS **BE09-PG-only**：Retrieval dependency absent: manual save200, sync503; no implicit vector or pushed-ledger update.
- PASS **BE10-sync-real-Qdrant**：Actual Qdrant HTTP point scenes equals PG effective codes, pushed ledger catches version; full batch needs empty finish; missing not acknowledged as pushed.
- PASS **BE11-cross-store-failure**：Injected vector-boundary failure preserves PG intent. Genuine PG ledger trigger after actual vector write yields502/staleledger; original-cursor retry repairs actual Qdrant/ledger.
- PASS **BE12-list-search-heating**：Actual HTTP list and actual Qdrant-backed search share membership; outputs_heating/scene_label/heating_group contract verified.
- PASS **BE13-directory-default-alias**：Separate real-HTTP90 assertions/32requests: dynamic CRUD/inactive visibility, protected general403, default/normal/device canonical, uppercase/unknown/inactive LIST/SEARCH/INDEX422 and no rejected-index additions.

## 78项逐条验收
全部为BLOCKED端到端；以下给出已证部分，不能等同GUI通过。
|ID|状态|契约结果|已证范围/未测边界|
|---|---|---|---|
|A1|BLOCKED|可见「场景管理」，点击进入列表+详情壳|Static App navigation and SceneConfigPage wiring inspected; real Vite GET/proxy200. No navigation click/DOM.|
|A2|BLOCKED|详情提供页签「概览 / 标签 / 规则 / 同步」；窄屏可返回列表|Static detail tab + URL + narrow-list source inspected; no viewport/DOM.|
|A3|BLOCKED|无「设为默认」、无单 scene GET / job / 服务端取消 / 权限系统 UI；未恢复或改配色|Static scope review; no visual/palette comparison.|
|A4|BLOCKED|复用 `AppModalShell` 的 `sm`（约 700）；非必要不用平行弹窗|Static modal-shell source only; no actual modal appearance.|
|B1|BLOCKED|发起 `GET /api/scenes`；value=code、展示=name、顺序与服务端一致；code 为自由 string|New nonbrowser DTO/catalog active-order tests; actual current GET /api/scenes includes arbitrary QA codes. Consumer dropdown rendering not tested.|
|B2|BLOCKED|只出现 `isActive` 项；管理列表仍可见停用并可恢复|New nonbrowser active filtering retains inactive catalog; real directory includes inactive QA scene. Actual selectors not clicked.|
|B3|BLOCKED|仅集中映射为 `general` / `oven`；`Normal` 等不擅自 lowercase 命中；请求只发 canonical|New nonbrowser case-sensitive alias tests + actual HTTP list/search normal->general, device->oven; Normal/OVEN rejected. GUI request canonicalization not observed.|
|B4|BLOCKED|无候选、无六项 seed、有错误与重试；不把失败当空数组成功|New nonbrowser first-load failure produces no seeds/data; no actual selector error/retry DOM.|
|B5|BLOCKED|保留上次展示并标错；禁止依赖候选的新提交|New nonbrowser cached failure retains data, valid=false; disabled actual submit DOM not tested.|
|B6|BLOCKED|选中值保留|Static useSceneSelection reconciliation only; useEffect is stubbed in QA tests, so lifecycle/selection persistence unproved.|
|B7|BLOCKED|回退到该默认并提示；不选首个非默认项|Static selected->valid-default reconciliation only; no real hook lifecycle or notification.|
|B8|BLOCKED|配置错误、禁提交、可重试；不伪造 general、不擅选首项|New nonbrowser invalid/missing active default makes catalog invalid, never selects first as replacement; actual submit gating/notification needs GUI.|
|B9|BLOCKED|缓存已失效并重新 GET；同步后 memberCount 更新；无轮询|New nonbrowser catalog mutation invalidation; real directory CRUD; actual cross-entry rerender/member count not observed.|
|B10|BLOCKED|请求合并或丢弃过期响应；旧响应不覆盖新数据|New nonbrowser request coalescing and generation stale-response discard with controlled promises; no actual React lifecycle.|
|B11|BLOCKED|只触发一次共享刷新，成功后再回退；不无限重试；不自动重发上传/写|Static unknown-scene error refresh handling only; actual BE LIST/SEARCH/INDEX_UNKNOWN_SCENE errors verified; no UI auto-refresh/replay observation.|
|C1|BLOCKED|回到 page=1 并重拉|Static library page-reset integration only; no page2->scene click/request observation.|
|C2|BLOCKED|**不**自动重搜；旧结果被清除或标明仍基于旧场景；阈值不变|Static search clear/stale-result handling only; no scene switch/threshold behavior observed.|
|C3|BLOCKED|失效项被移除；若无剩余则落到有效 default，并提示复核后再提交|Static import multi-selection reconciliation only; no upload/notification GUI.|
|C4|BLOCKED|不承诺 hints 等于已 manual 加入|Static import-hints explanatory source only; no rendered copy.|
|C5|BLOCKED|按实际数据展示；不凭 oven 种子猜测|New nonbrowser no oven heuristic/outputHeating tests; actual HTTP heating_group/label output verified; rendered result not observed.|
|D1|BLOCKED|可提交；可选字段默认符合契约；body 仅允许字段|Real HTTP create201 + directory default fields; FE API DTO tests. Actual form defaults/submit not tested.|
|D2|BLOCKED|前端拦截或提交后按 422/409 展示；不发明显非法请求（能拦则拦）|Form/schema source inspected; actual form interception and field errors not tested.|
|D3|BLOCKED|code 只读；可改 name/description/outputsHeating/includeAll（受保护约束）|New nonbrowser PATCH excludes code/extra; real HTTP name/active changes. Actual read-only code control not observed.|
|D4|BLOCKED|控件不可用并说明保护；即使强调接口，403 按保护而非权限展示|Actual general disable/include_allfalse/delete403 SCENE_PROTECTED; FE error parser distinguishes protection; actual disabled controls/copy not observed.|
|D5|BLOCKED|提示将忽略规则与旧 manual；保存成功出现「已保存，待重算同步」，不声称 Qdrant 已更新|Actual BE include_all behavior and stale-vector-vs-PG verified; static confirmation/saved-pending UI only.|
|D6|BLOCKED|提示旧 manual 可能恢复；成功同上条保存语义|Static include_all disable confirmation + historical override behavior code; not operated in GUI.|
|D7|BLOCKED|文案含：级联 labels/rules/memberships、不删图、需 sync；成功后该详情关闭|Actual isolated dynamic scene delete + directory removal; cascading UX/confirmation/close not observed.|
|D8|BLOCKED|无此入口|Static no default/code mutation entry; actual general protection403; no GUI negative discovery.|
|D9|BLOCKED|数字仍为物化值，UI 不把它标成实时预览|Actual readonly snapshot does not preview new rule; materialization boundary proved; actual memberCount UI copy not observed.|
|D10|BLOCKED|防重复提交；不确定时先重新 GET 对账再允许手动重试；不自动重放|New nonbrowser outstanding config write blocks sync round; component mutation uncertainty refresh/double-click guard only static.|
|D11|BLOCKED|不发空 PATCH；除 description 外不发非法 null|New nonbrowser PATCH whitelist; empty/null form gate source only; no actual form request interception.|
|E1|BLOCKED|201 后详情用返回 `scene` 更新；待同步提示出现|Actual label create201 used in live fixtures; API nested DTO decoding; actual editor response/rerender not observed.|
|E2|BLOCKED|409/422 正确落在字段；保留输入|No independent interactive duplicate label field-error test; source only.|
|E3|BLOCKED|允许；不可改所属 scene|Nested label DTO decoder tested; actual editing membership/scene ownership controls not observed.|
|E4|BLOCKED|409 `SCENE_IN_USE`；项仍在，不假装删除|No new real-HTTP delete-label-in-use scenario in this run; existing regression/source partial only.|
|E5|BLOCKED|该项可见，未被静默清空|New nonbrowser inactive nested label references preserved; actual list/rule rendering not observed.|
|F1|BLOCKED|不发请求或 422；须恰一非空|RuleEditor xor validation source only; no form interaction.|
|F2|BLOCKED|允许；无本地菜系词典拦截|RuleEditor free cuisine source only; no interactive submit.|
|F3|BLOCKED|禁止提交或 422；label 仅同类、仅本 scene 类别规则|Same-scene/category-label validation source only; no interactive invalid submission.|
|F4|BLOCKED|展示用嵌套对象；写出 ID；不把嵌套对象当请求体|New nonbrowser nested category/label DTO decode; actual category rule create uses IDs. Actual form payload observation not obtained.|
|F5|BLOCKED|必须确认「删旧建新」；不发改目标的 PATCH|Static confirmed delete-old/create-new + partial error UI examined; no live component partial-failure interaction.|
|F6|BLOCKED|允许；成功刷新 scene|API/source PATCH label_id path inspected; no new interactive change/clear.|
|F7|BLOCKED|409 `SCENE_RULE_EXISTS`|Existing regression/source partial; no independent new UI duplicate-target409 scenario.|
|F8|BLOCKED|文案不声称删除 manual；成功待同步|Static rule-delete explanation/mutation handling only; no confirmation rendering.|
|F9|BLOCKED|引用可见；不得因前端 active 过滤而消失，也不得宣称「BE 拒绝 inactive」除非真实返回错误|New nonbrowser nested inactive references survive decoder; no actual rule table rendering.|
|F10|BLOCKED|**未**调用任何本规格未给出的新类别 API；依赖已有数据源并在实现说明记录|Static RuleEditor uses existing /api/categories; actual fixture existing endpoint201; no invented category API identified in inspected code, no browser traffic audit.|
|G1|BLOCKED|说明须显式同步；PG 保存未被写成索引完成|Actual PG-only manual save vs explicit vector sync boundary proved; UI explanatory copy static only.|
|G2|BLOCKED|发出 `POST /api/scenes/sync`，`after` 空/null，limit∈[1,1000]|New nonbrowser default200/1..1000 limits/start cursor; actual backend sync HTTP tested. No button click.|
|G3|BLOCKED|写入口禁用|New nonbrowser shared write-lock state; actual config/manual controls disabled not observed.|
|G4|BLOCKED|用该 `next_after` 发下一批；失败前不得前移|New nonbrowser next cursor sequencing and unchanged original cursor on failure; no actual DOM.|
|G5|BLOCKED|正常结束；若 done=false 但 next_after 缺失或未前移则停止并报协议异常，禁止循环发请求|New nonbrowser extra empty finish + malformed/nonadvancing cursor stops; actual BE full batch needs extra empty batch. No UI protocol-error rendering.|
|G6|BLOCKED|仍用原 after/limit；不自动重放 POST；提供手动重试；有 detail/trace|New nonbrowser network failure preserves original after/limit, no auto retry, explicit retry; real BE502/503 and retry repair. Actual error controls not observed.|
|G7|BLOCKED|可见已处理/变更/已推/缺失数量；**无**虚假百分比|New nonbrowser counters in state; static missing/counters without fake percentages; no rendered progress.|
|G8|BLOCKED|processed 不被重试重复累加；missing 为去重集合|New nonbrowser manual retry accumulation/missing dedup; no actual UI counters.|
|G9|BLOCKED|明示扫描完成≠缺失已修复；集合仍展示|New nonbrowser missing retained on done; actual Qdrant missing has stale/no pushed ledger. Rendered finish warning unobserved.|
|G10|BLOCKED|不再发后续批；已发出请求不取消；已 PG 提交不撤销|New nonbrowser leave prevents next batch without aborting outstanding promise; no actual React effect/unmount because useEffect stubbed.|
|G11|BLOCKED|**不**静默续跑；有游标则显式「继续」或「从头」；游标不可靠则只从头并告知|New nonbrowser stopped state requires explicit continue; actual remount effect/controls not exercised.|
|G12|BLOCKED|无此能力、无假 API|Static no server cancellation path; new nonbrowser no AbortSignal on in-flight leave. No actual GUI.|
|H1|BLOCKED|面板说明待接入；所有写禁用；无假保存|Conditional unavailable-capability case (BE NOW delivered). Decoder errors tested; capability failure disables panel only static, no live405/501 GUI.|
|H2|BLOCKED|**不**从来源字段猜 origin/override；不把 chips 升级成来源表|Conditional unavailable-capability case (BE NOW delivered). Static panel derives fields only from validated GET; chips not origin. No unavailable-capability GUI.|
|H3|BLOCKED|全目录含 inactive 与 include_all；顶栏有效成员= `scenes[]`；name 按 ID join，失败显示 code；四类信息分列|Actual public GET full inactive/include_all/no-row snapshot + required nullables; FE DTO decode verified; actual join/columns GUI unobserved.|
|H4|BLOCKED|不引起写库副作用；不把未 sync 的规则变化预演成已生效（可标待同步）|Actual single snapshot SELECT audit + unchanged DB/GET not recompute; FE GET wire tested; pending-sync GUI copy unobserved.|
|H5|BLOCKED|可纳入/排除；**不**展示无意义的「回规则」|Actual no-row/null origin snapshot and active PUT matrix; UI no-meaningless-clear control source only.|
|H6|BLOCKED|DELETE；说明回到规则≠排除；200 后刷新快照并出现待同步（`sync_required`）|Actual public DELETE recomputes current rule and sync_required=true; FE exact DELETE wire tested; notice/rerender not observed.|
|H7|BLOCKED|PUT 禁用；允许 DELETE 清覆盖|Actual inactive PUT422 and DELETE200 old override clear; UI operation gating static only.|
|H8|BLOCKED|PUT/DELETE 均不提供|Actual inactive/no-row snapshot; UI neither action source only. BE DELETE idempotency is not UI action permission proof.|
|H9|BLOCKED|全部 PUT/DELETE 禁用；origin=implicit；停用时 is_member=false|Actual active/inactive include_all PUT/DELETE422, implicit origin, inactivefalse; actual disabled controls not observed.|
|H10|BLOCKED|不报错；仍提示需显式同步；不把 version 当同步确认；不自动 sync|Actual repeated/source-only writes200 unchanged version but sync_required=true; FE protocol parser tested; actual notice/no auto sync GUI not observed.|
|H11|BLOCKED|串行；前一个结束前后者不发|Actual BE simultaneous HTTP writes serialize/unique/version; FE per-image module lock inspected only, NOT proven by BE concurrency.|
|H12|BLOCKED|先 GET 对账，再由用户按当前意图手动重试；无后台重放；无 CAS UI|Static FE uncertain write first-GET reconciliation; no executed UI fault path. Real BE cross-store failure evidence does not prove FE replay policy.|
|H13|BLOCKED|写禁用|New nonbrowser shared write lock state tested; actual open-panel controls not observed.|
|H14|BLOCKED|按错误展示，不当成功|Actual missing scene404/include_all/inactive422; new nonbrowser DELETE404 propagates error, not success; actual error rendering unobserved.|
|I1|BLOCKED|可从 `X-Trace-Id` 取得；成功体无 `trace_id` 字段也成立|Actual success X-Trace-Id; manual/config success no body trace; index/list/search existing body trace allowed. New FE header-trace decoding tested; actual GUI trace display unobserved.|
|I2|BLOCKED|可见 `error`、`detail`、`trace_id`；有 `errors` 则字段级展示|Actual422/404/500/502/503 envelope and redaction; FE decoder error/protection tests. Actual field-level rendered errors unobserved.|
|I3|BLOCKED|解释为默认保护，不出现权限/登录流程|Actual403 SCENE_PROTECTED + FE parser protection; actual visible explanation/login absence not operated.|
|I4|BLOCKED|pages→features→entities→shared；无页面 fetch；不继续把本刀主逻辑堆进 `App`/`ManagementViews`；目录与选择器单一复用|Static source layering and new page/feature/entity/shared boundaries inspected; no browser proof per parent acceptance rule.|
|I5|BLOCKED|未发明/调用单 scene GET、`active_only`、job、改默认、服务端取消、未契约类别 API|Static actual route/client path audit; existing category endpoint verified. No full browser network audit.|
|I6|BLOCKED|已成功数据不被清空成空库|New nonbrowser successful catalog retained after failure; actual management/detail components not interactively faulted.|

## 发现与归属
- 未发现已证实产品BUG；不代表未测交互无缺陷。现公开manual/dynamic目录真实存在，旧缺口不得沿用。
- QA初版trace断言错误已仅修QA；保留失败attempt1。
- OVEN旧测试与大小写契约错位，经许可仅改预期一行，diff/原文件备份保留；不是产品修复。
- 新非浏览器测试初次14/15为复用Response的QA错误，修替身后15/15，旧证据保留。
- 历史95/95和早期187重复运行不计当前新增通过；前次暂时unreachable后的落盘结果已核实，不能再称持久离线。

## GUI交接历史（已到期；以下准备信息非当前可用服务）
- wondron机器本地：**http://127.0.0.1:58290/?view=scenes&scene=7&sceneTab=overview**；API **http://127.0.0.1:58283**。HTTP代理已核200，未执行浏览器。
- API/coordinator PID **16532**；FE PID **47568**。仅127.0.0.1，不是box可达URL；无公网隧道。
- 务必新浏览器配置或先确认API base为空走QA proxy/精确QA API；不可沿用业务API保存值。
- 生命周期至 **2026-11-10T11:14:11.768895-07:00 MST**：脚本finally自动停自建FE/API，并docker stop+rm自己的两个精确ID。该段为11:00 MST历史交接状态；最终清理状态以上方收尾核实为准。
- PG797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4；Qdrant ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb。两者Mounts=[]、storage为tmpfs，已复核；业务roki与旧Vite不动。
- 精确清理脚本 `.qa/scene-completion-gui-cleanup.ps1`；完成证据 `.qa/scene-completion-gui-ended.json`。parent需收尾核实精确ID已不存在。

GUI操作清单：
1. ONLY browser on wondron127.0.0.1, not box. Open frontend_url in clean profile or verify connection setting API base blank (QA proxy) or exactQA api_url BEFORE writes. Do not reuse saved business API base.
2. Open scene_management_url. Confirm dynamicQA/default/inactive directory, tabs, narrow layout, URL/reload and controls. A1-A4/B1-B10/D1-D11.
3. Create/edit/disable/reactivate/delete only newqa_gui_ prefixed scenes; labels/rules use qa_gui_ names. Validate protectedgeneral controls. Rule target replacement confirm and injected newcreatefailure must show partial delete status. E/F items.
4. Open Library QA image7177401c-46f8-16da-d8f2-e0fab52f3465. ActiveQA rule7, inactiveoldoverride8, inactiveincludeall9; verify H3-H14 matrix, sameimage serialization and uncertain-write firstGET. No realOSS; missing thumbnails expected stub boundary.
5. Use .qa/scene-completion-query.png for search/index file only on exactQA proxy. Check scene switch/page reset/no auto research/threshold/import reconciliation/heating/labels. B11/C1-C5.
6. Observe sync /api/scenes/sync batch/cursor. Defaultlimit200; use1 for extraempty. Verify no config/manual writes duringround, leave stops futurebatches, reenter explicitcontinue. To exercise errors/nonadvancing/missing use authorized browser request interception on QA origin only; do not kill businessservices or mutate service config. G1-G12.
7. For H1/H2 conditionally intercept manual GET405/501/unsupported schema in browserQA only; writes disabled, no fake origin. For cachedGET failure/defaultinactive/delay/stale races use browserQA interception, NOT businessconfig.
8. Capture actual GUI+network evidence per78. No visual/interaction PASS solely from these unit/API results. Finish before TTL or explicitly rerun controlled QA harness; do not tunnel. Cleanup own IDs/PIDs and verify no business PID touched.

## 保存与文件
完整初终git dirty status、330文件初终SHA256、响应状态/trace、断言名称、命令退出码、新建QA清单见同名JSON与 `.qa/scene-completion-final-state.json`。
新建/保留本轮QA文件：
- `.qa/scene-completion-acceptance-report.json`
- `.qa/scene-completion-acceptance-report.md`
- `.qa/scene-completion-backend-attempt1.json`
- `.qa/scene-completion-backend-attempt1.log`
- `.qa/scene-completion-backend-attempt2.json`
- `.qa/scene-completion-backend-attempt2.log`
- `.qa/scene-completion-backend-attempt3.json`
- `.qa/scene-completion-backend-command.log`
- `.qa/scene-completion-backend-http-results.json`
- `.qa/scene-completion-backend-http.log`
- `.qa/scene-completion-backend-regression.log`
- `.qa/scene-completion-baseline.json`
- `.qa/scene-completion-cache-cleanup.json`
- `.qa/scene-completion-directory-http-results.json`
- `.qa/scene-completion-directory-http.log`
- `.qa/scene-completion-evidence.zip`
- `.qa/scene-completion-final-state.json`
- `.qa/scene-completion-frontend-command.log`
- `.qa/scene-completion-frontend-regression-after.json`
- `.qa/scene-completion-frontend-regression.json`
- `.qa/scene-completion-gui-cleanup.ps1`
- `.qa/scene-completion-gui-frontend.log`
- `.qa/scene-completion-gui-ready.json`
- `.qa/scene-completion-gui-service.log`
- `.qa/scene-completion-model-test-before.txt`
- `.qa/scene-completion-nongui-attempt1.json`
- `.qa/scene-completion-nongui-command.log`
- `.qa/scene-completion-nongui-results.json`
- `.qa/scene-completion-nongui.test.ts`
- `.qa/scene-completion-query.png`
- `.qa/scene-completion-resume-hash.json`
- `.qa/scene-completion-test-compatibility.diff`
- `.qa/scene-completion-vitest.config.mjs`
- `.qa/scene_completion_backend_qa.py`
- `.qa/scene_completion_directory_http_qa.py`
- `.qa/scene_completion_final_state_qa.py`
- `.qa/scene_completion_gui_qa.py`
- `.qa/scene_completion_report_qa.py`

仅应用外测试预期获准修改；未修产品代码、未云端上传/commit/push、未读旧D仓、未写业务数据/真实OSS、未变用户服务/Nacos配置。
