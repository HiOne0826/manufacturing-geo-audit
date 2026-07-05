# 鸵鸟 GEO 服务器部署备注

## 系统边界

本项目 `manufacturing-geo-audit` 是一套独立的 **GEO 效果检测 / 审计系统**：

- 技术栈：`Python 标准库 + SQLite + 静态前端`
- 本地启动方式：`python3 app.py`
- 用途：维护项目、问题库、模型配置、批量采样和分析 GEO 呈现结果

它 **不是** 服务器上现有的 `geoflow` 系统。

服务器现有的 `geoflow`：

- 目录：`/opt/geoflow`
- 技术栈：`Laravel + Docker`
- 用途：GEO 内容生产 / 流程系统

因此部署时必须分开：

- `geoflow` 继续保留在 `/opt/geoflow`
- 本项目独立部署到新的目录与新的服务端口

## 本次建议部署方式

- 代码目录：`/opt/manufacturing-geo-audit`
- 进程监听：`127.0.0.1:8765`
- Nginx 对外代理端口：`18082`
- 服务名：`manufacturing-geo-audit.service`

## 2026-07-05 更新内容

本轮部署包含客户交付前修正：

- Excel 客户版导出字段精简，CSV 继续保留完整字段。
- 平台展示统一为 ChatGPT、Gemini、DeepSeek、豆包、千问、元宝等客户口径。
- DeepSeek 联网口径通过 Brave Search 外部检索增强。
- ChatGPT / Gemini 可通过 OpenRouter-GPT / OpenRouter-Gemini 作为临时真实采样源。
- 采样页和批次详情页新增按测试平台的运行状态监测，避免用户重复点击启动采样。
- 同项目 queued/running 批次会被 `/api/runs/start` 复用，不会因为刷新或重复点击创建重复批次。
- 正式页面不允许创建 Mock 模型；真实模型调用仍受 `ALLOW_LIVE_MODEL_CALLS` 控制。

## 服务器配置检查

部署前确认 `/opt/manufacturing-geo-audit/.env` 至少包含：

```bash
APP_PASSWORD=...
APP_SESSION_SECRET=...
ALLOW_LIVE_MODEL_CALLS=0
SAMPLING_MAX_WORKERS=8
SAMPLING_PROVIDER_MAX_WORKERS=3
SAMPLING_PROVIDER_CONCURRENCY_LIMITS=openrouter:4,deepseek:2,doubao:2,qwen:2,hunyuan:2,gemini:2,openai:2
```

真实六平台验收时再临时确认并启用：

```bash
ALLOW_LIVE_MODEL_CALLS=1
OPENROUTER_API_KEY=...
BRAVE_SEARCH_API_KEY=...
DEEPSEEK_API_KEY=...
DOUBAO_API_KEY=...
QWEN_API_KEY=...
HUNYUAN_API_KEY=...
```

不要把任何 API Key 写入代码或提交到 git。

## 部署后验证

```bash
sudo systemctl status manufacturing-geo-audit --no-pager
curl -fsS http://127.0.0.1:8765/api/health
python3 scripts/load_test_local.py --questions 2 --models 2 --workers 2
```

如果启用了 RQ：

```bash
sudo systemctl status manufacturing-geo-audit-worker --no-pager
python3 scripts/check_task_system.py
```

## 风险说明

如果把本项目直接覆盖到 `/opt/geoflow`，会直接破坏线上已运行的 GEO 内容生产系统，因此禁止混用部署目录。
