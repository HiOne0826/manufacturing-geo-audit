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

## 风险说明

如果把本项目直接覆盖到 `/opt/geoflow`，会直接破坏线上已运行的 GEO 内容生产系统，因此禁止混用部署目录。
