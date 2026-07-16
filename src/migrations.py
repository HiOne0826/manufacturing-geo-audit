from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from src.delivery import POSTGRES_DDL as TRUSTED_DELIVERY_POSTGRES_DDL
from src.delivery import SQLITE_DDL as TRUSTED_DELIVERY_SQLITE_DDL


Dialect = Literal["sqlite", "postgres"]


@dataclass(frozen=True, order=True)
class Migration:
    version: int
    name: str
    sqlite_sql: tuple[str, ...]
    postgres_sql: tuple[str, ...]

    def statements(self, dialect: Dialect) -> tuple[str, ...]:
        return self.sqlite_sql if dialect == "sqlite" else self.postgres_sql

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            {
                "version": self.version,
                "name": self.name,
                "sqlite": self.sqlite_sql,
                "postgres": self.postgres_sql,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_DELIVERY_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "002_trusted_delivery.sql"
PROVIDER_HEALTH_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "003_provider_health.sql"
TASK_SNAPSHOT_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "004_task_snapshot.sql"
ACTIVE_BATCH_RECONCILIATION_POSTGRES_PATH = (
    ROOT / "deploy" / "postgres" / "migrations" / "005_active_batch_conflict_reconciliation.sql"
)
RELIABILITY_GUARDS_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "006_reliability_concurrency_guards.sql"
SCHEMA_CONTRACT_COMPAT_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "007_schema_contract_compat.sql"
PROVIDER_HEALTH_TYPE_ALIGNMENT_POSTGRES_PATH = ROOT / "deploy" / "postgres" / "migrations" / "008_provider_health_type_alignment.sql"

RELIABILITY_GUARDS_SQLITE = """
ALTER TABLE dispatch_outbox ADD COLUMN claim_token TEXT DEFAULT '';
ALTER TABLE dispatch_outbox ADD COLUMN claim_expires_at TEXT;
UPDATE execution_attempts
SET attempt_no = (
    SELECT COUNT(*) FROM execution_attempts AS earlier
    WHERE earlier.batch_id = execution_attempts.batch_id
      AND earlier.task_key = execution_attempts.task_key
      AND (earlier.attempt_no < execution_attempts.attempt_no
           OR (earlier.attempt_no = execution_attempts.attempt_no AND earlier.id <= execution_attempts.id))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_attempts_sequence
ON execution_attempts(batch_id, task_key, attempt_no);
"""

ACTIVE_BATCH_RECONCILIATION_SQLITE = """
ALTER TABLE sampling_batches ADD COLUMN archived_at TEXT;
UPDATE sampling_batches AS stale
SET status = 'failed_system',
    error_message = CASE
        WHEN TRIM(COALESCE(stale.error_message, '')) = ''
            THEN 'V2 migration: superseded duplicate active batch'
        ELSE stale.error_message || CHAR(10) || 'V2 migration: superseded duplicate active batch'
    END,
    finished_at = COALESCE(stale.finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE stale.status IN ('queued', 'running', 'pause_requested', 'paused')
  AND stale.archived_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM sampling_batches AS newer
      WHERE newer.project_id = stale.project_id
        AND newer.status IN ('queued', 'running', 'pause_requested', 'paused')
        AND newer.archived_at IS NULL
        AND (
            COALESCE(newer.updated_at, '') > COALESCE(stale.updated_at, '')
            OR (
                COALESCE(newer.updated_at, '') = COALESCE(stale.updated_at, '')
                AND newer.id > stale.id
            )
        )
  );
CREATE UNIQUE INDEX IF NOT EXISTS idx_sampling_batches_one_active_project
ON sampling_batches(project_id)
WHERE status IN ('queued', 'running', 'pause_requested', 'paused') AND archived_at IS NULL;
"""

PROVIDER_HEALTH_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS provider_health_scopes (
    health_key TEXT PRIMARY KEY,
    endpoint TEXT DEFAULT '',
    credential_fingerprint TEXT DEFAULT 'unconfigured',
    exit_region TEXT DEFAULT '',
    scope_json TEXT DEFAULT '{}',
    half_open_trial_until TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_health_events (
    event_id TEXT PRIMARY KEY,
    health_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT DEFAULT '',
    mode TEXT DEFAULT 'pure',
    ok INTEGER NOT NULL,
    error_code TEXT DEFAULT '',
    latency_ms INTEGER DEFAULT 0,
    source TEXT DEFAULT 'passive',
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_health_events_window
ON provider_health_events(health_key, observed_at);
"""


def _split_sql(sql: str) -> tuple[str, ...]:
    """Split the additive DDL used by our DB adapters into runner statements."""
    return tuple(statement.strip() for statement in sql.split(";") if statement.strip())


# Version 1 marks the point at which schema changes became migration-managed.
# Existing init_db/schema.sql remain the bootstrap source until their DDL is
# incrementally moved into later migrations.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="migration_framework_baseline",
        sqlite_sql=("SELECT 1",),
        postgres_sql=("SELECT 1",),
    ),
    Migration(
        version=2,
        name="trusted_delivery",
        sqlite_sql=_split_sql(TRUSTED_DELIVERY_SQLITE_DDL),
        postgres_sql=_split_sql(
            TRUSTED_DELIVERY_POSTGRES_PATH.read_text(encoding="utf-8")
            if TRUSTED_DELIVERY_POSTGRES_PATH.exists()
            else TRUSTED_DELIVERY_POSTGRES_DDL
        ),
    ),
    Migration(
        version=3,
        name="provider_health_scopes_and_events",
        sqlite_sql=_split_sql(PROVIDER_HEALTH_SQLITE_DDL),
        postgres_sql=_split_sql(PROVIDER_HEALTH_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
    Migration(
        version=4,
        name="immutable_task_snapshot",
        sqlite_sql=("ALTER TABLE sampling_tasks ADD COLUMN task_snapshot_json TEXT DEFAULT '{}'",),
        postgres_sql=_split_sql(TASK_SNAPSHOT_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
    Migration(
        version=5,
        name="active_batch_conflict_reconciliation",
        sqlite_sql=_split_sql(ACTIVE_BATCH_RECONCILIATION_SQLITE),
        postgres_sql=_split_sql(ACTIVE_BATCH_RECONCILIATION_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
    Migration(
        version=6,
        name="reliability_concurrency_guards",
        sqlite_sql=_split_sql(RELIABILITY_GUARDS_SQLITE),
        postgres_sql=_split_sql(RELIABILITY_GUARDS_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
    Migration(
        version=7,
        name="postgres_schema_contract_compat",
        sqlite_sql=("SELECT 1",),
        postgres_sql=_split_sql(SCHEMA_CONTRACT_COMPAT_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
    Migration(
        version=8,
        name="provider_health_type_alignment",
        sqlite_sql=("SELECT 1",),
        postgres_sql=_split_sql(PROVIDER_HEALTH_TYPE_ALIGNMENT_POSTGRES_PATH.read_text(encoding="utf-8")),
    ),
)


def validate_migrations(migrations: Sequence[Migration]) -> None:
    versions = [item.version for item in migrations]
    if any(version < 1 for version in versions):
        raise ValueError("migration version 必须从 1 开始")
    if versions != sorted(versions):
        raise ValueError("migrations 必须按 version 升序排列")
    if len(set(versions)) != len(versions):
        raise ValueError("migration version 不能重复")
    names = [item.name for item in migrations]
    if len(set(names)) != len(names):
        raise ValueError("migration name 不能重复")
    for item in migrations:
        if not item.name.strip():
            raise ValueError(f"migration {item.version} 缺少名称")
        if not item.sqlite_sql or not item.postgres_sql:
            raise ValueError(f"migration {item.version} 必须同时声明 SQLite 和 PostgreSQL SQL")


class MigrationRunner:
    def __init__(self, conn: Any, dialect: Dialect, migrations: Iterable[Migration] = MIGRATIONS):
        if dialect not in {"sqlite", "postgres"}:
            raise ValueError(f"不支持的数据库方言：{dialect}")
        self.conn = conn
        self.dialect = dialect
        self.migrations = tuple(migrations)
        validate_migrations(self.migrations)

    def status(self) -> dict[str, Any]:
        applied_rows = self._applied_rows() if self._table_exists() else []
        applied = {int(row["version"]): row for row in applied_rows}
        known = {item.version: item for item in self.migrations}
        drift: list[dict[str, Any]] = []
        for version, row in applied.items():
            migration = known.get(version)
            if migration is None:
                drift.append({"version": version, "reason": "unknown_applied_version"})
            elif str(row.get("checksum") or "") != migration.checksum:
                drift.append({"version": version, "reason": "checksum_mismatch"})
        pending = [self._migration_info(item) for item in self.migrations if item.version not in applied]
        current_version = max(applied, default=0)
        target_version = max(known, default=0)
        return {
            "dialect": self.dialect,
            "current_version": current_version,
            "target_version": target_version,
            "applied": [dict(row) for row in applied_rows],
            "pending": pending,
            "drift": drift,
            "up_to_date": not pending and not drift,
            "ok": not drift,
        }

    def apply(self, *, dry_run: bool = False) -> dict[str, Any]:
        before = self.status()
        if before["drift"]:
            raise RuntimeError("schema_migrations 存在版本漂移，拒绝继续 apply")
        pending_versions = {int(item["version"]) for item in before["pending"]}
        pending = [item for item in self.migrations if item.version in pending_versions]
        plan = [
            {**self._migration_info(item), "statements": list(item.statements(self.dialect))}
            for item in pending
        ]
        if dry_run:
            return {"dry_run": True, "applied": [], "plan": plan, "status": before}
        if not pending and self._table_exists():
            return {"dry_run": False, "applied": [], "plan": [], "status": before}

        applied: list[dict[str, Any]] = []
        try:
            self._begin()
            self._ensure_table()
            # Re-read while holding the write transaction so concurrent runners
            # cannot apply the same version twice.
            recorded = {int(row["version"]) for row in self._applied_rows()}
            for item in self.migrations:
                if item.version in recorded:
                    continue
                for statement in item.statements(self.dialect):
                    self._execute_statement(statement)
                self.conn.execute(
                    f"INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES ({self._markers(4)})",
                    (item.version, item.name, item.checksum, _utc_now()),
                )
                applied.append(self._migration_info(item))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {"dry_run": False, "applied": applied, "plan": plan, "status": self.status()}

    def _execute_statement(self, statement: str) -> None:
        if self.dialect == "sqlite" and (
            statement.startswith("ALTER TABLE sampling_batches ADD COLUMN archived_at")
            or
            statement.startswith("UPDATE sampling_batches AS stale")
            or statement.startswith("CREATE UNIQUE INDEX IF NOT EXISTS idx_sampling_batches_one_active_project")
        ):
            table = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sampling_batches'"
            ).fetchone()
            if not table:
                return
            if statement.startswith("ALTER TABLE sampling_batches ADD COLUMN archived_at"):
                columns = {row[1] for row in self.conn.execute("PRAGMA table_info(sampling_batches)").fetchall()}
                if "archived_at" in columns:
                    return
        if self.dialect == "sqlite" and statement.startswith("ALTER TABLE sampling_tasks ADD COLUMN task_snapshot_json"):
            table = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sampling_tasks'"
            ).fetchone()
            if not table:
                return
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(sampling_tasks)").fetchall()}
            if "task_snapshot_json" in columns:
                return
        if self.dialect == "sqlite" and statement.startswith("ALTER TABLE dispatch_outbox ADD COLUMN"):
            table = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dispatch_outbox'"
            ).fetchone()
            if not table:
                return
            column = statement.split("ADD COLUMN", 1)[1].strip().split()[0]
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(dispatch_outbox)").fetchall()}
            if column in columns:
                return
        if self.dialect == "sqlite" and (
            statement.startswith("UPDATE execution_attempts")
            or statement.startswith("CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_attempts_sequence")
        ):
            table = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'execution_attempts'"
            ).fetchone()
            if not table:
                return
        self.conn.execute(statement)

    def _migration_info(self, item: Migration) -> dict[str, Any]:
        return {"version": item.version, "name": item.name, "checksum": item.checksum}

    def _begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite" else "BEGIN")

    def _markers(self, count: int) -> str:
        marker = "?" if self.dialect == "sqlite" else "%s"
        return ", ".join(marker for _ in range(count))

    def _table_exists(self) -> bool:
        if self.dialect == "sqlite":
            row = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'schema_migrations'"
            ).fetchone()
        return row is not None

    def _ensure_table(self) -> None:
        version_type = "INTEGER" if self.dialect == "sqlite" else "BIGINT"
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version {version_type} PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    def _applied_rows(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [_row_dict(row) for row in rows]


def connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres(dsn: str):
    """Lazy PostgreSQL adapter; importing this module never requires psycopg."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("PostgreSQL migration 需要安装 psycopg") from exc
    return psycopg.connect(dsn, row_factory=dict_row)


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {"version": row[0], "name": row[1], "checksum": row[2], "applied_at": row[3]}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
