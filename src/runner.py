from __future__ import annotations

import os
import json
import random
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from .adapters import AdapterError, call_configured_model, provider_sampling_defaults
from .db import (
    get_conn,
    get_model_config,
    get_project,
    insert_evaluation,
    insert_run,
    is_postgres_conn,
    list_questions,
    utc_now,
)
from .evaluator import evaluate_answer


PROVIDER_CONCURRENCY_GROUPS = {
    "openrouter_gpt": "openrouter",
    "openrouter_gemini": "openrouter_gemini",
}

DEFAULT_PROVIDER_CONCURRENCY_LIMITS = {
    "openai": 2,
    "gemini": 2,
    "openrouter": 4,
    "openrouter_gemini": 2,
    "deepseek": 1,
    "doubao": 2,
    "qwen": 1,
    "hunyuan": 2,
    "kimi": 2,
    "ernie": 1,
    "minimax": 2,
    "mock": 16,
}


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


def provider_concurrency_group(provider: str) -> str:
    normalized = str(provider or "unknown").strip().lower() or "unknown"
    return PROVIDER_CONCURRENCY_GROUPS.get(normalized, normalized)


def parse_provider_concurrency_limits(raw: Any) -> dict[str, int]:
    if not raw:
        return {}
    parsed: dict[str, Any]
    if isinstance(raw, dict):
        parsed = raw
    else:
        text = str(raw).strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = {}
            for item in text.split(","):
                if "=" not in item:
                    continue
                key, value = item.split("=", 1)
                parsed[key.strip()] = value.strip()
    limits: dict[str, int] = {}
    for key, value in parsed.items():
        group = provider_concurrency_group(str(key))
        try:
            limits[group] = max(1, min(int(value), 32))
        except (TypeError, ValueError):
            continue
    return limits


def provider_concurrency_limits(config: dict[str, Any] | None = None) -> dict[str, int]:
    limits = dict(DEFAULT_PROVIDER_CONCURRENCY_LIMITS)
    limits.update(parse_provider_concurrency_limits(os.environ.get("SAMPLING_PROVIDER_CONCURRENCY_LIMITS", "")))
    limits.update(parse_provider_concurrency_limits((config or {}).get("provider_concurrency_limits")))
    return limits


def provider_concurrency_limit(provider: str, config: dict[str, Any] | None = None) -> int:
    group = provider_concurrency_group(provider)
    return provider_concurrency_limits(config).get(group, provider_max_workers())


def retry_count(config: dict[str, Any] | None = None) -> int:
    raw = (config or {}).get("retry_count")
    if raw not in (None, "", "null"):
        try:
            return max(0, min(int(raw), 5))
        except (TypeError, ValueError):
            pass
    return env_int("SAMPLING_RETRY_COUNT", 1, minimum=0, maximum=5)


def retry_delay_seconds(error: AdapterError, attempt: int) -> float:
    try:
        base = max(0.0, float(os.environ.get("SAMPLING_RETRY_BACKOFF_BASE_SECONDS", "1") or 1))
    except (TypeError, ValueError):
        base = 1.0
    try:
        maximum = max(base, float(os.environ.get("SAMPLING_RETRY_BACKOFF_MAX_SECONDS", "30") or 30))
    except (TypeError, ValueError):
        maximum = 30.0
    exponential = min(maximum, base * (2 ** max(0, attempt)))
    requested_delay = max(0.0, float(error.retry_after or 0))
    delay = max(exponential, requested_delay)
    jitter = random.uniform(0, min(max(base, delay * 0.25), 1.0))
    return delay + jitter


def call_model_with_retries(
    task: dict[str, Any],
    semaphore: threading.Semaphore,
    max_retries: int,
) -> dict[str, Any]:
    last_error: AdapterError | None = None
    for attempt in range(0, max_retries + 1):
        try:
            with semaphore:
                return call_configured_model(
                    task["model_config"],
                    task["question"]["question"],
                    task["search_enabled"],
                    task["temperature"],
                    task["run_options"],
                )
        except AdapterError as exc:
            last_error = exc
            if attempt >= max_retries or not exc.retryable:
                raise
            time.sleep(retry_delay_seconds(exc, attempt))
    raise last_error or AdapterError("模型调用失败")


def connection_db_target(conn) -> Path | None:
    if is_postgres_conn(conn):
        return None
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
    sampling_defaults = provider_sampling_defaults(provider, model)
    default_search_mode = str(sampling_defaults.get("search_mode", "")).strip()
    raw_search_mode = provider_cfg.get("search_mode")
    if raw_search_mode in (None, "", "null"):
        raw_search_mode = default_search_mode or config.get("search_mode", "auto")
    model_search_mode = str(raw_search_mode).strip() or default_search_mode or "auto"
    search_enabled = bool(provider_cfg.get("search_enabled", False)) and model_search_mode != "off"
    model_thinking_enabled = bool(provider_cfg.get("thinking_enabled", False))
    model_thinking_type = str(
        provider_cfg.get("thinking_type", config.get("thinking_type", "enabled" if model_thinking_enabled else "disabled"))
    ).strip() or ("enabled" if model_thinking_enabled else "disabled")
    model_reasoning_effort = (
        str(provider_cfg.get("reasoning_effort", config.get("reasoning_effort", ""))).strip() if model_thinking_enabled else ""
    )
    raw_temperature = provider_cfg.get("temperature", config.get("temperature"))
    if raw_temperature in (None, "", "null"):
        raw_temperature = sampling_defaults.get("temperature", 0)
    model_temperature = float(raw_temperature or 0)
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
    default_search_strategy = str(sampling_defaults.get("search_strategy", "")).strip()
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
        "search_strategy": provider_cfg.get("search_strategy", default_search_strategy) or default_search_strategy,
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
    db_target: Path | None,
    project: dict[str, Any],
    semaphore: threading.Semaphore,
    max_retries: int,
) -> dict[str, Any]:
    base = task["base"]
    question = task["question"]
    try:
        result = call_model_with_retries(task, semaphore, max_retries)
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
        with get_conn(db_target) as conn:
            insert_run(conn, run)
            insert_evaluation(conn, evaluation)
        return {**base, "status": "success"}
    except Exception as exc:
        with get_conn(db_target) as conn:
            insert_run(
                conn,
                {
                    **base,
                    "response_text": "",
                    "citations": [],
                    "latency_ms": 0,
                    "status": "failed",
                    "error_message": str(exc),
                    "raw_response": getattr(exc, "raw_response", {}),
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


def run_prepared_tasks(
    *,
    tasks: list[dict[str, Any]],
    db_target: Path | None,
    project: dict[str, Any],
    config: dict[str, Any],
    batch_id: str,
    progress_callback=None,
    should_pause=None,
) -> dict[str, Any]:
    total_expected = len(tasks)
    completed = 0
    failed = 0
    limits = provider_concurrency_limits(config)
    semaphores: dict[str, threading.Semaphore] = {}
    for task in tasks:
        provider = task["base"]["provider"] or "unknown"
        group = provider_concurrency_group(provider)
        semaphores.setdefault(group, threading.Semaphore(limits.get(group, provider_max_workers())))
    max_workers = min(sampling_max_workers(config), max(1, total_expected))
    max_retries = retry_count(config)

    paused = False

    def submit_task(executor, task):
        return executor.submit(
            execute_sampling_task,
            task,
            db_target=db_target,
            project=project,
            semaphore=semaphores[provider_concurrency_group(task["base"]["provider"] or "unknown")],
            max_retries=max_retries,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        task_iter = iter(tasks)
        pending = set()

        while len(pending) < max_workers:
            if should_pause and should_pause():
                paused = True
                break
            try:
                pending.add(submit_task(executor, next(task_iter)))
            except StopIteration:
                break

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
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

            while len(pending) < max_workers:
                if should_pause and should_pause():
                    paused = True
                    break
                try:
                    pending.add(submit_task(executor, next(task_iter)))
                except StopIteration:
                    break
            if paused:
                continue

    return {
        "batch_id": batch_id,
        "total": completed,
        "failed": failed,
        "success": completed - failed,
        "status": "paused" if paused else "completed",
    }


def run_batch(
    conn,
    project_id: int,
    config: dict[str, Any],
    *,
    batch_id: str | None = None,
    progress_callback=None,
    should_pause=None,
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

    db_target = connection_db_target(conn)
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
        db_target=db_target,
        project=project,
        config=config,
        batch_id=batch_id,
        progress_callback=progress_callback,
        should_pause=should_pause,
    )


def build_missing_tasks_for_batch(conn, batch_id: str, config: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    from .db import get_sampling_batch, list_runs_by_batch

    batch = get_sampling_batch(conn, batch_id)
    if not batch:
        raise ValueError("批次不存在")
    project_id = int(batch["project_id"])
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    run_config = {**(batch.get("config") or {}), **(config or {})}
    model_configs: dict[int, dict[str, Any]] = {}
    for provider_cfg in run_config.get("models") or []:
        model_config_id = int(provider_cfg.get("model_config_id", 0))
        model_config = get_model_config(conn, model_config_id)
        if not model_config:
            raise ValueError("模型配置不存在")
        model_configs[model_config_id] = model_config
    planned_tasks = build_tasks(
        batch_id=batch_id,
        project_id=project_id,
        config=run_config,
        questions=list_questions(conn, project_id),
        model_configs=model_configs,
    )
    completed_keys = {logical_key_from_run(row) for row in list_runs_by_batch(conn, batch_id, limit=100000)}
    return project_id, run_config, [task for task in planned_tasks if logical_key_from_task(task) not in completed_keys]


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
        db_target=connection_db_target(conn),
        project=project,
        config=rerun_config,
        batch_id=batch_id,
    )
