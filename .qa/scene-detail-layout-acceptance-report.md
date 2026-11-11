# 场景管理右栏布局 v0.2 独立验收（L1–L22）
报告时间：2026-11-10T22:38:22.550831-07:00 MST

结论：**不能宣布视觉通过。** L20–L22 仅按静态 diff 为 PASS；L1–L19 因无真实页面截图与授权浏览器交互全部 BLOCKED。非 GUI 的 tsc/build/单测通过，不代替 L1–L19。

HEAD 初终均为 `c145e84e167fcf01e8e60218955427edb435b52f`，与实施交接一致。工作区应用改动仅为交接声称的 3 个文件（+167/−41），SHA256 实测一致：

- `SceneManagement.tsx` DA1F3FF120110314915E93602984EA39BF4B2BC33081D4C840959FDC8613BAA7
- `SceneSyncPanel.tsx` B41373D915060173F66EB6ABB254718EE1F32C9517D7FF79CE26CD21159F98A5
- `scene-management.css` 79477EB4827B3F29D94611DF161F37D79B34EE073ABC29D4249926F43CC70859

业务语义抽查（源码，不是视觉）：仍走 `updateRule`/`createRule`；`syncRequired` 两态原句与独立 `pendingSync` 保留，无「已保存，待重算同步」替换句。L19 不按已废弃的删旧建新/部分失败来验。

## 非 GUI 命令（与视觉结论分开）
- 前端既有 Vitest：131/131，退出码 0
- 其中 scene 相关单测（entities/scene + scene-catalog）：8/8；无 SceneManagement 组件测试
- tsc -b：退出码 0
- npm run build：退出码 0，1663 modules（忽略的 dist，非视觉验收）

## L1–L22
|ID|状态|准则|证据边界|
|---|---|---|---|
|L1|BLOCKED|右栏内宽 >=720：标题带不出现横向滚动；主操作在前、危险操作在末。|源码：标题 flex、删除在 scene-detail-danger-actions、container/data-scene-width=wide 于内宽>=720。无实测内宽、无截图，不能证明无横向滚动或操作顺序的视觉结果。|
|L2|BLOCKED|右栏内宽 480–719：名/code/徽标/操作可换行，无裁切、无标题横溢。|源码：480–719 整组 width:100%、overflow-wrap:anywhere、禁止标题 nowrap。无该内宽截图。|
|L3|BLOCKED|右栏内宽 <480：操作纵向堆叠且删除单独一行；表可横滚；页签可两行且「全量场景同步」完整；标题与页签不横溢。|源码：<480 操作 column、删除单独一行、表 overflow-x:auto、页签可换行且文案为「全量场景同步」。无该内宽截图或实际换行/横溢测量。|
|L4|BLOCKED|上述断点随右栏内宽变化（窗口仍宽但右栏变窄也应切换），不是只跟 viewport。|源码：@container scene-details + ResizeObserver 以 clientWidth-padding 写入 data-scene-width；viewport 800px 仅用于整页单栏。交接页尚未在浏览器按「窗口仍宽、只缩右栏」测量。|
|L5|BLOCKED|64 字名称、最长合法 code：折行可见，不撑出右栏。|隔离夹具已提供 64 字名称与 32 位合法 code（id=4），但未打开页面截图或确认未撑出右栏。|
|L6|BLOCKED|256 字 description：概览内折行可读，不溢出。|隔离夹具已提供 256 字描述，源码说明组 overflow-wrap:anywhere。无概览截图。|
|L7|BLOCKED|很大的 memberCount 完整可见；旁注仍为物化值、非实时预览。|隔离夹具 memberCount=9007199254740991；源码 tabular-nums 与旁注「当前已物化值，不是新规则实时预览」。无数字完整可见的截图。|
|L8|BLOCKED|启用或停用二选一，与默认、全包含并列时可换行且不重叠。|源码徽标行启用/停用二选一 + 默认 + 全包含，flex-wrap。夹具含 general（默认+全包含+启用）与 qa_badges（停用+全包含）。无重叠/换行截图。|
|L9|BLOCKED|标签或规则为空：该页签空态，不渲染假装有数据的表；不在状态堆/概览重复同一空文案。|源码空标签/规则走 EmptyState、不渲染空表；空文案不进状态堆/概览。夹具 qa_empty id=3。未打开页签确认。|
|L10|BLOCKED|加载中：沿用既有加载指示，不出现假字段/假表数据。|源码目录加载使用既有 LoaderCircle 文案，不造假表。夹具提供 /qa/delay-next-catalog。未观察加载态。|
|L11|BLOCKED|刷新或写入失败：约定状态位置给出真实错误+可重试（若允许）；已成功详情与列表不被清空。|源码目录失败在目录级状态堆+重试；写入失败按 feedbackTarget/scene id 显示；刷新不清空已有 data。夹具提供失败开关。未执行失败交互。|
|L12|BLOCKED|保存两态与 scope：syncRequired===true 显示「已保存。」；若 c.pendingSync 为真，同一状态堆保留目录级未同步提示与「去同步」，不得改写成统一的「已保存，待重算同步」。false 显示「已保存，已生效（不需要同步）。」，且不得清除或覆盖仍然为真的其他 pendingSync。场景级提示绑定该 scene id；目录级 pendingSync/写锁/目录错误未选中也可见。不画精确全局 dirty，不把保存写成索引已完成。|源码保留 result.syncRequired 两态原句，目录级 pendingSync 与「去同步」独立；sceneSaved 使用 pendingSync // syncRequired，false 不会清除已有 pending。无「已保存，待重算同步」替换句。catalog 单测覆盖 pending 标志逻辑（非状态堆视觉）。未在真实页面同时看到两态句与 pendingSync。|
|L13|BLOCKED|默认场景：停用/删除/取消全包含不可用，原因只出现一次，且不是权限/登录语气。|源码保护控件 disabled，说明只在右栏 #scene-protection-note 一次，文案非权限/登录语气。未点击/截图。|
|L14|BLOCKED|页签与页头进入的同步都标明「全量场景同步」；未选中或空列表也可达；同一控制器；切场景不重置轮次。|源码页头按钮、页签 label、Sync 区 h3 均为「全量场景同步」；未选中可进 sync；同一 SceneSyncPanel；切场景不调用 start/stop 重置。未做 GUI 进页/切场景观察。|
|L15|BLOCKED|同步数字进度、无百分比、原 cursor 手重试、missing 去重、离开停止后续批/返回不静默续跑等行为保持现实现。|SceneSyncPanel 只加层次 class，四项计数、无百分比、原游标按钮与 leaving 文案仍在；scene-sync 协议未在本三文件中改写。本刀未回归实际同步行为。夹具仅供版式演示，不是真实 Qdrant 协议验收。|
|L16|BLOCKED|滚动时 sticky 不遮挡当前焦点控件、工具条或 Dialog；关窗焦点回到触发按钮。|源码按实高 sticky、scroll-padding、focus 上移避开；Dialog 仍走既有壳。未测遮挡或关窗焦点回到触发按钮。|
|L17|BLOCKED|主要操作与页签可键盘到达并激活。|源码标题操作与页签可 tab，页签支持方向键/Home/End。未做键盘验收。|
|L18|BLOCKED|概览为少量分组+只读摘要，无多层嵌套卡；属性变更仍走编辑入口/既有确认。|源码概览三组只读摘要，属性仍走原编辑 Dialog/确认，无概览内直接提交开关。未打开编辑确认。|
|L19|BLOCKED|标签/规则各有工具条、表、空态。标签 SCENE_IN_USE：项仍在，真实错误出现在单一约定位置，不假装已删除。规则失败走当前 updateRule/createRule：保留表单输入与列表，展示真实错误，不伪造删旧建新或部分成功。不恢复旧删建流程，本布局不推断新 wire。|源码标签/规则含工具条+表或空态；删除走既有 perform；规则保存仍为 updateRule/createRule，无删旧建新。未执行 SCENE_IN_USE 或规则失败交互，不得用旧删建部分失败准则判 L19。|
|L20|PASS|未引入新的颜色 hex、未改全局配色/全局字体定义、未改左栏与导航、未改接口与保护规则。|Working tree application changes are exactly the three handed-off scene-management files (+167/-41). Diff adds no #hex or rgb/hsl color literals; color uses existing tokens (--text/--muted/--border/--primary/--surface/--panel-bg/--hover) plus background:none. styles.css, App.tsx, ManagementViews and other global typography/color files are unmodified. perform/sceneSaved two-state copy, updateRule/createRule, and default-protection rules remain in the same TSX. Shared .scene-layout gained min-height/flex/align-items:stretch only; left-column item IA/sort/color rules and navigation components were not changed.|
|L21|PASS|新增样式限于 scene-management 作用域；无页面 fetch；旧业务 Dialog 视觉与尺寸未变。|Newly added CSS rules are under .scene-management or @container scene-details. SceneManagement.tsx / SceneSyncPanel.tsx contain no fetch(). Dialog width selectors remain min(700px, calc(100vw - 30px)) and min(80vw, 980px) and were not edited. Pre-existing unscoped feature selectors .scene-details and .scene-table were updated in this file (padding 20->16, table horizontal padding 8->16, container-type, overflow-wrap). Membership dialog size rules were untouched. Runtime visual comparison of non-scene business Dialogs was not performed; this PASS is the static selector/size evidence allowed for L21, not a screenshot.|
|L22|PASS|本轮未改图片详情 manual 面板。|frontend/src/features/scene-memberships/SceneMembershipPanel.tsx and membership API/wiring files are not in the working-tree diff. Membership-specific CSS in scene-management.css (except unchanged dialog size rules) was not part of the added/edited hunks for this layout pass.|

计数：PASS 3 / FAIL 0 / BLOCKED 19

## GUI 交接（本机 loopback，限时保留）
- 前端：http://127.0.0.1:57402/?view=scenes
- API 夹具：http://127.0.0.1:57401
- Vite PID 53836 端口 57402；API PID 46208 端口 57401
- 已用 Vite 代理核过 GET /api/scenes HTTP 200，夹具含 32 位 code。这不是页面截图。
- 仅 wondron 127.0.0.1，不是 box 可达 URL；无隧道、无公网绑定。先前 peer Playwright 对 wondron loopback 出现过 net::ERR_CONNECTION_REFUSED，不能假定 Playwright 在用户机。请在 wondron 本机浏览器操作。
- 写操作前必须把应用内 API base 设为空（走代理）或精确夹具 URL，禁止沿用已保存的业务 API，以免写入业务数据。夹具为内存数据，不是真实 FastAPI/PG/Qdrant。
- 到期：2026-11-10T23:36:25.296524-07:00 MST。自然 TTL 只停上述精确 PID。清理脚本 `.qa/scene-detail-layout-gui-cleanup.ps1`。未启动 Docker，不碰业务 roki 与既有 Vite。

右栏内宽核实（截图前必做）：在 DevTools 执行交接 JSON 中的 `width_measurement` 片段，记录 inner width 与 `data-scene-width`。L4 须保持窗口仍宽、只改右栏列宽，不能只缩 viewport。

操作清单：
1. Only browse on wondron (the user machine). URLs are 127.0.0.1 and are not reachable from the box. Do not start a tunnel. Do not assume Playwright is available on this machine.
2. Before any write: in a clean profile or via connection settings, set API base blank (Vite proxy) or exactly http://127.0.0.1:57401. Never reuse a saved business food-atlas-api value.
3. Confirm the uncommitted layout is loaded before screenshots: tab label is 「全量场景同步」, .scene-details[data-scene-width] and .scene-detail-badges exist. CSS SHA256 at start was 79477EB4827B3F29D94611DF161F37D79B34EE073ABC29D4249926F43CC70859. Record the DevTools snippet from gui_handoff.width_measurement, including inner width.
4. L4: keep the window >=1280px wide and only narrow the right column with grid-template-columns 1fr 720px / 719px / 480px / 479px. Record inner width and data-scene-width each time. Viewport-only resize is not L4 evidence.
5. L1–L3/L5–L8: use scene 4 (64-char name, 32-char code, 256-char description, memberCount 9007199254740991), scene 1 (default badges) and scene 5 (inactive+include_all). Screenshot no overflow/clipping, action order, stacked delete at compact width, and caption 「当前已物化值，不是新规则实时预览」.
6. Unselected: open http://127.0.0.1:57402/?view=scenes without a scene id — right column should say 请选择左侧场景, header 全量场景同步 still works. http://127.0.0.1:57402/?view=scenes&sceneTab=sync should show only the full-sync title, no empty badges or edit/delete.
7. L9: http://127.0.0.1:57402/?view=scenes&scene=3&sceneTab=labels and rules tab — empty states, no empty table, no duplicate empty copy in the status stack or overview.
8. L10–L11: GET http://127.0.0.1:57401/qa/delay-next-catalog?ms=2500 then Refresh (loading, no fake rows). GET .../qa/fail-next-catalog then Refresh (error+retry, prior list remains). GET .../qa/fail-next-write or save a name containing QAFAIL (error in the agreed status place).
9. L12: On scene 3, edit description and save — expect 「已保存。」 plus directory pendingSync and 去同步, not a merged 「已保存，待重算同步」. Then edit only 输出加热信息 and save — expect 「已保存，已生效（不需要同步）。」 while pendingSync remains. Switch to another scene: the success sentence must not stick to the new scene; pendingSync stays visible including when none is selected.
10. L13: Open http://127.0.0.1:57402/?view=scenes&scene=1&sceneTab=overview — disable/delete/include-all-off are disabled, the reason appears once, not as a permission/login message.
11. L14–L15: Enter sync from the header without a selection and from the tab with a selection; copy says 全量场景同步; same controller; switching scenes does not reset the round. Layout may use the fixture sync (limit 1 then empty finish; /qa/fail-next-sync for a failed batch). Fixture sync is not real Qdrant acceptance; only layout/interaction of the existing client behavior.
12. L16–L17: sticky vs focus and Dialog; close returns focus to the trigger. Keyboard to actions and tabs, including arrow/Home/End.
13. L18–L19: overview is three read-only groups; attributes go through Edit + existing confirmation. Labels on scene 2: delete qa_in_use (id 21) expects SCENE_IN_USE, row remains, single error location. Rule create/update with cuisine qa_force_fail expects a real error, form and list kept; do NOT judge leftover delete-and-recreate/partial-failure behavior.
14. Do not write business databases or use the business API. Finish or stop before 2026-11-10T23:36:25.296524-07:00 MST. Cleanup script: .qa/scene-detail-layout-gui-cleanup.ps1 (exact PIDs only). Never touch business roki containers or pre-existing Vite.

## 保存与范围
未改业务实现、未 commit/push/云端、未读旧 D 仓、未写业务库。三文件哈希与交接一致。本任务只新增 `.qa` 报告/夹具文件。
截图目录应为 `.qa/` 与 box `/workspace/deliverables/scene-detail-layout/`，本阶段无截图。
