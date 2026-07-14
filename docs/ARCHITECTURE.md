# XA 项目结构与业务边界

本文件是代码目录的导航，不替代 [PRD](./PRD.md)、[接口文档](./API.md) 或 [ER 图](./ER.md)。

## 三端职责

| 端 | 目录 | 主要职责 | 后端入口 |
| --- | --- | --- | --- |
| 老板端 | `apps/boss-miniapp/` | 登录、浏览服务、下单、评价、钱包、营销、客服 | `/api/users/`、`/api/orders/`、`/api/wallet/` 等 |
| 陪玩端 | `apps/provider-miniapp/` | 抢单、服务流转、报单、提现、资料与每周档期 | `/api/users/escorts/*`、`/api/orders/`、`/api/wallet/` |
| 运营后台 | `apps/admin-web/` | 调度、审核、内容和 RBAC 运营 | `/api/admin/` |

## 后端模块边界

| 模块 | 责任 |
| --- | --- |
| `orders/` | 服务商品、订单、状态机、计价、结算、评价和 WS 订单通知 |
| `wallet/` | 钱包、资金流水、充值记录、提现、报单、押金与运行时资金配置 |
| `users/` | 登录、身份资料、陪玩资料/档期、签到与成就 |
| `console/` | 后台 RBAC、运营 CRUD、审核和看板 |
| `coupons/`、`promotions/` | 营销权益和下单折扣 |
| `chat/`、`site_messages/`、`support/` | 客服会话、站内消息和客服名片 |
| `announcements/`、`banners/`、`audition/` | 内容运营和试音活动 |

## 不可跨越的业务规则

- 金额以分存储、计算，前端只负责格式化为元。
- 所有订单状态修改必须通过 `orders.state_machine.transition`；金额分账通过 `orders.pricing` 与 `orders.settlement`。
- 钱包变更必须同时创建 `Transaction`，并在事务与行锁内完成。
- 前端不复刻计价与结算；以订单下单接口落库的快照为准。
- `archive/legacy_backup/` 不能被当前应用导入；需要迁移其中能力时，应先重写到对应现行端和加测试。
- 试音分享链接必须配置为两个 H5 基址：`AUDITION_BOSS_LINK_BASE_URL` 指向老板端，`AUDITION_PROVIDER_LINK_BASE_URL` 指向陪玩端；不能共用老板端页面。

## 验证基线

后端在 `services/backend/` 执行 `./venv/bin/python manage.py test`。前端分别执行对应的生产构建；构建产物不提交到版本库。
