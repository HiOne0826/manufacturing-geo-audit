from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .db import json_db_value, parse_json_field, utc_now
from .reliability import ClassifiedError, ErrorCode


class CircuitOpenError(RuntimeError):
    retryable = True
    error_code = "circuit_open"


_SECRET_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_-]?key\s*[:=]\s*|token\s*[:=]\s*)([^\s,;]+)"
)


def redact_health_message(message: Any, secrets: tuple[str, ...] = ()) -> str:
    """Return a diagnostic message that is safe for health APIs and audit logs."""
    text = _SECRET_PATTERN.sub(r"\1[REDACTED]", str(message or ""))
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    return text[:1000]


def credential_fingerprint(secret: str) -> str:
    """Stable credential identity without persisting or returning the credential."""
    value = str(secret or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else "unconfigured"


def safe_endpoint(value: str) -> str:
    """Normalize endpoint identity and discard userinfo, query and fragments."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), host.lower(), parsed.path.rstrip("/"), "", ""))


def health_scope(
    provider: str,
    model: str,
    mode: str,
    *,
    endpoint: str = "",
    credential: str = "",
    credential_fp: str = "",
    exit_region: str = "",
) -> dict[str, str]:
    return {
        "provider": str(provider or ""),
        "endpoint": safe_endpoint(endpoint),
        "model": str(model or ""),
        "mode": str(mode or "pure"),
        "credential_fingerprint": credential_fp or credential_fingerprint(credential),
        "exit_region": str(exit_region or os.environ.get("PROVIDER_EXIT_REGION", "")).strip(),
    }


def health_key(
    provider: str,
    model: str,
    mode: str,
    *,
    endpoint: str = "",
    credential: str = "",
    credential_fp: str = "",
    exit_region: str = "",
) -> str:
    if not endpoint and not credential and not credential_fp and not exit_region and not os.environ.get("PROVIDER_EXIT_REGION", ""):
        # Preserve the key used by the first V2 release for unscoped providers.
        return hashlib.sha256(f"{provider}|{model}|{mode}".encode("utf-8")).hexdigest()
    scope = health_scope(
        provider,
        model,
        mode,
        endpoint=endpoint,
        credential=credential,
        credential_fp=credential_fp,
        exit_region=exit_region,
    )
    encoded = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ensure_provider_health_schema(conn) -> None:
    """Create additive V2 health tables without coupling probes to app startup."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_health_scopes (
            health_key TEXT PRIMARY KEY,
            endpoint TEXT DEFAULT '',
            credential_fingerprint TEXT DEFAULT 'unconfigured',
            exit_region TEXT DEFAULT '',
            scope_json JSON DEFAULT '{}',
            half_open_trial_until TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_health_events_window ON provider_health_events(health_key, observed_at)"
    )


def _scope_key(provider: str, model: str, mode: str, scope: dict[str, Any]) -> str:
    if (
        not scope.get("endpoint")
        and scope.get("credential_fingerprint") in {None, "", "unconfigured"}
        and not scope.get("exit_region")
    ):
        return health_key(provider, model, mode)
    return health_key(
        provider,
        model,
        mode,
        endpoint=str(scope.get("endpoint") or ""),
        credential=str(scope.get("credential") or ""),
        credential_fp=str(scope.get("credential_fingerprint") or ""),
        exit_region=str(scope.get("exit_region") or ""),
    )


def _persist_scope(conn, key: str, scope: dict[str, str], *, half_open_trial_until: str | None = None) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO provider_health_scopes (
            health_key, endpoint, credential_fingerprint, exit_region, scope_json,
            half_open_trial_until, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (health_key) DO UPDATE SET
            endpoint = excluded.endpoint,
            credential_fingerprint = excluded.credential_fingerprint,
            exit_region = excluded.exit_region,
            scope_json = excluded.scope_json,
            half_open_trial_until = excluded.half_open_trial_until,
            updated_at = excluded.updated_at
        """,
        (
            key,
            scope["endpoint"],
            scope["credential_fingerprint"],
            scope["exit_region"],
            json_db_value(conn, scope),
            half_open_trial_until,
            now,
        ),
    )


def _record_event(
    conn,
    key: str,
    provider: str,
    model: str,
    mode: str,
    *,
    ok: bool,
    error_code: str = "",
    latency_ms: int = 0,
    source: str = "passive",
) -> None:
    conn.execute(
        """
        INSERT INTO provider_health_events (
            event_id, health_key, provider, model, mode, ok, error_code,
            latency_ms, source, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            key,
            provider,
            model,
            mode,
            int(ok),
            error_code,
            max(0, int(latency_ms or 0)),
            source,
            utc_now(),
        ),
    )


def get_health(conn, provider: str, model: str, mode: str, **scope_kwargs: Any) -> dict[str, Any] | None:
    ensure_provider_health_schema(conn)
    key = health_key(provider, model, mode, **scope_kwargs)
    row = conn.execute(
        """
        SELECT h.*, s.endpoint, s.credential_fingerprint, s.exit_region,
               s.scope_json, s.half_open_trial_until
        FROM provider_health h
        LEFT JOIN provider_health_scopes s ON s.health_key = h.health_key
        WHERE h.health_key = ?
        """,
        (key,),
    ).fetchone()
    return dict(row) if row else None


def assert_circuit_closed(
    conn,
    provider: str,
    model: str,
    mode: str,
    *,
    endpoint: str = "",
    credential: str = "",
    credential_fp: str = "",
    exit_region: str = "",
    half_open_seconds: int = 30,
) -> None:
    """Allow exactly one half-open trial after an open circuit cools down."""
    scope = health_scope(
        provider, model, mode, endpoint=endpoint, credential=credential,
        credential_fp=credential_fp, exit_region=exit_region,
    )
    ensure_provider_health_schema(conn)
    key = _scope_key(provider, model, mode, scope)
    row = get_health(
        conn, provider, model, mode, endpoint=endpoint, credential=credential,
        credential_fp=credential_fp, exit_region=exit_region,
    )
    if not row:
        return
    now = utc_now()
    status = str(row.get("status") or "unknown")
    open_until = str(row.get("circuit_open_until") or "")
    trial_until = str(row.get("half_open_trial_until") or "")
    if status == "open" and open_until and open_until > now:
        raise CircuitOpenError(f"信息源熔断中：{provider}/{model}/{mode}，预计恢复时间 {open_until}")
    if status == "half_open" and trial_until and trial_until > now:
        raise CircuitOpenError(f"信息源正在半开试探：{provider}/{model}/{mode}")
    if status not in {"open", "half_open"}:
        return

    # Atomic state claim: concurrent workers cannot both become the trial request.
    next_trial = (datetime.now(timezone.utc) + timedelta(seconds=max(5, half_open_seconds))).isoformat()
    cursor = conn.execute(
        """
        UPDATE provider_health
        SET status = 'half_open', circuit_open_until = ?, updated_at = ?
        WHERE health_key = ? AND status IN ('open', 'half_open')
          AND (circuit_open_until IS NULL OR circuit_open_until <= ?)
        """,
        (next_trial, now, key, now),
    )
    if getattr(cursor, "rowcount", 0) != 1:
        raise CircuitOpenError(f"信息源正在半开试探：{provider}/{model}/{mode}")
    _persist_scope(conn, key, scope, half_open_trial_until=next_trial)


def record_provider_success(
    conn,
    provider: str,
    model: str,
    mode: str,
    *,
    checked: bool = False,
    latency_ms: int = 0,
    endpoint: str = "",
    credential: str = "",
    credential_fp: str = "",
    exit_region: str = "",
    source: str = "passive",
) -> None:
    ensure_provider_health_schema(conn)
    scope = health_scope(
        provider, model, mode, endpoint=endpoint, credential=credential,
        credential_fp=credential_fp, exit_region=exit_region,
    )
    key = _scope_key(provider, model, mode, scope)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO provider_health (
            health_key, provider, model, mode, status, consecutive_failures,
            success_count, failure_count, last_error_code, last_error_message,
            circuit_open_until, last_success_at, last_failure_at, checked_at, updated_at
        ) VALUES (?, ?, ?, ?, 'healthy', 0, 1, 0, '', '', NULL, ?, NULL, ?, ?)
        ON CONFLICT (health_key) DO UPDATE SET
            status = 'healthy', consecutive_failures = 0,
            success_count = provider_health.success_count + 1,
            last_error_code = '', last_error_message = '', circuit_open_until = NULL,
            last_success_at = excluded.last_success_at,
            checked_at = COALESCE(excluded.checked_at, provider_health.checked_at), updated_at = excluded.updated_at
        """,
        (key, provider, model, mode, now, now if checked else None, now),
    )
    _persist_scope(conn, key, scope, half_open_trial_until=None)
    conn.execute("UPDATE provider_health_scopes SET half_open_trial_until = NULL WHERE health_key = ?", (key,))
    _record_event(
        conn, key, provider, model, mode, ok=True, latency_ms=latency_ms,
        source="active" if checked else source,
    )


def record_provider_failure(
    conn,
    provider: str,
    model: str,
    mode: str,
    classified: ClassifiedError,
    message: str,
    *,
    checked: bool = False,
    latency_ms: int = 0,
    endpoint: str = "",
    credential: str = "",
    credential_fp: str = "",
    exit_region: str = "",
    source: str = "passive",
) -> None:
    ensure_provider_health_schema(conn)
    scope = health_scope(
        provider, model, mode, endpoint=endpoint, credential=credential,
        credential_fp=credential_fp, exit_region=exit_region,
    )
    current = get_health(
        conn, provider, model, mode, endpoint=endpoint, credential=credential,
        credential_fp=credential_fp, exit_region=exit_region,
    ) or {}
    failures = int(current.get("consecutive_failures", 0) or 0) + 1
    # Authentication/region/model errors isolate immediately. A failed half-open trial reopens.
    immediate = classified.terminal or current.get("status") == "half_open"
    open_seconds = 3600 if classified.terminal else 120 if immediate or failures >= 5 else 0
    open_until = (datetime.now(timezone.utc) + timedelta(seconds=open_seconds)).isoformat() if open_seconds else None
    status = "open" if open_until else "degraded"
    key = _scope_key(provider, model, mode, scope)
    now = utc_now()
    safe_message = redact_health_message(message, (credential,))
    conn.execute(
        """
        INSERT INTO provider_health (
            health_key, provider, model, mode, status, consecutive_failures,
            success_count, failure_count, last_error_code, last_error_message,
            circuit_open_until, last_success_at, last_failure_at, checked_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, NULL, ?, ?, ?)
        ON CONFLICT (health_key) DO UPDATE SET
            status = excluded.status, consecutive_failures = excluded.consecutive_failures,
            failure_count = provider_health.failure_count + 1,
            last_error_code = excluded.last_error_code, last_error_message = excluded.last_error_message,
            circuit_open_until = excluded.circuit_open_until, last_failure_at = excluded.last_failure_at,
            checked_at = COALESCE(excluded.checked_at, provider_health.checked_at), updated_at = excluded.updated_at
        """,
        (
            key, provider, model, mode, status, failures, classified.code.value,
            safe_message, open_until, now, now if checked else None, now,
        ),
    )
    _persist_scope(conn, key, scope, half_open_trial_until=None)
    conn.execute("UPDATE provider_health_scopes SET half_open_trial_until = NULL WHERE health_key = ?", (key,))
    _record_event(
        conn, key, provider, model, mode, ok=False, error_code=classified.code.value,
        latency_ms=latency_ms, source="active" if checked else source,
    )


def _percentile95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)])


def list_provider_health(conn, *, window_minutes: int = 60, window_size: int = 100) -> list[dict[str, Any]]:
    """Return secret-safe scoped health plus bounded passive sliding-window metrics."""
    ensure_provider_health_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, window_minutes))).isoformat()
    rows = conn.execute(
        """
        SELECT h.*, s.endpoint, s.credential_fingerprint, s.exit_region, s.scope_json,
               s.half_open_trial_until
        FROM provider_health h
        LEFT JOIN provider_health_scopes s ON s.health_key = h.health_key
        ORDER BY h.provider, h.model, h.mode, h.health_key
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        events = conn.execute(
            """
            SELECT ok, error_code, latency_ms, observed_at
            FROM provider_health_events
            WHERE health_key = ? AND observed_at >= ? AND source = 'passive'
            ORDER BY observed_at DESC LIMIT ?
            """,
            (item["health_key"], cutoff, max(1, min(window_size, 1000))),
        ).fetchall()
        success = sum(int(event["ok"] or 0) for event in events)
        total = len(events)
        latencies = [int(event["latency_ms"] or 0) for event in events if int(event["latency_ms"] or 0) > 0]
        item["window"] = {
            "minutes": max(1, window_minutes),
            "sample_count": total,
            "success_rate": round(success / total, 4) if total else None,
            "rate_limit_count": sum(1 for event in events if event["error_code"] == ErrorCode.RATE_LIMIT.value),
            "p95_latency_ms": _percentile95(latencies),
        }
        item["scope"] = parse_json_field(item.pop("scope_json", None), {})
        # Health APIs return classification, never provider response bodies or secrets.
        item.pop("last_error_message", None)
        result.append(item)
    return result


@contextmanager
def distributed_provider_slot(provider: str, limit: int, ttl_seconds: int = 180) -> Iterator[None]:
    """Atomic Redis semaphore across batches/workers with lease renewal."""
    if os.environ.get("GLOBAL_PROVIDER_LIMITS_ENABLED", "1") != "1" or not os.environ.get("REDIS_URL"):
        yield
        return
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("已配置全局流控，但缺少 redis 依赖") from exc
    redis = Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=1, socket_timeout=2)
    key = f"geo-audit:provider-slots:{provider}"
    owner = hashlib.sha256(f"{os.getpid()}:{time.time_ns()}".encode()).hexdigest()
    deadline = time.monotonic() + 30
    acquired = False
    stop_renewal = threading.Event()
    renewal: threading.Thread | None = None
    ttl_ms = max(10_000, int(ttl_seconds * 1000))
    acquire_script = """
    local key, owner = KEYS[1], ARGV[1]
    local now, ttl, limit = tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
    if redis.call('ZCARD', key) < limit then
      redis.call('ZADD', key, now + ttl, owner)
      redis.call('PEXPIRE', key, ttl + 5000)
      return 1
    end
    return 0
    """
    renew_script = """
    local key, owner = KEYS[1], ARGV[1]
    if redis.call('ZSCORE', key, owner) then
      redis.call('ZADD', key, tonumber(ARGV[2]) + tonumber(ARGV[3]), owner)
      redis.call('PEXPIRE', key, tonumber(ARGV[3]) + 5000)
      return 1
    end
    return 0
    """
    try:
        while time.monotonic() < deadline:
            now_ms = int(time.time() * 1000)
            if int(redis.eval(acquire_script, 1, key, owner, now_ms, ttl_ms, max(1, limit)) or 0) == 1:
                acquired = True
                break
            time.sleep(0.1)
        if not acquired:
            raise TimeoutError(f"等待信息源全局并发槽超时：{provider}")

        def renew() -> None:
            while not stop_renewal.wait(max(1.0, ttl_seconds / 3)):
                try:
                    renewed = redis.eval(renew_script, 1, key, owner, int(time.time() * 1000), ttl_ms)
                except Exception:
                    return
                if int(renewed or 0) != 1:
                    return

        renewal = threading.Thread(target=renew, name=f"provider-slot-{provider}", daemon=True)
        renewal.start()
        yield
    finally:
        stop_renewal.set()
        if renewal and renewal.is_alive():
            renewal.join(timeout=2)
        if acquired:
            try:
                redis.zrem(key, owner)
            except Exception:
                pass
