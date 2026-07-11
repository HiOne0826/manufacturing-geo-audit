from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db import create_model_config, create_project, create_sampling_batch, get_conn, import_question_content_rows, init_db, list_runs_by_batch, utc_now
from src.runner import prepare_batch_ledger
from src.tasks import perform_rerun_runs, perform_sampling_task


class ImmutableTaskSnapshotTests(unittest.TestCase):
    def test_worker_uses_creation_time_question_and_model_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TASK_QUEUE_BACKEND": "inline"}, clear=False):
            path = Path(tmp) / "snapshot.db"
            init_db(path)
            with get_conn(path) as conn:
                project_id = create_project(conn, {"client_name": "快照", "brand_name": "快照品牌"})
                import_question_content_rows(conn, project_id, [{"问题内容": "创建时的问题"}])
                model_id = create_model_config(conn, {"provider": "mock", "label": "Mock", "model": "mock-v1", "active": True, "supports_pure": True})
                config = {"repeat_count": 1, "models": [{"model_config_id": model_id, "search_enabled": False}]}
                create_sampling_batch(conn, {"batch_id": "snapshot-batch", "project_id": project_id, "status": "queued", "total_count": 1, "config": config, "config_snapshot": config})
                tasks = prepare_batch_ledger(conn, "snapshot-batch", project_id, config)
                task_id = tasks[0]["task_id"]
                conn.execute("UPDATE questions SET question = '后来修改的问题' WHERE project_id = ?", (project_id,))
                conn.execute("UPDATE model_configs SET model = 'mock-v2', api_key = 'rotated-secret' WHERE id = ?", (model_id,))

            result = perform_sampling_task(task_id, path)
            self.assertEqual(result["status"], "success")
            with get_conn(path) as conn:
                run = list_runs_by_batch(conn, "snapshot-batch")[0]
                task_row = conn.execute("SELECT task_snapshot_json FROM sampling_tasks WHERE task_id = ?", (task_id,)).fetchone()
            self.assertEqual(run["model"], "mock-v1")
            self.assertIn("创建时的问题", run["response_text"])
            self.assertNotIn("后来修改的问题", run["response_text"])
            self.assertNotIn("rotated-secret", str(task_row["task_snapshot_json"]))

            with get_conn(path) as conn:
                conn.execute("UPDATE model_runs SET status = 'failed', error_message = 'timeout' WHERE id = ?", (run["id"],))
                conn.execute("UPDATE sampling_tasks SET status = 'failed' WHERE task_id = ?", (task_id,))
            rerun = perform_rerun_runs("snapshot-batch", [int(run["id"])], db_target=path)
            self.assertEqual(rerun["success"], 1)
            with get_conn(path) as conn:
                current = list_runs_by_batch(conn, "snapshot-batch")[0]
                attempt_count = conn.execute("SELECT COUNT(*) AS count FROM execution_attempts WHERE task_id = ?", (task_id,)).fetchone()["count"]
            self.assertEqual(current["model"], "mock-v1")
            self.assertIn("创建时的问题", current["response_text"])
            self.assertEqual(attempt_count, 2)


if __name__ == "__main__":
    unittest.main()
