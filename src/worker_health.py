from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB_PATH, get_conn, renew_sampling_task_lease, upsert_worker_heartbeat


class PeriodicHeartbeat:
    """Small exception-safe heartbeat loop for workers and leased tasks."""

    def __init__(self, callback, *, interval_seconds: float = 15.0, name: str = "heartbeat"):
        self._callback = callback
        self._interval = max(0.05, float(interval_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.last_error = ""

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if self._callback() is False:
                    return
            except Exception as exc:  # a transient DB outage must not kill task execution
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]

    def start(self) -> "PeriodicHeartbeat":
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))

    def __enter__(self) -> "PeriodicHeartbeat":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def task_lease_heartbeat(
    db_target: Path | str | None,
    task_id: str,
    lease_owner: str,
    *,
    lease_seconds: int,
    interval_seconds: float | None = None,
) -> PeriodicHeartbeat:
    interval = interval_seconds if interval_seconds is not None else max(10.0, min(30.0, lease_seconds / 3))

    def renew() -> bool:
        with get_conn(db_target) as conn:
            return renew_sampling_task_lease(conn, task_id, lease_owner, lease_seconds)

    return PeriodicHeartbeat(renew, interval_seconds=interval, name=f"lease-{task_id[:24]}")


def worker_heartbeat(
    worker_id: str,
    queue_name: str,
    *,
    kind: str,
    db_target: Path | str | None = DEFAULT_DB_PATH,
    interval_seconds: float | None = None,
) -> PeriodicHeartbeat:
    interval = interval_seconds or float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15") or 15)
    metadata: dict[str, Any] = {
        "kind": kind,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }

    def beat(status: str = "running") -> bool:
        with get_conn(db_target) as conn:
            upsert_worker_heartbeat(conn, worker_id, queue_name, status=status, metadata=metadata)
        return True

    beat()
    heartbeat = PeriodicHeartbeat(beat, interval_seconds=interval, name=f"worker-heartbeat-{kind}")
    original_stop = heartbeat.stop

    def stop(timeout: float = 2.0) -> None:
        original_stop(timeout)
        try:
            beat("stopped")
        except Exception:
            pass

    heartbeat.stop = stop  # type: ignore[method-assign]
    return heartbeat
