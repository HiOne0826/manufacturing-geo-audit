from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
from src.delivery import (
    DeliveryError,
    archive_report,
    build_export,
    compare_batches,
    create_report_version,
    ensure_delivery_schema,
    freeze_report,
    get_report_version,
    get_run_review,
    list_audit_events,
    list_report_versions,
    review_report,
    review_run,
)


class TrustedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "delivery.db"
        init_db(self.db_path)
        with get_conn(self.db_path) as conn:
            ensure_delivery_schema(conn)
            # Deliberately run twice: deploy/bootstrap must be idempotent.
            ensure_delivery_schema(conn)
            self.project_id = create_project(conn, {"client_name": "测试客户", "brand_name": "目标品牌"})
            import_question_content_rows(conn, self.project_id, [{"问题内容": "如何选择工业设备？"}])
            self.question_id = int(conn.execute(
                "SELECT id FROM questions WHERE project_id = ?", (self.project_id,)
            ).fetchone()["id"])
            self.model_id = int(list_model_configs(conn)[0]["id"])
            self._batch(conn, "baseline")
            self._batch(conn, "candidate")
            self._run(conn, "run-base", "baseline", latency_ms=100, mentioned=False)
            self._run(conn, "run-candidate", "candidate", latency_ms=50, mentioned=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _batch(self, conn, batch_id: str) -> None:
        create_sampling_batch(conn, {
            "batch_id": batch_id,
            "project_id": self.project_id,
            "status": "completed",
            "batch_name": batch_id,
            "config": {"repeat_count": 1},
            "config_snapshot": {"repeat_count": 1, "model_ids": [self.model_id]},
        })

    def _run(
        self,
        conn,
        run_id: str,
        batch_id: str,
        *,
        question_id: int | None = None,
        model_id: int | None = None,
        repeat_index: int = 1,
        latency_ms: int = 20,
        mentioned: bool = True,
        status: str = "success",
    ) -> None:
        insert_run(conn, {
            "run_id": run_id,
            "batch_id": batch_id,
            "project_id": self.project_id,
            "question_id": question_id or self.question_id,
            "model_config_id": model_id or self.model_id,
            "provider": "mock",
            "model": "mock-model",
            "requested_at": utc_now(),
            "response_text": "目标品牌值得考虑" if mentioned else "未提到品牌",
            "status": status,
            "latency_ms": latency_ms,
            "cost_estimate": 0.01,
            "search_mode": "off",
            "repeat_index": repeat_index,
        })
        if status == "success":
            insert_evaluation(conn, {
                "run_id": run_id,
                "target_brand_mentioned": mentioned,
                "target_brand_rank": 1 if mentioned else None,
                "recommendation_strength": "强" if mentioned else "未提及",
                "owned_site_cited": mentioned,
                "third_party_cited": False,
            })

    def test_run_review_is_append_only_and_audited(self):
        with get_conn(self.db_path) as conn:
            first = review_run(conn, "run-base", "needs_review", actor="alice", reason="引用待核验")
            second = review_run(conn, "run-base", "excluded", actor="bob", reason="回答不完整")
            current = get_run_review(conn, "run-base", include_history=True)
            self.assertEqual(current["review_id"], second["review_id"])
            self.assertEqual(current["decision"], "excluded")
            self.assertEqual([x["review_id"] for x in current["history"]], [second["review_id"], first["review_id"]])
            audits = list_audit_events(conn, entity_type="run", entity_id="run-base")
            self.assertEqual(len(audits), 2)
            self.assertEqual(audits[0]["details"]["decision"], "excluded")

            with self.assertRaises(DeliveryError) as ctx:
                review_run(conn, "run-base", "maybe", actor="alice")
            self.assertEqual(ctx.exception.code, "INVALID_REVIEW_DECISION")
            with self.assertRaises(DeliveryError) as ctx:
                review_run(conn, "missing", "valid", actor="alice")
            self.assertEqual(ctx.exception.code, "RUN_NOT_FOUND")

    def test_review_exclusion_changes_live_summary(self):
        with get_conn(self.db_path) as conn:
            before = build_export(conn, batch_id="baseline")
            self.assertEqual(before["summary"]["included_runs"], 1)
            review_run(conn, "run-base", "excluded", actor="reviewer", reason="样本无效")
            after = build_export(conn, batch_id="baseline")
            self.assertEqual(after["summary"]["total_runs"], 1)
            self.assertEqual(after["summary"]["included_runs"], 0)
            self.assertEqual(after["summary"]["excluded_runs"], 1)

    def test_report_state_machine_versions_and_audit(self):
        with get_conn(self.db_path) as conn:
            v1 = create_report_version(
                conn, project_id=self.project_id, batch_id="baseline", title="首次交付", actor="owner"
            )
            v2 = create_report_version(
                conn, project_id=self.project_id, batch_id="baseline", title="第二稿", actor="owner"
            )
            self.assertEqual((v1["version_no"], v2["version_no"]), (1, 2))
            self.assertEqual([r["version_no"] for r in list_report_versions(
                conn, project_id=self.project_id, batch_id="baseline"
            )], [2, 1])

            with self.assertRaises(DeliveryError) as ctx:
                freeze_report(conn, v1["report_id"], actor="owner")
            self.assertEqual(ctx.exception.code, "INVALID_REPORT_TRANSITION")
            with self.assertRaises(DeliveryError) as ctx:
                archive_report(conn, v2["report_id"], actor="owner")
            self.assertEqual(ctx.exception.code, "INVALID_REPORT_TRANSITION")

            reviewed = review_report(conn, v1["report_id"], actor="reviewer")
            self.assertEqual(reviewed["status"], "reviewed")
            # Idempotent repeated review does not create a second audit event.
            review_report(conn, v1["report_id"], actor="reviewer")
            frozen = freeze_report(conn, v1["report_id"], actor="approver")
            self.assertEqual(frozen["status"], "frozen")
            self.assertEqual(frozen["run_ids"], ["run-base"])
            self.assertEqual(frozen["summary_snapshot"]["snapshot_run_count"], 1)
            frozen_again = freeze_report(conn, v1["report_id"], actor="other")
            self.assertEqual(frozen_again["frozen_by"], "approver")
            archived = archive_report(conn, v1["report_id"], actor="owner")
            self.assertEqual(archived["status"], "archived")
            self.assertEqual(archived["run_ids"], ["run-base"])

            actions = [item["action"] for item in list_audit_events(
                conn, entity_type="report", entity_id=v1["report_id"]
            )]
            self.assertEqual(actions, ["report.archived", "report.frozen", "report.reviewed", "report.created"])

    def test_frozen_report_is_unchanged_after_retry_replaces_current_run(self):
        with get_conn(self.db_path) as conn:
            report = create_report_version(
                conn, project_id=self.project_id, batch_id="baseline", title="冻结报告", actor="owner"
            )
            review_report(conn, report["report_id"], actor="reviewer")
            frozen = freeze_report(conn, report["report_id"], actor="reviewer")
            old_snapshot = frozen["summary_snapshot"]

            # Same logical key: insert_run marks run-base historical and retry current.
            self._run(conn, "run-base-retry", "baseline", latency_ms=999, mentioned=True)
            live = build_export(conn, batch_id="baseline")
            exported = build_export(conn, report_id=report["report_id"])
            stored = get_report_version(conn, report["report_id"])

            self.assertEqual([r["run_id"] for r in live["runs"]], ["run-base-retry"])
            self.assertEqual([r["run_id"] for r in exported["runs"]], ["run-base"])
            self.assertEqual(exported["summary"], old_snapshot)
            self.assertEqual(stored["summary_snapshot"], old_snapshot)
            self.assertTrue(exported["scope"]["frozen"])

    def test_batch_comparison_reports_delta_when_scope_matches(self):
        with get_conn(self.db_path) as conn:
            result = compare_batches(conn, "baseline", "candidate")
            self.assertTrue(result["comparable"])
            self.assertFalse(result["delta_is_directional_only"])
            self.assertEqual(result["delta"]["brand_mention_rate"], 1.0)
            self.assertEqual(result["delta"]["average_latency_ms"], -50.0)

    def test_batch_comparison_blocks_direct_claim_for_different_scope(self):
        with get_conn(self.db_path) as conn:
            import_question_content_rows(conn, self.project_id, [{"问题内容": "第二个问题"}])
            extra_question = int(conn.execute(
                "SELECT MAX(id) AS id FROM questions WHERE project_id = ?", (self.project_id,)
            ).fetchone()["id"])
            self._run(conn, "run-candidate-extra", "candidate", question_id=extra_question, repeat_index=2)
            result = compare_batches(conn, "baseline", "candidate")
            self.assertFalse(result["comparable"])
            self.assertTrue(result["delta_is_directional_only"])
            codes = {reason["code"] for reason in result["comparability_reasons"]}
            self.assertIn("QUESTION_SET_MISMATCH", codes)
            self.assertIn("REPEAT_COUNT_MISMATCH", codes)

    def test_unified_export_includes_attempts_and_rejects_ambiguous_scope(self):
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO execution_attempts (
                    attempt_id, task_key, batch_id, status, usage_json, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("attempt-1", "key-1", "baseline", "success", "{\"tokens\": 12}", utc_now(), utc_now()),
            )
            payload = build_export(conn, batch_id="baseline", include_attempts=True)
            self.assertEqual(payload["schema_version"], "geo-audit-delivery/v1")
            self.assertEqual(payload["attempts"][0]["usage"], {"tokens": 12})
            self.assertEqual(payload["scope"]["batch_id"], "baseline")
            json.dumps(payload, ensure_ascii=False)
            with self.assertRaises(DeliveryError) as ctx:
                build_export(conn)
            self.assertEqual(ctx.exception.code, "EXPORT_SCOPE_REQUIRED")
            with self.assertRaises(DeliveryError):
                build_export(conn, batch_id="baseline", report_id="also-set")


if __name__ == "__main__":
    unittest.main()
