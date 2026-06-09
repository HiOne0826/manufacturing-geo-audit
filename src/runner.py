from __future__ import annotations

import uuid
from typing import Any

from .adapters import AdapterError, call_configured_model
from .db import (
    get_model_config,
    get_project,
    insert_evaluation,
    insert_run,
    list_questions,
    utc_now,
)
from .evaluator import evaluate_answer


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
    batch_id = batch_id or f"batch-{uuid.uuid4().hex[:10]}"
    total_expected = len(questions) * len(providers) * repeat_count
    total = 0
    failed = 0

    for question in questions:
        for provider_cfg in providers:
            for repeat_index in range(1, repeat_count + 1):
                run_id = f"run-{uuid.uuid4().hex}"
                model_config_id = int(provider_cfg.get("model_config_id", 0))
                model_config = get_model_config(conn, model_config_id)
                if not model_config:
                    raise ValueError("模型配置不存在")
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
                try:
                    result = call_configured_model(
                        runtime_model_config,
                        question["question"],
                        search_enabled,
                        model_temperature,
                        {
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
                        },
                    )
                    run = {
                        **base,
                        "provider": result.get("provider", provider),
                        "model": result.get("model", model or provider),
                        "model_version": result.get("model_version", runtime_model_config.get("model_version", "")),
                        "response_text": result.get("response_text", ""),
                        "citations": result.get("citations", []),
                        "latency_ms": result.get("latency_ms", 0),
                        "status": "success",
                        "raw_response": result.get("raw_response", {}),
                    }
                    insert_run(conn, run)
                    evaluation = evaluate_answer(
                        run_id=run_id,
                        answer=run["response_text"],
                        target_brand=question.get("target_brand") or project.get("brand_name", ""),
                        competitors=question.get("competitor_brands") or project.get("competitors", ""),
                        website_domain=project.get("website_domain", ""),
                        citations=run.get("citations", []),
                    )
                    insert_evaluation(conn, evaluation)
                except AdapterError as exc:
                    failed += 1
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
                conn.commit()
                total += 1
                if progress_callback:
                    progress_callback(
                        {
                            "batch_id": batch_id,
                            "total": total_expected,
                            "completed": total,
                            "failed": failed,
                            "success": total - failed,
                            "provider": provider,
                            "model": model or provider,
                            "question_id": question["id"],
                            "repeat_index": repeat_index,
                        }
                    )
    return {"batch_id": batch_id, "total": total, "failed": failed, "success": total - failed}
