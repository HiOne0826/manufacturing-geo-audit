from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.db import init_db
from src.migrations import MIGRATIONS, Migration, MigrationRunner, validate_migrations


ROOT = Path(__file__).resolve().parents[1]


class MigrationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "migrations.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_status_does_not_create_tracking_table(self) -> None:
        status = MigrationRunner(self.conn, "sqlite").status()
        self.assertEqual(status["current_version"], 0)
        self.assertEqual([item["version"] for item in status["pending"]], [1, 2, 3, 4, 5, 6])
        table = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        self.assertIsNone(table)

    def test_apply_records_version_and_is_idempotent(self) -> None:
        runner = MigrationRunner(self.conn, "sqlite")
        first = runner.apply()
        second = runner.apply()
        self.assertEqual([item["version"] for item in first["applied"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(second["applied"], [])
        self.assertTrue(second["status"]["up_to_date"])
        count = self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(count, 6)
        delivery_tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('run_review_events', 'report_versions', 'operation_audit_log')"
            )
        }
        self.assertEqual(delivery_tables, {"run_review_events", "report_versions", "operation_audit_log"})
        health_tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('provider_health_scopes', 'provider_health_events')"
            )
        }
        self.assertEqual(health_tables, {"provider_health_scopes", "provider_health_events"})

    def test_dry_run_returns_sql_without_writes(self) -> None:
        result = MigrationRunner(self.conn, "sqlite").apply(dry_run=True)
        self.assertEqual(result["plan"][0]["statements"], ["SELECT 1"])
        self.assertEqual(result["applied"], [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone())

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        broken = Migration(7, "broken", ("CREATE TABLE transient_table (id INTEGER)", "INVALID SQL"), ("SELECT 1",))
        runner = MigrationRunner(self.conn, "sqlite", (*MIGRATIONS, broken))
        with self.assertRaises(sqlite3.Error):
            runner.apply()
        names = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("transient_table", names)
        self.assertNotIn("schema_migrations", names)

    def test_checksum_drift_blocks_apply(self) -> None:
        MigrationRunner(self.conn, "sqlite").apply()
        self.conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        self.conn.commit()
        runner = MigrationRunner(self.conn, "sqlite")
        self.assertEqual(runner.status()["drift"][0]["reason"], "checksum_mismatch")
        with self.assertRaises(RuntimeError):
            runner.apply()

    def test_registry_validation_rejects_duplicate_and_unsorted_versions(self) -> None:
        one = Migration(1, "one", ("SELECT 1",), ("SELECT 1",))
        duplicate = Migration(1, "two", ("SELECT 1",), ("SELECT 1",))
        two = Migration(2, "two", ("SELECT 1",), ("SELECT 1",))
        with self.assertRaises(ValueError):
            validate_migrations((one, duplicate))
        with self.assertRaises(ValueError):
            validate_migrations((two, one))

    def test_postgres_sql_interface_is_available_without_driver_or_server(self) -> None:
        migration = MIGRATIONS[1]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS report_versions" in sql for sql in migration.statements("postgres")))
        self.assertIn("%s", MigrationRunner(self.conn, "postgres")._markers(2))

    def test_legacy_duplicate_active_batches_are_preserved_and_reconciled_idempotently(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                brand_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE sampling_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO projects (client_name, brand_name, created_at)
            VALUES ('legacy-client', 'legacy-brand', '2026-01-01T00:00:00Z');
            INSERT INTO sampling_batches
                (batch_id, project_id, status, error_message, created_at, updated_at)
            VALUES
                ('old-running', 1, 'running', 'original diagnostic', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'),
                ('new-paused', 1, 'paused', '', '2026-01-03T00:00:00Z', '2026-01-04T00:00:00Z');
            """
        )
        self.conn.commit()
        self.conn.close()

        init_db(self.db_path)
        init_db(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT batch_id, status, error_message, finished_at FROM sampling_batches ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "failed_system")
        self.assertIn("original diagnostic", rows[0]["error_message"])
        self.assertEqual(rows[0]["error_message"].count("V2 migration: superseded duplicate active batch"), 1)
        self.assertTrue(rows[0]["finished_at"])
        self.assertEqual(rows[1]["status"], "paused")
        active_count = self.conn.execute(
            """
            SELECT COUNT(*) FROM sampling_batches
            WHERE project_id = 1
              AND status IN ('queued', 'running', 'pause_requested', 'paused')
              AND archived_at IS NULL
            """
        ).fetchone()[0]
        self.assertEqual(active_count, 1)
        migration_count = self.conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 5"
        ).fetchone()[0]
        self.assertEqual(migration_count, 1)

    def test_v5_direct_migration_adds_archive_column_and_uses_id_tie_breaker(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE sampling_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO sampling_batches
                (batch_id, project_id, status, created_at, updated_at)
            VALUES
                ('tie-low-id', 9, 'queued', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'),
                ('tie-high-id', 9, 'running', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z');
            """
        )
        self.conn.commit()

        runner = MigrationRunner(self.conn, "sqlite", (MIGRATIONS[4],))
        first = runner.apply()
        second = runner.apply()

        self.assertEqual([item["version"] for item in first["applied"]], [5])
        self.assertEqual(second["applied"], [])
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(sampling_batches)")}
        self.assertIn("archived_at", columns)
        rows = self.conn.execute(
            "SELECT batch_id, status FROM sampling_batches ORDER BY id"
        ).fetchall()
        self.assertEqual([(row["batch_id"], row["status"]) for row in rows], [
            ("tie-low-id", "failed_system"),
            ("tie-high-id", "running"),
        ])


class MigrationCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "migrate.py"), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_apply_and_check_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cli.db"
            missing = self.run_cli("--check", "--db", str(path))
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(path.exists())

            dry = self.run_cli("--apply", "--dry-run", "--db", str(path))
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(dry.stdout)["plan"][0]["version"], 1)

            applied = self.run_cli("--apply", "--db", str(path))
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue(path.exists())
            checked = self.run_cli("--check", "--db", str(path))
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertTrue(json.loads(checked.stdout)["up_to_date"])


if __name__ == "__main__":
    unittest.main()
