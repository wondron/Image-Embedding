# R0 · Import 冻结图（backend rearch）

> **状态**：文档刀 · **不改代码行为** · **不 commit**  
> **基线方案**：`docs/backend-rearch-target-layout-plan.md`  
> **实现范围本波**：仅 R0 + R1（Wire）；**不启动 R2–R5 目录搬迁**  
> **日期**：2026-11-05（America/Denver）

---

## 1. 目的

在目录大搬（R2–R5）之前冻结「现状 `app.*` 包根 → 目标包根」对照，避免后续刀漂移。  
**R0 本身不移动任何模块**；短命 shim 策略仅在 R4→R5 使用。

---

## 2. 现状包根 → 目标包根

| 现状包 / 路径 | 目标包 / 路径 | R0 动作 | 后续刀 |
|---|---|---|---|
| `app.adapter.http`（endpoints / deps / router） | `app.api`（`api/v1/*`, `api/deps.py`, `api/router.py`） | 冻结 | R4 |
| `app.adapter.http.middleware` | `app.core.middleware`（或 `app.api.middleware`） | 冻结 | R4 |
| `app.adapter.http.exception_handlers` | `app.core.exceptions` + bootstrap 注册 | 冻结 | R4 |
| `app.adapter.http.schemas` | `app.schemas` | 冻结 | R4（**R1 已改 Search/List wire 形状，仍在旧路径**） |
| `app.adapter.http.services`（HTTP 胶水） | 薄留 `app.api` 或并入 `app.services` facade | 冻结 | R4 |
| `app.application.retrieval` | `app.services.retrieval` | 冻结 | R3（**R1 Wire 改行为，路径不动**） |
| `app.domain.retrieval.ports` | `app.repositories.ports` 或随 repo/client Protocol | 冻结 | R3 |
| `app.domain.retrieval.{scenes,score_threshold,embedding_validate,errors,…}` | `app.services.retrieval` / `app.core` 小模块 | 冻结 | R3 |
| `app.adapter.persistence.pg_connection` + 会话 | `app.db.session` | 冻结 | R2 |
| `app.adapter.persistence.pg_common` | `app.db.base` + `app.db.types` | 冻结 | R2 |
| `app.adapter.persistence.pg_*` | `app.repositories.*` | 冻结 | R2 |
| `app.adapter.persistence.qdrant_store` | `app.clients.qdrant` | 冻结 | R3 |
| `app.adapter.persistence.object_storage` | `app.clients.s3` | 冻结 | R3 |
| `app.adapter.persistence.flyb_fallback` | 删或 `app.clients.flyb_stub`（检索禁引用） | 冻结 | R5 |
| `app.adapter.embedding.embedder_port_impl` | `app.clients.inference`（不重写 ONNX） | 冻结 | R3 |
| `app.domain.common.embedding` | `app.clients` 内部 onnx 包装 | 冻结 | R3 |
| `app.domain.common.image_utils` | `app.utils.image` | 冻结 | R3 |
| `app.domain.common.{timer_utils,tokenizer,gen_foodtree}` | `app.utils` 或 predict 岛内 | 冻结 | R3 |
| `app.domain.common.flybSearch` | 仅 predict 岛可达；检索禁 import | 冻结 | R3 |
| `app.domain.food_service` | `app.services.predict.food_service` | 冻结 | R3 |
| `app.domain.pipeline` | `app.services.predict.pipeline` | 冻结 | R3 |
| `app.bootstrap`（包） | **保留** `app.bootstrap/`（G2）；可选根 `bootstrap.py` re-export | 冻结 | R4 |
| `app.worker` | `app.workers` | 冻结 | R4 |
| `app.core.config` / `app.core.observability` | `app.core` 下微调 | 冻结 | R2–R4 |
| （无） | `app.models`（12 表 declarative） | 冻结 | R2 **new** |
| `alembic/` | **保留目录名**（G1；逻辑=migrations） | 冻结 | — |
| `backend/config/`（onnx 等资产） | **保留**（G3）；`configs/nacos/` 仅样例 | 冻结 | — |
| `backend/main.py` → `app.bootstrap.application.create_app` | 入口尽量一行不变 | 冻结 | R4 |

---

## 3. Shim 策略（R4→R5，非本刀）

- **短命 re-export**：旧路径模块在 R4 搬迁后可留一层 `from app.<new> import *` / 显式符号，保证算法脚本与未改 tests 暂不炸。
- **删除窗口**：集中在 **R5**；R5 验收要求静态 grep 旧路径为 0（允许 docs 历史叙述）。
- **R0 / R1 禁止**：不新增 shim 包、不搬家、不改 import 图（R1 仅改 Search/List **响应 dict 形状**）。

---

## 4. R1 触及（行为变、路径不变）

| 文件 | 说明 |
|---|---|
| `app/application/retrieval/search_image.py` | §6.1 P1–P9 envelope+hit |
| `app/application/retrieval/list_images.py` | §6.1 P1–P2/P5/P9/P11 |
| `app/adapter/http/schemas/retrieval.py` | List/Search DTO 对齐权威字段 |
| HTTP `services/retrieval.py` | 透传 usecase dict（已 as-is；R1 确认无二次改名） |

P12：不新增 API。

---

## 5. 可选清单脚本

`backend/scripts/list_app_imports.py` — 扫描 `from app.` / `import app.`（只读，供 R2+ 对照）。

---

*R0 结束 · 目录刀等 R2。*
