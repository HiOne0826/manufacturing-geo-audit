from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app import api_json_default
from src.adapters import enrich_model_config
from src.db import (
    PostgresConnection,
    _sanitize_postgres_json,
    ensure_sampling_task_dispatch_event,
    list_runs,
    refresh_current_run_flags,
    run_row_to_dict,
)
from src.provider_health import CircuitOpenError, assert_circuit_closed


class _RowsCursor:
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _RecordingConnection:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if sql.lstrip().startswith("SELECT"):
            return _RowsCursor(self.rows)
        return _RowsCursor([])


class DatabaseJsonCompatibilityTests(unittest.TestCase):
    def test_run_row_normalizes_sqlite_text_and_postgres_jsonb(self):
        base = {"provider": "deepseek", "model": "deepseek-v4-flash"}
        sqlite_row = {
            **base,
            "citations_json": json.dumps([{"url": "https://example.com/a"}]),
            "raw_response_json": json.dumps({"id": "sqlite"}),
        }
        postgres_row = {
            **base,
            "citations_json": [{"url": "https://example.com/a"}],
            "raw_response_json": {"id": "postgres"},
        }

        sqlite_result = run_row_to_dict(sqlite_row)
        postgres_result = run_row_to_dict(postgres_row)

        self.assertEqual(json.loads(sqlite_result["citations_json"]), [{"url": "https://example.com/a"}])
        self.assertEqual(json.loads(postgres_result["citations_json"]), [{"url": "https://example.com/a"}])
        self.assertEqual(json.loads(sqlite_result["raw_response_json"]), {"id": "sqlite"})
        self.assertEqual(json.loads(postgres_result["raw_response_json"]), {"id": "postgres"})

    def test_project_run_listing_uses_the_same_normalized_contract(self):
        conn = _RecordingConnection([{
            "id": 1,
            "provider": "hunyuan",
            "model": "hy3",
            "citations_json": [{"url": "https://example.com/source"}],
            "raw_response_json": {"ok": True},
            "import_row_json": {"legacy": True},
        }])

        result = list_runs(conn, 25)

        self.assertEqual(json.loads(result[0]["citations_json"]), [{"url": "https://example.com/source"}])
        self.assertEqual(json.loads(result[0]["raw_response_json"]), {"ok": True})
        self.assertNotIn("import_row_json", result[0])

    def test_refresh_current_flags_clears_postgres_timestamp_with_null(self):
        conn = _RecordingConnection([{
            "id": 9,
            "batch_id": "batch-1",
            "question_id": 1,
            "model_config_id": 2,
            "search_enabled": 1,
            "search_mode": "search",
            "thinking_type": "disabled",
            "reasoning_effort": "",
            "thinking_budget": None,
            "repeat_index": 1,
        }])

        refresh_current_run_flags(conn, "batch-1")

        update_sql = [sql for sql, _ in conn.statements if "SET is_current = 1" in sql][0]
        self.assertIn("superseded_at = NULL", update_sql)
        self.assertNotIn("superseded_at = ''", update_sql)

    def test_postgres_json_sanitizer_removes_nested_nul_without_changing_sqlite_contract(self):
        value = {"bad\x00key": ["a\x00b", {"value": "\x00c"}], "ok": 1}
        self.assertEqual(
            _sanitize_postgres_json(value),
            {"badkey": ["ab", {"value": "c"}], "ok": 1},
        )

    def test_postgres_connection_exposes_rollback_for_migration_failures(self):
        inner = mock.Mock()
        conn = PostgresConnection.__new__(PostgresConnection)
        conn._conn = inner

        conn.rollback()

        inner.rollback.assert_called_once_with()

    def test_dispatch_event_conflict_is_idempotent_without_aborting_transaction(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE dispatch_outbox (
                event_id TEXT PRIMARY KEY, event_type TEXT, aggregate_id TEXT,
                payload_json TEXT, status TEXT, attempt_count INTEGER,
                available_at TEXT, delivered_at TEXT, last_error TEXT,
                created_at TEXT, updated_at TEXT
            )
            """
        )

        self.assertTrue(ensure_sampling_task_dispatch_event(conn, "task-1", "batch-1", 0))
        self.assertFalse(ensure_sampling_task_dispatch_event(conn, "task-1", "batch-1", 0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dispatch_outbox").fetchone()[0], 1)
        conn.close()

    def test_circuit_comparison_accepts_postgres_datetime_and_sqlite_iso_text(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        for open_until in (future, future.isoformat()):
            with self.subTest(value_type=type(open_until).__name__), \
                 mock.patch("src.provider_health.ensure_provider_health_schema"), \
                 mock.patch("src.provider_health.get_health", return_value={
                     "status": "open",
                     "circuit_open_until": open_until,
                     "half_open_trial_until": None,
                 }):
                with self.assertRaises(CircuitOpenError):
                    assert_circuit_closed(mock.Mock(), "deepseek", "deepseek-v4-flash", "search")

    def test_api_contract_normalizes_postgres_datetimes_and_integer_flags(self):
        value = datetime(2026, 7, 14, 0, 12, 14, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(api_json_default(value), "2026-07-14T00:12:14+08:00")
        model = enrich_model_config({
            "provider": "custom",
            "model": "model",
            "supports_pure": 1,
            "supports_search": 0,
            "active": 1,
        })
        self.assertIs(model["supports_pure"], True)
        self.assertIs(model["supports_search"], False)
        self.assertIs(model["active"], True)


if __name__ == "__main__":
    unittest.main()
