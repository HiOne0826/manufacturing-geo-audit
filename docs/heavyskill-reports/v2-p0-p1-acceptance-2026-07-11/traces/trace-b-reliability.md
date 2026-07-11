# Trace B：可靠性与架构审计

## 初始结论

task lease/fencing、current partial unique、provider scope/circuit、migration checksum 与 health API 已有实现；40 项相关单元测试通过。但不能据此签署正式多 worker 可靠性。

## 发现的关键问题

- batch CAS 没有比较 `lock_version`。
- `attempt_no` 使用 `MAX+1` 且无唯一约束。
- outbox 没有独占 claim、失败退避和 claimant fencing。
- lease 回收与未完成 attempt 没有精确关联。
- 缺少真实 PostgreSQL + Redis 两 worker 故障演练。

## 修复与实测

- migration v6 增加 attempt sequence 唯一约束及 outbox claim 字段。
- CAS 更新加入 `lock_version` 条件。
- outbox 增加原子 claim、claim lease、claimant fencing 和指数退避。
- lease 回收会立即把对应未提交 attempt 标为 `uncertain/lease_expired`。
- PostgreSQL 16 + Redis 7 + 两个 RQ worker 实测：杀死持租约 worker 后 reconciler 重新派发，6/6 收敛，duplicate current=0，expired lease=0。
- Redis 与 PostgreSQL 分别暂停时健康检查变红，恢复后重新变绿。
