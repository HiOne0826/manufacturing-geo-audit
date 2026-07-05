#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import (
    DEFAULT_DB_PATH,
    create_project,
    create_sampling_batch,
    get_conn,
    import_questions_rows,
    init_db,
    list_model_configs,
    list_runs_by_batch,
    update_sampling_batch,
    utc_now,
)
from src.platforms import test_platform_name
from src.runner import run_batch
from src.runtime_env import load_dotenv_file, provider_has_credentials, resolve_brave_search_api_key


TARGET_PROVIDERS = ["openrouter_gpt", "deepseek", "openrouter_gemini", "doubao", "qwen", "hunyuan"]


def active_model_by_provider(models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in models:
        provider = str(item.get("provider") or "")
        if provider not in TARGET_PROVIDERS or provider in selected:
            continue
        if not item.get("active"):
            continue
        selected[provider] = item
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="真实六平台并发采样验收。会写入当前 GEO 审计数据库。")
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--search", action="store_true", help="启用各平台联网搜索能力；默认只做纯模型通路验收。")
    args = parser.parse_args()

    if os.environ.get("ALLOW_LIVE_MODEL_CALLS") != "1":
        raise RuntimeError("真实模型调用未开启：请设置 ALLOW_LIVE_MODEL_CALLS=1 后再运行")

    load_dotenv_file(ROOT / ".env")
    db_path = Path(args.db_path)
    init_db(db_path)

    with get_conn(db_path) as conn:
        models = list_model_configs(conn)
        selected = active_model_by_provider(models)
        missing_configs = [provider for provider in TARGET_PROVIDERS if provider not in selected]
        missing_keys = [
            provider
            for provider in TARGET_PROVIDERS
            if provider in selected and not provider_has_credentials(provider, selected[provider].get("api_key", ""))
        ]
        if args.search and "deepseek" in selected and not resolve_brave_search_api_key():
            missing_keys.append("deepseek:brave")
        if missing_configs or missing_keys:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "missing_configs": [test_platform_name(provider) for provider in missing_configs],
                        "missing_keys": [
                            "DeepSeek Brave Search" if provider == "deepseek:brave" else test_platform_name(provider)
                            for provider in missing_keys
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return 2

        project_id = create_project(
            conn,
            {
                "client_name": "六平台真实采样验收",
                "brand_name": "英歌瑞",
                "product_category": "自动化涂装设备",
                "competitors": "机器人涂装集成商;新能源涂装设备厂家",
                "notes": "正式客户测试前的六平台并发采样验收。",
            },
        )
        import_questions_rows(
            conn,
            project_id,
            [
                {
                    "question_id": "LIVE001",
                    "question": "国内自动化涂装设备和涂胶控制系统有哪些值得优先了解的品牌？请简要说明理由。",
                    "question_type": "品牌推荐",
                    "target_brand": "英歌瑞",
                    "competitor_brands": "机器人涂装集成商;新能源涂装设备厂家",
                    "priority": "high",
                }
            ],
        )
        run_models = [
            {
                "model_config_id": int(selected[provider]["id"]),
                "search_enabled": bool(args.search and selected[provider].get("supports_search")),
            }
            for provider in TARGET_PROVIDERS
        ]
        batch_id = f"live-six-platform-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        config = {
            "models": run_models,
            "repeat_count": 1,
            "max_workers": args.max_workers,
            "retry_count": 0,
        }
        create_sampling_batch(
            conn,
            {
                "batch_id": batch_id,
                "project_id": project_id,
                "status": "running",
                "total_count": len(TARGET_PROVIDERS),
                "config": config,
                "started_at": utc_now(),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        conn.commit()
        result = run_batch(conn, project_id, config, batch_id=batch_id)
        status = "completed" if result["failed"] == 0 else "failed"
        update_sampling_batch(
            conn,
            batch_id,
            {
                "status": status,
                "completed_count": result["total"],
                "success_count": result["success"],
                "failed_count": result["failed"],
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        runs = list_runs_by_batch(conn, batch_id, limit=100)

    print(
        json.dumps(
            {
                "ok": result["failed"] == 0,
                "project_id": project_id,
                "batch_id": batch_id,
                "total": result["total"],
                "success": result["success"],
                "failed": result["failed"],
                "export_path": f"/api/export/batches/{batch_id}/runs.xls",
                "runs": [
                    {
                        "test_platform": row.get("test_platform") or test_platform_name(row.get("provider"), row.get("model")),
                        "provider": row.get("provider", ""),
                        "model": row.get("model", ""),
                        "status": row.get("status", ""),
                        "latency_ms": row.get("latency_ms", 0),
                        "error_message": str(row.get("error_message") or "")[:300],
                    }
                    for row in runs
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
