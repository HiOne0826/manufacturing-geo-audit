# 制造业 GEO 审计系统优化实施方案

## 1. 背景与目标

本项目当前是一套轻量工作台：`Python 标准库 + SQLite + 静态前端`，已经具备项目管理、问题库、模型配置、批量采样、结果归档、分析和导出能力。

下一阶段目标不是做多人 SaaS，而是把它升级成少数可信成员使用的正式内部系统：

- 使用人数预计不超过 3 人。
- 部署到公网，但只允许少数成员访问。
- 典型高负载场景：单次单模型约 150 个问题，并且可能同时启动多个模型采样。
- 不需要面向外部用户做访问限流。
- 需要保护 API Key、控制误操作、支持后台任务、并发采样、持久化结果、Agent/MCP 调用和可测试闭环。

核心判断：

- 当前最大瓶颈不是页面，而是采样任务调度、并发执行、数据库写入和失败恢复。
- 当前 API Key 列表接口已做掩码处理，但未授权用户仍可能调用采样接口消耗模型额度。
- 需要先补公网门禁，再补任务系统，最后再考虑框架迁移。

## 2. Git 分支策略

“用 git 分支逐步记录修改，最终确认后再合并到主代码”这个说法是正确的。

建议主分支保持可运行，所有优化都走功能分支：

```bash
git checkout main
git pull
git checkout -b feature/auth-gate
```

每个阶段一个分支，完成后本地验证，通过后再合并：

```bash
git checkout main
git merge --no-ff feature/auth-gate
```

推荐分支：

| 阶段 | 分支名 | 目标 |
| --- | --- | --- |
| 0 | `feature/test-harness` | 建立测试与基线验证 |
| 1 | `feature/auth-gate` | Nginx Basic Auth + 应用全局密码 |
| 2 | `feature/batch-persistence` | 批次状态落库 |
| 3 | `feature/concurrent-runner` | 并发采样执行 |
| 4 | `feature/retry-export-rerun` | 重试、失败重跑、批次导出 |
| 5 | `feature/postgres-rq` | PostgreSQL + Redis/RQ |
| 6 | `feature/mcp-server` | Agent/MCP 接入 |

如果某个阶段较大，可以再拆子分支，例如：

- `feature/concurrent-runner-core`
- `feature/concurrent-runner-ui`
- `feature/concurrent-runner-tests`

## 3. 总体目标架构

短期架构：

```text
Nginx Basic Auth
  -> Python http.server 应用
    -> 应用全局密码 / Cookie
    -> SQLite WAL
    -> ThreadPoolExecutor
    -> model_runs / answer_evaluations / sampling_batches
```

正式架构：

```text
Nginx Basic Auth
  -> FastAPI 或现有 Python API
    -> PostgreSQL
    -> Redis + RQ Worker
    -> Model Adapters
    -> Exporter
    -> MCP Server
```

不建议一开始重写成 FastAPI。先在当前代码内补齐安全、批次持久化、并发执行和测试闭环，等采样流程稳定后再迁移框架。

## 4. 阶段 0：测试与 Harness 基线

### 4.1 目标

先建立可重复验证的测试闭环，避免后续改并发、鉴权、数据库时破坏已有功能。

### 4.2 实施内容

新增：

- `tests/`：基础测试目录。
- `tests/fixtures/`：测试 CSV、mock 模型响应。
- `scripts/smoke_test.py`：本地冒烟测试。
- `scripts/load_test_local.py`：本地并发模拟，不调用真实模型。
- `docs/test-harness.md`：测试说明。

建议覆盖：

- 健康检查：`GET /api/health`
- 项目创建 / 列表
- 问题导入
- 模型配置读取时不返回明文 `api_key`
- Mock 采样批次可完成
- 导出 CSV / XLS 可生成
- 并发任务下数据库不报错

### 4.3 Harness 管控项

必须纳入 harness 的内容：

| 类别 | 管控项 | 目的 |
| --- | --- | --- |
| 安全 | API 响应不得包含明文 Key | 防止前端泄密 |
| 安全 | 未登录访问 `/api/*` 返回 401 | 防止公网误用 |
| 成本 | 真实模型调用默认关闭 | 防止测试误烧 token |
| 成本 | 测试使用 Mock Adapter | 保证可重复 |
| 并发 | 150 问题 × 3 模型模拟 | 验证调度稳定性 |
| 数据 | 每条 run 必须有 batch_id | 保证可追踪 |
| 数据 | 失败任务必须落库 | 保证可复盘 |
| 导出 | 导出行数等于成功/失败总任务数 | 防止漏数据 |
| 回归 | 修改后跑 smoke test | 防止基础功能破坏 |

### 4.4 验收标准

```bash
python3 app.py
python3 scripts/smoke_test.py
python3 scripts/load_test_local.py --questions 150 --models 3
```

验收结果：

- 健康检查正常。
- 不需要真实 API Key 也能跑完 Mock 流程。
- API 响应无明文 Key。
- 150 × 3 模拟任务可完成。
- 失败、成功、导出数量一致。

## 5. 阶段 1：公网门禁

### 5.1 目标

少数成员访问，不做复杂用户体系。

安全层级：

1. Nginx Basic Auth：挡住公网入口。
2. 应用全局密码：进入页面后再校验一次。
3. API 层统一校验：保护所有 `/api/*`。

### 5.2 实施内容

新增环境变量：

```bash
APP_PASSWORD=...
APP_SESSION_SECRET=...
AUTH_COOKIE_NAME=geo_audit_session
```

新增接口：

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/status`

修改：

- 除 `/api/health` 和 `/api/auth/login` 外，所有 `/api/*` 必须认证。
- 静态页面未登录时显示密码输入界面。
- 登录成功后写入 HttpOnly Cookie。
- Cookie 使用 HMAC 签名，避免明文密码存客户端。

Nginx 侧：

```nginx
auth_basic "GEO Audit";
auth_basic_user_file /etc/nginx/.geo_audit_htpasswd;
```

### 5.3 验收标准

- 未输入 Basic Auth 时，公网入口无法访问。
- 未登录应用密码时，访问 `/api/projects` 返回 401。
- 登录后可正常使用页面和接口。
- `/api/models` 不返回明文 API Key。
- `/api/runs/start` 未登录无法调用。

## 6. 阶段 2：批次状态持久化

### 6.1 目标

当前批次状态主要存在内存里，进程重启会丢。需要把 batch 状态落库。

### 6.2 新增表

```sql
CREATE TABLE IF NOT EXISTS sampling_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    total_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    config_json TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
```

可选新增：

```sql
CREATE TABLE IF NOT EXISTS sampling_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    model_config_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    repeat_index INTEGER DEFAULT 1,
    status TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    run_id TEXT DEFAULT '',
    latency_ms INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
```

### 6.3 接口调整

新增：

- `GET /api/batches?project_id=...`
- `GET /api/batches/{batch_id}`
- `GET /api/batches/{batch_id}/runs`

保留：

- `POST /api/runs/start`

但内部改为：

1. 创建 `sampling_batches`。
2. 展开任务。
3. 后台执行。
4. 更新批次状态。

### 6.4 验收标准

- 页面刷新后仍能看到历史批次。
- 应用重启后仍能查询已完成批次。
- 每条 `model_runs.batch_id` 都能对应到 `sampling_batches.batch_id`。
- 批次统计和 runs 实际数量一致。

## 7. 阶段 3：并发采样执行

### 7.1 目标

支持“单模型 150 问题”和“多个模型同时采样”。

当前串行结构：

```text
for question
  for model
    for repeat
      call_model()
```

目标结构：

```text
create tasks
ThreadPoolExecutor
  -> run one question/model/repeat task
  -> save one run
  -> update progress
```

### 7.2 并发配置

新增环境变量：

```bash
SAMPLING_MAX_WORKERS=8
SAMPLING_PROVIDER_MAX_WORKERS=3
SAMPLING_REQUEST_TIMEOUT=120
SAMPLING_RETRY_COUNT=1
SQLITE_WAL=1
```

说明：

- 这里不是用户访问限流。
- 这是系统稳定性保护，避免一次误操作打爆本机线程、数据库或模型服务商。

### 7.3 SQLite 短期优化

启用：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

每个任务独立数据库连接，不在线程间共享同一个 SQLite connection。

### 7.4 验收标准

```bash
python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
```

验收结果：

- 450 个模拟任务可完成。
- 无 `database is locked`。
- 成功数 + 失败数 = 总任务数。
- 进度接口能持续更新。
- 单个任务失败不影响整个批次继续执行。

## 8. 阶段 4：失败重试、失败重跑、批次导出

### 8.1 目标

真实模型调用会出现超时、429、服务商错误。正式系统必须可恢复。

### 8.2 实施内容

新增能力：

- 单任务失败自动重试 `SAMPLING_RETRY_COUNT` 次。
- 失败原因落库。
- 批次完成后可筛选失败任务。
- 支持只重跑失败任务。
- 支持按 batch 导出。

新增接口：

- `POST /api/batches/{batch_id}/rerun_failed`
- `GET /api/export/batches/{batch_id}/runs.xls`
- `GET /api/export/batches/{batch_id}/summary.xls`

### 8.3 验收标准

- 构造 10% mock 失败率，批次仍可完成。
- 失败任务有错误信息。
- 点击重跑失败后，只创建失败任务的新 run。
- 批次导出行数正确。

## 9. 阶段 5：PostgreSQL + Redis/RQ 正式任务系统

### 9.1 触发条件

满足任一条件就应进入本阶段：

- SQLite 并发写入开始不稳定。
- 需要部署多个 worker。
- 单批次任务超过 1000。
- 需要任务暂停、恢复、重试队列、worker 监控。

### 9.2 技术选型

推荐：

- PostgreSQL：正式业务数据。
- Redis：任务队列。
- RQ：后台任务 worker。

暂不推荐先上 Celery，除非后续任务编排明显复杂化。

### 9.3 迁移内容

- 抽象数据库连接层。
- 增加 PostgreSQL 初始化脚本。
- 迁移 SQLite 数据。
- Web 进程只负责提交任务和查状态。
- RQ worker 负责执行采样。

### 9.4 验收标准

- Web 进程重启不影响 worker 继续执行。
- Worker 重启后失败任务可重试。
- 批次状态以数据库为准。
- Redis 队列为空时，所有任务均有最终状态。

## 10. 阶段 6：Agent / MCP 接入

### 10.1 目标

让 Agent 可以提供问题表格，调用本系统采样，并返回结果表格。

### 10.2 MCP 工具设计

建议工具：

```text
create_geo_audit_batch(project_id, csv_text, model_ids, options)
get_geo_audit_batch_status(batch_id)
export_geo_audit_batch(batch_id, format)
rerun_failed_geo_audit_tasks(batch_id)
```

### 10.3 设计原则

- MCP 不直接调用模型服务商。
- MCP 不接触 API Key。
- MCP 只调用本系统后端。
- MCP 使用内部 token 鉴权。
- 所有结果仍落入同一数据库。

### 10.4 验收标准

- Agent 能提交 CSV。
- Agent 能拿到 batch_id。
- Agent 能轮询状态。
- Agent 能在完成后获取导出文件。
- Agent 触发的任务和前端触发的任务在数据库中结构一致。

## 11. 值得补充的 Harness 管控

除基础测试外，建议重点补以下管控。

### 11.1 成本管控 Harness

目标不是限制正常使用，而是防止误操作。

建议记录：

- 每个 batch 预计任务数。
- 每个 batch 实际 token usage。
- 每个 provider 估算成本。
- 每次运行的模型、搜索模式、思考模式。

可选保护：

- 提交前展示预计任务数。
- 超过阈值时要求二次确认。
- 不阻止高并发，但防止误点。

### 11.2 真实 API 调用开关

测试默认禁止真实 API。

环境变量：

```bash
ALLOW_LIVE_MODEL_CALLS=0
```

只有手动设置为 `1` 时，测试脚本才允许调用真实模型。

### 11.3 响应结构回归

不同 provider 返回结构经常变化。需要保留 raw JSON 样本，并测试解析器：

- Hunyuan citation parser
- Qwen citation parser
- Doubao citation parser
- Kimi search parser
- Gemini grounding parser

每次改 adapter，都要跑 citation parser 测试。

### 11.4 数据一致性检查

新增脚本：

```bash
python3 scripts/check_data_integrity.py
```

检查：

- batch 统计是否等于 runs 聚合。
- failed run 是否有 error_message。
- success run 是否有 response_text。
- citations_json 是否为合法 JSON。
- raw_response_json 是否为合法 JSON。

### 11.5 部署前检查

新增脚本：

```bash
python3 scripts/predeploy_check.py
```

检查：

- `.env` 必要变量存在。
- `APP_PASSWORD` 已设置。
- API Key 不为空时不打印明文。
- 数据库可连接。
- `/api/health` 正常。
- Nginx 配置存在 Basic Auth。

## 12. 推荐实施顺序

最务实的执行顺序：

1. `feature/test-harness`
   - 先做 smoke test、mock load test、API Key 泄露检查。
2. `feature/auth-gate`
   - 加 Nginx Basic Auth 文档和应用全局密码。
3. `feature/batch-persistence`
   - 新增 `sampling_batches`，让任务状态可恢复。
4. `feature/concurrent-runner`
   - 拆任务、加线程池、SQLite WAL。
5. `feature/retry-export-rerun`
   - 失败重试、失败重跑、按批次导出。
6. `feature/postgres-rq`
   - 当 SQLite/线程池达到边界后再上。
7. `feature/mcp-server`
   - 最后暴露给 Agent。

## 13. 每个分支的完成定义

每个分支合并前必须满足：

- 有对应文档更新。
- 有最小测试或 smoke test。
- 本地能启动。
- `/api/health` 正常。
- 不泄露 API Key。
- 不覆盖用户已有未提交改动。
- `git diff` 只包含本分支相关修改。

合并前建议命令：

```bash
git status --short
python3 app.py
python3 scripts/smoke_test.py
git diff --stat
```

## 14. 最终建议

这个项目下一步不要先做大重构。应先把“少数人公网安全使用 + 大批量并发采样 + 结果可恢复 + 测试闭环”补齐。

最小正式版定义：

- 公网入口有 Basic Auth。
- 应用 API 有全局密码。
- 批次状态落库。
- 支持 150 × 多模型并发采样。
- 支持失败重试和失败重跑。
- 支持按批次导出。
- 有 smoke test 和 load test。
- API Key 不在任何接口明文返回。

达到这个标准后，再考虑 PostgreSQL/RQ 和 MCP。这样风险最低，也最符合当前 3 人以内的真实使用场景。
