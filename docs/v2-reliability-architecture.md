# V2 可靠性架构：持久任务、可恢复执行与信息源隔离

状态：P0-P1 已实现，PostgreSQL + Redis 双 worker 故障演练已签署  
更新日期：2026-07-11

## 1. 当前事实与改造边界

当前系统是 Python 标准库 HTTP 服务、SQLite/PostgreSQL、Redis/RQ 可选队列和 React 前端。普通 API provider 与 DeepSeek Web 均使用持久化 `sampling_tasks`、execution attempt、lease heartbeat 和 owner fencing；RQ 以 task 为派发单位。过期 lease 与 task job failure 会生成幂等 task outbox 并重新派发。`model_runs.is_current` 已有 partial unique index，重复 current 由数据库约束拦截。

SQLite 保留本地/单机支持，正式多 worker 以 PostgreSQL 为目标。数据库由 `src/migrations.py` 管理版本与 checksum；当前 migration v1-v6 覆盖可信交付、provider 健康、不可变 task snapshot、旧库多活动批次的无损确定性收敛，以及 attempt/outbox 并发约束。

## 2. 四层职责与不变量

```text
sampling_batch  业务批次、配置快照、总体状态
  └─ sampling_task  一次应完成的逻辑采样，跨重试稳定
       └─ execution_attempt  一次真实领取和外部调用，append-only
            └─ model_run  可分析的标准化结果；current 是投影
```

- batch：回答“为什么、为哪个项目、用什么配置执行”。配置创建后不可变，业务元数据可修改。
- task：由项目、问题、模型配置、模式、参数指纹、repeat、generation 组成逻辑键。
- attempt：记录实际 provider/model、配置指纹、开始/结束、lease、错误分类、usage、cost、延迟和结果指针，不覆盖旧 attempt。
- run：保存回答、引用和标准化评估输入；同一逻辑键/generation 只能有一个 current。

不变量：

1. 一个 task 同一时刻最多一个未过期 lease。
2. 一个逻辑键/generation 最多一个 current run。
3. batch、tasks 和 dispatch outbox 在同一数据库事务创建。
4. 暂停后不派发新 task，已领取 task 可完成并提交。
5. provider/mode 故障不能改变其他 source 的状态。
6. fallback 必须生成新的 source/generation，保留原始失败事实。

## 3. 数据模型

### 现有表增量

- `projects`：`archived_at`。
- `sampling_batches`：`batch_name`、`description`、`purpose`、`tags_json`、`config_snapshot_json`、`generation`、`lock_version`、`client_request_id`、`archived_at`；`outcome` 由状态和计数实时计算。
- `sampling_tasks`：全部 provider 共用，任务键唯一；`task_snapshot_json` 保存创建时的问题、模型和执行参数，凭证只在执行时从当前 credential 配置解析。
- `model_runs`：保留 current/history 投影；task/attempt 关联由 `execution_attempts.run_id` 反向追溯。SQLite/PostgreSQL 都使用 partial unique index约束同一逻辑结果只有一个 current。

### 新表

- `execution_attempts`：append-only 调用账本，状态为 `claimed/running/succeeded/failed/abandoned`。
- `dispatch_outbox`：`pending/processing/delivered`，保存 task/job 引用、尝试次数、claim token/lease 和下次派发时间；失败按指数退避回到 pending。
- `provider_health`：静态/主动/被动健康、熔断状态、窗口指标；只保存 credential fingerprint 的不可逆短摘要。
- `worker_heartbeats`：worker、队列、能力、最后心跳和当前任务数。
- `report_versions`：状态、summary 快照、run ID 集合、配置口径和冻结时间。
- `operation_audit_log`：P1 操作审计，不保存密钥或原始回答。

所有 schema 变更通过版本化 migration 执行；不得依赖 `CREATE TABLE IF NOT EXISTS` 悄悄完成不可逆变更。先加 nullable/default 字段并回填，再加约束。

## 4. 状态机与结果语义

批次状态：

```text
queued → running → completed
   │        │  └→ pause_requested → paused → queued/running
   │        ├→ failed_system
   └────────┴→ cancelled
```

`completed` 只表示所有 task 已进入终态，不代表全部采样成功。结果另用：

- `pending`：仍有 queued/running。
- `clean`：全部 task 成功。
- `partial_failure`：至少一个成功且至少一个失败/blocked。
- `failure`：所有应执行 task 均失败/blocked，或 batch 为 `failed_system`。

状态迁移使用 `lock_version` CAS：`UPDATE ... WHERE id=? AND lock_version=?`。更新失败者重新读取，不覆盖其他 worker 的新状态。

task 状态：`queued/running/success/failed/blocked/cancelled`。可恢复错误回到 `queued` 并设置 `next_attempt_at`；不可恢复错误进入 `blocked`。attempt 永不回退或复用。

## 5. 派发、领取与恢复

### 创建与派发

1. command service 校验项目、活动批次和 `client_request_id`。
2. 同一事务创建 batch、展开 task、写 `dispatch_outbox`。
3. dispatcher 轮询 outbox 并以稳定 job ID 入 RQ；成功后标记 dispatched。
4. RQ 重复入队是允许的，worker 必须靠 task lease 和状态 CAS 去重。

### 领取与提交

- worker 仅领取 `queued AND next_attempt_at<=now` 或 lease 已过期的 running task。
- claim 原子更新 lease owner/expiry、attempt 和 task 状态；lease 时长大于请求 timeout，并定期 heartbeat。
- 外部响应先标准化，再在事务中写 attempt/run/evaluation、切换 current、完成 task 和聚合 batch 计数。
- DB commit 成功但 RQ ack 失败时，重复 job 读取 task 终态后直接返回，不重复调用 provider。

### Reconciler

定期且可手动运行，负责：

- queued task 无 pending/dispatched outbox：补 outbox。
- running task lease 过期：将 attempt 标为 abandoned，按策略重新排队或 blocked。
- batch 聚合计数与 task 账本不一致：以 task 账本重算并记录审计事件。
- 活动 batch 已无非终态 task：收敛为 completed 与正确 outcome。
- 重复 current：保留最新有效 run，其余降为历史；数据库约束上线后此项应为零。

`scripts/audit_consistency.py` 是只读前置工具，只报告僵尸批次、计数漂移、重复 current 和过期 lease；它不修复、不连接 Redis，也不打印回答、配置或凭证。

## 6. 信息源健康与熔断

健康键至少包含 `provider + endpoint + model + mode + credential_fingerprint + egress_region`。pure 与 search 分开，搜索依赖故障不能熔断 pure。

- 静态探针：配置、凭证存在性、模型能力和本地依赖，不产生付费调用。
- 主动探针：pure、search、citation extraction 分别验证；默认启动前按需执行，定时付费探针可选每 6 小时。
- 被动指标：真实 task 滑窗的成功率、429、p95、连续失败和最后成功时间。

错误分类：

| 类别 | 默认策略 |
| --- | --- |
| auth、region、model_not_found | 立即 open，task blocked，等待配置变化或人工探针 |
| rate_limit | 尊重 Retry-After，指数退避加抖动，限次重试 |
| timeout、5xx、DNS、TLS | 限次退避；超过阈值 open |
| parse | 保存脱敏诊断摘要，少量重试后 blocked |
| search dependency | 只隔离对应 search mode |

熔断器为 `closed/open/half_open`。open 到期后只允许少量探针/真实 task 进入 half-open；成功阈值达到后 closed，失败则重新 open。前端只展示安全摘要和修复入口。

并发与速率限制必须使用 Redis 的跨 batch、跨 worker 计数/令牌桶；进程内 semaphore 只可作为本地二级保护。

## 7. 健康接口与观测

- `/api/health/live`：进程 event loop/handler 可响应即 200，不访问外部依赖。
- `/api/health/ready`：数据库连接与 migration、Redis、目标队列 worker heartbeat、outbox 积压和磁盘剩余空间；关键依赖失败返回 503。
- `/api/sources/health`：按 source/mode 返回 `healthy/degraded/open/unknown`、最后成功/探针时间、安全错误码和窗口指标。

结构化日志必须包含 request ID、batch/task/attempt、provider/model/mode、状态迁移、延迟和错误分类；禁止记录 API Key、cookie、Authorization、完整 prompt、回答和 raw response。指标至少覆盖队列延迟、执行延迟、成功率、错误分类、lease 过期、outbox 积压、reconciler 修复数和 worker 心跳。

## 8. 迁移与回滚顺序

1. 引入 migration runner、增量表/字段和只读一致性审计；备份并验证恢复。
2. 双写 execution attempt/outbox，但旧 batch runner 仍是主路径；比对计数。
3. DeepSeek Web 先切统一协议，再逐个 API provider 切 task runner；每次只切一个 provider。
4. 启用 dispatcher/reconciler 与 current 唯一约束；关闭旧整批派发。
5. PostgreSQL 多 worker 故障演练通过后作为正式环境默认；SQLite 保留单 worker。

任一阶段回滚只关闭新写路径，不删除新增表或 attempt；旧 API 兼容别名至少保留一个 V2 发布周期。

## 9. 验证矩阵

- 重复 start/pause/resume/retry 请求与两个 worker 同时 claim。
- worker 在 claim、外部响应、DB commit、RQ ack 前后退出。
- Redis 中断、DB 短暂不可用、SQLite 锁、磁盘不足和 outbox 积压。
- 429/503/DNS/TLS/timeout/错误 JSON/auth/region/model_not_found。
- DeepSeek Web 登录失效、验证码、selector 变化和浏览器崩溃。
- 冻结报告后重试，旧 summary 与 run 集合不改变。
- 每轮故障演练后运行一致性审计，四类 issue 均应为零。
