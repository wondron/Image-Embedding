# 场景管理右栏布局 v0.2 独立验收（L1–L22）更新
报告时间：2026-11-10T22:53:10.720260-07:00 America/Denver

结论：**不能宣布全部视觉通过。** L16 FAIL（关窗焦点未回到触发按钮）。其余 L1–L15、L17–L22 为 PASS。计数 PASS 21 / FAIL 1 / BLOCKED 0。

当前 HEAD `de1c1b979f9024c22610f8e7eec88d153242be54`（提交说明「优化scene配置界面」，2026-11-10T22:49:52-07:00 America/Denver）。这不是交接时的 `c145e84e167fcf01e8e60218955427edb435b52f`，而是它的后续提交。本执行器没有做这次 commit。该提交把与交接 SHA256 完全一致的三个布局文件，以及一批 `.qa` 产物一并写入了仓库。工作树中这三个文件的 SHA256 仍与交接一致，并与当前 HEAD 对象一致。Playwright 是对着当时尚未提交、但字节相同的工作树跑的。

本机 Playwright 1.64.1 + 已安装 Chromium 1248，对着 http://127.0.0.1:57402 与代理/57401，退出码 0（27.2s）。未安装新浏览器，未用 MCP Playwright，未建隧道。API base 已清空走 Vite 代理。L4 在 viewport 1440 下只缩右栏。

三文件哈希（工作树 = HEAD = 交接）：
- SceneManagement.tsx `DA1F3FF120110314915E93602984EA39BF4B2BC33081D4C840959FDC8613BAA7`
- SceneSyncPanel.tsx `B41373D915060173F66EB6ABB254718EE1F32C9517D7FF79CE26CD21159F98A5`
- scene-management.css `79477EB4827B3F29D94611DF161F37D79B34EE073ABC29D4249926F43CC70859`

未提交变更仅剩更新后的 `.qa/scene-detail-layout-acceptance-report.json`、`.md` 与 `scene-detail-layout-evidence.zip`。

## 逐条
|ID|状态|证据要点|
|---|---|---|
|L1|PASS|viewport 1440, right-column inner 798, data-scene-width=wide, header did not overflow, actions ??/??/?? in that order. Screenshots 01-wide-inner800.png and 01-wide-inner800-panel.png.|
|L2|PASS|viewport 1440, inner 598, data-scene-width=medium, flex-wrap, no header clip/overflow. Screenshot 02-medium-inner600-panel.png.|
|L3|PASS|viewport 1440, inner 477, data-scene-width=compact. ??/?? stacked vertically, ?? on its own row, tab label ?????? fully visible, no title overflow. Table overflow-x:auto (this fixture table did not exceed 477px so canScroll=false). Screenshots 03-compact-overview-479.png and 03-compact-labels-table-479.png.|
|L4|PASS|inner/data-scene-width: 718/wide, 717/medium, 478/medium, 477/compact. Screenshots 04-inner720/719/480/479.png.|
|L5|PASS|64-character name wrapped (height 48.6px), overflow-wrap:anywhere, did not extend past the panel. 32-character code visible. 05-06-07-long-overview-600.png and 03/01 panels.|
|L6|PASS|256-character description wrapped (height 90px) without overflowing the panel. Isolated fixture description was stored as ASCII "?" after a QA encoding mishap; Chinese titles still render, so this is not treated as a product glyph bug. Screenshot 05-06-07-long-overview-600.png.|
|L7|PASS|memberCount 9007199254740991 fully visible; caption remains ??????????????????.|
|L8|PASS|general badges ?? / ?????? / ???; qa_badges ?? / ???; no overlap. Screenshots 08-badges-general.png and 08-badges-inactive-include.png.|
|L9|PASS|Empty labels/rules show EmptyState, zero tables, empty copy not repeated in the overview or status stack. 09-empty-labels.png, 09-empty-rules.png.|
|L10|PASS|After /qa/delay-next-catalog, ??????????? appeared, directory still had 5 items, no fake table rows. 10-loading.png.|
|L11|PASS|Catalog 503 INDEX_POSTGRES_FAILED kept 5 items and offered ????. Write 502 via QAFAIL kept the dialog open, description unchanged, directory intact. 11-catalog-error.png, 11-write-error.png.|
|L12|PASS|syncRequired true: scoped ?????? plus directory pendingSync and ???, not a merged ???????????. Then outputs_heating-only false: ????????????????? while pendingSync remained. Switching to oven did not put the success sentence in the new scene scoped stack (it stayed detached in the directory stack). A later full document goto to /?view=scenes remounted the client and cleared in-memory pendingSync; that is not a same-session unselect and is not scored as a product failure. Screenshots 12-save-true-pending.png, 12-save-false-pending-remains.png, 12-switch-scene-notice-scope.png.|
|L13|PASS|?? and ?? disabled; include-all and ?? checkboxes disabled in the editor; protection note appears once and says ?????????. 13-default-protection.png, 13-default-edit-dialog.png.|
|L14|PASS|Header button, tab, and panel title all read ??????. Unselected header entry shows only that title, no badges or edit/delete. After starting sync, switching to oven kept status done and the same counts (same controller, round not reset). 14-unselected-sync.png, 14-15-sync-after-scene-switch.png.|
|L15|PASS|Numeric ??/??/??/??, no percentage. After a failed batch: status failed, cursor still ??, ??????????????, ????????, missing UUID shown once. Same-session leave/return was not validly observed because Playwright page.goto remounted the app (progress empty, continue hidden). Not scored as a product defect. 14-15-sync-idle.png, 15-sync-failed-retry.png.|
|L16|FAIL|Sticky header/tabs did not cover the focused ???? ?? control (coveredByHeader/Tabs=false; 16-sticky-scroll.png). After closing the ?? dialog via ????, document.activeElement was a large page container (text length 1357 starting with ??...), not the trigger ?? button. Criterion requires focus to return to the trigger. Dialog.tsx was not one of the three layout files; this is still an L16 acceptance failure of the current Dialog close path.|
|L17|PASS|From ??, ArrowRight focused ??, End focused ??????, Home selected/focused ??. ?? was previously opened with Enter.|
|L18|PASS|Overview has three groups ??/??/????, no nested cards, no in-overview submit/toggles; attributes still go through ??. 18-overview-groups.png.|
|L19|PASS|Deleting qa_in_use: HTTP 409 SCENE_IN_USE, row remains, one error location. createRule and updateRule with cuisine qa_force_fail: 422, cuisine value kept, rule count unchanged, dialog stayed open. No delete-and-recreate/partial-success UI. 19-label-in-use.png, 19-createRule-error.png, 19-updateRule-error.png.|
|L20|PASS|Only the three handed-off files differ from HEAD; no new hex; no global styles.css/font/nav/API/protection edits.|
|L21|PASS|New CSS is under .scene-management or @container scene-details; no page fetch(); Dialog width selectors unchanged.|
|L22|PASS|SceneMembershipPanel and membership wiring are not in the working-tree diff.|

## 产品缺陷
- **L16**：关闭编辑弹窗后焦点落在大块页面容器上（activeElement 文本长度 1357），不是「编辑」按钮。Dialog 不在本轮 3 个布局文件中，但本条验收未满足。复现：打开编辑，点关闭弹窗，检查焦点。截图 `.qa/scene-detail-layout-screenshots/16-dialog-open-focus.png`。

## 截图
共 37 张，目录 `.qa/scene-detail-layout-screenshots/`。完整交互数据 `.qa/scene-detail-layout-gui-evidence.json`。

## 清理
已按精确身份清理 PID 46208（`.qa/scene_detail_layout_gui_qa.py`）和 PID 53836（Vite `--host 127.0.0.1 --port 57402 --strictPort`）。复查两 PID 均不存在，127.0.0.1:57401 与 57402 不可达。TTL 未延长，未碰 roki / 既有业务 Vite。

