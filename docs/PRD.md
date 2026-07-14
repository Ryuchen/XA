# 兴安电竞（XA）游戏陪玩点单系统 · 产品需求文档（PRD）

> 版本：v1.0　│　文档性质：现状梳理（按代码实现反向归纳）　│　最后更新：2026-06-30
>
> 说明：本 PRD 依据仓库现有实现（backend / frontend / admin-web）整理而成，用于记录系统的产品形态、角色职责、业务流程与功能边界，作为后续迭代与验收的基线。**全系统金额一律以「分」为单位存储与计算。**

---

## 1. 产品概述

### 1.1 产品定位
兴安电竞是一套面向**游戏陪玩俱乐部**的 SaaS 点单与运营系统，对标耀驰云途智管系统。它把「老板（下单方）下单 → 陪玩（接单方）接单服务 → 客服（运营）调度与结算」的完整业务闭环搬到线上，覆盖交易、钱包资金、营销、客服 IM、招募试音、后台运营等全链路。

### 1.2 业务模式
- **C 端（微信小程序 / H5）**：三角色共用一套小程序，按登录身份呈现不同界面与能力。
- **运营后台（Web）**：俱乐部运营人员管理订单、用户、陪玩、资金、营销与内容。
- **盈利方式**：平台对每笔订单按抽成率抽佣；支持老板分级折扣、促销活动、优惠券等营销手段；陪玩收益经提现审核后结算。

### 1.3 目标用户（角色）
| 角色 | C 端 role | 后端 Role | 职责 |
|------|-----------|-----------|------|
| 老板 | `customer` | `CUSTOMER` | 浏览服务、下单、付款、评价、签到、领券 |
| 陪玩（大神/打手） | `provider` | `PROVIDER` | 接单/拒单、提供服务、设置档期、报单、提现、缴押金 |
| 客服（运营） | `support` | `OPERATOR` | 派单、生成绑定码、IM 接待、试音招募 |
| 平台管理员 | — | `ADMIN` | 后台全功能（通过 RBAC 权限点控制） |

> 另有系统内置账户 `__platform__`（`PLATFORM_SYSTEM_USERNAME`），禁止登录，用于归集平台留存收入（`shop_income`）。

---

## 2. 系统架构

### 2.1 总体结构
单仓多端（monorepo），三个独立部署单元 + 共用 MySQL/Redis：

```
XA/
├── services/backend/         Django 6 + DRF + Channels（HTTP API + WebSocket）
├── apps/boss-miniapp/        Taro 4 + React 18（C 端小程序，可编译 weapp / H5）
├── apps/provider-miniapp/    Taro 4 + React 18（陪玩端小程序 / H5）
└── apps/admin-web/           Vue 3 + Vite + Element Plus（运营后台）
```

### 2.2 技术栈
| 层 | 技术 |
|----|------|
| 后端 | Django 6.0 · DRF 3.15 · SimpleJWT · Channels 4 + channels-redis · Daphne(ASGI) · Celery 5 · PyMySQL · Pillow |
| 数据/中间件 | MySQL 8.0（库 `xa_db`）· Redis 6（Channel Layer / Celery broker） |
| C 端 | Taro 4.1.9 · React 18 · TypeScript · Sass · Zustand · dayjs |
| 后台 | Vue 3.5 · Vite 5 · Element Plus 2.8 · Pinia · Vue Router 4 · ECharts 6 · Axios |

### 2.3 接口与通信约定
- **REST 前缀**：C 端业务接口挂在 `/api/<module>/`；运营后台接口挂在 `/api/admin/`。
- **统一响应体**：`{ code, msg, data }`，`code=0` 表示成功，业务错误用 `code=400/403` 等。
- **鉴权**：JWT Bearer Token，Access 有效期 12 小时、Refresh 7 天。
- **实时通信**：WebSocket 端点 `ws://<host>/api/ws/orders/?token=<JWT>`，按用户分组推送（`user_{id}` / `operators` / `providers`），用于订单状态变更与客服 IM 实时消息。

### 2.4 登录与身份
- **微信登录**（`/api/users/wechat-login/`）：小程序端用 code 换 openid 建号/登录，可附带手机号授权 code 回填手机号。
- **账号密码登录**（`/api/users/account-login/`）：H5/浏览器端使用，走 Django 真实密码校验。
- **Mock 开关**：`WECHAT_MOCK_LOGIN` 在未配置微信 AppID/Secret 时自动为真，开发期免真实微信调用。
- **角色绑定码**：陪玩/客服登录须携带 `bindCode`（由客服在 C 端 `/api/users/bind-code/` 生成，前缀 `PW`=陪玩 / `KF`=客服）。

---

## 3. 角色功能需求

### 3.1 老板端（customer）
| 功能模块 | 说明 |
|----------|------|
| 首页 | Banner 轮播、下单入口、公告、礼物/成就/优惠券专区、消费榜与接单榜 |
| 服务浏览 | 服务分类列表、服务详情（轮播图、局数选择、卖点、评价、收藏） |
| 选陪玩 | 大神列表（排序/智能匹配），查看评分、段位、时价 |
| 下单结算 | 填写游戏账号资料、局数、优惠券、备注，余额支付 |
| 订单管理 | 订单列表（玩家侧），取消、评价订单 |
| 钱包 | 余额、充值、资金流水 |
| 每月签到 | 签到日历 + 阶梯奖励（奖励入钱包） |
| 优惠券 | 领券中心、我的券包（未用/已用/过期） |
| 收藏 | 收藏的服务 |
| 成就馆 | 按已完成订单数/累计消费实时解锁 |
| 账号设置 | 基本资料 + 常用游戏资料维护 |

### 3.2 陪玩端（provider）
| 功能模块 | 说明 |
|----------|------|
| 工作台 | 上/下线开关、收益统计、7 日收益趋势、派单处理（接/拒/开始/完成） |
| 个人资料 | 编辑展示昵称/性别/城市/擅长/简介；只读认证、时价、抽成等 |
| 评价管理 | 查看收到的评价并回复 |
| 档期设置 | 每周循环可接单时段（四段制：00-06/06-12/12-18/18-24） |
| 报单 | 登记平台外完成的订单 + 上传凭证，审核通过后按抽成入账 |
| 押金 | 查看应缴/已缴，用余额补缴差额 |
| 试音报名 | 凭试音链接报名，查看报名审核结果 |
| 提现 | 发起提现申请，等待后台审核打款 |

### 3.3 客服端（support）
| 功能模块 | 说明 |
|----------|------|
| 工作台 | 工单队列（待派单/处理中/售后/完成），派单给陪玩、完成订单 |
| 绑定码 | 生成陪玩/客服绑定码 |
| 老板信息 | 查看老板联系方式以便服务 |
| 在线客服 IM | 与用户实时文字/图片沟通（移动端在小程序，后台亦有工作台） |
| 试音招募 | 通过后台生成招募/试音链接、审核报名 |

---

## 4. 核心业务流程

### 4.1 订单状态机
```
PENDING(待接单) ──grab──▶ GRABBED(已接单) ──start──▶ IN_SERVICE(服务中) ──complete──▶ COMPLETED(已完成)
     │                          │                                                         
     ├──assign(客服派单)────────┘                                                         
     │                                                                                    
     └──cancel / auto_cancel(超时) ──▶ CANCELLED(已取消)        COMPLETED/CANCELLED 为终态，不可再转换
```
- **接单方式**：陪玩主动抢单（`grab`）或客服派单（`assign`）。
- **拒单**：陪玩可拒单（`reject`），累计拒单次数记录在 `reject_count`。
- **超时取消**：未接订单到 `auto_cancel_at` 自动取消（`AUTO_CANCEL`）。
- **退款**：已支付订单可由后台退款（`refund`，支付状态转 `REFUNDED`）。
- **审计**：每次状态变更写入 `OrderStatusLog`（动作、前后状态、操作人、原因）。

### 4.2 计价与拆账（下单时落库快照）
**实付金额**：
```
amount = original_amount(原价：单价 × 局数)
       − boss_discount(老板分级 VIP 折扣)
       − promo_discount(命中促销活动折扣)
       − coupon_discount(优惠券抵扣)
```
**收益拆分**：
```
provider_income(陪玩实得) + inviter_commission(推荐人分佣) + shop_income(平台/店铺留存) = amount
```
- **抽成率来源优先级**：促销活动 `commission_rate` > 陪玩等级 `EscortLevel.commission_rate` > 服务商品 `ServiceItem.commission_rate` > 全局 `SystemConfig`（默认 `PLATFORM_COMMISSION_RATE=20`%）。
- **促销与优惠券**：命中折扣率型促销活动时与优惠券互斥。
- **推荐人分佣**：老板可绑定推荐人（`inviter`），按其订单平台抽成部分计提（`inviter_commission_rate`）。

### 4.3 资金流转
- **充值**：客服后台为老板充值，可附带赠送额（实充记 `TOPUP`、赠送记 `GIFT`）。
- **支付**：下单从老板钱包扣款（`PAY`）。
- **收益入账**：订单完成 / 报单通过，陪玩得 `INCOME`，平台留存进系统账户（`SHOP_INCOME`）。
- **提现**：陪玩发起 → 冻结资金 + PENDING 流水 → 后台审核通过（扣冻结、`WITHDRAW` 转 SUCCESS）/驳回（解冻退款、转 FAILED）。最低提现金额 `MIN_WITHDRAW_AMOUNT`（默认 10000 分=100 元）。
- **押金 / 奖罚**：押金（`DEPOSIT`）；后台对陪玩奖励（`REWARD`）/罚款（`PENALTY`）直接增减钱包余额。
- **流水记账**：每笔 `Transaction` 记录 `balance_before/after`，保证可追溯。

### 4.4 招募试音流程
客服后台生成 `AuditionLink`（含 `boss_token` / `provider_token` 两类免登录入口）→ 访客凭 token 换发 JWT 登录态（`/api/audition/exchange`）→ 陪玩提交报名（`AuditionSignup`）→ 客服后台审核通过/驳回。

---

## 5. 数据模型概览

### 5.1 用户与陪玩（users）
- **CustomUser**：扩展 Django User，含 `role`、`openid`、`phone`、推荐人体系（`inviter`/`inviter_commission_rate`）、老板分级（`boss_type`/`boss_no`）、登录与查看开关。
- **EscortProfile**：陪玩资料（展示名/性别/简介/城市/时价/段位/等级/状态/认证/押金/评分/完成单数），与用户一对一。
- **EscortLevel**：陪玩等级（对应平台抽成率）。
- **BossType**：老板分级（对应下单折扣率）。
- **EscortSchedule**：陪玩每周循环档期（weekday + 分钟时段）。
- **CheckinRecord**：老板每月签到记录（含本月序号与奖励额）。
- **Achievement**：成就配置（按完成订单数/累计消费维度，目标值解锁）。

### 5.2 订单与服务（orders）
- **ServiceItem**：服务商品（陪玩服务 / 礼品套餐两类，价格/抽成率/封面/图集/卖点）。
- **ServiceFavorite**：老板收藏。
- **Order**：订单（含计价明细、拆账字段、游戏账号资料、状态/支付状态、各时间节点快照）。
- **OrderStatusLog**：订单状态变更审计日志。
- **Evaluation**：订单评价（综合分 + 技术/态度/沟通三维分、回复、匿名）。

### 5.3 钱包与资金（wallet）
- **Wallet**：用户钱包（余额/冻结/累计充值/累计赠送）。
- **Transaction**：资金流水（9 种类型：充值/支付/收益/提现/奖励/赠送/罚款/押金/平台收入）。
- **SystemConfig**：运行时键值配置（平台抽成率、最低提现额）。
- **ProviderReport**：陪玩报单（平台外订单登记 + 审核入账）。
- **WithdrawRequest**：提现申请（微信/支付宝/银行卡）。
- **RechargeRecord**：充值记录（实充 + 赠送）。
- **DisposeRecord**：奖励/罚款记录。

### 5.4 营销与内容
- **Coupon / UserCoupon**（coupons）：优惠券模板（满减/无门槛）与用户领取记录。
- **Promotion**（promotions）：促销活动（折扣率 + 抽成覆盖，适用范围全场/分类/指定商品，时间窗 + 优先级）。
- **Banner**（banners）：首页轮播（750×320，支持跳转商品/公告/外链）。
- **Announcement**（announcements）：公告（置顶/排序/启停）。
- **Message**（site_messages）：站内消息（系统/订单/客服/活动）。

### 5.5 客服与试音
- **ChatSession / ChatMessage**（chat）：客服会话与消息（文本/图片，双向未读计数）。
- **SupportContactCard**（support）：客服名片（微信号/二维码/提示语）。
- **AuditionLink / AuditionSignup**（audition）：试音链接与报名。

### 5.6 后台权限（console）
- **AdminRole**：后台角色（持有权限点 code 集合）。
- **AdminMembership**：管理员身份（用户 ↔ 角色）。

---

## 6. 运营后台功能需求

> 权限模型：RBAC。后台账号绑定角色，角色持有权限点集合（如 `order:view`、`order:dispatch`）。路由守卫校验 `meta.perm`，页面内 `auth.hasPerm()` 控制按钮级权限；超管 `is_superuser` 放行全部。

### 6.1 工作台 / 数据看板
- **仪表盘**：用户/老板/陪玩/订单总量、今日订单、订单总额与实付额 KPI、订单状态分布、最近订单。
- **陪玩数据看板**：按日期区间统计接单额/接单量、游戏单与礼物单男女占比、押金/罚款/奖励/钱包等二级 KPI、接单额/收入/接单量 Top10 榜。

### 6.2 订单与服务
- **订单管理**：筛选、详情与状态流水、取消、退款、客服快捷派单。
- **评价管理**：评价列表、回复、删除（回退陪玩评分统计）。
- **陪玩报单审核**：通过（按抽成入账）/驳回，配置全局抽成率。
- **服务/礼物单**：服务商品 CRUD（含分类/抽成率/上下架）。

### 6.3 用户与陪玩
- **用户管理**：编辑昵称/角色/老板分级/编号/推荐人及分佣/登录与查看开关。
- **陪玩管理**：资料与等级/押金编辑、认证、奖罚。

### 6.4 财务
- **钱包管理**：余额查看、后台充值（含赠送）、调账。
- **充值记录 / 奖罚记录 / 交易流水**：只读流水查询。
- **提现审核**：通过（解冻打款）/驳回（退还冻结），配置最低提现额。

### 6.5 营销与内容
- 优惠券、促销活动、老板分级、陪玩等级、成就配置、首页轮播、公告管理（均为 CRUD）。

### 6.6 客服与试音
- **客服名片**、**试音链接**、**试音报名审核**、**在线客服 IM 工作台**、**站内消息**（定向/全员广播推送）。

### 6.7 系统管理
- **角色权限**：角色 CRUD + 权限点树勾选。
- **客服信息管理**：后台账号 CRUD（超管账号受保护）。

---

## 7. 主要 API 一览（C 端）

| 模块 | 端点（前缀 `/api/`） | 说明 |
|------|----------------------|------|
| 登录 | `users/wechat-login/`、`users/account-login/`、`users/bind-code/` | 登录与绑定码 |
| 用户 | `users/me/`、`users/achievements/`、`users/checkin/` | 资料/成就/签到 |
| 陪玩 | `users/escorts/`、`users/escorts/me/`、`users/escorts/status/`、`users/escorts/schedule/`、`users/provider-stats/` | 大神列表/资料/状态/档期/统计 |
| 服务 | `orders/services/`、`orders/services/{id}/`、`orders/services/{id}/evaluations/` | 服务列表/详情/评价 |
| 收藏/榜单 | `orders/favorites/`、`orders/favorites/toggle/`、`orders/rankings/` | 收藏与排行榜 |
| 订单 | `orders/create/`、`orders/orders/`、`orders/orders/{id}/{grab,start,complete,cancel,reject,refund,assign}/`、`orders/stats/` | 下单与流转 |
| 评价 | `orders/evaluate/`、`orders/evaluations/mine/`、`orders/evaluations/{id}/reply/` | 评价与回复 |
| 钱包 | `wallet/info/`、`wallet/topup/`、`wallet/income/`、`wallet/withdraw/`、`wallet/transactions/`、`wallet/reports/`、`wallet/deposit/` | 钱包/提现/报单/押金 |
| 优惠券 | `coupons/`、`coupons/mine/`、`coupons/{id}/claim/` | 领券中心 |
| 内容 | `banners/`、`announcements/`、`announcements/{id}/`、`messages/`、`messages/unread-count/`、`messages/read-all/` | Banner/公告/消息 |
| 客服 | `chat/session/`、`chat/send/`、`chat/read/`、`support/contact-card/` | IM 与名片 |
| 试音 | `audition/info`、`audition/exchange`、`audition/signup`、`audition/my-signups` | 试音招募 |

> 运营后台接口统一在 `/api/admin/` 下，由 DRF Router 提供 RESTful 资源（users/escorts/orders/wallets/coupons/promotions/... 共 27 个资源集）+ `auth/*`、`dashboard/*`、`config/*`、`permissions` 等专用端点。

---

## 8. 关键业务规则与约束

1. **金额单位**：全系统金额统一为「分」（整数），展示层除 100 转元。
2. **状态终态保护**：订单进入 `COMPLETED` / `CANCELLED` 后不可再流转。
3. **抽成优先级**：活动 > 陪玩等级 > 商品/全局，下单时固化为快照。
4. **促销互斥**：折扣率型促销与优惠券不可叠加。
5. **提现门槛**：低于 `MIN_WITHDRAW_AMOUNT`（默认 100 元）不可提现；提现走"冻结—审核—打款/退还"两阶段。
6. **快照落库**：订单下单时落库服务名、单价、手机号、陪玩名、计价与拆账明细，避免后续数据变更影响历史订单。
7. **会话唯一**：每个非客服用户与客服团队共享唯一会话池（`ChatSession` 一对一）。
8. **审计可追溯**：订单状态变更、资金流水均留痕。

---

## 9. 非功能性需求

- **实时性**：订单状态与客服消息通过 WebSocket 秒级推送；C 端断线后由 REST 拉取兜底。
- **并发安全**：钱包余额变更使用 `select_for_update` 行锁 + 事务，防止超扣/重复入账。
- **数据一致性**：资金相关写操作在 `transaction.atomic()` 内完成。
- **可配置**：抽成率、最低提现额等运营参数支持后台动态调整（`SystemConfig`）。
- **跨端兼容**：C 端基于 Taro，一套代码编译微信小程序与 H5。

---

## 10. 本地运行与测试账号

### 10.1 启动方式
| 端 | 命令（节选） | 端口 |
|----|--------------|------|
| 后端 | `daphne -b 127.0.0.1 -p 8000 config.asgi:application` | 8000 |
| C 端 H5 | `npm run dev:h5` | 10087 |
| 运营后台 | `npm run dev` | 5180 |
| 依赖 | MySQL 8.0 / Redis 6 | 3306 / 6379 |

> 后端根路径 `/` 无路由（纯 API 服务），直接访问返回 404 属正常；业务入口为 C 端 H5 与运营后台，数据查看可用 Django Admin（`/admin/`）。

### 10.2 测试账号（密码均为 `test1234`）
| 角色 | 账号 | 登录绑定码 |
|------|------|-----------|
| 老板（CUSTOMER） | `boss` | 无需 |
| 陪玩（PROVIDER） | `provider` | 任意非空（如 `PWTEST01`） |
| 客服（OPERATOR） | `support` | 任意非空（如 `KFTEST01`） |
| 超级管理员 | `admin` | 后台登录 |

### 10.3 全阶段批量联调账号

执行 `python manage.py seed_stage_test_data` 可安全刷新以下固定前缀的测试数据；脚本只清理
`demo_boss_*`、`demo_provider_*` 与 `demo_stage_operator`，不会删除其他账号。

| 角色 | 账号范围 | 密码 | 说明 |
|------|----------|------|------|
| 老板 | `demo_boss_01` ~ `demo_boss_20` | `test1234` | 每人含待接单、已接单、服务中、已完成、已取消等订单 |
| 打手 | `demo_provider_01` ~ `demo_provider_20` | `test1234` | 覆盖通行证、报单、提现、奖惩、评价回复与钱包流水 |
| 可抢单打手 | `demo_provider_01` ~ `demo_provider_05` | `test1234` | 状态为 AVAILABLE，可直接验证抢单池 |

默认生成 120 笔订单：待接单 20、已接单 20、服务中 20、已完成 40、已取消 20；
同时生成四档通行证与无卡账号各 4 个、四种报单状态各 5 条，以及提现、消息和资金流水参考数据。

---

## 附录：模块清单

- **后端 App（12）**：users、orders、wallet、coupons、promotions、banners、announcements、site_messages、chat、support、audition、console。
- **C 端页面（29）**：login、home、category、chat、mine、productDetail、players、checkout、orderList、customer/wallet、customer/checkin、coupon、coupon/mine、favorite、settings、providerCenter、providerProfile、providerEvaluations、providerSchedule、report、deposit、auditionSignups、supportCenter、serviceCard、customerService、audition、announcement、announcementDetail、messageDetail。
- **后台页面（29）**：dashboard、player-dashboard、order、evaluation、report、service、user、escort、wallet、recharge、dispose、transaction、withdraw、coupon、promotion、boss-type、escort-level、achievement、banner、announcement、support、audition、audition/signups、chat、message、system/role、system/admin、login、error/403。
</content>
</invoke>
