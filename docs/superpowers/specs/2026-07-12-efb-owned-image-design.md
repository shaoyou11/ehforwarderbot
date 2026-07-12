# EFB 自有镜像与配置备份设计

## 目标

- 由 `shaoyou11` 的 GitHub 仓库维护 EFB 镜像和 comwechat slave 定制代码。
- 离线后首次发送提醒；持续离线时每 8 小时再次提醒；重新登录后重置提醒周期。
- 保留地图链接解析和 `/forward` 微信原生消息转发功能。
- 飞牛 NAS 默认使用 `ghcr.io/shaoyou11/efb:latest`，同时保留固定版本标签用于回滚。
- 私有配置仓库只保存脱敏、可恢复的配置结构，不保存明文密钥和运行数据。

## 仓库划分

1. 公开 `shaoyou11/ehforwarderbot`
   - Dockerfile、Compose 模板、启动脚本和 GitHub Actions。
   - 构建并发布 `ghcr.io/shaoyou11/efb:latest` 与版本标签。
2. 公开 `shaoyou11/efb-wechat-comwechat-slave`
   - 基于上游 slave，直接实现 8 小时离线提醒。
   - 保留上游 `/forward` 功能。
3. 私有 `shaoyou11/efb-config-private`
   - 保存脱敏 Compose、YAML 配置模板、文件清单和恢复说明。
   - Token、API ID、API Hash、管理员 ID、密码使用占位符。
   - 排除数据库、日志、缓存、二维码和微信文件。

## 构建与版本

- EFB Dockerfile 固定安装 `shaoyou11/efb-wechat-comwechat-slave` 的明确提交，确保构建可复现。
- 地图中间件继续安装并在 comwechat profile 中启用。
- GitHub Actions 在主分支更新时发布 `latest`，同时发布提交版本标签。
- NAS Compose 使用 `latest`；切换前记录当前镜像摘要和可回滚固定标签。
- `telegram-bot-api` 与 comwechat 继续使用现有镜像，不将其源码纳入本次接管。

## 部署与备份

- 修改前在 NAS 创建带时间戳的本地备份，包含 Compose、入口脚本和 EFB 配置。
- 不上传 NAS 上的明文密钥；私有仓库只提交脱敏副本。
- 拉取新 EFB 镜像并仅重建 EFB 服务，避免无故重建 comwechat。
- 新镜像启动后验证容器状态、源码版本、离线提醒参数、地图中间件和 `/forward` 代码存在。
- 保留旧镜像摘要及固定标签，必要时将 Compose 临时改回固定标签恢复。

## 风险控制

- 上游更新不会自动合并进自有 slave；需要审阅后再同步，避免覆盖定制逻辑。
- `latest` 可能随构建变化，因此每次部署前保留固定标签和 NAS 本地备份。
- 健康检查只在现有服务具备稳定探测接口时添加，避免错误重启正常服务。
- 不改变 Telegram Bot API、comwechat 的网络结构及现有持久化目录。

## 验收标准

- 两个公开仓库和一个私有配置仓库可访问性正确。
- GHCR 同时存在 `latest` 和固定版本标签，支持 NAS 架构。
- EFB 容器使用 `ghcr.io/shaoyou11/efb:latest` 并正常运行。
- 镜像内离线提醒间隔为 8 小时，首次离线会提醒，登录后会重置。
- 地图中间件处于启用状态，`/forward` 功能代码保留。
- 私有配置库扫描不到真实 Token、API Hash、密码和运行数据。
- NAS 备份路径、修改内容、持久化方式和回滚命令均有记录。
