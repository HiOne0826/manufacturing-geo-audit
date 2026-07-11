# Trace C：UX、无障碍与验收审计

## 初始结论

视觉重构、分页、粘贴、基础响应式和 dialog 焦点管理已有实现，但原 5 项 E2E 只覆盖 shell，无法证明 P0 UX。

## 发现的关键问题

- 项目切换没有未保存内容保护，分页和分析 URL 状态清理不完整。
- 硬删除在影响预览完成前可提交。
- `ACTIVE_BATCH_EXISTS` 被降级成普通错误。
- 移动导航没有 dialog/focus/Escape 语义。
- `AsyncBoundary` 缺刷新、缓存过期与 skeleton 状态。
- Recharts 被打进所有业务页面共同 chunk。
- axe、三视口和 Lighthouse 缺少足够的可复现覆盖。

## 修复后复核方向

已补 dirty confirm、完整 URL 清理、删除 gate、结构化 API error 与恢复链接、移动导航焦点管理、统一异步状态、独立图表 chunk、全关键页三视口/axe/E2E 用例以及 Lighthouse 95 gate。新增 E2E 仍需在允许启动本地服务的环境中执行。
