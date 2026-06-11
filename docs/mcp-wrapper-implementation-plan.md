# MCP Wrapper 实施方案

分支：`feature/mcp-wrapper`

更新时间：2026-06-11

## 1. 目标

为制造业 GEO 审计系统增加一层 MCP Server，让 Codex、Claude Desktop、内部 Agent 或其他 MCP Client 能通过标准工具调用本系统的 Agent API。

MCP Server 只做协议包装，不直接访问模型服务商，不读取模型 API Key，不直接操作数据库。

目标链路：

```text
MCP Client / Agent
  -> MCP Server
    -> HTTP Agent API
      -> manufacturing-geo-audit backend
        -> sampling_batches / model_runs / exports
```

## 2. 不做什么

本阶段不做：

- 不重写后端。
- 不让 MCP Server 直接调用模型 API。
- 不让 MCP Server 读取或返回模型 API Key。
- 不在 MCP 内实现采样逻辑。
- 不让 MCP 直接连 SQLite/PostgreSQL。
- 不做外部多用户权限系统。

## 3. 已有基础

当前后端已经具备内部 Agent API：

- `POST /api/agent/batches`
- `GET /api/agent/batches/{batch_id}`
- `GET /api/agent/batches/{batch_id}/export`
- `POST /api/agent/batches/{batch_id}/rerun_failed`

鉴权：

```http
Authorization: Bearer <AGENT_API_TOKEN>
```

现有文档：

- `docs/agent-mcp.md`
- `scripts/agent_client.py`

## 4. 推荐目录

新增：

```text
mcp/
  server.py
  client.py
  schemas.py
  README.md
docs/
  mcp-wrapper-implementation-plan.md
```

职责：

- `mcp/server.py`：注册 MCP tools。
- `mcp/client.py`：封装 HTTP Agent API。
- `mcp/schemas.py`：集中定义输入输出结构。
- `mcp/README.md`：使用说明、环境变量、客户端配置示例。

## 5. 环境变量

MCP Server 只需要两个核心配置：

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765
AGENT_API_TOKEN=...
```

可选：

```bash
GEO_AUDIT_REQUEST_TIMEOUT=30
```

注意：

- `AGENT_API_TOKEN` 是内部系统 token，不是模型服务商 API Key。
- 不在 MCP 响应中返回 token。
- 不在日志中打印 token。

## 6. MCP Tools

### 6.1 `create_geo_audit_batch`

用途：创建一个采样批次，可附带 CSV 问题文本，也可复用项目已有问题。

输入：

```json
{
  "project_id": 1,
  "model_ids": [12, 13, 14],
  "csv_text": "question_id,question\nQ001,请介绍目标品牌",
  "options": {
    "repeat_count": 1,
    "max_workers": 8,
    "retry_count": 1
  }
}
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `project_id` | 是 | 目标项目 ID |
| `model_ids` | 是 | 模型配置 ID 列表 |
| `csv_text` | 否 | 问题 CSV 文本；为空则使用项目已有问题 |
| `options.repeat_count` | 否 | 默认 1 |
| `options.max_workers` | 否 | 默认走后端配置 |
| `options.retry_count` | 否 | 默认走后端配置 |

输出：

```json
{
  "batch_id": "batch-xxxx",
  "total": 18,
  "status": "queued"
}
```

HTTP 映射：

```text
POST /api/agent/batches
```

### 6.2 `get_geo_audit_batch_status`

用途：查询批次状态。

输入：

```json
{
  "batch_id": "batch-xxxx"
}
```

输出：

```json
{
  "batch": {
    "batch_id": "batch-xxxx",
    "project_id": 1,
    "status": "completed",
    "total": 18,
    "completed": 18,
    "success": 18,
    "failed": 0,
    "error": "",
    "created_at": "...",
    "started_at": "...",
    "finished_at": "..."
  }
}
```

HTTP 映射：

```text
GET /api/agent/batches/{batch_id}
```

### 6.3 `export_geo_audit_batch`

用途：获取批次导出路径。

输入：

```json
{
  "batch_id": "batch-xxxx",
  "format": "xls"
}
```

输出：

```json
{
  "batch_id": "batch-xxxx",
  "format": "xls",
  "path": "/api/export/batches/batch-xxxx/runs.xls",
  "url": "http://127.0.0.1:8765/api/export/batches/batch-xxxx/runs.xls"
}
```

HTTP 映射：

```text
GET /api/agent/batches/{batch_id}/export
```

### 6.4 `rerun_failed_geo_audit_tasks`

用途：重跑某个批次的失败任务。

输入：

```json
{
  "batch_id": "batch-xxxx",
  "options": {
    "max_workers": 8,
    "retry_count": 1
  }
}
```

输出：

```json
{
  "batch_id": "batch-xxxx",
  "total": 3,
  "status": "queued"
}
```

HTTP 映射：

```text
POST /api/agent/batches/{batch_id}/rerun_failed
```

## 7. 错误处理

MCP 层错误分三类：

| 类型 | 处理 |
| --- | --- |
| 鉴权错误 | 返回“Agent token 无效或未配置” |
| 后端不可达 | 返回 base URL、timeout、连接错误摘要 |
| 后端业务错误 | 透传后端 `error` 字段，但不透传敏感 header |

禁止：

- 打印 `AGENT_API_TOKEN`。
- 打印模型 API Key。
- 将后端异常堆栈直接返回给 Agent。

## 8. 测试计划

新增：

```text
tests/test_mcp_wrapper.py
```

测试项：

- MCP client 构造请求时带 Bearer token。
- `create_geo_audit_batch` 正确映射到 `/api/agent/batches`。
- `get_geo_audit_batch_status` 正确解析 batch。
- `export_geo_audit_batch` 返回绝对 URL。
- token 不出现在错误消息里。
- HTTP 401 转换为可读错误。
- HTTP 500 转换为可读错误。

本阶段测试默认不调用真实模型。可用本地 fake HTTP handler 或 monkeypatch `mcp.client`。

## 9. 本地验收

前置：

```bash
APP_PASSWORD=test-password \
APP_SESSION_SECRET=test-secret \
AGENT_API_TOKEN=agent-test-token \
python3 app.py
```

验证 Agent API：

```bash
AGENT_API_TOKEN=agent-test-token python3 scripts/agent_client.py \
  --base-url http://127.0.0.1:8765 \
  status batch-xxxx
```

验证 MCP Server：

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765 \
AGENT_API_TOKEN=agent-test-token \
python3 mcp/server.py
```

验收标准：

- MCP Client 能创建批次。
- 能查询状态。
- 能拿到导出路径。
- 能触发失败重跑。
- 不返回任何模型 API Key。
- 默认测试不调用真实模型。

## 10. 后续增强

可选增强：

- 增加 `list_geo_audit_projects`。
- 增加 `list_geo_audit_models`，只返回 `id/provider/label/model/has_key/sampling_defaults`。
- 增加 `get_geo_audit_batch_runs`，返回失败摘要和模型耗时。
- 增加 resource：暴露最近批次摘要。
- 增加 prompt：自动生成制造业 GEO 审计问题 CSV。

优先级建议：

1. 先完成 4 个核心 tools。
2. 再补项目/模型列表。
3. 最后补 runs 摘要和 prompts。
