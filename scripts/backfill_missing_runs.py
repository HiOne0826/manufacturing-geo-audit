#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import latest_logical_runs  # noqa: E402
from src.db import (  # noqa: E402
    DEFAULT_DB_PATH,
    get_conn,
    get_model_config,
    get_project,
    get_sampling_batch,
    list_questions,
    list_runs_by_batch,
)
from src.platforms import test_platform_name  # noqa: E402
from src.runner import build_tasks, run_prepared_tasks  # noqa: E402
from src.runtime_env import load_dotenv_file  # noqa: E402


def logical_key_from_run(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(row.get("question_id") or 0),
        int(row.get("model_config_id") or 0),
        bool(row.get("search_enabled")),
        row.get("search_mode") or "",
        row.get("thinking_type") or "",
        row.get("reasoning_effort") or "",
        row.get("thinking_budget"),
        int(row.get("repeat_index") or 1),
    )


def logical_key_from_task(task: dict[str, Any]) -> tuple[Any, ...]:
    base = task["base"]
    return (
        int(base.get("question_id") or 0),
        int(base.get("model_config_id") or 0),
        bool(base.get("search_enabled")),
        base.get("search_mode") or "",
        base.get("thinking_type") or "",
        base.get("reasoning_effort") or "",
        base.get("thinking_budget"),
        int(base.get("repeat_index") or 1),
    )


def platform_from_task(task: dict[str, Any]) -> str:
    base = task["base"]
    return test_platform_name(base.get("provider"), base.get("model"))


def provider_from_task(task: dict[str, Any]) -> str:
    return str(task["base"].get("provider") or "")


def apply_provider_filters(
    tasks: list[dict[str, Any]],
    include_providers: set[str],
    exclude_providers: set[str],
) -> list[dict[str, Any]]:
    filtered = []
    for task in tasks:
        provider = provider_from_task(task)
        if include_providers and provider not in include_providers:
            continue
        if exclude_providers and provider in exclude_providers:
            continue
        filtered.append(task)
    return filtered


def select_limited(tasks: list[dict[str, Any]], limit_per_platform: int, limit_total: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    per_platform: Counter[str] = Counter()
    for task in tasks:
        platform = platform_from_task(task)
        if limit_per_platform and per_platform[platform] >= limit_per_platform:
            continue
        selected.append(task)
        per_platform[platform] += 1
        if limit_total and len(selected) >= limit_total:
            break
    return selected


def summarize(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(tasks),
        "by_provider": dict(sorted(Counter(provider_from_task(task) for task in tasks).items())),
        "by_platform": dict(sorted(Counter(platform_from_task(task) for task in tasks).items())),
    }


def apply_local_proxy() -> None:
    os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    os.environ.setdefault("ALL_PROXY", "socks5://127.0.0.1:7890")
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or execute missing task backfill for a sampling batch.")
    parser.add_argument("batch_id")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--execute", action="store_true", help="Actually call model providers and append model_runs.")
    parser.add_argument("--include-provider", action="append", default=[], help="Provider id to include. Can repeat.")
    parser.add_argument("--exclude-provider", action="append", default=[], help="Provider id to exclude. Can repeat.")
    parser.add_argument("--limit-per-platform", type=int, default=0, help="0 means no per-platform limit.")
    parser.add_argument("--limit-total", type=int, default=0, help="0 means no total limit.")
    parser.add_argument("--max-workers", type=int, default=0, help="Override sampling max_workers for this run.")
    parser.add_argument(
        "--provider-concurrency",
        action="append",
        default=[],
        help="Provider concurrency limit, for example deepseek=1. Can repeat.",
    )
    parser.add_argument("--chunk-size", type=int, default=0, help="Run selected tasks in sequential chunks. 0 means one chunk.")
    parser.add_argument("--sleep-between-chunks", type=float, default=0, help="Seconds to sleep between chunks.")
    parser.add_argument("--use-local-proxy", action="store_true", help="Use localhost mihomo proxy env vars for this process.")
    parser.add_argument("--yes", action="store_true", help="Required with --execute.")
    args = parser.parse_args()

    load_dotenv_file(ROOT / ".env")
    if args.use_local_proxy:
        apply_local_proxy()
    db_path = Path(args.db_path)
    with get_conn(db_path) as conn:
        batch = get_sampling_batch(conn, args.batch_id)
        if not batch:
            raise SystemExit(f"batch not found: {args.batch_id}")
        project_id = int(batch["project_id"])
        project = get_project(conn, project_id)
        if not project:
            raise SystemExit(f"project not found: {project_id}")
        config = batch.get("config") or {}
        questions = list_questions(conn, project_id)
        model_configs = {}
        for item in config.get("models") or []:
            model_config_id = int(item.get("model_config_id") or 0)
            model_config = get_model_config(conn, model_config_id)
            if not model_config:
                raise SystemExit(f"model config not found: {model_config_id}")
            model_configs[model_config_id] = model_config
        planned_tasks = build_tasks(
            batch_id=args.batch_id,
            project_id=project_id,
            config=config,
            questions=questions,
            model_configs=model_configs,
        )
        latest_keys = {logical_key_from_run(row) for row in latest_logical_runs(list_runs_by_batch(conn, args.batch_id, limit=50000))}
        missing_tasks = [task for task in planned_tasks if logical_key_from_task(task) not in latest_keys]
        filtered_tasks = apply_provider_filters(
            missing_tasks,
            {str(item) for item in args.include_provider},
            {str(item) for item in args.exclude_provider},
        )
        selected_tasks = select_limited(filtered_tasks, args.limit_per_platform, args.limit_total)
        run_config = dict(config)
        if args.max_workers:
            run_config["max_workers"] = args.max_workers
        if args.provider_concurrency:
            run_config["provider_concurrency_limits"] = ",".join(args.provider_concurrency)
        summary = {
            "batch_id": args.batch_id,
            "planned_total": len(planned_tasks),
            "missing": summarize(missing_tasks),
            "filtered": summarize(filtered_tasks),
            "selected": summarize(selected_tasks),
            "execute": bool(args.execute),
            "use_local_proxy": bool(args.use_local_proxy),
            "chunk_size": args.chunk_size,
            "sleep_between_chunks": args.sleep_between_chunks,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not args.execute:
            return 0
        if not args.yes:
            raise SystemExit("--execute requires --yes")
        if not selected_tasks:
            return 0
        conn.commit()

    total = 0
    failed = 0
    success = 0
    chunks = chunked(selected_tasks, args.chunk_size)
    for index, tasks in enumerate(chunks, start=1):
        print(json.dumps({"chunk": index, "chunks": len(chunks), "tasks": summarize(tasks)}, ensure_ascii=False))
        result = run_prepared_tasks(
            tasks=tasks,
            db_target=db_path,
            project=project,
            config=run_config,
            batch_id=args.batch_id,
        )
        print(json.dumps({"chunk": index, "result": result}, ensure_ascii=False, indent=2))
        total += int(result.get("total", 0) or 0)
        failed += int(result.get("failed", 0) or 0)
        success += int(result.get("success", 0) or 0)
        if args.sleep_between_chunks > 0 and index < len(chunks):
            time.sleep(args.sleep_between_chunks)
    final = {"batch_id": args.batch_id, "total": total, "failed": failed, "success": success}
    print(json.dumps({"result": final}, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
