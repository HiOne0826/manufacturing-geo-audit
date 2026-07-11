# manufacturing-geo-audit V2 P0–P1 深度验收报告

日期：2026-07-11  
分支：`codex/V2`

## 执行摘要

本轮采用产品、可靠性、UX/QA 三个隔离视角审计。初始结论是“实现较多但证据不足，不能签署”。审计后继续修复了并发可靠性、产品契约和前端用户旅程，并用真实 PostgreSQL 16、Redis 7 和两个 RQ worker 做了恢复演练。

## 已确认结果

- 问题、批次和批次详情表格固定 10 条/页，支持页码与输入跳页，状态写入 URL。
- 粘贴导入兼容 `CRLF`、`LF`、`CR`。
- 项目切换会保护未保存输入并清理旧项目 URL/query 状态。
- 删除项目必须先成功加载影响范围并输入名称。
- 活动批次冲突保留结构化错误并提供进入现有批次入口。
- 启动 API 强制业务名称；支持说明、用途、标签、不可变配置快照与请求指纹幂等。
- batch CAS、attempt sequence、outbox claim/退避、lease fencing 已有数据库约束和测试。
- 杀死持租约 worker 后，reconciler 重新派发并最终 6/6 成功；无重复 current、无过期 lease。
- Redis/DB 中断分别能被检测，恢复后健康检查转绿。
- 分析页包含数据截止、报告版本、配置口径；证据可定位批次具体 run。
- 冻结报告提供客户版快照与内部诊断版导出。
- 前端 unit/build 通过；入口 gzip 94.17 KB，普通业务 chunk 20.93 KB，图表独立 chunk 105.26 KB。

## 当前证据状态

| 验收层 | 状态 | 证据 |
|---|---|---|
| 后端单元与 HTTP 契约测试 | 通过 | 130 tests OK；2 个条件跳过项分别启用后通过；migration v1–v6 |
| PostgreSQL schema/migration | 通过 | v6、无 pending、无 drift |
| PostgreSQL + Redis worker kill/recovery | 通过 | 6/6 success、duplicate current 0、expired lease 0 |
| Redis/DB 短断检测与恢复 | 通过 | 中断时 false，恢复后 true |
| 前端 unit/build | 通过 | 6 files / 12 tests；生产构建通过 |
| 完整 HTTP suite | 通过 | 已纳入 130 项后端套件；重复 retry 原子抢占专项通过 |
| 全页面 Playwright/axe/Lighthouse | 通过 | 35/35；8 页×3视口、6 页 axe、关键旅程；Lighthouse 100 |

## 仍需最终签署的风险

- HTTP 契约与浏览器 E2E 已在允许 localhost/browser 的本机环境实际执行并通过。
- 磁盘阈值、SQLite lock/recovery 与 DB commit 后 RQ ack 前迟到 failure callback 已有确定性测试；真实多进程下的对应压力演练仍可作为部署前加固项。
- DeepSeek Web 真实登录失效、验证码、selector 变化和浏览器崩溃需要有可控测试账号与浏览器环境。

## 结论

可靠性核心与 P0–P1 产品缺口已经实质修复，完整 HTTP、浏览器、可访问性和真实多 worker 故障演练均通过；当前工作树签署为 P0-P1 验收完成。
