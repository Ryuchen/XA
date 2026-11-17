# KOOK 与微信客服开通指引


## 先准备什么

| 项目 | 需要有权限的人 | 最终要交接的配置 |
| --- | --- | --- |
| KOOK | 俱乐部 KOOK 服务器管理员、机器人开发者账号负责人 | Bot Token、派单文字频道 ID；提醒角色 ID 可选 |


本文已核对当前源码与 KOOK 官方公开文档。微信官方文档本次未能通过工具打开，因此下面的企微操作入口按功能名称给出，实际菜单、主体资格与验证要求以管理员登录后的提示为准，尚未替用户开通或验证账号。

## 一、开通 KOOK 派单机器人

1. 用俱乐部负责人的 KOOK 账号登录 [KOOK 开发者中心](https://developer.kookapp.cn/)，按平台要求完成开发者资料，创建机器人，可命名为“兴安电竞派单助手”。
2. 在机器人配置中取得 **Bot Token / 机器人 Token**。只把 Token 本身保存到私有配置，XA 会自动加上 HTTP 的 `Bot` 鉴权前缀。不要填 OAuth Client Secret 或 Verify Token。鉴权方式见 [官方 API 说明](https://developer.kookapp.cn/doc/reference)。
3. 通过机器人提供的邀请入口，将它加入俱乐部服务器。先建一个专门的“派单联调”**文字频道**，只让参与验收的人员和机器人可见；允许机器人查看频道、发送消息。
4. KOOK 客户端进入“用户设置 → 高级设置”，打开“开发者模式”；右键该文字频道，选择“复制 ID”。这就是 `KOOK_DISPATCH_CHANNEL_ID`，不是服务器 ID 或邀请链接。操作见 [KOOK 官方帮助](https://help.kookapp.cn/71dc/e7b6)。
5. 如需提醒陪玩身份组，在服务器的角色权限中复制角色 ID，检查该身份组能否被机器人提及；填入可选的 `KOOK_DISPATCH_MENTION_ROLE_IDS`，多个 ID 用英文逗号分隔。首次联调可留空。
6. 将配置交接给部署人员，确认测试频道后再启用并发送测试订单。发送接口见 [KOOK 频道消息文档](https://developer.kookapp.cn/doc/http/message)。

对应部署配置如下，尖括号内容需替换，不能原样使用：

```dotenv
KOOK_ENABLED=false
KOOK_BOT_TOKEN=<机器人 Token>
KOOK_DISPATCH_CHANNEL_ID=<测试文字频道 ID>
KOOK_DISPATCH_NEW_ORDERS=true
KOOK_DISPATCH_REJECTED_ORDERS=true
KOOK_DISPATCH_MENTION_ROLE_IDS=
KOOK_ADMIN_ORDER_URL=
KOOK_REQUEST_TIMEOUT=8
```

先保留 `KOOK_ENABLED=false`，目标频道与接收人确认后改为 `true`。正式环境填服务器 `.env.release`；可选后台链接在公网管理端部署后填实际 `/orders` 地址，不能向远程用户发送本机 `127.0.0.1` 链接。内置库模板见 [release.env.example](../../../deploy/release.env.example)，托管库模板见 [managed.env.example](../../../deploy/managed.env.example)。修改后需重新创建 Web、Worker、Beat 容器，使它们读取同一份配置。

**本机已有的容量测试和业务回归要求外部派单关闭。** 开通后另做受控的真实消息联调，不直接打开当前回归环境的自动广播开关再跑批量测试。若先在 Windows 保存，可放在 Git 忽略的 `.tools/external-integrations.local.env`，交接文件路径即可；这个新文件不会自动被部署脚本读取，接入环境仍需单独配置。

当前发送的是已支付、未过期、未指派的单陪待接订单及符合条件的拒单回池订单。已指派、双陪、礼物订单不会广播到抢单频道。陪玩收到通知后仍在陪玩 H5 抢单。KOOK 内按钮抢单、账号绑定和群指令尚未实现，因此本次无需填写公网事件回调地址、Verify Token 或 Encrypt Key。

## 三、企业微信群派单是可选项

只有还希望在**企业微信群**里自动发派单消息时，才需要给目标群添加群机器人，保存完整 Webhook，并配置 `WECOM_DISPATCH_WEBHOOK` 和 `WECOM_DISPATCH_ENABLED`。微信客服名片不依赖此项，Webhook 不能填进 `wecom_service_url`。本项目相关规则见 [群派单说明](../../development/topics/grab/GROUP_DISPATCH.md)。

## 四、开通后怎样验收

| 操作 | 应看到的结果 |
| --- | --- |
| 指定测试频道，生成一笔符合广播条件的已支付订单 | KOOK 收到一张派单卡片；XA 发送记录显示成功 |
| 陪玩 H5 抢单、履约、报单 | 状态同步，KOOK 通知不代替服务端的抢单资格与并发校验 |
| 陪玩拒单回池 | 符合条件时通知新轮次，同一轮次不重复发送 |
| 打开已配置的微信客服入口 | 真机进入预定客服账号；指定接待人员能收发咨询 |
| 订单详情联系客服 | 客服收到正确订单上下文，不串到其他名片 |

先确认允许发送的测试频道，再执行真实消息验收。外部发送结果未知时先核查频道，不直接重复点击重试。

（微信小程序相关内容已按用户 2026-11-17 决定删除，git 历史可追溯。）
