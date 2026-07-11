# 制造业 GEO 审计系统

这是一个面向制造业客户的 GEO 效果检测 / 审计系统，用统一问题库批量询问多个模型，记录回答、引用、品牌出现情况、竞品共现和风险评估，为客户 GEO 现状报告提供数据底稿。

本项目不是 GEO 内容生产系统。它负责“采样、审计、归档、导出”，需要和服务器上的 GEOFlow 内容生产系统区分。

## 当前状态

截至 2026-07-05，本项目已经从轻量本地工作台升级为可公网小范围使用的内部系统：

- 使用人数预期不超过 3 人。
- 公网入口建议使用 Nginx Basic Auth。
- 应用层支持全局密码 `APP_PASSWORD`。
- API Key 不在任何接口明文返回。
- 默认测试不调用真实模型，真实调用必须显式设置 `ALLOW_LIVE_MODEL_CALLS=1`。
- 支持批次持久化、并发采样、失败隔离、失败重跑、CSV/XLS 导出。
- 采样页和批次详情页支持按测试平台展示运行监测，用户提交任务后可看到各来源进度。
- 客户版 Excel 导出已精简运行控制列；CSV 保留完整机器字段。
- 平台展示口径统一为 ChatGPT、Gemini、DeepSeek、豆包、千问、元宝等客户可理解名称。
- 支持 SQLite 本地模式，也支持 PostgreSQL + Redis/RQ 正式任务模式。
- 提供内部 Agent API，可被 MCP 或其他 Agent 包装调用。

详细当前态见：[docs/current-system-state.md](docs/current-system-state.md)。

## 核心能力

- 项目管理：创建客户 / 品牌 GEO 审计项目。
- 问题库：生成制造业模板问题、导入 CSV / 表格行。
- 模型库：配置多服务商模型，接口只返回掩码和 `has_key` 状态。
- 抽样任务：按项目、问题、模型批量采样。
- 并发执行：`ThreadPoolExecutor` 支持多模型多问题并发。
- 批次状态：`sampling_batches` 持久化任务状态。
- 平台进度：`/api/runs/progress` 返回 `source_statuses`，聚合每个测试平台的排队、运行、成功、失败和均耗时。
- 结果归档：`model_runs` 保存回答、引用、耗时、状态、错误。
- 规则评估：保存品牌命中、竞品共现、官网引用、风险等级。
- 失败重跑：按 batch 只重跑失败项。
- 导出：CSV / XLS，支持项目级和批次级运行明细。
- Agent API：创建批次、查询状态、获取导出路径、失败重跑。

## 本地运行

默认使用 SQLite 和 inline 后台线程：

```bash
python3 app.py
```

打开：

```text
http://127.0.0.1:8765
```

如果启用应用全局密码：

```bash
APP_PASSWORD=your-password APP_SESSION_SECRET=your-secret python3 app.py
```

## 前端

当前正式前端位于 `frontend/`，使用 Vite + React + TypeScript。

安装依赖：

```bash
cd frontend
npm install
```

开发模式：

```bash
npm run dev
```

构建正式静态文件：

```bash
npm run build
```

`frontend/dist` 存在时，`python3 app.py` 会优先服务 React 构建；旧 `static/` 保留为回退。

## 真实模型调用

默认不允许真实模型调用。需要人工实测时：

```bash
ALLOW_LIVE_MODEL_CALLS=1 python3 app.py
```

API Key 可放入 `.env`。常用环境变量：

```bash
DOUBAO_API_KEY=...
DASHSCOPE_API_KEY=...
QWEN_API_KEY=...
OPENROUTER_API_KEY=...
BOCHA_API_KEY=...
MOONSHOT_API_KEY=...
KIMI_API_KEY=...
DEEPSEEK_API_KEY=...
HUNYUAN_API_KEY=...
TENCENT_API_KEY=...
ERNIE_API_KEY=...
BAIDU_QIANFAN_API_KEY=...
```

不要提交 `.env`。仓库只提供 `.env.example`。

## 正式任务模式

安装 worker 依赖：

```bash
python3 -m pip install -r requirements-worker.txt
```

配置：

```bash
DATABASE_URL=postgresql://geo:password@127.0.0.1:5432/geo_audit
REDIS_URL=redis://127.0.0.1:6379/0
TASK_QUEUE_BACKEND=rq
RQ_QUEUE_NAME=geo-audit
RQ_JOB_TIMEOUT=3600
```

启动 Web：

```bash
python3 app.py
```

启动 worker：

```bash
python3 worker.py
```

详情见：[docs/postgres-rq.md](docs/postgres-rq.md)。

## DeepSeek 官网采样 worker

DeepSeek 官网联网搜索使用独立 Playwright worker，每个问题创建独立网页会话，不与 API provider 混跑。

```bash
python3 -m pip install -r requirements-web-worker.txt
python3 -m playwright install chromium
python3 scripts/deepseek_web_auth.py login
python3 deepseek_web_worker.py
```

详情见：[docs/deepseek-web-worker.md](docs/deepseek-web-worker.md)。

## 测试

单元与集成测试：

```bash
python3 -m unittest discover -s tests
```

本地 smoke：

```bash
python3 app.py
python3 scripts/smoke_test.py
```

本地 Mock 负载：

```bash
python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
```

任务系统检测：

```bash
python3 scripts/check_task_system.py
```

测试说明见：[docs/test-harness.md](docs/test-harness.md)。

## 最近验证

### 客户交付前回归

2026-07-05 完成本轮客户交付前修正后，已执行：

```bash
python3 -m unittest discover -s tests
cd frontend && npm run build
python3 scripts/load_test_local.py --questions 2 --models 2 --workers 2
```

结果：

- 单元 / 集成测试：25 tests OK。
- 前端构建：通过，仍有 Vite chunk size 提示。
- 本地 mock 负载：4/4 success，CSV / XLS 均生成。
- 内置浏览器截图验证：采样页、批次详情页、390px 移动宽度均可用，无页面级横向滚动。

### 真实模型并发抽样

最近真实模型并发抽样使用本地已有 3 个问题和 6 个已有 key 模型：

- 豆包
- 通义千问
- Kimi
- DeepSeek
- 腾讯混元
- 文心一言

批次：`batch-73ff043ef5`

结果：

- 3 问题 × 6 模型 = 18 任务。
- 成功：18。
- 失败：0。
- 总耗时：约 68.62 秒。
- 数据库落库：18 条 `model_runs`。
- 批次状态：`completed`。

## 关键文档

- [docs/current-system-state.md](docs/current-system-state.md)：当前系统状态总览。
- [docs/system-optimization-implementation-plan.md](docs/system-optimization-implementation-plan.md)：阶段化优化方案。
- [docs/test-harness.md](docs/test-harness.md)：测试 harness。
- [docs/auth-gate.md](docs/auth-gate.md)：公网门禁与应用密码。
- [docs/batch-persistence.md](docs/batch-persistence.md)：批次持久化。
- [docs/concurrent-runner.md](docs/concurrent-runner.md)：并发采样。
- [docs/retry-export-rerun.md](docs/retry-export-rerun.md)：重试、失败重跑和导出。
- [docs/postgres-rq.md](docs/postgres-rq.md)：PostgreSQL + Redis/RQ。
- [docs/agent-mcp.md](docs/agent-mcp.md)：Agent API / MCP 映射。
- [docs/deepseek-web-worker.md](docs/deepseek-web-worker.md)：DeepSeek 官网独立会话采样 worker。
- [mcp/README.md](mcp/README.md)：MCP wrapper 使用说明。
- [docs/model-api-capabilities-2026-06-06.md](docs/model-api-capabilities-2026-06-06.md)：模型 API 能力与参数依据。

## 部署备注

服务器部署目录建议保持独立：

```text
/opt/manufacturing-geo-audit
```

不要和 `/opt/geoflow` 混用。

已提供：

- `deploy/server/manufacturing-geo-audit.service`
- `deploy/server/manufacturing-geo-audit-worker.service`
- `deploy/server/manufacturing-geo-audit-deepseek-web-worker.service`
- `deploy/server/manufacturing-geo-audit.conf`
- `deploy/postgres/schema.sql`
