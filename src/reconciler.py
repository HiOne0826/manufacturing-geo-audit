from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .db import (
    DEFAULT_DB_PATH,
    claim_pending_outbox,
    ensure_sampling_task_dispatch_event,
    get_conn,
    get_sampling_batch,
    get_sampling_task,
    list_sampling_batches,
    mark_outbox_delivered,
    mark_outbox_failed,
    sampling_task_counts,
    update_sampling_batch,
    upsert_worker_heartbeat,
    utc_now,
)


DispatchCallback = Callable[[dict[str, Any], dict[str, Any]], None]


def reconcile_once(
    db_target: Path | str | None = DEFAULT_DB_PATH,
    dispatch_callback: DispatchCallback | None = None,
    *,
    stale_after_seconds: int | None = None,
) -> dict[str, int]:
    stale_after_seconds = stale_after_seconds or int(os.environ.get("RECONCILER_STALE_SECONDS", "600"))
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(60, stale_after_seconds))).isoformat()
    stats = {"outbox_delivered": 0, "outbox_failed": 0, "leases_requeued": 0, "task_events_created": 0, "attempts_uncertain": 0, "batches_recounted": 0, "batches_completed": 0}
    with get_conn(db_target) as conn:
        upsert_worker_heartbeat(
            conn,
            os.environ.get("RECONCILER_ID", f"reconciler-{socket.gethostname()}-{os.getpid()}"),
            "__reconciler__",
            metadata={"kind": "reconciler", "pid": os.getpid(), "hostname": socket.gethostname()},
        )
        expired_rows = conn.execute(
            """
            SELECT task_id FROM sampling_tasks
            WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
              AND batch_id IN (
                  SELECT batch_id FROM sampling_batches
                  WHERE status IN ('queued', 'running') AND archived_at IS NULL
              )
            """,
            (utc_now(),),
        ).fetchall()
        expired_task_ids = [str(row["task_id"]) for row in expired_rows]
        cur = conn.execute(
            """
            UPDATE sampling_tasks
            SET status = 'queued', lease_owner = '', lease_expires_at = NULL,
                heartbeat_at = NULL, rq_job_id = '', updated_at = ?
            WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
              AND batch_id IN (
                  SELECT batch_id FROM sampling_batches
                  WHERE status IN ('queued', 'running') AND archived_at IS NULL
              )
            """,
            (utc_now(), utc_now()),
        )
        stats["leases_requeued"] = max(0, int(getattr(cur, "rowcount", 0)))
        for task_id in expired_task_ids:
            attempt_cur = conn.execute(
                """
                UPDATE execution_attempts
                SET status = 'uncertain', error_code = 'lease_expired',
                    error_message = 'worker lease expired before durable completion',
                    finished_at = ?, updated_at = ?
                WHERE task_id = ? AND persistence_committed = 0
                  AND status IN ('running', 'response_received')
                """,
                (utc_now(), utc_now(), task_id),
            )
            stats["attempts_uncertain"] += max(0, int(getattr(attempt_cur, "rowcount", 0)))

        # Repair every active queued-without-job task, including tasks recovered
        # from a dead worker and tasks whose prior dispatcher died before enqueue.
        undispatched = conn.execute(
            """
            SELECT t.task_id, t.batch_id, t.attempt_count
            FROM sampling_tasks t
            JOIN sampling_batches b ON b.batch_id = t.batch_id
            JOIN model_configs m ON m.id = t.model_config_id
            WHERE t.status = 'queued' AND COALESCE(t.rq_job_id, '') = ''
              AND b.status IN ('queued', 'running') AND b.archived_at IS NULL
              AND m.provider <> 'deepseek_web'
            ORDER BY t.id ASC
            """
        ).fetchall()
        for row in undispatched:
            if ensure_sampling_task_dispatch_event(conn, row["task_id"], row["batch_id"], row["attempt_count"]):
                stats["task_events_created"] += 1

        # Persist recovery before invoking an external dispatcher. This avoids
        # holding the SQLite write lock while enqueue code records rq_job_id in
        # a separate connection, and ensures a crash leaves a durable event.
        conn.commit()

        claimant = os.environ.get("RECONCILER_ID", f"reconciler-{socket.gethostname()}-{os.getpid()}")
        pending = claim_pending_outbox(conn, claimant, limit=100)
        conn.commit()
        for event in pending:
            if event.get("event_type") == "dispatch_sampling_task":
                task = get_sampling_task(conn, str(event.get("aggregate_id") or ""))
                batch = get_sampling_batch(conn, str((task or {}).get("batch_id") or ""))
                dispatchable = bool(
                    task and task.get("provider") != "deepseek_web"
                    and task.get("status") == "queued" and not task.get("rq_job_id")
                )
            else:
                batch = get_sampling_batch(conn, str(event.get("aggregate_id") or ""))
                dispatchable = True
            if not batch or batch.get("status") not in {"queued", "running"} or not dispatchable:
                mark_outbox_delivered(conn, event["event_id"], claimant)
                stats["outbox_delivered"] += 1
                continue
            if dispatch_callback is None:
                continue
            try:
                dispatch_callback(event, batch)
                mark_outbox_delivered(conn, event["event_id"], claimant)
                stats["outbox_delivered"] += 1
            except Exception as exc:
                mark_outbox_failed(conn, event["event_id"], str(exc), claimant)
                stats["outbox_failed"] += 1

        cur = conn.execute(
            """
            UPDATE execution_attempts
            SET status = 'uncertain', finished_at = ?, updated_at = ?
            WHERE persistence_committed = 0
              AND status IN ('running', 'response_received')
              AND updated_at < ?
            """,
            (utc_now(), utc_now(), cutoff),
        )
        stats["attempts_uncertain"] += max(0, int(getattr(cur, "rowcount", 0)))

        for batch in list_sampling_batches(conn):
            counts = sampling_task_counts(conn, batch["batch_id"])
            if counts["total"] <= 0:
                continue
            desired = {
                "total_count": counts["total"],
                "completed_count": counts["completed"],
                "success_count": counts["success"],
                "failed_count": counts["failed"] + counts["blocked"],
                "updated_at": utc_now(),
            }
            should_complete = (
                batch.get("status") in {"queued", "running"}
                and counts["completed"] >= counts["total"]
            )
            if should_complete:
                desired.update({"status": "completed", "finished_at": utc_now(), "error_message": ""})
            if should_complete or any(int(batch.get(key, 0) or 0) != int(value) for key, value in desired.items() if key.endswith("_count")):
                update_sampling_batch(conn, batch["batch_id"], desired)
                stats["batches_recounted"] += 1
                stats["batches_completed"] += int(should_complete)
    return stats
