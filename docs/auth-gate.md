# 阶段 1 公网门禁

阶段 1 使用两层门禁：

1. Nginx Basic Auth 挡住公网入口。
2. 应用全局密码写入 HttpOnly HMAC Cookie，保护 `/api/*`。

## 应用环境变量

在服务器 `/opt/manufacturing-geo-audit/.env` 写入：

```bash
APP_PASSWORD=替换为强密码
APP_SESSION_SECRET=替换为独立随机字符串
AUTH_COOKIE_NAME=geo_audit_session
ALLOW_LIVE_MODEL_CALLS=0
```

未设置 `APP_PASSWORD` 时，本地开发保持无应用密码模式；公网部署必须设置。
需要在服务器验收真实模型调用时，把 `ALLOW_LIVE_MODEL_CALLS` 改为 `1`，并重启 Web 与 worker。

## 认证接口

- `POST /api/auth/login`：请求体 `{"password":"..."}`，成功后写入 HttpOnly Cookie。
- `POST /api/auth/logout`：清除 Cookie。
- `GET /api/auth/status`：返回是否启用应用密码、当前请求是否已登录。

除以下接口外，所有 `/api/*` 都需要已登录：

- `GET /api/health`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/status`

## Nginx Basic Auth

生成密码文件：

```bash
sudo htpasswd -c /etc/nginx/.geo_audit_htpasswd geo
```

项目配置位于：

```text
deploy/server/manufacturing-geo-audit.conf
```

关键配置：

```nginx
auth_basic "GEO Audit";
auth_basic_user_file /etc/nginx/.geo_audit_htpasswd;
```

## systemd 环境变量

`deploy/server/manufacturing-geo-audit.service` 已配置：

```ini
EnvironmentFile=-/opt/manufacturing-geo-audit/.env
```

更新服务器配置后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart manufacturing-geo-audit
sudo systemctl restart manufacturing-geo-audit-worker
sudo nginx -t
sudo systemctl reload nginx
```

## 本地验证

启用应用密码启动：

```bash
APP_PASSWORD=test-password APP_SESSION_SECRET=test-secret GEO_AUDIT_PORT=8876 python3 app.py
```

未登录访问应返回 401：

```bash
curl -i http://127.0.0.1:8876/api/projects
```

登录后冒烟：

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8876 --password test-password
```
