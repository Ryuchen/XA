# 兴安电竞（XA）接口文档

> 本文档基于后端真实代码反向归纳，逐接口列出「方法 / 路径 / 请求参数 / 响应字段 / 权限要求」。
>
> **金额单位：所有金额字段一律为「内部账务单位」整数，`10` 内部单位 = `1 兴安币`，
> 前端展示时除以 10。** 后端全链路（钱包余额、订单金额、分账、提现、打赏、签到奖励）
> 都以该整数记账，**永不**在服务端做 ÷10 换算 —— 换算只在展示层做一次。
>
> 早期版本此处误写为「以分为单位、除以 100」。按 100 换算会让所有金额显示为真实值的
> 1/10，且与流水对不上账，如有客户端仍按 100 换算请立即修正。

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
| 409 | 冲突（重复报名、入口未绑定等、**资金接口幂等键重复**） |
| 410 | 资源已失效 / 过期（试音链接） |

> 例外：后台 `POST /api/admin/auth/refresh/` 在 refresh token 失效时返回
> **HTTP 401**（body 内 `code` 同为 401），以便前端拦截器统一跳登录。

### 0.4 订单状态取值约定

订单列表的 `?status=` **只接受下列小写下划线取值**，传入未收录的值直接返回
`code=400`（早期实现会静默忽略并返回全部订单，导致前端拼错状态时拿到错数据）：

| 对外取值 | 含义 | 内部枚举 |
| --- | --- | --- |
| `all` | 不过滤 | — |
| `pending` | 待接单 | `PENDING` |
| `grabbed` | 已接单 | `GRABBED` |
| `in_progress` | 服务中 | `IN_SERVICE` |
| `completed` | 已完成 | `COMPLETED` |
| `cancelled` | 已取消 | `CANCELLED` |

注意 `in_progress`（对外）与 `IN_SERVICE`（内部）不同名，这层映射是刻意保留的，
用于隔离内部枚举改名对客户端的影响。

### 0.5 资金接口幂等约定

下列**出账**接口支持并强烈建议携带 `client_request_id`（字符串，≤64 字符，
建议 UUID hex，一次业务生成一次、重试时复用同一个）：

| 接口 | 说明 |
| --- | --- |
| `POST /api/orders/create/` | 下单扣款 |
| `POST /api/orders/<id>/tip/` | 礼物打赏 |
| `POST /api/wallet/withdraw/` | 提现申请 |
| `POST /api/wallet/deposit/` | 缴纳押金 |

- 携带该字段时：同一账户下同一个 `client_request_id` **只会成功一次**，
  重复请求返回 `code=409`，不会产生第二笔扣款。
- 未携带时：服务端按「账户 + 业务 + 关键参数」派生临时键，做 **60 秒**窗口去重
  （降级保护，仅能挡住弱网重试与连点）。部署侧可通过环境变量
  `REQUIRE_CLIENT_REQUEST_ID=true` 强制要求携带，届时缺失将返回 `code=409`。

### 0.6 限流约定

写操作（非 GET/HEAD/OPTIONS）按场景限流，超限返回 HTTP 429：

| 场景 | 默认速率 | 覆盖的接口 |
| --- | --- | --- |
| `login` | 10 次/分钟 | 微信登录、账号密码登录、后台登录 |
| `order_write` | 30 次/分钟 | 下单、打赏 |
| `wallet_write` | 10 次/分钟 | 提现、押金、通行证购买 |
| `checkin` | 10 次/分钟 | 每日签到 |

读接口不消耗限流配额。

### 0.7 后台列表 / 分页约定

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

---

## 2. C 端 · 服务与订单（`/api/orders/`）

### 2.1 服务列表
`GET /api/orders/services/` · 权限：登录 — 查询参数 `category`（可选）。
响应 `data[]`：`id, name, description, price, category, cover_url`。
服务价格还返回 `unit_price`（每人报价）与 `billing_provider_count`（计价人数），公开 `price` 是两者相乘的套餐价。用户已确认双陪套餐统一每人价 × 2，后续新成交快照保存计价人数；既有已成交订单的总金额和分账不追溯重算。

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
| provider_ids | int[] | 否 | 指定陪玩的业务账号 ID；单陪恰好 1 位、双陪恰好 2 位，不可重复；空数组表示平台安排 |
| provider_id | int | 否 | 兼容旧版的单陪指定字段；新客户端使用 `provider_ids`，混传时必须为同一位单陪 |
| dispatch_mode | string | 否 | `BOSS_SELECT`（默认）为平台报名后老板选择；`AUTO_GRAB` 仅限单陪 |
| user_coupon_id | int | 否 | 使用的优惠券 |
| game_rounds | int | 否 | 局数，默认 1 |
| game_region | string | 否 | 大区 |
| game_nickname | string | 否 | 游戏昵称 |
| game_uid | string | 否 | 游戏 UID |
| remark | string | 否 | 备注（≤255） |

下单即扣款，计价：`amount = 原价×局数 − 老板折扣 − 活动折扣 − 券抵扣`，并按订单的百分比或整单固定抽成快照拆账（见 10.2）。响应 `data` 为订单对象（见 2.9）。余额不足返回 `code=400`。

`POST /api/orders/quote/` 复用相同的选人、资格与计价校验，返回报价及余额是否足够，不扣款。公开商品 `price` 已是整套价格；选中两位陪玩不会再次乘人数，也不会增加套餐价格。

指定双陪时，服务端在同一扣款事务中锁住两位陪玩的派单名额，逐人复核可接状态、性别、游戏/平台/难度/档位资格与容量，写入 `escort_mode=DOUBLE`、两份 `OrderProvider` 分账快照并进入 `GRABBED`，两位均能读取本单。各人等级抽成沿用既有规则；固定金额抽成整单只扣一次再分摊。任一人不满足要求会拦截整单，不能提交一位陪玩的双陪订单。

不指定时，双陪以 `BOSS_SELECT` 进入 `PENDING`，冻结 `required_provider_count=2` 并等待两人组队报名、队友确认和老板选择，不允许单人直接抢单。仅真正配置 `billing_provider_count=2` 的双陪套餐可自助进入该流程；其他咨询服务、附加费用和双陪小时套餐数量限制仍生效。人数和报价在扣款前复核，目录变更后须重新确认。

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
| 抢单/报名 | `POST /api/orders/orders/<id>/grab/` | PROVIDER | `requires_boss_selection=true` 时提交报名，body `team_size`、双人时的 `partner_id`；其它可直抢单通过后接单。状态/资格/名额由服务端复核 |
| 可组队陪玩 | `GET /api/orders/orders/<id>/teammates/` | PROVIDER | 仅双陪待选单返回符合本单资格、通行证与名额条件的队友；单陪不能查询/借此发起组队 |
| 队友确认/撤回 | `POST /api/orders/orders/<id>/applications/<application_id>/action/` | 报名本人/队友 | body `action=CONFIRM_TEAM/WITHDRAW`；确认组队时再次校验人数和资格 |
| 老板选择报名 | `POST /api/orders/orders/<id>/applications/<application_id>/select/` | 订单所属老板 | 报名 ID 在路径中；双人须队友已确认，服务端复核资格并固化各成员分账 |
| 开始服务 | `POST /api/orders/orders/<id>/start/`（别名 `/start-service/`） | 订单关联陪玩 | multipart `entry_image`；GRABBED→IN_SERVICE，双陪需相关成员入队凭证齐全 |
| 完成 | `POST /api/orders/orders/<id>/complete/` | 订单关联陪玩 | multipart `completion_image`；完成履约并释放名额；新订单还须报单审核/售后条件满足后结算，见 3.2 |
| 取消 | `POST /api/orders/orders/<id>/cancel/` | 订单所属老板 | 仅 PENDING 可取消，全额退款；body 可带 `reason` |
| 拒单 | `POST /api/orders/orders/<id>/reject/` | 订单关联陪玩 | 回 PENDING，`reject_count+1`；body 可带 `reason` |
| 强制退款 | `POST /api/orders/orders/<id>/refund/` | OPERATOR | GRABBED/IN_SERVICE，`reason` 必填 |
| 客服派单 | `POST /api/orders/orders/<id>/assign/` | OPERATOR | body 必填 `provider_id` |

以上成功均返回 `data` 为最新订单对象；状态非法返回 `code=400`。
订单列表与详情返回权威 `required_team_size=1/2` 与 `can_team_apply`，取已成交 `required_provider_count` 等人数快照，不能根据当前参与人数、后续修改的商品目录或价格自行猜测。单陪仅 `team_size=1` 且不能携带 `partner_id`；明确双陪仅 `team_size=2` 且须另一名队友，队友确认后等待老板选择。陪玩端仅在 `required_team_size=2 && can_team_apply=true` 时开放队友选择，字段缺失按单人展示并由服务端最终校验。双陪仍由当前客服确认/后台派单流程成交，未新增客户端自助双陪付款入口。

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
| 充值旧入口 | `POST /api/wallet/topup/` | 登录 | 当前固定返回 `code=403`；须联系企业微信客服确认收款，再由后台凭证充值，客户端不能直接改余额 |
| 收益记录 | `GET /api/wallet/income/` | 登录 | 返回 INCOME/WITHDRAW 流水（最多 50）+ `total_income` |
| 提现配置+记录 | `GET /api/wallet/withdraw/` | PROVIDER | `balance, frozen_amount, min_amount, tax_rate, requests[]`；启用俱乐部规则时追加 `balance_threshold, can_withdraw`；可选 `?amount=` 试算，见 3.1 |
| 提现申请 | `POST /api/wallet/withdraw/` | PROVIDER | 见下 |
| 全部流水 | `GET /api/wallet/transactions/` | 登录 | `tx_type` 筛选 + 分页 |
| 奖惩/礼物收益 | `GET /api/wallet/provider-benefits/` | PROVIDER | 全量汇总与分别分页的奖惩、礼物记录；礼物区分预计分成、实际到账与结算状态 |
| 提现账户 | `GET /api/wallet/withdraw-accounts/`；`PUT/DELETE /api/wallet/withdraw-accounts/<payee_method>/` | PROVIDER | 微信、支付宝、银行卡各保存一个，按 ID 与版本校验，见 3.1.2 |
| 报单列表/草稿 | `GET/POST /api/wallet/reports/` | GET 登录 / POST PROVIDER | 查询本人记录；POST 仅以 `order_id` 创建/恢复服务报单草稿，见 3.2 |
| 报单凭证 | `POST /api/wallet/reports/<id>/images/`；`DELETE /api/wallet/reports/<id>/images/<image_id>/` | 本人可编辑报单 | 上传三类截图、移除战绩图片，见 3.2 |
| 报单提交 | `POST /api/wallet/reports/<id>/submit/` | 本人可提交报单 | 完成服务、仍已支付且三类截图齐全后提交审核 |
| 押金 | `GET/POST /api/wallet/deposit/` | PROVIDER | 见下 |

### 3.1 提现配置 / 试算（GET）

`GET /api/wallet/withdraw/` · 权限：PROVIDER

响应 `data`：`balance, frozen_amount, min_amount, tax_rate, requests[]`。
启用俱乐部资金规则时追加 `balance_threshold=5000`（¥50）与 `can_withdraw`，且 `min_amount=0`：申请前可用余额须严格大于该门槛，单次提现金额仍须为正整数且不超过余额。未启用时使用服务端返回的最低单次金额 `min_amount`，客户端不能硬编码两种口径。

| 查询参数 | 类型 | 说明 |
| --- | --- | --- |
| amount | int | **可选**。提现额（内部账务单位）。传入即在上述字段基础上追加 `amount`（原样回显）、`tax_amount`、`actual_amount` |

- **前端不要自己算税。** 试算与 POST 落库调用的是同一个 `compute_withdraw_tax()`，
  因此 `?amount=X` 返回的 `tax_amount` / `actual_amount` 与该笔 `X` 提交后落库的值**逐字段相等**。
  客户端若另算一份，税率或舍入口径一变就会出现「提现前显示到账 12.3 币、记录里却是 12.2 币」。
- 舍入为十进制 **ROUND_HALF_UP**（`.5` 一律进位），不是 JS `Math.round` 也不是 Python 内建 `round`
  的银行家舍入。例：`amount=125`、`tax_rate=2` → `tax_amount=3`、`actual_amount=122`。
- `amount` 非整数 / ≤0 / 含非法字符 → `code=400`「金额非法」；`?amount=`（空串）视为未传，按原响应返回。
- 试算**不校验最低提现额、不校验余额**，也**不产生任何副作用**（不建提现单、不写流水、不占幂等键）。
  准入判断使用同一响应的 `min_amount`、`balance`，以及存在时的 `balance_threshold`，避免逐字输入时反复弹错。POST 会再次校验规则与余额。
- 并发输入时响应可能乱序到达，用回显的 `amount` 丢弃过期响应。

### 3.1.1 提现申请（POST）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| amount | int | 正整数账务值，须 ≥ 返回的 `min_amount` 且 ≤ 余额；存在 `balance_threshold` 时申请前余额还须大于该值。允许非整币（如 `125` = 12.5 兴安币） |
| withdrawal_account_id | int | 当前陪玩绑定的收款账户 ID；现行陪玩端采用此方式 |
| expected_payee_version | int | 本次核对的绑定版本，与账户 ID 一起提交 |
| expected_tax_rate / expected_tax_amount / expected_actual_amount | int | 服务端最近试算的税率与金额，传其中一个时必须三项齐全；不一致返回 `WITHDRAW_QUOTE_CHANGED`，不冻结、不占请求编号 |
| payee_method / payee_account / payee_name | string | 旧客户端手填兼容：方式为 `WECHAT/ALIPAY/BANK`、账号 ≤100 字、姓名 ≤50 字；不能与绑定账户参数混用 |
| remark | string | 备注（≤255） |
| client_request_id | string | 可选但强烈建议，见 0.5 幂等约定 |

申请时冻结资金（`balance-=amount`、`frozen+=amount`），生成 `WITHDRAW/PENDING` 流水与提现单。
税额在申请时按当时税率快照落库：`tax_amount = ROUND_HALF_UP(amount × tax_rate / 100)`、`actual_amount = amount - tax_amount`；冻结与扣款仍是全额 `amount`，税只影响实际打款金额。
响应 `data`：`balance, frozen_amount, request{…}`。提现单字段：`id, amount, tax_rate, tax_amount, actual_amount, payee_method, payee_method_display, payee_account, payee_name, status, status_display, remark, audit_remark, payout_reference, paid_at, created_at, audited_at`。
绑定失效或版本变化返回 `errorCode=PAYEE_CHANGED`，要求重新核对；申请会保存独立收款快照，之后修改/解绑账户不改写历史申请。相同请求编号的已成功回执可恢复，不能以超时或余额变化推断上次未申请。

### 3.1.2 保存的提现账户

- GET 列表返回本人账户数组，字段：`id, payee_method, payee_account, payee_name, version, updated_at`；服务端返回完整资料，列表掩码由客户端展示，编辑和提现确认时供本人核对。
- PUT `/withdraw-accounts/<payee_method>/` body：`payee_account, payee_name, expected_id, expected_version`。首次绑定传两个 `0`；修改传最近读取的 ID/版本。每名陪玩每种方式最多一条。
- DELETE 同路径 body：`expected_id, expected_version`，解绑不影响已提交申请。旧页 ID/版本不匹配时返回 `code=409, errorCode=PAYEE_CHANGED`，不能覆盖重新绑定的账户。

### 3.2 订单关联报单、凭证与审核

| 方法/路径（均在 `/api/wallet/` 下） | 请求 | 返回/约束 |
| --- | --- | --- |
| `GET reports/` | — | 当前账号自己的报单数组，包含草稿、待审核、已通过与已驳回记录 |
| `POST reports/` | JSON `order_id` | 本人已接单/服务中/已完成的服务订单，返回创建或恢复的 `DRAFT` 草稿；同一订单/陪玩不重复建报单。礼物、订单打赏及转单后无结算份额者不能新建服务报单 |
| `POST reports/<id>/images/` | multipart `image` 文件、`kind=ENTRY/COMPLETION/RESULT` | 入队/结单各一张（再次上传替换）；战绩图片追加，最多九张；须为有效图片且报单可编辑，返回最新报单对象 |
| `DELETE reports/<id>/images/<image_id>/` | — | 仅移除本人可编辑报单的战绩图片引用，图片不存在返回 `code=404`；原文件由媒体保留策略处理 |
| `POST reports/<id>/submit/` | 无须提交金额或抽成 | 服务订单须 `COMPLETED + PAID`，且有入队、结单和至少一张战绩截图；成功变为 `PENDING`、更新 `submitted_at` 并清空旧驳回备注 |

创建草稿时，服务名称/备注、本人结算基数与抽成从关联订单和本人分账快照生成，客户端不以 `game_name/amount/proof_image` 新建手工报单。开始/完成服务上传的入队/结单截图会同步到该成员报单。三类凭证均通过权限签发的私有 URL 展示，查看过期图片应续签，不能把文件路径当公开素材。

报单对象字段：

| 字段组 | 字段与含义 |
| --- | --- |
| 标识/服务 | `id, order_id, order_no, service_name, fulfillment, game_name, description` |
| 金额/抽成 | `amount` 为本人结算基数；`expected_income` 为当前服务端收益快照；`commission_type=PERCENT/FIXED`、`commission_rate`、`commission_fixed`、`payout_amount`。金额均为内部账务单位；`FIXED` 的 `commission_rate=0` 不能解释为零抽成，须用固定抽成和收益快照展示 |
| 凭证 | `proof_image_url`（历史兼容）、`entry_image_url, completion_image_url, result_image_urls[]`、`result_images[{id,url}]`（用于准确删除对应图片） |
| 审核 | `status=DRAFT/PENDING/APPROVED/REJECTED, status_display, remark, audit_remark, created_at, audited_at, submitted_at` |
| 履约/结算 | `order_status, settled_at`；`APPROVED` 仅表示凭证通过，`settled_at` 才表明订单分账已执行，不能仅凭审核状态当作可提现余额 |
| 操作能力 | `can_edit, can_submit, edit_block_reason`；仅草稿/驳回且关联订单仍已付、未停止流程、本人仍有必要报单份额时可编辑。`can_submit` 另要求订单完成；该字段不代表截图已齐全，提交仍校验三类凭证 |

驳回后可补图并重提；取消、退款、回待接单池以及本人已退出且无结算份额时停止编辑与提交。后台列表排除 `DRAFT`；审核权限为 `report:audit`，只允许待审核报单通过/驳回，驳回必须有 `audit_remark`。后台对象另返回 `suggested_commission_rate, commission_source_label, commission_type, commission_fixed, expected_income, settlement_mode, settled_at`；关联订单采用本人已经保存的分账快照，不能在审核时将固定抽成重新套成百分比。

当前结算条件与实销确认使用两个时点：新 `REPORT_APPROVAL` 服务订单在必要成员全部报单通过、订单仍已付且已完成、完成时间起 72 小时售后窗口结束、无 `OPEN` 客诉时一次性结算；必要成员包含仍参与的成员和已退出但保留正数份额的成员。实销/会员确认另从最后一份有效报单提交起等待 72 小时；驳回会清除有效报单时点，重提重新计算。结算后的收益还可能依现有俱乐部规则抵扣押金/冻结，在钱包流水和可用/冻结余额中核对。历史 `COMPLETION` 订单保留原结算模式，报单审核不能重复入账。

历史无关联订单的手工报单只保留查询与兼容审核：后台按历史报单/有效等级/全局规则解析百分比抽成，审核后保存收益快照及流水。该兼容路径不是当前客户端的新建报单入口。

### 3.3 押金（GET/POST）

- GET → `deposit_required, deposit_paid, deposit_remaining, balance, deposit_mode, deposit_percent, hold_hours`。
- 现金模式 POST body `amount, client_request_id`：用钱包余额缴纳，不超过应缴差额，扣款并累加 `deposit_paid`，记陪玩 `DEPOSIT` 与平台 `DEPOSIT_INCOME` 流水。`EARNINGS` 模式拒绝手动缴纳，由订单净收益依服务端配置抵扣押金。

### 3.4 流水对象字段（income / transactions）

`id, tx_no, amount, tx_type, balance_before, balance_after, status, remark, created_at`。`transactions` 额外返回 `total_income, total, page, page_size, balance, frozen_amount`；`total_income` 来自成功的正数订单收益流水，不能把待结算礼物预估收入计入实得。

`provider-benefits/` 查询 `dispose_page, gift_page, page_size`（默认各第 1 页、每页 20，最多 100）。响应 `summary{total_reward,total_penalty,total_gift_amount,total_gift_income,gift_count}` 为全量汇总，`dispose_records[]`、`gift_records[]` 各自分页，并返回 `pagination{page_size,dispose{page,total},gift{page,total}}`。礼物记录的 `provider_income` 是本人预期分成，`received_income` 是成功收益流水实际到账，`settlement_status=SETTLED/PENDING/CHECK_REQUIRED` 用于区分已结算、待结算与待核对；礼物及订单打赏不走客户端服务报单入口。

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
| 订单默认抽成配置 | `GET/PUT /api/admin/config/commission` | GET 后台/PUT `report:audit` | `commission_type=PERCENT/FIXED`；百分比用 `rate`(0-100)，固定用 `commission_fixed`（内部金额整数）；见 10.2 |
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
| 报单 | 通过 | `POST reports/<id>/approve/` | `report:audit` | 关联订单按本人分账快照通过凭证；必要成员全通过且售后窗结束/无未结客诉后一次结算，见 3.2；历史无订单报单保留兼容入账 |
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

### 10.2 订单百分比与整单固定抽成

金额接口使用内部账务整数，`10` 内部单位 = `1` 兴安币。后台填写固定金额时显示兴安币，支持 1 位小数；例如填 `20` 币，接口提交 `commission_fixed: 200`。百分比为 `0–100` 整数；固定金额 `0` 明确表示不抽成。

服务项新增 `commission_override` 开关及 `commission_type=PERCENT/FIXED`、`commission_fixed`。开关关闭时沿用原档位、体验及等级规则；开启后用该商品的比例或固定金额覆盖抽成，不改变服务价格或接单资格。活动同样支持两种模式，仍须满足原活动适用范围。全局切换固定金额保留旧 `rate`，历史无订单手工报单继续使用该比例；礼物沿用独立规则。

`GET /api/admin/orders/dispatch-quote/` 与 `POST /api/admin/orders/dispatch/` 支持顶层 `commission_type=AUTO/PERCENT/FIXED`。`AUTO` 沿用配置；`FIXED` 仅提交一次顶层 `commission_fixed`（也支持同值别名 `commission_fixed_total`），不要在两位陪玩中重复填写。指定陪玩采用百分比时保留各 `providers[].commission_rate`，单陪未指定人时可用顶层 `commission_rate`。资格、人数、余额及预期成交金额仍由服务端校验。

试算返回 `commission_type, commission_rate, commission_fixed_total, commission_source_label, commission_locked`；固定模式另返回 `commission_fixed_shares` 和 `provider_shares`（每人结算基数、抽成、预计收益）。实际固定抽成以折后实付封顶，双陪按结算份额分摊并处理整数尾差，局数增加不重复收费。例如双陪两局实付 `200` 币、整单固定 `20` 币，两人各分摊 `10` 币、各得 `90` 币。

显式派单覆盖优先；适用的固定抽成活动可覆盖商品配置。其余按商品自定义及原自动规则解析，最终以服务端试算为准。创建订单时保存抽成模式、固定总额、来源及各人分账快照，后续配置修改不追溯旧单。转单只拆分已有收益；退款按实际入账比例冲正，任一方可追回资金不足时整笔拦截。旧版 LEGACY/AUTO 的逐人固定抽成请求保留原合同，与新整单固定模式分开处理。

## 11. 关键业务规则速记

- **计价拆账**：`amount = original_amount − boss_discount − promo_discount − coupon_discount`；`provider_income + inviter_commission + shop_income = amount`。
- **抽成方式**：百分比或整单固定金额，配置作用范围、试算和快照见 10.2；未开启新覆盖时保留原规则。
- **订单状态机**：`PENDING→GRABBED→IN_SERVICE→COMPLETED`；`PENDING/GRABBED/IN_SERVICE→CANCELLED`；`COMPLETED/CANCELLED` 为终态。PENDING 超时 30 分钟自动取消。
- **资金安全**：所有资金动作均在 `transaction.atomic()` + `select_for_update()` 内执行。
- **平台收入**：`shop_income` 归集到平台系统账户 `__platform__`（禁登录）。
- **提现门槛**：默认最低 10000 分（100 元）。
