# HA 通知接入

`control/notification_ha.py` 在独立服务中订阅 HA 的 `call_service` 实时事件，仅接收配置中指定通知实体的 `notify.send_message` 文本调用。它不读取 Telegram 更新、不修改 HA 自动化、不调用通知服务，也不回放历史。

现有 Telegram 通知与微信通知独立处理。该事件表示 HA 已调用通知动作，不代表 Telegram 已成功送达。微信仍通过现有通知队列执行绑定核对、限速、有效期、普通聊天优先和结果不确定时不重试。

## 部署

- 在独立 Python 虚拟环境安装 `control/requirements-ha-source.txt`。
- 将脚本与 `notification_queue.py`、`protocol.py` 放在同一代码目录。
- 设置 `EFB_NOTICE_ROOT` 和 `EFB_NOTICE_BINDINGS`，复用已部署的通知目录和只读绑定查询路径。
- 通知目录中的 `ha-source.json` 配置 `url`、`token`、`entity`。凭据文件仅服务用户可读，权限 `0600`，不提交仓库。
- `sources.json` 仅配置 `ha` 来源密钥；`policy.json` 中仅开启 `ha_ying` 路由，指向经过确认的原有英英绑定。
- HA 接入使用已有内网连接，不需要新增入站监听端口。生产服务以非 root 身份运行，限制 CPU 和内存，凭据与日志均不包含在镜像中。

## 验证与关闭

`ha-status.json` 的 `connected=true` 表示认证和事件订阅成功；心跳约 20 秒更新一次。此状态不能代替微信收件确认。通知结果以 `queue.sqlite` 的状态为准。

仅有新产生、时间不超过 30 秒、目标恰好等于配置实体的事件才会入队。多目标、其他收件人、间接目标、非文本、过期事件均不转发。重复事件使用确定性编号去重。连接中断采用退避重连，不补拉历史。

在机器人私聊使用 `/notify` 关闭通知即可恢复原先只有 Telegram 推送的行为，无需重启微信。也可停止 `efb-ha-notification-source.service`。已经发起的发送不能撤销；关闭后的待发送项由队列取消。该适配仅用于当前网页版，切换后通知队列暂停或取消，不会自行切换登录方案。

部署前保存通知配置和 SQLite 一致性备份，以及现有 HA 自动化的只读副本。回退不覆盖绑定和聊天记录。
