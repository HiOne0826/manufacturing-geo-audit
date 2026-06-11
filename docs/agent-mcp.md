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
