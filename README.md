# EFB 微信转 Telegram 镜像

基于 EH Forwarder Bot 的 Docker 镜像，用于将 ComWeChat 微信消息转发至 Telegram。项目在原部署方案上持续维护，并保留家庭 NAS 环境所需的个性化功能。

## 已集成功能

- ComWeChat 微信从端与 Telegram 主端
- 本地 Telegram Bot API，支持突破默认文件大小限制
- 微信视频号消息解析，视频下载失败时提供可点击的视频号链接
- 微信登录状态提醒及 Watchdog 自动恢复控制
- 地图、关键词替换、关键词回复和消息合并中间件
- Telegram 命令及微信附加功能中文化
- 多架构 Docker 镜像自动构建

## 镜像

```text
ghcr.io/shaoyou11/efb:latest
```

部署配置示例见仓库中的 `docker-compose.web.yaml` 和 `profiles` 目录。实际账号、Bot Token 等敏感配置请放在私有配置仓库或 NAS 持久化目录，不要提交到公开仓库。

## 上游与参考

- [EH Forwarder Bot](https://github.com/ehForwarderBot/ehForwarderBot)
- [EFB Docker 部署参考](https://jiz4oh.com/2023/01/run-efb-in-docker/)
- [EFB 聊天记录迁移参考](https://jiz4oh.com/2025/05/transfer-chat-history-in-efb/)

本仓库由 `shaoyou11` 维护。
