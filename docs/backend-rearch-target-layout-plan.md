# 后端目录重梳 + §6.1 Wire 整顿方案

> **状态**：仅方案文档 · **未动代码** · **不 commit**  
> **读者**：总裁 / PM  
> **仓**：`D:\01-code\Image-Embedding` · 后端 `backend/`  
> **基线 HEAD**：`2eaff7f`（祖先含 DDD D1–D6 @ `e8a7d4d`）  
> **契约 SSOT**：`docs/image-search-pg-v01-data-contract.md` §6.1  
> **总裁已拍（本派工）**：§6.1 P1–P12 **全按建议一刀切**；目标树如下标准 FastAPI 分层；算法权重/推理不重写，走 `clients/`。

---

## A. 现状摘要

### A.1 当前分层（DDD D1–D6 落点）

| 层 | 路径 | 职责 / D 刀落点 |
|---|---|---|
| HTTP 薄适配 | `app/adapter/http/` | 路由、schemas、HTTP services、middleware、DI（D3 变薄） |
| 用例 | `app/application/retrieval/` | index / search / list / delete / recompute / storage_upload（D1） |
| 持久化 | `app/adapter/persistence/` | `pg_*`（SQLAlchemy Core 手写 SQL）、`qdrant_store`（D5 自 domain 迁入）、`object_storage`、FlyB stub |
| 嵌入端口实现 | `app/adapter/embedding/` | `EmbedderPortImpl` 包 `OnnxEmbedding`（不重写算法） |
| 领域纯函数 / 端口 | `app/domain/retrieval/` | ports、errors、scenes、score_threshold、embedding_validate… |
| **Predict 岛（D6 Option A）** | `app/domain/food_service.py` + `app/domain/pipeline/` + `app/domain/common/` | 仅 lifespan / predict / health 引用；**不得**进 retrieval_stack |
| 组装 | `app/bootstrap/` | `create_app`、lifespan、`retrieval_stack`、state |
| Worker | `app/worker/` | `process_deletions`、`recompute_scenes`（经 stack，非直构 adapter） |
| 配置 / 日志 | `app/core/` | settings、nacos、loader、observability |

入口：`backend/main.py` → `app.bootstrap.application.create_app`。

### A.2 与目标树的结构性差距

| 目标 | 现状 | 差距性质 |
|---|---|---|
| `app/api/` | `adapter/http/` | **rename/move** |
| `app/schemas/` | `adapter/http/schemas/` | **move**；检索响应多为裸 dict，schema 落后于 PG |
| `app/repositories/` | `adapter/persistence/pg_*` | **move + 命名**；尚无独立 ORM `models/` |
| `app/models/` 12 表 | **无**（Alembic 手写 DDL，`target_metadata=None`） | **新增**（建议 declarative 镜像契约表，见 B/C） |
| `app/db/` | 会话逻辑散落 `pg_connection` / `pg_common` | **抽出** |
| `app/services/` | `application/retrieval` + `adapter/http/services` + domain 纯函数 | **合并/划界** |
| `app/clients/` | qdrant / s3 / embedder / FlyB stub 在 persistence 或 embedding | **迁入 clients** |
| `app/workers/` | `app/worker/`（单数） | **rename** |
| `app/utils/` | `domain/common/image_utils` 等 | **move** |
| `app/bootstrap.py` | `app/bootstrap/` 包 | **拍板见 G：保留包 `bootstrap/`，根模块可薄 re-export** |
| `migrations/` | `alembic/` | **可选 rename** 或保留 alembic + 文档说明 |
| `configs/nacos/` | `config/`（模型/字体/yaml）+ `core/config/nacos.py` | **配置资产 vs 代码** 分开说明 |
| `pyproject.toml` / `.env.example` | 仅 `requirements.txt` + `.env` | **本波可选补**，不阻塞目录刀 |

### A.3 §6.1 Wire 缺口与契约对照（同源契约 §6.1 主线）

- **Search**（`application/retrieval/search_image.py`）：仍吐 FlyB 旧壳（`image_url`/`thumbnail_url`、`site_name`、`text_snippets`、`details.*`、`title`）；envelope 键为 **`result`**；空结果时 `result` 为嵌套对象 `{results,status,message}`；`source=self|empty` 已有；`scene_label` 现为 **整行 dict**。
- **List**（`list_images.py`）：已有权威 `original_url`/`thumb_url`，但仍双吐别名 + `title` + `qdrant_point_id`。
- **Schemas**（`adapter/http/schemas/retrieval.py`）：List DTO 仍偏旧 MVP（`id: int`、`qdrant_point_id`、无权威 URL 字段）。
- FlyB：**检索路径已断**（D5/D6 stub）；§6.1 还禁 **响应壳字段**（与「不调外搜」是两件事）。

---

## B. 现状 → 目标路径映射表

图例：**M**=move（改路径/包名，逻辑基本不动）· **R**=rewrite/重塑 wire 或组装 · **N**=new · **D**=delete（实现刀末段）· **K**=keep 边界

| 现状 | 目标 | 动作 | 说明 |
|---|---|---|---|
| `adapter/http/endpoints/*` | `api/v1/*.py`（按资源：`retrieval.py`、`predict.py`、`health.py`、`tools.py`…） | M | 前缀 `/api` 可保留；健康可 `api/health.py` |
| `adapter/http/dependencies.py` | `api/deps.py` | M | |
| `adapter/http/router.py` | `api/router.py` | M | |
| `adapter/http/middleware.py` | `core/middleware.py`（或 `api/middleware.py`） | M | 建议进 `core/` 对齐目标 |
| `adapter/http/exception_handlers.py` | `core/exceptions.py` + 注册留 bootstrap | M/R | 异常类与 handler 同区 |
| `adapter/http/schemas/*` | `schemas/*` | M/R | **R1 重写 Search/List 响应模型对齐 §6.1** |
| `adapter/http/services/*`（HTTP 胶水） | 薄留在 `api/` 或并入 `services/` 的 facade | M | 推荐：HTTP 解析留 `api/`，业务只调 `services/` |
| `application/retrieval/*.py` | `services/retrieval/*.py` | M | usecase 改名 services，行为不变（除 Wire） |
| `domain/retrieval/ports.py` | `repositories/ports.py` 或拆到 repo/client Protocol | M | 标准树无 domain；端口跟实现边界走 |
| `domain/retrieval/{scenes,score_threshold,embedding_validate,errors,…}` | `services/retrieval/` 或 `core/` 小模块 | M | 纯函数跟检索业务走，不进 clients |
| `adapter/persistence/pg_connection.py` + 会话 | `db/session.py` | M | |
| `adapter/persistence/pg_common.py`（类型/工具） | `db/base.py` + `db/types.py`（ltree 等） | M/R | |
| `adapter/persistence/pg_images.py` 等 | `repositories/images.py`、`scenes.py`、`embeddings.py`、`deletions.py`、`metadata.py` | M | 可先整文件搬再逐步拆 |
| `adapter/persistence/qdrant_store.py` | `clients/qdrant.py`（`QdrantStore` + 薄 `QdrantVectorStore`） | M | **K**：算法侧类名/方法签名尽量不变 |
| `adapter/persistence/object_storage.py` | `clients/s3.py` | M | |
| `adapter/persistence/flyb_fallback.py` | **删或** `clients/flyb_stub.py` 至多一文件 | D/M | 检索禁引用；末段删更干净 |
| `adapter/embedding/embedder_port_impl.py` | `clients/inference.py`（或 `clients/embedding.py`） | M | **不重写** Onnx 推理；只搬壳 |
| `domain/common/embedding.py` | `clients/inference_onnx.py`（内部）或随 inference 包 | M | 算法权重文件路径仍指 `config/model/` |
| `domain/common/image_utils.py` | `utils/image.py` | M | |
| `domain/common/{timer_utils,tokenizer,gen_foodtree}` | `utils/` 或 predict 岛内 | M | predict 专用可跟岛 |
| `domain/common/flybSearch.py` | **保留仅 predict 岛可达** → `services/predict/_legacy_flyb.py` 或暂随岛；**检索禁 import** | M/K | 非检索路径；不在 list/search 出现 |
| `domain/food_service.py` | `services/predict/food_service.py` | M/K | D6 边界不变 |
| `domain/pipeline/*` | `services/predict/pipeline/*` | M/K | a/b/c/d 阶段原样搬 |
| `domain/retrieval/*` 搬空后 | 删除空包 | D | |
| `bootstrap/*` | 保留 `app/bootstrap/` 包；可选根 `bootstrap.py` re-export `create_app` | M | 见 G 拍板默认 |
| `worker/*` | `workers/*` | M | 入口改为 `python -m app.workers.process_deletions` |
| `core/config/*`、`core/observability/*` | `core/` 下微调（enums/exceptions 补齐） | K/N | |
| （无） | `models/` 12 表 ORM | N | 见 §C.2；首刀可「声明式镜像 + 不强制 Alembic autogenerate」 |
| `alembic/` | 建议 **保留目录名 `alembic/`**（工具习惯）；文档写「即 migrations」；或物理 rename + 改 `alembic.ini` | K/可选 M | 风险见 F |
| `config/`（模型 onnx、yaml、字体） | **保留 `config/`**（运行资产）；目标文案 `configs/nacos/` 指 **Nacos 配置约定/样例**，可新增 `configs/nacos/` 放样例 JSON，勿与 onnx 大文件混放 | K/N | |
| `tests/` | `tests/` 改 import 路径 | R | 随每刀改 |
| `requirements.txt` | 暂留；另补 `pyproject.toml` / `.env.example` 可并行小刀 | N 可选 | |

---

## C. Predict / Pipeline / 算法岛边界（新树）

### C.1 推荐落点（保留 D6 Option A）

```
app/
  services/
    predict/                 # 岛根：仅 api/predict、lifespan、health 可依赖
      food_service.py
      pipeline/              # a_onnx_segment / b_getGlobalData / c_embedding / d_vector_index
      ...
    retrieval/               # 图搜图用例（原 application.retrieval）
  clients/
    inference.py             # EmbedderPortImpl + 对 OnnxEmbedding 的包装（检索用）
    qdrant.py
    s3.py
  api/v1/predict.py          # 只调 services.predict，不调 retrieval_stack 内推理
```

**硬边界（实现刀验收）**：

1. `services/retrieval/**`、`repositories/**`、`workers/**`、`bootstrap/retrieval_stack.py` **禁止** import `services.predict` / `pipeline` / `flybSearch`。
2. 检索向量 **只**经 `clients.inference`（现 EmbedderPortImpl）。
3. `QdrantStore` 留在 `clients/`，算法脚本若有 `from app...QdrantStore` → 提供短期 shim 或改一处导入（R5）。
4. Predict 响应 wire **不在本波 §6.1 范围**（§6.1 管 Search/List）；predict 壳可暂原样。

### C.2 `models/` 与「无 ORM 现状」

现状是 **SQLAlchemy Core + 手写 SQL + Alembic 手写 DDL**，不是 12 个 Declarative 模型。  
**本方案默认（按最标准）**：

- **新增** `app/models/`：12 表 declarative（字段对齐契约 §2），供类型、未来 autogenerate、文档。
- **首波 repositories 仍可跑现有 SQL**（行为零回归）；模型与 repo 并行，不强制一刀把所有 SQL 改成 ORM。
- Alembic **本波不改历史 revision**；`env.py` 的 `target_metadata` 可先仍为 `None`，或挂上 metadata 但不启用 autogenerate。

---

## D. §6.1 Wire P1–P12 落地清单

> 总裁派工：**全按建议一刀切**。下表「契约要求」= 建议即目标。

| # | 契约要求 | 现状 | 本波 | 触及文件（实现时） | 验收点 |
|---|---|---|---|---|---|
| **P1** | 权威 URL 仅 `original_url` / `thumb_url`（https） | Search 只吐旧名；List 已有权威 | **改** | `services/retrieval/search_image.py`、`list_images.py`、`schemas/retrieval.py` | Search/List hit 必有权威二字段 |
| **P2** | 禁 `image_url` / `thumbnail_url` 别名 | Search/List 仍有 | **改** | 同上 + FE 同步（FE 另刀） | JSON 中零匹配旧键 |
| **P3** | Envelope 数组键 **`items`**（恒 array） | Search 用 `result` | **改** | `search_image.py`、schemas、HTTP 层若有透传 | 有命中：`items` 为 array；无 `result` |
| **P4** | 空：`source=empty` + `items:[]`；可选顶层 `message`；禁嵌套空壳对象 | 空时 `result={results,status,…}` | **改** | `search_image.py`（删 `_empty_envelope` 嵌套用法） | `source=empty` 且 `items==[]` |
| **P5** | 禁 `title` | Search/List 用 category 冒充 title | **改** | search/list hit 构造 | 无 `title`；展示靠 `category`/`scene_label` |
| **P6** | 禁 `description`（hit） | 已无 | **已 OK** | — | 回归勿新增 |
| **P7** | 禁 FlyB 壳：`site`/`site_name`/`page_url`/`snippets`/`text_snippets`/`details` | Search 仍吐 | **改** | `_hit_from_row` | hit 无上述键 |
| **P8** | `scene_label`: **string \| null** | 现返回 scene_labels **整行 dict** | **改** | `search_image.py`（取 `name`）；可选 repo 仍返回 dict 由 service 压成 string | 类型为 string 或 null |
| **P9** | `outputs_heating` **仅 envelope 布尔**；hit 仅在 true 时带 `heating_group`/`food_state` | envelope 未稳定回显；hit 逻辑近似 | **改** | `search_image.py`（及 list 加热分支） | envelope 有布尔；非加热 scene hit 无加热字段 |
| **P10** | 一刀切（BE+FE 同发） | — | **策略** | 发布说明；不设别名窗 | 无双源字段 |
| **P11** | List 禁 `qdrant_point_id` | List 仍吐（=id） | **改** | `list_images.py`、schemas | List item 无该键 |
| **P12** | `external_candidates` API **本波不定实现** | 表有、API 无 | **本波不做** | — | 目录预留 `api/v1/external_candidates.py` 空位即可，不写路由 |

**Search envelope 目标形（实现后）**：

```json
{
  "ok": true,
  "trace_id": "...",
  "source": "self",
  "scene": "general",
  "outputs_heating": false,
  "items": [ { "id": "...", "original_url": "https://...", "thumb_url": "https://...", "similarity": 0.9, "...": "..." } ]
}
```

空结果：`"source":"empty","items":[],"message":"..."`（HTTP 200，`ok=true`）。

---

## E. 分刀顺序（批准后再实现；每刀可独立交测优先）

| 刀 | 内容 | 交测重点 | 依赖 |
|---|---|---|---|
| **R0** | Import 冻结图：列出现状包 → 目标包对照；加临时 `app/_rearch_shims` 策略说明（先文档后代码）；CI/本地收集 `from app.` 清单 | 仅文档/脚本，不改行为 | — |
| **R1** | **§6.1 Wire 一刀切**（可在旧路径上先改行为）：Search/List hit+envelope；schemas 对齐；禁 FlyB 壳；P1–P11 | 契约字段单测 + 手测 search 空/有、list 一页；**FE 需同发** | 总裁已拍 P1–P12 |
| **R2** | `db/` + `models/`（12 表声明）+ `repositories/` 自 `pg_*` 迁入；`alembic` 路径策略落地 | 入库/list/search 读库回归 | R1 可并行，但建议 R1 先合减少双线冲突 |
| **R3** | `services/`（retrieval usecase 迁入）+ `clients/`（qdrant/s3/inference）；predict 岛迁 `services/predict/**` | retrieval 与 predict **分测**；确认 retrieval 无 predict import | R2 |
| **R4** | `api/` 自 `adapter/http` 迁入；`workers/` rename；`bootstrap` 组装改 import；`main.py` 入口不变或改一行 | `create_app` smoke；worker `--help`/干跑 | R3 |
| **R5** | 删空包与兼容 shim；删 `flyb_fallback`（若已无引用）；更新 tests import；补 `.env.example`（可选 pyproject） | 全量 `tests/` + 静态 grep 旧路径为 0 | R4 |
| **交测** | 交 boboji / 测试 bot：D3–D6 行为 + §6.1 字段矩阵 + predict 岛未破 | 报告 PASS/FAIL | R5 |

**建议优先序**：若希望「前端尽快解耦旧壳」，**R1 可先于目录大搬**（同批准下单独交刀）；目录重梳 R2–R5 紧随，避免长时间双结构。

---

## F. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚 |
|---|---|---|---|
| Import 爆炸（全仓改 `app.adapter`/`app.domain`） | 启动失败、测试红 | 分刀；短命 shim（旧路径 re-export 新路径）集中在 R4→R5 删除 | `git revert` 单刀 commit（实现阶段才 commit） |
| 算法 / 脚本仍 import 旧 `QdrantStore`/`OnnxEmbedding` 路径 | 补向量、离线脚本挂 | clients 保持类名；R5 前留 shim | 恢复 shim 文件 |
| Alembic 改名 `migrations/` | `alembic.ini`、Docker、文档全改 | **默认不改目录名**，只在方案认作 migrations | 不改则无此风险 |
| `models/` 与手写 SQL 双轨 | 字段漂移 | 模型注释钉契约 §2；本波不做 autogenerate | 删 models 不影响运行 |
| §6.1 一刀切破坏 FE | 搜图/图库白屏或无图 | **BE/FE 同发**（P10）；发布前对 FE `normalize`/`pick*Url` 对照 §6.1.4 | 临时双吐仅作紧急回滚（需总裁另批；本方案默认不做） |
| Predict 岛误被 retrieval 引用 | 边界回退、FlyB 壳回流 | R3 加 import-linter 或 grep CI | 挪回隔离 |
| 工作区已有未提交契约改动 | 与方案文档交织 | 方案独立文件；不碰契约 commit | — |

---

## G. 明确约束与默认拍板

1. **本文件仅整顿方案**；**批准前不实现、不 commit、不自测跑服务**（本波只读核实）。  
2. **算法权重 / ONNX 推理不重写**；只搬包装到 `clients/`。  
3. **§6.1 与目录重梳同一批准波次**；实施时可 R1 先行交测，但同属本方案。  
4. **P12 external_candidates API：本波不做实现**（只定 wire 形状已在契约）。  
5. **默认拍板（按最标准 / 少问总裁）**：  
   - **G1** 保留物理目录名 `alembic/`（逻辑=migrations）。  
   - **G2** 保留 `app/bootstrap/` 包；不强制单文件 `bootstrap.py`（若目标图要单文件，可加一层 re-export，不删包）。  
   - **G3** 运行资产目录保持 `backend/config/`（onnx 等）；`configs/nacos/` 仅作样例/文档向，不搬模型。  
   - **G4** Predict 岛 → `services/predict/`（含 `pipeline/`），不放 `clients/`。  
   - **G5** R1 Wire 与 FE 同发；后端不设别名兼容窗。

---

## 附录 · 本机核实命令摘要

```text
HEAD: 2eaff7f（含 e8a7d4d D3–D6）
dirty: docs/image-search-pg-v01-data-contract.md（已修改未提交）
untracked: docs/_tmp_s61_extract.txt
backend 包管理: requirements.txt（无 pyproject.toml / .env.example）
```

---

*方案结束 · 待总裁/PM 批「按此实施」后再开 R0/R1 实现刀。*
