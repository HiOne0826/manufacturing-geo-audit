# 制造业 GEO 审计系统当前状态

更新时间：2026-06-11

## 定位

本项目是一套制造业 GEO 效果检测 / 审计系统，用于批量采样多个模型对制造业问题的回答，并保存回答、引用、品牌出现情况、竞品共现、运行参数和评估结果。

它不是内容生产系统。它的核心目标是为客户 GEO 现状报告提供可追溯的数据底稿。

## 当前架构

默认本地模式：

```text
Python http.server
  -> 静态前端
  -> 应用全局密码 / HttpOnly Cookie
  -> SQLite WAL
  -> ThreadPoolExecutor
  -> model_runs / answer_evaluations / sampling_batches
```

正式任务模式：

```text
Nginx Basic Auth
  -> Python http.server
    -> 应用全局密码
    -> PostgreSQL
    -> Redis + RQ Worker
    -> Model Adapters
    -> Exporter
    -> Agent API
```

## 安全边界

- 公网入口建议先过 Nginx Basic Auth。
- 应用内使用 `APP_PASSWORD` 做全局密码。
- 登录态使用 HMAC 签名 HttpOnly Cookie。
- `/api/*` 默认需要认证，`/api/health` 和 auth 接口除外。
- Agent API 使用 `AGENT_API_TOKEN` Bearer token。
- `/api/models` 不返回明文 `api_key`，只返回 `has_key` 和掩码。
- 测试默认不允许调用真实模型。
- 真实模型调用必须显式设置 `ALLOW_LIVE_MODEL_CALLS=1`。

## 数据库

默认 SQLite：

```text
data/geo_audit.db
```

核心表：

- `projects`
- `questions`
- `model_configs`
- `sampling_batches`
- `model_runs`
- `answer_evaluations`

正式 PostgreSQL 模式：

```bash
DATABASE_URL=postgresql://geo:password@127.0.0.1:5432/geo_audit
```

Schema：

```text
deploy/postgres/schema.sql
```

`init_db()` 会按 `DATABASE_URL` 自动选择 SQLite 或 PostgreSQL。

## 任务系统

默认 inline 模式：

```bash
TASK_QUEUE_BACKEND=inline
```

Web 进程内启动后台线程执行采样任务。

正式 RQ 模式：

```bash
TASK_QUEUE_BACKEND=rq
REDIS_URL=redis://127.0.0.1:6379/0
RQ_QUEUE_NAME=geo-audit
```

流程：

1. Web 创建 `sampling_batches`，状态为 `queued`。
2. Web 将任务入队。
3. Worker 执行 `src.tasks.perform_batch`。
4. Worker 写入 `model_runs` 和 `answer_evaluations`。
5. Worker 更新 `sampling_batches`。
6. Web 从数据库恢复进度。

## 并发策略

相关配置：

```bash
SAMPLING_MAX_WORKERS=8
SAMPLING_PROVIDER_MAX_WORKERS=3
SAMPLING_REQUEST_TIMEOUT=120
SAMPLING_RETRY_COUNT=1
```

执行策略：

- 每个采样任务独立数据库连接。
- 每个 provider 有独立并发限制。
- 单任务失败不会中断整个 batch。
- 失败任务会落库为 `status=failed`。
- 支持按 batch 重跑失败项。

## 模型接入

当前模型库支持的主要 provider：

- OpenAI
- Gemini
- 豆包
- DeepSeek
- 通义千问
- 腾讯混元
- Kimi
- 文心一言
- MiniMax
- Mock

模型级采样默认值由后端返回：

```json
{
  "sampling_defaults": {
    "temperature": 0.6,
    "reasoning_effort": "",
    "defaults_note": "..."
  }
}
```

当前关键默认：

| Provider | 默认 |
| --- | --- |
| Kimi K2.5 | `temperature=0.6` |
| DeepSeek | `temperature=1` |
| 通义千问 | `temperature=0.1` |
| 腾讯混元 | `temperature=0` |
| 文心一言 | `temperature=0.1` |
| 豆包 | `temperature=1` |

前端抽样页和后端 runner 都会读取这些默认值；即使绕过前端调用 API，未传 `temperature` 时后端也会套用 provider 默认值。

## Agent API

Agent API 不接触模型 API Key，只调用本系统后端。

鉴权：

```http
Authorization: Bearer <AGENT_API_TOKEN>
```

接口：

- `POST /api/agent/batches`
- `GET /api/agent/batches/{batch_id}`
- `GET /api/agent/batches/{batch_id}/export`
- `POST /api/agent/batches/{batch_id}/rerun_failed`

CLI：

```bash
AGENT_API_TOKEN=... python3 scripts/agent_client.py \
  --base-url http://127.0.0.1:8765 \
  create-batch --project-id 1 --model-id 1 --csv-file questions.csv
```

详情见 `docs/agent-mcp.md`。

## 验证结果

### 自动测试

命令：

```bash
python3 -m unittest discover -s tests
```

最近结果：

```text
Ran 12 tests
OK
```

覆盖：

- 健康检查。
- 应用鉴权。
- Agent token 鉴权。
- API Key 不明文返回。
- Mock 采样。
- CSV / XLS 导出。
- 150 问题 × 3 模型本地 Mock 负载。
- 失败隔离。
- 失败重跑。
- RQ 入队 Mock。
- 模型默认采样参数。

任务系统检测：

```bash
python3 scripts/check_task_system.py
```

本地默认结果：

```json
{
  "ok": true,
  "task_queue_backend": "inline",
  "database_url_configured": false
}
```

### 真实模型并发抽样

最近真实抽样批次：

```text
batch-73ff043ef5
```

配置：

- 本地已有项目：1 个。
- 本地已有问题：3 个。
- 模型：豆包、通义千问、Kimi、DeepSeek、腾讯混元、文心一言。
- 总任务：3 问题 × 6 模型 = 18。
- `ALLOW_LIVE_MODEL_CALLS=1`
- `SAMPLING_MAX_WORKERS=8`
- `SAMPLING_PROVIDER_MAX_WORKERS=2`

结果：

- 成功：18。
- 失败：0。
- 总耗时：约 68.62 秒。
- 数据库落库：18 条 `model_runs`。
- 批次状态：`completed`。

模型耗时：

| 模型 | 成功/总数 | 平均耗时 | 最大耗时 |
| --- | ---: | ---: | ---: |
| DeepSeek | 3/3 | 12.29s | 14.48s |
| Kimi | 3/3 | 12.89s | 14.18s |
| 通义千问 | 3/3 | 15.11s | 15.87s |
| 文心一言 | 3/3 | 25.43s | 34.47s |
| 豆包 | 3/3 | 26.60s | 33.18s |
| 腾讯混元 | 3/3 | 34.47s | 49.87s |

## 已知边界

- macOS 本地 RQ worker 使用 fork 时曾触发 Objective-C runtime 崩溃；生产 Linux 环境预计不会遇到同类问题。
- 本地 RQ 真实模型测试建议放到 Docker/Linux worker 中执行。
- 当前仍使用 Python 标准库 http server；系统规模小于 3 人时可继续使用，后续如需要更多并发和更完整的 API 框架，可再迁移 FastAPI。
- 真实模型调用成本和速率由模型服务商控制，系统不做外部用户访问限流。

## 运行命令速查

本地：

```bash
python3 app.py
```

应用密码：

```bash
APP_PASSWORD=your-password APP_SESSION_SECRET=your-secret python3 app.py
```

真实模型：

```bash
ALLOW_LIVE_MODEL_CALLS=1 python3 app.py
```

RQ worker：

```bash
python3 worker.py
```

测试：

```bash
python3 -m unittest discover -s tests
python3 scripts/smoke_test.py
python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
python3 scripts/check_task_system.py
```

## 相关文档

- `docs/test-harness.md`
- `docs/auth-gate.md`
- `docs/batch-persistence.md`
- `docs/concurrent-runner.md`
- `docs/retry-export-rerun.md`
- `docs/postgres-rq.md`
- `docs/agent-mcp.md`
- `docs/model-api-capabilities-2026-06-06.md`
- `docs/system-optimization-implementation-plan.md`
