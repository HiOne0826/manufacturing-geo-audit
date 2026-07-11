from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.db import create_execution_attempt, create_outbox_event, get_conn, init_db, list_pending_outbox, mark_outbox_delivered, update_execution_attempt

from src.reliability import (
    ErrorCode,
    assert_batch_transition,
    batch_outcome,
    classify_error,
    stable_config_fingerprint,
)


class ReliabilityContractTests(unittest.TestCase):
    def test_error_taxonomy_distinguishes_terminal_and_retryable_failures(self):
        auth = classify_error("HTTP 401: invalid api key", 401)
        rate_limit = classify_error("HTTP 429: too many requests", 429)
        self.assertEqual(auth.code, ErrorCode.AUTH)
        self.assertTrue(auth.terminal)
        self.assertFalse(auth.retryable)
        self.assertEqual(rate_limit.code, ErrorCode.RATE_LIMIT)
        self.assertTrue(rate_limit.retryable)

    def test_batch_outcome_separates_partial_failure_from_system_failure(self):
        self.assertEqual(batch_outcome("completed", 9, 1, 10, 10), "partial_failure")
        self.assertEqual(batch_outcome("completed", 10, 0, 10, 10), "clean")
        self.assertEqual(batch_outcome("failed_system", 2, 0, 2, 10), "failure")

    def test_batch_state_machine_rejects_invalid_transition(self):
        assert_batch_transition("running", "pause_requested")
        with self.assertRaises(ValueError):
            assert_batch_transition("completed", "paused")

    def test_config_fingerprint_is_stable_and_ignores_secrets(self):
        first = stable_config_fingerprint({"model": "x", "api_key": "secret-a", "options": {"temperature": 0}})
        second = stable_config_fingerprint({"options": {"temperature": 0}, "api_key": "secret-b", "model": "x"})
        changed = stable_config_fingerprint({"model": "y", "options": {"temperature": 0}})
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_attempt_ledger_and_outbox_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v2.db"
            init_db(db_path)
            with get_conn(db_path) as conn:
                create_execution_attempt(conn, {"attempt_id": "attempt-1", "task_key": "task-key", "batch_id": "batch-1"})
                update_execution_attempt(conn, "attempt-1", {"status": "succeeded", "response_received": True, "persistence_committed": True})
                create_outbox_event(conn, "event-1", "dispatch_batch", "batch-1", {"batch_id": "batch-1"})
                self.assertEqual(len(list_pending_outbox(conn)), 1)
                mark_outbox_delivered(conn, "event-1")
                self.assertEqual(list_pending_outbox(conn), [])
                attempt = conn.execute("SELECT * FROM execution_attempts WHERE attempt_id = ?", ("attempt-1",)).fetchone()
                self.assertEqual(attempt["status"], "succeeded")
                self.assertEqual(attempt["persistence_committed"], 1)


if __name__ == "__main__":
    unittest.main()
