# 制造业 GEO 审计 MCP Wrapper

这个目录提供一个轻量 MCP stdio server，把 MCP tools 映射到后端已有的 Agent API。

MCP Server 不直接调用任何模型服务商，不读取模型 API Key，不直接连接数据库。

## 环境变量

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765
AGENT_API_TOKEN=替换为内部随机 token
GEO_AUDIT_REQUEST_TIMEOUT=30
```

`AGENT_API_TOKEN` 是本系统内部 Agent API token，不是模型服务商 API Key。

## 启动

```bash
GEO_AUDIT_BASE_URL=http://127.0.0.1:8765 \
AGENT_API_TOKEN=agent-test-token \
python3 -m mcp.server
```

## 暴露工具

- `create_geo_audit_batch`
- `get_geo_audit_batch_status`
- `export_geo_audit_batch`
- `rerun_failed_geo_audit_tasks`

## Agent 输入输出

创建批次：

```json
{
  "project_id": 1,
  "model_ids": [1, 2, 3],
  "csv_text": "question_id,question\nQ001,请介绍目标品牌",
  "options": {
    "repeat_count": 1,
    "max_workers": 8,
    "retry_count": 1
  }
}
```

返回内容是 JSON 文本，例如：

```json
{
  "batch_id": "batch-xxx",
  "total": 3,
  "status": "queued"
}
```

查询状态：

```json
{
  "batch_id": "batch-xxx"
}
```

获取导出路径：

```json
{
  "batch_id": "batch-xxx",
  "format": "xls"
}
```

失败重跑：

```json
{
  "batch_id": "batch-xxx",
  "options": {
    "max_workers": 8,
    "retry_count": 1
  }
}
```

## Claude Desktop 配置示例

```json
{
  "mcpServers": {
    "manufacturing-geo-audit": {
      "command": "python3",
      "args": [
        "-m",
        "mcp.server"
      ],
      "cwd": "/Users/ericgao/Documents/Projects/manufacturing-geo-audit",
      "env": {
        "GEO_AUDIT_BASE_URL": "http://127.0.0.1:8765",
        "AGENT_API_TOKEN": "替换为内部 token"
      }
    }
  }
}
```

## 安全边界

- MCP 响应不会返回 `AGENT_API_TOKEN`。
- MCP 响应不会返回模型服务商 API Key。
- 后端模型配置接口仍只返回 `has_key` 和掩码信息。
- 测试默认只 mock HTTP，不触发真实模型调用。
