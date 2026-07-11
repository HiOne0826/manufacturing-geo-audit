from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .db import ensure_sampling_task_dispatch_event, get_conn, get_project, get_sampling_batch, get_sampling_task, list_failed_runs_by_batch, list_runs_by_batch, reset_resumable_sampling_tasks, update_sampling_batch, update_sampling_batch_cas, update_sampling_task, utc_now
from .runner import build_missing_tasks_for_batch, estimate_batch_total, execute_sampling_task, prepare_batch_ledger, provider_concurrency_group, provider_concurrency_limits, rerun_failed_runs, restore_task_snapshot, retry_count, run_batch, run_prepared_tasks


ProgressHook = Callable[[dict[str, Any]], None]


def perform_sampling_task(task_id: str, db_target: Path | str | None = None) -> dict[str, Any]:
    import threading
    from .db import get_sampling_task, sampling_task_counts

    with get_conn(db_target) as conn:
        ledger = get_sampling_task(conn, task_id)
        if not ledger:
            raise ValueError("采样任务不存在")
        batch = get_sampling_batch(conn, ledger["batch_id"])
        if not batch:
            raise ValueError("批次不存在")
        if batch.get("status") in {"pause_requested", "paused", "cancelled", "completed"}:
            return {"task_id": task_id, "status": "skipped", "batch_status": batch.get("status")}
        if batch.get("status") == "queued":
            update_sampling_batch_cas(conn, batch["batch_id"], {"queued"}, {"status": "running", "started_at": utc_now(), "updated_at": utc_now()})
            batch = get_sampling_batch(conn, batch["batch_id"]) or batch
        restored = restore_task_snapshot(conn, ledger)
        if restored:
            task, project = restored
        else:
            project = get_project(conn, int(batch["project_id"]))
            tasks = prepare_batch_ledger(conn, batch["batch_id"], int(batch["project_id"]), batch.get("config") or {})
            task = next((item for item in tasks if item.get("task_id") == task_id), None)
        if not task or not project:
            raise ValueError("无法从批次快照恢复采样任务")
        limits = provider_concurrency_limits(batch.get("config") or {})
        group = provider_concurrency_group(task["base"]["provider"] or "unknown")
        limit = limits.get(group, 1)
    result = execute_sampling_task(
        task,
        db_target=db_target,
        project=project,
        semaphore=threading.Semaphore(limit),
        max_retries=retry_count(batch.get("config") or {}),
        global_limit=limit,
    )
    with get_conn(db_target) as conn:
        counts = sampling_task_counts(conn, batch["batch_id"])
        current = get_sampling_batch(conn, batch["batch_id"]) or batch
        status = "completed" if counts["completed"] >= counts["total"] else "pause_requested" if current.get("status") == "pause_requested" else "running"
        update_sampling_batch_cas(
            conn,
            batch["batch_id"],
            {str(current.get("status") or "running")},
            {
                "status": status,
                "total_count": counts["total"], "completed_count": counts["completed"],
                "success_count": counts["success"], "failed_count": counts["failed"] + counts["blocked"],
                "finished_at": utc_now() if status == "completed" else None, "updated_at": utc_now(),
            },
        )
    return result


def _latest_logical_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _full_batch_counts(conn, batch_id: str) -> dict[str, int | str]:
    batch = get_sampling_batch(conn, batch_id)
    rows = _latest_logical_runs(list_runs_by_batch(conn, batch_id))
    if batch:
        try:
            total = estimate_batch_total(conn, int(batch["project_id"]), batch.get("config") or {})
        except (TypeError, ValueError):
            # Legacy batches may predate the immutable model matrix. Failure
            # handling must still converge instead of raising a second error.
            total = int(batch.get("total_count") or 0)
    else:
        total = len(rows)
    total = max(total, len(rows))
    success = sum(1 for row in rows if row.get("status") == "success")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    completed = success + failed
    # Provider/task failures are a completed batch with partial_failure outcome;
    # only infrastructure failures use failed_system.
    status = "completed" if completed >= total else "queued"
    return {"status": status, "total": total, "completed": completed, "success": success, "failed": failed}


def _failure_counts(conn, batch_id: str) -> dict[str, int | str]:
    counts = _full_batch_counts(conn, batch_id)
    counts["status"] = "failed_system"
    return counts


def _pause_requested(batch_id: str, db_target: Path | str | None = None) -> bool:
    with get_conn(db_target) as conn:
        batch = get_sampling_batch(conn, batch_id)
    return bool(batch and batch.get("status") == "pause_requested")


def request_batch_pause(batch_id: str, db_target: Path | str | None = None) -> dict[str, Any]:
    with get_conn(db_target) as conn:
        batch = get_sampling_batch(conn, batch_id)
        if not batch:
            raise ValueError("批次不存在")
        if batch.get("status") == "running":
            update_sampling_batch_cas(conn, batch_id, {"running"}, {"status": "pause_requested", "updated_at": utc_now()})
            conn.commit()
            batch = get_sampling_batch(conn, batch_id)
        return batch or {"batch_id": batch_id, "status": "unknown"}


def perform_batch(
    batch_id: str,
    project_id: int,
    payload: dict[str, Any],
    progress_hook: ProgressHook | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
    try:
        with get_conn(db_target) as conn:
            claimed = update_sampling_batch_cas(
                conn,
                batch_id,
                {"queued"},
                {"status": "running", "started_at": utc_now(), "updated_at": utc_now()},
            )
            if not claimed:
                batch = get_sampling_batch(conn, batch_id)
                counts = _full_batch_counts(conn, batch_id)
                return {"batch_id": batch_id, **counts, "duplicate_dispatch": True, "status": (batch or {}).get("status", counts["status"])}
            conn.commit()

            def on_progress(progress: dict[str, Any]) -> None:
                current_status = (get_sampling_batch(conn, batch_id) or {}).get("status")
                next_status = "pause_requested" if current_status == "pause_requested" else "running"
                update_sampling_batch(
                    conn,
                    batch_id,
                    {
                        "status": next_status,
                        "total_count": progress["total"],
                        "completed_count": progress["completed"],
                        "failed_count": progress["failed"],
                        "success_count": progress["success"],
                        "updated_at": utc_now(),
                    },
                )
                conn.commit()
                if progress_hook:
                    progress_hook({**progress, "status": next_status})

            result = run_batch(
                conn,
                project_id,
                payload,
                batch_id=batch_id,
                progress_callback=on_progress,
                should_pause=lambda: _pause_requested(batch_id, db_target),
            )
            final_status = "paused" if result.get("status") == "paused" else "completed"
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": final_status,
                    "total_count": estimate_batch_total(conn, project_id, payload),
                    "completed_count": result["success"] + result["failed"],
                    "failed_count": result["failed"],
                    "success_count": result["success"],
                    "finished_at": utc_now() if final_status == "completed" else None,
                    "updated_at": utc_now(),
                },
            )
        return result
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=db_target)
        raise


def perform_resume_batch(
    batch_id: str,
    payload: dict[str, Any] | None = None,
    progress_hook: ProgressHook | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    try:
        with get_conn(db_target) as conn:
            batch = get_sampling_batch(conn, batch_id)
            if not batch:
                raise ValueError("批次不存在")
            if batch.get("status") not in {"paused", "queued", "failed", "failed_system"}:
                raise ValueError("只有已暂停、排队或失败的批次可以继续执行")
            project_id, run_config, tasks = build_missing_tasks_for_batch(conn, batch_id, payload)
            project = get_project(conn, project_id)
            if not project:
                raise ValueError("项目不存在")
            counts = _full_batch_counts(conn, batch_id)
            if not tasks:
                update_sampling_batch(
                    conn,
                    batch_id,
                    {
                        "status": counts["status"],
                        "total_count": counts["total"],
                        "completed_count": counts["completed"],
                        "failed_count": counts["failed"],
                        "success_count": counts["success"],
                        "finished_at": utc_now(),
                        "updated_at": utc_now(),
                    },
                )
                return {"batch_id": batch_id, "total": 0, "failed": 0, "success": 0, "status": counts["status"]}
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "running",
                    "total_count": counts["total"],
                    "completed_count": counts["completed"],
                    "failed_count": counts["failed"],
                    "success_count": counts["success"],
                    "error_message": "",
                    "started_at": batch.get("started_at") or utc_now(),
                    "finished_at": None,
                    "updated_at": utc_now(),
                },
            )
            conn.commit()

            base_completed = int(counts["completed"])
            base_failed = int(counts["failed"])
            base_success = int(counts["success"])

            def on_progress(progress: dict[str, Any]) -> None:
                current_status = (get_sampling_batch(conn, batch_id) or {}).get("status")
                next_status = "pause_requested" if current_status == "pause_requested" else "running"
                update_sampling_batch(
                    conn,
                    batch_id,
                    {
                        "status": next_status,
                        "total_count": counts["total"],
                        "completed_count": base_completed + progress["completed"],
                        "failed_count": base_failed + progress["failed"],
                        "success_count": base_success + progress["success"],
                        "updated_at": utc_now(),
                    },
                )
                conn.commit()
                if progress_hook:
                    progress_hook(
                        {
                            **progress,
                            "status": next_status,
                            "total": counts["total"],
                            "completed": base_completed + progress["completed"],
                            "failed": base_failed + progress["failed"],
                            "success": base_success + progress["success"],
                        }
                    )

            result = run_prepared_tasks(
                tasks=tasks,
                db_target=db_target,
                project=project,
                config=run_config,
                batch_id=batch_id,
                progress_callback=on_progress,
                should_pause=lambda: _pause_requested(batch_id, db_target),
            )
            final_counts = _full_batch_counts(conn, batch_id)
            final_status = "paused" if result.get("status") == "paused" else final_counts["status"]
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": final_status,
                    "total_count": final_counts["total"],
                    "completed_count": final_counts["completed"],
                    "failed_count": final_counts["failed"],
                    "success_count": final_counts["success"],
                    "finished_at": utc_now() if final_status != "paused" else None,
                    "updated_at": utc_now(),
                },
            )
        return {**result, "status": final_status}
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=db_target)
        raise


def perform_rerun_failed(
    batch_id: str,
    payload: dict[str, Any],
    progress_hook: ProgressHook | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
    try:
        with get_conn(db_target) as conn:
            failed_runs = list_failed_runs_by_batch(conn, batch_id)
            if not failed_runs:
                batch = get_sampling_batch(conn, batch_id)
                return {
                    "batch_id": batch_id,
                    "total": int((batch or {}).get("total_count", 0) or 0),
                    "failed": int((batch or {}).get("failed_count", 0) or 0),
                    "success": int((batch or {}).get("success_count", 0) or 0),
                    "status": (batch or {}).get("status", "completed"),
                }
            reset_resumable_sampling_tasks(conn, batch_id)
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "running",
                    "total_count": len(failed_runs),
                    "completed_count": 0,
                    "failed_count": 0,
                    "success_count": 0,
                    "error_message": "",
                    "started_at": utc_now(),
                    "finished_at": None,
                    "updated_at": utc_now(),
                },
            )
            conn.commit()
            result = rerun_failed_runs(conn, batch_id, failed_runs, payload)
            full_counts = _full_batch_counts(conn, batch_id)
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": full_counts["status"],
                    "total_count": full_counts["total"],
                    "completed_count": full_counts["completed"],
                    "failed_count": full_counts["failed"],
                    "success_count": full_counts["success"],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
        if progress_hook:
            progress_hook({"batch_id": batch_id, **result})
        return result
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=db_target)
        raise


def perform_rerun_runs(
    batch_id: str,
    run_ids: list[int],
    payload: dict[str, Any] | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    run_ids = [int(item) for item in run_ids if int(item)]
    if not run_ids:
        return {"batch_id": batch_id, "total": 0, "failed": 0, "success": 0, "status": "completed"}
    try:
        with get_conn(db_target) as conn:
            placeholders = ",".join("?" for _ in run_ids)
            rows = conn.execute(
                f"""
                SELECT r.*, q.question, q.target_brand, q.competitor_brands
                FROM model_runs r
                JOIN questions q ON q.id = r.question_id
                WHERE r.batch_id = ?
                  AND r.id IN ({placeholders})
                  AND COALESCE(r.is_current, 1) = 1
                ORDER BY r.id ASC
                """,
                (batch_id, *run_ids),
            ).fetchall()
            runs = [dict(row) for row in rows]
            if not runs:
                counts = _full_batch_counts(conn, batch_id)
                return {"batch_id": batch_id, "total": 0, "failed": 0, "success": 0, "status": counts["status"]}
            selected_run_ids = [str(row.get("run_id") or "") for row in runs]
            attempt_placeholders = ",".join("?" for _ in selected_run_ids)
            task_rows = conn.execute(
                f"SELECT DISTINCT task_id FROM execution_attempts WHERE run_id IN ({attempt_placeholders}) AND task_id <> ''",
                selected_run_ids,
            ).fetchall()
            for task_row in task_rows:
                update_sampling_task(
                    conn,
                    str(task_row["task_id"]),
                    {
                        "status": "queued", "rq_job_id": "", "lease_owner": "",
                        "lease_expires_at": None, "heartbeat_at": None,
                        "error_code": "", "error_message": "", "finished_at": None,
                        "updated_at": utc_now(),
                    },
                )
            counts = _full_batch_counts(conn, batch_id)
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "running",
                    "total_count": counts["total"],
                    "completed_count": counts["completed"],
                    "failed_count": counts["failed"],
                    "success_count": counts["success"],
                    "error_message": "",
                    "finished_at": None,
                    "updated_at": utc_now(),
                },
            )
            conn.commit()
            result = rerun_failed_runs(conn, batch_id, runs, payload)
            full_counts = _full_batch_counts(conn, batch_id)
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": full_counts["status"],
                    "total_count": full_counts["total"],
                    "completed_count": full_counts["completed"],
                    "failed_count": full_counts["failed"],
                    "success_count": full_counts["success"],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
            conn.commit()
        return {**result, "status": full_counts["status"]}
    except Exception as exc:
        mark_batch_failed(batch_id, str(exc), db_target=db_target)
        raise


def mark_batch_failed(
    batch_id: str,
    error: str,
    counts: dict[str, Any] | None = None,
    db_target: Path | str | None = None,
) -> None:
    counts = counts or {}
    with get_conn(db_target) as conn:
        persisted = _failure_counts(conn, batch_id)
        completed = counts.get("completed", persisted["completed"])
        failed = counts.get("failed", persisted["failed"])
        success = counts.get("success", persisted["success"])
        update_sampling_batch_cas(
            conn,
            batch_id,
            {"queued", "running", "pause_requested", "paused", "failed", "failed_system"},
            {
                "status": "failed_system",
                "error_message": error,
                "finished_at": utc_now(),
                "updated_at": utc_now(),
                "total_count": counts.get("total", persisted["total"]),
                "completed_count": completed,
                "failed_count": failed,
                "success_count": success,
            },
        )
        conn.commit()


def mark_rq_job_failed(job, connection=None, exc_type=None, exc_value=None, traceback=None, db_target: Path | str | None = None) -> None:
    args = list(getattr(job, "args", ()) or ())
    if not args:
        return
    aggregate_id = str(args[0] or "")
    if not aggregate_id:
        return
    timeout = getattr(job, "timeout", None)
    exc_text = str(exc_value or exc_type or "RQ job failed")
    if timeout and "timeout" not in exc_text.lower():
        exc_text = f"{exc_text}; job_timeout={timeout}"
    with get_conn(db_target) as conn:
        task = get_sampling_task(conn, aggregate_id)
        if task:
            batch = get_sampling_batch(conn, task["batch_id"])
            failed_job_id = str(getattr(job, "id", "") or "")
            current_job_id = str(task.get("rq_job_id") or "")
            if (
                not batch
                or batch.get("status") not in {"queued", "running"}
                or task.get("status") not in {"queued", "running"}
                or (failed_job_id and current_job_id and failed_job_id != current_job_id)
            ):
                return
            # Fenced cleanup plus a deterministic outbox event makes repeated
            # failure callbacks safe and lets the reconciler perform the retry.
            update_sampling_task(
                conn,
                aggregate_id,
                {
                    "status": "queued",
                    "rq_job_id": "",
                    "lease_owner": "",
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "error_code": "worker_job_failed",
                    "error_message": exc_text,
                    "updated_at": utc_now(),
                },
            )
            ensure_sampling_task_dispatch_event(
                conn,
                aggregate_id,
                task["batch_id"],
                int(task.get("attempt_count") or 0),
                f"rq-{failed_job_id}" if failed_job_id else "",
            )
            conn.commit()
            return
    # Compatibility for legacy RQ jobs whose first argument is the batch id.
    mark_batch_failed(aggregate_id, exc_text, db_target=db_target)
