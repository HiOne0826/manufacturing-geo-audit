# 阶段 5 PostgreSQL + Redis/RQ 正式任务系统

阶段 5 先补齐正式任务系统的部署骨架，不强行破坏当前 SQLite 可运行路径。

当前默认仍是：

```bash
TASK_QUEUE_BACKEND=inline
```

也就是 Web 进程内启动后台线程，保持阶段 0-4 的本地可运行性。

## 新增配置

```bash
DATABASE_URL=postgresql://geo:password@127.0.0.1:5432/geo_audit
REDIS_URL=redis://127.0.0.1:6379/0
TASK_QUEUE_BACKEND=inline
RQ_QUEUE_NAME=geo-audit
```

后续切换到 RQ 时：

```bash
TASK_QUEUE_BACKEND=rq
```

## PostgreSQL Schema

Schema 文件：

```text
deploy/postgres/schema.sql
```

初始化示例：

```bash
psql "$DATABASE_URL" -f deploy/postgres/schema.sql
```

## Worker 依赖

```bash
python3 -m pip install -r requirements-worker.txt
```

## Worker 启动

```bash
python3 worker.py
```

## 环境检测

```bash
python3 scripts/check_task_system.py
```

如果设置 `TASK_QUEUE_BACKEND=rq`，检测脚本会检查 `rq`、`redis`、`psycopg` 是否可用。

## 当前边界

本阶段完成正式任务系统的部署骨架和 schema 基线。当前 Web 应用仍默认使用 SQLite 和 inline 后台线程，避免在没有 PostgreSQL/Redis 服务的本地环境中破坏已有工作台。

真正把 `/api/runs/start` 切换为入队执行，需要在服务器准备好 PostgreSQL、Redis 和 worker 后再打开 `TASK_QUEUE_BACKEND=rq` 并补生产环境验证。
