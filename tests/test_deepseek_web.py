from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.db import (
    claim_sampling_task,
    create_project,
    create_sampling_batch,
    get_conn,
    get_sampling_task,
    init_db,
    list_model_configs,
    list_runs_by_batch,
    list_sampling_tasks,
    reset_resumable_sampling_tasks,
    sampling_task_counts,
    update_sampling_task,
    get_sampling_batch,
)
from src.deepseek_web import (
    DeepSeekWebBrowser,
    DeepSeekWebConfig,
    answers_match,
    extract_chat_id,
    network_answer,
    sanitize_url,
    sanitize_value,
)
from src.deepseek_web_tasks import (
    build_web_sampling_tasks,
    create_web_sampling_tasks,
    enqueue_next_web_task,
    perform_deepseek_web_task,
    web_batch_mode,
)
from src.db import import_questions_text


class DeepSeekWebHelpersTests(unittest.TestCase):
    def test_account_restriction_is_detected_before_composer_lookup(self):
        class FakeBody:
            @staticmethod
            def inner_text(timeout=5000):
                return "由于违反用户使用规范，你的账号已被禁言至 2026 年 7 月 11 日 23:47"

        class FakePage:
            @staticmethod
            def locator(selector):
                self = FakeBody()
                return self

        self.assertEqual(DeepSeekWebBrowser._blocked_page(FakePage()), "account_restricted")
        self.assertIn("账号已被限制", DeepSeekWebBrowser._blocked_message("account_restricted"))

    def test_sensitive_values_are_redacted_recursively(self):
        sanitized = sanitize_value(
            {
                "Authorization": "Bearer secret-token",
                "nested": {"refresh_token": "secret", "body": "token=\"secret\""},
            }
        )
        self.assertEqual(sanitized["Authorization"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["refresh_token"], "[REDACTED]")
        self.assertNotIn("secret", json.dumps(sanitized))

    def test_account_identifiers_are_redacted(self):
        url = sanitize_url("https://chat.deepseek.com/settings?did=device-secret&token=secret")
        sanitized = sanitize_value({"email": "user@example.com", "body": "phone=13800138000"})
        self.assertNotIn("device-secret", url)
        self.assertNotIn("secret", url)
        self.assertEqual(sanitized["email"], "[REDACTED]")
        self.assertNotIn("13800138000", sanitized["body"])

    def test_unrelated_network_response_bodies_are_not_persisted(self):
        class FakeResponse:
            url = "https://chat.deepseek.com/api/v0/users/current"
            headers = {"content-type": "application/json"}
            status = 200

            @staticmethod
            def body():
                return b'{"email":"user@example.com","token":"secret"}'

        events = DeepSeekWebBrowser._network_events([FakeResponse()])
        self.assertEqual(events[0]["body"], "")

    def test_chat_id_and_network_answer_extraction(self):
        events = [{"body": 'data: {"content":"官网联网回答"}\n', "url": "https://chat.deepseek.com/api?chat_session_id=abc-123"}]
        self.assertEqual(extract_chat_id("https://chat.deepseek.com", events), "abc-123")
        self.assertEqual(network_answer(events), "官网联网回答")

    def test_network_answer_reassembles_compact_json_patches(self):
        body = "\n".join(
            [
                'data: {"p":"response","o":"BATCH","v":[{"p":"fragments","o":"APPEND","v":[{"type":"RESPONSE","content":"官网"}]}]}',
                'data: {"p":"response/fragments/-1/content","o":"APPEND","v":"联网"}',
                'data: {"v":"回答"}',
            ]
        )
        self.assertEqual(network_answer([{"body": body}]), "官网联网回答")

    def test_answer_match_allows_missing_network_body_and_rejects_mismatch(self):
        self.assertEqual(answers_match("页面答案", "")[0], True)
        matched, ratio = answers_match("甲" * 100, "乙" * 100)
        self.assertFalse(matched)
        self.assertLess(ratio, 0.1)


class DeepSeekWebTaskTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            self.project_id = create_project(conn, {"client_name": "测试客户", "brand_name": "测试品牌"})
            import_questions_text(conn, self.project_id, "\n".join(f"问题 {index}" for index in range(140)))
            self.web_model = next(item for item in list_model_configs(conn) if item["provider"] == "deepseek_web")

    def tearDown(self):
        self.tempdir.cleanup()

    def payload(self):
        return {
            "project_id": self.project_id,
            "models": [{"model_config_id": self.web_model["id"], "search_enabled": True}],
            "repeat_count": 1,
        }

    def test_builds_140_deterministic_tasks_and_is_idempotent(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_WEB_ENABLED": "1", "TASK_QUEUE_BACKEND": "rq"}, clear=False):
            with get_conn(self.db_path) as conn:
                payload = {**self.payload(), "provider_mode": "deepseek_web"}
                create_sampling_batch(conn, {"batch_id": "batch-web", "project_id": self.project_id, "status": "queued", "total_count": 140, "config": payload})
                tasks = build_web_sampling_tasks(conn, "batch-web", self.project_id, payload)
                self.assertEqual(len(tasks), 140)
                create_web_sampling_tasks(conn, "batch-web", self.project_id, payload)
                create_web_sampling_tasks(conn, "batch-web", self.project_id, payload)
                rows = list_sampling_tasks(conn, "batch-web")
                self.assertEqual(len(rows), 140)
                self.assertEqual(len({row["task_id"] for row in rows}), 140)

    def test_claim_counts_and_reset(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_WEB_ENABLED": "1", "TASK_QUEUE_BACKEND": "rq"}, clear=False):
            with get_conn(self.db_path) as conn:
                payload = {**self.payload(), "provider_mode": "deepseek_web"}
                create_sampling_batch(conn, {"batch_id": "batch-state", "project_id": self.project_id, "status": "queued", "total_count": 140, "config": payload})
                create_web_sampling_tasks(conn, "batch-state", self.project_id, payload)
                task = list_sampling_tasks(conn, "batch-state")[0]
                self.assertTrue(claim_sampling_task(conn, task["task_id"], "test-worker", 60))
                self.assertFalse(claim_sampling_task(conn, task["task_id"], "other-worker", 60))
                update_sampling_task(conn, task["task_id"], {"status": "blocked", "error_code": "captcha"})
                self.assertEqual(sampling_task_counts(conn, "batch-state")["blocked"], 1)
                self.assertEqual(reset_resumable_sampling_tasks(conn, "batch-state"), 1)
                self.assertEqual(sampling_task_counts(conn, "batch-state")["queued"], 140)

    def test_mixed_batch_is_rejected(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_WEB_ENABLED": "1", "TASK_QUEUE_BACKEND": "rq"}, clear=False):
            with get_conn(self.db_path) as conn:
                other = next(item for item in list_model_configs(conn) if item["provider"] == "openai")
                mixed = {
                    "models": [
                        {"model_config_id": self.web_model["id"], "search_enabled": True},
                        {"model_config_id": other["id"], "search_enabled": True},
                    ]
                }
                with self.assertRaisesRegex(ValueError, "独立批次"):
                    web_batch_mode(conn, mixed)

    def test_successful_task_persists_run_citations_and_evaluation(self):
        result = {
            "response_text": "官网联网回答，包含真实引用。",
            "citations": [{"url": "https://example.com/source", "title": "来源"}],
            "chat_id": "chat-persisted-001",
            "network_match_ratio": 0.97,
            "network_answer_available": True,
            "latency_ms": 1234,
            "artifact_dir": "data/deepseek-web-artifacts/batch-persist/task-001",
        }
        env = {
            "DEEPSEEK_WEB_ENABLED": "1",
            "TASK_QUEUE_BACKEND": "rq",
            "DEEPSEEK_WEB_COOLDOWN_SECONDS": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with get_conn(self.db_path) as conn:
                payload = {**self.payload(), "provider_mode": "deepseek_web"}
                create_sampling_batch(
                    conn,
                    {
                        "batch_id": "batch-persist",
                        "project_id": self.project_id,
                        "status": "queued",
                        "total_count": 140,
                        "config": payload,
                    },
                )
                create_web_sampling_tasks(conn, "batch-persist", self.project_id, payload)
                original_task = list_sampling_tasks(conn, "batch-persist")[0]
                task_id = original_task["task_id"]
                original_question = original_task["question"]
                original_model = self.web_model["model"]
                self.assertEqual(get_sampling_task(conn, task_id)["task_snapshot"]["task"]["model"], original_model)
                conn.execute("UPDATE questions SET question = '后来修改的网页问题' WHERE project_id = ?", (self.project_id,))
                conn.execute("UPDATE projects SET brand_name = '后来修改的品牌' WHERE id = ?", (self.project_id,))
                conn.execute("UPDATE model_configs SET model = 'later-web-model' WHERE id = ?", (self.web_model["id"],))

            fake_browser = mock.Mock()
            fake_browser.sample.return_value = result
            with mock.patch("src.deepseek_web_tasks.get_deepseek_web_browser", return_value=fake_browser):
                with mock.patch("src.deepseek_web_tasks.enqueue_next_web_task", return_value=""):
                    outcome = perform_deepseek_web_task(task_id, self.db_path)

            with get_conn(self.db_path) as conn:
                task = next(item for item in list_sampling_tasks(conn, "batch-persist") if item["task_id"] == task_id)
                runs = list_runs_by_batch(conn, "batch-persist")
                evaluation = conn.execute(
                    "SELECT * FROM answer_evaluations WHERE run_id = ?",
                    (runs[0]["run_id"],),
                ).fetchone()

        self.assertEqual(outcome["status"], "success")
        self.assertEqual(task["status"], "success")
        self.assertEqual(task["chat_id"], result["chat_id"])
        self.assertEqual(task["artifact_dir"], result["artifact_dir"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(fake_browser.sample.call_args.kwargs["question"], original_question)
        self.assertEqual(runs[0]["model"], original_model)
        self.assertEqual(runs[0]["response_text"], result["response_text"])
        self.assertEqual(json.loads(runs[0]["citations_json"]), result["citations"])
        self.assertEqual(json.loads(runs[0]["raw_response_json"])["chat_id"], result["chat_id"])
        self.assertIsNotNone(evaluation)


@unittest.skipUnless(os.environ.get("RUN_REDIS_INTEGRATION") == "1", "需要显式启用 Redis 集成测试")
class DeepSeekWebRedisIntegrationTests(unittest.TestCase):
    def test_140_tasks_run_serially_through_simple_worker(self):
        from redis import Redis
        from rq import Queue, SimpleWorker

        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/15")
        redis = Redis.from_url(redis_url)
        redis.flushdb()
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "rq.db"
            init_db(db_path)
            class FakeBrowser:
                def sample(self, *, batch_id, task_id, question):
                    return {
                        "response_text": f"测试品牌回答：{question}",
                        "citations": [{"url": "https://example.com", "title": "来源"}],
                        "chat_id": f"chat-{task_id}",
                        "network_match_ratio": 1.0,
                        "network_answer_available": True,
                        "latency_ms": 1,
                        "artifact_dir": f"data/deepseek-web-artifacts/{batch_id}/{task_id}",
                    }

            env = {
                "REDIS_URL": redis_url,
                "RQ_WEB_QUEUE_NAME": "geo-audit-web-test",
                "TASK_QUEUE_BACKEND": "rq",
                "DEEPSEEK_WEB_ENABLED": "1",
                "DEEPSEEK_WEB_COOLDOWN_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with get_conn(db_path) as conn:
                    project_id = create_project(conn, {"client_name": "RQ 测试", "brand_name": "测试品牌"})
                    import_questions_text(conn, project_id, "\n".join(f"RQ 问题 {index}" for index in range(140)))
                    web_model = next(item for item in list_model_configs(conn) if item["provider"] == "deepseek_web")
                    payload = {
                        "project_id": project_id,
                        "provider_mode": "deepseek_web",
                        "models": [{"model_config_id": web_model["id"], "search_enabled": True}],
                        "repeat_count": 1,
                    }
                    create_sampling_batch(conn, {"batch_id": "batch-rq-140", "project_id": project_id, "status": "queued", "total_count": 140, "config": payload})
                    create_web_sampling_tasks(conn, "batch-rq-140", project_id, payload)
                enqueue_next_web_task("batch-rq-140", db_path)
                queue = Queue(env["RQ_WEB_QUEUE_NAME"], connection=redis)
                with mock.patch("src.deepseek_web_tasks.get_deepseek_web_browser", return_value=FakeBrowser()):
                    SimpleWorker([queue], connection=redis).work(burst=True, logging_level="WARNING")

            with get_conn(db_path) as conn:
                counts = sampling_task_counts(conn, "batch-rq-140")
                tasks = list_sampling_tasks(conn, "batch-rq-140")
                batch = get_sampling_batch(conn, "batch-rq-140")
            self.assertEqual(counts["success"], 140)
            self.assertEqual(len({task["chat_id"] for task in tasks}), 140)
            self.assertEqual(batch["status"], "completed")
        redis.flushdb()


@unittest.skipUnless(__import__("importlib").util.find_spec("playwright"), "Playwright 未安装")
class DeepSeekWebBrowserContractTests(unittest.TestCase):
    def test_mock_page_contract(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            auth_state = root / "storage-state.json"
            auth_state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            fixture = Path(__file__).parent / "fixtures" / "deepseek_web_mock.html"
            config = DeepSeekWebConfig(
                chat_url=fixture.resolve().as_uri(),
                auth_state=auth_state,
                artifact_root=root / "artifacts",
                headless=True,
                navigation_timeout_ms=10000,
                response_timeout_seconds=15,
                stable_seconds=2,
                viewport_width=1280,
                viewport_height=800,
            )
            browser = DeepSeekWebBrowser(config)
            try:
                result = browser.sample(batch_id="batch-contract", task_id="task-contract", question="独立测试问题")
            finally:
                browser.close()
            self.assertEqual(result["chat_id"], "mock-session-001")
            self.assertEqual(result["response_text"], "模拟官方联网回答。示例来源")
            self.assertEqual(result["citations"][0]["url"], "https://example.com/source")
            self.assertTrue((root / result["artifact_dir"] / "final.png").exists())


if __name__ == "__main__":
    unittest.main()
