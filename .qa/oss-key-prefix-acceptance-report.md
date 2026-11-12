# OSS key_prefix 独立验收报告（合同 §8）

- **总体判定：ACCEPTED**（PASS 8 / FAIL 0 / SKIP 1）
- 记录时间：2026-11-11 19:00:37 America/Denver (MST, UTC-7)
- 仓库：`C:\Users\47202\PROJECT\Image-Embedding`（machineId `fada9197-1d47-44f2-8e45-fdb157d8e181`）
- 规格：`/workspace/deliverables/oss-key-prefix-contract.md`（§1/§2 更严规则优先于实现方单测）
- 实现交接：`/workspace/deliverables/oss-key-prefix-backend-implementation.md`

实现方单测已复跑并记录，**不作为 §8 PASS**。本报告依据独立断言（`.qa/oss-key-prefix/test_oss_key_prefix_acceptance.py`，31 项，30 通过，1 跳过）。

## 九点判定

| # | 项 | 判定 | 分类 | 摘要 |
|---|----|------|------|------|
| 1 | 缺省兼容 | **PASS** | 产品 | 省略 / null / `""` / 仅斜杠或空白时，新键仍为 `original/{id}.{ext}` 等，URL 为 `{public_base_url}/{rel}`。 |
| 2 | 正向前缀 | **PASS** | 产品 | `ImageEmbedding` 及首尾斜杠/反斜杠/全角斜杠等价输入规范化后，键与 URL 带前缀、中间无 `//`、不缺 `/`。mock 与 `use_boto3=true`（隔离 FakeS3Client，无网络）键一致。 |
| 3 | 规范化报错 | **PASS** | 产品 | `..`、`.`、精确大小写保留段、中间空格、超长、非字符串类型均 `ValueError`，不静默改写。`key_prefix` 不走 `_text()`。`Original` 合法。 |
| 4 | URL 不加倍 | **PASS**（观察） | 产品观察，不强制失败 | `public_base_url` 已含 `ImageEmbedding` 再配相同 `key_prefix` 时，实际 URL 加倍。加载期拦截为规格待定，未拦截。 |
| 5 | 旧键兼容 | **PASS** | 产品 | 启用前缀后库册 URL 仍为旧完整 URL；按该 URL 读 / 删 / reembed 打到旧键；删除不会按当前前缀重算去动新键上的诱饵对象。 |
| 6 | 新写入 | **PASS** | 产品 | 启用前缀后 `IndexImageUseCase` 响应与库册 URL 带前缀；按存储 URL 删除会删掉带前缀对象。 |
| 7 | 重复入库 | **PASS** | 产品 | 前缀切换后对同一图片再上传，首次 URL 保留。产生了新键孤儿对象（已记录；清理孤儿不是必须通过项）。 |
| 8 | 前端 | **SKIP** | 环境限制，非产品缺陷 | 本刀未改前端（扫描无 `key_prefix` / `ImageEmbedding/`）。无合法存储/CDN（生效 YAML 仍为 `use_boto3: false` 与示例 `cdn.example.local`），未假装打开成功，未测签名 URL。 |
| 9 | 回归 | **PASS** | 产品 | 扩展名规则、thumb 固定 `.webp`、staging 键形态、`uuid_from_sha256`（SHA-256 前 16 字节）均不变。 |

## Git / 工作区

- **HEAD** `8232b2e8d8a9397c8cc7684fb257ff689574a593`（与实施方声称一致）。提交说明：`sql qdrant 操作优化`，作者 wondron / zhuo.wang@robam.com，提交时间 2026-11-11 16:42:48 America/Denver。
- 本刀相关文件仍为未提交状态。本验收**未 commit / push**，未改前端，未改生产业务代码以掩盖失败。
- 并行未提交改动已保护、哈希在收尾时与基线一致：
  - `frontend/src/entities/image/api.test.ts` `BDF6D0E3…DABD0A`
  - `frontend/src/entities/image/api.ts` `FB8AEF3D…6308`
  - `frontend/src/features/library-list/useLibraryList.ts` `4BB7FDBB…3285E`（路径是 `library-list`，不是 `library`）
  - `frontend/src/shared/lib/index.ts` `BA50663D…12C42`
  - `frontend/src/shared/lib/preference.ts` `A37F8F90…EA4B65`
  - `frontend/src/features/library-filters/*` 四个文件哈希未变
- 工作区另有交接未列出的未跟踪文件：`backend/scripts/check_oss_connectivity.py`、`backend/scripts/test_check_oss_connectivity.py`。**未执行、未修改**（可能触及真实 OSS）。
- 本验收仅新增 `.qa/oss-key-prefix/` 下的独立断言、日志与报告。

### settings.py 窗口内变化（如实记录，未回改）

验收过程中 `backend/app/core/config/settings.py` 的 mtime 变为 **2026-11-11 18:59:30 America/Denver**，长度 14464 → 14432，SHA256 `1EAE9D59…2ED3D6` → `8F7ED0A3…DBE906`。本验收**没有编辑该文件，也没有回改**（避免覆盖并行编辑）。对照窗口内快照，变更是将非字符串 `ValueError` 从多行 `raise` 收成单行，语义不变：仍拒绝非字符串标量，仍不把 `key_prefix` 交给 `_text()`。已对收尾哈希复跑独立套件，结果相同。更早一次会话记录的 `key_prefix.py` / `test_storage_keys.py` 哈希与当前磁盘（含 LF 规范化）也不一致；那两份文件的 mtime 为 2026-11-11 18:54:33 America/Denver。**判定以收尾时磁盘实文件为准。**

### 收尾刀文件 SHA256

| 文件 | SHA256 |
|------|--------|
| `backend/app/core/config/key_prefix.py` | `D5003C2454F0F9EA57B9CEF2C588F712BC8B411102D5195F081C2D240F8690B6` |
| `backend/tests/test_storage_keys.py` | `B5B49CA1E56415ED41F3AD1E40B3E30511E666E17CA7CF6C3B0E122980A0E4CF` |
| `backend/app/core/config/settings.py` | `8F7ED0A351430575AE5E9159D1065DE8BACEF2AD73115DBD13FE5818D5DBE906` |
| `backend/app/services/retrieval/storage_keys.py` | `6DCE55EF6A3B22E994B43150FE95EE75B315D4E602298FA41436D1C5675D6233` |
| `backend/app/clients/image_storage.py` | `D1054D626A76F58DEFBAEC0951F85361F78DDE87812AF734F8861A625C440501` |
| `backend/app/services/retrieval/storage_upload.py` | `E926CFA5FF1739C9D63EAC534F78AFFBF408EEE72FBE286009EB13E9311164FD` |
| `backend/app/repositories/ports.py` | `C3DEA8B9BA6FC1425F2DF32403A8CC3B9C0DF06772DA7A208548C0C183245548` |
| `docs/image-search-pg-v01-data-contract.md` | `AFC2268FA552B707F95EC88248F667C64F7B9D277E5AB8D9585E2B409D95CF20` |
| `README.md` | `BC89ED289BA471E84F703E7130E4E798D01BB3A68FADDBDE0565359814079BD8` |

## 命令与结果

Python：`D:\99-tools\anaconda\envs\onnxgpu\python.exe`

### 实现方单测（仅记录，非 §8 PASS）

```
cwd: C:\Users\47202\PROJECT\Image-Embedding\backend
D:\99-tools\anaconda\envs\onnxgpu\python.exe -m unittest tests.test_storage_keys -v
```

- 退出码 0；**Ran 11 tests in 0.001s, OK**（0 跳过）。
- 实施方声称「16 通过、1 跳过」与磁盘实文件不符：当前 `backend/tests/test_storage_keys.py` 只定义并跑了 11 个用例。
- 日志：`.qa/oss-key-prefix/implementer-unittest.log`

### 独立 §8 断言

```
cwd: C:\Users\47202\PROJECT\Image-Embedding
D:\99-tools\anaconda\envs\onnxgpu\python.exe -m unittest discover -s .qa\oss-key-prefix -p test_oss_key_prefix_acceptance.py -v
```

- 第一次：退出码 0；Ran 31 tests in 1.076s, OK (skipped=1)。日志：`.qa/oss-key-prefix/independent-acceptance.log`
- `settings.py` 窗口内变化后复跑：退出码 0；Ran 31 tests in 0.693s, OK (skipped=1)。日志：`.qa/oss-key-prefix/independent-acceptance-rerun.log`
- 跳过的一项是 §8.8 打开 URL（无合法 CDN）。

## 关键观察（不含密钥）

### §8.4 加倍 URL

- `public_base_url` = `https://cdn.example.local/ImageEmbedding`
- `key_prefix` = `ImageEmbedding`
- 实际 `public_url` = `https://cdn.example.local/ImageEmbedding/ImageEmbedding/original/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.png`
- 加载期未拦截。规格待定，不作为失败。

### §8.7 孤儿对象（隔离 mock，已随临时目录清理）

- `ImageEmbedding/original/117b65db-6618-04ab-abe1-f7225363e063.png`
- `ImageEmbedding/thumb/117b65db-6618-04ab-abe1-f7225363e063.webp`
- 首次入库 URL 仍保留为 `{base}/original/{id}.png`。清理孤儿不是必须通过项。

### §8.2 use_boto3 路径

本机可 `import boto3`（1.43.124），但生效 YAML 仍为 `use_boto3: false`。为避免改坏生效配置、避免写真实 OSS，`use_boto3=true` 路径用 `S3ImageStorage.__new__` + 进程内 `FakeS3Client`（无网络）与 mock 对键。

## 生效配置（只读核对）

- `backend/config/param/config.yaml` 被 gitignore；mtime **2026-11-11 18:49:41 America/Denver**（早于本验收写入，验收未改运行值）。
- 运行键中**没有** `key_prefix`；仅第 83 行注释示例 `# key_prefix: ""`。
- `use_boto3: false`；`public_base_url` 为示例 `https://cdn.example.local/image-embedding`；`endpoint_url` 为空。
- 未改 Nacos；未留下启用前缀的运行值；报告中凭证已脱敏。

## 隔离与清理

- 测试数据只在隔离临时目录（`oss-key-prefix-qa-*`）、内存 mock、FakeS3Client 与 FakeMetadata 中。
- **未**使用真实 OSS、业务库、Qdrant、Nacos；**未**自建容器或常驻进程。
- 收尾：`tempfile` 下无 `oss-key-prefix-qa-*` 残留；已删除 `.qa/oss-key-prefix/__pycache__`。
- 清理完成。

## 报告路径

- 盒内：`/workspace/deliverables/oss-key-prefix-acceptance-report.json` 与 `.md`
- C 仓：`C:\Users\47202\PROJECT\Image-Embedding\.qa\oss-key-prefix\oss-key-prefix-acceptance-report.json` 与 `.md`
