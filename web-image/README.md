# 独立网页版 EFB 镜像

此镜像用于验证旧网页版通道，默认仅运行离线检查，不登录微信、不连接 Telegram、不挂载生产数据。

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
docker compose -f web-image/compose.yaml --profile offline run --rm web-check
```

`web-check` 使用断网模式。默认入口执行测试后退出，退出不代表微信已登录。

## 旁路由网络

`web-standby` 复用既有 macvlan 网络 `efb2026_efb_lan`，该网络的 IPv4 网关必须为 `192.168.12.2`；不修改该网络。DNS 指定相同旁路由。Docker 内部解析器可能显示为 `127.0.0.11`，上游 DNS 以容器 HostConfig 为准。

网页版地址预设为 `192.168.12.116`。上线前重新检查地址冲突，并确认 DHCP 不会将该地址分配给其他设备。只有网页版容器禁用 IPv6，以确保走指定 IPv4 出口；原服务的 IPv6 策略不变。

该配置没有挂载账号数据，`EFB_WEB_ENABLE_LOGIN=0`，不会启动登录。

## 个性化配置准备

`prepare_profile.py` 只读取来源，输出到全新的独立目录，拒绝覆盖。可复制关键词规则、话题群、图片发送方式、名称显示和夜间静默；生产 Bot Token 不复制。含原通道群标识的规则保存到 `pending-mappings.yaml`，在身份映射完成前不启用。绑定数据库与登录缓存不复制。

独立配置准备完成不代表绑定同步已经实现。

## 后续真实登录测试

真实登录尚未验证。后续需要独立的 `/data/profiles/web`，仅启用 `blueset.wechat`，并同时满足 `EFB_WEB_ENABLE_LOGIN=1` 和独立数据目录下的 `ALLOW_WEB_LOGIN` 标记。

测试不得使用生产 Telegram Bot 的 Token 或生产数据目录。切换控制、两通道绑定转换和配置同步属于后续阶段；当前未接入。不能复制 ComWechat 群 ID 充当网页版 PUID，也不能按同名群直接合并。

上线前需要实际验证：扫码、群文本与附件双向收发、群成员身份、群改名后绑定、断网恢复、仅手机重新登录，以及稳定运行观察。离线测试通过不等于这些项目已通过。

## 回退

本阶段不替换生产镜像、不修改生产 Compose、不重启生产容器。停止独立测试实例即可结束测试；保留独立数据和镜像用于排查。

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

后续增量同步可重复执行导出与规划；当前没有定时同步、双向覆盖或 Telegram 切换按钮。涉及原聊天 ID 的接收策略和合并规则仍需同一对应表适配，不能直接复制启用。

### 已确认群绑定的导入

`binding_sync.apply_web_plan(plan, destination, backup)` 只接受 `profiles/web/blueset.telegram/tgdata.db`，导入前创建独立 SQLite 备份，再在事务中合并已确认的绑定。原有话题编号保持不变；若同一网页版群已经产生测试话题，则切回原话题编号，不删除消息历史。目标话题若已指向其他群，整批回滚。重复导入不新增重复记录。

群名只能用于候选匹配。迁移时需核对成员信息或由用户确认；网页版尚未返回的群保留在 `pending` 中，不能伪造网页版 ID，也不能声称已全部同步。
