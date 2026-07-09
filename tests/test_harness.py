from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

import app
from src.adapters import AdapterError, call_configured_model, kimi_search_request, normalize_choice_text, normalize_run_options, openai_compatible_request, openai_responses_request
from src.db import create_model_config, create_project, create_sampling_batch, get_conn, import_question_content_rows, import_questions_rows, init_db, insert_run, list_failed_runs_by_batch, list_questions, list_runs, update_sampling_batch, utc_now
from src.exporter import runs_to_csv, runs_to_excel_html
from src.runner import prepare_runtime_task, provider_concurrency_group, provider_concurrency_limit, rerun_failed_runs, run_batch
from src.tasks import mark_batch_failed


ROOT = Path(__file__).resolve().parents[1]


class AuthGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("APP_PASSWORD")
        cls.old_secret = os.environ.get("APP_SESSION_SECRET")
        cls.old_backend = os.environ.get("TASK_QUEUE_BACKEND")
        os.environ["APP_PASSWORD"] = "test-password"
        os.environ["APP_SESSION_SECRET"] = "test-secret"
        os.environ["TASK_QUEUE_BACKEND"] = "inline"
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
        if cls.old_backend is None:
            os.environ.pop("TASK_QUEUE_BACKEND", None)
        else:
            os.environ["TASK_QUEUE_BACKEND"] = cls.old_backend

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


class AgentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_token = os.environ.get("AGENT_API_TOKEN")
        cls.old_backend = os.environ.get("TASK_QUEUE_BACKEND")
        os.environ["AGENT_API_TOKEN"] = "agent-test-token"
        os.environ["TASK_QUEUE_BACKEND"] = "inline"
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "agent.db"
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
        if cls.old_token is None:
            os.environ.pop("AGENT_API_TOKEN", None)
        else:
            os.environ["AGENT_API_TOKEN"] = cls.old_token
        if cls.old_backend is None:
            os.environ.pop("TASK_QUEUE_BACKEND", None)
        else:
            os.environ["TASK_QUEUE_BACKEND"] = cls.old_backend

    def request_json(self, method: str, path: str, payload: dict | None = None, token: str = "agent-test-token") -> dict:
        data = None
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_agent_requires_token_and_can_create_batch(self):
        request = urllib.request.Request(f"{self.base_url}/api/agent/batches", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 401)
        with get_conn(self.db_path) as conn:
            project_id = create_project(conn, {"client_name": "Agent", "brand_name": "Agent品牌"})
            model_id = create_model_config(
                conn,
                {
                    "provider": "mock",
                    "label": "Agent Mock",
                    "model": "agent-mock",
                    "supports_pure": True,
                    "active": True,
                },
            )
        created = self.request_json(
            "POST",
            "/api/agent/batches",
            {
                "project_id": project_id,
                "csv_text": "问题ID,问题内容,问题类型,平台\nA001,请介绍Agent品牌,agent,Agent Mock\n",
                "model_ids": [model_id],
                "options": {"repeat_count": 1, "max_workers": 2},
            },
        )
        self.assertEqual(created["total"], 1)
        batch_id = created["batch_id"]
        status = {}
        for _ in range(50):
            status = self.request_json("GET", f"/api/agent/batches/{batch_id}")
            if status["batch"]["status"] == "completed":
                break
            time.sleep(0.1)
        self.assertEqual(status["batch"]["status"], "completed")
        exported = self.request_json("GET", f"/api/agent/batches/{batch_id}/export")
        self.assertEqual(exported["path"], f"/api/export/batches/{batch_id}/runs.xls")


class HarnessHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_backend = os.environ.get("TASK_QUEUE_BACKEND")
        os.environ["TASK_QUEUE_BACKEND"] = "inline"
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
        if cls.old_backend is None:
            os.environ.pop("TASK_QUEUE_BACKEND", None)
        else:
            os.environ["TASK_QUEUE_BACKEND"] = cls.old_backend

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
                "问题ID": f"T{idx:03d}",
                "问题内容": f"第 {idx} 个测试问题，测试品牌表现如何？",
                "问题类型": "brand_direct",
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

    def test_models_include_sampling_defaults(self):
        self.create_mock_project()
        models = self.request_json("GET", "/api/models")["models"]
        kimi = next(item for item in models if item["provider"] == "kimi")
        deepseek = next(item for item in models if item["provider"] == "deepseek")
        hunyuan = next(item for item in models if item["provider"] == "hunyuan")
        openrouter_gpt = next(item for item in models if item["provider"] == "openrouter_gpt")
        openrouter_gemini = next(item for item in models if item["provider"] == "openrouter_gemini")
        self.assertEqual(kimi["sampling_defaults"]["temperature"], 0.6)
        self.assertIn("$web_search", kimi["web_search_param_path"])
        self.assertEqual(hunyuan["label"], "腾讯元宝")
        self.assertEqual(hunyuan["sampling_defaults"]["search_mode"], "force")
        self.assertIn("force_search_enhancement=true", hunyuan["web_search_param_path"])
        self.assertEqual(deepseek["sampling_defaults"]["temperature"], 1)
        self.assertTrue(deepseek["supports_search"])
        self.assertIn("Brave Search", deepseek["web_search_mode"])
        self.assertEqual(openrouter_gpt["label"], "OpenRouter-GPT")
        self.assertEqual(openrouter_gemini["label"], "OpenRouter-Gemini")
        self.assertEqual(openrouter_gpt["sampling_defaults"]["search_strategy"], "exa")
        self.assertEqual(openrouter_gemini["sampling_defaults"]["search_strategy"], "exa")
        self.assertTrue(openrouter_gpt["supports_search"])
        self.assertTrue(openrouter_gemini["supports_search"])
        self.assertIn("web plugin", openrouter_gpt["web_search_mode"])

    def test_question_file_import_only_uses_question_content_column(self):
        project_id, _ = self.create_mock_project(question_count=0)
        csv_text = "\n".join(
            [
                "问题ID,问题内容,问题类型,产品线,平台,回答原文,品牌名是否出现,品牌名出现名称,推荐排名,是否Top3,官网域名类型,竞品出现,引用来源,原始数据,测试时间",
                "Q001,汽车白车身多材料连接有哪些 FDS 热熔螺接设备品牌值得推荐？,品牌推荐,FDS,ChatGPT,这段回答不应导入,,,,,,,,,",
                "Q002,新能源电池 PACK 装配有哪些 FDS 热熔螺接设备品牌值得推荐？,品牌推荐,FDS,DeepSeek,这段回答也不应导入,,,,,,,,,",
            ]
        )
        imported = self.request_json(
            "POST",
            "/api/questions/import_file",
            {
                "project_id": project_id,
                "file_name": "questions.csv",
                "file_base64": base64.b64encode(csv_text.encode("utf-8-sig")).decode("ascii"),
            },
        )
        self.assertEqual(imported["count"], 2)
        questions = self.request_json("GET", f"/api/questions?project_id={project_id}")["questions"]
        raw = json.dumps(questions, ensure_ascii=False)
        self.assertIn("汽车白车身多材料连接", raw)
        self.assertIn("新能源电池 PACK 装配", raw)
        self.assertNotIn("这段回答不应导入", raw)
        self.assertTrue(all(item["question_type"] == "品牌推荐" for item in questions))
        self.assertTrue(all(item["product_line"] == "FDS" for item in questions))
        self.assertIn("ChatGPT", raw)

    def test_openai_model_settings_match_hosted_web_search(self):
        self.create_mock_project()
        models = self.request_json("GET", "/api/models")["models"]
        gpt = next(item for item in models if item["provider"] == "openai")
        self.assertEqual(gpt["api_family"], "OpenAI Responses API")
        self.assertEqual(gpt["model"], "gpt-5.5")
        self.assertTrue(gpt["supports_search"])
        self.assertTrue(gpt["supports_user_location"])
        self.assertIn("tools[].type=web_search", gpt["web_search_param_path"])
        self.assertIn("tools[].search_context_size", gpt["web_search_param_path"])
        self.assertIn("url_citation", gpt["citation_param_path"])

    def test_openai_web_search_payload_uses_responses_tool(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "auto",
                "search_strategy": "high",
                "search_user_location": "Shanghai, CN, Asia/Shanghai",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "output_text": "ok",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "ok",
                                "annotations": [
                                    {"type": "url_citation", "url": "https://example.com", "title": "Example"}
                                ],
                            }
                        ],
                    }
                ],
            }
            result = openai_responses_request("https://api.openai.com/v1", "test-key", "gpt-5.5", "问题", options)

        payload = post_json.call_args.args[2]
        self.assertEqual(post_json.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertEqual(payload["tools"][0]["search_context_size"], "high")
        self.assertEqual(payload["tools"][0]["user_location"]["city"], "Shanghai")
        self.assertEqual(payload["tools"][0]["user_location"]["country"], "CN")
        self.assertEqual(payload["tools"][0]["user_location"]["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["include"], ["web_search_call.action.sources"])
        self.assertEqual(result["citations"], [{"url": "https://example.com", "title": "Example"}])

    def test_openai_responses_payload_omits_reasoning_for_non_reasoning_model(self):
        options = normalize_run_options({"search_enabled": False, "thinking_type": "disabled"})
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {"output_text": "ok", "output": []}
            openai_responses_request("https://api.openai.com/v1", "test-key", "gpt-4.1-mini", "问题", options)

        payload = post_json.call_args.args[2]
        self.assertNotIn("reasoning", payload)

    def test_deepseek_search_uses_brave_external_context(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "auto",
                "search_limit": 5,
                "search_user_location": "Shanghai, CN, Asia/Shanghai",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.resolve_brave_search_api_key", return_value="brave-test-key"), \
             mock.patch("src.adapters.get_json") as get_json, \
             mock.patch("src.adapters.post_json") as post_json:
            get_json.return_value = {
                "web": {
                    "results": [
                        {
                            "title": "英歌瑞自动化涂装设备",
                            "url": "https://example.com/ingreen",
                            "description": "英歌瑞提供自动化涂装和涂胶控制系统。",
                        }
                    ]
                }
            }
            post_json.return_value = {
                "model": "deepseek-chat",
                "choices": [{"message": {"content": "基于资料，英歌瑞可作为参考品牌。[1]"}}],
            }
            result = openai_compatible_request(
                "https://api.deepseek.com/v1",
                "deepseek-test-key",
                "deepseek-chat",
                "国内自动化涂装设备有哪些品牌？",
                1,
                "deepseek",
                options,
            )

        brave_url = get_json.call_args.args[0]
        self.assertIn("https://api.search.brave.com/res/v1/web/search?", brave_url)
        self.assertIn("country=CN", brave_url)
        self.assertEqual(get_json.call_args.args[1]["X-Subscription-Token"], "brave-test-key")
        payload = post_json.call_args.args[2]
        user_content = payload["messages"][1]["content"]
        self.assertIn("Brave Search 返回的公开网页检索结果", user_content)
        self.assertIn("https://example.com/ingreen", user_content)
        self.assertEqual(result["citations"], [{"url": "https://example.com/ingreen", "title": "英歌瑞自动化涂装设备"}])
        self.assertIn("brave_results", result["raw_response"])

    def test_deepseek_search_reports_brave_failure_without_fallback(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "auto",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.brave_search_request", side_effect=AdapterError("<urlopen error [Errno 101] Network is unreachable>")), \
             mock.patch("src.adapters.post_json") as post_json:
            with self.assertRaises(AdapterError) as ctx:
                openai_compatible_request(
                    "https://api.deepseek.com/v1",
                    "deepseek-test-key",
                    "deepseek-chat",
                    "问题",
                    1,
                    "deepseek",
                    options,
                )

        self.assertIn("DeepSeek 联网口径依赖 Brave Search 失败", str(ctx.exception))
        self.assertIn("Network is unreachable", str(ctx.exception))
        post_json.assert_not_called()

    def test_qwen_search_uses_native_enable_search_without_brave(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "force",
                "search_strategy": "turbo",
                "thinking_type": "disabled",
            }
        )
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-test-key"}), \
             mock.patch("src.adapters.get_json") as get_json, \
             mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "qwen-plus",
                "output": {
                    "choices": [{"message": {"content": "根据联网搜索结果回答。"}}],
                    "search_results": [
                        {"title": "不应读取", "url": "https://example.invalid"}
                    ],
                    "search_info": {
                        "search_results": [
                            {"title": "阿里云联网搜索", "url": "https://help.aliyun.com/zh/model-studio/web-search/"}
                        ]
                    },
                },
            }
            result = openai_compatible_request(
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen-test-key",
                "qwen-plus",
                "OpenRouter Web Search 支持哪些 engine？",
                0.1,
                "qwen",
                options,
            )

        self.assertEqual(
            post_json.call_args.args[0],
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        )
        payload = post_json.call_args.args[2]
        get_json.assert_not_called()
        self.assertEqual(payload["input"]["messages"][1]["content"], "OpenRouter Web Search 支持哪些 engine？")
        self.assertTrue(payload["parameters"]["enable_search"])
        self.assertEqual(payload["parameters"]["search_options"]["forced_search"], True)
        self.assertEqual(payload["parameters"]["search_options"]["search_strategy"], "turbo")
        self.assertTrue(payload["parameters"]["search_options"]["enable_source"])
        self.assertTrue(payload["parameters"]["search_options"]["enable_citation"])
        self.assertEqual(payload["parameters"]["search_options"]["citation_format"], "[ref_<number>]")
        self.assertEqual(
            result["citations"],
            [{"url": "https://help.aliyun.com/zh/model-studio/web-search/", "title": "阿里云联网搜索"}],
        )
        self.assertNotIn("brave_results", result["raw_response"])

    def test_ernie_search_uses_native_enable_search_without_brave(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "force",
                "thinking_type": "disabled",
            }
        )
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-test-key"}), \
             mock.patch("src.adapters.get_json") as get_json, \
             mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "ernie-4.5-turbo-32k",
                "search_results": [
                    {"title": "百度智能云联网搜索", "url": "https://cloud.baidu.com/doc/qianfan-docs/s/Wm8r4sw29"}
                ],
                "choices": [
                    {
                        "message": {
                            "content": "根据联网搜索结果回答。",
                        }
                    }
                ],
            }
            result = openai_compatible_request(
                "https://qianfan.baidubce.com/v2",
                "ernie-test-key",
                "ernie-4.5-turbo-32k",
                "联网搜索如何开启？",
                0.1,
                "ernie",
                options,
            )

        payload = post_json.call_args.args[2]
        get_json.assert_not_called()
        self.assertEqual(payload["messages"][1]["content"], "联网搜索如何开启？")
        self.assertNotIn("enable_search", payload)
        self.assertEqual(
            payload["web_search"],
            {
                "enable": True,
                "enable_trace": True,
                "enable_citation": True,
                "search_mode": "auto",
                "search_number": 10,
                "reference_number": 5,
            },
        )
        self.assertEqual(
            result["citations"],
            [{"url": "https://cloud.baidu.com/doc/qianfan-docs/s/Wm8r4sw29", "title": "百度智能云联网搜索"}],
        )
        self.assertNotIn("brave_results", result["raw_response"])

    def test_openrouter_online_payload_uses_web_plugin_and_citations(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_limit": 3,
                "search_strategy": "native",
                "search_site_filter": "example.com,industry.example",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "openai/gpt-5.2",
                "choices": [
                    {
                        "message": {
                            "content": "联网回答",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://example.com/source",
                                        "title": "Source Title",
                                    },
                                }
                            ],
                        }
                    }
                ],
            }
            result = openai_compatible_request(
                "https://openrouter.ai/api/v1",
                "openrouter-test-key",
                "openai/gpt-5.2",
                "测试问题",
                1,
                "openrouter_gpt",
                options,
            )

        self.assertEqual(post_json.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(post_json.call_args.args[1]["Authorization"], "Bearer openrouter-test-key")
        payload = post_json.call_args.args[2]
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["plugins"], [{"id": "web", "max_results": 3, "engine": "native", "include_domains": ["example.com", "industry.example"]}])
        self.assertEqual(result["citations"], [{"url": "https://example.com/source", "title": "Source Title"}])

    def test_openrouter_length_finish_reason_is_not_success(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "force",
                "search_strategy": "native",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "openai/gpt-5.2",
                "choices": [
                    {
                        "finish_reason": "length",
                        "native_finish_reason": "max_output_tokens",
                        "message": {"role": "assistant", "content": "被截断的回答"},
                    }
                ],
            }
            with self.assertRaises(AdapterError):
                openai_compatible_request(
                    "https://openrouter.ai/api/v1",
                    "openrouter-test-key",
                    "openai/gpt-5.2",
                    "测试问题",
                    1,
                    "openrouter_gpt",
                    options,
                )

    def test_hunyuan_search_payload_forces_search_and_url_metadata(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "search_mode": "force",
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "hunyuan-turbos-latest",
                "choices": [{"message": {"content": "联网回答"}}],
                "search_info": {
                    "search_results": [
                        {"url": "https://example.com/hunyuan", "title": "混元来源"},
                    ]
                },
            }
            result = openai_compatible_request(
                "https://api.hunyuan.cloud.tencent.com/v1",
                "hunyuan-test-key",
                "hunyuan-turbos-latest",
                "测试问题",
                0,
                "hunyuan",
                options,
            )

        payload = post_json.call_args.args[2]
        self.assertTrue(payload["enable_enhancement"])
        self.assertTrue(payload["force_search_enhancement"])
        self.assertTrue(payload["search_info"])
        self.assertTrue(payload["citation"])
        self.assertEqual(result["citations"], [{"url": "https://example.com/hunyuan", "title": "混元来源"}])

    def test_kimi_search_payload_uses_builtin_web_search_tool(self):
        options = normalize_run_options(
            {
                "search_enabled": True,
                "thinking_type": "disabled",
            }
        )
        with mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {
                "model": "kimi-k2.5",
                "choices": [{"message": {"content": "联网回答"}}],
            }
            kimi_search_request(
                "https://api.moonshot.cn/v1",
                "kimi-test-key",
                "kimi-k2.5",
                "测试问题",
                0.6,
                options,
            )

        payload = post_json.call_args.args[2]
        self.assertEqual(payload["tools"][0]["type"], "builtin_function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "$web_search")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_openrouter_gpt_wrapper_uses_direct_openai_fallback_when_key_exists(self):
        with mock.patch.dict(
            os.environ,
            {
                "ALLOW_LIVE_MODEL_CALLS": "1",
                "OPENROUTER_DIRECT_FALLBACK": "1",
                "OPENAI_API_KEY": "openai-direct-key",
                "OPENROUTER_GPT_FALLBACK_MODEL": "gpt-4.1-mini",
            },
        ), mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {"output_text": "ok", "output": []}
            result = call_configured_model(
                {
                    "provider": "openrouter_gpt",
                    "api_base": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-5.2",
                },
                "测试问题",
                True,
                1,
                {"search_mode": "auto", "thinking_type": "disabled"},
            )

        self.assertEqual(post_json.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(post_json.call_args.args[1]["Authorization"], "Bearer openai-direct-key")
        self.assertEqual(post_json.call_args.args[2]["model"], "gpt-4.1-mini")
        self.assertEqual(result["provider"], "openrouter_gpt")
        self.assertEqual(result["model"], "gpt-4.1-mini")

    def test_openrouter_wrapper_prefers_openrouter_key_by_default(self):
        with mock.patch.dict(
            os.environ,
            {
                "ALLOW_LIVE_MODEL_CALLS": "1",
                "OPENROUTER_API_KEY": "openrouter-key",
                "OPENAI_API_KEY": "openai-direct-key",
            },
            clear=False,
        ), mock.patch("src.adapters.post_json") as post_json:
            os.environ.pop("OPENROUTER_DIRECT_FALLBACK", None)
            post_json.return_value = {"model": "openai/gpt-5.2", "choices": [{"message": {"content": "ok"}}]}
            result = call_configured_model(
                {
                    "provider": "openrouter_gpt",
                    "api_base": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-5.2",
                },
                "测试问题",
                True,
                1,
                {"search_mode": "auto", "thinking_type": "disabled"},
            )

        self.assertEqual(post_json.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(post_json.call_args.args[1]["Authorization"], "Bearer openrouter-key")
        self.assertEqual(result["provider"], "openrouter_gpt")

    def test_openrouter_gemini_wrapper_uses_direct_gemini_fallback_when_key_exists(self):
        with mock.patch.dict(
            os.environ,
            {
                "ALLOW_LIVE_MODEL_CALLS": "1",
                "OPENROUTER_DIRECT_FALLBACK": "1",
                "GEMINI_API_KEY": "gemini-direct-key",
                "OPENROUTER_GEMINI_FALLBACK_MODEL": "gemini-2.5-flash",
            },
        ), mock.patch("src.adapters.post_json") as post_json:
            post_json.return_value = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}], "modelVersion": "gemini-2.5-flash"}
            result = call_configured_model(
                {
                    "provider": "openrouter_gemini",
                    "api_base": "https://openrouter.ai/api/v1",
                    "model": "google/gemini-2.5-flash",
                },
                "测试问题",
                True,
                1,
                {"search_mode": "auto", "thinking_type": "disabled"},
            )

        self.assertEqual(post_json.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertEqual(post_json.call_args.args[1]["x-goog-api-key"], "gemini-direct-key")
        self.assertEqual(result["provider"], "openrouter_gemini")
        self.assertEqual(result["model"], "gemini-2.5-flash")

    def test_customer_excel_export_uses_template_columns_and_suffixes_internal_columns(self):
        body = runs_to_excel_html(
            [
                {
                    "run_id": "run-test",
                    "batch_id": "batch-test",
                    "source_question_id": "Q001",
                    "question": "测试问题",
                    "question_type": "品牌推荐",
                    "product_category": "产品类型A",
                    "product_line": "FDS",
                    "purchase_stage": "认知阶段",
                    "scenario": "汽车焊装/轻量化连接",
                    "question_priority": "高",
                    "suggested_platforms": "ChatGPT; DeepSeek",
                    "import_row_json": json.dumps(
                        {
                            "问题ID": "Q001",
                            "问题内容": "测试问题",
                            "问题类型": "品牌推荐",
                            "产品线": "FDS",
                            "平台": "ChatGPT",
                            "品牌名是否出现": "待分析",
                            "品牌名出现名称": "",
                            "推荐排名": "",
                            "是否Top3": "",
                            "官网域名类型": "",
                            "竞品出现": "",
                        },
                        ensure_ascii=False,
                    ),
                    "provider": "doubao",
                    "model": "doubao-seed-2-0-mini-260428",
                    "search_enabled": True,
                    "search_mode": "auto",
                    "thinking_type": "disabled",
                    "reasoning_effort": "",
                    "thinking_budget": "",
                    "repeat_index": 1,
                    "requested_at": "2026-07-04T00:00:00+00:00",
                    "status": "success",
                    "target_brand_mentioned": True,
                    "recommendation_strength": "未提及",
                    "competitors_mentioned": "竞品A",
                    "owned_site_cited": False,
                    "third_party_cited": True,
                    "risk_level": "低",
                    "response_text": "回答内容",
                    "citations_json": json.dumps([
                        {"url": "https://example.com/a", "title": "A"},
                        {"uri": "https://example.com/b", "title": "B"},
                    ]),
                    "raw_response_json": json.dumps({"answer": "回答内容"}, ensure_ascii=False),
                    "error_message": "",
                }
            ]
        )
        expected_order = ["问题ID", "问题内容", "问题类型", "产品线", "平台", "回答原文", "品牌名是否出现", "品牌名出现名称", "推荐排名", "是否Top3", "官网域名类型", "竞品出现", "引用来源", "原始数据", "测试时间", "运行ID（内部信息）", "批次ID（内部信息）", "测试平台（内部信息）", "联网搜索（内部信息）", "状态（内部信息）", "耗时（内部信息）", "错误信息（内部信息）"]
        self.assertLess(body.index("平台"), body.index("回答原文"))
        self.assertLess(body.index("回答原文"), body.index("品牌名是否出现"))
        self.assertLess(body.index("测试时间"), body.index("运行ID（内部信息）"))
        for header in expected_order:
            self.assertIn(header, body)
        for value in ["Q001", "FDS", "豆包", "待分析", "回答内容", "https://example.com/a; https://example.com/b", "{&quot;answer&quot;:&quot;回答内容&quot;}"]:
            self.assertIn(value, body)
        self.assertNotIn("<td>ChatGPT</td>", body)
        self.assertIn("测试平台（内部信息）", body)
        self.assertIn("豆包", body)
        self.assertIn("doubao-seed-2-0-mini-260428", body)
        for internal_header in ["思考模式（内部信息）", "推理强度（内部信息）", "思考预算（内部信息）", "重复次数（内部信息）", "推荐强度（内部信息）", "竞品共现（内部信息）", "官网引用（内部信息）", "第三方引用（内部信息）", "风险等级（内部信息）"]:
            self.assertIn(internal_header, body)
        csv_body = runs_to_csv([
            {
                "run_id": "run-test",
                "batch_id": "batch-test",
                "source_question_id": "Q001",
                "question": "测试问题",
                "response_text": "回答内容",
                "citations_json": json.dumps([{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]),
                "question_type": "品牌推荐",
                "product_category": "产品类型A",
                "product_line": "FDS",
                "purchase_stage": "认知阶段",
                "scenario": "汽车焊装/轻量化连接",
                "question_priority": "高",
                "suggested_platforms": "ChatGPT; DeepSeek",
                "import_row_json": json.dumps({"问题ID": "Q001", "问题内容": "测试问题", "问题类型": "品牌推荐", "产品线": "FDS", "平台": "ChatGPT"}, ensure_ascii=False),
                "provider": "doubao",
                "model": "doubao-seed-2-0-mini-260428",
                "search_enabled": True,
                "requested_at": "2026-07-04T00:00:00+00:00",
                "status": "success",
                "latency_ms": 123,
                "raw_response_json": {"answer": "回答内容"},
                "error_message": "",
            }
        ])
        self.assertTrue(csv_body.splitlines()[0].startswith("问题ID,问题内容,问题类型,产品线,平台,回答原文,品牌名是否出现"))
        self.assertIn("回答内容", csv_body)
        self.assertIn("https://example.com/a; https://example.com/b", csv_body)
        self.assertIn('"{""answer"":""回答内容""}"', csv_body)
        self.assertIn(",豆包,", csv_body)
        self.assertNotIn(",ChatGPT,", csv_body)

        routed = runs_to_excel_html(
            [
                {
                    "run_id": "run-openrouter",
                    "batch_id": "batch-test",
                    "question": "测试问题",
                    "question_type": "品牌推荐",
                    "provider": "openrouter_gpt",
                    "model": "openai/gpt-5.2",
                    "search_enabled": True,
                    "requested_at": "2026-07-04T00:00:00+00:00",
                    "status": "success",
                    "target_brand_mentioned": False,
                    "response_text": "回答内容",
                    "error_message": "",
                },
                {
                    "run_id": "run-gemini",
                    "batch_id": "batch-test",
                    "question": "测试问题",
                    "question_type": "品牌推荐",
                    "provider": "openrouter_gemini",
                    "model": "google/gemini-2.5-flash",
                    "search_enabled": True,
                    "requested_at": "2026-07-04T00:00:00+00:00",
                    "status": "success",
                    "target_brand_mentioned": False,
                    "response_text": "回答内容",
                    "error_message": "",
                },
            ]
        )
        self.assertIn("OpenRouter-GPT", routed)
        self.assertIn("OpenRouter-Gemini", routed)
        self.assertIn("模型（内部信息）", routed)
        self.assertIn("openai/gpt-5.2", routed)
        self.assertIn("google/gemini-2.5-flash", routed)

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
        self.assertIn("source_statuses", status)
        self.assertEqual(len(status["source_statuses"]), 1)
        source = status["source_statuses"][0]
        self.assertEqual(source["test_platform"], "mock-model")
        self.assertEqual(source["total"], 2)
        self.assertEqual(source["completed"], 2)
        self.assertEqual(source["success"], 2)
        self.assertEqual(source["failed"], 0)
        self.assertEqual(source["queued"], 0)
        self.assertEqual(source["running"], 0)
        self.assertEqual(source["status"], "completed")
        batches = self.request_json("GET", f"/api/batches?project_id={project_id}")["batches"]
        batch = next(item for item in batches if item["batch_id"] == batch_id)
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["success_count"] + batch["failed_count"], batch["total_count"])
        batch_detail = self.request_json("GET", f"/api/batches/{batch_id}")["batch"]
        self.assertEqual(batch_detail["completed_count"], 2)
        batch_runs = self.request_json("GET", f"/api/batches/{batch_id}/runs")["runs"]
        self.assertEqual(len(batch_runs), 2)
        self.assertTrue(all("test_platform" in row for row in batch_runs))
        self.assertIn("<table", self.request_text(f"/api/export/batches/{batch_id}/runs.xls"))
        self.assertIn("<table", self.request_text(f"/api/export/batches/{batch_id}/summary.xls"))
        app.SAMPLING_JOBS.clear()
        persisted_status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
        self.assertEqual(persisted_status["status"], "completed")
        self.assertEqual(persisted_status["completed"], 2)
        self.assertEqual(persisted_status["source_statuses"][0]["status"], "completed")
        runs = self.request_json("GET", f"/api/runs?project_id={project_id}")["runs"]
        self.assertEqual(len(runs), 2)
        self.assertIn("运行ID", self.request_text(f"/api/export/runs.csv?project_id={project_id}"))
        self.assertIn("<table", self.request_text(f"/api/export/runs.xls?project_id={project_id}"))

    def test_batch_runs_default_to_latest_logical_records(self):
        project_id, model_id = self.create_mock_project(question_count=1)
        with get_conn(self.db_path) as conn:
            question = list_questions(conn, project_id)[0]
            batch_id = "batch-latest-runs"
            create_sampling_batch(
                conn,
                {
                    "batch_id": batch_id,
                    "project_id": project_id,
                    "status": "completed",
                    "total_count": 1,
                    "success_count": 1,
                    "failed_count": 0,
                    "completed_count": 1,
                    "config": {"models": [{"model_config_id": model_id, "search_enabled": True}], "repeat_count": 1},
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
            base = {
                "batch_id": batch_id,
                "project_id": project_id,
                "question_id": question["id"],
                "model_config_id": model_id,
                "provider": "mock",
                "model": "mock-model",
                "model_version": "mock-model",
                "search_enabled": True,
                "search_mode": "auto",
                "thinking_type": "disabled",
                "repeat_index": 1,
                "temperature": 0,
                "latency_ms": 1,
                "cost_estimate": 0,
                "raw_response": {},
            }
            insert_run(
                conn,
                {
                    **base,
                    "run_id": "old-failed-run",
                    "requested_at": "2026-07-08T00:00:00+00:00",
                    "response_text": "",
                    "citations": [],
                    "status": "failed",
                    "error_message": "旧失败不应默认展示",
                },
            )
            insert_run(
                conn,
                {
                    **base,
                    "run_id": "new-success-run",
                    "requested_at": "2026-07-08T00:01:00+00:00",
                    "response_text": "后续成功",
                    "citations": [],
                    "status": "success",
                    "error_message": "",
                },
            )

        latest_runs = self.request_json("GET", f"/api/batches/{batch_id}/runs")["runs"]
        history_runs = self.request_json("GET", f"/api/batches/{batch_id}/runs?history=1")["runs"]
        self.assertEqual([row["run_id"] for row in latest_runs], ["new-success-run"])
        self.assertEqual(len(history_runs), 2)
        self.assertTrue(any(row["run_id"] == "old-failed-run" for row in history_runs))

    def test_progress_reports_two_sources_from_batch_config_and_runs(self):
        project_id, first_model_id = self.create_mock_project(question_count=2)
        second_model = self.request_json(
            "POST",
            "/api/models",
            {
                "provider": "mock",
                "label": "Mock Two",
                "model": "mock-model-2",
                "supports_pure": True,
                "supports_search": True,
                "active": True,
            },
        )
        started = self.request_json(
            "POST",
            "/api/runs/start",
            {
                "project_id": project_id,
                "models": [
                    {"model_config_id": first_model_id, "search_enabled": True},
                    {"model_config_id": int(second_model["id"]), "search_enabled": False},
                ],
                "repeat_count": 1,
            },
        )
        batch_id = started["batch_id"]
        status = {}
        for _ in range(50):
            status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
            if status.get("status") == "completed":
                break
            time.sleep(0.1)
        self.assertEqual(status["status"], "completed")
        sources = {item["test_platform"]: item for item in status["source_statuses"]}
        self.assertEqual(set(sources), {"mock-model", "mock-model-2"})
        self.assertEqual(sources["mock-model"]["total"], 2)
        self.assertEqual(sources["mock-model-2"]["total"], 2)
        self.assertEqual(sources["mock-model"]["completed"], 2)
        self.assertEqual(sources["mock-model-2"]["completed"], 2)
        self.assertEqual(sources["mock-model"]["status"], "completed")
        self.assertEqual(sources["mock-model-2"]["status"], "completed")

    def test_progress_uses_latest_runs_after_rerun_counts_overwrite_batch_totals(self):
        project_id, model_id = self.create_mock_project(question_count=2)
        started = self.request_json(
            "POST",
            "/api/runs/start",
            {
                "project_id": project_id,
                "models": [{"model_config_id": model_id, "search_enabled": True}],
                "repeat_count": 1,
            },
        )
        batch_id = started["batch_id"]
        for _ in range(50):
            status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
            if status.get("status") == "completed":
                break
            time.sleep(0.1)

        with get_conn(self.db_path) as conn:
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "total_count": 1,
                    "completed_count": 1,
                    "success_count": 0,
                    "failed_count": 1,
                },
            )
            conn.commit()
        app.SAMPLING_JOBS.clear()
        imported = self.request_json(
            "POST",
            "/api/questions/import_rows",
            {
                "project_id": project_id,
                "rows": [
                    {
                        "问题ID": "T003",
                        "问题内容": "第 3 个测试问题，测试品牌表现如何？",
                        "问题类型": "brand_direct",
                        "target_brand": "测试品牌",
                        "competitor_brands": "竞品A;竞品B",
                    }
                ],
            },
        )
        self.assertEqual(imported["count"], 1)

        status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
        detail = self.request_json("GET", f"/api/batches/{batch_id}")["batch"]
        self.assertEqual(detail["status"], "queued")
        self.assertEqual(detail["total"], 3)
        self.assertEqual(detail["completed"], 2)
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["total"], 3)
        self.assertEqual(status["completed"], 2)
        self.assertEqual(status["success"], 2)
        self.assertEqual(status["failed"], 0)
        self.assertEqual(status["source_statuses"][0]["total"], 3)
        self.assertEqual(status["source_statuses"][0]["completed"], 2)
        self.assertEqual(status["source_statuses"][0]["queued"], 1)

        with get_conn(self.db_path) as conn:
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "failed",
                    "error_message": "RQ job timed out",
                },
            )
            conn.commit()
        failed_status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
        self.assertEqual(failed_status["status"], "failed")
        self.assertEqual(failed_status["total"], 3)
        self.assertEqual(failed_status["completed"], 2)
        self.assertEqual(failed_status["source_statuses"][0]["total"], 3)
        self.assertEqual(failed_status["source_statuses"][0]["queued"], 1)

        with get_conn(self.db_path) as conn:
            update_sampling_batch(
                conn,
                batch_id,
                {
                    "status": "running",
                    "failed_count": 1,
                    "completed_count": 2,
                },
            )
            conn.commit()
        running_status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
        self.assertEqual(running_status["status"], "running")

    def test_analytics_summary_reports_visibility_and_quality(self):
        project_id, model_id = self.create_mock_project(question_count=2)
        started = self.request_json(
            "POST",
            "/api/runs/start",
            {
                "project_id": project_id,
                "models": [{"model_config_id": model_id, "search_enabled": True}],
                "repeat_count": 1,
            },
        )
        batch_id = started["batch_id"]
        for _ in range(50):
            status = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
            if status.get("status") == "completed":
                break
            time.sleep(0.1)

        summary = self.request_json("GET", f"/api/analytics/summary?project_id={project_id}")
        self.assertEqual(summary["meta"]["scope"], "project")
        self.assertEqual(summary["sample_quality"]["planned"], 2)
        self.assertEqual(summary["sample_quality"]["valid"], 2)
        self.assertEqual(summary["sample_quality"]["failed"], 0)
        self.assertEqual(summary["visibility"]["mention_rate"], 100)
        self.assertEqual(summary["visibility"]["top3_rate"], 100)
        self.assertEqual(summary["source_analysis"]["owned_citation_rate"], 0)
        self.assertTrue(summary["provider_breakdown"])
        self.assertTrue(summary["recommendations"])

        batch_summary = self.request_json("GET", f"/api/analytics/summary?project_id={project_id}&batch_id={batch_id}")
        self.assertEqual(batch_summary["meta"]["scope"], "batch")
        self.assertEqual(batch_summary["meta"]["batch_id"], batch_id)
        self.assertEqual(batch_summary["sample_quality"]["planned"], 2)

    def test_rq_backend_enqueues_without_inline_thread(self):
        project_id, model_id = self.create_mock_project(question_count=1)
        old_backend = os.environ.get("TASK_QUEUE_BACKEND")
        os.environ["TASK_QUEUE_BACKEND"] = "rq"
        try:
            with mock.patch("app.enqueue_rq_task", return_value="job-test-id") as enqueue:
                started = self.request_json(
                    "POST",
                    "/api/runs/start",
                    {
                        "project_id": project_id,
                        "models": [{"model_config_id": model_id, "search_enabled": False}],
                        "repeat_count": 1,
                    },
                )
            self.assertEqual(started["task_queue_backend"], "rq")
            self.assertEqual(started["job_id"], "job-test-id")
            enqueue.assert_called_once()
            persisted_status = self.request_json("GET", f"/api/runs/progress?batch_id={started['batch_id']}")
            self.assertEqual(persisted_status["status"], "queued")
            self.assertEqual(persisted_status["source_statuses"][0]["status"], "queued")
            self.assertEqual(persisted_status["source_statuses"][0]["total"], 1)
        finally:
            if old_backend is None:
                os.environ.pop("TASK_QUEUE_BACKEND", None)
            else:
                os.environ["TASK_QUEUE_BACKEND"] = old_backend

    def test_resume_pause_requested_cancels_pause_without_reenqueue(self):
        project_id, model_id = self.create_mock_project(question_count=1)
        batch_id = "pause-requested-test"
        with get_conn(app.DEFAULT_DB_PATH) as conn:
            create_sampling_batch(
                conn,
                {
                    "batch_id": batch_id,
                    "project_id": project_id,
                    "status": "pause_requested",
                    "total_count": 1,
                    "completed_count": 0,
                    "success_count": 0,
                    "failed_count": 0,
                    "config": {"project_id": project_id, "repeat_count": 1, "models": [{"model_config_id": model_id, "search_enabled": False}]},
                },
            )
            conn.commit()
        with mock.patch("app.dispatch_resume_batch") as dispatch:
            response = self.request_json("POST", f"/api/batches/{batch_id}/resume", {})
        self.assertEqual(response["status"], "running")
        dispatch.assert_not_called()
        persisted = self.request_json("GET", f"/api/runs/progress?batch_id={batch_id}")
        self.assertEqual(persisted["status"], "running")


class HarnessDirectTests(unittest.TestCase):
    def test_normalize_choice_text_does_not_use_reasoning_as_answer(self):
        text = normalize_choice_text(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning": "模型把文本放在 reasoning 字段里。",
                        },
                    }
                ]
            }
        )
        self.assertEqual(text, "")

    def test_mark_batch_failed_preserves_persisted_progress_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "failed-progress.db"
            init_db(db_path)
            with get_conn(db_path) as conn:
                project_id = create_project(
                    conn,
                    {
                        "client_name": "失败收尾客户",
                        "brand_name": "失败收尾品牌",
                        "product_category": "测试品类",
                    },
                )
                rows = [
                    {
                        "问题ID": "F001",
                        "问题内容": "失败收尾品牌是否出现？",
                        "问题类型": "failure",
                    },
                    {
                        "问题ID": "F002",
                        "问题内容": "失败收尾品牌是否值得推荐？",
                        "问题类型": "failure",
                    },
                ]
                import_questions_rows(conn, project_id, rows)
                question_id = list_questions(conn, project_id)[0]["id"]
                model_id = create_model_config(
                    conn,
                    {
                        "provider": "mock",
                        "label": "Mock",
                        "model": "mock-success",
                        "supports_pure": True,
                        "active": True,
                    },
                )
                create_sampling_batch(
                    conn,
                    {
                        "batch_id": "failed-progress-batch",
                        "project_id": project_id,
                        "status": "running",
                        "total_count": 2,
                        "completed_count": 1,
                        "success_count": 1,
                        "failed_count": 0,
                        "config": {"project_id": project_id, "repeat_count": 1, "models": [{"model_config_id": model_id, "search_enabled": False}]},
                    },
                )
                insert_run(
                    conn,
                    {
                        "run_id": "run-failed-progress-1",
                        "batch_id": "failed-progress-batch",
                        "project_id": project_id,
                        "question_id": question_id,
                        "question_text": "失败收尾品牌是否出现？",
                        "provider": "mock",
                        "model": "mock-success",
                        "model_config_id": model_id,
                        "status": "success",
                        "requested_at": utc_now(),
                        "answer_text": "失败收尾品牌出现了。",
                        "response_text": "失败收尾品牌出现了。",
                        "latency_ms": 10,
                    },
                )
                conn.commit()
            mark_batch_failed("failed-progress-batch", "RQ job timed out", db_target=db_path)
            with get_conn(db_path) as conn:
                batch = conn.execute("SELECT status, total_count, completed_count, success_count, failed_count, error_message FROM sampling_batches WHERE batch_id = ?", ("failed-progress-batch",)).fetchone()
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(batch["total_count"], 2)
        self.assertEqual(batch["completed_count"], 1)
        self.assertEqual(batch["success_count"], 1)
        self.assertEqual(batch["failed_count"], 0)
        self.assertIn("timed out", batch["error_message"])

    def test_import_question_content_rows_ignores_other_table_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "questions.db"
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
                        "问题ID": "Q001",
                        "问题内容": "汽车白车身多材料连接有哪些 FDS 热熔螺接设备品牌值得推荐？",
                        "问题类型": "品牌推荐",
                        "产品线": "FDS",
                        "平台": "ChatGPT",
                        "回答原文": "这段回答不应导入",
                    },
                    {
                        "问题ID": "Q002",
                        "问题内容": "新能源电池 PACK 装配有哪些 FDS 热熔螺接设备品牌值得推荐？",
                        "问题类型": "品牌推荐",
                        "产品线": "FDS",
                        "平台": "DeepSeek",
                        "回答原文": "这段回答也不应导入",
                    },
                ]
                self.assertEqual(import_question_content_rows(conn, project_id, rows), 2)
                questions = list_questions(conn, project_id)
        raw = json.dumps(questions, ensure_ascii=False)
        self.assertIn("汽车白车身多材料连接", raw)
        self.assertIn("新能源电池 PACK 装配", raw)
        self.assertNotIn("这段回答不应导入", raw)
        self.assertTrue(all(item["question_type"] == "品牌推荐" for item in questions))
        self.assertTrue(all(item["product_line"] == "FDS" for item in questions))
        self.assertIn("DeepSeek", raw)

    def test_provider_concurrency_limits_use_shared_source_groups(self):
        old_limits = os.environ.get("SAMPLING_PROVIDER_CONCURRENCY_LIMITS")
        try:
            os.environ.pop("SAMPLING_PROVIDER_CONCURRENCY_LIMITS", None)
            self.assertEqual(provider_concurrency_group("openrouter_gpt"), "openrouter")
            self.assertEqual(provider_concurrency_group("openrouter_gemini"), "openrouter")
            self.assertEqual(provider_concurrency_limit("openrouter_gpt"), 4)
            self.assertEqual(provider_concurrency_limit("openrouter_gemini"), 4)
            self.assertEqual(provider_concurrency_limit("deepseek"), 1)
            self.assertEqual(provider_concurrency_limit("doubao"), 2)
            self.assertEqual(provider_concurrency_limit("qwen"), 1)
            self.assertEqual(provider_concurrency_limit("hunyuan"), 2)
            self.assertEqual(provider_concurrency_limit("ernie"), 1)
            self.assertEqual(provider_concurrency_limit("mock"), 16)

            os.environ["SAMPLING_PROVIDER_CONCURRENCY_LIMITS"] = "openrouter=1,deepseek=2"
            self.assertEqual(provider_concurrency_limit("openrouter_gpt"), 1)
            self.assertEqual(provider_concurrency_limit("openrouter_gemini"), 1)
            self.assertEqual(provider_concurrency_limit("deepseek"), 2)
        finally:
            if old_limits is None:
                os.environ.pop("SAMPLING_PROVIDER_CONCURRENCY_LIMITS", None)
            else:
                os.environ["SAMPLING_PROVIDER_CONCURRENCY_LIMITS"] = old_limits

    def test_openrouter_runtime_tasks_default_to_exa_search_strategy(self):
        question = {"id": 1, "question": "今天 OpenRouter Web Search 支持哪些 engine？"}
        config = {"search_mode": "auto", "thinking_type": "disabled"}
        for provider, model in [
            ("openrouter_gpt", "openai/gpt-5.2"),
            ("openrouter_gemini", "google/gemini-2.5-flash"),
        ]:
            task = prepare_runtime_task(
                batch_id="batch-test",
                project_id=1,
                question=question,
                provider_cfg={"model_config_id": 1, "search_enabled": True},
                model_config={
                    "id": 1,
                    "provider": provider,
                    "model": model,
                    "model_version": "",
                },
                config=config,
                repeat_index=1,
            )

            self.assertEqual(task["run_options"]["search_strategy"], "exa")

    def test_hunyuan_runtime_tasks_default_to_force_search_mode(self):
        task = prepare_runtime_task(
            batch_id="batch-test",
            project_id=1,
            question={"id": 1, "question": "今天有什么制造业新闻？"},
            provider_cfg={"model_config_id": 1, "search_enabled": True},
            model_config={
                "id": 1,
                "provider": "hunyuan",
                "model": "hunyuan-turbos-latest",
                "model_version": "",
            },
            config={"search_mode": "auto", "thinking_type": "disabled"},
            repeat_index=1,
        )

        self.assertEqual(task["base"]["search_mode"], "force")
        self.assertEqual(task["run_options"]["search_mode"], "force")

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
                        "问题ID": f"L{idx:03d}",
                        "问题内容": f"第 {idx} 个本地负载问题，测试品牌是否出现？",
                        "问题类型": "load",
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

    def test_run_batch_stops_submitting_new_tasks_when_pause_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pause.db"
            init_db(db_path)
            pause_requested = False
            with get_conn(db_path) as conn:
                project_id = create_project(
                    conn,
                    {
                        "client_name": "暂停测试客户",
                        "brand_name": "暂停测试品牌",
                        "product_category": "测试品类",
                    },
                )
                rows = [
                    {
                        "问题ID": f"P{idx:03d}",
                        "问题内容": f"第 {idx} 个暂停测试问题，暂停测试品牌是否出现？",
                        "问题类型": "pause",
                        "target_brand": "暂停测试品牌",
                    }
                    for idx in range(1, 4)
                ]
                self.assertEqual(import_questions_rows(conn, project_id, rows), 3)
                model_id = create_model_config(
                    conn,
                    {
                        "provider": "mock",
                        "label": "Mock",
                        "model": "mock-model",
                        "supports_pure": True,
                        "active": True,
                    },
                )

                def on_progress(_progress):
                    nonlocal pause_requested
                    pause_requested = True

                result = run_batch(
                    conn,
                    project_id,
                    {
                        "models": [{"model_config_id": model_id, "search_enabled": False}],
                        "repeat_count": 1,
                        "max_workers": 1,
                    },
                    batch_id="pause-test-batch",
                    progress_callback=on_progress,
                    should_pause=lambda: pause_requested,
                )
                runs = list_runs(conn, project_id, limit=10)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["total"], 1)
            self.assertEqual(len(runs), 1)

    def test_one_model_failure_does_not_stop_batch(self):
        old_value = os.environ.pop("ALLOW_LIVE_MODEL_CALLS", None)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = Path(temp_dir) / "mixed.db"
                init_db(db_path)
                with get_conn(db_path) as conn:
                    project_id = create_project(
                        conn,
                        {
                            "client_name": "混合测试客户",
                            "brand_name": "混合测试品牌",
                            "product_category": "测试品类",
                        },
                    )
                    rows = [
                        {
                            "问题ID": f"M{idx:03d}",
                            "问题内容": f"第 {idx} 个混合批次问题，混合测试品牌是否出现？",
                            "问题类型": "mixed",
                            "target_brand": "混合测试品牌",
                        }
                        for idx in range(1, 3)
                    ]
                    self.assertEqual(import_questions_rows(conn, project_id, rows), 2)
                    mock_model_id = create_model_config(
                        conn,
                        {
                            "provider": "mock",
                            "label": "Mock",
                            "model": "mock-model",
                            "supports_pure": True,
                            "active": True,
                        },
                    )
                    blocked_model_id = create_model_config(
                        conn,
                        {
                            "provider": "openai",
                            "label": "Blocked OpenAI",
                            "model": "gpt-4.1",
                            "api_key": "fake-key",
                            "api_base": "https://api.openai.com/v1",
                            "supports_pure": True,
                            "active": True,
                        },
                    )
                    result = run_batch(
                        conn,
                        project_id,
                        {
                            "models": [
                                {"model_config_id": mock_model_id, "search_enabled": False},
                                {"model_config_id": blocked_model_id, "search_enabled": False},
                            ],
                            "repeat_count": 1,
                            "max_workers": 4,
                        },
                        batch_id="mixed-failure-batch",
                    )
                    runs = list_runs(conn, project_id, limit=10)
                self.assertEqual(result["total"], 4)
                self.assertEqual(result["success"], 2)
                self.assertEqual(result["failed"], 2)
                self.assertEqual(len(runs), 4)
        finally:
            if old_value is not None:
                os.environ["ALLOW_LIVE_MODEL_CALLS"] = old_value

    def test_rerun_failed_runs_only_creates_replacement_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "rerun.db"
            init_db(db_path)
            with get_conn(db_path) as conn:
                project_id = create_project(
                    conn,
                    {
                        "client_name": "重跑测试客户",
                        "brand_name": "重跑测试品牌",
                        "product_category": "测试品类",
                    },
                )
                rows = [
                    {
                        "问题ID": f"R{idx:03d}",
                        "问题内容": f"第 {idx} 个重跑问题，重跑测试品牌是否出现？",
                        "问题类型": "rerun",
                        "target_brand": "重跑测试品牌",
                    }
                    for idx in range(1, 3)
                ]
                self.assertEqual(import_questions_rows(conn, project_id, rows), 2)
                model_id = create_model_config(
                    conn,
                    {
                        "provider": "mock",
                        "label": "Mock Fail",
                        "model": "mock-fail",
                        "supports_pure": True,
                        "active": True,
                    },
                )
                result = run_batch(
                    conn,
                    project_id,
                    {
                        "models": [{"model_config_id": model_id, "search_enabled": False}],
                        "repeat_count": 1,
                        "max_workers": 2,
                        "retry_count": 0,
                    },
                    batch_id="rerun-failed-batch",
                )
                self.assertEqual(result["failed"], 2)
                failed_runs = list_failed_runs_by_batch(conn, "rerun-failed-batch")
                self.assertEqual(len(failed_runs), 2)
                self.assertTrue(all(row["error_message"] for row in failed_runs))
                conn.execute("UPDATE model_configs SET model = 'mock-fixed' WHERE id = ?", (model_id,))
                rerun_result = rerun_failed_runs(conn, "rerun-failed-batch", failed_runs, {"max_workers": 2, "retry_count": 0})
                runs = list_runs(conn, project_id, limit=10, include_history=True)
            self.assertEqual(rerun_result["total"], 2)
            self.assertEqual(rerun_result["success"], 2)
            self.assertEqual(rerun_result["failed"], 0)
            self.assertEqual(len(runs), 4)


if __name__ == "__main__":
    unittest.main()
