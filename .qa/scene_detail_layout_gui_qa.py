#!/usr/bin/env python3
# QA ONLY: isolated in-memory fixture HTTP + Vite for scene-detail-layout visual handoff.
# Does not import the product backend, write business DB/OSS, bind public interfaces, or start a tunnel.
from __future__ import annotations
import json, os, socket, subprocess, sys, threading, time, traceback, uuid
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("America/Denver")
TTL_SECONDS = 3600
HEAD = "c145e84e167fcf01e8e60218955427edb435b52f"
NODE = Path(r"D:/98-softwaredata/nodejs/node.exe")
VITE = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
QA = ROOT / ".qa"
frontend = None
httpd = None

def now():
    return datetime.now(ZONE)

def iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port

def trace():
    return str(uuid.uuid4())

NAME64 = "长名称用于验收右栏折行与不溢出的场景标题" + "测" * (64 - len("长名称用于验收右栏折行与不溢出的场景标题"))
assert len(NAME64) == 64
DESC256 = '????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????'
DESC256 = '????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????'
assert len(DESC256) == 256
CODE32 = "qalongestlegalcode32charactersxx"
assert len(CODE32) == 32 and CODE32[0].islower() and CODE32.replace("_", "").isalnum()
HUGE = 9007199254740991  # max JSON/JS safe integer

def scene(id, code, name, **kw):
    row = {
        "id": id, "code": code, "name": name, "description": kw.get("description"),
        "include_all": kw.get("include_all", False), "outputs_heating": kw.get("outputs_heating", False),
        "is_active": kw.get("is_active", True), "created_at": kw.get("created_at", "2026-11-10T00:00:00+00:00"),
        "member_count": kw.get("member_count", 0), "labels": kw.get("labels", []), "rules": kw.get("rules", []),
    }
    return row

STATE = {
    "fail_next_catalog": False,
    "delay_next_catalog_ms": 0,
    "fail_next_sync": False,
    "fail_next_write": False,
    "catalog": {
        "ok": True,
        "default_scene": "general",
        "scenes": [
            scene(1, "general", "通用（日常）", description="含全部图片", include_all=True, member_count=3),
            scene(2, "oven", "一体机", description="按类别规则 + scene_labels；结果带加热组", outputs_heating=True, member_count=12,
                  labels=[
                      {"id": 21, "code": "qa_in_use", "name": "仍被规则引用的标签", "sort_order": 1, "is_active": True, "rule_count": 1},
                      {"id": 22, "code": "qa_free_label", "name": "未被引用的标签", "sort_order": 2, "is_active": False, "rule_count": 0},
                  ],
                  rules=[{
                      "id": 31,
                      "category": {"id": 1, "code": "dish", "name": "菜品"},
                      "cuisine": None,
                      "label": {"id": 21, "code": "qa_in_use", "name": "仍被规则引用的标签"},
                  }]),
            scene(3, "qa_empty", "空标签与空规则", description=None, member_count=0),
            scene(4, CODE32, NAME64, description=DESC256, include_all=False, outputs_heating=False, is_active=True, member_count=HUGE),
            scene(5, "qa_badges", "停用且全包含徽标", description="用于徽标换行", include_all=True, is_active=False, member_count=1),
        ],
    },
}

def find_scene(sid):
    for s in STATE["catalog"]["scenes"]:
        if s["id"] == sid:
            return s
    return None

def write_ok(scene_obj, sync_required, deleted=None):
    body = {"ok": True, "sync_required": bool(sync_required), "scene": deepcopy(scene_obj)}
    if deleted is not None:
        body = {"ok": True, "sync_required": bool(sync_required), "deleted": {"id": deleted}}
    return body

CATEGORIES = {
    "ok": True,
    "categories": [{
        "id": 1, "code": "dish", "parent_id": None, "path": "1", "name": "菜品",
        "names": [{"lang": "zh", "name": "菜品", "kind": "primary"}],
        "cuisines": [], "heating_group": None, "sort_order": 0, "is_active": True,
        "image_count": 0, "scene_rule_count": 1,
        "created_at": "2026-11-10T00:00:00+00:00", "updated_at": "2026-11-10T00:00:00+00:00",
    }],
    "heating_groups": [{"id": 2, "code": "grill", "name": "烤"}],
    "root_codes": ["ingredient", "dish", "dessert", "beverage"],
}

NEXT_ID = {"scene": 100, "label": 200, "rule": 300}

def json_body(handler):
    n = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(n) if n else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")

def send(handler, status, body, tid=None):
    tid = tid or trace()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("X-Trace-Id", tid)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

def err(code, detail, status=422):
    return status, {"ok": False, "error": code, "detail": detail, "trace_id": trace()}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        QA.joinpath("scene-detail-layout-gui-api.log").open("a", encoding="utf-8").write(
            f"{now().isoformat()} {self.command} {self.path} {fmt % args}\n"
        )
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        self.end_headers()
    def do_GET(self): self.dispatch()
    def do_POST(self): self.dispatch()
    def do_PATCH(self): self.dispatch()
    def do_DELETE(self): self.dispatch()
    def dispatch(self):
        try:
            u = urlparse(self.path)
            path, qs = u.path, {k: v[-1] for k, v in parse_qs(u.query).items()}
            method = self.command
            if path == "/api/ping" and method == "GET":
                return send(self, 200, {"ok": True, "ts": iso(), "timeout_s": 5, "max_image_bytes": 10485760})
            if path == "/qa/ready" and method == "GET":
                return send(self, 200, {"ok": True, "role": "scene-detail-layout isolated fixture", "pid": os.getpid()})
            if path == "/qa/fail-next-catalog" and method in ("GET", "POST"):
                STATE["fail_next_catalog"] = True
                return send(self, 200, {"ok": True, "fail_next_catalog": True})
            if path == "/qa/delay-next-catalog" and method in ("GET", "POST"):
                STATE["delay_next_catalog_ms"] = int(qs.get("ms") or 2500)
                return send(self, 200, {"ok": True, "delay_next_catalog_ms": STATE["delay_next_catalog_ms"]})
            if path == "/qa/fail-next-sync" and method in ("GET", "POST"):
                STATE["fail_next_sync"] = True
                return send(self, 200, {"ok": True, "fail_next_sync": True})
            if path == "/qa/fail-next-write" and method in ("GET", "POST"):
                STATE["fail_next_write"] = True
                return send(self, 200, {"ok": True, "fail_next_write": True})
            if path == "/qa/state" and method == "GET":
                return send(self, 200, {"ok": True, "flags": {k: STATE[k] for k in list(STATE) if k != "catalog"}, "scenes": [s["id"] for s in STATE["catalog"]["scenes"]]})
            if path == "/api/categories" and method == "GET":
                return send(self, 200, deepcopy(CATEGORIES))
            if path == "/api/scenes" and method == "GET":
                if STATE["delay_next_catalog_ms"]:
                    time.sleep(STATE["delay_next_catalog_ms"] / 1000)
                    STATE["delay_next_catalog_ms"] = 0
                if STATE["fail_next_catalog"]:
                    STATE["fail_next_catalog"] = False
                    st, body = err("INDEX_POSTGRES_FAILED", "QA isolated catalog failure fixture", 503)
                    body["trace_id"] = trace()
                    return send(self, st, body, body["trace_id"])
                body = deepcopy(STATE["catalog"]); body["ok"] = True
                return send(self, 200, body)
            if path == "/api/scenes" and method == "POST":
                data = json_body(self)
                sid = NEXT_ID["scene"]; NEXT_ID["scene"] += 1
                row = scene(sid, str(data.get("code")), str(data.get("name")), description=data.get("description"),
                            include_all=bool(data.get("include_all", False)), outputs_heating=bool(data.get("outputs_heating", False)),
                            is_active=bool(data.get("is_active", True)), created_at=iso())
                STATE["catalog"]["scenes"].append(row)
                return send(self, 201, write_ok(row, True))
            parts = path.strip("/").split("/")
            if parts[:2] == ["api", "scenes"] and len(parts) == 3 and parts[2] == "sync" and method == "POST":
                data = json_body(self)
                if STATE["fail_next_sync"]:
                    STATE["fail_next_sync"] = False
                    st, body = err("INDEX_QDRANT_FAILED", "QA isolated sync failure fixture", 502)
                    body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                after = data.get("after"); limit = int(data.get("limit") or 200)
                if after is None:
                    nxt = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
                    body = {"ok": True, "processed": min(max(limit, 1), 3), "membership_changed": 1, "payload_pushed": 1,
                            "payload_missing": ["bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"], "next_after": nxt, "done": False}
                else:
                    body = {"ok": True, "processed": 0, "membership_changed": 0, "payload_pushed": 0,
                            "payload_missing": ["bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"], "next_after": None, "done": True}
                return send(self, 200, body)
            if parts[:2] == ["api", "scenes"] and len(parts) >= 3 and parts[2].isdigit():
                sid = int(parts[2]); row = find_scene(sid)
                if row is None:
                    st, body = err("SCENE_NOT_FOUND", "scene not found", 404); body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                if STATE["fail_next_write"] and method in ("POST", "PATCH", "DELETE"):
                    STATE["fail_next_write"] = False
                    st, body = err("INDEX_POSTGRES_FAILED", "QA isolated write failure fixture", 502)
                    body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                if method == "PATCH" and len(parts) == 3:
                    data = json_body(self)
                    if isinstance(data.get("name"), str) and "QAFAIL" in data["name"]:
                        st, body = err("INDEX_POSTGRES_FAILED", "QA isolated write failure by name fixture", 502)
                        body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                    protected = row["code"] == "general" or row["code"] == STATE["catalog"]["default_scene"]
                    if protected and (data.get("is_active") is False or data.get("include_all") is False):
                        st, body = err("SCENE_PROTECTED", "default scene cannot drop include_all, deactivate, or be deleted", 403)
                        body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                    before = (row["name"], row["description"], row["include_all"], row["is_active"])
                    if "name" in data: row["name"] = data["name"]
                    if "description" in data: row["description"] = data["description"]
                    if "include_all" in data: row["include_all"] = bool(data["include_all"])
                    if "outputs_heating" in data: row["outputs_heating"] = bool(data["outputs_heating"])
                    if "is_active" in data: row["is_active"] = bool(data["is_active"])
                    after = (row["name"], row["description"], row["include_all"], row["is_active"])
                    # Only outputs_heating (or no effective member-set/config fields besides heating) => sync_required false.
                    sync_required = before != after
                    return send(self, 200, write_ok(row, sync_required))
                if method == "DELETE" and len(parts) == 3:
                    protected = row["code"] == "general" or row["code"] == STATE["catalog"]["default_scene"]
                    if protected:
                        st, body = err("SCENE_PROTECTED", "default scene cannot be deleted", 403)
                        body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                    STATE["catalog"]["scenes"] = [s for s in STATE["catalog"]["scenes"] if s["id"] != sid]
                    return send(self, 200, write_ok(None, True, deleted=sid))
                if len(parts) >= 4 and parts[3] == "labels":
                    if method == "POST" and len(parts) == 4:
                        data = json_body(self); lid = NEXT_ID["label"]; NEXT_ID["label"] += 1
                        lab = {"id": lid, "code": data["code"], "name": data["name"], "sort_order": int(data.get("sort_order") or 0), "is_active": bool(data.get("is_active", True)), "rule_count": 0}
                        row["labels"].append(lab)
                        return send(self, 201, write_ok(row, True))
                    if len(parts) == 5 and parts[4].isdigit():
                        lid = int(parts[4]); lab = next((x for x in row["labels"] if x["id"] == lid), None)
                        if lab is None:
                            st, body = err("SCENE_NOT_FOUND", "label not found", 404); body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                        if method == "PATCH":
                            data = json_body(self)
                            for k, src in (("code", "code"), ("name", "name"), ("sort_order", "sort_order"), ("is_active", "is_active")):
                                if src in data: lab[k if k != "sort_order" else "sort_order"] = data[src]
                            # Label active toggle is immediate in the product confirmation copy; fixture returns false.
                            sync_required = "code" in data or "name" in data or "sort_order" in data
                            return send(self, 200, write_ok(row, sync_required))
                        if method == "DELETE":
                            if lab["rule_count"] > 0:
                                st, body = err("SCENE_IN_USE", "label is still referenced by a rule", 409)
                                body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                            row["labels"] = [x for x in row["labels"] if x["id"] != lid]
                            return send(self, 200, write_ok(row, True))
                if len(parts) >= 4 and parts[3] == "rules":
                    if method == "POST" and len(parts) == 4:
                        data = json_body(self)
                        if data.get("cuisine") == "qa_force_fail":
                            st, body = err("SCENE_PROTOCOL_ERROR", "QA isolated createRule failure fixture", 422)
                            body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                        rid = NEXT_ID["rule"]; NEXT_ID["rule"] += 1
                        label = next((deepcopy(x) for x in row["labels"] if x["id"] == data.get("label_id")), None)
                        if label: label = {"id": label["id"], "code": label["code"], "name": label["name"]}
                        cat = {"id": 1, "code": "dish", "name": "菜品"} if data.get("category_id") else None
                        rule = {"id": rid, "category": cat, "cuisine": data.get("cuisine"), "label": label}
                        row["rules"].append(rule)
                        if label:
                            for x in row["labels"]:
                                if x["id"] == label["id"]: x["rule_count"] += 1
                        return send(self, 201, write_ok(row, True))
                    if method == "PATCH" and len(parts) == 5 and parts[4].isdigit():
                        data = json_body(self)
                        if data.get("cuisine") == "qa_force_fail":
                            st, body = err("SCENE_RULE_INVALID", "QA isolated updateRule failure fixture", 422)
                            body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                        rid = int(parts[4]); rule = next((x for x in row["rules"] if x["id"] == rid), None)
                        if rule is None:
                            st, body = err("SCENE_NOT_FOUND", "rule not found", 404); body["trace_id"] = trace(); return send(self, st, body, body["trace_id"])
                        if "category_id" in data:
                            rule["category"] = {"id": 1, "code": "dish", "name": "菜品"} if data.get("category_id") else None
                        if "cuisine" in data: rule["cuisine"] = data.get("cuisine")
                        if "label_id" in data:
                            lab = next((x for x in row["labels"] if x["id"] == data.get("label_id")), None)
                            rule["label"] = {"id": lab["id"], "code": lab["code"], "name": lab["name"]} if lab else None
                        return send(self, 200, write_ok(row, True))
                    if method == "DELETE" and len(parts) == 5 and parts[4].isdigit():
                        rid = int(parts[4])
                        row["rules"] = [x for x in row["rules"] if x["id"] != rid]
                        return send(self, 200, write_ok(row, True))
            send(self, 404, {"ok": False, "error": "QA_NOT_FOUND", "detail": path, "trace_id": trace()})
        except Exception as exc:
            traceback.print_exc()
            tid = trace(); send(self, 500, {"ok": False, "error": "QA_FIXTURE_ERROR", "detail": type(exc).__name__, "trace_id": tid}, tid)

def main():
    global frontend, httpd
    QA.mkdir(exist_ok=True)
    api_port = free_port(); fe_port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", api_port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{api_port}"
    env = dict(os.environ); env["VITE_API_PROXY_TARGET"] = api
    log = open(QA / "scene-detail-layout-gui-frontend.log", "w", encoding="utf-8")
    frontend = subprocess.Popen(
        [str(NODE), str(VITE), "--host", "127.0.0.1", "--port", str(fe_port), "--strictPort"],
        cwd=str(ROOT / "frontend"), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    fe = f"http://127.0.0.1:{fe_port}"
    deadline = time.monotonic() + 30
    import urllib.request
    while True:
        if frontend.poll() is not None:
            raise RuntimeError("QA Vite exited early; see .qa/scene-detail-layout-gui-frontend.log")
        try:
            with urllib.request.urlopen(fe + "/?view=scenes", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("QA Vite not ready")
        time.sleep(0.15)
    # Confirm proxy to fixture and that working-tree CSS is what Vite will serve.
    css_hash = __import__("hashlib").sha256((ROOT / "frontend/src/features/scene-management/scene-management.css").read_bytes()).hexdigest().upper()
    with urllib.request.urlopen(fe + "/api/scenes", timeout=5) as r:
        catalog = json.loads(r.read().decode())
        proxy_ok = r.status == 200 and any(s["code"] == CODE32 for s in catalog.get("scenes", []))
    ready = {
        "ready": True,
        "task": "scene-detail-layout v0.2 isolated GUI handoff",
        "head": HEAD,
        "code_root": str(ROOT),
        "served_from": "Vite dev of the uncommitted working tree (the three layout files), not frontend/dist",
        "three_file_css_sha256": css_hash,
        "api_url": api,
        "frontend_url": fe,
        "scene_management_url": fe + "/?view=scenes",
        "long_scene_url": fe + "/?view=scenes&scene=4&sceneTab=overview",
        "empty_scene_url": fe + "/?view=scenes&scene=3&sceneTab=labels",
        "default_scene_url": fe + "/?view=scenes&scene=1&sceneTab=overview",
        "oven_labels_url": fe + "/?view=scenes&scene=2&sceneTab=labels",
        "unselected_sync_url": fe + "/?view=scenes&sceneTab=sync",
        "frontend_pid": frontend.pid,
        "api_pid": os.getpid(),
        "frontend_port": fe_port,
        "api_port": api_port,
        "proxy_verified_HTTP": 200 if proxy_ok else None,
        "proxy_verified_long_code": CODE32 if proxy_ok else None,
        "network_boundary": "ONLY wondron 127.0.0.1. NOT box loopback. No tunnel or public bind. Previous peer Playwright against wondron loopback returned net::ERR_CONNECTION_REFUSED; do not assume Playwright runs on this machine. Use a browser ON wondron.",
        "api_base_warning": "Before any write, set the in-app API base blank (use Vite proxy) or to the exact fixture api_url. Do not reuse a saved business food-atlas-api localStorage value; that would hit production data.",
        "backend": "In-memory QA fixture only. Not the real FastAPI/PG/Qdrant and not a product substitute for backend acceptance.",
        "fixtures": {
            "name_64": NAME64, "code_32": CODE32, "description_256_length": 256,
            "member_count": HUGE, "empty_scene_id": 3, "badge_scene_id": 5, "in_use_label_id": 21,
            "sync_required_true": "Edit name or description of qa_empty (id 3) and save; fixture returns sync_required true.",
            "sync_required_false_with_pending": "After a true save, edit only outputs_heating on the same or another non-default scene; fixture returns sync_required false; client pendingSync must remain.",
            "write_fail": "GET {api}/qa/fail-next-write or set name to contain QAFAIL, then save.",
            "catalog_fail": "GET {api}/qa/fail-next-catalog then click Refresh.",
            "catalog_delay": "GET {api}/qa/delay-next-catalog?ms=2500 then Refresh to observe loading.",
            "sync_fail": "GET {api}/qa/fail-next-sync then start a sync batch.",
            "rule_fail": "Create or update a cuisine rule with cuisine qa_force_fail to exercise updateRule/createRule error (do not use deleted-and-recreated semantics).",
            "label_in_use": "On oven labels, delete qa_in_use (id 21); expect 409 SCENE_IN_USE and the row remains.",
        },
        "width_measurement": "In DevTools on wondron, run: const el=document.querySelector('.scene-details'); const s=getComputedStyle(el); const inner=el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight); ({inner, dataSceneWidth:el.dataset.sceneWidth, containerType:s.containerType, tabs:[...document.querySelectorAll('.scene-detail-tabs [role=tab]')].map(x=>x.textContent), hasBadges:!!document.querySelector('.scene-detail-badges')}). Confirm data-scene-width and classes from the uncommitted files before screenshots. For L4 keep the window wide (>=1280) and only narrow the right column: document.querySelector('.scene-layout').style.gridTemplateColumns='1fr 720px' then 719px, 480px, 479px. Record inner width each time. Do not treat viewport-only resize as L4 proof.",
        "ready_at": now().isoformat(),
        "lifetime_seconds": TTL_SECONDS,
        "expires_at": datetime.fromtimestamp(time.time() + TTL_SECONDS, ZONE).isoformat(),
        "cleanup": "Natural TTL stops exact frontend PID and this API PID only. Manual: .qa/scene-detail-layout-gui-cleanup.ps1. Never touch business roki or existing Vite.",
    }
    (QA / "scene-detail-layout-gui-ready.json").write_text(json.dumps(ready, ensure_ascii=False, indent=2), encoding="utf-8")
    needle_fe = "C:/Users/47202/PROJECT/Image-Embedding/frontend/node_modules/vite/bin/vite.js"
    lines = [
        "# QA ONLY. Stop layout-handoff PIDs only if command lines still match.",
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={frontend.pid}\"",
        f"if ($p -and $p.CommandLine.Replace('\\','/') -like '*{needle_fe}*') {{ Stop-Process -Id {frontend.pid} -ErrorAction Stop }} elseif ($p) {{ Write-Warning 'frontend PID command line changed; NOT stopped' }}",
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={os.getpid()}\"",
        "if ($p -and $p.CommandLine -like '*scene_detail_layout_gui_qa.py*') { Stop-Process -Id " + str(os.getpid()) + " -ErrorAction Stop } elseif ($p) { Write-Warning 'API PID command line changed; NOT stopped' }",
    ]
    (QA / "scene-detail-layout-gui-cleanup.ps1").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("QA_LAYOUT_GUI_READY " + json.dumps(ready, ensure_ascii=True), flush=True)
    end = time.monotonic() + TTL_SECONDS
    while time.monotonic() < end and frontend.poll() is None:
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        sys.exit(1)
    finally:
        if frontend is not None and frontend.poll() is None:
            frontend.terminate()
            try: frontend.wait(10)
            except Exception: frontend.kill()
        if httpd is not None:
            httpd.shutdown()
        (QA / "scene-detail-layout-gui-ended.json").write_text(
            json.dumps({
                "ended_at": now().isoformat(),
                "frontend_stopped": frontend is None or frontend.poll() is not None,
                "api_stopped": True,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
