from __future__ import annotations

import sqlite3
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import readiness_status

from scripts.audit_consistency import audit_batch_count_drift
from src.db import (
    claim_sampling_task,
    create_execution_attempt,
    create_outbox_event,
    create_project,
    create_sampling_batch,
    create_sampling_tasks,
    get_conn,
    get_sampling_task,
    import_questions_text,
    init_db,
    insert_run,
    list_execution_attempts,
    list_model_configs,
    list_pending_outbox,
    list_questions,
    list_runs_by_batch,
    mark_outbox_delivered,
    mark_outbox_failed,
    next_queued_sampling_task,
    sampling_task_counts,
    update_execution_attempt,
    update_sampling_batch,
    update_sampling_task,
    utc_now,
)
from src.reliability import ErrorCode, assert_batch_transition, classify_error


class V2FaultAcceptanceTests(unittest.TestCase):
    """Offline fault contracts for the durable V2 execution ledger."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "faults.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            self.project_id = create_project(conn, {"client_name": "故障测试客户", "brand_name": "故障测试品牌"})
            import_questions_text(conn, self.project_id, "故障注入问题")
            self.question_id = list_questions(conn, self.project_id)[0]["id"]
            self.model_id = list_model_configs(conn)[0]["id"]
            create_sampling_batch(
                conn,
                {
                    "batch_id": "batch-faults",
                    "project_id": self.project_id,
                    "status": "queued",
                    "total_count": 1,
                    "config": {},
                },
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def task(self, *, task_id: str = "task-1", task_key: str = "logical-task-1", status: str = "queued") -> dict:
        return {
            "task_id": task_id,
            "task_key": task_key,
            "batch_id": "batch-faults",
            "project_id": self.project_id,
            "question_id": self.question_id,
            "model_config_id": self.model_id,
            "repeat_index": 1,
            "status": status,
        }

    def run_payload(self, run_id: str, **overrides) -> dict:
        payload = {
            "run_id": run_id,
            "batch_id": "batch-faults",
            "project_id": self.project_id,
            "question_id": self.question_id,
            "model_config_id": self.model_id,
            "provider": "offline",
            "model": "offline-model",
            "search_enabled": False,
            "search_mode": "off",
            "thinking_type": "disabled",
            "reasoning_effort": "",
            "thinking_budget": None,
            "repeat_index": 1,
            "requested_at": utc_now(),
            "status": "success",
            "response_text": "ok",
        }
        payload.update(overrides)
        return payload

    def test_duplicate_dispatch_and_claim_are_idempotent(self) -> None:
        with get_conn(self.db_path) as conn:
            self.assertEqual(create_sampling_tasks(conn, [self.task()]), 1)
            # A replayed dispatcher cannot duplicate the logical task, even with a new task id.
            self.assertEqual(create_sampling_tasks(conn, [self.task(task_id="task-replay")]), 0)
            self.assertEqual(sampling_task_counts(conn, "batch-faults")["total"], 1)

            self.assertTrue(claim_sampling_task(conn, "task-1", "worker-a", 60))
            self.assertFalse(claim_sampling_task(conn, "task-1", "worker-b", 60))
            claimed = get_sampling_task(conn, "task-1")
            self.assertEqual(claimed["lease_owner"], "worker-a")
            self.assertEqual(claimed["attempt_count"], 1)

    def test_expired_lease_is_visible_and_can_be_taken_over_once(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with get_conn(self.db_path) as conn:
            create_sampling_tasks(conn, [self.task(status="running")])
            update_sampling_task(conn, "task-1", {"lease_owner": "dead-worker", "lease_expires_at": expired})
            self.assertEqual(next_queued_sampling_task(conn, "batch-faults")["task_id"], "task-1")
            self.assertTrue(claim_sampling_task(conn, "task-1", "recovery-worker", 60))
            self.assertFalse(claim_sampling_task(conn, "task-1", "late-worker", 60))
            recovered = get_sampling_task(conn, "task-1")
            self.assertEqual(recovered["lease_owner"], "recovery-worker")
            self.assertEqual(recovered["attempt_count"], 1)

    def test_pause_resume_interleavings_follow_state_machine(self) -> None:
        # Pause may either settle after in-flight work or be cancelled before settlement.
        assert_batch_transition("running", "pause_requested")
        assert_batch_transition("pause_requested", "paused")
        assert_batch_transition("paused", "running")
        assert_batch_transition("pause_requested", "running")
        assert_batch_transition("pause_requested", "completed")
        with self.assertRaises(ValueError):
            assert_batch_transition("paused", "pause_requested")
        with self.assertRaises(ValueError):
            assert_batch_transition("cancelled", "running")

    def test_outbox_retries_without_losing_or_duplicating_event(self) -> None:
        with get_conn(self.db_path) as conn:
            create_outbox_event(conn, "event-1", "dispatch_batch", "batch-faults", {"batch_id": "batch-faults"})
            with self.assertRaises(sqlite3.IntegrityError):
                create_outbox_event(conn, "event-1", "dispatch_batch", "batch-faults", {"batch_id": "batch-faults"})
            mark_outbox_failed(conn, "event-1", "redis unavailable")
            conn.execute("UPDATE dispatch_outbox SET available_at = ? WHERE event_id = ?", (utc_now(), "event-1"))
            pending = list_pending_outbox(conn)
            self.assertEqual([row["event_id"] for row in pending], ["event-1"])
            self.assertEqual(pending[0]["attempt_count"], 1)
            self.assertIn("redis unavailable", pending[0]["last_error"])
            mark_outbox_delivered(conn, "event-1")
            self.assertEqual(list_pending_outbox(conn), [])
            row = conn.execute("SELECT * FROM dispatch_outbox WHERE event_id = ?", ("event-1",)).fetchone()
            self.assertEqual(row["status"], "delivered")
            self.assertEqual(row["attempt_count"], 2)

    def test_uncertain_attempt_is_append_only_and_followed_by_new_attempt(self) -> None:
        with get_conn(self.db_path) as conn:
            create_execution_attempt(
                conn,
                {"attempt_id": "attempt-1", "task_id": "task-1", "task_key": "logical-task-1", "batch_id": "batch-faults"},
            )
            update_execution_attempt(
                conn,
                "attempt-1",
                {"status": "uncertain", "response_received": True, "persistence_committed": False, "error_code": "persistence_unknown"},
            )
            create_execution_attempt(
                conn,
                {"attempt_id": "attempt-2", "task_id": "task-1", "task_key": "logical-task-1", "batch_id": "batch-faults"},
            )
            update_execution_attempt(conn, "attempt-2", {"status": "succeeded", "response_received": True, "persistence_committed": True})
            attempts = sorted(list_execution_attempts(conn, "batch-faults"), key=lambda item: item["attempt_no"])
            self.assertEqual([item["attempt_no"] for item in attempts], [1, 2])
            self.assertEqual(attempts[0]["status"], "uncertain")
            self.assertEqual(attempts[0]["response_received"], 1)
            self.assertEqual(attempts[0]["persistence_committed"], 0)
            self.assertEqual(attempts[1]["status"], "succeeded")

    def test_only_one_current_run_exists_for_a_logical_key(self) -> None:
        with get_conn(self.db_path) as conn:
            insert_run(conn, self.run_payload("run-old", status="failed", error_message="timeout"))
            insert_run(conn, self.run_payload("run-new"))
            history = list_runs_by_batch(conn, "batch-faults", include_history=True)
            current = list_runs_by_batch(conn, "batch-faults")
            self.assertEqual(len(history), 2)
            self.assertEqual([row["run_id"] for row in current], ["run-new"])
            old = next(row for row in history if row["run_id"] == "run-old")
            self.assertEqual(old["is_current"], 0)
            self.assertTrue(old["superseded_at"])

            # The partial unique index is the final guard if an unsafe writer bypasses insert_run.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO model_runs (
                        run_id, batch_id, project_id, question_id, model_config_id, provider, model,
                        search_enabled, repeat_index, requested_at, status, search_mode,
                        thinking_type, reasoning_effort, thinking_budget, is_current
                    ) SELECT ?, batch_id, project_id, question_id, model_config_id, provider, model,
                             search_enabled, repeat_index, requested_at, status, search_mode,
                             thinking_type, reasoning_effort, thinking_budget, 1
                      FROM model_runs WHERE run_id = ?
                    """,
                    ("run-unsafe-duplicate", "run-new"),
                )

    def test_error_taxonomy_covers_operational_failure_classes(self) -> None:
        cases = {
            "HTTP 401 invalid api key": ErrorCode.AUTH,
            "HTTP 429 too many requests": ErrorCode.RATE_LIMIT,
            "request timed out": ErrorCode.TIMEOUT,
            "TLS connection failed": ErrorCode.NETWORK,
            "region unsupported blocked": ErrorCode.REGION_BLOCK,
            "model_not_found": ErrorCode.MODEL_NOT_FOUND,
            "Bocha web search unavailable": ErrorCode.SEARCH_DEPENDENCY,
            "invalid JSON response": ErrorCode.MALFORMED_RESPONSE,
            "HTTP 503 service unavailable": ErrorCode.UPSTREAM,
            "unclassified provider failure": ErrorCode.UNKNOWN,
        }
        self.assertEqual({text: classify_error(text).code for text in cases}, cases)

    def test_batch_count_invariant_and_drift_audit(self) -> None:
        tasks = [
            self.task(task_id="task-success", task_key="key-success", status="success"),
            self.task(task_id="task-failed", task_key="key-failed", status="failed"),
            self.task(task_id="task-blocked", task_key="key-blocked", status="blocked"),
            self.task(task_id="task-running", task_key="key-running", status="running"),
            self.task(task_id="task-queued", task_key="key-queued", status="queued"),
        ]
        with get_conn(self.db_path) as conn:
            create_sampling_tasks(conn, tasks)
            counts = sampling_task_counts(conn, "batch-faults")
            self.assertEqual(counts, {"total": 5, "queued": 1, "running": 1, "success": 1, "failed": 1, "blocked": 1, "completed": 3})
            self.assertLessEqual(counts["completed"], counts["total"])
            self.assertEqual(counts["completed"], counts["success"] + counts["failed"] + counts["blocked"])

            drift, warnings = audit_batch_count_drift(conn)
            self.assertEqual(warnings, [])
            self.assertEqual(drift[0]["actual"], {"total": 5, "success": 1, "failed": 2, "completed": 3})
            update_sampling_batch(conn, "batch-faults", {"total_count": 5, "success_count": 1, "failed_count": 2, "completed_count": 3})
            self.assertEqual(audit_batch_count_drift(conn), ([], []))

    def test_readiness_blocks_when_disk_floor_is_not_met(self) -> None:
        with get_conn(self.db_path) as conn, patch("app.shutil.disk_usage", return_value=shutil._ntuple_diskusage(100, 99, 1)), patch.dict("os.environ", {"MIN_FREE_DISK_BYTES": "10"}):
            result, status = readiness_status(conn)
        self.assertEqual(status, 503)
        self.assertFalse(result["checks"]["disk"]["ok"])

    def test_sqlite_lock_is_detected_and_recovers_after_owner_releases(self) -> None:
        owner = sqlite3.connect(self.db_path, timeout=0.05)
        contender = sqlite3.connect(self.db_path, timeout=0.05)
        try:
            owner.execute("BEGIN EXCLUSIVE")
            owner.execute("UPDATE projects SET notes = 'locked' WHERE id = ?", (self.project_id,))
            with self.assertRaises(sqlite3.OperationalError) as raised:
                contender.execute("UPDATE projects SET notes = 'blocked' WHERE id = ?", (self.project_id,))
            self.assertIn("locked", str(raised.exception).lower())
            owner.rollback()
            contender.execute("UPDATE projects SET notes = 'recovered' WHERE id = ?", (self.project_id,))
            contender.commit()
        finally:
            owner.close()
            contender.close()


if __name__ == "__main__":
    unittest.main()
