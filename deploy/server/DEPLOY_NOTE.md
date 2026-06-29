# 制造业品牌 GEO 审计系统部署备注

- 部署目录：`/opt/manufacturing-geo-audit`
- 应用类型：`Python + SQLite + 静态前端`
- 本系统用途：`GEO 效果检测 / 审计`
- 本系统不是：`/opt/geoflow`
- `/opt/geoflow` 用途：独立的 GEO 内容生产系统（Laravel + Docker）
- 禁止把本项目覆盖到 `/opt/geoflow`

## 真实模型调用

如果服务器页面提示：

```text
真实模型测试当前被后端安全开关拦截。需要本地验收真实调用时，用 ALLOW_LIVE_MODEL_CALLS=1 重启 python3 app.py。
```

说明当前后端进程没有开启真实模型调用。systemd 服务会读取：

```text
/opt/manufacturing-geo-audit/.env
```

在服务器写入或修改：

```bash
ALLOW_LIVE_MODEL_CALLS=1
```

然后重启 Web 和 worker：

```bash
sudo systemctl restart manufacturing-geo-audit
sudo systemctl restart manufacturing-geo-audit-worker
```

如果只做 Mock 流程验证，保持：

```bash
ALLOW_LIVE_MODEL_CALLS=0
```
