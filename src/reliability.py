from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    from enum import StrEnum
except ImportError:  # Python 3.10 production compatibility.
    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str(self.value)


class ErrorCode(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    REGION_BLOCK = "region_block"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UPSTREAM = "upstream"
    MALFORMED_RESPONSE = "malformed_response"
    SEARCH_DEPENDENCY = "search_dependency"
    CONFIGURATION = "configuration"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class BatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED_SYSTEM = "failed_system"
    CANCELLED = "cancelled"


TERMINAL_BATCH_STATUSES = {
    BatchStatus.COMPLETED,
    BatchStatus.FAILED_SYSTEM,
    BatchStatus.CANCELLED,
}


ALLOWED_BATCH_TRANSITIONS: dict[str, set[str]] = {
    BatchStatus.QUEUED: {
        BatchStatus.RUNNING,
        BatchStatus.PAUSED,
        BatchStatus.FAILED_SYSTEM,
        BatchStatus.CANCELLED,
    },
    BatchStatus.RUNNING: {
        BatchStatus.PAUSE_REQUESTED,
        BatchStatus.COMPLETED,
        BatchStatus.FAILED_SYSTEM,
    },
    BatchStatus.PAUSE_REQUESTED: {
        BatchStatus.RUNNING,
        BatchStatus.PAUSED,
        BatchStatus.COMPLETED,
        BatchStatus.FAILED_SYSTEM,
    },
    BatchStatus.PAUSED: {
        BatchStatus.RUNNING,
        BatchStatus.COMPLETED,
        BatchStatus.FAILED_SYSTEM,
        BatchStatus.CANCELLED,
    },
    BatchStatus.COMPLETED: {BatchStatus.RUNNING},
    BatchStatus.FAILED_SYSTEM: {BatchStatus.RUNNING, BatchStatus.CANCELLED},
    # Backward compatibility for batches created before V2.
    "failed": {BatchStatus.RUNNING, BatchStatus.CANCELLED},
}


@dataclass(frozen=True)
class ClassifiedError:
    code: ErrorCode
    retryable: bool
    terminal: bool = False


_PATTERNS: tuple[tuple[ErrorCode, re.Pattern[str], bool, bool], ...] = (
    (ErrorCode.AUTH, re.compile(r"\b(401|unauthori[sz]ed|invalid api key|authentication|鉴权|密钥无效)\b", re.I), False, True),
    (ErrorCode.QUOTA, re.compile(r"\b(quota|insufficient[_ ]quota|余额不足|额度不足|credit)\b", re.I), False, True),
    (ErrorCode.RATE_LIMIT, re.compile(r"\b(429|rate.?limit|too many requests|限流)\b", re.I), True, False),
    (ErrorCode.REGION_BLOCK, re.compile(r"\b(region|country).*(blocked|unsupported|restricted)|地区限制|区域限制", re.I), False, True),
    (ErrorCode.MODEL_NOT_FOUND, re.compile(r"\b(model[_ -]?not[_ -]?found|unknown model|404.*model|模型不存在)\b", re.I), False, True),
    (ErrorCode.TIMEOUT, re.compile(r"\b(timeout|timed out|deadline exceeded|超时)\b", re.I), True, False),
    (ErrorCode.SEARCH_DEPENDENCY, re.compile(r"\b(bocha|searchpro|web.?search|搜索依赖|联网搜索|博查)\b", re.I), True, False),
    (
        ErrorCode.MALFORMED_RESPONSE,
        re.compile(r"\b(json|parse|decode|malformed|invalid response|empty (response|output)|解析失败|响应格式)\b|回答内容为空|空响应", re.I),
        True,
        False,
    ),
    (ErrorCode.NETWORK, re.compile(r"\b(dns|tls|ssl|connection|network|urlerror|网络|连接失败)\b", re.I), True, False),
    (
        ErrorCode.UPSTREAM,
        re.compile(r"\b(408|409|425|500|502|503|504|upstream|service unavailable|circuit.?open|half.?open)\b|信息源熔断中|信息源正在半开试探", re.I),
        True,
        False,
    ),
    (ErrorCode.CONFIGURATION, re.compile(r"缺少|未配置|unsupported|暂不支持|configuration", re.I), False, True),
    (ErrorCode.CANCELLED, re.compile(r"cancelled|canceled|已取消", re.I), False, True),
)


def classify_error(message: str, status_code: int | None = None, retryable: bool | None = None) -> ClassifiedError:
    text = f"{status_code or ''} {message or ''}".strip()
    for code, pattern, default_retryable, terminal in _PATTERNS:
        if pattern.search(text):
            return ClassifiedError(code, default_retryable if retryable is None else retryable, terminal)
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return ClassifiedError(ErrorCode.UPSTREAM, True if retryable is None else retryable)
    return ClassifiedError(ErrorCode.UNKNOWN, bool(retryable))


def batch_outcome(status: str, success: int, failed: int, completed: int, total: int) -> str:
    if status in {BatchStatus.FAILED_SYSTEM, "failed"}:
        return "failure"
    if status in {BatchStatus.QUEUED, BatchStatus.RUNNING, BatchStatus.PAUSE_REQUESTED, BatchStatus.PAUSED}:
        return "pending"
    if failed > 0:
        return "partial_failure"
    if total > 0 and completed >= total and success >= total:
        return "clean"
    return "pending"


def assert_batch_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = ALLOWED_BATCH_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"非法批次状态迁移：{current} -> {target}")


def stable_config_fingerprint(payload: dict[str, Any]) -> str:
    """Return a secret-safe, stable identity for an immutable execution snapshot."""
    safe = _without_secrets(payload)
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_secrets(item)
            for key, item in value.items()
            if not any(token in str(key).lower() for token in ("api_key", "secret", "token", "password", "cookie", "authorization"))
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_secrets(item) for item in value)
    return value
