import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from base64 import b64decode
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.adapters import PROVIDER_PRESETS, enrich_model_config, test_model_config
from src.db import (
    DEFAULT_DB_PATH,
    analytics,
    create_sampling_batch,
    create_model_config,
    create_project,
    delete_model_config,
    delete_project,
    delete_question,
    get_model_config,
    get_sampling_batch,
    get_conn,
    import_questions_csv,
    import_questions_rows,
    init_db,
    list_model_configs,
    list_projects,
    list_questions,
    list_runs_by_batch,
    list_runs,
    list_sampling_batches,
    seed_questions,
    update_model_config,
    update_project,
    update_question,
    update_sampling_batch,
)
from src.exporter import (
    analytics_to_csv,
    analytics_to_excel_html,
    runs_to_csv,
    runs_to_excel_html,
)
from src.runtime_env import load_dotenv_file
from src.runner import estimate_batch_total, run_batch
from src.db import utc_now


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
EXPORT_DIR = ROOT / "exports"
SAMPLING_JOBS: dict[str, dict] = {}
SAMPLING_JOBS_LOCK = threading.Lock()
AUTH_PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}
AUTH_SESSION_TTL_SECONDS = 60 * 60 * 12


def auth_password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()


def auth_cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "geo_audit_session").strip() or "geo_audit_session"


def auth_secret() -> str:
    return os.environ.get("APP_SESSION_SECRET", "").strip() or auth_password()


def auth_enabled() -> bool:
    return bool(auth_password())


def sign_auth_payload(payload: str) -> str:
    return hmac.new(auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def build_auth_token() -> str:
    issued_at = str(int(time.time()))
    payload = f"v1:{issued_at}"
    return f"{payload}:{sign_auth_payload(payload)}"


def verify_auth_token(token: str) -> bool:
    parts = str(token or "").split(":")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    payload = f"{parts[0]}:{parts[1]}"
    expected = sign_auth_payload(payload)
    if not hmac.compare_digest(expected, parts[2]):
        return False
    try:
        issued_at = int(parts[1])
    except ValueError:
        return False
    return 0 <= time.time() - issued_at <= AUTH_SESSION_TTL_SECONDS


def set_sampling_job(batch_id: str, **updates):
    with SAMPLING_JOBS_LOCK:
        current = SAMPLING_JOBS.get(batch_id, {}).copy()
        current.update(updates)
        SAMPLING_JOBS[batch_id] = current
        return current.copy()


def get_sampling_job(batch_id: str) -> dict | None:
    with SAMPLING_JOBS_LOCK:
        job = SAMPLING_JOBS.get(batch_id)
        return job.copy() if job else None


def sampling_batch_to_progress(batch: dict) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "project_id": batch["project_id"],
        "status": batch["status"],
        "total": batch.get("total_count", 0),
        "completed": batch.get("completed_count", 0),
        "failed": batch.get("failed_count", 0),
        "success": batch.get("success_count", 0),
        "error": batch.get("error_message", ""),
        "created_at": batch.get("created_at"),
        "started_at": batch.get("started_at"),
        "finished_at": batch.get("finished_at"),
        "updated_at": batch.get("updated_at"),
    }


def run_batch_in_background(batch_id: str, project_id: int, payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())

    try:
        with get_conn(DEFAULT_DB_PATH) as conn:
            update_sampling_batch(conn, batch_id, {"status": "running", "started_at": utc_now(), "updated_at": utc_now()})
            conn.commit()

            def on_progress(progress: dict):
                set_sampling_job(
                    batch_id,
                    status="running",
                    total=progress["total"],
                    completed=progress["completed"],
                    failed=progress["failed"],
                    success=progress["success"],
                    current_provider=progress["provider"],
                    current_model=progress["model"],
                    current_question_id=progress["question_id"],
                    current_repeat_index=progress["repeat_index"],
                    updated_at=utc_now(),
                )
                update_sampling_batch(
                    conn,
                    batch_id,
                    {
                        "status": "running",
                        "total_count": progress["total"],
                        "completed_count": progress["completed"],
                        "failed_count": progress["failed"],
                        "success_count": progress["success"],
                        "updated_at": utc_now(),
                    },
                )
                conn.commit()

            result = run_batch(conn, project_id, payload, batch_id=batch_id, progress_callback=on_progress)
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "completed",
                    "total_count": result["total"],
                    "completed_count": result["total"],
                    "failed_count": result["failed"],
                    "success_count": result["success"],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
        set_sampling_job(
            batch_id,
            status="completed",
            total=result["total"],
            completed=result["total"],
            failed=result["failed"],
            success=result["success"],
            finished_at=utc_now(),
            updated_at=utc_now(),
        )
    except Exception as exc:
        job = get_sampling_job(batch_id) or {}
        with get_conn(DEFAULT_DB_PATH) as conn:
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                    "completed_count": job.get("completed", 0),
                    "failed_count": job.get("failed", 0),
                    "success_count": job.get("success", 0),
                },
            )
        set_sampling_job(
            batch_id,
            status="failed",
            error=str(exc),
            finished_at=utc_now(),
            updated_at=utc_now(),
            completed=job.get("completed", 0),
            failed=job.get("failed", 0),
            success=job.get("success", 0),
        )


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(STATIC_DIR / "index.html")
        if parsed.path.startswith("/static/"):
            return str(ROOT / parsed.path.lstrip("/"))
        return str(STATIC_DIR / parsed.path.lstrip("/"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.ensure_api_authenticated(parsed.path):
                return
            self.handle_api_get(parsed)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.ensure_api_authenticated(parsed.path):
                return
            self.handle_api_post(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def json_response(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def parse_cookies(self) -> SimpleCookie:
        cookie = SimpleCookie()
        raw = self.headers.get("Cookie", "")
        if raw:
            cookie.load(raw)
        return cookie

    def has_valid_auth_session(self) -> bool:
        if not auth_enabled():
            return True
        cookie = self.parse_cookies()
        morsel = cookie.get(auth_cookie_name())
        return bool(morsel and verify_auth_token(morsel.value))

    def ensure_api_authenticated(self, path: str) -> bool:
        if path in AUTH_PUBLIC_PATHS:
            return True
        if self.has_valid_auth_session():
            return True
        self.json_response({"error": "未登录或会话已过期"}, HTTPStatus.UNAUTHORIZED)
        return False

    def auth_cookie_header(self, value: str, *, max_age: int) -> str:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
        return (
            f"{auth_cookie_name()}={value}; Path=/; Max-Age={max_age}; "
            f"HttpOnly; SameSite=Lax{secure}"
        )

    def login_response(self, payload: dict):
        password = str(payload.get("password", ""))
        if not auth_enabled():
            self.json_response({"ok": True, "authenticated": True, "auth_enabled": False})
            return
        if not hmac.compare_digest(password, auth_password()):
            self.json_response({"error": "密码错误"}, HTTPStatus.UNAUTHORIZED)
            return
        body = json.dumps({"ok": True, "authenticated": True, "auth_enabled": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", self.auth_cookie_header(build_auth_token(), max_age=AUTH_SESSION_TTL_SECONDS))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def logout_response(self):
        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", self.auth_cookie_header("", max_age=0))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def csv_response(self, body: str, filename: str):
        raw = body.encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def excel_html_response(self, body: str, filename: str):
        raw = body.encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.ms-excel; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def save_export_file(self, filename: str, body: str) -> str:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / filename
        path.write_text(body, encoding="utf-8-sig")
        return str(path)

    def handle_api_get(self, parsed):
        query = parse_qs(parsed.query)
        try:
            with get_conn(DEFAULT_DB_PATH) as conn:
                if parsed.path == "/api/health":
                    self.json_response({"ok": True, "db": str(DEFAULT_DB_PATH)})
                elif parsed.path == "/api/auth/status":
                    self.json_response(
                        {
                            "ok": True,
                            "auth_enabled": auth_enabled(),
                            "authenticated": self.has_valid_auth_session(),
                        }
                    )
                elif parsed.path == "/api/projects":
                    self.json_response({"projects": list_projects(conn)})
                elif parsed.path == "/api/models":
                    self.json_response(
                        {
                            "models": [enrich_model_config(item) for item in list_model_configs(conn)],
                            "presets": PROVIDER_PRESETS,
                        }
                    )
                elif parsed.path == "/api/questions":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw and project_raw != "all" else None
                    self.json_response({"questions": list_questions(conn, project_id)})
                elif parsed.path == "/api/runs":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response({"runs": list_runs(conn, project_id)})
                elif parsed.path == "/api/runs/progress":
                    batch_id = str(query.get("batch_id", [""])[0]).strip()
                    if not batch_id:
                        raise ValueError("缺少 batch_id")
                    job = get_sampling_job(batch_id)
                    if not job:
                        batch = get_sampling_batch(conn, batch_id)
                        if not batch:
                            self.json_response({"error": "批次不存在"}, 404)
                            return
                        self.json_response(sampling_batch_to_progress(batch))
                        return
                    self.json_response(job)
                elif parsed.path == "/api/batches":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw and project_raw != "all" else None
                    self.json_response({"batches": list_sampling_batches(conn, project_id)})
                elif parsed.path.startswith("/api/batches/"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) not in {3, 4}:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    if len(parts) == 4 and parts[3] == "runs":
                        self.json_response({"runs": list_runs_by_batch(conn, batch_id)})
                        return
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    self.json_response({"batch": batch})
                elif parsed.path == "/api/analytics":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response(analytics(conn, project_id))
                elif parsed.path == "/api/export/runs.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    self.csv_response(runs_to_csv(list_runs(conn, project_id, limit=10000)), "geo-runs.csv")
                elif parsed.path == "/api/export/summary.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    self.csv_response(analytics_to_csv(analytics(conn, project_id)), "geo-summary.csv")
                elif parsed.path == "/api/export/runs.xls":
                    project_id = int(query.get("project_id", [0])[0])
                    self.excel_html_response(runs_to_excel_html(list_runs(conn, project_id, limit=10000)), "geo-runs.xls")
                elif parsed.path == "/api/export/summary.xls":
                    project_id = int(query.get("project_id", [0])[0])
                    self.excel_html_response(analytics_to_excel_html(analytics(conn, project_id)), "geo-summary.xls")
                elif parsed.path == "/api/export/runs/save":
                    project_id = int(query.get("project_id", [0])[0])
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    filename = f"geo-runs-{project_id}-{stamp}.xls"
                    path = self.save_export_file(filename, runs_to_excel_html(list_runs(conn, project_id, limit=10000)))
                    self.json_response({"ok": True, "path": path, "filename": filename})
                elif parsed.path == "/api/export/summary/save":
                    project_id = int(query.get("project_id", [0])[0])
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    filename = f"geo-summary-{project_id}-{stamp}.xls"
                    path = self.save_export_file(filename, analytics_to_excel_html(analytics(conn, project_id)))
                    self.json_response({"ok": True, "path": path, "filename": filename})
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)

    def handle_api_post(self, parsed):
        try:
            payload = self.read_json()
            if parsed.path == "/api/auth/login":
                self.login_response(payload)
                return
            if parsed.path == "/api/auth/logout":
                self.logout_response()
                return
            with get_conn(DEFAULT_DB_PATH) as conn:
                if parsed.path == "/api/projects":
                    project_id = create_project(conn, payload)
                    self.json_response({"id": project_id})
                elif parsed.path == "/api/projects/update":
                    update_project(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/projects/delete":
                    delete_project(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models":
                    model_id = create_model_config(conn, payload)
                    self.json_response({"id": model_id})
                elif parsed.path == "/api/models/update":
                    update_model_config(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models/delete":
                    delete_model_config(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models/test":
                    model_id = int(payload.get("id", 0))
                    model_config = get_model_config(conn, model_id)
                    if not model_config:
                        raise ValueError("模型配置不存在")
                    if payload.get("api_key") and payload.get("api_key") != "__KEEP__":
                        model_config["api_key"] = payload["api_key"]
                    if payload.get("api_base"):
                        model_config["api_base"] = payload["api_base"]
                    if payload.get("model"):
                        model_config["model"] = payload["model"]
                    self.json_response(test_model_config(model_config))
                elif parsed.path == "/api/models/preset":
                    preset = PROVIDER_PRESETS.get(payload.get("provider", ""))
                    if not preset:
                        raise ValueError("未找到默认服务商模板")
                    model_id = create_model_config(conn, preset)
                    self.json_response({"id": model_id})
                elif parsed.path == "/api/questions/seed":
                    count = seed_questions(conn, int(payload.get("project_id", 0)))
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/import":
                    count = import_questions_csv(conn, int(payload.get("project_id", 0)), payload.get("csv_text", ""))
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/import_rows":
                    rows = payload.get("rows", [])
                    if not rows and payload.get("file_base64"):
                        rows = self.decode_csv_rows(payload["file_base64"])
                    count = import_questions_rows(conn, int(payload.get("project_id", 0)), rows)
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/update":
                    update_question(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/questions/delete":
                    delete_question(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/runs/start":
                    project_id = int(payload.get("project_id", 0))
                    total = estimate_batch_total(conn, project_id, payload)
                    batch_id = payload.get("batch_id") or f"batch-{uuid.uuid4().hex[:10]}"
                    create_sampling_batch(
                        conn,
                        {
                            "batch_id": batch_id,
                            "project_id": project_id,
                            "status": "queued",
                            "total_count": total,
                            "success_count": 0,
                            "failed_count": 0,
                            "completed_count": 0,
                            "config": payload,
                            "created_at": utc_now(),
                            "updated_at": utc_now(),
                        },
                    )
                    set_sampling_job(
                        batch_id,
                        project_id=project_id,
                        total=total,
                        completed=0,
                        failed=0,
                        success=0,
                        status="queued",
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                    threading.Thread(
                        target=run_batch_in_background,
                        args=(batch_id, project_id, payload),
                        daemon=True,
                    ).start()
                    result = {"batch_id": batch_id, "total": total, "failed": 0, "success": 0, "status": "queued"}
                    self.json_response(result)
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)

    def decode_csv_rows(self, file_base64: str):
        raw = b64decode(file_base64.encode("utf-8")).decode("utf-8-sig")
        return list(__import__("csv").DictReader(raw.splitlines()))


def main():
    load_dotenv_file(ROOT / ".env")
    init_db(DEFAULT_DB_PATH)
    host = os.environ.get("GEO_AUDIT_HOST", "127.0.0.1")
    port = int(os.environ.get("GEO_AUDIT_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"制造业品牌 GEO 工作台已启动：http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
