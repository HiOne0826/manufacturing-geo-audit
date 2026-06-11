#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    backend = os.environ.get("TASK_QUEUE_BACKEND", "inline")
    result = {
        "ok": True,
        "task_queue_backend": backend,
        "database_url_configured": bool(os.environ.get("DATABASE_URL")),
        "redis_url": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        "rq_available": module_available("rq"),
        "redis_available": module_available("redis"),
        "psycopg_available": module_available("psycopg"),
    }
    if backend == "rq":
        missing = [name for name in ["rq", "redis", "psycopg"] if not result[f"{name}_available"]]
        if missing:
            result["ok"] = False
            result["error"] = f"缺少依赖：{', '.join(missing)}。请安装 requirements-worker.txt。"
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
