# PostgreSQL + Redis/RQ 正式任务系统

当前主线已经支持正式切换：

- 设置 `DATABASE_URL` 后，应用数据库自动使用 PostgreSQL。
- 设置 `TASK_QUEUE_BACKEND=rq` 后，采样任务不再由 Web 进程内线程执行，而是进入 Redis/RQ 队列。
- `worker.py` 负责执行采样、更新 `sampling_batches`、写入 `model_runs` 和 `answer_evaluations`。
- 未设置 `DATABASE_URL` 时仍可回退 SQLite，便于本地快速测试。

## 必要配置

```bash
DATABASE_URL=postgresql://geo:password@127.0.0.1:5432/geo_audit
REDIS_URL=redis://127.0.0.1:6379/0
TASK_QUEUE_BACKEND=rq
RQ_QUEUE_NAME=geo-audit
RQ_JOB_TIMEOUT=3600
```

## 初始化

安装 worker 依赖：

```bash
python3 -m pip install -r requirements-worker.txt
```

应用和 worker 启动时都会调用 `init_db()`。PostgreSQL 模式下会执行：

```text
deploy/postgres/schema.sql
```

也可以手动初始化：

```bash
psql "$DATABASE_URL" -f deploy/postgres/schema.sql
```

## 启动方式

Web：

```bash
python3 app.py
```

Worker：

```bash
python3 worker.py
```

生产环境建议至少部署：

- 1 个 Web 进程。
- 1-3 个 RQ worker。
- PostgreSQL 独立服务。
- Redis 独立服务。

## 检测

```bash
python3 scripts/check_task_system.py
```

检查项：

- RQ / Redis / psycopg 依赖是否存在。
- `DATABASE_URL` 配置后 PostgreSQL 是否能连接。
- `TASK_QUEUE_BACKEND=rq` 时 Redis 是否能 ping 通。

健康接口会返回当前后端：

```json
{
  "ok": true,
  "db": "postgres",
  "task_queue_backend": "rq"
}
```

## 任务流

1. Web 接收 `/api/runs/start` 或 `/api/agent/batches`。
2. Web 创建 `sampling_batches`，状态为 `queued`。
3. Web 将任务入队到 `RQ_QUEUE_NAME`。
4. Worker 执行 `src.tasks.perform_batch`。
5. Worker 更新批次状态和每条运行记录。
6. Web 查询 `/api/runs/progress?batch_id=...` 时从数据库恢复状态。

## 本地测试边界

默认单元测试仍强制 `TASK_QUEUE_BACKEND=inline`，不依赖真实 Redis/PostgreSQL。

切换测试分两层：

- 单元测试 Mock RQ 入队，确认 Web 在 `TASK_QUEUE_BACKEND=rq` 时不启动 inline 线程。
- 集成测试需要真实 PostgreSQL + Redis；可用 Docker 临时启动后运行 smoke/load。
