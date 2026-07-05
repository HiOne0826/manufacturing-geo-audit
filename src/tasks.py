from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .db import get_conn, get_sampling_batch, list_failed_runs_by_batch, update_sampling_batch, utc_now
from .runner import rerun_failed_runs, run_batch


ProgressHook = Callable[[dict[str, Any]], None]


def perform_batch(
    batch_id: str,
    project_id: int,
    payload: dict[str, Any],
    progress_hook: ProgressHook | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
    with get_conn(db_target) as conn:
        update_sampling_batch(conn, batch_id, {"status": "running", "started_at": utc_now(), "updated_at": utc_now()})
        conn.commit()

        def on_progress(progress: dict[str, Any]) -> None:
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
            if progress_hook:
                progress_hook(progress)

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
    return result


def perform_rerun_failed(
    batch_id: str,
    payload: dict[str, Any],
    progress_hook: ProgressHook | None = None,
    db_target: Path | str | None = None,
) -> dict[str, Any]:
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
    if progress_hook:
        progress_hook({"batch_id": batch_id, **result})
    return result


def mark_batch_failed(
    batch_id: str,
    error: str,
    counts: dict[str, Any] | None = None,
    db_target: Path | str | None = None,
) -> None:
    counts = counts or {}
    with get_conn(db_target) as conn:
        update_sampling_batch(
            conn,
            batch_id,
            {
                "status": "failed",
                "error_message": error,
                "finished_at": utc_now(),
                "updated_at": utc_now(),
                "completed_count": counts.get("completed", 0),
                "failed_count": counts.get("failed", 0),
                "success_count": counts.get("success", 0),
            },
        )
