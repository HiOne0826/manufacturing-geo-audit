from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.db import get_conn, init_db
from src.provider_health import (
    CircuitOpenError,
    assert_circuit_closed,
    credential_fingerprint,
    distributed_provider_slot,
    get_health,
    list_provider_health,
    record_provider_failure,
    record_provider_success,
    redact_health_message,
    safe_endpoint,
)
from src.provider_probes import run_active_probe, start_optional_probe_scheduler, supported_probe_kinds
from src.reliability import ClassifiedError, ErrorCode


class ProviderHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "health.db"
        init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope_isolates_endpoint_mode_credential_and_region(self):
        auth = ClassifiedError(ErrorCode.AUTH, retryable=False, terminal=True)
        with get_conn(self.db_path) as conn:
            record_provider_failure(
                conn, "openai", "gpt-x", "search", auth, "HTTP 401",
                endpoint="https://api-a.example/v1?token=secret",
                credential="secret-a", exit_region="cn-north",
            )
            first = get_health(
                conn, "openai", "gpt-x", "search",
                endpoint="https://api-a.example/v1", credential="secret-a", exit_region="cn-north",
            )
            second = get_health(
                conn, "openai", "gpt-x", "search",
                endpoint="https://api-b.example/v1", credential="secret-b", exit_region="cn-north",
            )
            pure = get_health(
                conn, "openai", "gpt-x", "pure",
                endpoint="https://api-a.example/v1", credential="secret-a", exit_region="cn-north",
            )
            self.assertEqual(first["status"], "open")
            self.assertIsNone(second)
            self.assertIsNone(pure)
            public = list_provider_health(conn)[0]
            self.assertEqual(public["scope"]["endpoint"], "https://api-a.example/v1")
            self.assertEqual(public["scope"]["credential_fingerprint"], credential_fingerprint("secret-a"))
            self.assertNotIn("secret-a", str(public))

    def test_open_circuit_allows_only_one_half_open_trial(self):
        transient = ClassifiedError(ErrorCode.UPSTREAM, retryable=True)
        scope = {"endpoint": "https://api.example/v1", "credential": "key"}
        with get_conn(self.db_path) as conn:
            for _ in range(5):
                record_provider_failure(conn, "p", "m", "pure", transient, "503", **scope)
            row = get_health(conn, "p", "m", "pure", **scope)
            self.assertEqual(row["status"], "open")
            conn.execute(
                "UPDATE provider_health SET circuit_open_until = ? WHERE health_key = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), row["health_key"]),
            )
            assert_circuit_closed(conn, "p", "m", "pure", **scope)
            claimed = get_health(conn, "p", "m", "pure", **scope)
            self.assertEqual(claimed["status"], "half_open")
            with self.assertRaises(CircuitOpenError):
                assert_circuit_closed(conn, "p", "m", "pure", **scope)
            record_provider_success(conn, "p", "m", "pure", latency_ms=12, **scope)
            assert_circuit_closed(conn, "p", "m", "pure", **scope)
            self.assertEqual(get_health(conn, "p", "m", "pure", **scope)["status"], "healthy")

    def test_sliding_window_reports_success_rate_429_and_p95(self):
        limited = ClassifiedError(ErrorCode.RATE_LIMIT, retryable=True)
        with get_conn(self.db_path) as conn:
            for latency in (10, 20, 100):
                record_provider_success(conn, "p", "m", "search", latency_ms=latency)
            record_provider_failure(conn, "p", "m", "search", limited, "HTTP 429", latency_ms=30)
            row = list_provider_health(conn, window_minutes=60)[0]
            self.assertEqual(row["window"]["sample_count"], 4)
            self.assertEqual(row["window"]["success_rate"], 0.75)
            self.assertEqual(row["window"]["rate_limit_count"], 1)
            self.assertEqual(row["window"]["p95_latency_ms"], 100)
            self.assertEqual(row["consecutive_failures"], 1)
            self.assertTrue(row["last_success_at"])

    def test_messages_and_endpoint_are_redacted(self):
        message = redact_health_message("Authorization: Bearer abc api_key=xyz token=123", ("abc",))
        self.assertNotIn("abc", message)
        self.assertNotIn("xyz", message)
        self.assertNotIn("123", message)
        self.assertEqual(safe_endpoint("https://user:pass@example.com/v1?q=secret#x"), "https://example.com/v1")

    def test_active_probes_are_separate_and_do_not_create_runs(self):
        config = {
            "provider": "mock",
            "model": "mock-probe",
            "api_base": "https://api.example/v1",
            "api_key": "probe-secret",
            "supports_pure": 1,
            "supports_search": 1,
            "supports_citation": 1,
            "active": 1,
        }
        self.assertEqual(supported_probe_kinds(config), ["pure", "search", "citation"])

        def fake_call(_config, spec):
            return {
                "latency_ms": 15,
                "response_text": "probe-ok",
                "citations": [{"url": "https://example.com"}] if spec.search_enabled else [],
                "raw_response": {"secret": "must-not-leak"},
            }

        with get_conn(self.db_path) as conn:
            results = [run_active_probe(conn, config, kind, call=fake_call) for kind in supported_probe_kinds(config)]
            self.assertTrue(all(item["ok"] for item in results))
            self.assertEqual(results[2]["citation_count"], 1)
            self.assertNotIn("must-not-leak", str(results))
            self.assertNotIn("probe-secret", str(results))
            self.assertEqual(conn.execute("SELECT COUNT(*) AS count FROM model_runs").fetchone()["count"], 0)
            health = list_provider_health(conn)
            self.assertEqual({item["mode"] for item in health}, {"pure", "search", "citation"})
            self.assertTrue(all(item["checked_at"] for item in health))

    def test_citation_probe_fails_when_extraction_is_empty(self):
        config = {
            "provider": "mock", "model": "mock", "supports_search": 1,
            "supports_citation": 1,
        }
        with get_conn(self.db_path) as conn:
            result = run_active_probe(conn, config, "citation", call=lambda _config, _spec: {"citations": []})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], ErrorCode.SEARCH_DEPENDENCY.value)

    def test_probe_failure_never_returns_or_persists_the_credential(self):
        secret = "sk-super-secret-value"
        config = {
            "provider": "mock", "model": "mock", "api_key": secret,
            "supports_pure": 1,
        }

        def fail(_config, _spec):
            raise RuntimeError(f"upstream echoed {secret}")

        with get_conn(self.db_path) as conn:
            result = run_active_probe(conn, config, "pure", call=fail)
            rows = conn.execute("SELECT * FROM provider_health").fetchall()
            self.assertNotIn(secret, str(result))
            self.assertNotIn(secret, str([dict(row) for row in rows]))

    def test_paid_scheduler_is_disabled_by_default_and_without_live_permission(self):
        @contextmanager
        def factory():
            with get_conn(self.db_path) as conn:
                yield conn

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(start_optional_probe_scheduler(factory, lambda: []))
        with patch.dict(os.environ, {"PROVIDER_ACTIVE_PROBES_ENABLED": "1"}, clear=True):
            self.assertIsNone(start_optional_probe_scheduler(factory, lambda: []))

    def test_global_provider_limit_uses_atomic_redis_lease(self):
        calls: list[tuple] = []

        class FakeClient:
            def eval(self, *args):
                calls.append(("eval", *args))
                return 1

            def zrem(self, *args):
                calls.append(("zrem", *args))

        client = FakeClient()

        class FakeRedis:
            @staticmethod
            def from_url(*_args, **_kwargs):
                return client

        module = types.SimpleNamespace(Redis=FakeRedis)
        with patch.dict(sys.modules, {"redis": module}), patch.dict(
            os.environ,
            {"REDIS_URL": "redis://example.invalid/0", "GLOBAL_PROVIDER_LIMITS_ENABLED": "1"},
            clear=False,
        ):
            with distributed_provider_slot("openai", 2):
                self.assertTrue(any(call[0] == "eval" for call in calls))
        self.assertTrue(any(call[0] == "zrem" for call in calls))


if __name__ == "__main__":
    unittest.main()
