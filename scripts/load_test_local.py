#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import create_model_config, create_project, get_conn, import_questions_rows, init_db, list_runs
from src.exporter import runs_to_csv, runs_to_excel_html
from src.runner import run_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 Mock 负载测试，不调用真实模型。")
    parser.add_argument("--questions", type=int, default=150)
    parser.add_argument("--models", type=int, default=3)
    parser.add_argument("--keep-db", default="")
    args = parser.parse_args()

    if args.questions <= 0 or args.models <= 0:
        raise ValueError("--questions 和 --models 必须大于 0")

    temp_dir = None
    if args.keep_db:
        db_path = Path(args.keep_db)
    else:
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "load-test.db"

    started_at = time.time()
    init_db(db_path)
    with get_conn(db_path) as conn:
        project_id = create_project(
            conn,
            {
                "client_name": "本地负载测试客户",
                "brand_name": "本地负载测试品牌",
                "product_category": "本地负载测试品类",
                "competitors": "竞品A;竞品B",
            },
        )
        rows = [
            {
                "question_id": f"L{idx:03d}",
                "question": f"第 {idx} 个本地负载问题，本地负载测试品牌是否出现？",
                "question_type": "load",
                "target_brand": "本地负载测试品牌",
                "competitor_brands": "竞品A;竞品B",
            }
            for idx in range(1, args.questions + 1)
        ]
        imported = import_questions_rows(conn, project_id, rows)
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
            for idx in range(1, args.models + 1)
        ]
        result = run_batch(
            conn,
            project_id,
            {
                "models": [{"model_config_id": model_id, "search_enabled": False} for model_id in model_ids],
                "repeat_count": 1,
            },
            batch_id="local-load-test",
        )
        runs = list_runs(conn, project_id, limit=args.questions * args.models + 10)
        csv_body = runs_to_csv(runs)
        xls_body = runs_to_excel_html(runs)

    expected = args.questions * args.models
    checks = {
        "imported_questions": imported,
        "expected_total": expected,
        "actual_total": result["total"],
        "success": result["success"],
        "failed": result["failed"],
        "runs": len(runs),
        "success_plus_failed": result["success"] + result["failed"],
        "csv_generated": "run_id" in csv_body,
        "xls_generated": "<table" in xls_body,
        "elapsed_seconds": round(time.time() - started_at, 3),
        "db_path": str(db_path),
    }
    assert imported == args.questions, checks
    assert result["total"] == expected, checks
    assert result["success"] + result["failed"] == expected, checks
    assert len(runs) == expected, checks
    assert checks["csv_generated"] and checks["xls_generated"], checks

    print(json.dumps({"ok": True, **checks}, ensure_ascii=False))
    if temp_dir:
        temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
