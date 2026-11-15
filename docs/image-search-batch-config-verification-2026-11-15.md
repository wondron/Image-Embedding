# 多图搜索与配置传输验收记录

日期：2026-11-15（America/Denver）。验证对象为本次工作区，包括尚未提交的后端、前端和文档改动。复现命令及逐项预期见[验收步骤](image-search-batch-config-acceptance.md)。

## 执行结果

| 检查 | 结果 | 范围 |
|---|---|---|
| 后端自动化 | 98 项通过，零失败/跳过 | 新增 batch/config import，以及 retrieval HTTP、categories/scenes HTTP、结果卡、检索用例回归 |
| 前端单元测试 | 38 文件、306 项通过 | 包括批量结果映射、配置导入适配、文件读取、PNG 尺寸边界及真实 FP32 分数回归 |
| 前端构建 | 通过 | TypeScript + Vite；有现有 bundle 大小提示 |
| Edge 浏览器回归 | 7 文件、74 项通过 | 七文件整组 73 项；另追加 PNG 失败兜底 1 项通过。覆盖配置传输、下载、图片搜索、类别管理、图库标注、场景同步、工作台 |
| 真实配置接口 | 38 项通过，80 次主要 HTTP 请求 | PostgreSQL 中真实预览、提交、幂等、部分失败、prune、保护和数量边界 |
| 真实搜索/存储/同步 | 21 项通过，44 次 HTTP 请求 | ONNX、PostgreSQL、Qdrant、OSS；包含上传和 URL 两个 batch 入口 |
| 真实页面 | 通过 | 图库、类别/场景导出与上传预览、单图/列表/批量搜索；无 mock |
| 清理核对 | 通过 | 本次测试对象无残留，原有配置保持不变 |

真实搜索与配置接口共 **59 项通过**。自动化中的网络/仓储替身与真实接口验收分别统计。

## 真实环境与关键断言

验收使用单独的 `http://127.0.0.1:18320` 实例，`NACOS_ENV=test`、`NACOS_ENABLED=0`。连接本机 PostgreSQL `image_embedding`、Qdrant 和测试 OSS；模型输出 1024 维，实际加载 CUDA provider。原有 `8320` 服务未被本次验收脚本停止，结束时健康检查正常。

- 类别/场景原样 GET 备份可往返；真实页面上传分别显示 602 / 3 项无变化、零错误，确认按钮禁用，随后取消。
- 类别新建、更新、重复提交、大小写判重、部分失败、文件内父级解析、移动/缺父级/循环拒绝均通过。两个接口的显式 `schema_version` / `kind` 校验通过；类别 5000/5001、深度 32/33、场景 200/201、每场景 labels/rules 5000/5001 的边界均已实际请求。
- 场景、label、rule 按编码创建和幂等通过；省略列表保持不变，显式 prune 的删除顺序与引用保护通过；默认场景保护通过。
- 已知图库原图与单张接口、批量接口的命中 ID 和顺序一致；批量 1/30 张成功、31 张拒绝；混入空文件/坏图只影响对应项；15 MiB 上限、共享参数及 JSON 未知字段均核对。
- 真实新建独立图片，经 ONNX 提取向量并写 OSS/Qdrant，详情显示向量 `synced`，按测试场景可搜索到。导入类别菜系后实际图片的场景归属与向量 payload 同步改变；删除测试规则后分页执行场景同步至 `done=true`，对应场景搜索成功返回空结果。
- 真实浏览器的单图命中 3 条，批量 2/2 成功、每图 3 条；网格/列表展示正常。602 类别的 ZIP 已解压检查，包含 JSON、TXT、SVG、PNG、README。
- PNG 编码失败用受控 Canvas 故障复验：ZIP 仍含 JSON、TXT、SVG、README，不含 PNG，页面显示部分文件没有生成的提示；尺寸缩放与不可读大图边界另有单测。

## 联调发现及处理

1. **同图分数微超 1 的显示问题已修复。** 实际 Qdrant 分数为 `1.0000001192092896`，原前端显示“未返回分数”。前端仅容忍 `1e-6` 的浮点误差并收敛到 `[-1, 1]`，明显越界及非数值仍拒绝。单测与真实页面复验通过，同图显示 `100.0%`。
2. **配置文件元数据已严格校验。** 显式提供的版本只接受整数 `1`；`null`、布尔、浮点、字符串及不匹配的 `kind` 返回 422。GET 响应不含这些字段时仍可导入。
3. **本机 fake-IP DNS 影响 URL 下载。** 原 YAML 的 `trusted_networks=[]`，OSS 域名解析到 `198.18.0.61` / `2001:2::3d`，默认策略返回 `FORBIDDEN_HOST`。在临时验收进程内，按本次实际 DNS 结果设定精确的 `/32`、`/128` 信任范围后，真实 OSS URL 下载检索通过；无效协议为 `INVALID_URL`，回环地址仍为 `FORBIDDEN_HOST`。本次没有修改用户 YAML 或原服务的下载配置；原配置下的 fake-IP 限制仍存在。
4. **Global 实际上游超时。** 单次真实兜底请求使用 `top_k=1`，上游返回 `UPSTREAM_TIMEOUT`；业务接口保持 HTTP 200、`source=empty`、`fallback.status=failed`，说明与 trace 均保留。此结果验证了失败兜底，未取得 Global 成功命中；成功命中分支由受控浏览器/接口测试覆盖。

真实依赖宕机、写入中断等故障分支使用自动化注入验证，未通过停止现有 PostgreSQL/Qdrant 来制造故障。

## 数据清理与证据

独立验收前缀创建的类别、场景、label、rule、图片、入库任务均已清理。两张临时图片的详情和 Qdrant 点均复核为 404，删除接口返回对象存储清理 `done`。最终计数为 **602 类别、3 场景、47 图片、47 向量点**；602 个原有类别、3 个原有场景的配置字段与备份一致。两个临时验收进程和独立浏览器会话均已关闭。

以下为本机证据，已加入 Git 忽略规则，不随代码提交：

- [配置接口逐项报告](../backend/output/acceptance/acceptance_cfg_5e16f89a0c/report.json)、[原有配置对比](../backend/output/acceptance/acceptance_cfg_5e16f89a0c/baseline-config-comparison.json)。
- [搜索/存储/同步最终复验](../backend/output/acceptance/acceptance_search_1158372400/report.json)、[最终清理核对](../backend/output/acceptance/final-cleanup-audit.json)、[Global 实际响应](../backend/output/acceptance/global-fallback-real-result.json)。
- [浏览器回归完成状态](../frontend/test-results/final-acceptance/.last-run.json)。
- [PNG 失败兜底追加回归状态](../frontend/test-results/png-fallback/.last-run.json)。
- [真实图库截图](../output/playwright/live-library-18320.png)、[同图搜索截图](../output/playwright/live-search-single.png)、[批量搜索截图](../output/playwright/live-search-batch.png)。
- [类别上传预览](../output/playwright/live-category-roundtrip-preview.png)、[场景上传预览](../output/playwright/live-scene-roundtrip-preview.png)、[真实类别 ZIP](../output/playwright/live-category-tree.zip)。
