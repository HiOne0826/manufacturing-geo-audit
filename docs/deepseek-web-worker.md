# DeepSeek 官网采样 worker

## 能力边界

`deepseek_web` 通过 Playwright 正常操作 `https://chat.deepseek.com`，用于复现官网开启联网搜索后的用户体验。它不是 DeepSeek 公共 API，也不会直接调用或逆向网页内部接口。

- 每个问题创建新的 `BrowserContext` 和官网 chat session。
- 官网渲染 DOM 是答案和引用的真相源。
- 页面网络响应仅用于被动交叉校验和审计。
- 单账号固定并发 1；网页 batch 不允许混入 API provider。
- 验证码、登录失效和账号风控会暂停 batch，不自动绕过。

## 本地安装

```bash
python3 -m pip install -r requirements-web-worker.txt
python3 -m playwright install chromium
```

必要配置：

```bash
TASK_QUEUE_BACKEND=rq
REDIS_URL=redis://127.0.0.1:6379/0
DEEPSEEK_WEB_ENABLED=1
RQ_WEB_QUEUE_NAME=geo-audit-web
DEEPSEEK_WEB_AUTH_STATE=private/deepseek-web/storage-state.json
DEEPSEEK_WEB_ARTIFACT_DIR=data/deepseek-web-artifacts
DEEPSEEK_WEB_HEADLESS=0
DEEPSEEK_WEB_JOB_TIMEOUT=900
DEEPSEEK_WEB_RESPONSE_TIMEOUT_SECONDS=240
DEEPSEEK_WEB_COOLDOWN_SECONDS=3
```

首次登录必须人工完成：

```bash
python3 scripts/deepseek_web_auth.py login
python3 scripts/deepseek_web_auth.py status
python3 scripts/deepseek_web_auth.py preflight
```

登录态保存为 `0600` 的 `storage_state.json`，已通过 `.gitignore` 排除。不要把该文件、Cookie 或 Token 放入日志、数据库、工单或提交记录。

## 启动

先启动 Redis、Web 应用和普通 API worker，再启动网页 worker：

```bash
python3 app.py
python3 worker.py
python3 deepseek_web_worker.py
```

网页 worker 使用 RQ `SimpleWorker`，同一进程复用 Chromium，但每题销毁独立 context。创建 batch 时会持久化所有 `sampling_tasks`，仅链式入队下一题，保证暂停、恢复和单账号串行执行。

## API

- `POST /api/runs/start`：使用 `deepseek_web` 模型且 `search_enabled=true` 创建独立网页 batch。
- `GET /api/runs/progress?batch_id=...`：返回 task 聚合进度和 `blocked` 数量。
- `GET /api/batches/{batch_id}/tasks`：查看每题状态、尝试次数、chat ID 和错误类型。
- `POST /api/batches/{batch_id}/pause`：当前题收尾后停止调度下一题。
- `POST /api/batches/{batch_id}/resume`：重置失败、阻塞或过期任务并继续缺失项。
- `GET /api/settings/deepseek-web`：返回 Playwright、认证、队列和证据目录统计，不返回敏感值。

## 证据与故障

每题证据保存在：

```text
data/deepseek-web-artifacts/{batch_id}/{task_id}/
```

包含截图、HTML、答案、引用、脱敏网络事件、操作事件和运行元数据。目录不对公网提供静态访问。显式清理前先 dry-run：

```bash
python3 scripts/prune_deepseek_web_artifacts.py --before 2026-01-01
python3 scripts/prune_deepseek_web_artifacts.py --before 2026-01-01 --execute
```

错误处理：

- 网络、页面超时、浏览器崩溃：最多尝试 3 次。
- 登录失效、验证码：任务记为 `blocked`，batch 进入 `paused`。
- 同一种选择器或采集契约错误连续 3 次：触发 circuit breaker 并暂停。
- worker 重启：启动时将遗留 `running` 任务恢复为 `queued`，从缺失项继续。

## 验收顺序

1. 运行离线测试：`python3 -m unittest tests.test_deepseek_web`。
2. 用 5 条真实问题校准官网 selector contract。
3. 用专用账号运行 30 条 POC，核对唯一 chat ID、页面答案、引用和证据。
4. 首次成功率达到 95%，其余任务经重试成功或具有明确不可重试错误后，才部署服务器。
5. 服务器先跑 5 条 canary，再允许 140 条完整批次。

服务器需要 Chromium 和 `xvfb-run`，systemd 模板为 `deploy/server/manufacturing-geo-audit-deepseek-web-worker.service`。若服务器持续出现验证码、地区限制或账号风控，停止部署，不启用规避方案。
