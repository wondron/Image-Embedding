"""把一个文件夹里的图片逐张入库：每张图发一次 POST /api/index（multipart 只带一个 file）。

参数不走命令行，改下面「参数」一节即可。先启动后端（launch.json 的「Python: Backend」），
再用 launch.json 的「Python: Index Folder Images」调试，或在 backend 目录执行：
  python scripts/index_folder.py

- 上传前请求 GET /api/health，embedding、Qdrant、PostgreSQL 有一个不可用就不上传；
  填了 category 时再用 GET /api/categories 核对编码：服务端遇到不存在的编码不报错，图片会按未标注入库。
- 按路径排序逐张上传，每张打印一行：新入库 / 已存在 / 失败。
  字节完全相同的图片服务端只存一份，再传返回「已存在」并沿用首次入库的标注，所以中断后重跑是安全的
  （「入库任务」里每次上传各记一条）。
- 连不上、超时、429、5xx 自动重试；重试完仍失败、且连续 MAX_SERVER_FAILURES 张都这样，就停止。
- 表单字段不合法（VALIDATION_ERROR / INDEX_UNKNOWN_SCENE）时每张图都会同样失败，第一张就停止。
- 其余失败（图片解不开、超过大小上限等）只是这张图的问题：记下来，继续下一张，最后汇总列出。

退出码：0 全部入库（新入库或已存在）；1 有图片失败或中途停止；2 参数不对或后端未就绪，没有上传。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# ==== 参数 ==========================================================================

# 待入库的图片文件夹，例如 r"E:\dataset\红薯叶"
IMAGE_DIR = r""
# True：连同子文件夹；False：只取这一层
RECURSIVE = True
# 只上传这些扩展名（不区分大小写），和前端「上传与导入」一致
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# 后端地址
BASE_URL = "http://127.0.0.1:8320"

# 每张图都带的标注，None 表示不传、用服务端默认值。
# 只对新入库的图片生效：已存在的图片沿用首次入库的标注（要改去「图片数据」页或 PATCH /api/images/{id}）。
FORM_FIELDS: dict[str, Any] = {
    "category": None,  # 类别编码（categories.code），如 "dish.pizza"，不区分大小写
    "label_status": None,  # "auto" / "verified"，要同时填 category；不填时有类别为 auto，否则 unlabeled
    "tags": None,  # 标签列表，如 ["红薯叶"]
    "description": None,  # 图片描述
    "source_type": None,  # 取值见 GET /api/enums/source_type，默认 user_upload
    "license": None,  # 取值见 GET /api/enums/license，默认 unknown
    "food_state": None,  # 取值见 GET /api/enums/food_state，如 "raw"
    "food_processing": None,  # 取值见 GET /api/enums/food_processing，如 "sliced"
    "food_count": None,  # 食物个数，正整数
    "total_mass_g": None,  # 总质量（克），正整数
    "water_mass_g": None,  # 含水质量（克），可以为 0
    "main_ingredient_mass_g": None,  # 主食材质量（克），正整数
}

# 调试用：True 只列出会上传哪些文件，不请求后端
DRY_RUN = False
# 调试用：只上传排序后的前 N 张；None 表示全部
LIMIT: int | None = None

# 每次请求的超时（秒）
TIMEOUT_SECONDS = 60
# 连不上 / 超时 / 429 / 5xx 时，每张图最多再试几次（间隔 2、4、8… 秒）
RETRIES = 2
# 连续这么多张图重试完仍是服务端的问题，就停止
MAX_SERVER_FAILURES = 3

# ==== 以下一般不用改 ==================================================================

# 200 响应里这张图在 failed 中时，这些错误码是服务端的问题（5xx），值得重试
SERVER_ERRORS = frozenset({"INTERNAL_ERROR", "NOT_READY", "INDEX_QDRANT_FAILED", "INDEX_POSTGRES_FAILED"})
# 表单字段的问题：每张图带的是同一组字段，后面的图片都会同样失败
FORM_ERRORS = frozenset({"VALIDATION_ERROR", "INDEX_UNKNOWN_SCENE"})
# 汉字在终端里占两列，"失败" 后补两个空格和另外两个对齐
STATUS_TEXT = {"created": "新入库", "existed": "已存在", "failed": "失败  "}


@dataclass(frozen=True)
class Outcome:
    status: str  # created / existed / failed
    image_id: str = ""
    error: str = ""  # 错误码，如 INDEX_BAD_IMAGE；请求没发出去时是异常类名，如 ConnectionError
    detail: str = ""
    server_side: bool = False  # 连不上、超时、429、5xx


def api(path: str) -> str:
    return BASE_URL.rstrip("/") + path


def json_object(resp: requests.Response) -> dict[str, Any]:
    """响应体里的 JSON 对象；不是 JSON（例如代理返回的 HTML）时为空字典。"""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def find_images(root: Path) -> list[Path]:
    exts = {ext.lower() for ext in IMAGE_EXTS}
    candidates = root.rglob("*") if RECURSIVE else root.iterdir()
    return sorted(p for p in candidates if p.is_file() and p.suffix.lower() in exts)


def build_form(fields: dict[str, Any]) -> dict[str, str]:
    """FORM_FIELDS -> multipart 文本字段：None 和空值不传，tags 编成 JSON 数组。"""
    form: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "tags":
            tags = [value] if isinstance(value, str) else list(value)
            text = json.dumps(tags, ensure_ascii=False) if tags else ""
        else:
            text = str(value).strip()
        if text:
            form[key] = text
    return form


# -- 上传前检查 -------------------------------------------------------------------------


def preflight(session: requests.Session, category: str | None) -> bool:
    """后端能入库、category（填了的话）存在，才开始上传。"""
    try:
        return backend_ready(session) and (category is None or category_exists(session, category))
    except requests.RequestException as exc:
        print(f"连不上后端 {BASE_URL}：{exc}")
        print("先启动后端（launch.json 的「Python: Backend」），或修改 BASE_URL")
        return False


def backend_ready(session: requests.Session) -> bool:
    """GET /api/health：embedding、Qdrant、PostgreSQL 都可用时返回 200。"""
    resp = session.get(api("/api/health"), timeout=TIMEOUT_SECONDS)
    body = json_object(resp)
    components = body.get("components") or {}
    if resp.status_code != 200 or not body.get("ok"):
        print(f"后端还不能入库（GET /api/health 返回 {resp.status_code}），没有上传：")
        for name, comp in components.items():
            if isinstance(comp, dict) and not comp.get("ok") and not comp.get("optional"):
                print(f"  {name}: {comp.get('detail')}")
        return False
    embedding = components.get("embedding") or {}
    print(
        f"后端：{BASE_URL}，模型 {embedding.get('current_model')}，"
        f"collection {embedding.get('current_collection')}"
    )
    return True


def category_exists(session: requests.Session, code: str) -> bool:
    """服务端按编码（不区分大小写）找类别，找不到也照常入库、只是不带类别，所以先在这里核对。"""
    resp = session.get(api("/api/categories"), timeout=TIMEOUT_SECONDS)
    body = json_object(resp)
    if resp.status_code != 200:
        print(f"读不到类别列表（GET /api/categories 返回 {resp.status_code}）：{body.get('detail', '')}")
        return False
    for item in body.get("categories") or []:
        if str(item.get("code", "")).lower() == code.lower():
            inactive = "" if item.get("is_active", True) else "（已停用）"
            print(f"类别：{item.get('code')} {item.get('name') or ''}{inactive}")
            return True
    print(f"类别编码 {code} 不存在，没有上传；编码见「食材管理」或 GET /api/categories")
    return False


# -- 上传 -------------------------------------------------------------------------------


def post_image(session: requests.Session, name: str, data: bytes, form: dict[str, str]) -> Outcome:
    """一次 POST /api/index，只带这一张图。"""
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        resp = session.post(
            api("/api/index"),
            files={"file": (name, data, mime)},
            data=form,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return Outcome("failed", error=type(exc).__name__, detail=str(exc), server_side=True)

    body = json_object(resp)
    if not resp.ok:  # 整个请求失败：{ok: false, error, detail, trace_id}
        return Outcome(
            "failed",
            error=str(body.get("error") or f"HTTP_{resp.status_code}"),
            detail=str(body.get("detail") or resp.text[:200]),
            server_side=resp.status_code == 429 or resp.status_code >= 500,
        )
    indexed = body.get("indexed") or []
    if indexed:  # created=false：字节相同的图片之前入过库
        item = indexed[0]
        status = "existed" if item.get("created") is False else "created"
        return Outcome(status, image_id=str(item.get("id", "")))
    failed = (body.get("failed") or [{}])[0]  # 200，但这张图没有入库
    error = str(failed.get("error") or "NO_RESULT")
    return Outcome("failed", error=error, detail=str(failed.get("detail", "")), server_side=error in SERVER_ERRORS)


def upload(session: requests.Session, name: str, data: bytes, form: dict[str, str]) -> Outcome:
    """服务端的问题最多重试 RETRIES 次。重试不会重复入库：服务端按内容去重，
    上一次其实已经写入时这一次返回「已存在」。"""
    attempt = 0
    while True:
        outcome = post_image(session, name, data, form)
        if not outcome.server_side or attempt >= RETRIES:
            return outcome
        attempt += 1
        wait = 2**attempt
        print(f"    {name}：{outcome.error}，{wait} 秒后重试（{attempt}/{RETRIES}）")
        time.sleep(wait)


def run(session: requests.Session, root: Path, paths: list[Path], form: dict[str, str]) -> int:
    counts = {"created": 0, "existed": 0, "failed": 0}
    failures: list[tuple[str, Outcome]] = []
    server_failures = 0  # 连续几张重试完仍是服务端的问题
    stop_reason = ""
    total = len(paths)
    width = len(str(total))
    started = time.perf_counter()
    try:
        for i, path in enumerate(paths, 1):
            name = path.relative_to(root).as_posix()  # 也是「入库任务」里显示的文件名
            try:
                data = path.read_bytes()
            except OSError as exc:
                outcome = Outcome("failed", error="READ_FAILED", detail=str(exc))
            else:
                outcome = upload(session, name, data, form)

            counts[outcome.status] += 1
            line = f"[{i:>{width}}/{total}] {STATUS_TEXT[outcome.status]} {name}"
            if outcome.status == "failed":
                failures.append((name, outcome))
                print(f"{line}  {outcome.error}: {outcome.detail}")
            else:
                print(f"{line}  id={outcome.image_id}")

            if outcome.error in FORM_ERRORS:
                stop_reason = "表单字段不合法，后面的图片也会同样失败；改 FORM_FIELDS 后重跑"
                break
            server_failures = server_failures + 1 if outcome.server_side else 0
            if outcome.server_side and server_failures >= MAX_SERVER_FAILURES:
                stop_reason = f"连续 {server_failures} 张都是服务端的问题，先检查后端"
                break
    except KeyboardInterrupt:
        stop_reason = "手动中断（正在上传的那张没有计入，可能已经入库）"

    elapsed = time.perf_counter() - started
    print(
        f"\n处理 {sum(counts.values())}/{total} 张，用时 {elapsed:.1f} 秒："
        f"新入库 {counts['created']}，已存在 {counts['existed']}，失败 {counts['failed']}"
    )
    if failures:
        print("失败的文件：")
        for name, outcome in failures:
            print(f"  {name}  {outcome.error}: {outcome.detail}")
    if stop_reason:
        print(f"已停止：{stop_reason}")
    return 1 if failures or stop_reason else 0


def main() -> int:
    if not IMAGE_DIR.strip():
        print("先在脚本顶部设置 IMAGE_DIR（待入库的图片文件夹）")
        return 2
    root = Path(IMAGE_DIR.strip())
    if not root.is_dir():
        print(f"文件夹不存在：{root}")
        return 2
    paths = find_images(root)
    if LIMIT is not None:
        paths = paths[:LIMIT]
    if not paths:
        print(f"{root} 下没有 {' / '.join(IMAGE_EXTS)} 图片")
        return 2

    form = build_form(FORM_FIELDS)
    print(f"文件夹：{root}（{'含' if RECURSIVE else '不含'}子文件夹），{len(paths)} 张图片")
    print(f"标注：{form or '不带'}")
    if DRY_RUN:
        for path in paths:
            print(f"  {path.relative_to(root).as_posix()}")
        print("DRY_RUN = True，没有上传")
        return 0

    with requests.Session() as session:
        if not preflight(session, form.get("category")):
            return 2
        return run(session, root, paths, form)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
