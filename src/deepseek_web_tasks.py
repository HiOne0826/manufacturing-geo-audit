from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .db import (
    DEFAULT_DB_PATH,
    claim_sampling_task,
    create_sampling_tasks,
    get_conn,
    get_model_config,
    get_project,
    get_sampling_batch,
    get_sampling_task,
    insert_evaluation,
    insert_run,
    list_questions,
    next_queued_sampling_task,
    recent_sampling_task_error_codes,
    reset_resumable_sampling_tasks,
    reset_running_sampling_tasks,
    sampling_task_counts,
    update_sampling_batch,
    update_sampling_task,
    utc_now,
)
from .deepseek_web import DeepSeekWebError, get_deepseek_web_browser
from .evaluator import evaluate_answer


WEB_PROVIDER = "deepseek_web"
BLOCKING_ERROR_CODES = {"auth_missing", "auth_expired", "captcha"}
STRUCTURAL_ERROR_CODES = {
    "selector_composer",
    "selector_new_chat",
    "selector_search_toggle",
    "new_chat_failed",
    "search_state_unknown",
    "chat_id_missing",
    "session_not_isolated",
    "capture_mismatch",
}


def web_job_timeout() -> int:
    try:
        value = int(os.environ.get("DEEPSEEK_WEB_JOB_TIMEOUT", "900") or 900)
    except ValueError:
        value = 900
    return max(300, min(value, 3600))


def web_batch_mode(conn, payload: dict[str, Any]) -> str:
    providers = []
    for item in payload.get("models") or []:
        model = get_model_config(conn, int(item.get("model_config_id", 0) or 0))
        if not model:
            raise ValueError("模型配置不存在")
        providers.append(str(model.get("provider") or ""))
    has_web = WEB_PROVIDER in providers
    if has_web and any(provider != WEB_PROVIDER for provider in providers):
        raise ValueError("DeepSeek 官网采样必须使用独立批次，不能与 API provider 混跑")
    if has_web:
        if os.environ.get("DEEPSEEK_WEB_ENABLED", "0") != "1":
            raise ValueError("DeepSeek 官网采样未启用，请设置 DEEPSEEK_WEB_ENABLED=1")
        if os.environ.get("TASK_QUEUE_BACKEND", "inline").strip().lower() != "rq":
            raise ValueError("DeepSeek 官网采样要求 TASK_QUEUE_BACKEND=rq")
        for item in payload.get("models") or []:
            if not bool(item.get("search_enabled")):
                raise ValueError("DeepSeek 官网 provider 仅支持联网搜索，请启用 search_enabled")
        return "web"
    return "api"


def build_web_sampling_tasks(conn, batch_id: str, project_id: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if web_batch_mode(conn, payload) != "web":
        raise ValueError("当前批次不属于 DeepSeek 官网采样")
    questions = list_questions(conn, project_id)
    repeat_count = max(1, min(int(payload.get("repeat_count", 1) or 1), 10))
    now = utc_now()
    tasks = []
    for question in questions:
        for model_item in payload.get("models") or []:
            model_config_id = int(model_item["model_config_id"])
            for repeat_index in range(1, repeat_count + 1):
                task_key = f"{batch_id}:{int(question['id'])}:{model_config_id}:{repeat_index}"
                digest = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:20]
                tasks.append(
                    {
                        "task_id": f"dsw-{digest}",
                        "task_key": task_key,
                        "batch_id": batch_id,
                        "project_id": project_id,
                        "question_id": int(question["id"]),
                        "model_config_id": model_config_id,
                        "repeat_index": repeat_index,
                        "status": "queued",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
    return tasks


def create_web_sampling_tasks(conn, batch_id: str, project_id: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = build_web_sampling_tasks(conn, batch_id, project_id, payload)
    create_sampling_tasks(conn, tasks)
    return tasks


def _rq_queue():
    try:
        from redis import Redis
        from rq import Queue
    except ImportError as exc:
        raise RuntimeError("缺少网页 worker 的 Redis/RQ 依赖") from exc
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_name = os.environ.get("RQ_WEB_QUEUE_NAME", "geo-audit-web")
    return Queue(queue_name, connection=Redis.from_url(redis_url))


def enqueue_next_web_task(batch_id: str, db_target: Path | str | None = DEFAULT_DB_PATH) -> str:
    with get_conn(db_target) as conn:
        batch = get_sampling_batch(conn, batch_id)
        if not batch or batch.get("status") in {"pause_requested", "paused", "completed", "failed"}:
            return ""
        task = next_queued_sampling_task(conn, batch_id)
        if not task:
            _sync_web_batch(conn, batch_id)
            return ""
        job_id = f"dsw-{task['task_id']}-{uuid.uuid4().hex[:8]}"
        update_sampling_task(conn, task["task_id"], {"rq_job_id": job_id, "updated_at": utc_now()})
        if batch.get("status") == "queued":
            update_sampling_batch(conn, batch_id, {"status": "running", "started_at": utc_now(), "updated_at": utc_now()})
        conn.commit()
    timeout = web_job_timeout()
    try:
        _rq_queue().enqueue(
            perform_deepseek_web_task,
            task["task_id"],
            str(db_target) if db_target is not None else None,
            job_id=job_id,
            job_timeout=timeout,
            result_ttl=86400,
            failure_ttl=86400,
        )
    except Exception as exc:
        with get_conn(db_target) as conn:
            update_sampling_task(conn, task["task_id"], {"rq_job_id": "", "updated_at": utc_now()})
            update_sampling_batch(
                conn,
                batch_id,
                {"status": "paused", "error_message": f"网页任务入队失败：{exc}", "updated_at": utc_now()},
            )
        raise
    return job_id


def _sync_web_batch(conn, batch_id: str) -> dict[str, Any]:
    batch = get_sampling_batch(conn, batch_id)
    if not batch:
        return {}
    counts = sampling_task_counts(conn, batch_id)
    current_status = str(batch.get("status") or "queued")
    if current_status == "pause_requested" and counts["running"] == 0:
        status = "paused"
    elif counts["blocked"]:
        status = "paused"
    elif counts["total"] and counts["completed"] >= counts["total"]:
        status = "completed"
    elif current_status in {"pause_requested", "paused"}:
        status = current_status
    elif counts["running"] or counts["success"] or counts["failed"]:
        status = "running"
    else:
        status = "queued"
    update_sampling_batch(
        conn,
        batch_id,
        {
            "status": status,
            "total_count": counts["total"],
            "completed_count": counts["completed"],
            "success_count": counts["success"],
            "failed_count": counts["failed"] + counts["blocked"],
            "finished_at": utc_now() if status == "completed" else None,
            "updated_at": utc_now(),
        },
    )
    return {**counts, "status": status}


def sync_web_batch(batch_id: str, db_target: Path | str | None = DEFAULT_DB_PATH) -> dict[str, Any]:
    with get_conn(db_target) as conn:
        return _sync_web_batch(conn, batch_id)


def _persist_run(conn, task: dict[str, Any], *, result: dict[str, Any] | None, error: DeepSeekWebError | None) -> str:
    run_id = f"run-{uuid.uuid4().hex}"
    response_text = str((result or {}).get("response_text") or "")
    citations = list((result or {}).get("citations") or [])
    run = {
        "run_id": run_id,
        "batch_id": task["batch_id"],
        "project_id": int(task["project_id"]),
        "question_id": int(task["question_id"]),
        "model_config_id": int(task["model_config_id"]),
        "provider": WEB_PROVIDER,
        "model": task.get("model") or "deepseek-web-search",
        "model_version": task.get("model_version") or "",
        "search_enabled": True,
        "search_mode": "official_web",
        "temperature": 0,
        "repeat_index": int(task.get("repeat_index") or 1),
        "thinking_type": "disabled",
        "reasoning_effort": "",
        "thinking_budget": None,
        "requested_at": task.get("started_at") or utc_now(),
        "response_text": response_text,
        "citations": citations,
        "latency_ms": int((result or {}).get("latency_ms") or 0),
        "status": "failed" if error else "success",
        "error_message": str(error or ""),
        "raw_response": {
            "source": "deepseek_web_official_search",
            "chat_id": (result or {}).get("chat_id", ""),
            "artifact_dir": (result or {}).get("artifact_dir", getattr(error, "artifact_dir", "")),
            "network_match_ratio": (result or {}).get("network_match_ratio", 0),
            "network_answer_available": (result or {}).get("network_answer_available", False),
            "error_code": error.code if error else "",
        },
    }
    insert_run(conn, run)
    if not error:
        insert_evaluation(
            conn,
            evaluate_answer(
                run_id=run_id,
                answer=response_text,
                target_brand=task.get("target_brand") or task.get("brand_name") or "",
                competitors=task.get("competitor_brands") or task.get("project_competitors") or "",
                website_domain=task.get("website_domain") or "",
                citations=citations,
            ),
        )
    return run_id


def perform_deepseek_web_task(task_id: str, db_target: Path | str | None = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_target = Path(db_target) if db_target else None
    try:
        from rq import get_current_job

        job = get_current_job()
        lease_owner = str(getattr(job, "id", "") or f"pid-{os.getpid()}")
    except ImportError:
        lease_owner = f"pid-{os.getpid()}"
    with get_conn(db_target) as conn:
        task = get_sampling_task(conn, task_id)
        if not task:
            raise ValueError("网页采样任务不存在")
        batch = get_sampling_batch(conn, task["batch_id"])
        if not batch or batch.get("status") in {"pause_requested", "paused", "completed"}:
            return {"task_id": task_id, "status": "skipped"}
        if not claim_sampling_task(conn, task_id, lease_owner, lease_seconds=web_job_timeout() + 60):
            return {"task_id": task_id, "status": "duplicate"}
        update_sampling_batch(conn, task["batch_id"], {"status": "running", "updated_at": utc_now()})
        conn.commit()
        task = get_sampling_task(conn, task_id) or task

    result = None
    final_error: DeepSeekWebError | None = None
    max_attempts = 3
    initial_attempt = int(task.get("attempt_count") or 1)
    for local_attempt in range(max_attempts):
        if local_attempt:
            with get_conn(db_target) as conn:
                current = get_sampling_task(conn, task_id) or task
                update_sampling_task(
                    conn,
                    task_id,
                    {"attempt_count": int(current.get("attempt_count") or 0) + 1, "heartbeat_at": utc_now(), "updated_at": utc_now()},
                )
            time.sleep(2)
        try:
            result = get_deepseek_web_browser().sample(batch_id=task["batch_id"], task_id=task_id, question=task["question"])
            final_error = None
            break
        except DeepSeekWebError as exc:
            final_error = exc
            if not exc.retryable or local_attempt >= max_attempts - 1:
                break

    with get_conn(db_target) as conn:
        task = get_sampling_task(conn, task_id) or task
        if final_error:
            blocked = final_error.code in BLOCKING_ERROR_CODES
            status = "blocked" if blocked else "failed"
            _persist_run(conn, task, result=None, error=final_error)
            update_sampling_task(
                conn,
                task_id,
                {
                    "status": status,
                    "artifact_dir": getattr(final_error, "artifact_dir", ""),
                    "error_code": final_error.code,
                    "error_message": str(final_error)[:1000],
                    "lease_owner": "",
                    "lease_expires_at": None,
                    "heartbeat_at": utc_now(),
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
            codes = recent_sampling_task_error_codes(conn, task["batch_id"], 3)
            circuit_open = len(codes) == 3 and len(set(codes)) == 1 and codes[0] in STRUCTURAL_ERROR_CODES
            if blocked or circuit_open:
                update_sampling_batch(
                    conn,
                    task["batch_id"],
                    {
                        "status": "paused",
                        "error_message": f"网页采样已暂停：{final_error.code}: {final_error}",
                        "updated_at": utc_now(),
                    },
                )
            counts = _sync_web_batch(conn, task["batch_id"])
            outcome = {"task_id": task_id, "status": status, "error_code": final_error.code, "attempt": initial_attempt + local_attempt}
        else:
            _persist_run(conn, task, result=result, error=None)
            update_sampling_task(
                conn,
                task_id,
                {
                    "status": "success",
                    "chat_id": result.get("chat_id", ""),
                    "artifact_dir": result.get("artifact_dir", ""),
                    "lease_owner": "",
                    "lease_expires_at": None,
                    "heartbeat_at": utc_now(),
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
            counts = _sync_web_batch(conn, task["batch_id"])
            outcome = {"task_id": task_id, "status": "success", "chat_id": result.get("chat_id", ""), "attempt": initial_attempt + local_attempt}
        conn.commit()

    if counts.get("status") == "running":
        try:
            cooldown = float(os.environ.get("DEEPSEEK_WEB_COOLDOWN_SECONDS", "3") or 3)
        except ValueError:
            cooldown = 3
        time.sleep(max(0, min(cooldown, 30)))
        enqueue_next_web_task(task["batch_id"], db_target)
    return outcome


def resume_web_batch(batch_id: str, db_target: Path | str | None = DEFAULT_DB_PATH) -> dict[str, Any]:
    with get_conn(db_target) as conn:
        batch = get_sampling_batch(conn, batch_id)
        if not batch:
            raise ValueError("批次不存在")
        if not batch_is_web(batch):
            raise ValueError("当前批次不是 DeepSeek 官网采样批次")
        reset = reset_resumable_sampling_tasks(conn, batch_id)
        counts = sampling_task_counts(conn, batch_id)
        update_sampling_batch(
            conn,
            batch_id,
            {"status": "running", "error_message": "", "finished_at": None, "updated_at": utc_now()},
        )
        conn.commit()
    job_id = "" if counts["running"] else enqueue_next_web_task(batch_id, db_target)
    return {"batch_id": batch_id, "status": "running", "reset": reset, "job_id": job_id}


def recover_web_batches(db_target: Path | str | None = DEFAULT_DB_PATH) -> list[str]:
    from .db import list_sampling_batches

    recovered = []
    with get_conn(db_target) as conn:
        batches = [batch for batch in list_sampling_batches(conn) if batch_is_web(batch) and batch.get("status") in {"queued", "running"}]
        for batch in batches:
            reset_running_sampling_tasks(conn, batch["batch_id"])
            update_sampling_batch(conn, batch["batch_id"], {"status": "running", "updated_at": utc_now()})
            recovered.append(batch["batch_id"])
        conn.commit()
    for batch_id in recovered:
        enqueue_next_web_task(batch_id, db_target)
    return recovered


def batch_is_web(batch: dict[str, Any] | None) -> bool:
    if not batch:
        return False
    config = batch.get("config") or {}
    return str(config.get("provider_mode") or "") == WEB_PROVIDER
