from __future__ import annotations

import os
from pathlib import Path

from src.db import init_db
from src.runtime_env import load_dotenv_file
from src.worker_health import worker_heartbeat


def main() -> int:
    load_dotenv_file(Path(".env"))
    try:
        from redis import Redis
        from rq import Worker
    except ImportError as exc:
        raise SystemExit("缺少 RQ worker 依赖，请先安装：python3 -m pip install -r requirements-worker.txt") from exc

    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_name = os.environ.get("RQ_QUEUE_NAME", "geo-audit")
    init_db()
    conn = Redis.from_url(redis_url)
    worker = Worker([queue_name], connection=conn)
    heartbeat = worker_heartbeat(str(worker.name), queue_name, kind="rq")
    try:
        heartbeat.start()
        worker.work()
    finally:
        heartbeat.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
