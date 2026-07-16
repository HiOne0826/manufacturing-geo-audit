from __future__ import annotations

import hashlib
import hmac
import csv
import io
import json
import os
import re
import shutil
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

from src.adapters import PROVIDER_PRESETS, enrich_model_config, mask_key, test_model_config
from src.db import (
    DEFAULT_DB_PATH,
    analytics,
    create_outbox_event,
    create_sampling_batch,
    create_model_config,
    create_project,
    delete_model_config,
    delete_project,
    delete_question,
    get_model_config,
    get_project,
    get_project_impact,
    get_sampling_batch,
    get_sampling_batch_by_client_request,
    get_sampling_task,
    get_conn,
    import_question_content_rows,
    import_questions_csv,
    import_questions_rows,
    init_db,
    list_failed_runs_by_batch,
    list_model_configs,
    list_execution_attempts,
    list_projects,
    list_questions,
    list_runs_by_batch,
    list_runs,
    list_sampling_batches,
    list_sampling_tasks,
    mark_outbox_delivered,
    mark_outbox_failed,
    sampling_task_counts,
    reset_resumable_sampling_tasks,
    seed_questions,
    update_sampling_batch,
    update_sampling_batch_cas,
    update_sampling_batch_metadata,
    update_sampling_task,
    update_model_config,
    update_project,
    archive_project,
    update_question,
    database_url,
    reliability_status,
)
from src.exporter import (
    analytics_to_csv,
    analytics_to_excel_html,
    runs_to_csv,
    runs_to_excel_html,
)
from src.analytics_summary import build_analytics_summary
from src.platforms import test_platform_name
from src.runtime_env import BOCHA_SEARCH_ENV_KEYS, load_dotenv_file, provider_has_credentials, resolve_bocha_search_api_key, resolve_provider_api_key
from src.runner import estimate_batch_total, prepare_batch_ledger
from src.reliability import batch_outcome as reliability_batch_outcome, classify_error, stable_config_fingerprint
from src.reconciler import reconcile_once
from src.provider_health import circuit_blocks_start, credential_fingerprint, list_provider_health, safe_endpoint
from src.provider_probes import run_active_probe, start_optional_probe_scheduler
from src.delivery import (
    DeliveryError,
    archive_report,
    build_export,
    compare_batches,
    create_report_version,
    ensure_delivery_schema,
    freeze_report,
    get_run_review,
    list_audit_events,
    list_report_versions,
    review_report,
    review_run,
    write_audit,
)
from src.migrations import MigrationRunner
from src.db import utc_now
from src.tasks import mark_batch_failed, mark_rq_job_failed, perform_batch, perform_rerun_failed, perform_rerun_runs, perform_resume_batch, perform_sampling_task, request_batch_pause
from src.deepseek_web import deepseek_web_status
from src.deepseek_web_tasks import (
    batch_is_web,
    create_web_sampling_tasks,
    enqueue_next_web_task,
    resume_web_batch,
    web_batch_mode,
)


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"
EXPORT_DIR = ROOT / "exports"
APP_BASE_PATH = "/manufacturing-geo-audit"
SAMPLING_JOBS: dict[str, dict] = {}
SAMPLING_JOBS_LOCK = threading.Lock()
AUTH_PUBLIC_PATHS = {"/api/health", "/api/health/live", "/api/health/ready", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}
AUTH_SESSION_TTL_SECONDS = 60 * 60 * 12
UI_PROVIDER_PRESETS = {key: value for key, value in PROVIDER_PRESETS.items() if key != "mock"}


def api_json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def update_env_file_values(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def bocha_search_status() -> dict:
    key = resolve_bocha_search_api_key()
    return {
        "configured": bool(key),
        "api_key_masked": mask_key(key),
        "env_keys": list(BOCHA_SEARCH_ENV_KEYS),
        "web_search_param_path": "BOCHA_API_KEY; POST https://api.bochaai.com/v1/web-search; query/count/freshness/summary/include",
        "used_by": ["DeepSeek 联网搜索"],
    }


def readiness_status(conn) -> tuple[dict, int]:
    checks: dict[str, dict] = {}
    try:
        conn.execute("SELECT 1").fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}
    try:
        marker = f"ready-{uuid.uuid4().hex}"
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS readiness_write_probe (value TEXT)")
        conn.execute("INSERT INTO readiness_write_probe (value) VALUES (?)", (marker,))
        conn.execute("DELETE FROM readiness_write_probe WHERE value = ?", (marker,))
        checks["database_write"] = {"ok": True}
    except Exception as exc:
        checks["database_write"] = {"ok": False, "error": str(exc)[:200]}
    try:
        dialect = "postgres" if database_url() else "sqlite"
        migration = MigrationRunner(conn, dialect).status()
        checks["schema"] = {
            "ok": bool(migration.get("ok") and migration.get("up_to_date")),
            "current_version": migration.get("current_version"),
            "target_version": migration.get("target_version"),
            "pending": migration.get("pending", []),
            "drift": migration.get("drift", []),
        }
    except Exception as exc:
        checks["schema"] = {"ok": False, "error": str(exc)[:200]}
    usage = shutil.disk_usage(ROOT)
    minimum_free = int(os.environ.get("MIN_FREE_DISK_BYTES", str(512 * 1024 * 1024)))
    checks["disk"] = {"ok": usage.free >= minimum_free, "free_bytes": usage.free, "minimum_free_bytes": minimum_free}
    backend = task_queue_backend()
    if backend == "rq":
        try:
            from redis import Redis
            from rq import Queue, Worker
            redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), socket_connect_timeout=1, socket_timeout=1)
            checks["redis"] = {"ok": bool(redis.ping())}
            queue_name = os.environ.get("RQ_QUEUE_NAME", "geo-audit")
            queue = Queue(queue_name, connection=redis)
            checks["queue"] = {"ok": True, "name": queue_name, "queued_jobs": len(queue)}
            workers = Worker.all(connection=redis, queue=queue)
            checks["workers"] = {"ok": bool(workers), "count": len(workers)}
        except Exception as exc:
            checks["redis"] = {"ok": False, "error": str(exc)[:200]}
            checks.setdefault("queue", {"ok": False, "error": "队列不可用"})
            checks.setdefault("workers", {"ok": False, "count": 0})
    else:
        checks["redis"] = {"ok": True, "skipped": True, "reason": "inline backend"}
        checks["queue"] = {"ok": True, "skipped": True, "reason": "inline backend"}
        checks["workers"] = {"ok": True, "skipped": True, "reason": "inline backend"}
    try:
        durability = reliability_status(
            conn,
            worker_stale_seconds=int(os.environ.get("WORKER_HEARTBEAT_STALE_SECONDS", "60")),
        )
        max_pending_outbox = int(os.environ.get("READY_MAX_PENDING_OUTBOX", "1000"))
        max_outbox_attempts = int(os.environ.get("READY_MAX_OUTBOX_ATTEMPTS", "10"))
        outbox = durability["outbox"]
        tasks = durability["tasks"]
        checks["outbox"] = {
            "ok": outbox["pending"] <= max_pending_outbox and outbox["max_attempt_count"] < max_outbox_attempts,
            **outbox,
        }
        checks["leases"] = {
            "ok": tasks["expired_leases"] == 0,
            **tasks,
        }
        if backend == "rq":
            checks["worker_heartbeats"] = {
                "ok": durability["workers"]["available"] > 0,
                **durability["workers"],
            }
        else:
            checks["worker_heartbeats"] = {
                "ok": True,
                "skipped": True,
                "reason": "inline backend",
                **durability["workers"],
            }
        checks["attempts"] = {
            "ok": True,
            "warning": durability["attempts"]["uncertain"] > 0,
            **durability["attempts"],
        }
    except Exception as exc:
        durability = {}
        checks["durability"] = {"ok": False, "error": str(exc)[:200]}
    ok = all(bool(item.get("ok")) for item in checks.values())
    return {"ok": ok, "checks": checks, "durability": durability, "task_queue_backend": backend}, 200 if ok else 503


def source_health_rows(conn) -> list[dict]:
    rows = []
    passive = list_provider_health(conn)
    bocha_ready = bocha_search_status()["configured"]
    web_status = deepseek_web_status()
    for model in list_model_configs(conn):
        provider = str(model.get("provider") or "")
        endpoint = safe_endpoint(str(model.get("api_base") or ""))
        credential_fp = credential_fingerprint(resolve_provider_api_key(provider, str(model.get("api_key") or "")))
        exit_region = str(model.get("exit_region") or os.environ.get("PROVIDER_EXIT_REGION", "")).strip()
        configured = provider == "mock" or provider_has_credentials(provider, str(model.get("api_key") or ""))
        if provider == "deepseek_web":
            configured = bool(web_status.get("enabled") and web_status.get("playwright_installed") and web_status.get("auth_configured"))
        pure_ready = bool(model.get("supports_pure")) and configured
        search_dependency_ready = bocha_ready if provider == "deepseek" else True
        search_ready = bool(model.get("supports_search")) and configured and search_dependency_ready
        rows.append(
            {
                "source": provider,
                "model_config_id": model.get("id"),
                "label": model.get("label", provider),
                "active": bool(model.get("active")),
                "configured": configured,
                "modes": {
                    "pure": {"ready": pure_ready},
                    "search": {"ready": search_ready, "dependency_ready": search_dependency_ready},
                },
                "passive": [
                    {key: item.get(key) for key in (
                        "model", "mode", "status", "consecutive_failures", "success_count",
                        "failure_count", "last_error_code", "circuit_open_until",
                        "half_open_trial_until", "last_success_at", "last_failure_at",
                        "checked_at", "scope", "window",
                    )}
                    for item in passive
                    if item.get("provider") == provider
                    and item.get("model") == model.get("model")
                    and (
                        provider == "deepseek_web"
                        or (
                            (item.get("scope") or {}).get("endpoint", "") == endpoint
                            and (item.get("scope") or {}).get("credential_fingerprint", "unconfigured") == credential_fp
                            and (item.get("scope") or {}).get("exit_region", "") == exit_region
                        )
                    )
                ],
            }
        )
    return rows


def batch_outcome(batch: dict) -> str:
    return reliability_batch_outcome(
        str(batch.get("status") or ""),
        int(batch.get("success_count", 0) or 0),
        int(batch.get("failed_count", 0) or 0),
        int(batch.get("completed_count", 0) or 0),
        int(batch.get("total_count", 0) or 0),
    )


def start_preflight(conn, payload: dict) -> dict:
    blockers: list[dict] = []
    warnings: list[dict] = []
    project_id = int(payload.get("project_id") or 0)
    project = get_project(conn, project_id)
    if not project:
        blockers.append({"code": "PROJECT_NOT_FOUND", "message": "项目不存在", "fix_path": "/projects"})
    elif project.get("archived_at"):
        blockers.append({"code": "PROJECT_ARCHIVED", "message": "归档项目不能启动批次", "fix_path": "/projects"})
    question_count = len(list_questions(conn, project_id)) if project else 0
    if project and question_count == 0:
        blockers.append({"code": "QUESTIONS_EMPTY", "message": "当前项目没有可采样问题", "fix_path": "/questions"})
    selected = payload.get("models") or []
    if not selected:
        blockers.append({"code": "MODELS_EMPTY", "message": "至少选择一个信息源", "fix_path": "/models"})
    health_by_id = {int(item.get("model_config_id") or 0): item for item in source_health_rows(conn)}
    configs = {int(item["id"]): item for item in list_model_configs(conn)}
    for choice in selected:
        model_id = int(choice.get("model_config_id") or 0)
        config = configs.get(model_id)
        mode = "search" if choice.get("search_enabled") else "pure"
        if not config:
            blockers.append({"code": "MODEL_NOT_FOUND", "message": f"模型配置 #{model_id} 不存在", "model_config_id": model_id, "fix_path": "/models"})
            continue
        if not config.get("active"):
            blockers.append({"code": "MODEL_DISABLED", "message": f"{config.get('label')} 未启用", "model_config_id": model_id, "fix_path": "/models"})
            continue
        supports = bool(config.get("supports_search" if mode == "search" else "supports_pure"))
        health = health_by_id.get(model_id, {})
        mode_health = (health.get("modes") or {}).get(mode, {})
        if not supports:
            blockers.append({"code": "MODE_UNSUPPORTED", "message": f"{config.get('label')} 不支持{mode}", "model_config_id": model_id, "mode": mode, "fix_path": "/models"})
        elif not mode_health.get("ready"):
            blockers.append({"code": "SOURCE_NOT_READY", "message": f"{config.get('label')} 的{mode}模式未就绪", "model_config_id": model_id, "mode": mode, "fix_path": "/settings"})
        passive = [item for item in (health.get("passive") or []) if item.get("model") == config.get("model") and item.get("mode") == mode]
        if any(circuit_blocks_start(item) for item in passive):
            blockers.append({"code": "SOURCE_CIRCUIT_OPEN", "message": f"{config.get('label')} 的{mode}模式正在熔断", "model_config_id": model_id, "mode": mode, "fix_path": "/settings"})
    ready_snapshot, _ = readiness_status(conn)
    for name in ("database", "database_write", "schema", "disk", "redis", "queue", "workers", "worker_heartbeats"):
        check = (ready_snapshot.get("checks") or {}).get(name)
        if check and not check.get("ok"):
            blockers.append({"code": f"SYSTEM_{name.upper()}_NOT_READY", "message": f"系统依赖 {name} 未就绪", "fix_path": "/settings"})
    total = estimate_batch_total(conn, project_id, payload) if project and selected and question_count else 0
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "project_id": project_id,
        "question_count": question_count,
        "model_count": len(selected),
        "total_tasks": total,
        "estimated_duration": None,
        "checked_at": utc_now(),
    }


def immutable_batch_config_snapshot(conn, project_id: int, config: dict) -> dict:
    questions = list_questions(conn, project_id)
    model_rows = {int(item["id"]): item for item in list_model_configs(conn)}
    matrix = []
    for choice in config.get("models") or []:
        model_id = int(choice.get("model_config_id") or 0)
        model = model_rows.get(model_id, {})
        matrix.append(
            {
                **choice,
                "provider": model.get("provider", ""),
                "model": model.get("model", ""),
                "model_version": model.get("model_version", ""),
                "api_base": model.get("api_base", ""),
            }
        )
    return {
        **config,
        "project_id": project_id,
        "question_ids": [int(item["id"]) for item in questions],
        "question_source_ids": [str(item.get("question_id") or "") for item in questions],
        "question_count": len(questions),
        "model_matrix": matrix,
        "captured_at": utc_now(),
    }


def batch_request_fingerprint(project_id: int, payload: dict) -> str:
    identity = {
        "project_id": project_id,
        "batch_name": str(payload.get("batch_name") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "purpose": str(payload.get("purpose") or "").strip(),
        "tags": sorted(str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()),
        "repeat_count": int(payload.get("repeat_count") or 1),
        "provider_mode": str(payload.get("provider_mode") or ""),
        "models": payload.get("models") or [],
    }
    return stable_config_fingerprint(identity)


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
    enqueue_kwargs = {"job_timeout": timeout, "on_failure": mark_rq_job_failed}
    try:
        job = queue.enqueue(func, *args, **enqueue_kwargs)
    except TypeError:
        enqueue_kwargs.pop("on_failure")
        job = queue.enqueue(func, *args, **enqueue_kwargs)
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
        "batch_name": batch.get("batch_name", ""),
        "description": batch.get("description", ""),
        "purpose": batch.get("purpose", ""),
        "tags": batch.get("tags", []),
        "config_snapshot": batch.get("config_snapshot", batch.get("config", {})),
        "generation": int(batch.get("generation", 1) or 1),
        "lock_version": int(batch.get("lock_version", 0) or 0),
        "archived_at": batch.get("archived_at"),
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
        if batch.get("status") in {"queued", "running", "pause_requested", "paused"}:
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


def attempt_history_rows(rows: list[dict]) -> list[dict]:
    attempts: list[dict] = []
    counters: dict[tuple, int] = {}
    for row in reversed(rows):
        key = (
            row.get("question_id"), row.get("model_config_id"), bool(row.get("search_enabled")),
            row.get("search_mode") or "", row.get("thinking_type") or "",
            row.get("reasoning_effort") or "", row.get("thinking_budget"), int(row.get("repeat_index") or 1),
        )
        counters[key] = counters.get(key, 0) + 1
        attempts.append(
            {
                "id": row.get("id"),
                "run_id": row.get("run_id"),
                "question_id": row.get("question_id"),
                "source_question_id": row.get("source_question_id"),
                "model_config_id": row.get("model_config_id"),
                "provider": row.get("provider"),
                "model": row.get("model"),
                "test_platform": row.get("test_platform"),
                "repeat_index": row.get("repeat_index"),
                "attempt_index": counters[key],
                "status": row.get("status"),
                "is_current": bool(row.get("is_current", 1)),
                "requested_at": row.get("requested_at"),
                "latency_ms": row.get("latency_ms", 0),
                "cost_estimate": row.get("cost_estimate", 0),
                "error_message": row.get("error_message", ""),
                "error_category": classify_error(str(row.get("error_message") or "")).code.value if row.get("status") == "failed" else "",
                "superseded_at": row.get("superseded_at"),
            }
        )
    return list(reversed(attempts))


def attach_task_ids(conn, batch_id: str, rows: list[dict]) -> list[dict]:
    task_by_run = {
        str(item.get("run_id") or ""): str(item.get("task_id") or "")
        for item in list_execution_attempts(conn, batch_id)
        if item.get("run_id") and item.get("task_id")
    }
    return [{**row, "task_id": task_by_run.get(str(row.get("run_id") or ""), "")} for row in rows]


def planned_batch_total(conn, batch: dict) -> int:
    try:
        return estimate_batch_total(conn, int(batch["project_id"]), batch.get("config") or {})
    except Exception:
        return int(batch.get("total_count", 0) or 0)


def latest_run_progress(rows: list[dict], total: int) -> dict:
    total = max(total, len(rows))
    success = sum(1 for row in rows if row.get("status") == "success")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    completed = success + failed
    running = max(total - completed, 0)
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success": success,
        "running": running,
    }


def failure_category_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "failed":
            continue
        code = classify_error(str(row.get("error_message") or "")).code.value
        counts[code] = counts.get(code, 0) + 1
    return counts


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
    elif batch_status == "paused" and completed < total:
        status = "paused"
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
    rows = latest_logical_runs(list_runs_by_batch(conn, batch["batch_id"]))
    statuses = planned_source_statuses(conn, batch)
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
    if batch_is_web(batch):
        counts = sampling_task_counts(conn, batch["batch_id"])
        base.update(
            {
                "total": counts["total"],
                "completed": counts["completed"],
                "failed": counts["failed"] + counts["blocked"],
                "success": counts["success"],
                "running": counts["running"],
                "queued": counts["queued"],
                "blocked": counts["blocked"],
                "status": batch.get("status", "queued"),
            }
        )
        base["total_count"] = counts["total"]
        base["completed_count"] = counts["completed"]
        base["success_count"] = counts["success"]
        base["failed_count"] = counts["failed"] + counts["blocked"]
        base["source_statuses"] = source_statuses_for_batch(conn, batch, job)
        tasks = list_sampling_tasks(conn, batch["batch_id"])
        categories: dict[str, int] = {}
        for task in tasks:
            if task.get("status") not in {"failed", "blocked"}:
                continue
            code = str(task.get("error_code") or classify_error(str(task.get("error_message") or "")).code.value)
            categories[code] = categories.get(code, 0) + 1
        base["failure_categories"] = categories
        base["outcome"] = batch_outcome({**batch, "total_count": base["total_count"], "completed_count": base["completed_count"], "failed_count": base["failed_count"], "success_count": base["success_count"]})
        return base
    rows = latest_logical_runs(list_runs_by_batch(conn, batch["batch_id"]))
    if rows and not job:
        latest_progress = latest_run_progress(rows, planned_batch_total(conn, batch))
        base.update(latest_progress)
        batch_status = str(batch.get("status") or "")
        if batch_status in {"queued", "running", "paused", "pause_requested"}:
            base["status"] = batch["status"]
        elif batch_status == "failed" or latest_progress["failed"]:
            base["status"] = "failed"
        elif latest_progress["completed"] < latest_progress["total"]:
            base["status"] = "queued"
    base["total_count"] = base.get("total", base.get("total_count", 0))
    base["completed_count"] = base.get("completed", base.get("completed_count", 0))
    base["success_count"] = base.get("success", base.get("success_count", 0))
    base["failed_count"] = base.get("failed", base.get("failed_count", 0))
    base["source_statuses"] = source_statuses_for_batch(conn, batch, job)
    base["failure_categories"] = failure_category_counts(rows)
    base["outcome"] = batch_outcome({**batch, "status": base.get("status"), "total_count": base["total_count"], "completed_count": base["completed_count"], "failed_count": base["failed_count"], "success_count": base["success_count"]})
    return base


def run_batch_in_background(batch_id: str, project_id: int, payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())
    try:
        def on_progress(progress: dict):
            set_sampling_job(
                batch_id,
                status=progress.get("status", "running"),
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
        final_status = result.get("status", "completed")
        set_sampling_job(
            batch_id,
            status=final_status,
            total=result["total"],
            completed=result["success"] + result["failed"],
            failed=result["failed"],
            success=result["success"],
            finished_at=utc_now() if final_status != "paused" else None,
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


def rerun_selected_in_background(batch_id: str, run_ids: list[int], payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())
    try:
        result = perform_rerun_runs(batch_id, run_ids, payload, db_target=DEFAULT_DB_PATH)
        set_sampling_job(
            batch_id,
            status=result.get("status", "completed"),
            total=result.get("total", len(run_ids)),
            completed=result.get("success", 0) + result.get("failed", 0),
            failed=result.get("failed", 0),
            success=result.get("success", 0),
            finished_at=utc_now(),
            updated_at=utc_now(),
        )
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=DEFAULT_DB_PATH)
        set_sampling_job(batch_id, status="failed", error=str(exc), finished_at=utc_now(), updated_at=utc_now())


def resume_batch_in_background(batch_id: str, payload: dict):
    set_sampling_job(batch_id, status="running", started_at=utc_now())
    try:
        def on_progress(progress: dict):
            set_sampling_job(
                batch_id,
                status=progress.get("status", "running"),
                total=progress["total"],
                completed=progress["completed"],
                failed=progress["failed"],
                success=progress["success"],
                current_provider=progress.get("provider"),
                current_model=progress.get("model"),
                current_question_id=progress.get("question_id"),
                current_repeat_index=progress.get("repeat_index"),
                updated_at=utc_now(),
            )

        result = perform_resume_batch(batch_id, payload, progress_hook=on_progress, db_target=DEFAULT_DB_PATH)
        final_status = result.get("status", "completed")
        job = get_sampling_job(batch_id) or {}
        set_sampling_job(
            batch_id,
            status=final_status,
            total=job.get("total", result.get("total", 0)),
            completed=job.get("completed", result.get("success", 0) + result.get("failed", 0)),
            failed=job.get("failed", result.get("failed", 0)),
            success=job.get("success", result.get("success", 0)),
            finished_at=utc_now() if final_status != "paused" else None,
            updated_at=utc_now(),
        )
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=DEFAULT_DB_PATH)
        set_sampling_job(batch_id, status="failed", error=str(exc), finished_at=utc_now(), updated_at=utc_now())


def dispatch_batch(batch_id: str, project_id: int, payload: dict) -> str | None:
    if task_queue_backend() == "rq":
        job_ids: list[str] = []
        with get_conn(DEFAULT_DB_PATH) as conn:
            prepare_batch_ledger(conn, batch_id, project_id, payload)
            tasks = list_sampling_tasks(conn, batch_id)
            for task in tasks:
                if task.get("status") != "queued" or task.get("rq_job_id"):
                    continue
                job_id = enqueue_rq_task(perform_sampling_task, task["task_id"])
                update_sampling_task(conn, task["task_id"], {"rq_job_id": job_id, "updated_at": utc_now()})
                job_ids.append(job_id)
        return job_ids[0] if job_ids else None
    threading.Thread(
        target=run_batch_in_background,
        args=(batch_id, project_id, payload),
        daemon=True,
    ).start()
    return None


def dispatch_sampling_task(task_id: str) -> str | None:
    with get_conn(DEFAULT_DB_PATH) as conn:
        task = get_sampling_task(conn, task_id)
        if not task or task.get("status") != "queued" or task.get("rq_job_id"):
            return None
        batch = get_sampling_batch(conn, task["batch_id"])
        if not batch or batch.get("status") not in {"queued", "running"}:
            return None
        if task_queue_backend() == "rq":
            job_id = enqueue_rq_task(perform_sampling_task, task_id)
            update_sampling_task(conn, task_id, {"rq_job_id": job_id, "updated_at": utc_now()})
            return job_id
    threading.Thread(target=perform_sampling_task, args=(task_id, DEFAULT_DB_PATH), daemon=True).start()
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


def dispatch_rerun_selected(batch_id: str, run_ids: list[int], payload: dict) -> str | None:
    if task_queue_backend() == "rq":
        return enqueue_rq_task(perform_rerun_runs, batch_id, run_ids, payload)
    threading.Thread(
        target=rerun_selected_in_background,
        args=(batch_id, run_ids, payload),
        daemon=True,
    ).start()
    return None


def dispatch_resume_batch(batch_id: str, payload: dict) -> str | None:
    if task_queue_backend() == "rq":
        with get_conn(DEFAULT_DB_PATH) as conn:
            batch = get_sampling_batch(conn, batch_id)
            if not batch:
                raise ValueError("批次不存在")
            reset_resumable_sampling_tasks(conn, batch_id)
            update_sampling_batch(conn, batch_id, {"status": "queued", "finished_at": None, "updated_at": utc_now()})
        return dispatch_batch(batch_id, int(batch["project_id"]), {**(batch.get("config") or {}), **payload})
    threading.Thread(
        target=resume_batch_in_background,
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
        if isinstance(data, dict) and data.get("error"):
            message = str(data.get("message") or data.get("error"))
            data = {
                **data,
                "error": message,
                "message": message,
                "code": str(data.get("code") or (f"HTTP_{status}" if status >= 400 else "API_ERROR")),
                "retryable": bool(data.get("retryable", status == 429 or status >= 500)),
            }
        body = json.dumps(data, ensure_ascii=False, default=api_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def delivery_error_response(self, exc: DeliveryError):
        if exc.code.endswith("_NOT_FOUND"):
            status = HTTPStatus.NOT_FOUND
        elif exc.code in {"INVALID_REPORT_TRANSITION", "BATCH_PROJECT_MISMATCH", "REPORT_NOT_FROZEN"}:
            status = HTTPStatus.CONFLICT
        else:
            status = HTTPStatus.BAD_REQUEST
        self.json_response({"error": str(exc), "code": exc.code}, status)

    def operation_actor(self, payload: dict | None = None) -> str:
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer ") and agent_api_token() and hmac.compare_digest(authorization[7:].strip(), agent_api_token()):
            return "agent-api"
        if auth_enabled() and self.has_valid_auth_session():
            return "authenticated-user"
        return "local-user"

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
                if parsed.path in {"/api/health", "/api/health/live"}:
                    self.json_response(
                        {
                            "ok": True,
                            "db": "postgres" if database_url() else str(DEFAULT_DB_PATH),
                            "task_queue_backend": task_queue_backend(),
                        }
                    )
                elif parsed.path == "/api/health/ready":
                    result, status = readiness_status(conn)
                    self.json_response(result, status)
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
                elif parsed.path.startswith("/api/projects/") and parsed.path.endswith("/impact"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    impact = get_project_impact(conn, int(parts[2]))
                    if not impact:
                        self.json_response({"error": "项目不存在"}, 404)
                        return
                    self.json_response({"impact": impact})
                elif parsed.path == "/api/sources/health":
                    self.json_response({"sources": source_health_rows(conn), "checked_at": utc_now()})
                elif parsed.path == "/api/tasks/active":
                    batches = [
                        {**sampling_batch_to_progress(item), "outcome": batch_outcome(item)}
                        for item in list_sampling_batches(conn)
                        if item.get("status") in {"queued", "running", "pause_requested", "paused", "failed", "failed_system"}
                    ]
                    self.json_response({"batches": batches, "stale": False, "checked_at": utc_now()})
                elif parsed.path == "/api/models":
                    self.json_response(
                        {
                            "models": [enrich_model_config(item) for item in list_model_configs(conn)],
                            "presets": UI_PROVIDER_PRESETS,
                        }
                    )
                elif parsed.path == "/api/settings/bocha-search":
                    self.json_response(bocha_search_status())
                elif parsed.path == "/api/settings/deepseek-web":
                    self.json_response(deepseek_web_status())
                elif parsed.path == "/api/questions":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw and project_raw != "all" else None
                    self.json_response({"questions": list_questions(conn, project_id)})
                elif parsed.path == "/api/runs":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response({"runs": list_runs(conn, project_id)})
                elif parsed.path.startswith("/api/runs/") and parsed.path.endswith("/reviews"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    self.json_response({"review": get_run_review(conn, parts[2], include_history=True)})
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
                    self.json_response({"batch": progress_response_for_batch(conn, batch)})
                elif parsed.path == "/api/batches":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw and project_raw != "all" else None
                    batches = list_sampling_batches(conn, project_id)
                    self.json_response({"batches": [{**item, "outcome": batch_outcome(item)} for item in batches]})
                elif parsed.path.startswith("/api/batches/"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) not in {3, 4}:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    if len(parts) == 4 and parts[3] == "tasks":
                        batch = get_sampling_batch(conn, batch_id)
                        if not batch:
                            self.json_response({"error": "批次不存在"}, 404)
                            return
                        self.json_response({"tasks": list_sampling_tasks(conn, batch_id)})
                        return
                    if len(parts) == 4 and parts[3] == "runs":
                        include_history = str(query.get("history", [""])[0]).lower() in {"1", "true", "yes"}
                        rows = list_runs_by_batch(conn, batch_id, include_history=include_history)
                        self.json_response({"runs": attach_task_ids(conn, batch_id, rows)})
                        return
                    if len(parts) == 4 and parts[3] == "attempts":
                        batch = get_sampling_batch(conn, batch_id)
                        if not batch:
                            self.json_response({"error": "批次不存在"}, 404)
                            return
                        ledger = list_execution_attempts(conn, batch_id)
                        if ledger:
                            self.json_response({"attempts": ledger, "source": "execution_attempts"})
                        else:
                            rows = list_runs_by_batch(conn, batch_id, include_history=True)
                            self.json_response({"attempts": attempt_history_rows(rows), "source": "model_runs_compat"})
                        return
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    self.json_response({"batch": progress_response_for_batch(conn, batch)})
                elif parsed.path == "/api/analytics":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response(analytics(conn, project_id))
                elif parsed.path == "/api/analytics/summary":
                    project_id = int(query.get("project_id", [0])[0])
                    batch_id = str(query.get("batch_id", [""])[0]).strip() or None
                    self.json_response(build_analytics_summary(conn, project_id, batch_id))
                elif parsed.path == "/api/analytics/compare":
                    project_id = int(query.get("project_id", [0])[0])
                    baseline_batch_id = str(query.get("baseline_batch_id", [""])[0]).strip()
                    candidate_batch_id = str(query.get("batch_id", [""])[0]).strip()
                    if not project_id or not baseline_batch_id or not candidate_batch_id:
                        raise DeliveryError("COMPARE_SCOPE_REQUIRED", "缺少 project_id、baseline_batch_id 或 batch_id")
                    comparison = compare_batches(conn, baseline_batch_id, candidate_batch_id)
                    for batch_key in ("baseline_batch_id", "candidate_batch_id"):
                        batch = get_sampling_batch(conn, comparison[batch_key])
                        if not batch or int(batch["project_id"]) != project_id:
                            raise DeliveryError("BATCH_PROJECT_MISMATCH", "对比批次不属于指定项目")
                    self.json_response(comparison)
                elif parsed.path == "/api/reports":
                    project_id = int(query.get("project_id", [0])[0])
                    if not project_id:
                        raise DeliveryError("PROJECT_REQUIRED", "缺少 project_id")
                    batch_raw = query.get("batch_id", [None])[0]
                    batch_id = str(batch_raw).strip() if batch_raw is not None else None
                    self.json_response({"reports": list_report_versions(conn, project_id=project_id, batch_id=batch_id)})
                elif parsed.path == "/api/audit":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw not in {None, "", "all"} else None
                    self.json_response(
                        {
                            "events": list_audit_events(
                                conn,
                                project_id=project_id,
                                entity_type=str(query.get("entity_type", [""])[0]).strip() or None,
                                entity_id=str(query.get("entity_id", [""])[0]).strip() or None,
                                limit=int(query.get("limit", [200])[0]),
                            )
                        }
                    )
                elif parsed.path == "/api/export/delivery.json":
                    batch_id = str(query.get("batch_id", [""])[0]).strip() or None
                    report_id = str(query.get("report_id", [""])[0]).strip() or None
                    include_attempts = str(query.get("include_attempts", [""])[0]).lower() in {"1", "true", "yes"}
                    self.json_response(build_export(conn, batch_id=batch_id, report_id=report_id, include_attempts=include_attempts))
                elif parsed.path.startswith("/api/reports/") and parsed.path.endswith("/export"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    include_attempts = str(query.get("include_attempts", [""])[0]).lower() in {"1", "true", "yes"}
                    self.json_response(build_export(conn, report_id=parts[2], include_attempts=include_attempts))
                elif parsed.path == "/api/export/runs.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    include_history = str(query.get("history", [""])[0]).lower() in {"1", "true", "yes"}
                    self.csv_response(runs_to_csv(list_runs(conn, project_id, limit=10000, include_history=include_history)), "geo-runs.csv")
                elif parsed.path == "/api/export/summary.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    self.csv_response(analytics_to_csv(analytics(conn, project_id)), "geo-summary.csv")
                elif parsed.path == "/api/export/runs.xls":
                    project_id = int(query.get("project_id", [0])[0])
                    include_history = str(query.get("history", [""])[0]).lower() in {"1", "true", "yes"}
                    self.excel_html_response(runs_to_excel_html(list_runs(conn, project_id, limit=10000, include_history=include_history)), "geo-runs.xls")
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
                    include_history = str(query.get("history", [""])[0]).lower() in {"1", "true", "yes"}
                    rows = list_runs_by_batch(conn, batch_id, include_history=include_history)
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
                    include_history = str(query.get("history", [""])[0]).lower() in {"1", "true", "yes"}
                    path = self.save_export_file(filename, runs_to_excel_html(list_runs(conn, project_id, limit=10000, include_history=include_history)))
                    self.json_response({"ok": True, "path": path, "filename": filename})
                elif parsed.path == "/api/export/summary/save":
                    project_id = int(query.get("project_id", [0])[0])
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    filename = f"geo-summary-{project_id}-{stamp}.xls"
                    path = self.save_export_file(filename, analytics_to_excel_html(analytics(conn, project_id)))
                    self.json_response({"ok": True, "path": path, "filename": filename})
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except DeliveryError as exc:
            self.delivery_error_response(exc)
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
                elif parsed.path == "/api/reports":
                    report = create_report_version(
                        conn,
                        project_id=int(payload.get("project_id", 0)),
                        batch_id=str(payload.get("batch_id") or "").strip() or None,
                        title=str(payload.get("title") or "").strip(),
                        actor=self.operation_actor(payload),
                    )
                    commit_json({"report": report}, HTTPStatus.CREATED)
                elif parsed.path.startswith("/api/reports/") and parsed.path.endswith("/status"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    status = str(payload.get("status") or "").strip().lower()
                    if status == "reviewed":
                        report = review_report(conn, parts[2], actor=self.operation_actor(payload))
                    elif status == "archived":
                        report = archive_report(conn, parts[2], actor=self.operation_actor(payload))
                    else:
                        raise DeliveryError("INVALID_REPORT_TRANSITION", "status 仅支持 reviewed 或 archived")
                    commit_json({"report": report})
                elif parsed.path.startswith("/api/reports/") and parsed.path.endswith("/freeze"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    report = freeze_report(
                        conn,
                        parts[2],
                        actor=self.operation_actor(payload),
                    )
                    commit_json({"report": report})
                elif parsed.path.startswith("/api/runs/") and parsed.path.endswith("/review"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    review = review_run(
                        conn,
                        parts[2],
                        str(payload.get("decision") or "").strip(),
                        actor=self.operation_actor(payload),
                        reason=str(payload.get("reason") or "").strip(),
                        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                    )
                    commit_json({"review": review}, HTTPStatus.CREATED)
                elif parsed.path == "/api/projects/update":
                    update_project(conn, payload)
                    commit_json({"ok": True})
                elif parsed.path == "/api/projects/archive":
                    project_id = int(payload.get("id", 0))
                    project = get_project(conn, project_id)
                    if not project:
                        self.json_response({"error": "项目不存在"}, 404)
                        return
                    archived = bool(payload.get("archived", True))
                    archive_project(conn, project_id, archived)
                    write_audit(
                        conn, entity_type="project", entity_id=str(project_id),
                        action="project_archived" if archived else "project_restored",
                        actor=self.operation_actor(payload), project_id=project_id,
                        details={"project_name": project.get("brand_name") or project.get("client_name")},
                    )
                    commit_json({"ok": True, "id": project_id, "archived": archived})
                elif parsed.path == "/api/projects/delete":
                    project_id = int(payload.get("id", 0))
                    project = get_project(conn, project_id)
                    if not project:
                        self.json_response({"error": "项目不存在"}, 404)
                        return
                    expected_names = {str(project.get("brand_name") or "").strip(), str(project.get("client_name") or "").strip()}
                    confirm_name = str(payload.get("confirm_name") or "").strip()
                    impact = get_project_impact(conn, project_id)
                    if not confirm_name or confirm_name not in expected_names:
                        self.json_response(
                            {
                                "error": "需要输入项目品牌名或客户名确认永久删除",
                                "code": "PROJECT_DELETE_CONFIRMATION_REQUIRED",
                                "impact": impact,
                            },
                            409,
                        )
                        return
                    write_audit(
                        conn, entity_type="project", entity_id=str(project_id), action="project_deleted",
                        actor=self.operation_actor(payload), project_id=project_id,
                        details={"project_name": confirm_name, "impact": impact},
                    )
                    delete_project(conn, project_id)
                    commit_json({"ok": True, "impact": impact})
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
                elif parsed.path == "/api/settings/bocha-search":
                    api_key = str(payload.get("api_key", "")).strip()
                    if not api_key:
                        raise ValueError("缺少博查 API Key")
                    values = {name: api_key for name in BOCHA_SEARCH_ENV_KEYS}
                    update_env_file_values(ROOT / ".env", values)
                    for name, value in values.items():
                        os.environ[name] = value
                    commit_json({"ok": True, **bocha_search_status()})
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
                elif parsed.path == "/api/questions/preview_file":
                    rows = self.decode_table_rows(payload.get("file_base64", ""), payload.get("file_name", ""))
                    self.json_response(preview_question_import(conn, int(payload.get("project_id", 0)), rows))
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
                elif parsed.path.startswith("/api/sources/") and parsed.path.endswith("/probe"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    source = parts[2]
                    matches = [item for item in source_health_rows(conn) if item["source"] == source]
                    if not matches:
                        self.json_response({"error": "信息源不存在"}, 404)
                        return
                    probe_type = str(payload.get("probe_type") or "static").lower()
                    if probe_type == "static":
                        self.json_response({"source": source, "probe_type": "static", "checked_at": utc_now(), "configs": matches})
                        return
                    configs = [item for item in list_model_configs(conn) if item.get("provider") == source and item.get("active")]
                    model_config_id = int(payload.get("model_config_id") or 0)
                    if model_config_id:
                        configs = [item for item in configs if int(item.get("id") or 0) == model_config_id]
                    if not configs:
                        self.json_response({"error": "没有启用的信息源配置"}, 409)
                        return
                    config = configs[0]
                    kind = str(payload.get("kind") or payload.get("mode") or (probe_type if probe_type in {"pure", "search", "citation"} else "pure")).lower()
                    result = run_active_probe(conn, config, kind)
                    result_kind = result.pop("probe_type", kind)
                    commit_json({"source": source, **result, "probe_type": "active", "kind": result_kind}, 200 if result.get("ok") else 502)
                elif parsed.path == "/api/runs/preflight":
                    self.json_response(start_preflight(conn, payload))
                elif parsed.path == "/api/runs/start":
                    project_id = int(payload.get("project_id", 0))
                    if not isinstance(payload.get("tags", []), list):
                        self.json_response({"error": "tags 必须是数组", "code": "INVALID_TAGS"}, 400)
                        return
                    project = get_project(conn, project_id)
                    if not project:
                        self.json_response({"error": "项目不存在"}, 404)
                        return
                    if project.get("archived_at"):
                        self.json_response({"error": "归档项目不能启动新批次", "code": "PROJECT_ARCHIVED"}, 409)
                        return
                    try:
                        mode = web_batch_mode(conn, payload)
                    except ValueError as exc:
                        self.json_response({"error": str(exc)}, 400)
                        return
                    run_payload = {**payload, "provider_mode": "deepseek_web"} if mode == "web" else payload
                    total = estimate_batch_total(conn, project_id, run_payload)
                    client_request_id = str(payload.get("client_request_id") or "").strip()
                    idempotent_batch = get_sampling_batch_by_client_request(conn, project_id, client_request_id)
                    if idempotent_batch:
                        original_fingerprint = str((idempotent_batch.get("config") or {}).get("_request_fingerprint") or "")
                        request_fingerprint = batch_request_fingerprint(project_id, run_payload)
                        if original_fingerprint and original_fingerprint != request_fingerprint:
                            self.json_response({"error": "client_request_id 已用于不同的启动请求", "code": "IDEMPOTENCY_KEY_CONFLICT", "batch_id": idempotent_batch["batch_id"]}, 409)
                            return
                        result = progress_response_for_batch(conn, idempotent_batch, get_sampling_job(idempotent_batch["batch_id"]))
                        result.update({"task_queue_backend": task_queue_backend(), "existing": True, "idempotent_replay": True})
                        self.json_response(result)
                        return
                    existing_batch = active_project_batch(conn, project_id)
                    if existing_batch:
                        existing_job = get_sampling_job(existing_batch["batch_id"])
                        result = progress_response_for_batch(conn, existing_batch, existing_job)
                        result["task_queue_backend"] = task_queue_backend()
                        result.update({"existing": True, "code": "ACTIVE_BATCH_EXISTS", "error": "当前项目已有活动批次"})
                        self.json_response(result, 409)
                        return
                    batch_id = payload.get("batch_id") or f"batch-{uuid.uuid4().hex[:10]}"
                    outbox_event_id = f"dispatch-{uuid.uuid4().hex}"
                    request_fingerprint = batch_request_fingerprint(project_id, run_payload)
                    stored_run_payload = {**run_payload, "_request_fingerprint": request_fingerprint}
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
                            "config": stored_run_payload,
                            "batch_name": str(payload.get("batch_name") or "").strip() or f"批次 {batch_id}",
                            "description": payload.get("description", ""),
                            "purpose": payload.get("purpose", ""),
                            "tags": payload.get("tags", []),
                            "config_snapshot": immutable_batch_config_snapshot(conn, project_id, stored_run_payload),
                            "client_request_id": client_request_id,
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
                    if mode == "web":
                        create_web_sampling_tasks(conn, batch_id, project_id, run_payload)
                    else:
                        prepare_batch_ledger(conn, batch_id, project_id, run_payload)
                    create_outbox_event(
                        conn,
                        outbox_event_id,
                        "dispatch_batch",
                        batch_id,
                        {"batch_id": batch_id, "project_id": project_id, "mode": mode},
                    )
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_created",
                        actor=self.operation_actor(payload), project_id=project_id, batch_id=batch_id,
                        details={"batch_name": str(payload.get("batch_name") or "").strip(), "total_tasks": total, "mode": mode},
                    )
                    conn.commit()
                    try:
                        if mode == "web":
                            job_id = enqueue_next_web_task(batch_id, DEFAULT_DB_PATH)
                        else:
                            job_id = dispatch_batch(batch_id, project_id, run_payload)
                        mark_outbox_delivered(conn, outbox_event_id)
                        conn.commit()
                    except Exception as exc:
                        mark_outbox_failed(conn, outbox_event_id, str(exc))
                        conn.commit()
                        raise
                    result = {
                        "batch_id": batch_id,
                        "total": total,
                        "failed": 0,
                        "success": 0,
                        "status": "queued",
                        "task_queue_backend": task_queue_backend(),
                        "job_id": job_id,
                        "batch_name": str(payload.get("batch_name") or "").strip(),
                        "outcome": "pending",
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
                    try:
                        mode = web_batch_mode(conn, run_payload)
                    except ValueError as exc:
                        self.json_response({"error": str(exc)}, 400)
                        return
                    if mode == "web":
                        run_payload["provider_mode"] = "deepseek_web"
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
                            "config_snapshot": immutable_batch_config_snapshot(conn, project_id, run_payload),
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
                    if mode == "web":
                        create_web_sampling_tasks(conn, batch_id, project_id, run_payload)
                    conn.commit()
                    if mode == "web":
                        job_id = enqueue_next_web_task(batch_id, DEFAULT_DB_PATH)
                    else:
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
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/metadata"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    update_sampling_batch_metadata(conn, batch_id, payload)
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_metadata_updated",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        details={key: payload.get(key) for key in ("batch_name", "description", "purpose", "tags") if key in payload},
                    )
                    commit_json({"batch": get_sampling_batch(conn, batch_id)})
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/archive"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    archived = bool(payload.get("archived", True))
                    if archived and batch.get("status") in {"queued", "running", "pause_requested"}:
                        self.json_response({"error": "活动批次不能归档", "code": "ACTIVE_BATCH_ARCHIVE_FORBIDDEN"}, 409)
                        return
                    update_sampling_batch(conn, batch_id, {"archived_at": utc_now() if archived else None, "updated_at": utc_now()})
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id,
                        action="batch_archived" if archived else "batch_restored",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                    )
                    commit_json({"batch": get_sampling_batch(conn, batch_id)})
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/retry"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    if batch.get("status") in {"queued", "running", "pause_requested"}:
                        self.json_response(
                            {
                                "error": "该批次已有操作正在执行，请等待当前操作完成后再重试",
                                "code": "RETRY_IN_PROGRESS",
                                "batch": batch,
                            },
                            409,
                        )
                        return
                    scope = str(payload.get("scope") or "all").strip().lower()
                    if scope not in {"all", "source", "tasks"}:
                        self.json_response({"error": "scope 必须是 all、source 或 tasks"}, 400)
                        return
                    if batch_is_web(batch):
                        if scope == "tasks":
                            self.json_response({"error": "网页采样暂不支持按任务选择重试"}, 400)
                            return
                        result = resume_web_batch(batch_id, DEFAULT_DB_PATH)
                        write_audit(
                            conn, entity_type="batch", entity_id=batch_id, action="batch_retried",
                            actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                            details={"scope": scope, "task_count": int(result.get("total", 0) or 0)},
                        )
                        commit_json({**progress_response_for_batch(conn, get_sampling_batch(conn, batch_id) or batch), **result, "retry_scope": scope})
                        return
                    failed_runs = list_failed_runs_by_batch(conn, batch_id)
                    selected = failed_runs
                    if scope == "source":
                        source = str(payload.get("source") or "").strip()
                        if not source:
                            self.json_response({"error": "按信息源重试时缺少 source"}, 400)
                            return
                        selected = [row for row in failed_runs if row.get("provider") == source or row.get("test_platform") == source]
                    elif scope == "tasks":
                        requested = {str(item) for item in (payload.get("task_ids") or payload.get("run_ids") or []) if str(item)}
                        task_by_run = {
                            str(item.get("run_id") or ""): str(item.get("task_id") or "")
                            for item in list_execution_attempts(conn, batch_id)
                        }
                        selected = [
                            row for row in failed_runs
                            if task_by_run.get(str(row.get("run_id") or ""), "") in requested
                            or str(row.get("id", "")) in requested
                            or str(row.get("run_id", "")) in requested
                        ]
                    if not selected:
                        self.json_response({"error": "当前范围没有可重跑的失败任务"}, 400)
                        return
                    run_ids = [int(row["id"]) for row in selected]
                    retry_claimed = update_sampling_batch_cas(
                        conn,
                        batch_id,
                        {str(batch.get("status") or "")},
                        {
                            "status": "running",
                            "error_message": "",
                            "finished_at": None,
                            "updated_at": utc_now(),
                        },
                    )
                    if not retry_claimed:
                        self.json_response(
                            {
                                "error": "该批次已有操作正在执行，请等待当前操作完成后再重试",
                                "code": "RETRY_IN_PROGRESS",
                                "batch": get_sampling_batch(conn, batch_id),
                            },
                            409,
                        )
                        return
                    set_sampling_job(batch_id, project_id=batch["project_id"], total=len(run_ids), completed=0, failed=0, success=0, status="queued", updated_at=utc_now())
                    conn.commit()
                    try:
                        job_id = dispatch_rerun_failed(batch_id, payload) if scope == "all" else dispatch_rerun_selected(batch_id, run_ids, payload)
                    except Exception as exc:
                        mark_batch_failed(batch_id, f"重试任务派发失败：{exc}", db_target=DEFAULT_DB_PATH)
                        raise
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_retried",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        details={"scope": scope, "task_count": len(run_ids), "source": payload.get("source") if scope == "source" else None},
                    )
                    commit_json({"batch_id": batch_id, "total": len(run_ids), "status": "queued", "retry_scope": scope, "task_queue_backend": task_queue_backend(), "job_id": job_id})
                elif parsed.path.startswith("/api/agent/batches/") and parsed.path.endswith("/rerun_failed"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 5:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[3]
                    batch = get_sampling_batch(conn, batch_id)
                    if batch_is_web(batch):
                        result = resume_web_batch(batch_id, DEFAULT_DB_PATH)
                        self.json_response(result)
                        return
                    failed_runs = list_failed_runs_by_batch(conn, batch_id)
                    job_id = dispatch_rerun_failed(batch_id, payload)
                    if batch:
                        write_audit(
                            conn, entity_type="batch", entity_id=batch_id, action="batch_retried",
                            actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                            details={"scope": "all", "task_count": len(failed_runs), "compatibility_route": "agent_rerun_failed"},
                        )
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
                    if batch_is_web(batch):
                        result = resume_web_batch(batch_id, DEFAULT_DB_PATH)
                        updated = get_sampling_batch(conn, batch_id) or batch
                        self.json_response({**progress_response_for_batch(conn, updated), **result})
                        return
                    failed_runs = list_failed_runs_by_batch(conn, batch_id)
                    if not failed_runs:
                        self.json_response({"error": "当前批次没有可重跑的失败任务"}, 400)
                        return
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
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_retried",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        details={"scope": "all", "task_count": len(failed_runs), "compatibility_route": "rerun_failed"},
                    )
                    self.json_response(
                        {
                            "batch_id": batch_id,
                            "total": len(failed_runs),
                            "status": "queued",
                            "task_queue_backend": task_queue_backend(),
                            "job_id": job_id,
                        }
                    )
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/pause"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    if batch.get("status") == "queued":
                        update_sampling_batch(conn, batch_id, {"status": "paused", "updated_at": utc_now()})
                        conn.commit()
                        set_sampling_job(batch_id, status="paused", updated_at=utc_now())
                    elif batch.get("status") == "running":
                        request_batch_pause(batch_id, DEFAULT_DB_PATH)
                        set_sampling_job(batch_id, status="pause_requested", updated_at=utc_now())
                    updated = get_sampling_batch(conn, batch_id) or batch
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_pause_requested",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        details={"previous_status": batch.get("status"), "status": updated.get("status")},
                    )
                    commit_json(progress_response_for_batch(conn, updated, get_sampling_job(batch_id)))
                elif parsed.path.startswith("/api/batches/") and parsed.path.endswith("/resume"):
                    parts = [unquote(item) for item in parsed.path.split("/") if item]
                    if len(parts) != 4:
                        self.json_response({"error": "接口不存在"}, 404)
                        return
                    batch_id = parts[2]
                    batch = get_sampling_batch(conn, batch_id)
                    if not batch:
                        self.json_response({"error": "批次不存在"}, 404)
                        return
                    if batch.get("status") not in {"paused", "pause_requested", "failed", "failed_system", "queued"}:
                        self.json_response({"error": "当前批次状态不能继续执行"}, 409)
                        return
                    if batch_is_web(batch):
                        result = resume_web_batch(batch_id, DEFAULT_DB_PATH)
                        updated = get_sampling_batch(conn, batch_id) or batch
                        write_audit(
                            conn, entity_type="batch", entity_id=batch_id, action="batch_resumed",
                            actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                            details={"previous_status": batch.get("status")},
                        )
                        commit_json({**progress_response_for_batch(conn, updated), **result})
                        return
                    if batch.get("status") == "pause_requested":
                        update_sampling_batch(conn, batch_id, {"status": "running", "updated_at": utc_now()})
                        conn.commit()
                        set_sampling_job(batch_id, status="running", updated_at=utc_now())
                        updated = get_sampling_batch(conn, batch_id) or batch
                        write_audit(
                            conn, entity_type="batch", entity_id=batch_id, action="batch_resume_cancelled_pause",
                            actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        )
                        commit_json(progress_response_for_batch(conn, updated, get_sampling_job(batch_id)))
                        return
                    set_sampling_job(
                        batch_id,
                        project_id=batch["project_id"],
                        total=batch.get("total_count", 0),
                        completed=batch.get("completed_count", 0),
                        failed=batch.get("failed_count", 0),
                        success=batch.get("success_count", 0),
                        status="queued",
                        updated_at=utc_now(),
                    )
                    update_sampling_batch(conn, batch_id, {"status": "queued", "updated_at": utc_now()})
                    conn.commit()
                    job_id = dispatch_resume_batch(batch_id, payload)
                    write_audit(
                        conn, entity_type="batch", entity_id=batch_id, action="batch_resumed",
                        actor=self.operation_actor(payload), project_id=int(batch["project_id"]), batch_id=batch_id,
                        details={"previous_status": batch.get("status")},
                    )
                    commit_json(
                        {
                            "batch_id": batch_id,
                            "total": batch.get("total_count", 0),
                            "status": "queued",
                            "task_queue_backend": task_queue_backend(),
                            "job_id": job_id,
                        }
                    )
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except DeliveryError as exc:
            self.delivery_error_response(exc)
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


def preview_question_import(conn, project_id: int, rows: list[dict[str, str]]) -> dict:
    """Read-only import validation shared by XLSX/CSV clients."""
    existing = {
        re.sub(r"\s+", " ", str(item.get("question") or "").strip()).lower()
        for item in list_questions(conn, project_id)
    }
    seen: set[str] = set()
    valid_rows: list[dict[str, str]] = []
    issues: list[dict[str, object]] = []
    duplicate = 0
    empty = 0
    invalid = 0
    for index, row in enumerate(rows, start=2):
        question_key = next((key for key in row if str(key).replace("\ufeff", "").strip() == "问题内容"), None)
        if question_key is None:
            invalid += 1
            issues.append({"row": index, "reason": "缺少“问题内容”列"})
            continue
        question = str(row.get(question_key) or "").strip()
        if not question:
            empty += 1
            issues.append({"row": index, "reason": "问题内容为空"})
            continue
        normalized = re.sub(r"\s+", " ", question).lower()
        if normalized in existing or normalized in seen:
            duplicate += 1
            issues.append({"row": index, "reason": "与当前项目或本次导入重复", "question": question[:120]})
            continue
        seen.add(normalized)
        valid_rows.append({str(key): str(value or "").strip() for key, value in row.items()})
    return {
        "valid_rows": valid_rows,
        "valid": len(valid_rows),
        "duplicate": duplicate,
        "empty": empty,
        "invalid": invalid,
        "skipped": duplicate + empty + invalid,
        "issues": issues[:200],
    }


def main():
    load_dotenv_file(ROOT / ".env")
    init_db(DEFAULT_DB_PATH)
    with get_conn(DEFAULT_DB_PATH) as conn:
        dialect = "postgres" if database_url() else "sqlite"
        MigrationRunner(conn, dialect).apply()
        ensure_delivery_schema(conn)
    host = os.environ.get("GEO_AUDIT_HOST", "127.0.0.1")
    port = int(os.environ.get("GEO_AUDIT_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    probe_scheduler = start_optional_probe_scheduler(
        lambda: get_conn(DEFAULT_DB_PATH),
        lambda: _load_active_model_configs(),
    )
    if os.environ.get("RECONCILER_ENABLED", "1") == "1":
        def reconcile_loop():
            interval = max(5, int(os.environ.get("RECONCILER_INTERVAL_SECONDS", "15")))
            def dispatch_event(event, batch):
                if event.get("event_type") == "dispatch_sampling_task":
                    dispatch_sampling_task(str((event.get("payload") or {}).get("task_id") or event.get("aggregate_id") or ""))
                    return
                payload = batch.get("config") or {}
                if str((event.get("payload") or {}).get("mode") or "") == "web":
                    enqueue_next_web_task(batch["batch_id"], DEFAULT_DB_PATH)
                else:
                    dispatch_batch(batch["batch_id"], int(batch["project_id"]), payload)
            while True:
                try:
                    reconcile_once(DEFAULT_DB_PATH, dispatch_event)
                except Exception as exc:
                    print(f"reconciler error: {exc}")
                time.sleep(interval)
        threading.Thread(target=reconcile_loop, name="geo-audit-reconciler", daemon=True).start()
    print(f"制造业品牌 GEO 工作台已启动：http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        if probe_scheduler:
            probe_scheduler.stop()


def _load_active_model_configs() -> list[dict]:
    with get_conn(DEFAULT_DB_PATH) as conn:
        return [item for item in list_model_configs(conn) if item.get("active")]


if __name__ == "__main__":
    main()
