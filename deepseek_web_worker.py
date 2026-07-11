from __future__ import annotations

import os
from pathlib import Path

from src.db import init_db
from src.runtime_env import load_dotenv_file
from src.deepseek_web_tasks import recover_web_batches
from src.worker_health import worker_heartbeat


def main() -> int:
    load_dotenv_file(Path(".env"))
    if os.environ.get("DEEPSEEK_WEB_ENABLED", "0") != "1":
        raise SystemExit("DeepSeek 网页 worker 未启用，请设置 DEEPSEEK_WEB_ENABLED=1")
    try:
        from redis import Redis
        from rq import SimpleWorker
    except ImportError as exc:
        raise SystemExit("缺少网页 worker 依赖，请安装 requirements-web-worker.txt") from exc
    init_db()
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_name = os.environ.get("RQ_WEB_QUEUE_NAME", "geo-audit-web")
    recover_web_batches()
    worker = SimpleWorker([queue_name], connection=Redis.from_url(redis_url))
    heartbeat = worker_heartbeat(str(worker.name), queue_name, kind="deepseek_web")
    try:
        heartbeat.start()
        worker.work()
    finally:
        heartbeat.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
