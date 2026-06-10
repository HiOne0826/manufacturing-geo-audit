# 阶段 2 批次状态持久化

阶段 2 将采样批次状态从单纯内存状态扩展为 SQLite 持久化状态。

## 新增表

```sql
sampling_batches
```

核心字段：

- `batch_id`：批次唯一 ID。
- `project_id`：所属项目。
- `status`：`queued`、`running`、`completed`、`failed`。
- `total_count`：计划任务数。
- `completed_count`：已完成任务数。
- `success_count`：成功任务数。
- `failed_count`：失败任务数。
- `config_json`：启动批次时的配置快照。
- `error_message`：批次级错误。
- `created_at`、`started_at`、`finished_at`、`updated_at`：批次生命周期时间。

## 接口

- `GET /api/batches?project_id=...`：列出批次。
- `GET /api/batches/{batch_id}`：读取批次详情。
- `GET /api/batches/{batch_id}/runs`：读取该批次下的运行明细。
- `GET /api/runs/progress?batch_id=...`：优先读内存进度；内存不存在时回退到数据库批次状态。

## 兼容性

`POST /api/runs/start` 保持原有请求和响应格式不变，但内部会先创建 `sampling_batches` 记录，再启动后台采样线程。

`model_runs.batch_id` 已经存在，本阶段继续复用它作为批次与运行明细的关联键。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 scripts/load_test_local.py --questions 150 --models 3
```
