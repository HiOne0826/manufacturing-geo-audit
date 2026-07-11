from __future__ import annotations

import os
import time

from src.db import claim_sampling_task, get_conn


def claim_then_hang(task_id: str, lease_seconds: int = 3) -> None:
    """Fault-drill job: claim a durable task and wait until the worker is killed."""
    owner = f"fault-drill-{os.getpid()}"
    with get_conn(None) as conn:
        if not claim_sampling_task(conn, task_id, owner, lease_seconds):
            raise RuntimeError(f"cannot claim {task_id}")
    time.sleep(300)
