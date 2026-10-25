# 独立网页版 EFB 镜像

部署入口：[安装、恢复与 AI 运维指南](../docs/AI-REDEPLOY.md)。当前代码已包含双方案切换、已确认身份同步、话题保护和独立 HA 通知；这些能力需要匹配的主机控制服务和私有配置，并非拉取镜像即可全部启用。

默认镜像入口仍仅运行离线检查，不登录微信；真实启动要求独立 web 配置、启用环境变量和 ALLOW_WEB_LOGIN 标记。

## 固定版本与改动

- 网页通道：Ovler-Young/efb-wechat-slave，提交 `7eeec172bf347ad7eef1c2222fe2048aefc4b59d`。
- Telegram 通道：沿用当前固定提交 `9816e65fa50c93fd5272260aff045a23e4386ed7`，保留通用个性化功能。
- 会话缓存：写入临时文件并刷盘，原子替换；覆盖失败时保留旧文件。损坏的会话文件保留原件，返回重新认证状态。
- 转发投递：移除连接异常时整条消息重发 3 次的旧逻辑，由 Telegram 请求层处理安全的连接重试；响应不确定时不重放消息。
- HTTP 请求：缺省连接/读取超时为 10/60 秒；保留显式超时，不自动重发发送操作。
- 群组：群名不变时仍更新新增成员；群标识映射覆盖失败时保留旧文件。空聊天返回明确的缺失对象。
- 运行环境：非 root、独立配置档案、独立进程锁、独立镜像标签，不包含 ComWechat 通道。

构建产物包含 `/opt/efb-web/packages.txt`，用于核对实际依赖版本。所有基础源和主要依赖固定；构建同时使用系统仓库更新，不能视为逐字节可复现构建。

## 离线检查

在仓库根目录执行：

```sh
docker build -f web-image/Dockerfile -t efb-web-standby:20260927 .
docker run --rm --network none efb-web-standby:20260927
```

该命令使用断网模式。默认入口执行测试后退出，退出不代表微信已登录。

## 旁路由网络

`web-standby` 复用私有清单中指定的既有网络，Compose 所需 `EFB_LAN_NETWORK`、`EFB_DNS`、`EFB_WEB_IPV4` 从仓库外的私有环境文件读取。网关由网络 IPAM 决定，必须核对为用户指定旁路由；设置 DNS 不会改变网关。

上线前检查地址冲突和 DHCP 保留。不要删除或重新创建已有网络，也不要改变其他服务的 IPv6 策略。

该配置没有挂载账号数据，`EFB_WEB_ENABLE_LOGIN=0`，不会启动登录。

## 个性化配置准备

`prepare_profile.py` 只读取来源，输出到全新的独立目录，拒绝覆盖。可复制关键词规则、话题群、图片发送方式、名称显示和夜间静默；生产 Bot Token 不复制。含原通道群标识的规则保存到 `pending-mappings.yaml`，在身份映射完成前不启用。绑定数据库与登录缓存不复制。

独立配置准备仅生成候选；实际同步由控制服务和已确认身份映射管理，不能直接覆盖绑定库。

## 启用与回退

生产启用顺序、数据挂载和凭据管理见部署指南。测试实例不得与当前活动前端同时使用同一机器人接收更新。两套登录缓存独立，不能复制 ComWechat 群 ID 充当网页版 PUID，也不能按同名群直接合并。

实际验收需覆盖扫码、群与话题、附件、断网恢复；离线测试不等于这些项目通过。回退优先恢复先前固定镜像，保留新的绑定和消息去重状态；不要用早期测试数据覆盖生产 profile。

## 群和话题绑定同步准备

`binding_sync.py` 以 SQLite 只读事务导出普通绑定、话题绑定和聊天目录，不读取消息正文或复制消息历史。每次输出必须使用新目录。

```sh
python web-image/binding_sync.py --source-db /readonly/tgdata.db --output /private/new-snapshot
```

首次无网页版联系人目录时，全部绑定留在待映射清单，候选数据库不投入运行。拿到网页版群及联系人 PUID 后，提供 `--web-catalog` 和 `--mapping`：

- 联系人目录为 `[{"uid": "web-puid"}]`。必须来自实际登录的同一微信账号。
- 对应表为 `[{"source": "honus.comwechat native-id", "target": "blueset.wechat web-puid", "confirmed": true}]`。
- 仅确认项转换；目标必须出现在网页版目录，禁止重复或多个原聊天指向同一目标。群名相同不构成身份确认。
- 转换保留原 Telegram 群 ID 和话题 ID；未映射项留在 `plan.json`。
- `binding-candidate.db` 仅为新建候选，绝不自动覆盖现有数据库。正式应用需停止目标实例、检查冲突和目标现有绑定后再合并。

增量同步由主机控制服务定期执行，也可通过 Telegram /sync 和 /backend 操作。只翻译已确认 ID 和实现支持的配置字段；合并规则中的会话列表不能直接复制启用。

### 已确认群绑定的导入

`binding_sync.apply_web_plan(plan, destination, backup)` 只接受 `profiles/web/blueset.telegram/tgdata.db`，导入前创建独立 SQLite 备份，再在事务中合并已确认的绑定。原有话题编号保持不变；若同一网页版群已经产生测试话题，则切回原话题编号，不删除消息历史。目标话题若已指向其他群，整批回滚。重复导入不新增重复记录。

群名只能用于候选匹配。迁移时需核对成员信息或由用户确认；网页版尚未返回的群保留在 `pending` 中，不能伪造网页版 ID，也不能声称已全部同步。

### 本地 Telegram Bot API 附件权限

启用 `local_bot_api` 时，附件目录应按同一路径只读挂载到 `/var/lib/telegram-bot-api`。网页版的 `EFB_WEB_UID`、`EFB_WEB_GID` 应匹配该目录实际所有者，独立网页版数据目录也应由这一身份持有；不要放宽原附件目录权限或改变原 EFB 的所有权。登录入口会检查附件目录的读取和遍历权限，避免文本可用而所有附件失败。

附件验证应使用运行容器内的 Telegram `File.download_to_memory()` 分别读取图片和文档，再由用户验证实际微信接收。不要自动重发先前失败或结果不确定的业务消息。
