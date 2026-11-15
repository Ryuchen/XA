# 声浪电竞 · 老板端五页设计稿

2026-11-11 · 内置 ImageGen 直接生成的手机 H5 静态设计预览。示例门店为声浪电竞，账户、人物、订单、未读数和店币金额均为设计样例。未修改正式前端、后端或部署。

| 页面 | 前端入口 | 首屏用途 |
| --- | --- | --- |
| [首页](01-home.png) | `/home` | 搜索服务、去选陪玩、回看当前服务 |
| [陪玩](02-companions.png) | `/companions` | 按游戏与语音条件找人，查看参考时价和资料 |
| [订单](03-orders.png) | `/orders` | 区分来源、履约状态和实际支付 |
| [消息](04-messages.png) | `/messages` | 查看个人通知、俱乐部动态与客服入口 |
| [我的](05-mine.png) | `/mine` | 核对本店钱包与订单，进入会员、预约等个人服务 |

以奶白、钴蓝与通透声波装置形成一套客户侧视觉。门店域名绑定不变，不含切店、跨店钱包或平台管理。首页、订单、消息和个人中心以同一笔“王者双排”示例演示不同视角：服务中、实付 48 浪花、通知服务已开始，个人中心计 1 笔服务中。陪玩目录中的每小时参考价不是结算报价。

功能来源：[路由](../../fronted/boss/src/App.tsx)、[首页与我的](../../fronted/boss/src/business/account.tsx)、[陪玩目录](../../fronted/boss/src/business/commerce.tsx)、[订单](../../fronted/boss/src/business/orders.tsx)、[消息](../../fronted/boss/src/business/content.tsx)、[PRD](../../docs/prd/02-老板端.md)。[各页生成提示词](PROMPTS.md)。

