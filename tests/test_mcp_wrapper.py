from __future__ import annotations

import json
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mcp.client import AgentApiClient, AgentApiError
from mcp.schemas import CreateBatchInput, ExportBatchInput, RerunFailedInput, ValidationError
from mcp.server import TOOLS, call_tool, handle_request


class FakeAgentApiHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    token = "agent-test-token"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.handle_any()

    def do_POST(self) -> None:
        self.handle_any()

    def handle_any(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        payload = json.loads(body.decode("utf-8")) if body else None
        FakeAgentApiHandler.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "payload": payload,
            }
        )
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.write_json(401, {"error": "bad token agent-test-token SHOULD_NOT_LEAK"})
            return
        parsed = urllib.parse.urlparse(self.path)
        if self.command == "POST" and parsed.path == "/api/agent/batches":
            self.write_json(200, {"batch_id": "batch-test", "total": 3, "status": "queued"})
            return
        if self.command == "GET" and parsed.path == "/api/agent/batches/batch-test":
            self.write_json(
                200,
                {
                    "batch": {
                        "batch_id": "batch-test",
                        "status": "completed",
                        "total": 3,
                        "success": 3,
                        "failed": 0,
                    }
                },
            )
            return
        if self.command == "GET" and parsed.path == "/api/agent/batches/batch-test/export":
            self.write_json(200, {"batch_id": "batch-test", "format": "xls", "path": "/api/export/batches/batch-test/runs.xls"})
            return
        if self.command == "POST" and parsed.path == "/api/agent/batches/batch-test/rerun_failed":
            self.write_json(200, {"batch_id": "batch-test", "total": 1, "status": "queued"})
            return
        if parsed.path == "/api/agent/batches/server-error":
            self.write_json(500, {"error": "backend exploded"})
            return
        self.write_json(404, {"error": "not found"})

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class McpWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeAgentApiHandler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAgentApiHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAgentApiHandler.requests = []
        self.client = AgentApiClient(self.base_url, "agent-test-token", timeout=5)

    def test_client_maps_create_status_export_and_rerun(self):
        created = self.client.create_batch(
            CreateBatchInput.from_dict(
                {
                    "project_id": 1,
                    "model_ids": [11, 12],
                    "csv_text": "question_id,question\nQ001,测试",
                    "options": {"repeat_count": 1, "max_workers": 8, "retry_count": 1},
                }
            )
        )
        status = self.client.get_batch_status("batch-test")
        exported = self.client.export_batch(ExportBatchInput.from_dict({"batch_id": "batch-test"}))
        rerun = self.client.rerun_failed(RerunFailedInput.from_dict({"batch_id": "batch-test", "options": {"max_workers": 2}}))

        self.assertEqual(created["batch_id"], "batch-test")
        self.assertEqual(status["batch"]["status"], "completed")
        self.assertEqual(exported["url"], f"{self.base_url}/api/export/batches/batch-test/runs.xls")
        self.assertEqual(rerun["status"], "queued")
        self.assertEqual(
            [(item["method"], item["path"]) for item in FakeAgentApiHandler.requests],
            [
                ("POST", "/api/agent/batches"),
                ("GET", "/api/agent/batches/batch-test"),
                ("GET", "/api/agent/batches/batch-test/export"),
                ("POST", "/api/agent/batches/batch-test/rerun_failed"),
            ],
        )
        self.assertTrue(all(item["authorization"] == "Bearer agent-test-token" for item in FakeAgentApiHandler.requests))
        self.assertEqual(FakeAgentApiHandler.requests[0]["payload"]["model_ids"], [11, 12])
        self.assertEqual(FakeAgentApiHandler.requests[3]["payload"], {"options": {"max_workers": 2}})

    def test_errors_are_readable_and_do_not_leak_token(self):
        bad_client = AgentApiClient(self.base_url, "wrong-token", timeout=5)
        with self.assertRaises(AgentApiError) as ctx:
            bad_client.get_batch_status("batch-test")
        message = str(ctx.exception)
        self.assertIn("Agent token 无效或未配置", message)
        self.assertNotIn("wrong-token", message)
        self.assertNotIn("agent-test-token", message)

        with self.assertRaises(AgentApiError) as server_ctx:
            self.client.get_batch_status("server-error")
        self.assertIn("Agent API 返回 500", str(server_ctx.exception))
        self.assertIn("backend exploded", str(server_ctx.exception))

    def test_schema_validation(self):
        with self.assertRaises(ValidationError):
            CreateBatchInput.from_dict({"project_id": 1, "model_ids": []})
        with self.assertRaises(ValidationError):
            ExportBatchInput.from_dict({"batch_id": "batch-test", "format": "csv"})

    def test_mcp_tools_list_and_call(self):
        tool_names = {item["name"] for item in TOOLS}
        self.assertEqual(
            tool_names,
            {
                "create_geo_audit_batch",
                "get_geo_audit_batch_status",
                "export_geo_audit_batch",
                "rerun_failed_geo_audit_tasks",
            },
        )
        result = call_tool(
            "export_geo_audit_batch",
            {"batch_id": "batch-test", "format": "xls"},
            client=self.client,
        )
        self.assertEqual(result["url"], f"{self.base_url}/api/export/batches/batch-test/runs.xls")

    def test_json_rpc_handler(self):
        list_response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, client=self.client)
        self.assertEqual(list_response["result"]["tools"][0]["name"], "create_geo_audit_batch")

        call_response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_geo_audit_batch_status", "arguments": {"batch_id": "batch-test"}},
            },
            client=self.client,
        )
        text = call_response["result"]["content"][0]["text"]
        self.assertIn('"status": "completed"', text)


if __name__ == "__main__":
    unittest.main()
