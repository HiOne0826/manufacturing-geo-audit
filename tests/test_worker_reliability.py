from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import (
    claim_sampling_task,
    claim_pending_outbox,
    create_execution_attempt,
    create_outbox_event,
    create_project,
    create_sampling_batch,
    create_sampling_tasks,
    finalize_sampling_task,
    get_conn,
    get_sampling_task,
    import_questions_text,
    init_db,
    list_model_configs,
    list_pending_outbox,
    mark_outbox_failed,
    list_questions,
    list_worker_heartbeats,
    reliability_status,
    renew_sampling_task_lease,
    update_execution_attempt,
    update_sampling_task,
    upsert_worker_heartbeat,
    utc_now,
)
from src.reconciler import reconcile_once
from src.runner import defer_sampling_task_for_slot
from src.tasks import _queue_rerun_tasks, mark_rq_job_failed
from src.worker_health import task_lease_heartbeat, worker_heartbeat


class WorkerReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "worker.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            self.project_id = create_project(conn, {"client_name": "worker", "brand_name": "worker"})
            import_questions_text(conn, self.project_id, "heartbeat question")
            self.question_id = int(list_questions(conn, self.project_id)[0]["id"])
            self.model_id = int(list_model_configs(conn)[0]["id"])
            create_sampling_batch(
                conn,
                {"batch_id": "batch-heartbeat", "project_id": self.project_id, "status": "queued", "config": {}},
            )
            create_sampling_tasks(
                conn,
                [{
                    "task_id": "task-heartbeat", "task_key": "key-heartbeat",
                    "batch_id": "batch-heartbeat", "project_id": self.project_id,
                    "question_id": self.question_id, "model_config_id": self.model_id,
                    "repeat_index": 1, "status": "queued",
                }],
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_lease_renewal_is_fenced_by_owner_and_status(self) -> None:
        with get_conn(self.db_path) as conn:
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "worker-a", 60))
            before = get_sampling_task(conn, "task-heartbeat")["lease_expires_at"]
            self.assertFalse(renew_sampling_task_lease(conn, "task-heartbeat", "worker-b", 120))
            self.assertTrue(renew_sampling_task_lease(conn, "task-heartbeat", "worker-a", 120))
            after = get_sampling_task(conn, "task-heartbeat")["lease_expires_at"]
            self.assertGreater(after, before)
            update_sampling_task(conn, "task-heartbeat", {"status": "success"})
            self.assertFalse(renew_sampling_task_lease(conn, "task-heartbeat", "worker-a", 120))

    def test_periodic_lease_heartbeat_stops_when_task_finishes(self) -> None:
        with get_conn(self.db_path) as conn:
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "worker-a", 60))
            initial = get_sampling_task(conn, "task-heartbeat")["heartbeat_at"]
        heartbeat = task_lease_heartbeat(
            self.db_path, "task-heartbeat", "worker-a", lease_seconds=60, interval_seconds=0.05
        ).start()
        time.sleep(0.12)
        with get_conn(self.db_path) as conn:
            renewed = get_sampling_task(conn, "task-heartbeat")["heartbeat_at"]
            self.assertGreater(renewed, initial)
            update_sampling_task(conn, "task-heartbeat", {"status": "success"})
        time.sleep(0.08)
        heartbeat.stop()
        self.assertFalse(heartbeat._thread.is_alive())

    def test_late_worker_cannot_finalize_after_lease_takeover(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with get_conn(self.db_path) as conn:
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "worker-old", 60))
            update_sampling_task(conn, "task-heartbeat", {"lease_expires_at": expired})
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "worker-new", 60))
            self.assertFalse(finalize_sampling_task(conn, "task-heartbeat", "worker-old", status="success"))
            self.assertTrue(finalize_sampling_task(conn, "task-heartbeat", "worker-new", status="success"))
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["status"], "success")
            self.assertEqual(task["lease_owner"], "")

    def test_worker_heartbeat_reports_fresh_and_stale_without_secrets(self) -> None:
        with get_conn(self.db_path) as conn:
            upsert_worker_heartbeat(
                conn, "worker-1", "geo-audit", metadata={"kind": "rq", "pid": 42, "api_key": "secret"}
            )
            fresh = list_worker_heartbeats(conn, stale_after_seconds=60)[0]
            self.assertTrue(fresh["available"])
            self.assertNotIn("api_key", fresh["metadata"])
            old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            conn.execute("UPDATE worker_heartbeats SET heartbeat_at = ? WHERE worker_id = ?", (old, "worker-1"))
            stale = list_worker_heartbeats(conn, stale_after_seconds=60)[0]
            self.assertTrue(stale["stale"])
            self.assertFalse(stale["available"])

    def test_worker_heartbeat_accepts_postgres_datetime_format(self) -> None:
        with get_conn(self.db_path) as conn:
            upsert_worker_heartbeat(conn, "worker-postgres", "geo-audit", metadata={"kind": "rq"})
            postgres_style = datetime.now(timezone(timedelta(hours=8))).isoformat(sep=" ")
            conn.execute(
                "UPDATE worker_heartbeats SET heartbeat_at = ? WHERE worker_id = ?",
                (postgres_style, "worker-postgres"),
            )
            worker = next(
                item for item in list_worker_heartbeats(conn, stale_after_seconds=60)
                if item["worker_id"] == "worker-postgres"
            )

        self.assertFalse(worker["stale"])
        self.assertTrue(worker["available"])

    def test_worker_heartbeat_loop_marks_clean_shutdown(self) -> None:
        heartbeat = worker_heartbeat(
            "worker-loop", "geo-audit", kind="rq", db_target=self.db_path, interval_seconds=0.05
        ).start()
        time.sleep(0.08)
        heartbeat.stop()
        with get_conn(self.db_path) as conn:
            worker = next(item for item in list_worker_heartbeats(conn) if item["worker_id"] == "worker-loop")
            self.assertEqual(worker["status"], "stopped")
            self.assertFalse(worker["available"])

    def test_reliability_snapshot_and_reconciler_recover_crash_state(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        with get_conn(self.db_path) as conn:
            update_sampling_task(
                conn, "task-heartbeat",
                {"status": "running", "lease_owner": "dead", "lease_expires_at": expired},
            )
            create_execution_attempt(
                conn,
                {"attempt_id": "attempt-dead", "task_id": "task-heartbeat", "task_key": "key-heartbeat", "batch_id": "batch-heartbeat"},
            )
            update_execution_attempt(conn, "attempt-dead", {"updated_at": stale})
            create_outbox_event(conn, "event-pending", "dispatch_batch", "batch-heartbeat", {"batch_id": "batch-heartbeat"})
            before = reliability_status(conn)
            self.assertEqual(before["tasks"]["expired_leases"], 1)
            self.assertEqual(before["attempts"]["open"], 1)
            self.assertEqual(before["outbox"]["pending"], 1)

        stats = reconcile_once(self.db_path, stale_after_seconds=60)
        self.assertEqual(stats["leases_requeued"], 1)
        self.assertEqual(stats["attempts_uncertain"], 1)
        with get_conn(self.db_path) as conn:
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["status"], "queued")
            after = reliability_status(conn)
            self.assertEqual(after["tasks"]["expired_leases"], 0)
            self.assertEqual(after["attempts"]["uncertain"], 1)
            self.assertIn("__reconciler__", after["workers"]["queues"])

    def test_expired_lease_is_requeued_and_dispatched_in_same_reconcile(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with get_conn(self.db_path) as conn:
            update_sampling_task(
                conn,
                "task-heartbeat",
                {"status": "running", "rq_job_id": "dead-job", "lease_owner": "dead", "lease_expires_at": expired},
            )

        dispatched: list[str] = []

        def dispatch(event, batch) -> None:
            self.assertEqual(event["event_type"], "dispatch_sampling_task")
            self.assertEqual(batch["batch_id"], "batch-heartbeat")
            task_id = event["payload"]["task_id"]
            dispatched.append(task_id)
            with get_conn(self.db_path) as conn:
                update_sampling_task(conn, task_id, {"rq_job_id": "replacement-job", "updated_at": utc_now()})

        stats = reconcile_once(self.db_path, dispatch)
        self.assertEqual(stats["leases_requeued"], 1)
        self.assertEqual(stats["task_events_created"], 1)
        self.assertEqual(dispatched, ["task-heartbeat"])
        with get_conn(self.db_path) as conn:
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["rq_job_id"], "replacement-job")
            self.assertEqual(list_pending_outbox(conn), [])

    def test_reconciler_does_not_dispatch_paused_batch_tasks(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with get_conn(self.db_path) as conn:
            update_sampling_task(
                conn, "task-heartbeat",
                {"status": "running", "lease_owner": "dead", "lease_expires_at": expired},
            )
            conn.execute("UPDATE sampling_batches SET status = 'paused' WHERE batch_id = ?", ("batch-heartbeat",))
        dispatched: list[str] = []
        stats = reconcile_once(self.db_path, lambda event, batch: dispatched.append(event["event_id"]))
        self.assertEqual(stats["leases_requeued"], 0)
        self.assertEqual(stats["task_events_created"], 0)
        self.assertEqual(dispatched, [])

    def test_reconciler_completes_stuck_running_batch_from_terminal_tasks(self) -> None:
        with get_conn(self.db_path) as conn:
            update_sampling_task(conn, "task-heartbeat", {"status": "failed", "finished_at": utc_now()})
            conn.execute(
                "UPDATE sampling_batches SET status = 'running', total_count = 1, completed_count = 0 WHERE batch_id = ?",
                ("batch-heartbeat",),
            )

        stats = reconcile_once(self.db_path)

        self.assertEqual(stats["batches_completed"], 1)
        with get_conn(self.db_path) as conn:
            batch = conn.execute(
                "SELECT status, completed_count, failed_count, finished_at FROM sampling_batches WHERE batch_id = ?",
                ("batch-heartbeat",),
            ).fetchone()
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["completed_count"], 1)
        self.assertEqual(batch["failed_count"], 1)
        self.assertTrue(batch["finished_at"])

    def test_reconciler_never_dispatches_web_tasks_to_api_worker(self) -> None:
        with get_conn(self.db_path) as conn:
            web_model_id = int(next(item for item in list_model_configs(conn) if item["provider"] == "deepseek_web")["id"])
            conn.execute(
                "UPDATE sampling_tasks SET task_id = 'dsw-task', task_key = 'web-key', model_config_id = ? WHERE task_id = ?",
                (web_model_id, "task-heartbeat"),
            )

        dispatched: list[str] = []
        stats = reconcile_once(self.db_path, lambda event, batch: dispatched.append(event["event_id"]))

        self.assertEqual(stats["task_events_created"], 0)
        self.assertEqual(dispatched, [])

    def test_task_rq_failure_requeues_real_batch_and_is_idempotent(self) -> None:
        class FailedJob:
            id = "rq-job-1"
            args = ("task-heartbeat",)
            timeout = 30

        with get_conn(self.db_path) as conn:
            update_sampling_task(
                conn, "task-heartbeat",
                {"status": "running", "rq_job_id": "rq-job-1", "lease_owner": "worker", "lease_expires_at": utc_now()},
            )
        mark_rq_job_failed(FailedJob(), exc_value=RuntimeError("worker crashed"), db_target=self.db_path)
        mark_rq_job_failed(FailedJob(), exc_value=RuntimeError("worker crashed"), db_target=self.db_path)
        with get_conn(self.db_path) as conn:
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["rq_job_id"], "")
            self.assertEqual(task["lease_owner"], "")
            self.assertIn("worker crashed", task["error_message"])
            events = conn.execute(
                "SELECT * FROM dispatch_outbox WHERE aggregate_id = ? ORDER BY id",
                ("task-heartbeat",),
            ).fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["aggregate_id"], "task-heartbeat")
            batch = conn.execute("SELECT status FROM sampling_batches WHERE batch_id = ?", ("batch-heartbeat",)).fetchone()
            self.assertEqual(batch["status"], "queued")

    def test_provider_slot_backpressure_requeues_without_failing_task(self) -> None:
        with get_conn(self.db_path) as conn:
            create_execution_attempt(
                conn,
                {
                    "attempt_id": "attempt-slot-busy", "task_id": "task-heartbeat",
                    "task_key": "key-heartbeat", "batch_id": "batch-heartbeat",
                },
            )
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "slot-worker", 60))
            self.assertTrue(
                defer_sampling_task_for_slot(
                    conn,
                    task_id="task-heartbeat",
                    batch_id="batch-heartbeat",
                    lease_owner="slot-worker",
                    attempt_id="attempt-slot-busy",
                    message="等待信息源全局并发槽超时：deepseek",
                )
            )
            task = get_sampling_task(conn, "task-heartbeat")
            attempt = conn.execute(
                "SELECT status, error_code FROM execution_attempts WHERE attempt_id = ?",
                ("attempt-slot-busy",),
            ).fetchone()
            events = conn.execute(
                "SELECT * FROM dispatch_outbox WHERE aggregate_id = ? ORDER BY id",
                ("task-heartbeat",),
            ).fetchall()

        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["error_code"], "provider_slot_busy")
        self.assertEqual(attempt["status"], "deferred")
        self.assertEqual(attempt["error_code"], "provider_slot_busy")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "dispatch_sampling_task")
        self.assertGreater(events[0]["available_at"], events[0]["created_at"])

    def test_rerun_queue_clears_stale_running_task_and_is_idempotent(self) -> None:
        with get_conn(self.db_path) as conn:
            create_execution_attempt(
                conn,
                {
                    "attempt_id": "attempt-rerun", "task_id": "task-heartbeat",
                    "task_key": "key-heartbeat", "batch_id": "batch-heartbeat",
                    "run_id": "run-failed",
                },
            )
            update_sampling_task(
                conn,
                "task-heartbeat",
                {
                    "status": "running", "rq_job_id": "stale-job", "lease_owner": "dead-worker",
                    "lease_expires_at": utc_now(),
                },
            )
            first = _queue_rerun_tasks(conn, "batch-heartbeat", [{"run_id": "run-failed"}])
            second = _queue_rerun_tasks(conn, "batch-heartbeat", [{"run_id": "run-failed"}])
            task = get_sampling_task(conn, "task-heartbeat")
            events = list_pending_outbox(conn)

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["rq_job_id"], "")
        self.assertEqual(len(events), 1)
        self.assertIn("rerun-run-failed", events[0]["event_id"])

    def test_rerun_queue_recovers_after_prior_dispatch_was_delivered(self) -> None:
        with get_conn(self.db_path) as conn:
            create_execution_attempt(
                conn,
                {
                    "attempt_id": "attempt-rerun-delivered", "task_id": "task-heartbeat",
                    "task_key": "key-heartbeat", "batch_id": "batch-heartbeat",
                    "run_id": "run-failed-delivered",
                },
            )
            _queue_rerun_tasks(conn, "batch-heartbeat", [{"run_id": "run-failed-delivered"}])
            conn.execute(
                "UPDATE dispatch_outbox SET status = 'delivered' WHERE event_id = ?",
                ("dispatch-task:task-heartbeat:rerun-run-failed-delivered",),
            )
            update_sampling_task(conn, "task-heartbeat", {"status": "failed", "attempt_count": 1})

            queued = _queue_rerun_tasks(conn, "batch-heartbeat", [{"run_id": "run-failed-delivered"}])
            events = conn.execute(
                "SELECT event_id, status FROM dispatch_outbox WHERE aggregate_id = ? ORDER BY id",
                ("task-heartbeat",),
            ).fetchall()

        self.assertEqual(queued, 1)
        self.assertEqual([event["status"] for event in events], ["delivered", "pending"])
        self.assertTrue(events[-1]["event_id"].endswith("rerun-run-failed-delivered-2"))

    def test_rerun_queue_refuses_to_steal_active_task_lease(self) -> None:
        with get_conn(self.db_path) as conn:
            create_execution_attempt(
                conn,
                {
                    "attempt_id": "attempt-rerun-active", "task_id": "task-heartbeat",
                    "task_key": "key-heartbeat", "batch_id": "batch-heartbeat",
                    "run_id": "run-failed-active",
                },
            )
            update_sampling_task(
                conn,
                "task-heartbeat",
                {
                    "status": "running", "lease_owner": "live-worker",
                    "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                },
            )

            with self.assertRaisesRegex(ValueError, "拒绝抢占重跑"):
                _queue_rerun_tasks(conn, "batch-heartbeat", [{"run_id": "run-failed-active"}])

            task = get_sampling_task(conn, "task-heartbeat")

        self.assertEqual(task["status"], "running")
        self.assertEqual(task["lease_owner"], "live-worker")

    def test_outbox_claim_is_exclusive_and_failed_claim_uses_backoff(self) -> None:
        with get_conn(self.db_path) as conn:
            create_outbox_event(conn, "event-exclusive", "dispatch_batch", "batch-heartbeat", {"batch_id": "batch-heartbeat"})
            first = claim_pending_outbox(conn, "reconciler-a")
            second = claim_pending_outbox(conn, "reconciler-b")
            self.assertEqual([item["event_id"] for item in first], ["event-exclusive"])
            self.assertEqual(second, [])
            mark_outbox_failed(conn, "event-exclusive", "redis unavailable", "reconciler-a")
            self.assertEqual(list_pending_outbox(conn), [], "backoff 期间不能立即形成重试风暴")

    def test_late_failure_callback_cannot_clear_replacement_job(self) -> None:
        class OldJob:
            id = "old-job"
            args = ("task-heartbeat",)
            timeout = 30

        with get_conn(self.db_path) as conn:
            update_sampling_task(
                conn, "task-heartbeat",
                {"status": "queued", "rq_job_id": "replacement-job", "updated_at": utc_now()},
            )
        mark_rq_job_failed(OldJob(), exc_value=RuntimeError("late callback"), db_target=self.db_path)
        with get_conn(self.db_path) as conn:
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["rq_job_id"], "replacement-job")
            self.assertEqual(list_pending_outbox(conn), [])

    def test_late_rq_failure_after_commit_and_before_ack_is_ignored(self) -> None:
        class AckRaceJob:
            id = "ack-race-job"
            args = ("task-heartbeat",)
            timeout = 30

        with get_conn(self.db_path) as conn:
            self.assertTrue(claim_sampling_task(conn, "task-heartbeat", "worker-committed", 60))
            self.assertTrue(finalize_sampling_task(conn, "task-heartbeat", "worker-committed", status="success"))
        mark_rq_job_failed(AckRaceJob(), exc_value=RuntimeError("worker died before ack"), db_target=self.db_path)
        with get_conn(self.db_path) as conn:
            task = get_sampling_task(conn, "task-heartbeat")
            self.assertEqual(task["status"], "success")
            self.assertEqual(list_pending_outbox(conn), [])

    def test_legacy_batch_rq_failure_still_marks_system_failure(self) -> None:
        class LegacyBatchJob:
            id = "legacy-job"
            args = ("batch-heartbeat",)
            timeout = 30

        mark_rq_job_failed(LegacyBatchJob(), exc_value=RuntimeError("legacy crash"), db_target=self.db_path)
        with get_conn(self.db_path) as conn:
            batch = conn.execute(
                "SELECT status, error_message FROM sampling_batches WHERE batch_id = ?", ("batch-heartbeat",)
            ).fetchone()
            self.assertEqual(batch["status"], "failed_system")
            self.assertIn("legacy crash", batch["error_message"])


if __name__ == "__main__":
    unittest.main()
