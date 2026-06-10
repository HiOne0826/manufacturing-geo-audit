# 阶段 0 测试 Harness

本阶段只建立可重复验证基线，默认不调用真实模型。

## 真实模型调用开关

`mock` provider 用于本地测试和流程演示，不需要 API Key，也不会发起网络请求。

真实 provider 调用默认关闭。需要人工实测真实模型时，必须显式设置：

```bash
ALLOW_LIVE_MODEL_CALLS=1 python3 app.py
```

未设置时，真实 provider 会返回错误：

```text
真实模型调用默认关闭。需要调用真实模型时请设置 ALLOW_LIVE_MODEL_CALLS=1。
```

## 单元与集成测试

```bash
python3 -m unittest discover -s tests
```

覆盖项：

- `GET /api/health` 正常。
- `/api/models` 不返回明文 `api_key`，也不返回 `api_key` 字段。
- Mock 采样流程可完成。
- CSV / XLS 导出可生成。
- `150 问题 × 3 模型` 本地 Mock 任务可完成。
- `成功数 + 失败数 = 总任务数`。
- 默认不允许真实模型调用。

## 冒烟测试

先启动服务：

```bash
python3 app.py
```

另开终端执行：

```bash
python3 scripts/smoke_test.py
```

如需指定地址：

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8765
```

## 本地负载测试

不需要启动服务，直接使用临时 SQLite 数据库和 Mock adapter：

```bash
python3 scripts/load_test_local.py --questions 150 --models 3
```

保留测试数据库用于排查：

```bash
python3 scripts/load_test_local.py --questions 150 --models 3 --keep-db /tmp/geo-load-test.db
```

## 测试数据库隔离

`app.py` 默认仍使用 `data/geo_audit.db`。测试或临时服务可以通过环境变量覆盖：

```bash
GEO_AUDIT_DB_PATH=/tmp/geo-harness.db GEO_AUDIT_PORT=8876 python3 app.py
```
