from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import start_preflight
from src.db import create_model_config, create_project, get_conn, import_question_content_rows, init_db
from src.provider_health import record_provider_failure
from src.reliability import ClassifiedError, ErrorCode


class StartPreflightTests(unittest.TestCase):
    def test_preflight_blocks_missing_questions_then_accepts_ready_mock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TASK_QUEUE_BACKEND": "inline"}, clear=False):
            path = Path(tmp) / "preflight.db"
            init_db(path)
            with get_conn(path) as conn:
                project_id = create_project(conn, {"client_name": "预检", "brand_name": "预检品牌"})
                model_id = create_model_config(conn, {"provider": "mock", "label": "Mock", "model": "mock-model", "active": True, "supports_pure": True})
                payload = {"project_id": project_id, "repeat_count": 1, "models": [{"model_config_id": model_id, "search_enabled": False}]}
                blocked = start_preflight(conn, payload)
                self.assertFalse(blocked["ready"])
                self.assertIn("QUESTIONS_EMPTY", {item["code"] for item in blocked["blockers"]})

                import_question_content_rows(conn, project_id, [{"问题内容": "预检问题"}])
                ready = start_preflight(conn, payload)
                self.assertTrue(ready["ready"], ready["blockers"])
                self.assertEqual(ready["total_tasks"], 1)

    def test_preflight_does_not_block_an_expired_provider_circuit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TASK_QUEUE_BACKEND": "inline"}, clear=False):
            path = Path(tmp) / "preflight.db"
            init_db(path)
            with get_conn(path) as conn:
                project_id = create_project(conn, {"client_name": "预检", "brand_name": "预检品牌"})
                import_question_content_rows(conn, project_id, [{"问题内容": "预检问题"}])
                model_id = create_model_config(conn, {
                    "provider": "mock", "label": "Mock", "model": "mock-model",
                    "active": True, "supports_pure": True,
                })
                record_provider_failure(
                    conn,
                    "mock",
                    "mock-model",
                    "pure",
                    ClassifiedError(ErrorCode.AUTH, retryable=False, terminal=True),
                    "HTTP 401",
                )
                conn.execute(
                    "UPDATE provider_health SET circuit_open_until = ? WHERE provider = ? AND model = ? AND mode = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), "mock", "mock-model", "pure"),
                )
                payload = {
                    "project_id": project_id,
                    "repeat_count": 1,
                    "models": [{"model_config_id": model_id, "search_enabled": False}],
                }

                result = start_preflight(conn, payload)

            self.assertTrue(result["ready"], result["blockers"])
            self.assertNotIn("SOURCE_CIRCUIT_OPEN", {item["code"] for item in result["blockers"]})


if __name__ == "__main__":
    unittest.main()
