# 阶段 3 并发采样执行

阶段 3 将采样执行从串行循环改为任务展开后并发执行。

## 执行模型

启动批次时仍调用：

```text
POST /api/runs/start
```

内部流程调整为：

1. 读取项目、问题和模型配置。
2. 展开 `question × model × repeat` 任务。
3. 使用 `ThreadPoolExecutor` 并发执行。
4. 每个任务使用独立 SQLite 连接写入 `model_runs` 和 `answer_evaluations`。
5. 每个任务完成后更新进度和 `sampling_batches`。

## 并发环境变量

```bash
SAMPLING_MAX_WORKERS=8
SAMPLING_PROVIDER_MAX_WORKERS=3
SAMPLING_REQUEST_TIMEOUT=120
SAMPLING_RETRY_COUNT=1
SQLITE_WAL=1
```

当前阶段实际使用：

- `SAMPLING_MAX_WORKERS`：批次总线程数上限。
- `SAMPLING_PROVIDER_MAX_WORKERS`：单 provider 同时执行任务上限。
- `SQLITE_WAL`：默认启用 SQLite WAL。

`SAMPLING_REQUEST_TIMEOUT` 和 `SAMPLING_RETRY_COUNT` 先进入配置基线，真实请求超时和重试会在阶段 4 扩展。

## SQLite 设置

连接数据库时启用：

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA journal_mode = WAL;
```

每个采样任务独立打开连接，不在线程之间共享 SQLite connection。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
```

验收重点：

- 450 个 Mock 任务完成。
- 无 `database is locked`。
- 成功数 + 失败数 = 总任务数。
- 单个模型失败不影响同批次其他任务继续执行。
