# 前端 Vite + React 重构实施方案

分支：`feature/frontend-vite-react`

更新时间：2026-06-11

## 1. 目标

把当前 `static/index.html + static/app.js + static/styles.css` 的原生前端，重构为 Vite + React + TypeScript 的正式内部系统前端。

重构目标不是做营销页，而是做一个高密度、低干扰、适合反复操作的内部审计工具。

核心目标：

- 把新增的批次、并发、失败重跑、Postgres/RQ、Agent API、模型默认参数等能力可视化。
- 改善多模型多问题采样的任务创建体验。
- 给批次运行状态、失败原因、模型耗时和导出入口提供清晰视图。
- 保持当前后端 API 兼容，不先重写后端。

## 2. 当前问题

当前前端集中在一个大文件：

```text
static/app.js
```

主要问题：

- 页面状态、API 调用、DOM 渲染和事件绑定耦合在一起。
- 新增功能后，采样页信息密度过高。
- 批次状态、模型耗时、失败原因、重跑入口没有独立工作区。
- 模型默认采样参数已进入后端，但前端展示还不够系统。
- Agent API、Postgres/RQ 等能力没有可视化运维入口。

## 3. 重构原则

- 保持内部工具风格：高信息密度、少装饰、快速扫描。
- 不做 landing page。
- 第一屏就是可操作系统。
- 保留现有后端 API，不阻塞后端迁移。
- 不让前端接触明文 API Key。
- 不在前端绕过后端直接调用模型服务商。
- 先做功能完整，再做视觉微调。

## 4. 技术选型

推荐：

```text
Vite
React
TypeScript
TanStack Query
Zustand
React Router
Recharts
Lucide React
```

说明：

- Vite：构建简单，适合当前轻量后端。
- React + TypeScript：更适合复杂状态和组件拆分。
- TanStack Query：管理 API 请求、轮询、缓存和 loading/error 状态。
- Zustand：管理全局 UI 状态，如当前项目、当前批次、筛选条件。
- React Router：管理页面路由。
- Recharts：批次耗时、成功率、模型对比图。
- Lucide React：统一图标。

不建议首期引入：

- 大型 UI 框架。
- SSR。
- Next.js。
- Redux。
- 复杂权限系统。

## 5. 推荐目录

新增：

```text
frontend/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    app/
      App.tsx
      routes.tsx
      queryClient.ts
    api/
      client.ts
      auth.ts
      projects.ts
      questions.ts
      models.ts
      batches.ts
      runs.ts
      exports.ts
      agent.ts
      types.ts
    components/
      layout/
        AppShell.tsx
        Sidebar.tsx
        TopBar.tsx
      common/
        Button.tsx
        IconButton.tsx
        StatusBadge.tsx
        EmptyState.tsx
        DataTable.tsx
        Metric.tsx
        ConfirmDialog.tsx
      sampling/
        ProjectQuestionScope.tsx
        ModelMatrix.tsx
        RuntimeDefaultsPanel.tsx
        TaskEstimatePanel.tsx
        SamplingSubmitBar.tsx
      batches/
        BatchList.tsx
        BatchProgress.tsx
        BatchTimeline.tsx
        BatchProviderSummary.tsx
        BatchFailurePanel.tsx
        BatchExportActions.tsx
      models/
        ModelConfigTable.tsx
        ModelProviderBadge.tsx
        ModelDefaultsPopover.tsx
    pages/
      DashboardPage.tsx
      ProjectsPage.tsx
      QuestionsPage.tsx
      ModelsPage.tsx
      SamplingPage.tsx
      BatchesPage.tsx
      BatchDetailPage.tsx
      AnalysisPage.tsx
      SettingsPage.tsx
    store/
      uiStore.ts
      selectionStore.ts
    styles/
      globals.css
      tokens.css
```

保留：

```text
static/
```

迁移期可让后端优先服务构建后的静态文件：

```text
frontend/dist -> static/app/
```

或者后端直接指向：

```text
STATIC_DIR = ROOT / "frontend" / "dist"
```

最终选择建议：

- 本地开发：`npm run dev` 代理到 `127.0.0.1:8765`。
- 生产部署：`npm run build`，把 `frontend/dist` 作为静态目录。

## 6. 页面信息架构

### 6.1 Dashboard

目标：进入系统后快速知道是否可运行。

内容：

- 最近批次数。
- 运行中批次数。
- 最近 24 小时成功率。
- 模型可用数量。
- 最近真实抽样耗时。
- 系统模式：SQLite / PostgreSQL，inline / RQ。
- `ALLOW_LIVE_MODEL_CALLS` 状态提示。

需要后端补强：

- 可选新增 `/api/system/status`。
- 或先复用 `/api/health` + `/api/batches` + `/api/models`。

### 6.2 Projects

目标：维护客户和品牌项目。

功能：

- 项目列表。
- 问题数、run 数、最近批次状态。
- 创建/编辑/删除项目。
- 进入问题库或采样页。

### 6.3 Questions

目标：维护项目问题库。

功能：

- 项目筛选。
- 表格编辑。
- CSV 导入。
- 模板问题生成。
- 按优先级、问题类型、采购阶段筛选。

UX：

- 表格优先，避免卡片堆叠。
- 批量导入结果要显示新增数量。

### 6.4 Models

目标：管理模型配置和可用性。

展示字段：

- provider
- label
- model
- has_key
- supports_search
- supports_reasoning
- sampling_defaults
- 最近成功率
- 最近平均耗时

功能：

- 添加 provider preset。
- 编辑模型。
- 测试模型。
- 显示 key 掩码，不显示明文 key。

UX：

- 模型默认参数用 popover 展示。
- 模型测试结果要区分：认证失败、参数错误、网络超时、服务商错误。

### 6.5 Sampling

这是重构重点。

布局：

```text
左列：项目和问题范围
中列：模型矩阵
右列：运行参数和任务预估
底部：提交栏
```

模块：

- `ProjectQuestionScope`
- `ModelMatrix`
- `RuntimeDefaultsPanel`
- `TaskEstimatePanel`
- `SamplingSubmitBar`

任务预估：

```text
问题数 × 模型数 × repeat_count = 总任务数
```

显示：

- 总任务数。
- 预计并发数。
- 预计最长模型。
- 是否启用真实模型调用。
- 是否启用搜索增强。
- 是否启用深度思考。

模型矩阵：

| 选择 | 模型 | Key | 默认参数 | 搜索 | 思考 | 最近成功率 | 最近平均耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |

重要交互：

- 搜索和思考互斥规则按 provider 提示。
- Kimi 搜索时提示必须关闭思考。
- Kimi K2.5 显示 `temperature=0.6` 且默认不可随意改。
- 没有 key 的模型默认不可选。
- 真实调用未开启时，真实模型提交前提示。

### 6.6 Batches

目标：批次中心。

列表字段：

- batch_id
- 项目
- 状态
- total
- completed
- success
- failed
- started_at
- finished_at
- elapsed
- 操作

操作：

- 查看详情。
- 导出 runs XLS。
- 重跑失败。

状态：

- queued
- running
- completed
- failed

### 6.7 Batch Detail

目标：单批次复盘和操作。

模块：

- 总进度。
- 成功/失败指标。
- Provider summary。
- 模型耗时排行。
- 失败原因聚合。
- 运行明细表。
- 导出按钮。
- 重跑失败按钮。

Provider summary 示例：

| Provider | Total | Success | Failed | Avg Latency | Max Latency |
| --- | ---: | ---: | ---: | ---: | ---: |

失败原因聚合：

- 认证失败。
- 参数错误。
- 超时。
- 服务商错误。
- 系统错误。

### 6.8 Analysis

目标：项目级分析。

内容：

- 品牌提及率。
- 官网引用率。
- 竞品共现排行。
- 分模型表现。
- 搜索增强 vs 纯模型对比。

首期可复用现有 `/api/analytics`。

### 6.9 Settings

目标：系统状态和运行配置可见化。

显示：

- auth_enabled。
- db 模式。
- task_queue_backend。
- live model calls 是否开启。
- max workers。
- provider max workers。
- request timeout。
- RQ queue name。

不在前端编辑 `.env`，只展示状态。

## 7. API 契约整理

前端第一阶段复用现有 API：

```text
GET  /api/health
GET  /api/auth/status
POST /api/auth/login
POST /api/auth/logout

GET  /api/projects
POST /api/projects
POST /api/projects/update
POST /api/projects/delete

GET  /api/questions
POST /api/questions/import_rows
POST /api/questions/update
POST /api/questions/delete

GET  /api/models
POST /api/models
POST /api/models/update
POST /api/models/test
POST /api/models/delete

POST /api/runs/start
GET  /api/runs/progress?batch_id=...
GET  /api/runs?project_id=...

GET  /api/batches
GET  /api/batches/{batch_id}
GET  /api/batches/{batch_id}/runs
POST /api/batches/{batch_id}/rerun_failed

GET  /api/export/batches/{batch_id}/runs.xls
GET  /api/export/batches/{batch_id}/summary.xls
GET  /api/analytics
```

建议补强 API：

```text
GET /api/system/status
GET /api/batches/{batch_id}/summary
GET /api/models/runtime_stats
```

`/api/batches/{batch_id}/summary` 建议输出：

```json
{
  "batch_id": "batch-xxx",
  "status": "completed",
  "total": 18,
  "success": 18,
  "failed": 0,
  "elapsed_ms": 68623,
  "providers": [
    {
      "provider": "kimi",
      "total": 3,
      "success": 3,
      "failed": 0,
      "avg_latency_ms": 12887,
      "max_latency_ms": 14178
    }
  ],
  "failure_groups": []
}
```

## 8. 前端状态模型

服务端状态：

- projects
- questions
- models
- batches
- batch detail
- analytics

用 TanStack Query 管理。

客户端状态：

- 当前项目。
- 当前页面。
- 当前采样配置草稿。
- 表格筛选条件。
- 当前选中的 batch。

用 Zustand 管理。

轮询策略：

- running batch detail：1 秒轮询。
- batch list：5 秒轮询。
- completed / failed：停止轮询。

## 9. 视觉与 UX 规范

整体风格：

- 内部运维工具。
- 高密度。
- 清晰表格。
- 少装饰。
- 不做大面积 hero。
- 不使用大量卡片嵌套。

颜色建议：

- 背景：浅灰白。
- 主色：深青 / 蓝灰。
- 成功：绿色。
- 失败：红色。
- running：蓝色。
- queued：灰色。

组件规范：

- 操作按钮用 icon + text。
- 重复行数据用表格。
- 批次状态用 badge。
- 导出、重跑、刷新用 lucide 图标。
- 长错误信息默认折叠。
- 危险操作需要确认。

响应式：

- 桌面优先。
- 平板可用。
- 手机只保证查看，不重点优化批量操作。

## 10. 迁移步骤

### 阶段 A：脚手架

新增 `frontend/`：

```bash
npm create vite@latest frontend -- --template react-ts
```

安装：

```bash
npm install @tanstack/react-query zustand react-router-dom recharts lucide-react
```

配置 Vite proxy：

```ts
server: {
  proxy: {
    "/api": "http://127.0.0.1:8765"
  }
}
```

验收：

- `npm run dev` 可打开。
- 能访问 `/api/health`。
- 登录流程可用。

### 阶段 B：App Shell

实现：

- Sidebar。
- TopBar。
- AuthGate。
- 路由。
- 全局 QueryClient。

验收：

- 页面路由可切换。
- 未登录时展示登录。
- 登录后进入 Dashboard。

### 阶段 C：核心页面迁移

顺序：

1. Projects。
2. Questions。
3. Models。
4. Sampling。
5. Batches。
6. Batch Detail。
7. Analysis。

验收：

- 现有静态前端所有核心功能在 React 前端可用。
- API Key 不明文出现。
- Mock smoke test 可通过。

### 阶段 D：新增可视化

实现：

- 批次中心。
- 批次详情。
- provider summary。
- failure groups。
- latency chart。
- task estimate。
- runtime defaults display。

验收：

- 能查看最近批次。
- 能看单批次进度。
- 能导出 batch XLS。
- 能重跑失败项。

### 阶段 E：生产接入

方式：

```bash
cd frontend
npm run build
```

后端服务静态目录改为：

```text
frontend/dist
```

或构建后复制到：

```text
static/
```

建议保留旧前端一段时间：

```text
static-legacy/
```

验收：

- `python3 app.py` 能直接打开新前端。
- smoke test 通过。
- 手动完成一次 Mock 采样。
- 手动完成一次已有问题 × 已有 key 模型的真实采样。

## 11. 后端补强清单

为支持更好的前端体验，建议同步增加：

### 11.1 System Status

```text
GET /api/system/status
```

返回：

```json
{
  "auth_enabled": true,
  "db": "sqlite",
  "task_queue_backend": "inline",
  "live_model_calls_enabled": false,
  "sampling_max_workers": 8,
  "provider_max_workers": 3,
  "request_timeout": 120
}
```

### 11.2 Batch Summary

```text
GET /api/batches/{batch_id}/summary
```

用于批次详情页。

### 11.3 Model Runtime Stats

```text
GET /api/models/runtime_stats
```

用于模型库和采样页展示最近成功率、平均耗时。

## 12. 测试计划

新增：

```text
frontend/src/**/*.test.tsx
frontend/playwright/
```

建议测试：

- API client 单元测试。
- AuthGate 渲染。
- Sampling task estimate。
- ModelMatrix 选择逻辑。
- BatchProgress 状态渲染。
- FailureGroups 聚合。

端到端：

- 登录。
- 创建项目。
- 导入问题。
- 创建 mock 模型。
- 启动采样。
- 等待 completed。
- 查看 batch detail。
- 导出 XLS。

## 13. 验收标准

功能验收：

- 新前端覆盖旧前端核心功能。
- 批次中心可展示 queued/running/completed/failed。
- 批次详情可展示 provider summary、失败聚合、耗时排行。
- 采样页可显示任务预估。
- 模型默认参数可视化。
- API Key 不明文出现。

稳定性验收：

- `python3 -m unittest discover -s tests` 通过。
- 前端构建通过。
- Playwright smoke 通过。
- Mock 150 × 3 本地负载仍通过。

真实验证：

- 用已有 3 个问题 × 6 个已有 key 模型跑一次真实采样。
- 成功数 + 失败数 = 总数。
- 数据库落库数 = 总数。
- 批次状态最终为 completed 或 failed，不允许永久 running。

## 14. 风险和处理

| 风险 | 处理 |
| --- | --- |
| 前端迁移范围过大 | 保留旧 static，分阶段切换 |
| API 返回结构不稳定 | 建立 `api/types.ts` 并补测试 |
| 构建引入 Node 依赖 | 生产只部署 dist，不要求服务器跑 dev server |
| UI 变漂亮但效率下降 | 坚持表格、密度、批次中心优先 |
| 真实模型测试成本上升 | 默认 Mock，真实测试手动开启 |

## 15. 推荐实施顺序

1. 建立 Vite/React/TS 脚手架。
2. 建立 API client 和类型。
3. 实现 AuthGate + AppShell。
4. 迁移 Projects / Questions / Models。
5. 重做 Sampling 页面。
6. 新增 Batches / Batch Detail。
7. 补后端 summary/status/stats API。
8. 接入图表和失败聚合。
9. 构建生产静态文件。
10. 跑完整验证并替换旧前端入口。
