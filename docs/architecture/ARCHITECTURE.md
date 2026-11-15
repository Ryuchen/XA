# XA 项目结构与业务边界

本文件是代码目录的导航，不替代 [PRD](../PRD.md)、[接口文档](../API.md) 或 [ER 图](./ER.md)。

2026-11-04 起系统按门店隔离运行：中央目录记录名称、入口、状态与数据库别名，店内所有业务模型使用各店独立数据库；默认库保留原业务及中央目录。HTTP、WebSocket、Celery、缓存、文件与事务共享并校验同一门店上下文。具体部署与验证见[门店与多租户](../ops/multitenancy-20261104.md)。

## 三端职责

| 端 | 目录 | 主要职责 | 后端入口 |
| --- | --- | --- | --- |
| 老板端 | `fronted/boss/` | 手机/桌面Web，微信快捷登录、服务、下单、评价、钱包、营销、客服 | `/api/users/`、`/api/orders/`、`/api/wallet/` 等 |
| 陪玩端 | `fronted/provider/` | 手机/桌面Web，抢单、服务流转、报单、提现、资料与每周档期 | `/api/users/escorts/*`、`/api/orders/`、`/api/wallet/` |
| 运营后台 | `fronted/admin/` | 桌面/横屏平板，调度、审核、内容、核算、RBAC及获授权的平台门店管理 | `/api/admin/`、`/api/stores/` |

三端由官方shadcn脚手架建立，采用React和Tailwind CSS 4；`fronted/shared` 提供门店、会话、请求和实时连接，`fronted/wechat-bridge` 只提供微信与手机号授权桥接。`apps/` 为历史实现与迁移依据。

## 后端模块边界

| 模块 | 责任 |
| --- | --- |
| `stores/` | 中央门店目录、独立平台授权、上下文、SQL/事务路由、缓存/任务/频道/媒体隔离及初始化升级命令 |
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
