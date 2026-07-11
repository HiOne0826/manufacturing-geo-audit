# 制造业 GEO 审计系统当前状态

更新时间：2026-07-11

## 定位

本项目是一套制造业 GEO 效果检测 / 审计系统，用于批量采样多个模型对制造业问题的回答，并保存回答、引用、品牌出现情况、竞品共现、运行参数和评估结果。

它不是内容生产系统。它的核心目标是为客户 GEO 现状报告提供可追溯的数据底稿。

## V2 当前边界

`codex/V2` 已以 `main` 为基线，V2 的目标是把现有轻量工作台升级为“可追溯、可恢复、可交付”的 GEO 审计工作台，而不是重写技术栈。

截至 2026-07-11，V2 P0-P1 已进入合并验收阶段：

- 项目上下文以 URL `project_id` 为优先来源，切换时清理旧项目查询并重挂页面状态；全局任务中心打开批次时同步所属项目。
- 项目支持完整编辑、归档/恢复、影响预览和名称确认后的危险删除；归档项目不能启动批次。
- 普通 API provider 与 DeepSeek Web 都使用 `sampling_tasks`、周期 lease heartbeat、owner fencing 和 append-only `execution_attempts`。
- batch/task/outbox 同事务创建；RQ 按 task 派发，reconciler 恢复过期 lease、未提交 attempt、outbox 和计数漂移。
- provider 健康按 provider/endpoint/model/mode/credential fingerprint/出口区域隔离，支持滑窗指标、主动探针、熔断和原子 half-open。
- P1 已提供 run 质检、报告版本/冻结、批次对比、统一 JSON 导出和操作审计。
- SQLite 保留本地/单机模式；PostgreSQL schema 与增量 migration 已同步，但正式多 worker 上线仍必须执行真实 Redis/PostgreSQL 故障演练。

V2 产品、体验和可靠性契约分别见：

- `docs/v2-product-requirements.md`
- `docs/v2-ux-spec.md`
- `docs/v2-reliability-architecture.md`

## 当前架构

默认本地模式：

```text
Python http.server
  -> 静态前端
  -> 应用全局密码 / HttpOnly Cookie
  -> SQLite WAL
  -> sampling_tasks / execution_attempts / lease
  -> ThreadPoolExecutor
  -> model_runs / answer_evaluations / sampling_batches / report_versions
  -> source_statuses 平台级进度聚合
```

正式任务模式：

```text
Nginx Basic Auth
  -> Python http.server
    -> 应用全局密码
    -> PostgreSQL
    -> Redis + RQ Worker（每个 sampling task 独立 job）
    -> dispatch_outbox + reconciler + worker heartbeat
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
- 正式页面不允许创建 Mock 模型；真实模型测试入口受 `ALLOW_LIVE_MODEL_CALLS` 拦截。

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
- `sampling_tasks`
- `execution_attempts`
- `dispatch_outbox`
- `worker_heartbeats`
- `provider_health` / `provider_health_events`
- `model_runs`
- `answer_evaluations`
- `run_review_events`
- `report_versions`
- `operation_audit_log`

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

1. Web 在同一事务创建 `sampling_batches`、不可变 task snapshot、`sampling_tasks` 和 `dispatch_outbox`。
2. dispatcher 以稳定 task ID 将每个逻辑任务入队。
3. Worker CAS 领取 task、写 execution attempt，并在外部调用期间续租。
4. 提交前验证 lease owner；迟到 worker 只能把 attempt 标为 uncertain，不能覆盖 current。
5. Worker 写入 run/evaluation、完成 task，并从 task 账本聚合 batch。
6. reconciler 修复过期 lease、outbox、uncertain attempt 和计数漂移。

## 并发策略

相关配置：

```bash
SAMPLING_MAX_WORKERS=8
SAMPLING_PROVIDER_MAX_WORKERS=3
SAMPLING_PROVIDER_CONCURRENCY_LIMITS=openrouter:4,deepseek:2,doubao:2,qwen:2,hunyuan:2,gemini:2,openai:2
SAMPLING_REQUEST_TIMEOUT=120
SAMPLING_RETRY_COUNT=1
```

执行策略：

- 每个采样任务独立数据库连接。
- 每个 provider 有独立并发限制。
- 同一项目已有活动批次时返回 `409 ACTIVE_BATCH_EXISTS`，不静默复用。
- `client_request_id` 用于重复提交幂等。
- 单任务失败不会中断整个 batch。
- 失败任务会落库为 `status=failed`。
- 支持按 batch 重跑当前最新仍失败的任务；重跑成功后平台状态按最新等价 run 汇总。

## 客户交付口径

- 抽样页和批次详情页按“测试平台”展示状态，不暴露内部 provider/model_config_id。
- `openai` / `openrouter_gpt` 展示为 ChatGPT。
- `gemini` / `openrouter_gemini` 展示为 Gemini。
- `deepseek` 展示为 DeepSeek。
- `doubao` 展示为豆包。
- `qwen` 展示为千问。
- `hunyuan` 展示为元宝。
- CSV 保留完整机器字段；Excel 面向客户精简为运行 ID、批次 ID、问题、问题类型、联网搜索、生成时间、状态、品牌命中、回答摘要、错误信息、测试平台。
- DeepSeek 联网口径统一通过博查 Web Search API 外部检索增强，结果保留搜索来源 / 引用。

## 运行状态监测

`/api/runs/progress` 返回批次总体进度和 `source_statuses`：

- `test_platform`
- `provider`
- `model`
- `total`
- `completed`
- `success`
- `failed`
- `queued`
- `running`
- `status`
- `avg_latency_ms`
- `last_error`

状态规则：

1. `completed` 表示所有 task 进入终态，不代表全部成功。
2. 采样失败通过 `outcome=partial_failure|failure` 表达。
3. 调度、数据库或 worker 级故障使用 `failed_system`。
4. 运行中暂停先进入 `pause_requested`，停止派发新任务并允许在途任务完成。

inline 模式优先结合内存 job 和数据库结果；RQ 或无内存 job 时，从 `sampling_batches.config_json` 与 `model_runs` 推导平台状态。

## 模型接入

当前模型库支持的主要 provider：

- OpenAI
- OpenRouter-GPT
- OpenRouter-Gemini
- Gemini
- 豆包
- DeepSeek
- 通义千问
- 腾讯混元 / 元宝
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

V2 P0-P1 最近一次完整环境结果：

```text
Ran 130 tests
OK (skipped=2)
```

最终合并态已完成完整 HTTP 与直接调用回归；旧库冲突迁移、task snapshot、启动预检、XLSX 预检、操作审计、过期 lease 重派发和 RQ task failure callback 均已纳入测试。

其中跳过项是需要显式启用的 DeepSeek Web browser contract 与 Redis worker 集成场景，两项均已单独启用并通过。前端 Playwright 已在允许 localhost/Chrome 的环境完成 35/35 验收。

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
- 平台展示名聚合。
- Excel 客户版字段精简。
- `/api/runs/progress` 平台状态聚合。
- RQ 无内存 job 时的平台状态推导。
- task lease heartbeat、owner fencing 和迟到 worker 丢弃。
- outbox/reconciler、重复派发、过期 lease 和 current 唯一性。
- provider 滑窗健康、熔断、half-open 与凭证脱敏。
- 报告冻结后重试不改变旧快照。
- run 质检、批次对比、统一导出和迁移漂移检测。

### 2026-07-11 V2 基线回归

命令：

```bash
python3 -m unittest discover -s tests
cd frontend && npm run build
python3 scripts/load_test_local.py --questions 2 --models 2 --workers 2
```

当前验证结果：

- 完整后端套件：130 tests OK，2 skipped；两个条件跳过项已分别启用真实 Redis/RQ 与 Python Playwright 环境单独通过。
- 后端专项：migration、task snapshot、启动预检、provider health、worker reliability、故障不变量均通过。
- 前端 Vitest + React Testing Library + MSW：12 tests OK。
- 前端构建：入口 94.17 KB gzip，普通业务 chunk 20.93 KB gzip，分析图表独立 chunk 105.26 KB gzip，均低于预算。
- Playwright：35/35 通过，覆盖 8 个关键页面 × 3 视口、6 个关键页面完整 axe 和关键旅程。
- Lighthouse Accessibility：100。
- 问题列表、批次列表，以及批次详情的失败任务、当前结果、Attempt History 均固定每页 10 条，支持具体页码与输入跳转，页码保存在 URL；问题粘贴兼容 `CRLF`、`LF`、`CR`。
- 全局布局已通过两轮截图审查，修复 Grid 等高拉伸造成的异常空白、固定空态高度、设置页孤列和中间宽度适配。
- PostgreSQL 16 + Redis 7 + 两 RQ worker 故障演练通过：kill 持租约 worker 后 6/6 收敛，无 duplicate current 和 expired lease；Redis/DB 短断可检测且恢复后转绿。

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
