# XA 接口文档

> **2026-11-13 门店创建接口更新**：统一后台平台超级管理员调用 `POST /api/admin/platform/store-management` 创建新店时，必须明确提交 `entity_prefix`（2–4 位英文字母或数字，不区分输入大小写，服务端转为大写且全平台唯一）。它是业务编号前缀，与 `provider_name_prefix` 陪玩展示简称分别填写；服务端不再从 slug 推导。字段校验发生在建库之前，已存在的门店及编号不修改。其他门店创建、独立数据库与首次营业流程以当前接口为准。

> **首版门店与认证口径**：老板端和陪玩端分别使用 `sl.esports.club`、`xa.esports.club` 所属门店域名，后台使用 `admin.esports.club`。门店身份以域名或统一后台已验证的登录身份为准，查询参数和请求头不能切换已绑定门店；JWT 和刷新令牌绑定门店。产品要求三端支持账号密码加 OTP 或短信验证，绑定微信后可使用微信快捷登录；各入口的实现状态以开发记录为准，微信和短信真实通道尚待申请及联调。钱包和订单使用整数内部账务单位：`10` 内部单位为 `1` 店币，`100` 内部单位为人民币 `1` 元；薪资、投流和经营成本的 `*_fen` 字段为人民币分。以下较早记录中与本条冲突的入口、认证和品牌口径仅供追溯。

> **试音新录音提交**：`POST /api/orders/orders/<order_id>/applications/recording/` 可直接上传录音并报名；队友确认或补录使用 `POST /api/orders/orders/<order_id>/applications/<application_id>/recording/`。两者使用 multipart，字段、失败恢复与旧录音选择见[试音报名接口说明](../../development/topics/fulfillment/试音报名直接提交录音.md)。代码尚未最终联调。

> **本店企微群派单配置**：`GET /api/admin/config/wecom-dispatch/` 读取配置状态；`PUT` 提交 `revision`、`enabled`、`auto_new_orders`、`auto_rejected_orders`，可选填写 `webhook` 或 `clear_webhook: true`。读取须有 `dispatch_config:view` 或 `dispatch_config:edit`，修改须有 `dispatch_config:edit`；启用须有相应 License。响应只包含地址是否已保存及配置状态，不返回 Webhook。版本冲突需重新读取后提交，开业前可保存禁用草稿。当前每店支持一个企微群机器人，详见[配置记录](../../development/topics/store-init/门店企微群派单配置.md)；接口及真实渠道尚待最终联调。

> **退押金申请**：陪玩通过 `GET/POST /api/wallet/deposit-applications/` 查询或提交，未处理申请可调用 `POST /api/wallet/deposit-applications/<id>/cancel/` 取消。客服通过 `GET /api/admin/deposit-applications/` 筛选待办，并调用 `POST /api/admin/deposit-applications/<id>/process/` 办理或 `POST /api/admin/deposit-applications/<id>/reject/` 拒绝。申请仅登记意向，办理时再次核对业务结清及七天等待期；字段与分页见[退押金申请说明](../../development/topics/deposit/陪玩退押金申请.md)。代码及迁移尚待最终联调。

> **KOOK 积分配置**：`GET/PUT /api/admin/config/kook-points/` 读取或设置本店服务器、签到频道、每日签到积分和启停状态；读取需 `engagement:view`，修改需 `engagement:edit`，启用还需本店 CHECKIN License。在线积分规则先随 PUT 提交 `online_draft_window_ms`（累计毫秒数）和 `online_draft_points`（积分数）保存草稿；另一次 PUT 提交 `publish_online_draft_revision` 发布。每次 PUT 均应带当前配置的 `revision`、`enabled`、`guild_id` 和 `checkin_channel_id`；版本冲突返回 409，需刷新后操作。读取返回 `online_rule_draft` 和 `online_rule_active`；草稿保存不等于生效，发布结果包含实际 `effective_at`。同一草稿重复发布返回原版本。`GET /api/admin/kook-points-status/` 返回监听状态；规则发布不代表已连接。接口和计分代码尚未最终联调，也未执行其所需结构迁移。

> **预约业务周期**：预约列表和详情新增 `viewer_business_cycle_status`（`CURRENT`、`PREVIOUS`、`UNKNOWN` 或后台 `NOT_APPLICABLE`）以及 `viewer_participation_status`；旧周期、无法核实周期或已被替换的本人历史席位只读，不返回可操作项。双陪邀请被拒绝或超时后，本人仍可查看受限历史摘要。实际写入时后端仍重新核验，字段与适用范围见[预约历史与本人操作周期](../../development/topics/reservation/预约历史与本人操作周期.md)；尚待最终联调。

> **双陪预约重试**：`POST /api/orders/reservations/<reservation_id>/replace-provider/` 支持可选的 `client_request_id`；客户端应在同一操作重试时复用该编号。相同请求仅恢复仍有效的原邀请或原确认，失效的邀请不会被编号重试恢复；不传编号时，已失效后可重新邀请同一陪玩。陪玩重复提交本人有效确认时返回既有结果，不延长确认或付款截止。详情见[替换邀请重试](../../development/topics/reservation/双陪预约替换邀请重试.md)和[本人确认重试](../../development/topics/reservation/双陪预约确认重试.md)；尚待最终联调。

> **客服登记续单**：具有订单查看及客服编辑权限的人员可调用 `POST /api/admin/orders/<续单ID>/continuation/`，提交 `original_order_id` 和 `client_request_id`。同一关联已有申请时返回原申请；登记不等于核实，仍须通过后台续单核实接口处理。订单和参与人按所属门店及业务周期校验，重试须复用请求编号。接口及奖励结算尚待最终联调。

> **离职结清与重入查询**：有 `salary:view` 权限的后台人员可调用 `GET /api/admin/payroll/separation-settlements/?kind=settlements` 查询结清记录，默认返回待重入项；支持按员工、状态和游标筛选。重新入职仍使用既有授权操作。具体字段见[结清与重入查询说明](../../development/topics/payroll/离职结清与重新入职查询.md)，尚待最终联调。

门店接口使用既有 `{code, data, msg}` 信封：

| 方法与路径 | 权限与行为 |
| --- | --- |
| `GET /api/stores/current/` | 当前营业门店的信息、三端入口和当前账户的平台门店管理权限；不公开全店目录或连接凭据 |
| `GET /api/stores/manage/` | 默认门店内已显式获得平台授权的后台账户；返回 `results` 和 `available_database_aliases` |
| `POST /api/stores/manage/` | 同上；登记 `slug/name/database_alias/boss_url/provider_url/admin_url`，返回201及 `PROVISIONING`；不会假装已建库营业 |
| `PATCH /api/stores/manage/<id>/` | 修改名称、入口及启停状态；不能修改标识或数据库；未初始化不可营业，平台所属默认门店不可从网页停用 |

WebSocket 使用所属门店域名及 `/api/ws/orders/?token=<JWT>`，服务端校验令牌所属门店并隔离频道；查询参数不能覆盖已绑定的门店身份。微信网页授权及手机号绑定以实际接入能力和当前认证规则为准。

> 本文档基于后端真实代码反向归纳，逐接口列出「方法 / 路径 / 请求参数 / 响应字段 / 权限要求」。
>
> **金额单位：订单、钱包等店币金额字段为「内部账务单位」整数，`10` 内部单位 = `1 店币`，
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
| C 端（Web / H5） | `/api/` |
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
服务价格返回 `unit_price`（配置的单位价格）与 `billing_provider_count`（计价倍率），公开 `price` 为两者相乘后的套餐价。`fulfillment_provider_count` 表示实际需要的陪玩人数：双陪可以配置按人计价（倍率 2），也可以配置包含两人的整单价（倍率 1），不能根据计价倍率推断单陪或双陪。本字段和对应下单链路已开始接入，须完成门店结构初始化和最终联调后使用。

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

排行榜需本店 `ACHIEVEMENTS_RANKINGS` 授权。授权不支持时返回 HTTP 403；控制库不可用且没有可验证授权缓存时返回 HTTP 503。个人接单统计和订单履约不受此项限制。
响应 `data`：`period`、`consume_rank[]`（老板消费榜）、`order_rank[]`（陪玩接单榜）；榜项含 `rank, user_id, nickname, avatar, total_amount, order_count, title(爵位)`。
陪玩接单榜项另含 `display_name`，使用本店陪玩简称前缀；`nickname` 保留原昵称。代码尚待最终联调。
`order_rank_complete` 为布尔值：`false` 表示所选周期内有已结束履约的陪玩缺少可核对的本人结束或转出事实，返回的接单榜仅包含已核实部分，不能视为完整排名。双陪按本人首次提交结单截图的时间归属，同人同单按最后一次本人结束或转出计一次；退款不抹除已履约事实。

服务中转入的陪玩在订单详情读取本人 `my_pending_work_stint.id`，通过 `POST /api/orders/orders/{order_id}/participation-start/` 以 multipart 提交 `work_stint_id`、本人 `entry_image` 和可选 `client_request_id` 确认本次入队。系统核对当前席位、本人身份和订单状态；未入队不得完成，重试同一请求返回原结果。原价逐段功能启用时仍使用原有 `segment_id` 入口契约。

客服对已纳入独立原价记录的老板指定单陪执行 `POST /api/admin/orders/{order_id}/transfer/` 时，`transfers[]` 的每一项须包含 `original_share: {"retained": 4000, "transferred": 6000, "agreement": "客服确认原价份额"}`：前两项为内部账务单位的非负整数，10 内部单位为 1 店币，必须是 10 的整数倍且合计等于该席位转出前的原价份额；依据须为 3–500 字。未实际开始的原陪玩保留份额必须为零。缺少、超出或不守恒时整笔转单回滚。该独立统计当前仅覆盖无开始前转单的老板指定单陪普通店币单；双陪、加购、部分退款及其他支付来源的排序原价仍需各自可核对凭据，不能视为已完整接入。

### 2.7 下单
`POST /api/orders/create/` · 权限：CUSTOMER 或 OPERATOR

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| service_id / product_id | int | 是 | 服务 ID（二者其一） |
| provider_ids | int[] | 否 | 指定陪玩的业务账号 ID；单陪恰好 1 位、双陪恰好 2 位，不可重复；空数组表示平台安排 |
| provider_id | int | 否 | 兼容旧版的单陪指定字段；新客户端使用 `provider_ids`，混传时必须为同一位单陪 |
| dispatch_mode | string | 否 | 未指定陪玩时默认 `AUTO_GRAB`，单陪或双陪按门店抢单规则分配；主动招募试音使用 `BOSS_SELECT`。指定陪玩未传模式时使用指定流程；可用性受对应功能门禁控制。 |
| user_coupon_id | int | 否 | 使用的优惠券 |
| game_rounds | int | 否 | 局数，默认 1 |
| game_region | string | 否 | 大区 |
| game_nickname | string | 否 | 游戏昵称 |
| game_uid | string | 否 | 游戏 UID |
| remark | string | 否 | 备注（≤255） |

下单即扣款。会员折扣、门店活动折扣和折扣券属于比例优惠，每单最多选一种；每单最多使用一张券，金额券可与会员或门店活动折扣叠加，符合条件的订单还可按规则使用积分。响应 `data` 为订单对象（见 2.9）；余额不足返回 `code=400`。实际支付来源及复杂退款组合仍受相应功能门禁控制。

`POST /api/orders/quote/` 复用相同的选人、资格与计价校验，返回报价及余额是否足够，不扣款。公开商品 `price` 已是整套价格；选中两位陪玩不会再次乘人数，也不会增加套餐价格。

指定双陪时，服务端在同一扣款事务中锁住两位陪玩的派单名额，逐人复核可接状态、性别、游戏/平台/难度/档位资格与容量，写入 `escort_mode=DOUBLE`、两份 `OrderProvider` 分账快照并进入 `GRABBED`，两位均能读取本单。各人等级抽成沿用既有规则；固定金额抽成整单只扣一次再分摊。任一人不满足要求会拦截整单，不能提交一位陪玩的双陪订单。

双陪人数根据服务配置的 `fulfillment_provider_count=2` 确定，计费倍率可以是 1 或 2；订单保存 `required_provider_count=2` 快照，不允许单人抢得双陪订单。人数和报价在扣款前复核，目录变更后须重新确认。普通未指定订单应进入抢单池、双陪需完整组队；招募试音使用单独选人流程。相关路由及功能开关仍需最终联调，不能将此说明视为全路径已开放。

### 2.8 订单列表
`GET /api/orders/orders/` · 权限：登录

查询参数：`status`(`pending`/`grabbed`/`in_progress`/`completed`/`cancelled`/`all`)、`role`(`provider` 强制按陪玩视角)。
数据范围：
- PROVIDER（或 `role=provider`）：`status=pending` 返回**待接单池**——所有 `provider` 为空的 PENDING 订单，供陪玩抢单；其它 `status` 返回自己接的订单。
- OPERATOR：看全部。
- CUSTOMER：看自己下的。

### 2.9 订单对象字段（OrderSerializer）

`id, order_no, status, payment_status, amount, game_rounds, game_region, game_nickname, game_uid, remark, service{…}, customer{id,nickname,phone}, provider{id,nickname}|null, is_evaluated, grabbed_at, in_service_at, completed_at, cancelled_at, refunded_at, cancel_reason, auto_cancel_at, reject_count, created_at, updated_at`。

正式抢单订单还返回 `pending_deadline_expired`：`false` 表示当前可信时钟确定未截止，`true` 表示确定已截止，`null` 表示时钟证据或截止边界尚不确定。客户端不能把 `null` 当作未截止；操作以 `allowed_actions` 和服务端实际收件、仲裁结果为准。暂定 `auto_cancel_at` 不能替代已激活轮次的真实截止时间。

### 2.10 订单状态流转接口

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 单陪抢单／试音报名 | `POST /api/orders/orders/<id>/grab/` | PROVIDER | 抢单池单陪提交候选后等待本轮排序，不能把提交成功当作已接单；试音单使用相应报名流程。状态、资格和名额由服务端复核 |
| 双陪抢单组队 | `POST /api/orders/orders/<id>/grab-team/`、`POST /api/orders/grab-team/invitations/<team_id>/<action>/` | PROVIDER | 发起邀请及确认／退出；首轮须双方完成组队后进入排序，实际操作名称及结果以接口返回为准 |
| 可组队陪玩 | `GET /api/orders/orders/<id>/teammates/` | PROVIDER | 仅双陪待选单返回符合本单资格、通行证与名额条件的队友；单陪不能查询/借此发起组队 |
| 队友确认/撤回 | `POST /api/orders/orders/<id>/applications/<application_id>/action/` | 报名本人/队友 | body `action=CONFIRM_TEAM/WITHDRAW`；确认组队时再次校验人数和资格 |
| 老板选择报名 | `POST /api/orders/orders/<id>/applications/<application_id>/select/` | 订单所属老板 | 报名 ID 在路径中；双人须队友已确认，服务端复核资格并固化各成员分账 |
| 开始服务 | `POST /api/orders/orders/<id>/start/`（别名 `/start-service/`） | 订单关联陪玩 | multipart `entry_image`；GRABBED→IN_SERVICE，双陪需相关成员入队凭证齐全 |
| 完成 | `POST /api/orders/orders/<id>/complete/` | 订单关联陪玩 | multipart `completion_image`；完成履约并释放名额；新订单还须报单审核/售后条件满足后结算，见 3.2 |
| 取消 | `POST /api/orders/orders/<id>/cancel/` | 订单所属老板 | 仅 PENDING 可取消，全额退款；body 可带 `reason` |
| 自行拒单 | `POST /api/orders/orders/<id>/reject/` | 已登录账号 | 不改变订单；返回 `ORDER_SELF_REJECTION_DISABLED`，由客服协调改派或取消退款 |
| 强制退款 | `POST /api/orders/orders/<id>/refund/` | OPERATOR | GRABBED/IN_SERVICE，`reason` 必填 |
| 旧直接指派入口 | `POST /api/orders/orders/<id>/assign/` | 后台 `order:dispatch` | 返回 HTTP 409，不指定陪玩；客服安排的新单按抢单池分发 |

除旧直接指派入口外，以上成功均返回 `data` 为最新订单对象；状态非法返回 `code=400`。
订单列表与详情的 `required_team_size=1/2` 取已成交人数快照，不能根据当前参与人数、后续修改的商品目录或价格自行猜测。普通双陪抢单须由两位合资格陪玩组成完整队伍，首轮结束后由系统排序分配；招募试音的选人流程与抢单池分开。抢单窗口、组队和退款仍依赖正式时钟与对应功能配置，完整链路待最终联调。

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
| 报单撤回 | `POST /api/wallet/reports/<id>/withdraw/` | 本人待审核报单 | 撤回为待提交草稿，保留全部凭证，可修改后重新提交 |
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

### 3.1.3 后台提现领取与审核

后台提现列表和详情返回 `claimed_by_account`、`claimed_by_name`、`claimed_at` 和当前账号的 `claim_is_mine`。待审核申请须先由持有 `withdraw:audit` 的客服调用 `POST /api/admin/withdrawals/<id>/claim/`；本人重复领取返回当前领取，其他客服领取时返回 409。历史待审核申请迁移后为未领取状态。

领取人核对最新收款快照并线下打款后，调用 `POST /api/admin/withdrawals/<id>/approve/`，提交 `payout_reference` 和本次领取返回的 `expected_claimed_at`。驳回使用 `POST /api/admin/withdrawals/<id>/reject/`，提交 `audit_remark` 和同一 `expected_claimed_at`。审核仅接受当前领取人及当前领取时间；缺失或不一致返回 409，须重新读取申请并核对打款记录。仅比较账号不足以区分同一账号在释放后的再次领取。

持有 `withdraw:view` 和 `withdraw:release` 的管理员在核实尚未打款后，可调用 `POST /api/admin/withdrawals/<id>/release-claim/`，提交 `confirmed_unpaid: true`、`reason`、`expected_claimed_by_account` 和 `expected_claimed_at`。仅当前待审核且领取信息一致时释放，写入审计并允许其他客服重新领取；不自动释放。领取、释放和审核变更推送提现状态，客户端断线重连后应重新读取详情。

上线时需先执行包括 `wallet.0032` 和 `console.0016` 的数据库迁移，再同步更新后台客户端；未发送领取时间的旧审核客户端无法继续审核。线下打款仍需管理员与领取人核实，接口不能控制系统外转账。

### 3.2 订单关联报单、凭证与审核

| 方法/路径（均在 `/api/wallet/` 下） | 请求 | 返回/约束 |
| --- | --- | --- |
| `GET reports/` | — | 当前账号自己的报单数组，包含草稿、待审核、已通过与已驳回记录 |
| `POST reports/` | JSON `order_id` | 本人已接单/服务中/已完成的服务订单，返回现有报单或创建 `DRAFT` 草稿；同一订单/陪玩不重复建报单。礼物、订单打赏及转单后无结算份额者不能使用此入口 |
| `POST reports/<id>/images/` | multipart `image` 文件、`kind=ENTRY/COMPLETION/RESULT` | 入队/结单各一张（再次上传替换）；战绩图片追加，最多九张；须为有效图片且报单可编辑，返回最新报单对象 |
| `DELETE reports/<id>/images/<image_id>/` | — | 仅移除本人可编辑报单的战绩图片引用，图片不存在返回 `code=404`；原文件由媒体保留策略处理 |
| `POST reports/<id>/submit/` | 无须提交金额或抽成 | 服务订单须 `COMPLETED + PAID`，且有入队、结单和至少一张战绩截图；成功变为 `PENDING`、更新 `submitted_at` 并清空旧驳回备注 |

创建草稿时，服务名称/备注、本人结算基数与抽成从关联订单和本人分账快照生成，客户端不以 `game_name/amount/proof_image` 新建手工报单。开始/完成服务上传的入队/结单截图会同步到该成员报单。三类凭证均通过权限签发的私有 URL 展示，查看过期图片应续签，不能把文件路径当公开素材。

订单列表及详情为陪玩返回 `report_entry: {available, reason}`，表示当前账号能否使用上述 POST 创建或获取该订单报单，原因与 POST 的资格判断一致；其他身份返回 `null`。这不是报单编辑或历史查看权限：报单列表仍保留本人历史记录，能否编辑及提交以报单对象字段为准。实际提交时服务端仍以最新订单状态重新判断。

报单对象字段：

| 字段组 | 字段与含义 |
| --- | --- |
| 标识/服务 | `id, order_id, order_no, service_name, fulfillment, game_name, description` |
| 金额/抽成 | `amount` 为本人结算基数；`expected_income` 为当前服务端收益快照；`commission_type=PERCENT/FIXED`、`commission_rate`、`commission_fixed`、`payout_amount`。金额均为内部账务单位；`FIXED` 的 `commission_rate=0` 不能解释为零抽成，须用固定抽成和收益快照展示 |
| 凭证 | `proof_image_url`（历史兼容）、`entry_image_url, completion_image_url, result_image_urls[]`、`result_images[{id,url}]`（用于准确删除对应图片） |
| 审核 | `status=DRAFT/PENDING/APPROVED/REJECTED, status_display, remark, audit_remark, created_at, audited_at, submitted_at` |
| 履约/结算 | `order_status, settled_at, aftersale_status`；`aftersale_status` 为关联订单售后状态或空字符串，不包含老板的售后原文。`OPEN` 未结算订单暂不结算，已结算订单仍以钱包流水核对；`APPROVED` 仅表示凭证通过，`settled_at` 才表明订单分账已执行，不能仅凭审核状态当作可提现余额 |
| 操作能力 | `can_edit, can_submit, edit_block_reason`；仅草稿/驳回且关联订单仍已付、未停止流程、本人仍有必要报单份额时可编辑。`can_submit` 另要求订单完成；该字段不代表截图已齐全，提交仍校验三类凭证 |

驳回后可补图并重提；取消、退款、回待接单池以及本人已退出且无结算份额时停止编辑与提交。后台列表排除 `DRAFT`；审核权限为 `report:audit`，只允许待审核报单通过/驳回，驳回必须有 `audit_remark`。后台对象另返回 `suggested_commission_rate, commission_source_label, commission_type, commission_fixed, expected_income, settlement_mode, settled_at`；关联订单采用本人已经保存的分账快照，不能在审核时将固定抽成重新套成百分比。

**首版目标规则**：售后等待和收益冻结按门店配置分别计算，默认均为 72 小时；普通服务的售后等待从整单完成开始，本人收益冻结从本人最后一次有效报单提交开始。两段时间重叠，须满足适用的本人审核、等待及售后条件；驳回后重提按最后一次有效提交重算。同一订单不同陪玩可以在整单补资足额且各自条件满足后分别释放，收益可能先用于补足押金。需要门店补资时，整单补资足额后才可结算；老板消费积分和 VIP 成长按各自规则及实销确认状态处理。审核通过不代表收益已到账，应以本人实际结算和钱包流水为准。

当前退款、转单、加购等资金组合仍有未接通或默认关闭的门禁，上述为已确认的目标口径，不能当作所有情形均已开放或通过验收；具体可用范围以接口响应和最新开发记录为准。历史订单接管不属于当前首版范围。

历史无关联订单的手工报单只保留查询与兼容审核：后台按历史报单/有效等级/全局规则解析百分比抽成，审核后保存收益快照及流水。该兼容路径不是当前客户端的新建报单入口。

报单审核通过或驳回的通知恢复入口：

- `GET /api/admin/notice-reviews/?kind=report-approval&page=1&page_size=20` 查询本店待核对通知，需同时具有 `message:view` 和 `order:view` 权限。返回原收件账号及业务周期、报单和订单标识、审核类型与时间、当前报单状态和审核时间；身份或周期缺失时可能为 `null`。
- `POST /api/admin/notice-reviews/report-approval/<id>/` 提交 `action`（`recheck` 或 `dismiss`）、`reason`（2～255 字）及列表返回的 `expected_note`；需同时具有 `message:push` 和 `order:view` 权限。`recheck` 仅对已保存原账号和周期的通知按实际审核事实重新投递，成功提交不等于已送达；`dismiss` 留审计并结束待办。状态或原因变化时返回冲突，客户端应刷新。接口不允许修改原收件人、周期或来源。
- 通知恢复依赖 `REPORT_APPROVAL_NOTICE_RECOVERY_ENABLED`、陪玩生命周期集成、门店数据库结构初始化以及逐店后台任务，当前开关默认关闭；该流程尚未最终联调。

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
| 可领券列表 | `GET /api/coupons/` | 登录 | 返回本周期领取状态及当前是否可以领取 |
| 我的券 | `GET /api/coupons/mine/` | 登录 | `?usable=1&amount=<分>` 仅返回满足金额门槛的可用券 |
| 领取 | `POST /api/coupons/<coupon_id>/claim/` | 登录 | 本周期同一券已领取时返回原结果，`replayed=true`；其他不符合领取条件的请求按实际原因拒绝 |

列表的 `claimable` 与 `claim_status` 保持一致：仅状态为 `AVAILABLE` 时为 `true`；已领取、资格已使用、库存不足及授权不允许或暂不可核验时为 `false`。领取接口仍在事务中重新核验规则和库存，列表状态不保证后续领取一定成功。本次变更尚待最终联调。

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

站内消息创建或已读状态变化后，提交成功会向接收账号发送 `site_message_update` WebSocket 失效提示，消息内容及未读数仍通过上述 HTTP 接口读取；重连后应重新查询，推送不是持久消息队列。

均需登录且仅能访问本人消息。

### 5.4 客服名片
`GET /api/support/contact-card/` · 权限：登录 — 返回当前启用、排序最靠前的一张名片（无则 `data=null`）。

---

## 6. C 端 · 在线客服 IM（`/api/chat/`）

| 接口 | 方法/路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| 会话+历史 | `GET /api/chat/session/` | 登录（OPERATOR 拒绝，走后台工作台） | 自动创建会话并返回最近 50 条消息；响应含 `session`、按时间正序排列的 `messages[]`、`has_more`、`next_before_id`。加载更早记录传 `before_id=next_before_id`；游标只属于当前账号的会话。 |
| 客服回复未读数 | `GET /api/chat/unread/` | 登录（客服账号除外） | 返回 `unread`；仅读取现有会话，不创建空会话。 |
| 发送 | `POST /api/chat/send/` | 老板或陪玩登录 | body `content`（最多 1000 字）、`image`（multipart）或 `order_ref` 至少提供一项；`order_ref` 关联本人有权查看的本店订单，可单独发送订单卡片，返回 `order_card`。参数或图片无效返回 HTTP 400，后台员工调用返回 HTTP 403。`unread_support+1` 并 WS 推送。无旧用户映射的业务账号也可创建会话；未绑定的历史会话会按已认证旧身份关联。 |
| 标记已读 | `POST /api/chat/read/` | 登录 | `through_id` 为已展示的最后消息 ID；仅标记该 ID 及之前的客服消息，返回剩余 `unread_user`。旧客户端省略时沿用全部标记已读。 |

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
| Banner | `banners` | 读写 | `banner:view` / `banner:edit`、`banner:delete` | 创建和修改需本店 `CONTENT_OPERATIONS` 授权；授权不足返回 403，无法核验授权返回 503。读取和删除仍按角色权限执行。 |
| 俱乐部内容 | `club-content` | 读写 | `engagement:view` / `engagement:edit` | 创建或修改老板权益 `RIGHTS`、俱乐部活动 `HOT` 需本店 `CONTENT_OPERATIONS` 授权；下单须知、陪玩保障、售后及积分规则仍按角色权限维护。读取和删除按角色权限执行。 |
| 播报规则 | `broadcast-rules` | 读取、创建、修改 | `engagement:view` / `engagement:edit` | 创建和修改需本店 `CONTENT_OPERATIONS` 授权；授权不足返回 403，无法核验返回 503。无授权期间不生成新播报，已有播报可继续查看和管理。 |
| 成就 | `achievements` | 读写 | `achievement:view` / `achievement:edit`、`achievement:delete` | — |
| 服务项 | `service-items` | 读写 | `service:view` / `service:edit`、`service:delete` | `category` |
| 老板分级 | `boss-types` | 读写 | `boss_type:view` / `boss_type:edit` | — |
| 陪玩等级 | `escort-levels` | 读写 | `escort_level:view` / `escort_level:edit` | — |
| 促销活动 | `promotions` | 读写 | `promotion:view` / `promotion:edit` | — |
| 客服名片 | `support-cards` | 读写 | `support:view` / `support:edit` | — |
| 试音链接 | `audition-links` | 读写 | `audition:view` / `audition:edit` | — |
| 老板试音入口 | `audition-links/{id}/bind-boss/` | POST | `audition:bind_boss` | `account_id` 为老板业务账号 ID 或 null，`expected_updated_at` 为读取版本；绑定变化轮换老板 token，保留陪玩 token，旧版 `boss_user` 直接修改被拒绝。持有该权限才能读取老板 token 和 URL；不撤销已签发会话。 |
| 老板试音入口候选 | `options/audition-bosses/` | GET | `audition:bind_boss` | 返回可登录老板账号的编号、昵称和用户名，不返回余额。 |
| 陪玩试音入口 | `audition-links/{id}/bind-provider/` | POST | `audition:bind_provider` | `account_id` 为陪玩业务账号 ID 或 null，`expected_updated_at` 为读取版本；绑定变化轮换陪玩 token，保留老板 token 和既有报名，旧版 `provider_user` 直接修改被拒绝。已绑定入口的 token 和 URL 仅该权限可读；普通报名入口仍按查看权限读取。轮换不撤销已签发会话，绑定也不限制其他已登录陪玩报名。 |
| 陪玩试音入口候选 | `options/audition-providers/` | GET | `audition:bind_provider` | 返回可登录陪玩账号的编号、昵称和用户名。 |
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
| 会话 | 回复 | `POST chat-sessions/<id>/reply/` | `chat:reply` | `content` 或 `image`；可传 `read_through_id` 仅将已展示的用户消息标为已读，`unread_user+1`，WS 推送。旧客户端省略时沿用全部标记已读。 |
| 会话 | 标记已读 | `POST chat-sessions/<id>/read/` | `chat:reply` | 可传 `through_id`，仅标记该 ID 及之前的用户消息，返回剩余 `unread_support`；旧客户端省略时沿用全部标记已读。 |
| 试音报名 | 通过 | `POST audition-signups/<id>/approve/` | `audition:signup_audit` | 发站内消息通知 |
| 试音报名 | 驳回 | `POST audition-signups/<id>/reject/` | `audition:signup_audit` | `audit_remark`(必填) |
| 站内消息 | 推送 | `POST messages/push/` | `message:push` | `title, preview, detail, type, broadcast(bool), recipient_ids[]` |

---

#### 老板订单筛选确认

`GET /api/admin/orders/?customer_account_id=<老板账号ID>` 按业务账号筛选，并兼容关联该账号的旧用户订单。成功响应的 `data.customer_account_id_applied` 为实际应用的账号 ID 字符串，空结果也会返回；未指定筛选不返回该字段。无效 ID 返回 400。后台页面要求确认值与请求一致，缺失或不一致时不展示结果；前后端需要配合部署。

`GET /api/admin/orders/?created_day=YYYY-MM-DD` 按服务端当前时区的订单创建自然日筛选，起始包含、次日零点不包含；成功响应在 `data.created_day_applied` 返回 `date` 和 `timezone`，即使没有匹配订单也返回。日期无效返回 400；未指定日期不返回该字段。工作台的七日新增订单使用相同时区和创建日期，统计与列表分别读取，期间数据变化可能造成数量差异。新页面要求服务端确认日期，前后端需配合部署。

`GET /api/admin/orders/?actionable_staff=1` 的客服可派队列按 `auto_cancel_at` 从早到晚排序，未记录截止时间的订单排后，同一截止时间按创建时间及 ID 升序稳定排序；直付校验有上限时亦优先检查较早截止的候选项。成功响应的 `data.staff_queue_order` 为 `deadline_asc`；旧服务未返回时页面显示排序待确认。`staff_queue_needs_review` 仍表示有订单需要单独核对，排序不替代指派时资格检查。

工作台 `pending_paid_overdue` 统计读取时仍为已支付、待接单且接单截止时间已到的订单；无截止时间的不计入。`GET /api/admin/orders/?overdue_pending=1` 按服务端当前时间筛选相同范围，成功响应包含 `data.overdue_pending_applied=true`。单独筛选时按截止时间、创建时间和订单 ID 升序排序，并返回 `overdue_pending_order=deadline_asc`；与售后或客服待派叠加时沿用对应队列排序。两次读取间状态或时间可能变化，数量不保证相等。该筛选只供核对，不执行取消或退款。

工作台 `legacy_team_inflight_total` 统计当前仍处于已接单或服务中、且使用旧版履约流程的双陪订单。`GET /api/admin/orders/?legacy_team=1` 筛选同一范围，成功返回 `data.legacy_team_applied=true`；跨请求期间状态变化可能导致数量不同。客服核实双方尚未开始后，可对满足条件的已接单订单提交 `POST /api/admin/orders/{id}/enable-team-evidence/`，请求体为 `{"confirmed":true}`；服务端会再次核查成员及履约记录并写入状态流水，不能切换已开始服务或已有报单的订单。该操作需要 `order:dispatch` 权限，不自动修改其他历史订单。

`GET /api/admin/orders/?aftersale_open=1` 返回当前有处理中售后的订单，按本轮申请时间从早到晚、同一时间按订单 ID 升序排列，分页前完成排序；成功响应 `data.aftersale_queue_order` 为 `opened_asc`，每行 `aftersale_open_at` 为本轮申请时间。售后处理后再次申请使用重新申请时间，不是原订单创建时间。叠加 `actionable_staff=1` 时仍按客服待派截止时间排序，仅返回其排序标记；未确认排序时页面显示“排序待确认”。

### 10.2 订单百分比与整单固定抽成

金额接口使用内部账务整数，`10` 内部单位等于 `1` 店币，`1` 元人民币等于 `10` 店币。兴安店币称星辉，声浪店币称浪花。抽成规则由各店配置，客服代单或派单不能为单笔订单临时改比例或固定金额。

普通服务按会员、门店活动或折扣券中选定的一项比例优惠及适用金额券计算成交额，再按规则使用积分抵扣。陪玩收益以积分抵扣**前**的成交额和本人适用抽成快照计算；员工工资和岗位提成按实际支付店币计算。服务主单推荐佣金按主单实际支付店币乘适用比例计算，加购和礼物不计推荐佣金。积分、赠送及赔付可能产生的门店补资缺口按实际资金规则处理，不应从实付金额直接倒推陪玩收益。

三角洲按每名陪玩本人在对应服务范围的资格档位取本店抽成；瓦罗兰特和英雄联盟按陪玩或代肝类别配置，上分按代肝类别。转入人员使用接入时的门店规则快照；转单涉及成交额和固定抽成分配时，按已确认的个人原价份额及对应取整规则执行。

涉及积分、加购、转单和售后的资金组合仍有受控或未完成路径。客户端应以服务端当前报价和可执行结果为准，不能依本文假定所有组合已开放；最终须在隔离环境联调实际流水和结算。

安全箱补贴启用后，`GET /api/admin/wallets/platform-funds/` 返回本店补贴资金状态和待结算汇总。找到资金钱包时 `funds_status` 为 `AVAILABLE`，金额取真实钱包和押金流水；钱包尚未初始化时为 `NOT_INITIALIZED`，`wallet_id`、`balance`、`deposits`、`available` 为 `null`，不代表余额为零。待结算汇总仍按实际订单独立返回。相关报单的 `platform_funding_insufficient` 在钱包尚未初始化时同样返回 `null`，表示不能判断，而非认定资金不足。这些状态尚待最终联调。

### 10.3 服务加购报价与申请

老板通过 `GET /api/orders/orders/{order_id}/addons/` 查看本单可发现的加购项目，再向 `POST /api/orders/orders/{order_id}/addons/quote/` 提交 `addon_id`、`unit`、正整数 `quantity`、`configuration_version` 和 `provider_account_id`。报价返回内部单位金额，以及 `intent_available` 和 `intent_reason`：仅在所选项目、陪玩与抽成快照可核对且不需客服核实时允许继续发起申请。需要客服核实的项目通过客服代办；报价不是付款保证。

`POST /api/orders/orders/{order_id}/addons/intents/` 接收相同选择、`request_id`、`expected_amount`、`gift_balance_amount` 和 `compensation_balance_amount`。申请详情中的 `payment_available` 表示当前付款入口能否处理；`purchase_available` 不能作为已付款依据。付款使用 `POST /api/orders/orders/{order_id}/addons/intents/{intent_id}/pay/`，并在事务中重新校验价格、付款来源和资金条件。上述新增报价字段尚待最终联调。

## 11. 关键业务规则速记

- **计价与分账**：原价、优惠后成交额、积分抵扣及实付店币分别记录；陪玩收益、推荐佣金和岗位业绩按各自口径计算，补资与退款单独留账。
- **抽成方式**：本店配置百分比或整单固定金额，订单和转入人员保存各自适用快照，客服不得单笔覆盖。
- **订单状态**：常规流程为 `PENDING→GRABBED→IN_SERVICE→COMPLETED`；取消和售后依具体状态及权限处理。抢单等待期限由门店配置，实际截止以正式轮次和持久化收件结果为准。
- **资金处理**：支付、冻结、退款、押金及结算须以本店实际流水和事务结果为准，跨库补资另按平台到账记录核对。
- **提现门槛**：门店分别配置单笔最低额及可用余额门槛；可用余额必须严格大于后者，且扣除手续费后实际打款须大于零。
