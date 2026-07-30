# 兴安电竞（XA）· 数据模型 ER 图

> 配套文档：[PRD.md](./PRD.md) / [DATABASE_DESIGN.md](./DATABASE_DESIGN.md)　│　最后更新：2026-07-30
>
> 本文用 Mermaid `erDiagram` 描述系统数据表及其关系。金额字段使用整数内部账务单位，
> 当前换算规则为 `10` 个内部单位等于 `1` 兴安币。
> GitHub / 支持 Mermaid 的 Markdown 预览器可直接渲染。

---

## 1. 总览：实体关系全景

```mermaid
erDiagram
    CustomUser ||--o| EscortProfile : "1:1 陪玩资料"
    CustomUser ||--o| Wallet : "1:1 钱包"
    CustomUser ||--o| ChatSession : "1:1 客服会话"
    CustomUser ||--o| AdminMembership : "1:1 管理员身份"
    CustomUser ||--o{ Order : "下单(customer)"
    CustomUser ||--o{ Order : "接单(provider)"
    CustomUser ||--o{ CheckinRecord : "签到"
    CustomUser ||--o{ EscortSchedule : "档期"
    CustomUser ||--o{ UserCoupon : "领券"
    CustomUser ||--o{ ServiceFavorite : "收藏"
    CustomUser ||--o{ WithdrawRequest : "提现"
    CustomUser ||--o{ ProviderReport : "报单"
    CustomUser ||--o{ RechargeRecord : "充值"
    CustomUser ||--o{ DisposeRecord : "奖罚"
    CustomUser ||--o{ Message : "站内消息"
    CustomUser }o--o| BossType : "老板分级"
    CustomUser }o--o| CustomUser : "推荐人(inviter)"

    BossType ||--o{ CustomUser : ""
    EscortLevel ||--o{ EscortProfile : "等级"

    ServiceItem ||--o{ Order : "服务"
    ServiceItem ||--o{ ServiceFavorite : ""
    ServiceItem }o--o{ Promotion : "活动适用商品"

    Order ||--o| Evaluation : "1:1 评价"
    Order ||--o{ OrderStatusLog : "状态日志"
    Order ||--o{ Transaction : "关联流水"
    Order ||--o{ UserCoupon : "用券订单"
    Promotion ||--o{ Order : "命中活动"

    Wallet ||--o{ Transaction : "流水"
    Transaction ||--o| WithdrawRequest : "提现流水"
    Transaction ||--o| ProviderReport : "报单入账流水"

    Coupon ||--o{ UserCoupon : "券模板"

    AuditionLink ||--o{ AuditionSignup : "试音报名"
    CustomUser ||--o{ AuditionSignup : "报名人"

    ChatSession ||--o{ ChatMessage : "会话消息"
    CustomUser ||--o{ ChatMessage : "发送者"

    AdminRole ||--o{ AdminMembership : "角色成员"
```

---

## 2. 用户与陪玩域（users）

```mermaid
erDiagram
    CustomUser {
        int id PK
        string username
        string password
        string role "CUSTOMER/PROVIDER/OPERATOR/ADMIN"
        string openid "微信 openid"
        string phone
        string nickname
        string real_name
        string avatar_url
        bool is_phone_verified
        bool is_openid_bound
        string game_region "常用游戏区服"
        string game_nickname
        string game_uid
        int inviter_id FK "推荐人(self)"
        int inviter_commission_rate "推荐分佣率%"
        int boss_type_id FK
        string boss_no "老板编号"
        bool can_login
        bool can_view
        datetime last_active_at
        datetime created_at
    }
    BossType {
        int id PK
        string name UK
        int discount_rate "下单折扣率% 100=原价"
        int sort_order
        bool is_active
    }
    EscortLevel {
        int id PK
        string name UK
        int commission_rate "平台抽成率%"
        int sort_order
        bool is_active
    }
    EscortProfile {
        int id PK
        int user_id FK "1:1"
        string display_name
        string gender "MALE/FEMALE/UNKNOWN"
        string bio
        string city
        string service_area
        int price_per_hour "时价(分)"
        string rank_tier "段位"
        int level_id FK
        decimal win_rate
        string status "AVAILABLE/BUSY/OFFLINE"
        bool is_verified
        string escort_no
        int deposit_required "应缴押金(分)"
        int deposit_paid "已缴押金(分)"
        int total_reward "累计奖励(分)"
        int total_penalty "累计罚款(分)"
        decimal rating_avg
        int rating_count
        int completed_order_count
    }
    CheckinRecord {
        int id PK
        int user_id FK
        date checkin_date "UK(user,date)"
        int seq_in_month "本月第几次"
        int reward_amount "奖励(分)"
    }
    Achievement {
        int id PK
        string code UK
        string title
        string desc
        string icon
        string metric "orders/amount"
        int target "目标值(amount维度为分)"
        int sort_order
        bool is_active
    }
    EscortSchedule {
        int id PK
        int provider_id FK
        int weekday "0=周一..6=周日"
        int start_minute "0~1440"
        int end_minute "0~1440"
    }

    CustomUser ||--o| EscortProfile : ""
    CustomUser }o--o| BossType : ""
    EscortLevel ||--o{ EscortProfile : ""
    CustomUser ||--o{ CheckinRecord : ""
    CustomUser ||--o{ EscortSchedule : ""
    CustomUser }o--o| CustomUser : "inviter"
```

> 说明：`Achievement` 为成就配置表，老板的解锁状态按其已完成订单数/累计消费实时计算，不单独落用户成就记录。

---

## 3. 订单与服务域（orders）

```mermaid
erDiagram
    ServiceItem {
        int id PK
        string name
        string description
        int price "单价(分)"
        string category "NORMAL陪玩/GIFT礼品"
        int commission_rate "商品抽成率% null回落全局"
        string cover_url
        json images
        json highlights
        int sort_order
        bool is_active
    }
    ServiceFavorite {
        int id PK
        int user_id FK
        int service_id FK "UK(user,service)"
    }
    Order {
        int id PK
        string order_no UK
        int customer_id FK "下单老板"
        int provider_id FK "接单陪玩"
        int service_id FK
        int amount "实付(分)"
        int game_rounds "局数"
        int original_amount "原价(分)"
        int boss_discount "老板折扣额(分)"
        int promo_discount "活动折扣额(分)"
        int coupon_discount "券抵扣(分)"
        int promotion_id FK
        int commission_rate "抽成率快照%"
        int provider_income "陪玩实得(分)"
        int inviter_id FK "推荐人快照"
        int inviter_commission "推荐分佣(分)"
        int shop_income "平台留存(分)"
        string game_region
        string game_nickname
        string game_uid
        string remark
        string payment_status "UNPAID/PAID/REFUNDED"
        string status "PENDING/GRABBED/IN_SERVICE/COMPLETED/CANCELLED"
        datetime grabbed_at
        datetime in_service_at
        datetime completed_at
        datetime cancelled_at
        datetime refunded_at
        string cancel_reason
        datetime auto_cancel_at "超时自动取消时间"
        int reject_count
        datetime created_at
    }
    OrderStatusLog {
        int id PK
        int order_id FK
        string action "CREATE/GRAB/ASSIGN/START/COMPLETE/REJECT/CANCEL/REFUND/AUTO_CANCEL"
        string from_status
        string to_status
        int operator_id FK
        string reason
        datetime created_at
    }
    Evaluation {
        int id PK
        int order_id FK "1:1"
        int customer_id FK
        int provider_id FK
        int score "综合1-5"
        int skill_score "技术1-5"
        int attitude_score "态度1-5"
        int communication_score "沟通1-5"
        string content
        bool is_anonymous
        string reply_content
        datetime replied_at
    }

    ServiceItem ||--o{ Order : ""
    ServiceItem ||--o{ ServiceFavorite : ""
    Order ||--o| Evaluation : ""
    Order ||--o{ OrderStatusLog : ""
```

---

## 4. 钱包与资金域（wallet）

```mermaid
erDiagram
    Wallet {
        int id PK
        int user_id FK "1:1"
        int balance "余额(分)"
        int frozen_amount "冻结(分)"
        int total_recharge "累计实充(分)"
        int total_gift "累计赠送(分)"
        bool is_active
    }
    Transaction {
        int id PK
        int wallet_id FK
        int order_id FK
        string tx_no UK
        int amount "正增负减(分)"
        string tx_type "TOPUP/PAY/INCOME/WITHDRAW/REWARD/GIFT/PENALTY/DEPOSIT/SHOP_INCOME"
        int balance_before
        int balance_after
        string status "PENDING/SUCCESS/FAILED"
        string remark
        string external_tx_id
        datetime created_at
    }
    SystemConfig {
        int id PK
        string key UK "platform_commission_rate/min_withdraw_amount"
        string value
        string remark
    }
    ProviderReport {
        int id PK
        int provider_id FK
        string game_name
        string description
        int amount "报单总额(分)"
        image proof_image "凭证"
        string status "PENDING/APPROVED/REJECTED"
        int commission_rate "抽成率快照%"
        int payout_amount "陪玩实得(分)"
        int auditor_id FK
        int transaction_id FK
        datetime audited_at
    }
    WithdrawRequest {
        int id PK
        int user_id FK
        int amount "提现额(分)"
        string payee_method "WECHAT/ALIPAY/BANK"
        string payee_account
        string payee_name
        string status "PENDING/APPROVED/REJECTED"
        int auditor_id FK
        int transaction_id FK
        datetime audited_at
    }
    RechargeRecord {
        int id PK
        int user_id FK
        int amount "实充(分)"
        int gift_amount "赠送(分)"
        int operator_id FK
        int recharge_tx_id FK
        int gift_tx_id FK
    }
    DisposeRecord {
        int id PK
        int user_id FK
        string dispose_type "REWARD/PENALTY"
        int amount "金额(分)"
        string reason
        int operator_id FK
        int transaction_id FK
    }

    Wallet ||--o{ Transaction : ""
    Transaction ||--o| WithdrawRequest : ""
    Transaction ||--o| ProviderReport : ""
    Transaction ||--o| RechargeRecord : "recharge_tx/gift_tx"
    Transaction ||--o| DisposeRecord : ""
```

---

## 5. 营销与内容域（coupons / promotions / banners / announcements / site_messages）

```mermaid
erDiagram
    Coupon {
        int id PK
        string name
        string discount_type "THRESHOLD满减/DIRECT无门槛"
        int threshold "门槛(分)"
        int amount "面额(分)"
        datetime valid_to "有效期至"
        int total_qty "发放总量 0不限"
        int claimed_qty "已领"
        bool is_active
        int sort_order
    }
    UserCoupon {
        int id PK
        int user_id FK
        int coupon_id FK "UK(user,coupon)"
        string status "UNUSED/USED/EXPIRED"
        int order_id FK "使用订单"
        datetime claimed_at
        datetime used_at
    }
    Promotion {
        int id PK
        string title
        int discount_rate "活动折扣率% null=不打折"
        int commission_rate "覆盖抽成率% null=不覆盖"
        string scope "ALL/CATEGORY/ITEMS"
        string category
        datetime start_at
        datetime end_at
        int priority "越大越优先"
        bool is_active
    }
    Banner {
        int id PK
        image image "750x320"
        string title
        string link_type "NONE/PRODUCT/ANNOUNCEMENT/URL"
        string link_value
        int sort_order
        bool is_active
    }
    Announcement {
        int id PK
        string title
        text content
        bool is_pinned
        bool is_active
        int sort_order
    }
    Message {
        int id PK
        int recipient_id FK
        string type "SYSTEM/ORDER/SUPPORT/PROMOTION"
        string title
        string preview
        text detail
        string action_url
        bool is_read
        int related_order_id
    }

    Coupon ||--o{ UserCoupon : ""
    Promotion }o--o{ ServiceItem : "scope=ITEMS"
```

---

## 6. 客服 / 试音 / 后台权限域（chat / support / audition / console）

```mermaid
erDiagram
    ChatSession {
        int id PK
        int user_id FK "1:1 发起用户"
        string last_message
        datetime last_message_at
        int unread_user "用户未读"
        int unread_support "客服未读"
    }
    ChatMessage {
        int id PK
        int session_id FK
        int sender_id FK
        bool is_from_support
        string content_type "TEXT/IMAGE"
        string content
        image image
        bool is_read
        datetime created_at
    }
    SupportContactCard {
        int id PK
        string name
        string company
        string wechat_id
        string avatar_url
        string qrcode_url
        text tips
        bool is_active
        int sort_order
    }
    AuditionLink {
        int id PK
        string title
        datetime expire_at
        bool is_active
        int operator_id FK
        string boss_token UK
        string provider_token UK
        int boss_user_id FK
        int provider_user_id FK
    }
    AuditionSignup {
        int id PK
        int link_id FK
        int applicant_id FK "UK(link,applicant)"
        string contact
        string game
        string status "PENDING/APPROVED/REJECTED"
        int auditor_id FK
        string audit_remark
        datetime audited_at
    }
    AdminRole {
        int id PK
        string name UK
        string code UK
        json permissions "权限点code列表"
        bool is_active
        int sort_order
    }
    AdminMembership {
        int id PK
        int user_id FK "1:1"
        int role_id FK
        string remark
        bool is_active
    }

    ChatSession ||--o{ ChatMessage : ""
    AuditionLink ||--o{ AuditionSignup : ""
    AdminRole ||--o{ AdminMembership : ""
```

---

## 关系要点速记

| 关系 | 说明 |
|------|------|
| CustomUser ↔ EscortProfile | 一对一；仅陪玩角色拥有 |
| CustomUser ↔ Wallet | 一对一；首次访问自动创建（含平台系统账户） |
| CustomUser ↔ CustomUser (inviter) | 自关联；推荐人体系 |
| Order ↔ Evaluation | 一对一；订单完成后可评价一次 |
| Order → ServiceItem / Promotion | 多对一；下单时落库快照字段，历史不随源数据变更 |
| Transaction ↔ WithdrawRequest / ProviderReport / RechargeRecord / DisposeRecord | 资金动作各自挂关联流水，保证可追溯 |
| Promotion ↔ ServiceItem | 多对多；仅 `scope=ITEMS` 时使用 |
| ChatSession ↔ CustomUser | 一对一；非客服用户与客服团队共享会话池 |
| AdminRole ↔ AdminMembership ↔ CustomUser | RBAC：角色持权限点，用户绑角色 |
</content>
