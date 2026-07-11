from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .db import utc_now
from .provider_health import (
    credential_fingerprint,
    health_scope,
    record_provider_failure,
    record_provider_success,
    redact_health_message,
)
from .reliability import classify_error
from .runtime_env import resolve_provider_api_key


PROBE_PROMPT = "这是信息源可用性探针。请只回复：probe-ok"
SEARCH_PROBE_PROMPT = "搜索 OpenAI 官方网站，并用一句话概括其首页主题。"


@dataclass(frozen=True)
class ProbeSpec:
    kind: str
    mode: str
    search_enabled: bool
    require_citation: bool = False


PROBE_SPECS: dict[str, ProbeSpec] = {
    "pure": ProbeSpec("pure", "pure", False),
    "search": ProbeSpec("search", "search", True),
    "citation": ProbeSpec("citation", "citation", True, True),
}


def probe_scope(model_config: dict[str, Any], kind: str) -> dict[str, str]:
    provider = str(model_config.get("provider") or "")
    resolved = resolve_provider_api_key(provider, str(model_config.get("api_key") or ""))
    return health_scope(
        provider,
        str(model_config.get("model") or ""),
        PROBE_SPECS[kind].mode,
        endpoint=str(model_config.get("api_base") or ""),
        credential_fp=credential_fingerprint(resolved),
        exit_region=str(model_config.get("exit_region") or ""),
    )


def supported_probe_kinds(model_config: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    if bool(model_config.get("supports_pure")):
        kinds.append("pure")
    if bool(model_config.get("supports_search")):
        kinds.append("search")
    if bool(model_config.get("supports_search")) and bool(model_config.get("supports_citation")):
        kinds.append("citation")
    return kinds


def _default_call(model_config: dict[str, Any], spec: ProbeSpec) -> dict[str, Any]:
    # Imported lazily so loading the app never starts a paid call or browser.
    from .adapters import call_configured_model, test_model_config

    if model_config.get("provider") == "deepseek_web":
        from .deepseek_web import DeepSeekWebBrowser

        browser = DeepSeekWebBrowser()
        try:
            return browser.sample(
                batch_id="isolated-provider-probe",
                task_id=f"probe-{uuid.uuid4().hex}",
                question=SEARCH_PROBE_PROMPT,
            )
        finally:
            browser.close()
    if spec.kind == "pure":
        return test_model_config(model_config)
    return call_configured_model(
        model_config,
        SEARCH_PROBE_PROMPT,
        True,
        0,
        {
            "search_mode": "auto",
            "thinking_type": "disabled",
            "reasoning_effort": "",
            "thinking_budget": None,
        },
    )


def run_active_probe(
    conn,
    model_config: dict[str, Any],
    kind: str,
    *,
    call: Callable[[dict[str, Any], ProbeSpec], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run an isolated active probe; it never creates a batch, task, run or report row."""
    if kind not in PROBE_SPECS:
        raise ValueError(f"未知探针类型：{kind}")
    if kind not in supported_probe_kinds(model_config):
        raise ValueError(f"当前信息源不支持 {kind} 探针")
    spec = PROBE_SPECS[kind]
    provider = str(model_config.get("provider") or "")
    model = str(model_config.get("model") or "")
    scope = probe_scope(model_config, kind)
    resolved_credential = resolve_provider_api_key(provider, str(model_config.get("api_key") or ""))
    started = utc_now()
    try:
        result = (call or _default_call)(model_config, spec)
        citations = result.get("citations") or []
        if spec.require_citation and not citations:
            raise RuntimeError("搜索依赖：引用提取探针未返回引用")
        latency_ms = max(0, int(result.get("latency_ms", 0) or 0))
        record_provider_success(
            conn,
            provider,
            model,
            spec.mode,
            checked=True,
            latency_ms=latency_ms,
            endpoint=scope["endpoint"],
            credential=resolved_credential,
            credential_fp=scope["credential_fingerprint"],
            exit_region=scope["exit_region"],
            source="active",
        )
        # Deliberately return only health metadata, not prompts, answers or raw responses.
        return {
            "ok": True,
            "probe_type": kind,
            "provider": provider,
            "model": model,
            "mode": spec.mode,
            "latency_ms": latency_ms,
            "citation_count": len(citations) if spec.require_citation else None,
            "scope": scope,
            "started_at": started,
            "checked_at": utc_now(),
        }
    except Exception as exc:
        classified = classify_error(
            str(exc), getattr(exc, "status_code", None), getattr(exc, "retryable", None)
        )
        record_provider_failure(
            conn,
            provider,
            model,
            spec.mode,
            classified,
            str(exc),
            checked=True,
            endpoint=scope["endpoint"],
            credential=resolved_credential,
            credential_fp=scope["credential_fingerprint"],
            exit_region=scope["exit_region"],
            source="active",
        )
        return {
            "ok": False,
            "probe_type": kind,
            "provider": provider,
            "model": model,
            "mode": spec.mode,
            "error_code": classified.code.value,
            "error": redact_health_message(str(exc), (resolved_credential,)),
            "scope": scope,
            "started_at": started,
            "checked_at": utc_now(),
        }


def run_probe_cycle(
    connection_factory: Callable[[], Any],
    model_configs: Iterable[dict[str, Any]],
    *,
    call: Callable[[dict[str, Any], ProbeSpec], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for config in model_configs:
        if not bool(config.get("active", True)):
            continue
        for kind in supported_probe_kinds(config):
            with connection_factory() as conn:
                results.append(run_active_probe(conn, config, kind, call=call))
    return results


class ActiveProbeScheduler:
    """Opt-in six-hour scheduler. Disabled unless explicitly configured."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        config_loader: Callable[[], Iterable[dict[str, Any]]],
        *,
        interval_seconds: int = 21600,
        call: Callable[[dict[str, Any], ProbeSpec], dict[str, Any]] | None = None,
    ) -> None:
        self.connection_factory = connection_factory
        self.config_loader = config_loader
        self.interval_seconds = max(60, int(interval_seconds))
        self.call = call
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self._run, name="provider-active-probes", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        # Wait first: starting the API process must never trigger surprise paid traffic.
        while not self._stop.wait(self.interval_seconds):
            try:
                run_probe_cycle(self.connection_factory, self.config_loader(), call=self.call)
            except Exception:
                # A scheduler failure cannot take down the API. The next interval retries.
                continue


def start_optional_probe_scheduler(
    connection_factory: Callable[[], Any],
    config_loader: Callable[[], Iterable[dict[str, Any]]],
    *,
    call: Callable[[dict[str, Any], ProbeSpec], dict[str, Any]] | None = None,
) -> ActiveProbeScheduler | None:
    """Return a running scheduler only with both explicit enable and live-call permission."""
    if os.environ.get("PROVIDER_ACTIVE_PROBES_ENABLED") != "1":
        return None
    if os.environ.get("ALLOW_LIVE_MODEL_CALLS") != "1":
        return None
    interval = int(os.environ.get("PROVIDER_ACTIVE_PROBE_INTERVAL_SECONDS", "21600") or 21600)
    scheduler = ActiveProbeScheduler(connection_factory, config_loader, interval_seconds=interval, call=call)
    scheduler.start()
    return scheduler
