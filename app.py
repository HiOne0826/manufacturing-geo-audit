from __future__ import annotations

import hashlib
import hmac
import csv
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from base64 import b64decode
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

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
    import_question_content_rows,
    import_questions_csv,
    import_questions_rows,
    init_db,
    list_failed_runs_by_batch,
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
    database_url,
)
from src.exporter import (
    analytics_to_csv,
    analytics_to_excel_html,
    runs_to_csv,
    runs_to_excel_html,
)
from src.analytics_summary import build_analytics_summary
from src.platforms import test_platform_name
from src.runtime_env import load_dotenv_file
from src.runner import estimate_batch_total
from src.db import utc_now
from src.tasks import mark_batch_failed, perform_batch, perform_rerun_failed


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"
EXPORT_DIR = ROOT / "exports"
APP_BASE_PATH = "/manufacturing-geo-audit"
SAMPLING_JOBS: dict[str, dict] = {}
SAMPLING_JOBS_LOCK = threading.Lock()
AUTH_PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}
AUTH_SESSION_TTL_SECONDS = 60 * 60 * 12
UI_PROVIDER_PRESETS = {key: value for key, value in PROVIDER_PRESETS.items() if key != "mock"}


def auth_password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()


def auth_cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "geo_audit_session").strip() or "geo_audit_session"


def auth_secret() -> str:
    return os.environ.get("APP_SESSION_SECRET", "").strip() or auth_password()


def auth_enabled() -> bool:
    return bool(auth_password())


def agent_api_token() -> str:
    return os.environ.get("AGENT_API_TOKEN", "").strip()


def task_queue_backend() -> str:
    return os.environ.get("TASK_QUEUE_BACKEND", "inline").strip().lower() or "inline"


def enqueue_rq_task(func, *args) -> str:
    try:
        from redis import Redis
        from rq import Queue
    except ImportError as exc:
        raise RuntimeError("缺少 RQ 依赖，请先安装：python3 -m pip install -r requirements-worker.txt") from exc
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_name = os.environ.get("RQ_QUEUE_NAME", "geo-audit")
    timeout = int(os.environ.get("RQ_JOB_TIMEOUT", "3600"))
    queue = Queue(queue_name, connection=Redis.from_url(redis_url))
    job = queue.enqueue(func, *args, job_timeout=timeout)
    return job.id


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


def planned_source_statuses(conn, batch: dict) -> dict[str, dict]:
    config = batch.get("config") or {}
    repeat_count = max(1, min(int(config.get("repeat_count", 1) or 1), 10))
    questions = list_questions(conn, int(batch["project_id"]))
    question_count = len(questions)
    statuses: dict[str, dict] = {}
    for item in config.get("models") or []:
        model_config_id = int(item.get("model_config_id", 0) or 0)
        model_config = get_model_config(conn, model_config_id)
        if not model_config:
            continue
        provider = model_config.get("provider", "")
        model = str(item.get("runtime_model") or model_config.get("model") or provider)
        platform = test_platform_name(provider, model)
        current = statuses.setdefault(
            platform,
            {
                "test_platform": platform,
                "provider": provider,
                "model": model,
                "total": 0,
                "completed": 0,
                "success": 0,
                "failed": 0,
                "queued": 0,
                "running": 0,
                "status": "queued",
                "avg_latency_ms": 0,
                "last_error": "",
                "_latency_sum": 0,
                "_latency_count": 0,
            },
        )
        current["total"] += question_count * repeat_count
    return statuses


def active_project_batch(conn, project_id: int) -> dict | None:
    for batch in list_sampling_batches(conn, project_id):
        if batch.get("status") in {"queued", "running"}:
            return batch
    return None


def latest_logical_runs(rows: list[dict]) -> list[dict]:
    latest = []
    seen = set()
    for row in rows:
        key = (
            row.get("question_id"),
            row.get("model_config_id"),
            bool(row.get("search_enabled")),
            row.get("search_mode") or "",
            row.get("thinking_type") or "",
            row.get("reasoning_effort") or "",
            row.get("thinking_budget"),
            int(row.get("repeat_index") or 1),
        )
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


def finalize_source_status(item: dict, batch_status: str, current_platform: str = "", infer_running: bool = False) -> dict:
    completed = int(item.get("success", 0)) + int(item.get("failed", 0))
    total = max(int(item.get("total", 0)), completed)
    item["total"] = total
    running = 0
    if batch_status == "running" and completed < total:
        if infer_running or (current_platform and item["test_platform"] == current_platform):
            running = 1
    queued = max(total - completed - running, 0)
    if item.get("failed", 0):
        status = "failed"
    elif running:
        status = "running"
    elif total > 0 and completed >= total:
        status = "completed"
    else:
        status = "queued"
    latency_count = int(item.pop("_latency_count", 0) or 0)
    latency_sum = int(item.pop("_latency_sum", 0) or 0)
    return {
        **item,
        "completed": completed,
        "queued": queued,
        "running": running,
        "status": status,
        "avg_latency_ms": round(latency_sum / latency_count) if latency_count else 0,
    }


def source_statuses_for_batch(conn, batch: dict, job: dict | None = None) -> list[dict]:
    statuses = planned_source_statuses(conn, batch)
    rows = latest_logical_runs(list_runs_by_batch(conn, batch["batch_id"]))
    for row in rows:
        platform = row.get("test_platform") or test_platform_name(row.get("provider"), row.get("model"))
        current = statuses.setdefault(
            platform,
            {
                "test_platform": platform,
                "provider": row.get("provider", ""),
                "model": row.get("model", ""),
                "total": 0,
                "completed": 0,
                "success": 0,
                "failed": 0,
                "queued": 0,
                "running": 0,
                "status": "queued",
                "avg_latency_ms": 0,
                "last_error": "",
                "_latency_sum": 0,
                "_latency_count": 0,
            },
        )
        if not current.get("provider"):
            current["provider"] = row.get("provider", "")
        if not current.get("model"):
            current["model"] = row.get("model", "")
        if row.get("status") == "success":
            current["success"] += 1
        elif row.get("status") == "failed":
            current["failed"] += 1
            if not current.get("last_error"):
                current["last_error"] = str(row.get("error_message") or "")[:300]
        latency = int(row.get("latency_ms") or 0)
        if latency:
            current["_latency_sum"] += latency
            current["_latency_count"] += 1
    current_platform = ""
    if job:
        current_platform = test_platform_name(job.get("current_provider"), job.get("current_model"))
    infer_running = not bool(job) and batch.get("status") == "running"
    finalized = [
        finalize_source_status(item, str(batch.get("status") or "queued"), current_platform, infer_running)
        for item in statuses.values()
    ]
    return sorted(finalized, key=lambda item: item["test_platform"])


def progress_response_for_batch(conn, batch: dict, job: dict | None = None) -> dict:
    base = sampling_batch_to_progress(batch)
    if job:
        base.update(job)
    base["source_statuses"] = source_statuses_for_batch(conn, batch, job)
    return base


def run_batch_in_background(batch_id: str, project_id: int, payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())
    try:
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

        result = perform_batch(batch_id, project_id, payload, progress_hook=on_progress, db_target=DEFAULT_DB_PATH)
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
        mark_batch_failed(batch_id, str(exc), job, db_target=DEFAULT_DB_PATH)
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


def rerun_failed_in_background(batch_id: str, payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())
    try:
        result = perform_rerun_failed(batch_id, payload, db_target=DEFAULT_DB_PATH)
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
        mark_batch_failed(batch_id, str(exc), db_target=DEFAULT_DB_PATH)
        set_sampling_job(batch_id, status="failed", error=str(exc), finished_at=utc_now(), updated_at=utc_now())


def dispatch_batch(batch_id: str, project_id: int, payload: dict) -> str | None:
    if task_queue_backend() == "rq":
        return enqueue_rq_task(perform_batch, batch_id, project_id, payload)
    threading.Thread(
        target=run_batch_in_background,
        args=(batch_id, project_id, payload),
        daemon=True,
    ).start()
    return None


def dispatch_rerun_failed(batch_id: str, payload: dict) -> str | None:
    if task_queue_backend() == "rq":
        return enqueue_rq_task(perform_rerun_failed, batch_id, payload)
    threading.Thread(
        target=rerun_failed_in_background,
        args=(batch_id, payload),
        daemon=True,
    ).start()
    return None
class Handler(SimpleHTTPRequestHandler):
    def normalize_app_path(self, path: str) -> str:
        if path == APP_BASE_PATH:
            return "/"
        if path.startswith(f"{APP_BASE_PATH}/"):
            return path[len(APP_BASE_PATH):] or "/"
        return path

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        normalized_path = self.normalize_app_path(parsed.path)
        if FRONTEND_DIST_DIR.exists():
            candidate = FRONTEND_DIST_DIR / normalized_path.lstrip("/")
            if normalized_path == "/" or not candidate.exists() or candidate.is_dir():
                return str(FRONTEND_DIST_DIR / "index.html")
            return str(candidate)
        if normalized_path == "/":
            return str(STATIC_DIR / "index.html")
        if normalized_path.startswith("/static/"):
            return str(ROOT / normalized_path.lstrip("/"))
        return str(STATIC_DIR / normalized_path.lstrip("/"))

    def do_GET(self):
        parsed = urlparse(self.path)
        parsed = parsed._replace(path=self.normalize_app_path(parsed.path))
        if parsed.path.startswith("/api/"):
            if not self.ensure_api_authenticated(parsed.path):
                return
            self.handle_api_get(parsed)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        parsed = parsed._replace(path=self.normalize_app_path(parsed.path))
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
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
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
        if path.startswith("/api/agent/"):
            if self.has_valid_agent_token():
                return True
            self.json_response({"error": "Agent token 无效或未配置"}, HTTPStatus.UNAUTHORIZED)
            return False
        if path in AUTH_PUBLIC_PATHS:
            return True
        if self.has_valid_auth_session():
            return True
        self.json_response({"error": "未登录或会话已过期"}, HTTPStatus.UNAUTHORIZED)
        return False

    def has_valid_agent_token(self) -> bool:
        token = agent_api_token()
        if not token:
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):].strip(), token)

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
                    self.json_response(
                        {
                            "ok": True,
                            "db": "postgres" if database_url() else str(DEFAULT_DB_PATH),
                            "task_queue_backend": task_queue_backend(),
                        }
                    )
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
                            "presets": UI_PROVIDER_PRESETS,
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
                    job = None if task_queue_backend() == "rq" else get_sampling_job(batch_id)
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    self.json_response(progress_response_for_batch(conn, batch, job))
                elif parsed.path.startswith("/api/agent/batches/"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) not in {4, 5}:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[3]
                    if len(parts) == 5 and parts[4] == "export":
                        self.json_response({"batch_id": batch_id, "format": "xls", "path": f"/api/export/batches/{batch_id}/runs.xls"})
                        return
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    self.json_response({"batch": sampling_batch_to_progress(batch)})
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
                elif parsed.path == "/api/analytics/summary":
                    project_id = int(query.get("project_id", [0])[0])
                    batch_id = str(query.get("batch_id", [""])[0]).strip() or None
                    self.json_response(build_analytics_summary(conn, project_id, batch_id))
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
                elif parsed.path.startswith("/api/export/batches/"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 5:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[3]
                    export_name = parts[4]
                    rows = list_runs_by_batch(conn, batch_id)
                    if export_name == "runs.xls":
                        self.excel_html_response(runs_to_excel_html(rows), f"geo-batch-{batch_id}-runs.xls")
                        return
                    if export_name == "summary.xls":
                        batch = get_sampling_batch(conn, batch_id)
                        if not batch:
                            self.json_response({"error": "批次不存在"}, 404)
                            return
                        self.excel_html_response(analytics_to_excel_html(analytics(conn, int(batch["project_id"]))), f"geo-batch-{batch_id}-summary.xls")
                        return
                    self.json_response({"error": "接口不存在"}, 404)
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
                def commit_json(data, status: int = 200):
                    conn.commit()
                    self.json_response(data, status)

                if parsed.path == "/api/projects":
                    project_id = create_project(conn, payload)
                    commit_json({"id": project_id})
                elif parsed.path == "/api/projects/update":
                    update_project(conn, payload)
                    commit_json({"ok": True})
                elif parsed.path == "/api/projects/delete":
                    delete_project(conn, int(payload.get("id", 0)))
                    commit_json({"ok": True})
                elif parsed.path == "/api/models":
                    model_id = create_model_config(conn, payload)
                    commit_json({"id": model_id})
                elif parsed.path == "/api/models/update":
                    update_model_config(conn, payload)
                    commit_json({"ok": True})
                elif parsed.path == "/api/models/delete":
                    delete_model_config(conn, int(payload.get("id", 0)))
                    commit_json({"ok": True})
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
                    if preset.get("provider") == "mock":
                        raise ValueError("正式页面不允许创建 Mock 模型")
                    model_id = create_model_config(conn, preset)
                    commit_json({"id": model_id})
                elif parsed.path == "/api/questions/seed":
                    count = seed_questions(conn, int(payload.get("project_id", 0)))
                    commit_json({"count": count})
                elif parsed.path == "/api/questions/import":
                    count = import_questions_csv(conn, int(payload.get("project_id", 0)), payload.get("csv_text", ""))
                    commit_json({"count": count})
                elif parsed.path == "/api/questions/import_rows":
                    rows = payload.get("rows", [])
                    if not rows and payload.get("file_base64"):
                        rows = self.decode_table_rows(payload["file_base64"], payload.get("file_name", ""))
                    count = import_questions_rows(conn, int(payload.get("project_id", 0)), rows)
                    commit_json({"count": count})
                elif parsed.path == "/api/questions/import_file":
                    rows = self.decode_table_rows(payload.get("file_base64", ""), payload.get("file_name", ""))
                    count = import_question_content_rows(conn, int(payload.get("project_id", 0)), rows)
                    commit_json({"count": count})
                elif parsed.path == "/api/questions/update":
                    update_question(conn, payload)
                    commit_json({"ok": True})
                elif parsed.path == "/api/questions/delete":
                    delete_question(conn, int(payload.get("id", 0)))
                    commit_json({"ok": True})
                elif parsed.path == "/api/runs/start":
                    project_id = int(payload.get("project_id", 0))
                    total = estimate_batch_total(conn, project_id, payload)
                    existing_batch = active_project_batch(conn, project_id)
                    if existing_batch:
                        existing_job = get_sampling_job(existing_batch["batch_id"])
                        result = progress_response_for_batch(conn, existing_batch, existing_job)
                        result["task_queue_backend"] = task_queue_backend()
                        result["existing"] = True
                        self.json_response(result)
                        return
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
                    conn.commit()
                    job_id = dispatch_batch(batch_id, project_id, payload)
                    result = {
                        "batch_id": batch_id,
                        "total": total,
                        "failed": 0,
                        "success": 0,
                        "status": "queued",
                        "task_queue_backend": task_queue_backend(),
                        "job_id": job_id,
                    }
                    self.json_response(result)
                elif parsed.path == "/api/agent/batches":
                    project_id = int(payload.get("project_id", 0))
                    if payload.get("csv_text"):
                        import_questions_csv(conn, project_id, payload.get("csv_text", ""))
                    model_ids = [int(item) for item in payload.get("model_ids", [])]
                    if not model_ids:
                        model_ids = [int(item.get("model_config_id", 0)) for item in payload.get("models", []) if item.get("model_config_id")]
                    run_payload = {
                        "project_id": project_id,
                        "models": payload.get("models") or [{"model_config_id": model_id, "search_enabled": False} for model_id in model_ids],
                        "repeat_count": int((payload.get("options") or {}).get("repeat_count", payload.get("repeat_count", 1)) or 1),
                        "max_workers": (payload.get("options") or {}).get("max_workers", payload.get("max_workers")),
                        "retry_count": (payload.get("options") or {}).get("retry_count", payload.get("retry_count")),
                    }
                    total = estimate_batch_total(conn, project_id, run_payload)
                    batch_id = payload.get("batch_id") or f"batch-{uuid.uuid4().hex[:10]}"
                    create_sampling_batch(
                        conn,
                        {
                            "batch_id": batch_id,
                            "project_id": project_id,
                            "status": "queued",
                            "total_count": total,
                            "config": run_payload,
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
                    conn.commit()
                    job_id = dispatch_batch(batch_id, project_id, run_payload)
                    self.json_response(
                        {
                            "batch_id": batch_id,
                            "total": total,
                            "status": "queued",
                            "task_queue_backend": task_queue_backend(),
                            "job_id": job_id,
                        }
                    )
                elif parsed.path.startswith("/api/agent/batches/") and parsed.path.endswith("/rerun_failed"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 5:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[3]
                    failed_runs = list_failed_runs_by_batch(conn, batch_id)
                    job_id = dispatch_rerun_failed(batch_id, payload)
                    self.json_response(
                        {
                            "batch_id": batch_id,
                            "total": len(failed_runs),
                            "status": "queued",
                            "task_queue_backend": task_queue_backend(),
                            "job_id": job_id,
                        }
                    )
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/rerun_failed"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    failed_runs = list_failed_runs_by_batch(conn, batch_id)
                    set_sampling_job(
                        batch_id,
                        project_id=batch["project_id"],
                        total=len(failed_runs),
                        completed=0,
                        failed=0,
                        success=0,
                        status="queued",
                        updated_at=utc_now(),
                    )
                    conn.commit()
                    job_id = dispatch_rerun_failed(batch_id, payload)
                    self.json_response(
                        {
                            "batch_id": batch_id,
                            "total": len(failed_runs),
                            "status": "queued",
                            "task_queue_backend": task_queue_backend(),
                            "job_id": job_id,
                        }
                    )
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)

    def decode_table_rows(self, file_base64: str, file_name: str = ""):
        if not file_base64:
            return []
        raw = b64decode(file_base64.encode("utf-8"))
        lower_name = file_name.lower()
        if lower_name.endswith(".xlsx") or raw.startswith(b"PK\x03\x04"):
            return parse_xlsx_rows(raw)
        text = raw.decode("utf-8-sig")
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
            delimiter = dialect.delimiter
        except csv.Error:
            pass
        return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def parse_xlsx_rows(raw: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        sheet_path = first_xlsx_sheet_path(archive)
        root = ElementTree.fromstring(archive.read(sheet_path))
    table: list[list[str]] = []
    for row in root.iter():
        if local_name(row.tag) != "row":
            continue
        values: list[str] = []
        for cell in row:
            if local_name(cell.tag) != "c":
                continue
            cell_ref = cell.attrib.get("r", "")
            column_index = xlsx_column_index(cell_ref)
            while len(values) <= column_index:
                values.append("")
            values[column_index] = xlsx_cell_value(cell, shared_strings)
        if any(value.strip() for value in values):
            table.append(values)
    if not table:
        return []
    header = [value.strip() for value in table[0]]
    rows: list[dict[str, str]] = []
    for values in table[1:]:
        rows.append({header[index]: values[index].strip() for index in range(min(len(header), len(values))) if header[index]})
    return rows


def read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root:
        if local_name(item.tag) != "si":
            continue
        parts = [node.text or "" for node in item.iter() if local_name(node.tag) == "t"]
        strings.append("".join(parts))
    return strings


def first_xlsx_sheet_path(archive: zipfile.ZipFile) -> str:
    names = archive.namelist()
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = next((node for node in workbook.iter() if local_name(node.tag) == "sheet"), None)
        relation_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if first_sheet is not None else ""
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for relation in rels:
            if relation.attrib.get("Id") == relation_id:
                target = relation.attrib.get("Target", "")
                normalized_target = target.lstrip("/")
                return normalized_target if normalized_target.startswith("xl/") else f"xl/{normalized_target}"
    sheet_names = sorted(name for name in names if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
    if not sheet_names:
        raise ValueError("未找到 XLSX 工作表")
    return sheet_names[0]


def xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter() if local_name(node.tag) == "t")
    value_node = next((node for node in cell if local_name(node.tag) == "v"), None)
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        index = int(value)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return value


def xlsx_column_index(cell_ref: str) -> int:
    letters = re.match(r"([A-Z]+)", cell_ref.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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
