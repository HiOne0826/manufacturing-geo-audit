from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .adapters import AdapterError, call_configured_model
from .db import (
    get_conn,
    get_model_config,
    get_project,
    insert_evaluation,
    insert_run,
    list_questions,
    utc_now,
)
from .evaluator import evaluate_answer


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def sampling_max_workers(config: dict[str, Any] | None = None) -> int:
    raw = (config or {}).get("max_workers")
    if raw not in (None, "", "null"):
        try:
            return max(1, min(int(raw), 64))
        except (TypeError, ValueError):
            pass
    return env_int("SAMPLING_MAX_WORKERS", 8, minimum=1, maximum=64)


def provider_max_workers() -> int:
    return env_int("SAMPLING_PROVIDER_MAX_WORKERS", 3, minimum=1, maximum=32)


def retry_count(config: dict[str, Any] | None = None) -> int:
    raw = (config or {}).get("retry_count")
    if raw not in (None, "", "null"):
        try:
            return max(0, min(int(raw), 5))
        except (TypeError, ValueError):
            pass
    return env_int("SAMPLING_RETRY_COUNT", 1, minimum=0, maximum=5)


def connection_db_path(conn) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        raise ValueError("当前数据库连接没有文件路径，不能用于并发采样")
    return Path(row[2])


def estimate_batch_total(conn, project_id: int, config: dict[str, Any]) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    questions = list_questions(conn, project_id)
    if not questions:
        raise ValueError("项目还没有问题库")

    providers = config.get("models") or []
    repeat_count = max(1, min(int(config.get("repeat_count", 1)), 10))
    if not providers:
        raise ValueError("请至少选择一个模型")
    return len(questions) * len(providers) * repeat_count


def prepare_runtime_task(
    *,
    batch_id: str,
    project_id: int,
    question: dict[str, Any],
    provider_cfg: dict[str, Any],
    model_config: dict[str, Any],
    config: dict[str, Any],
    repeat_index: int,
) -> dict[str, Any]:
    run_id = f"run-{uuid.uuid4().hex}"
    runtime_model = str(provider_cfg.get("runtime_model", "")).strip()
    runtime_model_version = str(provider_cfg.get("runtime_model_version", "")).strip()
    runtime_model_config = {**model_config}
    if runtime_model:
        runtime_model_config["model"] = runtime_model
    if runtime_model_version:
        runtime_model_config["model_version"] = runtime_model_version
    provider = runtime_model_config.get("provider", "")
    model = runtime_model_config.get("model", "")
    model_search_mode = str(provider_cfg.get("search_mode", config.get("search_mode", "auto"))).strip() or "auto"
    search_enabled = bool(provider_cfg.get("search_enabled", False)) and model_search_mode != "off"
    model_thinking_enabled = bool(provider_cfg.get("thinking_enabled", False))
    model_thinking_type = str(
        provider_cfg.get("thinking_type", config.get("thinking_type", "enabled" if model_thinking_enabled else "disabled"))
    ).strip() or ("enabled" if model_thinking_enabled else "disabled")
    model_reasoning_effort = (
        str(provider_cfg.get("reasoning_effort", config.get("reasoning_effort", ""))).strip() if model_thinking_enabled else ""
    )
    model_temperature = float(provider_cfg.get("temperature", config.get("temperature", 0)) or 0)
    raw_budget = provider_cfg.get("thinking_budget", config.get("thinking_budget"))
    model_thinking_budget = None
    if model_thinking_enabled and raw_budget not in (None, "", "null"):
        model_thinking_budget = int(raw_budget)
    base = {
        "run_id": run_id,
        "batch_id": batch_id,
        "project_id": project_id,
        "question_id": question["id"],
        "model_config_id": int(model_config.get("id", provider_cfg.get("model_config_id", 0)) or 0),
        "provider": provider,
        "model": model or provider,
        "search_enabled": search_enabled,
        "search_mode": model_search_mode if search_enabled else "off",
        "temperature": model_temperature,
        "repeat_index": repeat_index,
        "thinking_type": model_thinking_type,
        "reasoning_effort": model_reasoning_effort,
        "thinking_budget": model_thinking_budget,
        "requested_at": utc_now(),
    }
    run_options = {
        "search_mode": model_search_mode,
        "thinking_type": model_thinking_type,
        "reasoning_effort": model_reasoning_effort,
        "thinking_budget": model_thinking_budget,
        "runtime_model": runtime_model,
        "runtime_model_version": runtime_model_version,
        "search_sources": provider_cfg.get("search_sources", ""),
        "search_limit": provider_cfg.get("search_limit"),
        "search_max_keyword": provider_cfg.get("search_max_keyword", ""),
        "search_user_location": provider_cfg.get("search_user_location", ""),
        "search_site_filter": provider_cfg.get("search_site_filter", ""),
        "search_time_filter": provider_cfg.get("search_time_filter", ""),
        "search_strategy": provider_cfg.get("search_strategy", ""),
        "search_freshness": provider_cfg.get("search_freshness", ""),
        "search_prompt_intervene": provider_cfg.get("search_prompt_intervene", ""),
        "search_enable_source": provider_cfg.get("search_enable_source", False),
        "search_enable_citation": provider_cfg.get("search_enable_citation", False),
        "search_citation_format": provider_cfg.get("search_citation_format", ""),
    }
    return {
        "base": base,
        "model_config": runtime_model_config,
        "question": question,
        "run_options": run_options,
        "temperature": model_temperature,
        "search_enabled": search_enabled,
    }


def execute_sampling_task(
    task: dict[str, Any],
    *,
    db_path: Path,
    project: dict[str, Any],
    semaphore: threading.Semaphore,
    max_retries: int,
) -> dict[str, Any]:
    base = task["base"]
    question = task["question"]
    last_error = None
    result = None
    try:
        for attempt in range(0, max_retries + 1):
            try:
                with semaphore:
                    result = call_configured_model(
                        task["model_config"],
                        question["question"],
                        task["search_enabled"],
                        task["temperature"],
                        task["run_options"],
                    )
                break
            except AdapterError as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise
        if result is None:
            raise AdapterError(str(last_error or "模型调用失败"))
        run = {
            **base,
            "provider": result.get("provider", base["provider"]),
            "model": result.get("model", base["model"]),
            "model_version": result.get("model_version", task["model_config"].get("model_version", "")),
            "response_text": result.get("response_text", ""),
            "citations": result.get("citations", []),
            "latency_ms": result.get("latency_ms", 0),
            "status": "success",
            "raw_response": result.get("raw_response", {}),
        }
        evaluation = evaluate_answer(
            run_id=base["run_id"],
            answer=run["response_text"],
            target_brand=question.get("target_brand") or project.get("brand_name", ""),
            competitors=question.get("competitor_brands") or project.get("competitors", ""),
            website_domain=project.get("website_domain", ""),
            citations=run.get("citations", []),
        )
        with get_conn(db_path) as conn:
            insert_run(conn, run)
            insert_evaluation(conn, evaluation)
        return {**base, "status": "success"}
    except Exception as exc:
        with get_conn(db_path) as conn:
            insert_run(
                conn,
                {
                    **base,
                    "response_text": "",
                    "citations": [],
                    "latency_ms": 0,
                    "status": "failed",
                    "error_message": str(exc),
                    "raw_response": {},
                },
            )
        return {**base, "status": "failed", "error_message": str(exc)}


def build_tasks(
    *,
    batch_id: str,
    project_id: int,
    config: dict[str, Any],
    questions: list[dict[str, Any]],
    model_configs: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    providers = config.get("models") or []
    repeat_count = max(1, min(int(config.get("repeat_count", 1)), 10))
    tasks: list[dict[str, Any]] = []
    for question in questions:
        for provider_cfg in providers:
            model_config_id = int(provider_cfg.get("model_config_id", 0))
            for repeat_index in range(1, repeat_count + 1):
                tasks.append(
                    prepare_runtime_task(
                        batch_id=batch_id,
                        project_id=project_id,
                        question=question,
                        provider_cfg=provider_cfg,
                        model_config=model_configs[model_config_id],
                        config=config,
                        repeat_index=repeat_index,
                    )
                )
    return tasks


def run_prepared_tasks(
    *,
    tasks: list[dict[str, Any]],
    db_path: Path,
    project: dict[str, Any],
    config: dict[str, Any],
    batch_id: str,
    progress_callback=None,
) -> dict[str, Any]:
    total_expected = len(tasks)
    completed = 0
    failed = 0
    provider_limit = provider_max_workers()
    semaphores: dict[str, threading.Semaphore] = {}
    for task in tasks:
        provider = task["base"]["provider"] or "unknown"
        semaphores.setdefault(provider, threading.Semaphore(provider_limit))
    max_workers = min(sampling_max_workers(config), max(1, total_expected))
    max_retries = retry_count(config)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                execute_sampling_task,
                task,
                db_path=db_path,
                project=project,
                semaphore=semaphores[task["base"]["provider"] or "unknown"],
                max_retries=max_retries,
            )
            for task in tasks
        ]
        for future in as_completed(futures):
            outcome = future.result()
            completed += 1
            if outcome["status"] == "failed":
                failed += 1
            if progress_callback:
                progress_callback(
                    {
                        "batch_id": batch_id,
                        "total": total_expected,
                        "completed": completed,
                        "failed": failed,
                        "success": completed - failed,
                        "provider": outcome["provider"],
                        "model": outcome["model"],
                        "question_id": outcome["question_id"],
                        "repeat_index": outcome["repeat_index"],
                    }
                )

    return {"batch_id": batch_id, "total": completed, "failed": failed, "success": completed - failed}


def run_batch(
    conn,
    project_id: int,
    config: dict[str, Any],
    *,
    batch_id: str | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    questions = list_questions(conn, project_id)
    if not questions:
        raise ValueError("项目还没有问题库")

    providers = config.get("models") or []
    repeat_count = max(1, min(int(config.get("repeat_count", 1)), 10))
    if not providers:
        raise ValueError("请至少选择一个模型")

    db_path = connection_db_path(conn)
    batch_id = batch_id or f"batch-{uuid.uuid4().hex[:10]}"
    model_configs: dict[int, dict[str, Any]] = {}
    for provider_cfg in providers:
        model_config_id = int(provider_cfg.get("model_config_id", 0))
        model_config = get_model_config(conn, model_config_id)
        if not model_config:
            raise ValueError("模型配置不存在")
        model_configs[model_config_id] = model_config

    tasks = build_tasks(batch_id=batch_id, project_id=project_id, config=config, questions=questions, model_configs=model_configs)
    conn.commit()
    return run_prepared_tasks(
        tasks=tasks,
        db_path=db_path,
        project=project,
        config=config,
        batch_id=batch_id,
        progress_callback=progress_callback,
    )


def rerun_failed_runs(conn, batch_id: str, failed_runs: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    if not failed_runs:
        return {"batch_id": batch_id, "total": 0, "failed": 0, "success": 0}
    project_id = int(failed_runs[0]["project_id"])
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    questions_by_id = {item["id"]: item for item in list_questions(conn, project_id)}
    model_configs: dict[int, dict[str, Any]] = {}
    tasks = []
    rerun_config = {"repeat_count": 1, **(config or {})}
    for run in failed_runs:
        model_config_id = int(run.get("model_config_id", 0) or 0)
        if not model_config_id:
            continue
        if model_config_id not in model_configs:
            model_config = get_model_config(conn, model_config_id)
            if not model_config:
                continue
            model_configs[model_config_id] = model_config
        question = questions_by_id.get(int(run["question_id"]))
        if not question:
            continue
        provider_cfg = {
            "model_config_id": model_config_id,
            "runtime_model": "",
            "runtime_model_version": run.get("model_version", ""),
            "search_enabled": bool(run.get("search_enabled")),
            "search_mode": run.get("search_mode", "auto"),
            "thinking_enabled": run.get("thinking_type") != "disabled",
            "thinking_type": run.get("thinking_type", "disabled"),
            "reasoning_effort": run.get("reasoning_effort", ""),
            "thinking_budget": run.get("thinking_budget"),
            "temperature": run.get("temperature", 0),
        }
        tasks.append(
            prepare_runtime_task(
                batch_id=batch_id,
                project_id=project_id,
                question=question,
                provider_cfg=provider_cfg,
                model_config=model_configs[model_config_id],
                config=rerun_config,
                repeat_index=int(run.get("repeat_index", 1) or 1),
            )
        )
    conn.commit()
    return run_prepared_tasks(
        tasks=tasks,
        db_path=connection_db_path(conn),
        project=project,
        config=rerun_config,
        batch_id=batch_id,
    )
