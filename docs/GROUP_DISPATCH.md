# 一键发单与打手单钱

腾讯云新版支持在后台配置 KOOK 机器人、多个频道路由及每个频道的文案，优先使用 [后台 KOOK 操作说明](KOOK_ADMIN_SETUP.md)。下方环境变量方式适用于没有后台配置记录的旧部署。

管理后台 → 派单管理 → 待接单订单的「一键发单」，可以预览、复制派单信息，或同时发送到 KOOK 频道、企业微信群。快捷派单不指定打手时，创建订单后也会打开此窗口。

活动折扣按成交价重新抽成。例如商品原价 100 元，活动成交价 80 元，抽成 20%，打手实得 64 元，俱乐部留存 16 元（如有推荐分佣，则从平台抽成中继续分配）。发单文案显示 `打手单钱：640 兴安币（¥64.00）`，不包含原价、老板实付、折扣、抽成或内部备注。打手订单与报单页也只显示个人实际收益。金额取订单创建时冻结的收入，不随后续商品、活动或抽成配置变化。

快捷派单的预计扣款由后端计算老板折扣和活动折扣。双陪在成交价内分配每人的结算基数，再分别抽成；不能将双陪合计收入当作某一位打手的单钱。双陪继续直接指定两名打手，不进入公开抢单群。

## 配置渠道

在后端环境中设置以下配置，重新启动 Web 和 Celery worker。Docker 使用仓库根 `.env`，正式编排使用 `.env.release`；直接启动 Python 时需要导入环境变量，Django 不会自动读取 `.env` 文件。

```dotenv
KOOK_ENABLED=true
KOOK_BOT_TOKEN=填写机器人Token
KOOK_DISPATCH_CHANNEL_ID=填写文字频道ID

WECOM_DISPATCH_ENABLED=true
WECOM_DISPATCH_WEBHOOK=填写群机器人完整Webhook
```

机器人需具备目标频道/群的发消息权限。企微配置必须是官方 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…` 地址；后端不接受任意 URL，也不把完整地址返回前端或写入发送记录。该群机器人用于向打手发单，与老板端的企微客服接待入口分别配置。

默认在「新订单待接单」和「打手拒单回池」时自动推送。若只需要人工点击发单，可关闭自动触发：

```dotenv
KOOK_DISPATCH_NEW_ORDERS=false
KOOK_DISPATCH_REJECTED_ORDERS=false
WECOM_DISPATCH_NEW_ORDERS=false
WECOM_DISPATCH_REJECTED_ORDERS=false
```

自动与人工发单共用同一轮次的投递记录，重复点击不会重复发群。渠道未配置时，页面仍可复制文案。复制和群消息只发布订单信息，真正接单仍由打手端或管理后台完成。

## 状态与失败处理

- 只发送已支付、未指定打手、未过期的待接单单陪订单；发送任务执行前再次检查订单状态和轮次。
- 每个渠道分别显示未发送、已入队、发送中、已发送、发送失败、结果待核对或已跳过；一个渠道失败不会回滚订单或另一个渠道的结果。
- 明确拒绝或任务入队失败可人工重试。网络超时、响应无法确认时不自动重发，需先核对群内消息。进程在发送中异常退出时可能保留「发送中」，同样应先核对，不能直接当作未发送。
- Webhook 和 KOOK 均不能提供端到端恰好一次投递保证；KOOK 的 `nonce` 只回传，不是上游去重键。群消息具有时效性，请以系统接单状态为准。
- 已发送文案保存在发送记录的 `content` 中。正式 Web 和 worker 应使用同一套渠道配置和数据库；本地未配置真实机器人时不会发送真实群消息。

协议参考：[KOOK 频道消息接口](https://developer.kookapp.cn/doc/http/message)、[腾讯云 CloudBase 企微群机器人示例](https://docs.cloudbase.net/recipes/connect-wecom-webhook-cloud-function)、[企微群机器人说明](https://developer.work.weixin.qq.com/document/path/91770)。
