from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .schemas import CreateBatchInput, ExportBatchInput, RerunFailedInput


class AgentApiError(RuntimeError):
    pass


class AgentApiClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        if not self.base_url:
            raise AgentApiError("GEO_AUDIT_BASE_URL 未配置")
        if not self.token:
            raise AgentApiError("AGENT_API_TOKEN 未配置")

    @classmethod
    def from_env(cls) -> "AgentApiClient":
        timeout = float(os.environ.get("GEO_AUDIT_REQUEST_TIMEOUT", "30"))
        return cls(
            os.environ.get("GEO_AUDIT_BASE_URL", "http://127.0.0.1:8765"),
            os.environ.get("AGENT_API_TOKEN", ""),
            timeout=timeout,
        )

    def create_batch(self, data: CreateBatchInput) -> dict[str, Any]:
        return self.request_json("POST", "/api/agent/batches", data.to_agent_payload())

    def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/api/agent/batches/{urllib.parse.quote(batch_id)}")

    def export_batch(self, data: ExportBatchInput) -> dict[str, Any]:
        result = self.request_json("GET", f"/api/agent/batches/{urllib.parse.quote(data.batch_id)}/export")
        path = result.get("path")
        if isinstance(path, str) and path.startswith("/"):
            result = {**result, "url": f"{self.base_url}{path}"}
        return result

    def rerun_failed(self, data: RerunFailedInput) -> dict[str, Any]:
        return self.request_json(
            "POST",
            f"/api/agent/batches/{urllib.parse.quote(data.batch_id)}/rerun_failed",
            {"options": data.options},
        )

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = self._read_error_message(exc)
            if exc.code == 401:
                raise AgentApiError("Agent token 无效或未配置") from exc
            raise AgentApiError(f"Agent API 返回 {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise AgentApiError(f"Agent API 不可达: {self.base_url} ({exc.reason})") from exc
        except TimeoutError as exc:
            raise AgentApiError(f"Agent API 请求超时: {self.base_url}") from exc

    def _read_error_message(self, exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                return data["error"]
            return raw[:300]
        except Exception:
            return exc.reason or "未知错误"

