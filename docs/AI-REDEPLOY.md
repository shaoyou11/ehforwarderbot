# EFB 安装、恢复与 AI 运维指南

本文是本仓库的部署入口，适用于自己的 Linux / 飞牛主机。先阅读本文，再看 [网页版说明](../web-image/README.md)、[HA 通知](../web-image/HA-NOTIFICATIONS.md) 和 [通知保护与回退](../web-image/NOTIFICATIONS.md)。仓库中的旧 Compose 不是现有生产环境的完整备份，不能直接覆盖线上配置。

## 1. 必须先确定的事项

接手任务的 AI 必须先区分“检查”“新装”“恢复”“更新”。检查不包含重启、重新扫码、发送消息或重放历史。用户没有要求切换方案时，保留当前活动方案。

- 两套 EFB 前端共用 Telegram 机器人时，只允许一套接收更新和转发；不要同时启动。
- 网页版使用独立 `web` 配置；ComWechat 使用独立 `comwechat` 配置。登录凭据不相互复制。
- 群名、联系人名称只能作为核对候选；实际绑定必须用确认后的通道 ID，并保留原 Telegram 群和话题编号。
- 不回放发送结果不确定的消息，不把通知失败变成无限重启或重复扫码。
- 不删除 Docker 网络、卷、聊天数据库或历史数据。需要清理时先列出明确路径并取得授权。
- 不在终端输出、Git、PR、构建日志或镜像内写入密码、Token、HA 地址、真实管理员 ID、聊天 ID、联系人目录或绑定表。
- 容器运行不等于登录成功，发送接口返回不等于接收端已人工确认。

## 2. 架构和产物

| 部件 | 用途 | 安装来源与边界 |
|---|---|---|
| 原版 EFB | Telegram 与 ComWechat 转发 | `Dockerfile`，镜像 `ghcr.io/shaoyou11/efb` |
| ComWechat 引擎 | Windows 微信和接口 | 从私有部署清单恢复已验证的镜像摘要、版本和挂载；不要随意升级微信版本 |
| 网页版 EFB | 使用 Web 微信协议转发 | `web-image/Dockerfile`，镜像 `ghcr.io/shaoyou11/efb-web-standby` |
| 本地 Telegram Bot API | 大文件下载和共享附件缓存 | 从私有清单恢复固定镜像；独立网络，不依赖 ComWechat 容器存活 |
| 后端控制服务 | 切换、同步、二维码状态和互斥 | `web-image/control/worker.py`，运行在有 Docker 权限的主机 |
| 外部通知入口 | 验证来源并写独立队列 | `notification_ingress.py`；默认关闭，仅本机监听 |
| HA 通知适配器 | 订阅指定通知实体的新文本调用 | `notification_ha.py`；独立进程，不修改 HA 自动化、不读取 Telegram 历史 |

这里的“网页版”指协议实现，不是浏览器自动化。微信是否允许登录由上游决定，不能承诺永不下线或绕过限制。Telegram 本地 Bot API 的容量不代表微信上传容量；网页版普通文件/视频目前保留 25 MiB 保护阈值。

## 3. 私有部署清单

新装前由用户提供，恢复时从加密备份读取。缺少项目不要猜测。真实文件放在仓库外，权限 `0600`；相关目录为 `0700`，仅必要服务用户可访问。

| 项目 | 必须记录 |
|---|---|
| 镜像 | 每个容器的完整 `@sha256:` 引用、源码提交、CPU 架构；不能只记 `latest` |
| 容器和服务 | Compose 项目名、服务名、容器名、systemd 单元、启动顺序 |
| 数据位置 | 原版根目录、网页版根目录、两套 profile、本地 Bot API 缓存、控制状态目录 |
| 权限 | 容器实际 UID/GID、缓存所有者、只读与读写挂载 |
| 网络 | 既有网络名、驱动、父接口、子网、指定网关、DNS、预留地址、Bot API 内网端口 |
| 密钥 | Telegram Bot Token、API ID/hash、管理员 ID、控制签名密钥、HA Token、通知来源密钥；单独加密保管 |
| 业务 | 当前活动方案、已确认身份映射、原群/话题绑定、个性化配置、通知允许列表 |
| 备份 | 时间、校验和、恢复所用镜像、加密方式；解密密钥与备份分开存储 |

不能直接运行根目录的历史 `docker-compose.yaml`：它包含多实例示例和浮动标签。`web-image/compose.yaml` 也只是默认不登录的隔离检查示例。生产 Compose 应从私有备份恢复或根据以上清单单独生成。

## 4. 目录与配置

推荐结构示意（名称可变，容器内路径必须与代码保持一致）：

```text
<私有部署根目录>/
  native/                         # 原版 /data 根目录
    profiles/comwechat/
    telegram-bot-api/              # 共享缓存，Bot API 可写
  web/
    data/
      ALLOW_WEB_LOGIN              # 只有明确启用登录时才建立
      profiles/web/
        config.yaml
        blueset.telegram/config.yaml
        blueset.telegram/tgdata.db
        blueset.wechat/            # 独立会话缓存
    controller-code/               # 从匹配版本代码提取
    controller-config.json
    controller-state/
      secret                      # 随机签名密钥，不与通知来源密钥混用
      state.json
      mapping.json
      notifications/
        policy.json
        sources.json
        queue.sqlite
        ha-source.json
```

实际状态文件由程序生成，不要凭示意手造“已登录”状态。`mapping.json` 结构及控制配置字段以匹配版本的 `sync.py`、`worker.py`、`transport.py` 和测试为准；恢复优先使用已验证备份。

网页版顶层 `config.yaml` 的核心字段：

```yaml
master_channel: blueset.telegram
slave_channels:
  - blueset.wechat
```

中间件只加入已安装且需要的组件。Telegram 子配置需要私有 `token`、`admins`；启用本地 Bot API 时设置 `flags.api_base_url`（以 `/bot` 结尾）、`flags.api_base_file_url`（以 `/file/bot` 结尾）和 `flags.local_bot_api: true`。不要复制原生专用中间件到网页版。

网页版容器至少需要：

- `EFB_WEB_ENABLE_LOGIN=1`、`EFB_PROFILE=web`，以及 `/data/ALLOW_WEB_LOGIN`。
- 网页版数据读写挂载为 `/data`；控制状态读写挂载为 `/backend-control`。
- Bot API 缓存按原路径只读挂载为 `/var/lib/telegram-bot-api`。
- UID/GID 能读写自己的数据，并读取/遍历共享缓存。不要用 `chmod 777` 或改变原服务所有权解决权限问题。
- 固定镜像摘要、合理日志轮转和独立网络。由控制服务管理互斥时，前端使用 `restart: "no"`，避免 Docker 自行同时启动两套。

## 5. 网络与启动顺序

先读取现有 Docker 网络配置，不要删除后重建同名网络。网关必须是用户指定的旁路由，不能把 DNS 设置误认为网关设置。检查地址冲突、DHCP 保留和主机到容器的可达性；macvlan 的主机隔离需要按实际拓扑处理，不能为了连通而更改整个宿主机路由。

Bot API、网页版拥有独立网络连接，不能用 `network_mode: service:comwechat` 让其依赖原生引擎。`web-image/deploy/independent_network.py` 是既有布局的迁移工具，会改写多个配置文件，不是通用新装脚本；必须先审阅、在副本试运行并核对输出。迁移工具要求私有环境变量 `EFB_BOT_API_IPV4`、`EFB_WEB_IPV4`、`EFB_DNS`、`EFB_LAN_NETWORK`，不会替用户选择网关。

启动顺序：

1. 备份并确认另一套转发前端已停止。
2. 启动本地 Bot API，检查 API 可达和缓存权限，不发送业务消息。
3. 启动控制服务，读取恢复的活动方案；ComWechat 模式需先等待原生引擎健康。
4. 启动选定前端，检查新鲜心跳、微信认证、Telegram 转发初始化。
5. 需要扫码时使用最新二维码；确认登录并就绪后撤回二维码。
6. 最后启动已授权的通知入口/HA 订阅。新装默认关闭所有通知路由。

`boot.py` 负责共享传输启动检查；`worker.py <私有controller-config.json>` 负责控制循环。控制配置需要 `control_root`、`profiles`、`containers`、`admins`、`watchdog`；独立网络模式还需要 `network_independent`、`native_container`、`bot_api_container`，可选 `native_aux_unit` / `native_aux_container`。这些名字必须来自真实部署清单。主机服务只给予必要的 Docker 控制权限，不能挂载 Docker socket 到公网应用。

## 6. 绑定同步与命令

`/status` 查看当前方案和适用状态，`/backend` 切换，`/sync` 同步已确认绑定和个性化，`/link` 管理绑定。`/login` 登录当前方案，`/web_login` 与 `/cw_login` 分别说明对应方案；非活动方案应先切换。

自动同步以当前活动方案为来源，并通过已确认身份表转换 ID。仅同步实现支持的配置字段；登录会话、Token、历史消息和待发送队列不参与同步。遇到未知身份、重复映射、目的位置冲突时停止应用或保留待核对项，不能按同名强行覆盖。独立群绑定与话题绑定要分别验收。

原生 `/bridge`、`/watchdog`、`/trace` 等功能不能当作网页版能力。话题误投补发必须另获授权，先生成去重清单，不自动补发历史消息。

## 7. HA 通知服务

详见 [HA 接入说明](../web-image/HA-NOTIFICATIONS.md)。生产只启用用户明确指定的来源和绑定；不得因为新装默认开启全部绑定。

- 主机独立 Python 虚拟环境安装 `control/requirements-ha-source.txt`，不要修改 EFB 容器或宿主全局 Python 依赖。
- 服务命令：`<venv>/bin/python <controller-code>/notification_ha.py`。
- 环境变量：`EFB_NOTICE_ROOT=<通知目录>`、`EFB_NOTICE_BINDINGS=<网页版tgdata.db>`。
- `ha-source.json` 仅有私有 `url`、`token`、`entity`；来源密钥放在 `sources.json`，不是 Telegram Token。
- 服务用户与通知队列所有者一致，虚拟环境必须可读/可执行。推荐 systemd `NoNewPrivileges=yes`、`ProtectSystem=strict`、`ProtectHome=yes`、`UMask=0077`、仅通知目录可写、`Restart=on-failure`，限制内存和 CPU。
- HA 适配器使用内网出站订阅，不需要新开放监听端口。HTTP 入口另运行 `notification_ingress.py`，默认只监听回环地址。
- `/notify` 可关闭转发；不会停用原 HA → Telegram 通知。切换方案或离线时不能跨账号发送，过期通知不集中补发。

## 8. 备份、恢复、回退

变更前保存 Compose、环境文件、镜像摘要、控制代码/配置/状态、两套 profile、身份映射和通知设置。在线 SQLite 用 SQLite backup API 创建一致性副本，不直接复制活跃数据库及部分 WAL。会话文件需要在程序停止或程序支持的一致性保存点备份。核对数据库 `PRAGMA quick_check`、文件校验和，并保存一份加密异机备份。

恢复到新的私有目录，先校验备份再修复 UID/GID。不要先覆盖现有在线数据。停止受影响服务，恢复固定镜像及匹配配置，按启动顺序逐个启用。故障回退默认只回退程序/镜像，不覆盖较新的群绑定和消息去重数据库。

通知回退可先用 `/notify` 关闭，再停止 HA 订阅服务。`notification_switch.py rollback --root <网页版根目录> --backup <匹配备份>` 默认预演；只有核对预演并获得实际回退授权后才加 `--apply`。该工具依赖既有布局和备份清单，不适用于任意新装目录。

## 9. 验收和故障定位

验收记录区分静态检查、接口返回、实际收件和未验证项。未授权不要发测试消息。

| 检查 | 判断依据 |
|---|---|
| 配置与权限 | Compose 在私有环境校验成功，挂载/UID/GID/网关一致，无双前端运行 |
| 微信连接 | 认证完成且新鲜心跳就绪；不能只看容器 Up |
| 绑定 | 普通群、话题各核对一个已确认目标；无新增误建话题 |
| 媒体 | 实际图片和文件双向收件；本地缓存读取成功不等于微信已收到 |
| HA 接入 | `ha-status.json` 订阅连接正常且心跳更新；通知队列 `sent` 为发送接口成功，实际收件另行确认 |
| 异常恢复 | 先离线模拟网络/超时/重复事件；真实断网、主机重启需授权维护窗口 |
| 回退 | 备份可读、摘要一致、数据库通过检查；预演通过不等于已实际回退 |

HA 有 Telegram 消息但微信无记录：先看订阅状态、目标通知实体是否完全匹配、来源及路由是否开启。`pending` 查限速/聊天优先/离线，`blocked` 查绑定变更，`unknown` 不能自动重发，`expired` 不追补。不要直接触发一次真实水满自动化来“验证日志”。

## 10. 代码检查、镜像发布和部署分离

原版和网页版分别构建不同镜像。主分支合并和 PR 只检查，不自动发布或部署。需要发布时，在对应工作流选择主分支并显式设置 `publish=true`；发布后仍须单独授权生产部署，并在私有 Compose 中固定新摘要。

源码、镜像、线上版本分别记录，不因 Git 合并成功就宣称已经更新线上。GitHub 不持有主机部署凭据；不增加自动更新固定生产镜像的任务。
