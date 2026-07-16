# 生产 PostgreSQL + 10 RQ worker 验收

时间：2026-07-13（Asia/Shanghai）

## 迁移

- 生产数据库已由 SQLite 切换为 PostgreSQL，公网与本机 health 均返回 `db: postgres`。
- SQLite 源迁移前后 SHA-256 一致；原文件未修改。
- 17 张表逐表行数一致。
- 仅 PostgreSQL 副本清除了 8 条 `model_runs.raw_response_json` 中的 NUL；受影响 `run_id` 见迁移审计 JSON。
- 原三份备份保留，并新增正式切换快照 `postgres-cutover-20260713-222727`。

## Worker

- 主 RQ worker：10 个 systemd 模板实例，全部 `active`。
- 旧单例 worker：`disabled` / `inactive`。
- 独立 DeepSeek Web worker 保留。
- 已显式配置 `REDIS_URL=redis://127.0.0.1:6379/0`，跨进程 provider semaphore 生效。

## 压测结果

| 场景 | 任务 | 结果 | 墙钟耗时 | 有效并行度 | provider 槽峰值 | PG 连接峰值 | worker RSS 峰值 |
|---|---:|---:|---:|---:|---|---:|---:|
| 单数据源：混元 SearchPro | 10 | 10 成功 | 22.947s | 3.07 | hunyuan=4 | 6 | 861.1 MiB |
| 多数据源：无重试 | 10 | 8 成功 / 2 失败 | 32.458s | 3.39 | 5 组各 2，共 10 | 4 | 873.1 MiB |
| 多数据源：重试 1 次 | 10 | 10 成功 | 59.057s | 2.83 | 5 组各 2，共 10 | 7 | 879.1 MiB |

首轮多数据源的两条失败分别来自 OpenRouter-GPT 与 OpenRouter-Gemini，错误均为瞬时 `SSL: UNEXPECTED_EOF_WHILE_READING`。开启线上正常的一次重试后 10/10 成功。

## 结论与限制

- 10 个 worker 可以正常同时领取并执行 10 条任务；PostgreSQL、RQ、Redis semaphore 和 systemd 均稳定。
- 单数据源不会自动获得 10 路上游并发；混元受 `hunyuan=4` 限制，实测峰值为 4。这是供应商保护策略，不是 worker 不生效。
- 多数据源能更充分利用 10 个 worker；本次 5 个 provider group 各 2 条，同时达到 10 条 running。
- 实际总耗时仍由最慢供应商决定。本轮重试场景中豆包平均 46.008s，是主要尾延迟来源。
- OpenRouter 经代理偶发 TLS EOF，必须保留至少一次重试；否则首轮实测成功率为 80%。
- 当前主机约 3.5 GiB 内存，压测时 worker 总 RSS 峰值约 879 MiB；现有 10 worker 可用，但不建议在未扩容内存前继续显著增加 worker 数。

## 收尾状态

- 公网 health：正常。
- 主队列 / Web 队列：0 / 0。
- provider semaphore：全部释放。
- PostgreSQL 当前连接：1。
- PostgreSQL 数据库大小：87 MB。
- 切换后 SQLite SHA-256 仍与迁移审计一致。
- 切换后 app / worker 日志无 error 级别记录。
