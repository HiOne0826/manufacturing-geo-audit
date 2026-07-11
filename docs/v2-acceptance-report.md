# V2 P0-P1 验收记录

更新时间：2026-07-11  
分支：`codex/V2`  
状态：P0-P1 核心实现、完整自动化套件与真实多 worker 演练均已通过

## 已实现范围

### P0 核心旅程

- 项目上下文：URL 与持久状态同步；深链接恢复；任务中心打开批次时同步项目；切换项目清理旧缓存并重挂页面状态。
- 项目生命周期：完整编辑、归档/恢复、影响预览、名称确认后的硬删除。
- 项目准备度：档案、问题库、信息源、批次和分析数据统一展示下一步。
- 问题导入：粘贴、CSV、TSV、XLSX；XLSX 先执行只读服务端预检，确认后才写入；返回合法、重复、空内容和逐行原因。
- 信息源就绪度：启动前检查项目、问题、模型能力、凭证、search 依赖、熔断和系统 readiness。
- 批次：业务名称、说明、用途、标签、问题集/模型矩阵配置快照、活动批次冲突和 client request 幂等。
- 生命周期：pause requested 语义、继续补缺、分类重试、attempt history、全局任务中心。
- 结果语义：`completed` 与 `outcome` 分离，系统故障使用 `failed_system`。
- 异步状态：加载、错误、空数据、后台过期提示分别处理。

### P0 可靠性

- 所有 provider 共用持久化 task 账本；RQ 以 task 为 job。
- task snapshot 固化创建时问题、模型和执行参数，凭证不入快照并允许执行时轮换。
- lease heartbeat、owner fencing 和迟到 worker 丢弃。
- execution attempt append-only；DeepSeek Web 与 API provider 采用同一追溯语义。
- batch/task/outbox 同事务创建；reconciler 修复 outbox、过期 lease、uncertain attempt 和计数漂移。
- worker 与 reconciler 持久化 heartbeat；readiness 检查 DB、写入、schema、磁盘、Redis、队列、worker、outbox 和 lease。
- provider 健康按 provider、endpoint、model、mode、credential fingerprint、出口区域隔离。
- pure/search/citation 主动探针、被动滑窗、熔断和原子 half-open。
- Redis 全局 provider 并发槽使用 Lua 原子领取和续租。
- 跨 provider fallback 被拒绝；必须新建独立 source/generation。

### P1 可信交付

- Run 质检：有效、排除、需复核，追加式历史。
- 报告版本：draft、reviewed、frozen、archived。
- 冻结报告保存 summary、run ID 和配置快照，后续重试不改变旧报告。
- 批次对比检查问题集、模型矩阵、search、思考参数和重复次数，并返回 delta。
- 客户汇总、current、完整 attempt history 和冻结报告导出。
- 项目归档、批次创建/元数据/暂停/继续/重试/归档、run 质检和报告操作写入审计日志。

## 自动化证据

### 后端

- 完整后端套件：130 tests OK，2 skipped；两个条件跳过项已分别在真实 Redis/RQ 与 Python Playwright 环境中单独启用并通过。
- 重复重试使用数据库状态与 `lock_version` 原子抢占；并发或连续点击只会派发一次，后续请求返回 `409 RETRY_IN_PROGRESS`。
- 覆盖 task snapshot、启动预检、XLSX 预检、操作审计、旧库活动批次冲突迁移、过期 lease 重派发和 per-task RQ failure callback。
- DeepSeek Web 创建时问题/品牌/模型快照在配置变更后仍保持原执行语义。

### 前端

- Vitest + React Testing Library + MSW：12 tests OK。
- 构建：入口 94.17 KB gzip；普通业务 chunk 20.93 KB gzip；分析图表独立 chunk 105.26 KB gzip；CSS 7.01 KB gzip。
- Playwright：35/35 通过，覆盖 8 个关键页面 × 3 视口、6 个关键页面完整 axe、项目切换保护、分页 URL、批次详情与移动导航键盘测试。
- 1440、900、390 三档页面内容加载完成后均无页面级横向滚动。
- axe：关键页无 serious/critical 违规。
- Lighthouse Accessibility：100。
- 问题列表、批次列表，以及批次详情的失败任务、当前结果、Attempt History 均固定每页 10 条，支持页码、上一页/下一页和输入页码跳转；分页状态写入 URL。
- 问题粘贴支持 `CRLF`、`LF`、`CR` 和连续空行；纯文本每个非空行识别为一个问题。
- 两轮截图审查修复 Grid 等高拉伸、固定空态高度、设置页孤列、问题输入区过高及 981–1180px 中间断点问题。

## 真实 PostgreSQL + Redis 演练

- PostgreSQL 16、Redis 7、两个 RQ worker。
- 一个 worker 在持有任务租约时被强制终止，reconciler 回收并重新派发。
- 最终 6/6 success、duplicate current=0、expired lease=0。
- Redis 与 PostgreSQL 分别暂停时依赖检查正确返回失败，恢复后重新返回正常。
- 演练发现并修复 PostgreSQL JSON datetime 与空字符串 timestamp 两个真实兼容问题。

## 已签署与部署前观察项

1. P0-P1 当前工作树验收已签署：后端 130 项、前端 12 项、E2E 35 项、Lighthouse Accessibility 100、真实 PostgreSQL/Redis 双 worker 演练全部通过。
2. DeepSeek Web browser contract 已通过；真实账号下的登录失效、验证码、selector 变化和 browser crash 专项演练仍需可控账号，作为上线前运行演练而非代码验收阻断项。
3. 磁盘阈值、SQLite lock/recovery 与 DB commit 后 RQ ack 前迟到 failure callback 已有确定性测试；生产规模压力演练继续作为部署加固项。

结论：V2 P0-P1 实现与当前环境可执行验收完成。
