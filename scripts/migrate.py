#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import DEFAULT_DB_PATH
from src.migrations import MigrationRunner, connect_postgres, connect_sqlite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查或应用 manufacturing-geo-audit 数据库迁移。")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="只读检查版本状态；有待应用版本时退出码为 1")
    action.add_argument("--apply", action="store_true", help="事务性应用所有待执行迁移")
    parser.add_argument("--dry-run", action="store_true", help="输出 apply 计划但不修改数据库")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--db", type=Path, help=f"SQLite 文件路径，默认 {DEFAULT_DB_PATH}")
    target.add_argument("--database-url", help="PostgreSQL DSN；也可使用 DATABASE_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check and args.dry_run:
        raise SystemExit("--dry-run 只能与 --apply 一起使用")
    database_url = str(args.database_url or os.environ.get("DATABASE_URL", "")).strip()
    if database_url:
        dialect = "postgres"
        conn = connect_postgres(database_url)
        target = _safe_postgres_target(database_url)
    else:
        dialect = "sqlite"
        db_path = args.db or Path(os.environ.get("GEO_AUDIT_DB_PATH", DEFAULT_DB_PATH))
        if args.check and not db_path.is_file():
            result = {"ok": False, "dialect": dialect, "database": str(db_path), "error": "database_not_found"}
            print(json.dumps(result, ensure_ascii=False))
            return 2
        db_path.parent.mkdir(parents=True, exist_ok=True) if args.apply and not args.dry_run else None
        conn = connect_sqlite(str(db_path)) if db_path.exists() or (args.apply and not args.dry_run) else connect_sqlite(":memory:")
        target = str(db_path)
    try:
        runner = MigrationRunner(conn, dialect)
        if args.check:
            result = runner.status()
            result["database"] = target
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["up_to_date"] else 1
        result = runner.apply(dry_run=args.dry_run)
        result["database"] = target
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "dialect": dialect, "database": target, "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        conn.close()


def _safe_postgres_target(dsn: str) -> str:
    # Never echo credentials from a DSN into CLI output or logs.
    if "@" in dsn:
        return f"postgresql://***@{dsn.rsplit('@', 1)[1]}"
    return "postgresql://***"


if __name__ == "__main__":
    raise SystemExit(main())
