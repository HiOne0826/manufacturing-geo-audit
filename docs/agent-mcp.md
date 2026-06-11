# 阶段 6 Agent / MCP 接入

阶段 6 提供内部 Agent API。MCP 或其他 Agent 不直接接触模型 API Key，只调用本系统后端。

## 鉴权

配置：

```bash
AGENT_API_TOKEN=替换为内部随机 token
```

调用时使用：

```http
Authorization: Bearer <AGENT_API_TOKEN>
```

## 接口

### 创建批次

```text
POST /api/agent/batches
```

请求：

```json
{
  "project_id": 1,
  "csv_text": "question_id,question\nA001,请介绍目标品牌",
  "model_ids": [1, 2],
  "options": {
    "repeat_count": 1,
    "max_workers": 8,
    "retry_count": 1
  }
}
```

返回：

```json
{
  "batch_id": "batch-...",
  "total": 150,
  "status": "queued"
}
```

### 查询状态

```text
GET /api/agent/batches/{batch_id}
```

### 获取导出路径

```text
GET /api/agent/batches/{batch_id}/export
```

返回可下载路径：

```json
{
  "batch_id": "batch-...",
  "format": "xls",
  "path": "/api/export/batches/batch-.../runs.xls"
}
```

### 重跑失败

```text
POST /api/agent/batches/{batch_id}/rerun_failed
```

## CLI

```bash
AGENT_API_TOKEN=... python3 scripts/agent_client.py \
  --base-url http://127.0.0.1:8765 \
  create-batch --project-id 1 --model-id 1 --csv-file questions.csv
```

查询：

```bash
AGENT_API_TOKEN=... python3 scripts/agent_client.py status batch-xxx
```

导出：

```bash
AGENT_API_TOKEN=... python3 scripts/agent_client.py export batch-xxx
```

## MCP 工具映射

- `create_geo_audit_batch` -> `POST /api/agent/batches`
- `get_geo_audit_batch_status` -> `GET /api/agent/batches/{batch_id}`
- `export_geo_audit_batch` -> `GET /api/agent/batches/{batch_id}/export`
- `rerun_failed_geo_audit_tasks` -> `POST /api/agent/batches/{batch_id}/rerun_failed`

## MCP Wrapper

本仓库已提供轻量 stdio MCP wrapper：

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765 \
AGENT_API_TOKEN=... \
python3 -m mcp.server
```

也兼容：

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765 \
AGENT_API_TOKEN=... \
python3 mcp/server.py
```

实现位置：

- `mcp/server.py`：MCP JSON-RPC stdio server。
- `mcp/client.py`：Agent API HTTP client。
- `mcp/schemas.py`：MCP 工具输入校验和 JSON Schema。
- `mcp/README.md`：客户端配置和输入输出示例。

当前实现不额外依赖 Python MCP SDK，避免本地 `mcp/` 目录和 SDK 包名冲突。它提供标准 `tools/list` 和 `tools/call`，适合 Codex、Claude Desktop 或其他 MCP client 通过 stdio 调用。

安全边界：

- MCP wrapper 不读取 `.env` 中的模型服务商 API Key。
- MCP wrapper 不直接调用模型服务商。
- MCP wrapper 响应不返回 `AGENT_API_TOKEN`。
- 测试默认 mock HTTP，不触发真实模型调用。
