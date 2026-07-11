#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redis import Redis
from rq import Queue

from src.db import (
    create_model_config,
    create_project,
    create_sampling_batch,
    get_conn,
    get_sampling_batch,
    get_sampling_task,
    import_question_content_rows,
    init_db,
    list_execution_attempts,
    list_runs_by_batch,
    sampling_task_counts,
    update_sampling_task,
    utc_now,
)
from src.reconciler import reconcile_once
from src.runner import prepare_batch_ledger
from src.tasks import mark_rq_job_failed, perform_sampling_task


def wait_until(predicate, timeout: float, label: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise TimeoutError(label)


def start_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "worker.py"],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_worker(process: subprocess.Popen, force: bool = False) -> None:
    if process.poll() is not None:
        return
    if force:
        try:
            children = subprocess.check_output(["pgrep", "-P", str(process.pid)], text=True).split()
        except (subprocess.CalledProcessError, FileNotFoundError):
            children = []
        for child in children:
            try:
                os.kill(int(child), signal.SIGKILL)
            except ProcessLookupError:
                pass
    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


def main() -> int:
    if not os.environ.get("DATABASE_URL") or os.environ.get("TASK_QUEUE_BACKEND") != "rq":
        raise SystemExit("DATABASE_URL and TASK_QUEUE_BACKEND=rq are required")
    redis = Redis.from_url(os.environ["REDIS_URL"])
    queue = Queue(os.environ.get("RQ_QUEUE_NAME", "geo-audit"), connection=redis)
    redis.flushdb()
    init_db()
    suffix = uuid.uuid4().hex[:8]
    batch_id = f"fault-drill-{suffix}"
    with get_conn(None) as conn:
        project_id = create_project(conn, {"client_name": "V2 fault drill", "brand_name": f"drill-{suffix}"})
        import_question_content_rows(conn, project_id, [{"问题内容": f"故障演练问题 {index}"} for index in range(6)])
        model_id = create_model_config(conn, {"provider": "mock", "label": "Fault drill mock", "model": "mock-model", "active": True, "supports_pure": True})
        config = {"repeat_count": 1, "models": [{"model_config_id": model_id, "search_enabled": False}]}
        create_sampling_batch(conn, {"batch_id": batch_id, "project_id": project_id, "status": "queued", "total_count": 6, "batch_name": "PostgreSQL Redis fault drill", "config": config, "config_snapshot": config})
        tasks = prepare_batch_ledger(conn, batch_id, project_id, config)
    crash_task = tasks[0]["task_id"]
    crash_job = queue.enqueue("scripts.fault_drill_jobs.claim_then_hang", crash_task, 3, job_id=f"crash-{suffix}", job_timeout=60, on_failure=mark_rq_job_failed)
    with get_conn(None) as conn:
        update_sampling_task(conn, crash_task, {"rq_job_id": crash_job.id, "updated_at": utc_now()})

    crash_worker = start_worker()
    wait_until(lambda: _task_status(crash_task) == "running", 15, "crash task was not claimed")
    stop_worker(crash_worker, force=True)
    with get_conn(None) as conn:
        conn.execute(
            "UPDATE sampling_tasks SET lease_expires_at = ? WHERE task_id = ? AND status = 'running'",
            ("2000-01-01T00:00:00+00:00", crash_task),
        )
    wait_until(lambda: _task_lease_expired(crash_task), 10, "crash task lease did not expire")

    def dispatch(event, _batch):
        task_id = event["payload"]["task_id"]
        job = queue.enqueue(perform_sampling_task, task_id, job_id=f"recover-{task_id}", job_timeout=60, on_failure=mark_rq_job_failed)
        with get_conn(None) as conn:
            update_sampling_task(conn, task_id, {"rq_job_id": job.id, "updated_at": utc_now()})

    recovered = reconcile_once(None, dispatch, stale_after_seconds=1)

    workers = [start_worker(), start_worker()]
    try:
        wait_until(lambda: _batch_done(batch_id), 30, "batch did not converge")
    finally:
        for worker in workers:
            stop_worker(worker)

    with get_conn(None) as conn:
        batch = get_sampling_batch(conn, batch_id)
        counts = sampling_task_counts(conn, batch_id)
        runs = list_runs_by_batch(conn, batch_id)
        attempts = list_execution_attempts(conn, batch_id)
        duplicate_current = conn.execute(
            """SELECT COUNT(*) AS count FROM (
                SELECT question_id, model_config_id, search_enabled, repeat_index, COUNT(*)
                FROM model_runs WHERE batch_id = ? AND is_current = 1
                GROUP BY question_id, model_config_id, search_enabled, repeat_index HAVING COUNT(*) > 1
            ) AS duplicates""",
            (batch_id,),
        ).fetchone()["count"]
        expired = conn.execute("SELECT COUNT(*) AS count FROM sampling_tasks WHERE batch_id = ? AND status = 'running' AND lease_expires_at < ?", (batch_id, utc_now())).fetchone()["count"]
    result = {
        "ok": bool(batch and batch["status"] == "completed" and counts["success"] == 6 and len(runs) == 6 and duplicate_current == 0 and expired == 0),
        "batch_id": batch_id,
        "batch_status": batch["status"] if batch else "missing",
        "counts": counts,
        "runs": len(runs),
        "attempts": len(attempts),
        "reconciler": recovered,
        "duplicate_current": duplicate_current,
        "expired_leases": expired,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _task_status(task_id: str) -> str:
    with get_conn(None) as conn:
        task = get_sampling_task(conn, task_id)
    return str((task or {}).get("status") or "")


def _batch_done(batch_id: str) -> bool:
    with get_conn(None) as conn:
        batch = get_sampling_batch(conn, batch_id)
    return bool(batch and batch.get("status") == "completed")


def _task_lease_expired(task_id: str) -> bool:
    with get_conn(None) as conn:
        row = conn.execute(
            "SELECT 1 AS expired FROM sampling_tasks WHERE task_id = ? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (task_id, utc_now()),
        ).fetchone()
    return bool(row)


if __name__ == "__main__":
    raise SystemExit(main())
