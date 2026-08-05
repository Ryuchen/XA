# 兴安电竞（XA）接口文档

> 本文档基于后端真实代码反向归纳，逐接口列出「方法 / 路径 / 请求参数 / 响应字段 / 权限要求」。
> 金额字段统一以「分」为单位（整数），前端展示时需除以 100 转元。

---

## 0. 通用约定

### 0.1 基础前缀

| 端 | 前缀 |
| --- | --- |
| C 端（小程序 / H5） | `/api/` |
| 运营后台（admin-web） | `/api/admin/` |
| Django Admin | `/admin/` |
| WebSocket 实时推送 | `ws://<host>/api/ws/orders/?token=<JWT>` |

### 0.2 认证方式

- 除登录 / 试音免登类接口外，均需在请求头携带 JWT：
  `Authorization: Bearer <access_token>`
- Token 由 SimpleJWT 签发：Access 有效期 **12 小时**，Refresh 有效期 **7 天**。
- C 端权限基于角色：`CUSTOMER`（老板）/ `PROVIDER`（陪玩）/ `OPERATOR`（客服）。
- 后台权限基于「权限点」（如 `order:view`），超级管理员默认拥有全部权限点。

### 0.3 统一响应结构

所有接口返回 HTTP 200，业务状态由 body 内 `code` 表达（少数鉴权失败返回 401/403）：

```json
{ "code": 0, "msg": "success", "data": { } }
```

| code | 含义 |
| --- | --- |
| 0 | 成功 |
| 400 | 参数错误 / 业务校验失败 |
| 403 | 无权限 / 角色不符 |
| 404 | 资源不存在 |
| 409 | 冲突（重复报名、入口未绑定等） |
| 410 | 资源已失效 / 过期（试音链接） |

### 0.4 后台列表 / 分页约定

- 后台所有 ViewSet 列表复用统一分页：查询参数 `?page=&page_size=`（默认 `page_size=10`，最大 100）。
- 列表响应：`{ code, data: { list: [...], total, page, page_size } }`
- 详情响应：`{ code, data: { ...对象字段 } }`
- 创建 / 更新响应：`{ code, data: { ...对象字段 } }`
- 删除响应：`{ code, msg: "deleted" }`

---

## 1. C 端 · 用户与登录（`/api/users/`）

### 1.1 微信登录（仅老板端 · CUSTOMER）
`POST /api/users/wechat-login/` · 权限：AllowAny

> 此端点仅服务老板（CUSTOMER）角色。openid 命中的既有账号若非 CUSTOMER（陪玩/客服/管理员）直接返回 `code=403`；不再接收 `role`/`bindCode`，也不改写既有账号角色。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| code | string | 是 | 微信登录 code |
| phoneCode | string | 否 | 手机号授权 code，传则换取手机号 |
| nickname | string | 否 | 微信昵称（≤50），有则同步落库 |
| avatar | file | 否 | 微信头像文件（multipart），有则落库并回填绝对 URL |

响应 `data`：`token`、`userInfo{id, username, nickname, avatar, role:"customer", backendRole, openid, phone, bindCode:null, bindStatus:"direct"}`。

### 1.2 账号密码登录（仅陪玩端 · PROVIDER）
`POST /api/users/account-login/` · 权限：AllowAny

> 此端点仅服务陪玩（PROVIDER）角色，账号由客服在后台开户。既有账号若非 PROVIDER 返回 `code=403`，账号被禁用返回 `code=403`；不再接收 `role`/`bindCode`。开发期 `WECHAT_MOCK_LOGIN=True` 时按用户名自动建号且不强校验密码，生产环境用 Django `authenticate` 校验真实密码。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| username | string | 是 | 账号 |
| password | string | 否 | 生产环境必填并校验真实密码 |

响应 `data`：`token`、`userInfo{id, username, nickname, role:"provider", backendRole, openid, phone, bindCode:null, bindStatus:"direct"}`（陪玩端不返回 `avatar`）。

### 1.3 当前用户资料
`GET /api/users/me/` · 权限：登录

响应 `data`：`id, username, nickname, role, phone, avatar, game_region, game_nickname, game_uid`。

`PATCH /api/users/me/` · 权限：登录 — 可更新 `nickname`(≤50)、`phone`(≤20)、`game_region`(≤50)、`game_nickname`(≤50)、`game_uid`(≤50)。

### 1.4 陪玩列表（C 端展示）
`GET /api/users/escorts/` · 权限：登录

查询参数：`game`（可选）。仅返回 `AVAILABLE` 且已认证的陪玩。
响应 `data[]`：`id, nickname, avatar, rank, rating, ratingCount, orderCount, winRate, pricePerHour, bio, city, status, is_verified`。

### 1.5 陪玩本人资料
`GET /api/users/escorts/me/` · 权限：PROVIDER
`PATCH /api/users/escorts/me/` · 权限：PROVIDER — 可编辑 `display_name`(≤50，非空)、`bio`(≤500)、`city`(≤50)、`service_area`(≤255)、`gender`(MALE/FEMALE/UNKNOWN)。

响应 `data`：`display_name, gender, bio, city, service_area, price_per_hour, rank_tier, status, is_verified, rating_avg, rating_count, completed_order_count`（价格 / 抽成 / 认证 / 押金等仅展示，由后台维护）。

### 1.6 陪玩在线状态切换
`POST /api/users/escorts/status/` · 权限：PROVIDER

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| status | string | `AVAILABLE`/`BUSY`/`OFFLINE` |

### 1.7 陪玩每周档期
`GET /api/users/escorts/schedule/` · 权限：PROVIDER
`PUT /api/users/escorts/schedule/` · 权限：PROVIDER

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| slots | array | 每项 `{weekday(0-6), start_minute, end_minute}`；时段须为四段制之一：00-06/06-12/12-18/18-24（分钟：0-360/360-720/720-1080/1080-1440）。整表覆盖。 |

### 1.8 陪玩经营统计
`GET /api/users/provider-stats/` · 权限：PROVIDER

响应 `data`：`today_orders, serving, total_completed, pending, rating_avg, rating_count, total_income, completion_rate, income_trend[{date, income}]（近 7 日）, escort_status`。

### 1.9 老板成就馆
`GET /api/users/achievements/` · 权限：登录

响应 `data[]`：`code, title, desc, icon, unlocked, current, target`（按已完成订单数 / 累计消费实时计算解锁）。

### 1.10 老板每月签到（消费型签到）
`GET /api/users/checkin/` · 权限：CUSTOMER（非 BOSS 返回 `code=403`）

响应 `data`：
- 日历：`year, month, days_in_month, today, today_checked, checked_days[], checked_count, continuous_days`
- 奖励：`rewards[{seq, amount, name, description, icon}]`（整月阶梯礼物）、`next_reward`、`next_gift{name, description, icon}`、`balance`
- 消费门槛：`today_spend`（当日已支付额，分）、`daily_spend_required`（日签门槛）、`today_eligible`（是否可签）、`makeup_card_spend_required`（发卡门槛）
- 补签卡：`makeup_cards`（剩余张数）、`max_makeup_cards`、`card_earned_today`、`makeup_available_days[]`（本月今日之前未签的日期）
- 全勤：`full_attendance`（本月是否已获奖）、`full_attendance_reward{name, description, icon, tag_code}`

`POST /api/users/checkin/` · 权限：CUSTOMER — 执行签到/补签并发放钱包奖励。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| day | int | 可选。传入即为**补签**（消耗 1 张补签卡，不校验当日消费）；不传为当日签到（校验 `today_spend >= daily_spend_required`） |

响应 `data`：`reward_amount, gift{name, description, icon}, seq_in_month, checked_count, balance, makeup_cards, full_attendance_awarded`。
错误：重复签到 / 当日消费未达门槛（返回还差金额）/ 补签卡不足 / 补签日期非法 均返回 `code=400`。当月签满时自动发放全勤 Tag 并推送站内消息。

> 门槛、阶梯礼物、补签卡上限、全勤奖名称均由后台配置（`GET|PUT /api/admin/config/checkin`、`/api/admin/checkin-gifts/`）。

### 1.11 生成绑定码（⚠️ 已废弃）
`POST /api/users/bind-code/` · 权限：OPERATOR

> **该端点已废弃，请勿调用。** 绑定码机制已下线：生成的 code 不落库、无任何消费方，登录端点也不再接收/校验 `bindCode`（`/wechat-login/`、`/account-login/` 响应中的 `bindCode` 恒为 `null`、`bindStatus` 恒为 `"direct"`）。陪玩/客服统一由后台开户后用账号密码登录。端点保留仅为兼容历史前端，后续版本将移除。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| role | string | `provider`(前缀 PW) / 其它(前缀 KF) |

响应 `data`：`bindCode`（随机生成、不落库，无实际用途）。

---

## 2. C 端 · 服务与订单（`/api/orders/`）

### 2.1 服务列表
`GET /api/orders/services/` · 权限：登录 — 查询参数 `category`（可选）。
响应 `data[]`：`id, name, description, price, category, cover_url`。

### 2.2 服务详情
`GET /api/orders/services/<service_id>/` · 权限：登录
响应 `data`：`id, name, description, price, cover_url, images, highlights, sales_count, is_favorited`。

### 2.3 服务评价列表
`GET /api/orders/services/<service_id>/evaluations/` · 权限：登录
响应 `data`：`list[评价对象], total, avg_score`（评价对象见 2.11）。

### 2.4 我的收藏
`GET /api/orders/favorites/` · 权限：登录 — 返回收藏的服务列表（同 2.1 结构）。

### 2.5 收藏切换
`POST /api/orders/favorites/toggle/` · 权限：登录

| 参数 | 说明 |
| --- | --- |
| service_id | 服务 ID |

响应 `data`：`favorited`(bool)。

### 2.6 风云榜
`GET /api/orders/rankings/` · 权限：登录 — 查询参数 `period`：`day`/`week`/`month`（默认 month）。
响应 `data`：`period`、`consume_rank[]`（老板消费榜）、`order_rank[]`（陪玩接单榜）；榜项含 `rank, user_id, nickname, avatar, total_amount, order_count, title(爵位)`。

### 2.7 下单
`POST /api/orders/create/` · 权限：CUSTOMER 或 OPERATOR

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| service_id / product_id | int | 是 | 服务 ID（二者其一） |
| provider_id | int | 否 | 指定陪玩下单 |
| user_coupon_id | int | 否 | 使用的优惠券 |
| game_rounds | int | 否 | 局数，默认 1 |
| game_region | string | 否 | 大区 |
| game_nickname | string | 否 | 游戏昵称 |
| game_uid | string | 否 | 游戏 UID |
| remark | string | 否 | 备注（≤255） |

下单即扣款，计价：`amount = 原价×局数 − 老板折扣 − 活动折扣 − 券抵扣`，并按抽成率拆账。响应 `data` 为订单对象（见 2.9）。余额不足返回 `code=400`。

### 2.8 订单列表
`GET /api/orders/orders/` · 权限：登录

查询参数：`status`(`pending`/`grabbed`/`in_progress`/`completed`/`cancelled`/`all`)、`role`(`provider` 强制按陪玩视角)。
数据范围：
- PROVIDER（或 `role=provider`）：`status=pending` 返回**待接单池**——所有 `provider` 为空的 PENDING 订单，供陪玩抢单；其它 `status` 返回自己接的订单。
- OPERATOR：看全部。
- CUSTOMER：看自己下的。

### 2.9 订单对象字段（OrderSerializer）

`id, order_no, status, payment_status, amount, game_rounds, game_region, game_nickname, game_uid, remark, service{…}, customer{id,nickname,phone}, provider{id,nickname}|null, is_evaluated, grabbed_at, in_service_at, completed_at, cancelled_at, refunded_at, cancel_reason, auto_cancel_at, reject_count, created_at, updated_at`。

### 2.10 订单状态流转接口

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 抢单 | `POST /api/orders/orders/<id>/grab/` | PROVIDER | 陪玩须 AVAILABLE，抢后置 BUSY |
| 开始服务 | `POST /api/orders/orders/<id>/start/`（别名 `/start-service/`） | 订单关联陪玩 | GRABBED→IN_SERVICE |
| 完成 | `POST /api/orders/orders/<id>/complete/` | 订单关联陪玩 | 三方入账 + 陪玩恢复 AVAILABLE |
| 取消 | `POST /api/orders/orders/<id>/cancel/` | 订单所属老板 | 仅 PENDING 可取消，全额退款；body 可带 `reason` |
| 拒单 | `POST /api/orders/orders/<id>/reject/` | 订单关联陪玩 | 回 PENDING，`reject_count+1`；body 可带 `reason` |
| 强制退款 | `POST /api/orders/orders/<id>/refund/` | OPERATOR | GRABBED/IN_SERVICE，`reason` 必填 |
| 客服派单 | `POST /api/orders/orders/<id>/assign/` | OPERATOR | body 必填 `provider_id` |

以上成功均返回 `data` 为最新订单对象；状态非法返回 `code=400`。

### 2.11 评价

`POST /api/orders/evaluate/` · 权限：订单所属老板（仅已完成订单，且未评价）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| order_id | int | 订单 ID |
| score | int | 综合分 1-5 |
| skill_score / attitude_score / communication_score | int | 三维分 1-5，缺省回落综合分 |
| content | string | 内容（≤500） |
| is_anonymous | bool | 是否匿名 |

评价对象字段：`id, order, order_no, score, skill_score, attitude_score, communication_score, avg_score, content, is_anonymous, customer_name, customer_avatar, provider_name, service_name, reply_content, replied_at, created_at`。

- 我收到的评价：`GET /api/orders/evaluations/mine/` · 权限：PROVIDER → `list, total, avg_score`。
- 回复评价：`POST /api/orders/evaluations/<evaluation_id>/reply/` · 权限：被评价陪玩 → body `reply_content`。

### 2.12 订单统计
`GET /api/orders/stats/` · 权限：登录 — 按角色范围聚合。
响应 `data`：`total, pending, grabbed, in_service, completed, cancelled`。

---

## 3. C 端 · 钱包（`/api/wallet/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 钱包信息 | `GET /api/wallet/info/` | 登录 | `data.balance` |
| 充值 | `POST /api/wallet/topup/` | 登录 | body `amount`（分，>0），返回 `balance` |
| 收益记录 | `GET /api/wallet/income/` | 登录 | 返回 INCOME/WITHDRAW 流水（最多 50）+ `total_income` |
| 提现配置+记录 | `GET /api/wallet/withdraw/` | 登录 | `balance, frozen_amount, min_amount, requests[]` |
| 提现申请 | `POST /api/wallet/withdraw/` | PROVIDER | 见下 |
| 全部流水 | `GET /api/wallet/transactions/` | 登录 | `tx_type` 筛选 + 分页 |
| 报单 | `GET/POST /api/wallet/reports/` | GET 登录 / POST PROVIDER | 见下 |
| 押金 | `GET/POST /api/wallet/deposit/` | PROVIDER | 见下 |

### 3.1 提现申请（POST）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| amount | int | 提现额（分），须 ≥ 最低门槛且 ≤ 余额 |
| payee_method | string | 收款方式（WithdrawRequest.PayeeMethod） |
| payee_account | string | 收款账号（≤100） |
| payee_name | string | 收款人（≤50） |
| remark | string | 备注（≤255） |

申请时冻结资金（`balance-=amount`、`frozen+=amount`），生成 `WITHDRAW/PENDING` 流水与提现单。
响应 `data`：`balance, frozen_amount, request{…}`。提现单字段：`id, amount, payee_method, payee_method_display, payee_account, payee_name, status, status_display, remark, audit_remark, created_at, audited_at`。

### 3.2 报单（GET/POST）

POST body（multipart 支持图片）：`game_name, description, amount(>0), proof_image(可选)`。
报单对象：`id, game_name, description, amount, proof_image_url, status, status_display, commission_rate, payout_amount, remark, audit_remark, created_at, audited_at`（抽成 / 入账 / 审核结果由后台审核时回写）。

### 3.3 押金（GET/POST）

- GET → `deposit_required, deposit_paid, deposit_remaining, balance`。
- POST body `amount`：用钱包余额缴纳，不超过应缴差额，扣款并累加 `deposit_paid`，记 DEPOSIT 流水。

### 3.4 流水对象字段（income / transactions）

`id, tx_no, amount, tx_type, balance_before, balance_after, status, remark, created_at`。`transactions` 额外返回 `total, page, page_size, balance, frozen_amount`。

---

## 4. C 端 · 优惠券（`/api/coupons/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 可领券列表 | `GET /api/coupons/` | 登录 | 带「当前用户是否已领」标记 |
| 我的券 | `GET /api/coupons/mine/` | 登录 | `?usable=1&amount=<分>` 仅返回满足金额门槛的可用券 |
| 领取 | `POST /api/coupons/<coupon_id>/claim/` | 登录 | 抢光/过期→400，重复领取→400 |

---

## 5. C 端 · 内容位（Banner / 公告 / 站内消息 / 客服名片）

### 5.1 Banner
`GET /api/banners/` · 权限：登录 — 返回启用的轮播图列表。

### 5.2 公告
- 列表：`GET /api/announcements/` · 权限：登录 — `?limit=`（默认 5，最大 20）。
- 详情：`GET /api/announcements/<pk>/` · 权限：登录。

### 5.3 站内消息（`/api/messages/`）

| 接口 | 方法/路径 | 说明 |
| --- | --- | --- |
| 列表 | `GET /api/messages/` | `?type=` 筛选 + 分页；返回 `messages, total, unread, page, page_size` |
| 未读数 | `GET /api/messages/unread-count/` | `data.unread` |
| 全部已读 | `POST /api/messages/read-all/` | 全量置已读 |
| 详情 | `GET /api/messages/<message_id>/` | 读取自动置已读；不存在→404 |

均需登录且仅能访问本人消息。

### 5.4 客服名片
`GET /api/support/contact-card/` · 权限：登录 — 返回当前启用、排序最靠前的一张名片（无则 `data=null`）。

---

## 6. C 端 · 在线客服 IM（`/api/chat/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 会话+历史 | `GET /api/chat/session/` | 登录（OPERATOR 拒绝，走后台工作台） | 自动创建会话，返回 `session{id,last_message,last_message_at,unread_user,created_at}` + `messages[]` |
| 发送 | `POST /api/chat/send/` | 登录（OPERATOR 拒绝） | body `content` 或 `image`（multipart），至少其一；`unread_support+1` 并 WS 推送 |
| 标记已读 | `POST /api/chat/read/` | 登录 | 清零 `unread_user` |

消息对象字段：`id, is_from_support, content_type, content_type_display, content, image_url, is_read, created_at`。

---

## 7. C 端 · 试音招募（`/api/audition/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 落地页信息 | `GET /api/audition/info?token=&role=` | AllowAny | 只读，`role`∈`boss`/`provider`；返回 `title, remark, expire_at, is_active, role`，绝不回传 token |
| 免登换 JWT | `POST /api/audition/exchange` | AllowAny | body `token, role`；凭链接绑定用户换发 JWT，返回结构同登录（`token, userInfo`）；未绑定→409 |
| 提交报名 | `POST /api/audition/signup` | PROVIDER | body `token, contact, game, remark`；同链接同人仅一次（重复→409） |
| 我的报名 | `GET /api/audition/my-signups` | 登录 | 返回本人报名记录：`id, link, link_title, contact, game, remark, status, status_display, audit_remark, audited_at, created_at` |

链接停用返回 410，过期返回 410，不存在返回 404。

---

## 8. WebSocket 实时推送

- 端点：`ws://<host>/api/ws/orders/?token=<JWT>`
- 分组：`user_{id}`（个人）、`operators`（客服）、`providers`（陪玩）
- 事件类型：`order_status_update`（订单状态变更）、`chat_message`（新消息）、`chat_session_update`（会话未读/概览更新）

---

## 9. 后台 · 认证与基础（`/api/admin/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 登录 | `POST /api/admin/auth/login` | AllowAny | body `username, password`；校验为后台用户；返回 `token, refresh, profile` |
| 刷新 | `POST /api/admin/auth/refresh` | AllowAny | body `refresh`；返回新 `token` |
| 我的信息 | `GET /api/admin/auth/profile` | 登录 | 返回 `profile{id, username, nickname, avatar, is_superuser, role{id,name,code}|null, permissions[]}` |
| 仪表盘 | `GET /api/admin/dashboard/stats` | 后台用户 | 用户/订单/收入统计 + `recent_orders` |
| 陪玩看板 | `GET /api/admin/dashboard/player` | `player_dashboard:view` | `?start_date=&end_date=`；返回 `kpi, secondary, rank` |
| 权限树 | `GET /api/admin/permissions` | 后台用户 | 权限点分组树 |
| 抽成率配置 | `GET/PUT /api/admin/config/commission` | GET 后台/PUT `report:audit` | PUT body `rate`(0-100) |
| 提现门槛配置 | `GET/PUT /api/admin/config/withdraw` | GET 后台/PUT `withdraw:audit` | PUT body `min_amount`(分) |

---

## 10. 后台 · 资源型接口（RESTful ViewSet）

以下资源统一支持列表 `GET /api/admin/<resource>/`、详情 `GET .../<id>/`；具备写能力者支持 `POST`（创建）、`PATCH/PUT`（更新）、`DELETE`（删除）。各操作所需权限点如「权限」列所示。常用查询参数在「筛选」列列出。

| 资源 | 路径 | 类型 | 权限（view / 写） | 筛选 |
| --- | --- | --- | --- | --- |
| 用户 | `users` | 只读+改 | `user:view` / `user:edit` | `role, keyword, is_active` |
| 陪玩 | `escorts` | 读写 | `escort:view` / `escort:edit` | `status, is_verified, keyword` |
| 订单 | `orders` | 只读 | `order:view` | `status, payment_status, keyword` |
| 评价 | `evaluations` | 只读 | `evaluation:view` | — |
| 报单 | `reports` | 只读 | `report:view` | `status, keyword` |
| 提现 | `withdrawals` | 只读 | `withdraw:view` | `status, keyword` |
| 钱包 | `wallets` | 只读 | `wallet:view` | `keyword` |
| 充值记录 | `recharge-records` | 只读 | `recharge:view` | `user_id, keyword` |
| 奖惩记录 | `dispose-records` | 只读 | `dispose:view` | `user_id, dispose_type, keyword` |
| 交易流水 | `transactions` | 只读 | `transaction:view` | — |
| 优惠券 | `coupons` | 读写 | `coupon:view` / `coupon:edit`、`coupon:delete` | `is_active` |
| 用户券 | `user-coupons` | 只读 | `coupon:view` | `status, coupon` |
| 公告 | `announcements` | 读写 | `announcement:view` / `announcement:edit`、`announcement:delete` | — |
| Banner | `banners` | 读写 | `banner:view` / `banner:edit`、`banner:delete` | — |
| 成就 | `achievements` | 读写 | `achievement:view` / `achievement:edit`、`achievement:delete` | — |
| 服务项 | `service-items` | 读写 | `service:view` / `service:edit`、`service:delete` | `category` |
| 老板分级 | `boss-types` | 读写 | `boss_type:view` / `boss_type:edit` | — |
| 陪玩等级 | `escort-levels` | 读写 | `escort_level:view` / `escort_level:edit` | — |
| 促销活动 | `promotions` | 读写 | `promotion:view` / `promotion:edit` | — |
| 客服名片 | `support-cards` | 读写 | `support:view` / `support:edit` | — |
| 试音链接 | `audition-links` | 读写 | `audition:view` / `audition:edit` | — |
| 试音报名 | `audition-signups` | 只读 | `audition:signup_view` | `status, link, keyword` |
| 会话 | `chat-sessions` | 只读 | `chat:view` | `keyword, only_unread` |
| 站内消息 | `messages` | 只读 | `message:view` | `type, recipient` |
| 角色 | `roles` | 读写 | `role:view` / `role:edit`、`role:delete` | — |
| 客服账号 | `admins` | 读写 | `admin:view` / `admin:edit` | — |

### 10.1 后台自定义动作（Action）

| 资源 | 动作 | 方法/路径 | 权限 | 请求参数 |
| --- | --- | --- | --- | --- |
| 陪玩 | 认证 | `POST escorts/<id>/verify/` | `escort:verify` | `is_verified`(bool) |
| 陪玩 | 奖励/罚款 | `POST escorts/<id>/dispose/` | `escort:dispose` | `dispose_type`(REWARD/PENALTY), `amount`(分,>0), `reason` |
| 订单 | 状态日志 | `GET orders/<id>/logs/` | `order:view` | — |
| 订单 | 代派单 | `POST orders/dispatch/` | `order:dispatch` | `customer_id, service_id, game_rounds, remark, provider_id(可选)` |
| 订单 | 退款 | `POST orders/<id>/refund/` | `order:refund` | `reason`(必填) |
| 订单 | 取消 | `POST orders/<id>/cancel/` | `order:cancel` | `reason`；仅 PENDING |
| 评价 | 代回复 | `POST evaluations/<id>/reply/` | `evaluation:reply` | `reply_content` |
| 评价 | 删除 | `DELETE evaluations/<id>/` | `evaluation:delete` | 回退陪玩评分聚合 |
| 钱包 | 调账 | `POST wallets/<id>/adjust/` | `wallet:adjust` | `amount`(分,≠0), `remark` |
| 钱包 | 充值 | `POST wallets/recharge/` | `wallet:recharge` | `user_id, amount(>0), gift_amount(≥0), remark` |
| 报单 | 通过 | `POST reports/<id>/approve/` | `report:audit` | 按抽成率入账，落 INCOME 流水 |
| 报单 | 驳回 | `POST reports/<id>/reject/` | `report:audit` | `audit_remark`(必填) |
| 提现 | 通过 | `POST withdrawals/<id>/approve/` | `withdraw:audit` | `frozen-=amount`，流水→SUCCESS |
| 提现 | 驳回 | `POST withdrawals/<id>/reject/` | `withdraw:audit` | `audit_remark`(必填)；`frozen-=amount, balance+=amount`，流水→FAILED |
| 会话 | 消息列表 | `GET chat-sessions/<id>/messages/` | `chat:view` | — |
| 会话 | 回复 | `POST chat-sessions/<id>/reply/` | `chat:reply` | `content` 或 `image`；`unread_user+1`，WS 推送 |
| 会话 | 标记已读 | `POST chat-sessions/<id>/read/` | `chat:reply` | 清零 `unread_support` |
| 试音报名 | 通过 | `POST audition-signups/<id>/approve/` | `audition:signup_audit` | 发站内消息通知 |
| 试音报名 | 驳回 | `POST audition-signups/<id>/reject/` | `audition:signup_audit` | `audit_remark`(必填) |
| 站内消息 | 推送 | `POST messages/push/` | `message:push` | `title, preview, detail, type, broadcast(bool), recipient_ids[]` |

---

## 11. 关键业务规则速记

- **计价拆账**：`amount = original_amount − boss_discount − promo_discount − coupon_discount`；`provider_income + inviter_commission + shop_income = amount`。
- **抽成率优先级**：促销活动 > 陪玩等级 > 商品/全局（默认 20%）。
- **订单状态机**：`PENDING→GRABBED→IN_SERVICE→COMPLETED`；`PENDING/GRABBED/IN_SERVICE→CANCELLED`；`COMPLETED/CANCELLED` 为终态。PENDING 超时 30 分钟自动取消。
- **资金安全**：所有资金动作均在 `transaction.atomic()` + `select_for_update()` 内执行。
- **平台收入**：`shop_income` 归集到平台系统账户 `__platform__`（禁登录）。
- **提现门槛**：默认最低 10000 分（100 元）。
