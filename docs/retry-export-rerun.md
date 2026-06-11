# 阶段 4 失败重试、失败重跑、批次导出

阶段 4 增加失败恢复能力，避免真实模型调用中的超时、限流或服务商错误导致整批不可复盘。

## 自动重试

采样任务支持重试：

```bash
SAMPLING_RETRY_COUNT=1
```

也可以在批次配置中传入：

```json
{
  "retry_count": 1
}
```

重试仍然只针对单个任务；任务最终失败会写入 `model_runs.status = failed` 和 `error_message`。

## 失败重跑

新增接口：

```text
POST /api/batches/{batch_id}/rerun_failed
```

该接口只读取当前批次中 `status = failed` 的 run，并为这些失败项创建新的 run。成功项不会重复执行。

限制：

- 只有阶段 4 之后新生成的 run 才稳定包含 `model_config_id`，可以完整重跑。
- 历史 failed run 如果 `model_config_id = 0`，会被跳过。

## 批次导出

新增接口：

```text
GET /api/export/batches/{batch_id}/runs.xls
GET /api/export/batches/{batch_id}/summary.xls
```

`runs.xls` 按批次导出明细；`summary.xls` 当前复用项目级摘要，后续可在阶段 5 或报表阶段细化为严格批次级摘要。

## Mock 失败率

本地可通过环境变量构造稳定的 Mock 失败：

```bash
MOCK_FAILURE_RATE=0.1 python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
```

也可以将 Mock 模型 ID 设置为 `mock-fail`，用于测试所有任务失败和重跑。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 scripts/load_test_local.py --questions 150 --models 3 --workers 8
```
