# 兴安电竞（XA）· 核心流程时序图

> 配套文档：[PRD.md](./PRD.md) · [ER.md](./ER.md)　│　最后更新：2026-06-30
>
> 本文用 Mermaid `sequenceDiagram` 描述关键业务链路的端到端调用顺序，含资金动作与 WebSocket 推送。金额单位均为「分」。

---

## 1. 老板下单支付（含计价拆账）

> 入口：`POST /api/orders/create/`　│　核心：`CreateOrderView` + `compute_split`

```mermaid
sequenceDiagram
    autonumber
    participant C as 老板(C端)
    participant API as CreateOrderView
    participant SER as CreateOrderSerializer
    participant DB as MySQL(事务)
    participant WS as WebSocket(notifier)
    participant MQ as Celery

    C->>API: POST /orders/create/ {service,rounds,coupon,provider...}
    API->>SER: 校验并计算金额
    SER-->>API: amount/抽成率/折扣明细/inviter
    API->>API: compute_split(amount, 抽成率, 分佣率)
    Note over API: 得到 provider_income / inviter_commission / shop_income

    rect rgb(235,245,255)
    API->>DB: BEGIN atomic
    API->>DB: SELECT wallet FOR UPDATE
    alt 余额不足
        DB-->>API: balance < amount
        API-->>C: code=400 余额不足
    else 余额充足
        opt 使用了优惠券
            API->>DB: SELECT user_coupon FOR UPDATE (status=UNUSED)
        end
        API->>DB: wallet.balance -= amount
        API->>DB: INSERT Order(PENDING, PAID, auto_cancel_at=+30min)
        API->>DB: INSERT Transaction(PAY, -amount)
        opt 有券
            API->>DB: user_coupon → USED, 绑定 order
        end
        API->>DB: INSERT OrderStatusLog(CREATE)
        API->>DB: COMMIT
    end
    end

    API->>MQ: 投递超时自动取消(countdown=30min)
    API->>WS: notify_order_update(order)
    WS-->>C: order_status_update 推送
    API-->>C: code=0 下单成功
```

**计价公式**：`amount = 原价(单价×局数) − 老板折扣 − 活动折扣 − 券抵扣`
**拆账**：`provider_income + inviter_commission + shop_income = amount`

---

## 2. 接单 → 服务 → 完成（收益三方入账）

> 抢单 `POST /orders/{id}/grab/`、开始 `start/`、完成 `complete/`

```mermaid
sequenceDiagram
    autonumber
    participant P as 陪玩(C端)
    participant API as Orders API
    participant SM as state_machine.transition
    participant DB as MySQL(事务)
    participant WS as WebSocket
    participant C as 老板

    Note over P,API: ① 抢单
    P->>API: POST /orders/{id}/grab/
    API->>API: 校验 role=PROVIDER 且 escort.status=AVAILABLE
    API->>SM: transition → GRABBED
    SM->>DB: 校验未被他人接单 → set provider, escort=BUSY
    API->>WS: notify_order_update
    WS-->>C: 大神已接单
    API-->>P: 接单成功

    Note over P,API: ② 开始服务
    P->>API: POST /orders/{id}/start/
    API->>SM: transition → IN_SERVICE (校验是本人)
    API->>WS: notify_order_update

    Note over P,API: ③ 完成（资金结算）
    P->>API: POST /orders/{id}/complete/
    rect rgb(235,255,235)
    API->>DB: BEGIN atomic
    API->>DB: 陪玩钱包 += provider_income, INSERT Tx(INCOME)
    opt 有推荐人分佣
        API->>DB: 推荐人钱包 += inviter_commission, INSERT Tx(INCOME)
    end
    opt 有平台留存
        API->>DB: 平台账户 += shop_income, INSERT Tx(SHOP_INCOME)
    end
    API->>DB: escort.status=AVAILABLE, completed_order_count += 1
    API->>SM: transition → COMPLETED
    API->>DB: COMMIT
    end
    API->>WS: notify_order_update
    WS-->>C: 订单已完成，去评价
    API-->>P: 订单已完成
```

> 拒单（`reject/`）：订单回 `PENDING`、清 provider、`reject_count+1`、陪玩恢复 `AVAILABLE`。

---

## 3. 客服派单（替代陪玩抢单）

> 入口：`POST /api/orders/{id}/assign/`　│　仅 OPERATOR

```mermaid
sequenceDiagram
    autonumber
    participant S as 客服(C端/后台)
    participant API as AssignOrderView
    participant SM as transition
    participant DB as MySQL
    participant WS as WebSocket
    participant P as 陪玩
    participant C as 老板

    S->>API: POST /orders/{id}/assign/ {provider_id}
    API->>API: 校验 role=OPERATOR, provider 存在
    API->>SM: transition → GRABBED (action=ASSIGN)
    SM->>DB: set provider, provider 状态=BUSY
    API->>WS: notify_order_update
    API->>P: 站内消息「客服派单给你」
    API->>C: 站内消息「已为你指派大神」
    API-->>S: 派单成功
```

---

## 4. 订单取消 / 退款（全额退回）

> 老板取消 `cancel/`（仅 PENDING）；客服强制退款 `refund/`（GRABBED/IN_SERVICE）

```mermaid
sequenceDiagram
    autonumber
    participant U as 老板/客服
    participant API as Cancel/RefundOrderView
    participant DB as MySQL(事务)
    participant WS as WebSocket

    U->>API: POST /orders/{id}/cancel|refund/ {reason}
    API->>API: pre_check 状态与权限
    rect rgb(255,240,240)
    API->>DB: BEGIN atomic
    API->>DB: 老板钱包 += order.amount (退款)
    API->>DB: INSERT Transaction(TOPUP, +amount)
    API->>DB: order.payment_status=REFUNDED, refunded_at=now
    opt 强制退款且已有陪玩
        API->>DB: 陪玩 status=AVAILABLE
    end
    API->>DB: transition → CANCELLED, COMMIT
    end
    API->>WS: notify_order_update
    API-->>U: 已取消/退款，款项原路退回
```

---

## 5. 陪玩提现申请 → 后台审核

> 申请 `POST /api/wallet/withdraw/`（冻结）→ 后台 `approve/` 或 `reject/`

```mermaid
sequenceDiagram
    autonumber
    participant P as 陪玩(C端)
    participant W as WithdrawView
    participant DB as MySQL(事务)
    participant A as 后台 WithdrawRequestViewSet
    participant Op as 运营

    Note over P,W: ① 发起提现（冻结资金）
    P->>W: POST /wallet/withdraw/ {amount, payee_*}
    W->>W: 校验 role=PROVIDER, amount≥最低提现额
    rect rgb(255,250,235)
    W->>DB: BEGIN atomic, SELECT wallet FOR UPDATE
    alt 余额不足
        W-->>P: code=400 余额不足
    else
        W->>DB: balance -= amount, frozen_amount += amount
        W->>DB: INSERT Tx(WITHDRAW, -amount, status=PENDING)
        W->>DB: INSERT WithdrawRequest(PENDING)
        W->>DB: COMMIT
        W-->>P: 申请已提交，等待审核
    end
    end

    Note over Op,A: ② 后台审核
    Op->>A: POST /admin/withdrawals/{id}/approve|reject/
    rect rgb(235,245,255)
    A->>DB: BEGIN atomic, SELECT withdraw+wallet FOR UPDATE
    alt 通过 approve
        A->>DB: frozen_amount -= amount (实际打款)
        A->>DB: Tx → SUCCESS
        A->>DB: withdraw → APPROVED
        A->>P: 站内消息「提现审核通过」
    else 驳回 reject(需 audit_remark)
        A->>DB: frozen_amount -= amount, balance += amount (退回)
        A->>DB: Tx → FAILED
        A->>DB: withdraw → REJECTED
        A->>P: 站内消息「提现未通过」
    end
    A->>DB: COMMIT
    end
```

---

## 6. 陪玩报单 → 后台审核入账

> 报单 `POST /api/wallet/reports/`（待审）→ 后台 `approve/`（按抽成入账）

```mermaid
sequenceDiagram
    autonumber
    participant P as 陪玩
    participant API as ReportListCreateView
    participant A as 后台 ProviderReportViewSet
    participant DB as MySQL

    P->>API: POST /wallet/reports/ {game,amount,proof_image}
    API->>DB: INSERT ProviderReport(PENDING)
    API-->>P: 报单已提交，等待审核

    A->>A: POST /admin/reports/{id}/approve/
    rect rgb(235,255,235)
    A->>A: 固化抽成率快照，算 payout_amount
    A->>DB: 陪玩钱包 += payout, INSERT Tx(INCOME)
    A->>DB: report → APPROVED, 绑定 transaction
    end
    A->>P: 站内消息（审核结果）
```

> 押金缴纳（`POST /api/wallet/deposit/`）：校验应缴差额 → 扣余额、`deposit_paid += amount`、记 `DEPOSIT` 负数流水。

---

## 7. 客服 IM 实时会话

> C 端 `POST /api/chat/send/` + WebSocket 推送；后台工作台对称回复

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户(C端)
    participant API as ChatSendView
    participant DB as MySQL(事务)
    participant WS as WebSocket(notify_chat_message)
    participant Op as 客服(后台)

    U->>API: POST /chat/send/ {content 或 image}
    rect rgb(245,245,255)
    API->>DB: SELECT ChatSession FOR UPDATE (get_or_create)
    API->>DB: INSERT ChatMessage(is_from_support=false)
    API->>DB: session.last_message/at, unread_support += 1
    API->>DB: COMMIT
    end
    API->>WS: notify_chat_message
    WS-->>Op: chat_message + chat_session_update (operators 组)
    API-->>U: 消息已发送

    Op->>Op: 后台回复（is_from_support=true, unread_user += 1）
    Op->>WS: 推送至 user_{id} 组
    WS-->>U: chat_message
    U->>API: POST /chat/read/ → unread_user=0
```

> 会话模型：每个非客服用户与客服团队共享唯一 `ChatSession`（一对一）。

---

## 8. 招募试音（免登录换发 JWT）

> 客服后台生成链接 → 访客凭 token 换 JWT → 陪玩报名 → 后台审核

```mermaid
sequenceDiagram
    autonumber
    participant Op as 客服(后台)
    participant V as 访客/陪玩(C端)
    participant PUB as AuditionPublicView
    participant EX as AuditionExchangeView
    participant SU as AuditionSignupView
    participant DB as MySQL

    Op->>DB: 生成 AuditionLink(boss_token/provider_token)
    Op-->>V: 分享链接(含 token)

    V->>PUB: GET /audition/info?token=&role= (AllowAny)
    PUB-->>V: 活动信息(只读, 不发 token)

    V->>EX: POST /audition/exchange {token, role}
    EX->>DB: 校验链接有效 + 绑定用户
    EX-->>V: {token(JWT), userInfo} 登录态

    V->>SU: POST /audition/signup {token,contact,game} (需登录, role=PROVIDER)
    SU->>DB: INSERT AuditionSignup(PENDING) unique(link,applicant)
    SU-->>V: 报名成功，等待审核

    Op->>DB: POST /admin/audition-signups/{id}/approve|reject/
```

---

## 9. 微信 / 账号登录

> `POST /api/users/wechat-login/`（小程序）｜`account-login/`（H5）

```mermaid
sequenceDiagram
    autonumber
    participant C as 客户端
    participant API as Wechat/AccountLoginView
    participant WX as 微信(code2session)
    participant DB as MySQL

    alt 微信登录
        C->>API: POST /wechat-login/ {code, role, bindCode, phoneCode?}
        alt WECHAT_MOCK_LOGIN=True
            API->>API: openid='wx_mock_<code>'
        else 真实
            API->>WX: code2session(code)
            WX-->>API: openid
            opt 带 phoneCode
                API->>WX: 解析手机号 → 回填
            end
        end
    else 账号登录
        C->>API: POST /account-login/ {username, password, role, bindCode}
        API->>DB: 校验密码(真实 hash)
    end

    API->>API: 非 customer 角色校验 bindCode 非空
    API->>DB: get_or_create 用户, 升级 role
    API-->>C: {token(JWT 12h), userInfo}
```

---

## 推送与并发要点

| 机制 | 说明 |
|------|------|
| WebSocket 分组 | `user_{id}`（点对点）、`operators`（全体客服）、`providers`（全体陪玩） |
| 推送事件 | `order_status_update`、`chat_message`、`chat_session_update` |
| 并发安全 | 所有资金动作在 `transaction.atomic()` + `select_for_update()` 行锁内完成 |
| 兜底 | 超时取消优先 Celery；不可用时由 management command 兜底 |
| 站内消息 | `_push_message_safe` 包裹，推送失败绝不影响主流程 |
</content>
