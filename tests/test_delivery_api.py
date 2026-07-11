from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app
from src.db import (
    create_project,
    create_sampling_batch,
    get_conn,
    import_question_content_rows,
    init_db,
    insert_evaluation,
    insert_run,
    list_model_configs,
    utc_now,
)
from src.delivery import ensure_delivery_schema


class TrustedDeliveryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.old_backend = os.environ.get("TASK_QUEUE_BACKEND")
        os.environ["TASK_QUEUE_BACKEND"] = "inline"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / "delivery-api.db"
        app.DEFAULT_DB_PATH = cls.db_path
        init_db(cls.db_path)
        with get_conn(cls.db_path) as conn:
            ensure_delivery_schema(conn)
            cls.project_id = create_project(conn, {"client_name": "交付测试", "brand_name": "测试品牌"})
            import_question_content_rows(conn, cls.project_id, [{"问题内容": "测试品牌表现如何？"}])
            question_id = int(conn.execute("SELECT id FROM questions WHERE project_id = ?", (cls.project_id,)).fetchone()["id"])
            model_id = int(list_model_configs(conn)[0]["id"])
            for batch_id in ("delivery-base", "delivery-next"):
                create_sampling_batch(conn, {
                    "batch_id": batch_id,
                    "project_id": cls.project_id,
                    "status": "completed",
                    "batch_name": batch_id,
                    "config": {"repeat_count": 1},
                    "config_snapshot": {"repeat_count": 1, "model_ids": [model_id]},
                })
                run_id = f"run-{batch_id}"
                insert_run(conn, {
                    "run_id": run_id,
                    "batch_id": batch_id,
                    "project_id": cls.project_id,
                    "question_id": question_id,
                    "model_config_id": model_id,
                    "provider": "mock",
                    "model": "mock-model",
                    "requested_at": utc_now(),
                    "response_text": "测试品牌值得关注",
                    "status": "success",
                    "latency_ms": 20,
                    "search_mode": "off",
                    "repeat_index": 1,
                })
                insert_evaluation(conn, {
                    "run_id": run_id,
                    "target_brand_mentioned": True,
                    "target_brand_rank": 1,
                    "owned_site_cited": True,
                    "third_party_cited": False,
                })
            cls.run_id = "run-delivery-base"

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()
        if cls.old_backend is None:
            os.environ.pop("TASK_QUEUE_BACKEND", None)
        else:
            os.environ["TASK_QUEUE_BACKEND"] = cls.old_backend

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        raw = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "X-Actor": "api-tester"}
        request = urllib.request.Request(f"{self.base_url}{path}", data=raw, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_review_report_compare_and_frozen_export_contracts(self) -> None:
        status, reviewed = self.request("POST", f"/api/runs/{self.run_id}/review", {
            "decision": "valid", "reason": "证据完整"
        })
        self.assertEqual(status, 201)
        self.assertEqual(reviewed["review"]["actor"], "local-user")
        status, current = self.request("GET", f"/api/runs/{self.run_id}/reviews")
        self.assertEqual(status, 200)
        self.assertEqual(current["review"]["decision"], "valid")

        status, created = self.request("POST", "/api/reports", {
            "project_id": self.project_id,
            "batch_id": "delivery-base",
            "title": "客户交付 V1",
        })
        self.assertEqual(status, 201)
        report_id = created["report"]["report_id"]
        status, reviewed_report = self.request("POST", f"/api/reports/{report_id}/status", {"status": "reviewed"})
        self.assertEqual((status, reviewed_report["report"]["status"]), (200, "reviewed"))
        status, frozen = self.request("POST", f"/api/reports/{report_id}/freeze", {"summary_snapshot": {"total_runs": 999999}})
        self.assertEqual((status, frozen["report"]["status"]), (200, "frozen"))
        self.assertNotEqual(frozen["report"]["summary_snapshot"]["total_runs"], 999999)

        status, reports = self.request("GET", f"/api/reports?project_id={self.project_id}&batch_id=delivery-base")
        self.assertEqual(status, 200)
        self.assertEqual(reports["reports"][0]["report_id"], report_id)
        status, exported = self.request("GET", f"/api/reports/{report_id}/export")
        self.assertEqual(status, 200)
        self.assertTrue(exported["scope"]["frozen"])
        self.assertEqual(exported["runs"][0]["run_id"], self.run_id)

        status, comparison = self.request(
            "GET",
            f"/api/analytics/compare?project_id={self.project_id}&baseline_batch_id=delivery-base&batch_id=delivery-next",
        )
        self.assertEqual(status, 200)
        self.assertTrue(comparison["comparable"])

        status, live_export = self.request("GET", "/api/export/delivery.json?batch_id=delivery-base&include_attempts=1")
        self.assertEqual(status, 200)
        self.assertEqual(live_export["schema_version"], "geo-audit-delivery/v1")

    def test_delivery_errors_are_stable_4xx_responses(self) -> None:
        status, body = self.request("POST", "/api/reports", {"project_id": 999999, "title": "missing"})
        self.assertEqual(status, 404)
        self.assertEqual(body["code"], "PROJECT_NOT_FOUND")
        status, body = self.request("GET", "/api/export/delivery.json")
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "EXPORT_SCOPE_REQUIRED")

    def test_batch_metadata_archive_and_audit_contract(self) -> None:
        status, updated = self.request(
            "POST",
            "/api/batches/delivery-base/metadata",
            {"batch_name": "客户基线", "purpose": "首次交付"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["batch"]["batch_name"], "客户基线")

        status, archived = self.request("POST", "/api/batches/delivery-base/archive", {"archived": True})
        self.assertEqual(status, 200)
        self.assertTrue(archived["batch"]["archived_at"])

        status, audit = self.request(
            "GET",
            f"/api/audit?project_id={self.project_id}&entity_type=batch&entity_id=delivery-base",
        )
        self.assertEqual(status, 200)
        actions = {item["action"] for item in audit["events"]}
        self.assertIn("batch_metadata_updated", actions)
        self.assertIn("batch_archived", actions)
        self.assertTrue(all(item["actor"] == "local-user" for item in audit["events"]))


if __name__ == "__main__":
    unittest.main()
