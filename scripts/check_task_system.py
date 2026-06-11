#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    backend = os.environ.get("TASK_QUEUE_BACKEND", "inline").strip().lower() or "inline"
    database_url = os.environ.get("DATABASE_URL", "").strip()
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    result = {
        "ok": True,
        "task_queue_backend": backend,
        "database_url_configured": bool(database_url),
        "redis_url": redis_url,
        "rq_available": module_available("rq"),
        "redis_available": module_available("redis"),
        "psycopg_available": module_available("psycopg"),
    }
    if backend == "rq":
        missing = [name for name in ["rq", "redis", "psycopg"] if not result[f"{name}_available"]]
        if missing:
            result["ok"] = False
            result["error"] = f"缺少依赖：{', '.join(missing)}。请安装 requirements-worker.txt。"
    if result["psycopg_available"] and database_url:
        try:
            import psycopg

            with psycopg.connect(database_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1").fetchone()
            result["postgres_connection_ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["postgres_connection_ok"] = False
            result["postgres_error"] = str(exc)
    elif database_url:
        result["postgres_connection_ok"] = False
    if result["redis_available"] and backend == "rq":
        try:
            from redis import Redis

            Redis.from_url(redis_url, socket_connect_timeout=3).ping()
            result["redis_connection_ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["redis_connection_ok"] = False
            result["redis_error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
