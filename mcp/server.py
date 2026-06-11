#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mcp.client import AgentApiClient, AgentApiError
    from mcp.schemas import (
        BATCH_ID_SCHEMA,
        CREATE_BATCH_SCHEMA,
        EXPORT_BATCH_SCHEMA,
        RERUN_FAILED_SCHEMA,
        BatchIdInput,
        CreateBatchInput,
        ExportBatchInput,
        RerunFailedInput,
        ValidationError,
    )
else:
    from .client import AgentApiClient, AgentApiError
    from .schemas import (
        BATCH_ID_SCHEMA,
        CREATE_BATCH_SCHEMA,
        EXPORT_BATCH_SCHEMA,
        RERUN_FAILED_SCHEMA,
        BatchIdInput,
        CreateBatchInput,
        ExportBatchInput,
        RerunFailedInput,
        ValidationError,
    )


TOOLS = [
    {
        "name": "create_geo_audit_batch",
        "description": "创建制造业 GEO 审计采样批次。",
        "inputSchema": CREATE_BATCH_SCHEMA,
    },
    {
        "name": "get_geo_audit_batch_status",
        "description": "查询制造业 GEO 审计批次状态。",
        "inputSchema": BATCH_ID_SCHEMA,
    },
    {
        "name": "export_geo_audit_batch",
        "description": "获取制造业 GEO 审计批次 XLS 导出路径。",
        "inputSchema": EXPORT_BATCH_SCHEMA,
    },
    {
        "name": "rerun_failed_geo_audit_tasks",
        "description": "重跑某个制造业 GEO 审计批次中的失败任务。",
        "inputSchema": RERUN_FAILED_SCHEMA,
    },
]


def call_tool(name: str, arguments: dict[str, Any], client: AgentApiClient | None = None) -> dict[str, Any]:
    api = client or AgentApiClient.from_env()
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "create_geo_audit_batch": lambda args: api.create_batch(CreateBatchInput.from_dict(args)),
        "get_geo_audit_batch_status": lambda args: api.get_batch_status(BatchIdInput.from_dict(args).batch_id),
        "export_geo_audit_batch": lambda args: api.export_batch(ExportBatchInput.from_dict(args)),
        "rerun_failed_geo_audit_tasks": lambda args: api.rerun_failed(RerunFailedInput.from_dict(args)),
    }
    if name not in handlers:
        raise ValidationError(f"未知 MCP 工具: {name}")
    return handlers[name](arguments or {})


def make_text_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
            }
        ],
        "isError": is_error,
    }


def handle_request(message: dict[str, Any], client: AgentApiClient | None = None) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if request_id is None:
        return None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "manufacturing-geo-audit", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            result = make_text_result(call_tool(name, arguments, client=client))
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"不支持的方法: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (ValidationError, AgentApiError) as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": make_text_result({"error": str(exc)}, is_error=True),
        }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": make_text_result({"error": f"MCP 工具调用失败: {exc.__class__.__name__}"}, is_error=True),
        }


def serve_stdio(client: AgentApiClient | None = None) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle_request(message, client=client)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"JSON 解析失败: {exc.msg}"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
