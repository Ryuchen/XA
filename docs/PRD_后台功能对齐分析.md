# 后端管理项目（Console）功能 ↔ PRD 文档 对齐分析报告

> 审计对象：`services/backend/console/`（路由前缀 `/api/admin/`，31 个路由注册点、60 个权限点、24 组）
> 对照文档：`PRD.md`、`PRD_订单管理与陪玩结算.md`、`产品开发文档_兴安电竞陪玩平台.md`、`PRODUCT_DOCUMENTATION.md`
> 审计日期：2026-08-03
> 结论：**主干功能已落地约 85%，但存在 3 处 PRD 与代码互相矛盾、5 类数据口径/资金隐患、以及若干 PRD 已规划但代码缺失的缺口。** 建议以「整合版开发文档」为单一事实源，把 `PRD_订单管理与陪玩结算.md` 的增量需求作为本期 backlog，并优先处理资金隐患。
>
> **修订记录**：
> - **2026-08-03（文档侧对齐）**：按「先修 PRD 文档、不动代码」方案，第四节 3 处矛盾全部消除，第八节「签到」PRD 缺项补录（见《附录一：文档侧变更清单》）。
> - **2026-08-03（第二轮代码修复）**：第五节的 5 类资金/数据隐患中 **3 类已修复**（提现税额入账、双陪报单金额口径、负数调账 tx_type），见《附录二：代码侧修复记录（第一轮）》。
> - **2026-08-04（第三轮代码修复）**：**第 4 类已修复**——两套 ID 空间错充风险收敛为分字段收口（`account_id` 透传 ClubAccount.id，`user_id` 保留 legacy 兼容），见《附录二：代码侧修复记录（第三轮）》。
> - **2026-08-04（第四轮代码修复）**：**第 5 类（最后一类）已修复**——后台 `OrderViewSet.complete` 结算前加凭证闸门 `_require_order_evidence`，与顾客端 `REQUIRE_ORDER_EVIDENCE_IMAGES` 约束对齐，缺失入队/结单图整体回滚，见《附录二：代码侧修复记录（第四轮）》。**5 类资金/数据隐患全部收口**。

---

## 一、结论速览

| 维度 | 结论 |
|------|------|
| 已实现（对齐良好） | 用户/老板/陪玩人员管理、订单履约全链路（派单→转单→结算→退款）、钱包调账/充值、提现审核（强制凭证）、报单审核、营销（券/促销/Banner/公告/成就/签到）、内容、IM 工作台、试音、RBAC、两套数据看板、系统配置（抽成率/最低提现/税率） |
| PRD 已规划但代码缺失 | 退押闭环（DP-1）、订单预约时间/档期冲突（ORD-1/ORD-2）、封禁带原因/期限/审计（ORD-4）、报表导出（WD-4）、报单业务归属/凭证分级/批量模板（RPT 类；其中双陪报单金额口径已于 2026-08-03 代码轮修复） |
| PRD 与代码矛盾（需对齐） | ✅ **已全部对齐（2026-08-03）**：双陪结算基数口径、自动取消兜底（PRD 标缺失实则已实现）、绑定码机制（PRD 要求实则已废弃） |
| 代码有但 PRD 未覆盖 / 风险 | ✅ **5 项全部已修复**：提现税额账务黑洞、双陪报单金额错位、负数调账 tx_type（2026-08-03 代码轮）；两套 ID 空间错充、后台 complete 绕过凭证风控（2026-08-04 代码轮） |

---

## 二、对齐总览表（按域）

| # | 功能域 | PRD 状态 | Console 实现 | 对齐 |
|---|--------|----------|--------------|------|
| 1 | 用户/老板账号管理 | 编辑/分级/推荐人/开关 | ✅ 编辑+分级+推荐人 | ✅ 良好 |
| 2 | 陪玩（打手）管理 | 开户/资料/等级/奖惩 | ✅ 代开户+资料+认证+奖惩+等级 | ✅ 良好（退押/停用缺） |
| 3 | 老板钱包管理 | 充值/赠送/调账 | ✅ 充值+调账+流水 | ✅ 良好 |
| 4 | 订单管理 | 查询/代建/退款/预约/冲突/兜底 | ✅ 查询+代建(双陪)+退款；❌预约/冲突 | ⚠️ 部分 |
| 5 | 结算/分账/钱包 | 调账/流水/全局抽成 | ✅；⚠️ 陪玩钱包不可管/ID 空间 | ⚠️ 部分 |
| 6 | 充值/提现审核 | 充值/审核/凭证/税/导出 | ✅ 充值+审核+凭证；⚠️ 税/导出缺 | ⚠️ 部分 |
| 7 | 报单/奖惩/押金 | 报单审核/退押 | ✅ 报单审核；❌ 退押 | ⚠️ 部分 |
| 8 | 营销/权益 | 券/促销/等级/成就/Banner | ✅ 全部；⚠️ 无定向发券 | ✅ 良好 |
| 9 | 内容/公告/Banner/名片/消息 | CRUD | ✅ 全部 | ✅ 良好 |
| 10 | 客诉/IM | IM 工作台/仲裁单 | ✅ IM；❌ 独立仲裁单 | ⚠️ 部分 |
| 11 | 招募试音 | 链接+审核 | ✅ 链接+审核 | ✅ 良好 |
| 12 | 数据看板/报表 | 概览/陪玩/KPI/导出 | ✅ 概览+陪玩；❌ 导出 | ⚠️ 部分 |
| 13 | RBAC/管理员/审计 | 角色/账号/审计 | ✅ 完整；⚠️ 树/看板权限未校验 | ✅ 良好 |
| 14 | 系统配置 | 抽成率/最低提现/税率/~~绑定码~~ | ✅ 四项配置（含签到规则）；绑定码已决策废弃；❌ 无通用 SystemConfig CRUD | ✅ 良好 |

---

## 三、逐项对齐矩阵

### ① 用户/老板账号管理
- ✅ **编辑**：`UserViewSet`（仅 BOSS）支持昵称/真名/手机/分级/编号/推荐人/分佣率/`is_active`/`can_login`/`can_view`，双写 legacy `CustomUser`。
- ✅ **老板分级**：`BossTypeViewSet` CRUD + 折扣率。
- ✅ **推荐人分佣**：绑定 `inviter` + `inviter_commission_rate`，影响订单平台抽成计提。
- ❌ **封禁审计（P2/ORD-4）**：当前「封禁」= 直接 PATCH 三个布尔开关，**无原因、无期限、无专用封禁记录表、无自动解封**。仅落一条通用 `AdminAuditLog`（PATCH）无法追溯语义。
- 备注：后台**不能新增/删除老板账号**（走 C 端微信自助注册），属合理边界。

### ② 陪玩（打手）管理
- ✅ **代开户** `EscortViewSet.create`：建 `ClubAccount(PROVIDER)` + legacy `CustomUser` + `LegacyAccountMap` + `EscortProfile`，钱包信号自动建。
- ✅ **资料编辑/认证/奖惩/等级**：`update`/`verify`/`dispose`/`EscortLevelViewSet` 均实现，`dispose` 走行锁+流水+`DisposeRecord`+累加 `total_reward/total_penalty`。
- ❌ **陪玩账号停用/删除**：`UserViewSet` 只查 BOSS；陪玩序列化器**不含** `is_active`/`can_login`，档案 `http_method_names` 不含 `delete` → 只能靠（不存在的）停用开关。
- ❌ **退押闭环（DP-1, P2）**：押金仅"看+改应缴"，缴纳走 C 端；全 backend 无任何退押申请/冻结/审核/打款路径，陪玩退出无闭环。

### ③ 老板钱包（见⑤结算）

### ④ 订单管理
- ✅ **查询/筛选/状态日志/抽成建议**：`OrderViewSet`（只读）全实现；`suggest_commission` 返回 活动>等级>商品>全局 来源。
- ✅ **代建单/双陪**：`quick_dispatch` 支持 `escort_mode=DOUBLE` + 每个打手独立 `commission_type/rate/fixed`，建 N 条 `OrderProvider` 快照；双陪必须显式指定 2 名不同打手。
- ✅ **转单/开始/完成结算/指派**：`transfer`/`start`/`complete`/`assign` 全实现；`complete` 有 `assert_order_conserved` 跨行守恒闸门 + 事务回滚（即前次修复的 P0-1/P0-2）。
- ✅ **退款/取消**：`refund` 全额退老板+`REFUND` 流水+回滚优惠券；`cancel` 仅 PENDING。
- ❌ **预约服务时间/预计时长（ORD-1, P1）**：`Order` 模型**无预约/时长字段**，无法支撑档期冲突与履约提醒。
- ❌ **跨单档期冲突检测（ORD-2, P1）**：`provider_is_scheduled_now()` 只判断"此刻是否落在周档期内"，**不查该打手同时段已有订单**；`MAX_CONCURRENT_ORDERS=3` 常量存在但派单路径**从未引用** → 后台可无限量派单给同一打手。
- ✅ **自动取消兜底（PRD 标 P2 缺口 → 实际已实现）**：`orders/tasks.py::check_pending_timeouts` + Celery beat + `check_order_timeout` cron 命令。**PRD 过时，勿误判为缺口。**
- ❌ **业务归属字段（ORD-6, P2）**：订单无法关联游戏/商品/时长/来源，经营分析口径不全。
- ⚠️ **退款限制**：仅 PENDING 可退（终态不可退），无部分退款/售后/争议；退款为全额，不回收已结算给打手的钱（因终态拦截回避）。
- ✅ **评价管理**：列表/详情/代回复/删除（回退评分聚合）。

### ⑤ 结算/分账/钱包/调账/充值
- ✅ **钱包查看/调账/充值/流水**：`WalletViewSet`（仅 `role=CUSTOMER` 老板钱包）+ `adjust`/`recharge` + `RechargeRecord` + `TransactionViewSet`。
- ⚠️ **陪玩钱包不可在 console 管理**：`WalletViewSet` 硬过滤 `role=CUSTOMER`，陪玩钱包只能经 `dispose`（奖惩）间接加减。
- ✅ **负数调账 tx_type 语义错位 —— 已修复（2026-08-03 代码轮）**：`adjust` 不论正负统一落 `ADJUST`（后台调账）流水，不再污染提现统计；新增 `Transaction.TxType.ADJUST` 并配套迁移 `0020`。
- ✅ **两套 ID 空间错充 —— 已修复（2026-08-04 代码轮）**：`recharge`/`messages.push`/`recharge-records`/`dispose-records` 改为分字段收口——前端 user 选择器返回 `ClubAccount.id`，统一用 `account_id`（recharge / 记录筛选）/ `recipient_account_ids`（push）显式透传；legacy `CustomUser.id` 仍走 `user_id` / `recipient_ids` 兼容。两套 ID 空间都是小整数自增，bare integer 无法区分，分字段传入从根本消除错充（新增 `_legacy_user_id_from_account_id` 做 ClubAccount.id → legacy 的单向映射）。
- ✅ **全局抽成率**：`SystemConfig[commission_rate]` 动态配置。

### ⑥ 充值/提现审核
- ✅ **充值入账**：`WalletViewSet.recharge` 实充+赠送+凭证图+`RechargeRecord`。
- ✅ **提现审核通过/驳回 + 强制打款凭证（WD-1 固化）**：`approve` 空凭证直接 400；`reject` 强制原因+退回余额。
- ✅ **税额/实到账（WD-2, 部分）—— 已修复（2026-08-03 代码轮）**：审核 `approve` 现读取 `tax_amount`，将税额贷记**平台钱包**（`get_platform_wallet`）并落 `WITHDRAW_TAX` 流水（含 `operator/operator_account` 审计），资金守恒（陪玩流出 = 税额 + 实到账）；给陪玩到账通知改用 `actual_amount` 并附代扣税明细；提现提交侧 `msg` 与 pending 流水 remark 也补充税前/税/实到拆分。零税率时不产生 `WITHDRAW_TAX` 流水。
- ❌ **打款渠道回执（WD-3, P1）**：无渠道回执字段/状态机，真实渠道 API 未接。
- ❌ **提现明细导出（WD-4, P2）**：全站零导出能力（grep `export|csv|xlsx` 零命中）。
- ❌ **失败重试与通知（WD-5, P2）**：无"打款中/失败"中间态，失败只能人工。

### ⑦ 报单/奖惩/押金
- ✅ **报单审核**：`ProviderReportViewSet` approve/reject，双分支（平台订单报单仅核验凭证防重复入账；手工报单按抽成优先级入账 `INCOME`），校验 `status==PENDING`。
- ✅ **双陪报单金额口径错位（与 P0-1 同源）—— 已修复（2026-08-03 代码轮）**：`approve` 新增 `_resolve_report_order_share(report)` 辅助，取报单人在该订单中**自己那条** `OrderProvider.provider_income`（按 `provider_account_id`/`provider_id` 匹配），副陪审核金额与钱包实际到账一致；无分账快照的旧订单回落到原订单级口径，向后兼容。`get_queryset` 增加 `order__providers` 预取避免 N+1。
- ❌ **退押（DP-1）**：见②。
- ❌ **报单业务归属（RPT-1）/ 凭证分级（RPT-2）/ 批量模板（RPT-3）**：基本缺失。
- ⚠️ **草稿不可见**：`get_queryset` `.exclude(DRAFT)`，陪玩端卡草稿后台无感知。

### ⑧ 营销/权益
- ✅ **优惠券/促销/等级/成就/Banner/公告/客服名片/站内消息**：全部 CRUD/审核实现；Banner 强制图片尺寸、名片校验企微 kfid。
- ✅ **签到**：`CheckinRuleConfig` + `CheckinGift` + `CheckinMonthProgress`（代码有，PRD 原未单列）→ **已于 2026-08-03 补录**至 `PRD.md §3.1/§5.1/§6.5` 与 `产品开发文档 §3.3/§4.10`（消费门槛、阶梯礼物、补签卡、全勤奖、后台三个端点与 `checkin:view/edit` 权限点）。
- ⚠️ **无定向发券**：`UserCouponViewSet` 只读，发放只能靠 C 端自助领取。
- ⚠️ **站内消息推送 O(N) 同步写库**：全员广播会超时，无异步/批量。

### ⑨ 内容/公告/Banner/名片/消息 — ✅ 全部对齐

### ⑩ 客诉/IM
- ✅ **IM 工作台**：会话列表/消息/回复/已读，WS 实时推送，共享会话池。
- ❌ **争议/仲裁单**：当前仅 IM + 后台退款结案，**无独立仲裁单实体/申诉流程**。
- ⚠️ 共享池无分配/认领/转接/工作量统计/快捷回复模板。

### ⑪ 招募试音（Audition）
- ✅ **试音链接（双 token）+ 报名审核**：`AuditionLinkViewSet` + `AuditionSignupViewSet` approve/reject（强制原因）。
- ⚠️ 报名通过后**无自动转陪玩账号**（需人工走 `escorts/create`）；无音频/视频材料字段。

### ⑫ 数据看板/报表
- ✅ **总览看板 + 陪玩经营看板**：KPI、状态分布、男女/游戏礼物拆分、Top10 排行。
- ⚠️ **`dashboard:view` 权限点定义了却从不校验**（任何后台账号可看）。
- ⚠️ **`settled_salary` 取 `WithdrawRequest.amount`（税前）**，与 `actual_amount` 口径不一致。
- ❌ **导出能力缺失**（财务对账只能翻页）。
- ❌ 无营收趋势/日周月环比/客服业绩报表（除"今日派单额"）。

### ⑬ RBAC/管理员/审计
- ✅ **完整**：`RoleViewSet`/`AdminMembershipViewSet` + `HasConsolePerm` + `AdminAuditMiddleware`（写操作全量落 `AdminAuditLog`）+ 预置 4 角色。
- ⚠️ **`PermissionTreeView` 无权限校验**：任何后台账号可拉全部权限点定义。
- ⚠️ 审计仅 HTTP 层（method/path/body/status），**无业务语义**（"封禁了谁/调账多少"需解析）。

### ⑭ 系统配置
- ✅ **平台抽成率 / 最低提现额 + 税率 / 签到规则**：三个专用 Config 视图可读写。
- ⚠️ **绑定码机制（已决策废弃，PRD 已同步）**：`BindCodeGenerateView` 生成即丢弃、不落库、无消费方；登录端点注释"不再接收 bindCode"，响应恒 `null`。**PRD 侧已全部标注废弃（2026-08-03）；代码侧死接口待清理。**
- ⚠️ **Config GET 全裸**（仅 `IsConsoleUser`）；**改抽成率竟复用 `report:audit` 权限点**（语义错配），权限表无 `config:*` 分组；**无通用 `SystemConfig` CRUD**（新键必改代码）。

---

## 四、PRD 与代码相互矛盾 / 过时（3 处 —— ✅ 已于 2026-08-03 全部对齐）

1. **双陪结算基数口径冲突** —— ✅ **已消除**
   - 原状：`PRODUCT_DOCUMENTATION §10` 标注"已优化：先平均分配实付再各自抽成"；`PRD_订单 §6.3` 仍按"每名打手以订单实付为基数、实得和可能 > 实付"描述并标 P1 开放问题。
   - 事实：代码已修复（`split_settlement_bases` 均分 + `assert_order_conserved` 闸门 + 回归测试），资金已守恒。
   - 处置：`PRD_订单管理与陪玩结算.md §6.3` 已重写为「已落地·资金守恒」，风险表该行由 P1 改为「已修复」。

2. **自动取消兜底（ORD-3）** —— ✅ **已消除**
   - 原状：PRD 标 P2 缺口；实际已实现（`orders/tasks.py::check_pending_timeouts` + Celery beat + `check_order_timeout` cron）。
   - 处置：`PRD_订单管理与陪玩结算.md` ORD-3 由 `P2` 改为 `✅ 已实现`，风险表「自动取消」改为「已落地」，并在 ORD 表后补充实现说明。

3. **绑定码机制** —— ✅ **已消除（决策：废弃）**
   - 原状：`PRD.md` / `Sequence.md` / `产品开发文档` 要求陪玩/客服登录校验 `bindCode`；代码已废弃（生成即弃、登录不接收、响应恒 `null`）。
   - 处置：采纳「以代码为准」的决策，三份文档共 9 处引用全部标注**已废弃**，`Sequence.md` 登录时序图移除 `bindCode` 参数与校验节点，改为"角色由后台开户确定"。
   - 遗留：`BindCodeGenerateView` 死接口仍在代码中，建议后续清理（不影响功能，属代码卫生）。

---

## 五、代码实现但 PRD 未覆盖 / 数据口径风险（产品视角重点）

以下不是"功能缺失"，而是**已实现但存在资金/数据隐患**，建议作为本期高优先级修复：

| 风险 | 影响 | 位置 | 状态 |
|------|------|------|------|
| ~~**提现税额账务黑洞**~~ | `tax_amount` 既不入平台钱包也无 `Transaction`，账面凭空消失；通知用税前额易生客诉 | `WithdrawRequestViewSet.approve` / `wallet/views.py` | ✅ **已修复（2026-08-03）** |
| ~~**双陪报单金额错位**~~ | 副陪看到的审核金额 ≠ 钱包实际到账，对账混乱 | `ProviderReportViewSet.approve` | ✅ **已修复（2026-08-03）** |
| **后台 complete 绕过凭证风控** | 不校验入队/结单图即可结算，报单审核形同事后追认 | `OrderViewSet.complete` | ✅ 已修复（2026-08-04，结算前凭证闸门） |
| **两套 ID 空间错充风险** | `ClubAccount.id` vs legacy `CustomUser.id` 混用，前端直连易错充 | `recharge` / `messages.push` 等 | ✅ 已修复（2026-08-04，分字段收口） |
| ~~**负数调账 tx_type=WITHDRAW**~~ | 污染流水统计与提现口径 | `WalletViewSet.adjust` | ✅ **已修复（2026-08-03）** |

---

## 六、优先级建议（下一步 backlog）

**P0（资金/合规隐患）**
1. ~~提现税额入账与通知口径对齐（第五节第 1 项）。~~ → ✅ **已修复（2026-08-03 代码轮）**。
2. ~~双陪报单金额取个人 `OrderProvider.provider_income`（第五节第 2 项）。~~ → ✅ **已修复（2026-08-03 代码轮）**。
3. ~~两套 ID 空间收敛（统一用 `ClubAccount.id` 透传，legacy 仅内部互推）。~~ → ✅ **已修复（2026-08-04 代码轮，分字段收口）**。

**P1（PRD 已规划缺口）**
4. 订单预约时间/预计时长 + 跨单档期冲突检测（引用 `MAX_CONCURRENT_ORDERS`）。
5. 封禁带原因/期限/审计专表。
6. ~~后台 complete 结算前校验报单凭证/状态。~~ → ✅ **已修复（2026-08-04 代码轮，凭证闸门）**。
7. ~~负数调账 tx_type 修正~~ → ✅ **已修复（2026-08-03 代码轮）**；陪玩钱包后台可视/可调（仍待办，P1）。

**P2（完善度）**
8. 退押闭环（DP-1）。
9. 报表导出（WD-4）+ 营收趋势/客服业绩报表。
10. 报单业务归属/凭证分级（RPT 类）。
11. 独立争议/仲裁单。
12. ~~绑定码机制：决定废弃（改 PRD）或恢复实现。~~ → ✅ **已决策废弃并同步文档**；剩余动作仅为清理 `BindCodeGenerateView` 死接口（P3 代码卫生）。

**文档侧**
13. ~~修正第四节 3 处矛盾（双陪口径、自动取消、绑定码）。~~ → ✅ **已完成（2026-08-03）**，见附录。
14. 仍待办：以 `产品开发文档_兴安电竞陪玩平台.md` 为单一事实源，将 `PRD_订单管理与陪玩结算.md` 的增量需求（ORD-1/2/4、DP-1、WD-2~5、RPT 类）正式并入产品 backlog 与排期。

---

## 附录一：文档侧对齐变更清单（2026-08-03 第一轮）

> 处置范围：**仅改文档，不动代码**（用户选定方案，零上线风险）。代码侧的 5 类资金/数据隐患与功能缺口均未触碰。

| 文件 | 变更 |
|------|------|
| `PRD_订单管理与陪玩结算.md` | §6.3 双陪结算规则由「开放问题·实得和可能超额」重写为「已落地·资金守恒」（均分基数 + 各自抽成 + `assert_order_conserved` 闸门）；风险表双陪行 P1→已修复；ORD-3 由 `P2`→`✅ 已实现`；风险表自动取消 `P2`→已落地；ORD 表后新增实现说明（Celery beat + cron）；§5.2 陪玩端登录移除 `bindCode` 要求 |
| `API.md` | §1.10 签到接口由 1 行响应字段扩写为完整契约（消费门槛/补签卡/全勤三组字段 + `day` 补签参数 + 错误码）；§1.11 生成绑定码标题加「⚠️ 已废弃」并加入禁止调用说明 |
| `PRD.md` | §2.4 角色绑定码标注「已废弃」并说明现状；角色表客服职责移除「生成绑定码」；功能表绑定码→已废弃；接口表登录条目移除 `users/bind-code/`；测试账号表「登录绑定码」列改为「登录方式」；§3.1 每月签到扩写为消费型签到规则；§5.1 补充 `CheckinRuleConfig`/`CheckinGift`/`CheckinMonthProgress` 三个模型；§6.5 新增「签到运营」后台能力与端点 |
| `产品开发文档_兴安电竞陪玩平台.md` | 3 处 bindCode 引用（登录说明、开户说明、接口表）标注已废弃；§3.3 运营后台新增「签到运营」条目；§4.10 签到章节由 1 行扩写为完整规则表（日签门槛/阶梯奖励/补签卡/全勤奖/月度重置）+ 数据模型说明 |
| `Sequence.md` | 登录时序图移除 `/wechat-login/`、`/account-login/` 请求体中的 `bindCode`；删除「非 customer 角色校验 bindCode 非空」节点，替换为「角色已由后台开户确定」注释 |
| `PRODUCT_DOCUMENTATION.md` | §4.7 签到描述由「每日唯一签到 + 阶梯奖励」补齐为消费型签到（门槛 / 补签卡 / 全勤奖 / 后台可配） |

**覆盖校验**：全 `docs/` 目录 grep `bindCode|绑定码` 与 `签到`，已无与代码矛盾的残留表述（`API.md` 保留 1.11 端点条目但显式标注废弃，用于告知历史前端）。

**未处理（转代码工作）**：第五节的 5 类资金/数据隐患**已于 2026-08-04 全部收口**（提现税额 / 双陪报单 / 负数调账 / 两套 ID 空间 / 后台 complete 凭证风控）；第三节各 ❌ 功能缺口、`BindCodeGenerateView` 死接口清理仍待排期。

---

## 附录二：代码侧修复记录（2026-08-03 第二轮）

> 处置范围：**修复 3 类资金/数据隐患**（用户选定「继续下一轮代码修复」）。覆盖 `wallet` / `console` 两模块，配套迁移与回归测试，全量 425 测试通过、零回归。

| 隐患 | 代码改动 | 验证 |
|------|----------|------|
| **提现税额账务黑洞** | `console/views.py::WithdrawRequestViewSet.approve` 读取 `tax_amount`，贷记平台钱包（`get_platform_wallet`）并落 `Transaction.TxType.WITHDRAW_TAX`（带 `operator/operator_account` 审计）；到账通知改用 `actual_amount` + 代扣税明细；`wallet/views.py::WithdrawView.post` 提交侧 `msg` 与 pending 流水 remark 补充税前/税/实到拆分 | 新增 `WithdrawTaxAccountingTest`（6 用例）：税额快照、平台钱包入账、资金守恒、零税率无税流水、通知用实到账、驳回不动平台钱包 |
| **双陪报单金额错位** | `console/views.py::ProviderReportViewSet.approve` 新增 `_resolve_report_order_share(report)` 取报单人自身 `OrderProvider.provider_income`（按 `provider_account_id`/`provider_id` 匹配），无快照旧单回落订单级；`get_queryset` 增加 `order__providers` 预取 | 新增 `DoubleEscortReportPayoutTest`（6 用例）：副陪/主陪各取自身分账、入账与审核金额一致、无重复入账、旧单向后兼容 |
| **负数调账 tx_type 语义错位** | `console/views.py::WalletViewSet.adjust` 不论正负统一 `tx_type=Transaction.TxType.ADJUST`；`wallet/models.py` 新增 `ADJUST` / `WITHDRAW_TAX` 两个 `TxType`，并生成迁移 `0020_add_withdraw_tax_and_adjust_tx_types.py`（更新 `transaction_type_valid` 约束） | 新增 `WalletAdjustTxTypeTest`（3 用例）：负调账→`ADJUST` 非 `WITHDRAW`、正调账→`ADJUST` 非 `REWARD`、余额与操作人追溯保留 |

### 代码侧修复记录（第三轮 2026-08-04）

> 处置范围：**修复第 4 类资金/数据隐患——两套 ID 空间错充**。覆盖 `console/views.py`，配套回归测试 `console/test_id_space_fixes.py`（9 用例），全量测试通过、零回归。

| 隐患 | 代码改动 | 验证 |
|------|----------|------|
| **两套 ID 空间错充风险** | `console/views.py` 中 `recharge` / `messages.push` / `recharge-records` / `dispose-records` 四个端点由"单 `user_id` 字段既当 ClubAccount.id 又当 legacy id"改为**分字段收口**：前端 `UserViewSet` 返回的 `ClubAccount.id` 走 `account_id`（recharge / 记录筛选）与 `recipient_account_ids`（push）；legacy `CustomUser.id` 仍走 `user_id` / `recipient_ids` 兼容。新增 `_legacy_user_id_from_account_id()` 做 ClubAccount.id → legacy 的**单向**映射（经 `LegacyAccountMap`，缺映射自愈补链），绝不把 bare integer 当 legacy 猜 | 新增 `test_id_space_fixes.py`（9 用例）：`account_id` 正确入账、错充防护、legacy `user_id` 向后兼容、记录列表按 `account_id` 筛选命中、`recipient_account_ids` 推送、撞号场景两字段解析到不同用户 |

**未处理（仍待代码工作）**：第三节各 ❌ 功能缺口（退押 DP-1、预约冲突 ORD-1/2、封禁审计 ORD-4、报表导出 WD-4、RPT 类、争议仲裁单等）、`BindCodeGenerateView` 死接口清理（P3 代码卫生）。**5 类资金/数据隐患已全部收口。**

### 代码侧修复记录（第四轮 2026-08-04）

> 处置范围：**修复最后 1 类资金/数据隐患——后台 complete 绕过凭证风控**。覆盖 `console/views.py`，配套回归测试 `console/test_complete_evidence.py`（5 用例），全量测试通过、零回归。

| 隐患 | 代码改动 | 验证 |
|------|----------|------|
| **后台 complete 绕过凭证风控** | `console/views.py::OrderViewSet.complete` 的 `side_effect` 开头调用新增 `_require_order_evidence(order)`：当 `settings.REQUIRE_ORDER_EVIDENCE_IMAGES` 开启时，要求每个参与结算的陪玩报单（`ProviderReport`）齐备 `entry_image` + `completion_image`，缺失（或无报单）抛 `SettlementError`，由 `complete` 统一回滚事务并返回清晰错误（含"缺少入队/结单截图/报单凭证"字样）。与顾客端 `StartOrderServiceView` / `CompleteOrderView` 的凭证约束完全对齐 | 新增 `test_complete_evidence.py`（5 用例）：凭证齐备可结算并正确入账、缺结单图/缺入队图/无报单均被拒且钱包与订单状态不变、`REQUIRE_ORDER_EVIDENCE_IMAGES=False` 时不阻结算 |

> **上线提醒**：凭证闸门仅在 `REQUIRE_ORDER_EVIDENCE_IMAGES=True` 时生效，与顾客端行为一致；生产环境务必开启该开关，否则后台 complete 仍不校验凭证。
