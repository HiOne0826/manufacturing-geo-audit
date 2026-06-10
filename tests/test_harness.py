from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app
from src.adapters import AdapterError, call_configured_model
from src.db import create_model_config, create_project, get_conn, import_questions_rows, init_db, list_runs
from src.runner import run_batch


ROOT = Path(__file__).resolve().parents[1]


class AuthGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("APP_PASSWORD")
        cls.old_secret = os.environ.get("APP_SESSION_SECRET")
        os.environ["APP_PASSWORD"] = "test-password"
        os.environ["APP_SESSION_SECRET"] = "test-secret"
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "auth.db"
        app.DEFAULT_DB_PATH = cls.db_path
        app.SAMPLING_JOBS.clear()
        init_db(cls.db_path)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()
        if cls.old_password is None:
            os.environ.pop("APP_PASSWORD", None)
        else:
            os.environ["APP_PASSWORD"] = cls.old_password
        if cls.old_secret is None:
            os.environ.pop("APP_SESSION_SECRET", None)
        else:
            os.environ["APP_SESSION_SECRET"] = cls.old_secret

    def request_json(self, method: str, path: str, payload: dict | None = None, cookie: str = "") -> tuple[dict, dict]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers)

    def assert_unauthorized(self, method: str, path: str, payload: dict | None = None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 401)

    def login_cookie(self) -> str:
        data, headers = self.request_json("POST", "/api/auth/login", {"password": "test-password"})
        self.assertTrue(data["authenticated"])
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("geo_audit_session=", cookie)
        return cookie.split(";", 1)[0]

    def test_api_requires_login_and_allows_health_status(self):
        health, _ = self.request_json("GET", "/api/health")
        self.assertTrue(health["ok"])
        status, _ = self.request_json("GET", "/api/auth/status")
        self.assertTrue(status["auth_enabled"])
        self.assertFalse(status["authenticated"])
        self.assert_unauthorized("GET", "/api/projects")
        self.assert_unauthorized("POST", "/api/runs/start", {"project_id": 1, "models": []})

    def test_login_allows_api_and_models_still_hide_key(self):
        cookie = self.login_cookie()
        project, _ = self.request_json(
            "POST",
            "/api/projects",
            {"client_name": "认证测试", "brand_name": "认证品牌"},
            cookie=cookie,
        )
        self.assertGreater(project["id"], 0)
        model, _ = self.request_json(
            "POST",
            "/api/models",
            {
                "provider": "mock",
                "label": "Auth Mock",
                "model": "auth-mock",
                "api_key": "AUTH-SECRET-KEY",
                "supports_pure": True,
                "active": True,
            },
            cookie=cookie,
        )
        self.assertGreater(model["id"], 0)
        models, _ = self.request_json("GET", "/api/models", cookie=cookie)
        raw = json.dumps(models, ensure_ascii=False)
        self.assertNotIn("AUTH-SECRET-KEY", raw)
        for item in models["models"]:
            self.assertNotIn("api_key", item)


class HarnessHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "harness.db"
        app.DEFAULT_DB_PATH = cls.db_path
        app.SAMPLING_JOBS.clear()
        init_db(cls.db_path)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def request_text(self, path: str) -> str:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=10) as response:
            self.assertEqual(response.status, 200)
            return response.read().decode("utf-8-sig")

    def create_mock_project(self, question_count: int = 2) -> tuple[int, int]:
        project = self.request_json(
            "POST",
            "/api/projects",
            {
                "client_name": "测试客户",
                "brand_name": "测试品牌",
                "product_category": "测试品类",
                "competitors": "竞品A;竞品B",
            },
        )
        model = self.request_json(
            "POST",
            "/api/models",
            {
                "provider": "mock",
                "label": "Mock",
                "model": "mock-model",
                "api_key": "SECRET-KEY-SHOULD-NOT-LEAK",
                "supports_pure": True,
                "supports_search": True,
                "supports_tool_calling": False,
                "active": True,
            },
        )
        rows = [
            {
                "question_id": f"T{idx:03d}",
                "question": f"第 {idx} 个测试问题，测试品牌表现如何？",
                "question_type": "brand_direct",
                "target_brand": "测试品牌",
                "competitor_brands": "竞品A;竞品B",
            }
            for idx in range(1, question_count + 1)
        ]
        imported = self.request_json("POST", "/api/questions/import_rows", {"project_id": project["id"], "rows": rows})
        self.assertEqual(imported["count"], question_count)
        return int(project["id"]), int(model["id"])

    def test_health(self):
        data = self.request_json("GET", "/api/health")
        self.assertTrue(data["ok"])

    def test_models_do_not_return_plain_api_key(self):
        self.create_mock_project()
        raw = json.dumps(self.request_json("GET", "/api/models"), ensure_ascii=False)
        self.assertNotIn("SECRET-KEY-SHOULD-NOT-LEAK", raw)
        for model in json.loads(raw)["models"]:
            self.assertNotIn("api_key", model)

    def test_mock_sampling_and_exports(self):
        project_id, model_id = self.create_mock_project()
        started = self.request_json(
            "POST",
            "/api/runs/start",
            {
                "project_id": project_id,
                "models": [{"model_config_id": model_id, "search_enabled": True}],
                "repeat_count": 1,
            },
        )
        self.assertEqual(started["total"], 2)
        batch_id = started["batch_id"]
        status = {}
        for _ in range(50):
            status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
            if status.get("status") == "completed":
                break
            time.sleep(0.1)
        self.assertEqual(status.get("status"), "completed")
        self.assertEqual(status["success"] + status["failed"], status["total"])
        runs = self.request_json("GET", f"/api/runs?project_id={project_id}")["runs"]
        self.assertEqual(len(runs), 2)
        self.assertIn("run_id", self.request_text(f"/api/export/runs.csv?project_id={project_id}"))
        self.assertIn("<table", self.request_text(f"/api/export/runs.xls?project_id={project_id}"))


class HarnessDirectTests(unittest.TestCase):
    def test_default_blocks_live_model_calls(self):
        old_value = os.environ.pop("ALLOW_LIVE_MODEL_CALLS", None)
        try:
            with self.assertRaises(AdapterError):
                call_configured_model(
                    {
                        "provider": "openai",
                        "api_key": "fake-key",
                        "api_base": "https://api.openai.com/v1",
                        "model": "gpt-4.1",
                    },
                    "不会真正调用",
                    False,
                    0,
                    {"search_mode": "off", "thinking_type": "disabled"},
                )
        finally:
            if old_value is not None:
                os.environ["ALLOW_LIVE_MODEL_CALLS"] = old_value

    def test_150_questions_times_3_models_local_mock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "load.db"
            init_db(db_path)
            with get_conn(db_path) as conn:
                project_id = create_project(
                    conn,
                    {
                        "client_name": "测试客户",
                        "brand_name": "测试品牌",
                        "product_category": "测试品类",
                        "competitors": "竞品A;竞品B",
                    },
                )
                rows = [
                    {
                        "question_id": f"L{idx:03d}",
                        "question": f"第 {idx} 个本地负载问题，测试品牌是否出现？",
                        "question_type": "load",
                        "target_brand": "测试品牌",
                        "competitor_brands": "竞品A;竞品B",
                    }
                    for idx in range(1, 151)
                ]
                self.assertEqual(import_questions_rows(conn, project_id, rows), 150)
                model_ids = [
                    create_model_config(
                        conn,
                        {
                            "provider": "mock",
                            "label": f"Mock {idx}",
                            "model": f"mock-model-{idx}",
                            "supports_pure": True,
                            "supports_search": True,
                            "active": True,
                        },
                    )
                    for idx in range(1, 4)
                ]
                result = run_batch(
                    conn,
                    project_id,
                    {
                        "models": [{"model_config_id": model_id, "search_enabled": False} for model_id in model_ids],
                        "repeat_count": 1,
                    },
                    batch_id="test-load-batch",
                )
                runs = list_runs(conn, project_id, limit=1000)
            self.assertEqual(result["total"], 450)
            self.assertEqual(result["success"] + result["failed"], result["total"])
            self.assertEqual(len(runs), 450)


if __name__ == "__main__":
    unittest.main()
