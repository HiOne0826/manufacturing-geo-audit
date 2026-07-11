#!/usr/bin/env python3
"""Read-only consistency audit for a manufacturing-geo-audit SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def open_read_only(path: Path) -> sqlite3.Connection:
    # SQLite URI mode=ro prevents the audit itself from creating or changing the DB.
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def audit_stale_batches(
    conn: sqlite3.Connection, cutoff: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    required = {"batch_id", "status", "created_at", "started_at", "updated_at"}
    columns = table_columns(conn, "sampling_batches")
    if not required.issubset(columns):
        return [], ["sampling_batches 缺少僵尸批次检查所需字段"]

    issues: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT batch_id, project_id, status, created_at, started_at, updated_at
        FROM sampling_batches
        WHERE status IN ('queued', 'running', 'pause_requested')
        ORDER BY id
        """
    ):
        last_activity = (
            parse_timestamp(row["updated_at"])
            or parse_timestamp(row["started_at"])
            or parse_timestamp(row["created_at"])
        )
        if last_activity is None or last_activity >= cutoff:
            continue
        issues.append(
            {
                "batch_id": row["batch_id"],
                "project_id": row["project_id"],
                "status": row["status"],
                "last_activity_at": last_activity.isoformat(),
            }
        )
    return issues, []


def audit_batch_count_drift(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[str]]:
    batch_required = {
        "batch_id",
        "total_count",
        "success_count",
        "failed_count",
        "completed_count",
    }
    task_required = {"batch_id", "status"}
    if not batch_required.issubset(table_columns(conn, "sampling_batches")):
        return [], ["sampling_batches 缺少计数漂移检查所需字段"]
    if not task_required.issubset(table_columns(conn, "sampling_tasks")):
        return [], ["sampling_tasks 缺少计数漂移检查所需字段"]

    # sampling_tasks currently exists only for persisted-task providers. A batch with
    # zero task rows may be a valid legacy/API-provider batch and must not be flagged.
    rows = conn.execute(
        """
        SELECT b.batch_id, b.project_id,
               b.total_count AS stored_total,
               b.success_count AS stored_success,
               b.failed_count AS stored_failed,
               b.completed_count AS stored_completed,
               COUNT(t.id) AS actual_total,
               SUM(CASE WHEN t.status = 'success' THEN 1 ELSE 0 END) AS actual_success,
               SUM(CASE WHEN t.status IN ('failed', 'blocked') THEN 1 ELSE 0 END) AS actual_failed,
               SUM(CASE WHEN t.status IN ('success', 'failed', 'blocked') THEN 1 ELSE 0 END)
                   AS actual_completed
        FROM sampling_batches b
        JOIN sampling_tasks t ON t.batch_id = b.batch_id
        GROUP BY b.id, b.batch_id, b.project_id, b.total_count, b.success_count,
                 b.failed_count, b.completed_count
        ORDER BY b.id
        """
    ).fetchall()

    issues: list[dict[str, Any]] = []
    for row in rows:
        stored = {
            "total": int(row["stored_total"] or 0),
            "success": int(row["stored_success"] or 0),
            "failed": int(row["stored_failed"] or 0),
            "completed": int(row["stored_completed"] or 0),
        }
        actual = {
            "total": int(row["actual_total"] or 0),
            "success": int(row["actual_success"] or 0),
            "failed": int(row["actual_failed"] or 0),
            "completed": int(row["actual_completed"] or 0),
        }
        if stored != actual:
            issues.append(
                {
                    "batch_id": row["batch_id"],
                    "project_id": row["project_id"],
                    "stored": stored,
                    "actual": actual,
                }
            )
    return issues, []


def audit_duplicate_current_runs(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[str]]:
    required = {
        "batch_id",
        "question_id",
        "model_config_id",
        "search_enabled",
        "search_mode",
        "thinking_type",
        "reasoning_effort",
        "thinking_budget",
        "repeat_index",
        "is_current",
    }
    if not required.issubset(table_columns(conn, "model_runs")):
        return [], ["model_runs 缺少重复 current 检查所需字段"]

    rows = conn.execute(
        """
        SELECT batch_id, question_id, model_config_id, search_enabled,
               COALESCE(search_mode, '') AS search_mode,
               COALESCE(thinking_type, '') AS thinking_type,
               COALESCE(reasoning_effort, '') AS reasoning_effort,
               COALESCE(thinking_budget, -1) AS thinking_budget,
               COALESCE(repeat_index, 1) AS repeat_index,
               COUNT(*) AS current_count
        FROM model_runs
        WHERE COALESCE(is_current, 1) = 1
        GROUP BY batch_id, question_id, model_config_id, search_enabled,
                 COALESCE(search_mode, ''), COALESCE(thinking_type, ''),
                 COALESCE(reasoning_effort, ''), COALESCE(thinking_budget, -1),
                 COALESCE(repeat_index, 1)
        HAVING COUNT(*) > 1
        ORDER BY batch_id, question_id, model_config_id
        """
    ).fetchall()
    return [dict(row) for row in rows], []


def audit_expired_task_leases(
    conn: sqlite3.Connection, now: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    required = {
        "task_id",
        "batch_id",
        "project_id",
        "status",
        "lease_expires_at",
        "heartbeat_at",
    }
    if not required.issubset(table_columns(conn, "sampling_tasks")):
        return [], ["sampling_tasks 缺少 lease 检查所需字段"]

    issues: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT task_id, batch_id, project_id, status, lease_expires_at, heartbeat_at
        FROM sampling_tasks
        WHERE status = 'running' AND lease_expires_at IS NOT NULL
        ORDER BY id
        """
    ):
        expires_at = parse_timestamp(row["lease_expires_at"])
        if expires_at is None or expires_at >= now:
            continue
        issues.append(
            {
                "task_id": row["task_id"],
                "batch_id": row["batch_id"],
                "project_id": row["project_id"],
                "lease_expires_at": expires_at.isoformat(),
                "heartbeat_at": row["heartbeat_at"],
            }
        )
    return issues, []


def run_audit(db_path: Path, stale_after_minutes: int) -> dict[str, Any]:
    now = utc_now()
    cutoff = now - timedelta(minutes=stale_after_minutes)
    report: dict[str, Any] = {
        "ok": False,
        "database": str(db_path.resolve()),
        "audited_at": now.isoformat(),
        "read_only": True,
        "stale_after_minutes": stale_after_minutes,
        "summary": {},
        "issues": {
            "stale_batches": [],
            "batch_count_drift": [],
            "duplicate_current_runs": [],
            "expired_task_leases": [],
        },
        "warnings": [],
    }

    with open_read_only(db_path) as conn:
        known_tables = {
            name
            for name in ("sampling_batches", "sampling_tasks", "model_runs")
            if table_exists(conn, name)
        }
        missing = sorted({"sampling_batches", "sampling_tasks", "model_runs"} - known_tables)
        if missing:
            report["warnings"].append(f"缺少数据表: {', '.join(missing)}")

        checks = (
            ("stale_batches", audit_stale_batches, (conn, cutoff)),
            ("batch_count_drift", audit_batch_count_drift, (conn,)),
            ("duplicate_current_runs", audit_duplicate_current_runs, (conn,)),
            ("expired_task_leases", audit_expired_task_leases, (conn, now)),
        )
        for name, function, args in checks:
            required_table = {
                "stale_batches": "sampling_batches",
                "batch_count_drift": "sampling_batches",
                "duplicate_current_runs": "model_runs",
                "expired_task_leases": "sampling_tasks",
            }[name]
            if required_table not in known_tables:
                continue
            issues, warnings = function(*args)
            report["issues"][name] = issues
            report["warnings"].extend(warnings)

    report["summary"] = {
        name: len(items) for name, items in report["issues"].items()
    }
    report["summary"]["total_issues"] = sum(report["summary"].values())
    report["ok"] = report["summary"]["total_issues"] == 0 and not report["warnings"]
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读检查 SQLite 中的僵尸批次、计数漂移、重复 current 与过期 lease。"
    )
    parser.add_argument("--db", required=True, type=Path, help="SQLite 数据库文件路径")
    parser.add_argument(
        "--stale-after-minutes",
        type=int,
        default=30,
        help="活跃批次超过多少分钟未更新视为僵尸，默认 30",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.stale_after_minutes < 1:
        parser.error("--stale-after-minutes 必须大于 0")
    if not args.db.is_file():
        parser.error(f"数据库文件不存在: {args.db}")

    try:
        report = run_audit(args.db, args.stale_after_minutes)
    except sqlite3.Error as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "database": str(args.db.resolve()),
                    "read_only": True,
                    "error": f"SQLite audit failed: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
