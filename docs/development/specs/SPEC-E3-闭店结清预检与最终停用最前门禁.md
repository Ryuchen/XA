# SPEC-E3 闭店结清预检与最终停用最前门禁

2026-11-25 第三波执行规格。依据开发 29 第 07 项、PRD 37 §3.4、PRD 33 的停用快照规则及 SPEC-E2 已签收的逐户本金返还切片。

## 一、当前结论与范围

- `SETTLEMENT_PENDING` 只证明 30 天公示已届满，不代表余额、履约或售后已经结清。
- 当前返还写入、退款、收益释放、提现等尚未共用店库级最终结算屏障；PC04 费用规则仍待确认。没有证据时不得默认 0 费用，不得提供强制结清或假停用按钮。
- 本波交付平台授权角色可用的**全店只读结清预检**：分别给出老板资金、履约／售后／提现等义务、费用及最终写入能力的真实状态和阻断原因。
- 本波不新增最终结清成功事件，不修改 `Store.status`，不提前生成停用快照，不发放或扣减资金。最终确认只有在费用规则、共享屏障和跨库恢复均接通后另行实施。

## 二、预检原则

- 预检以门店身份、原闭店公示及到期证据为边界；未进入 `SETTLEMENT_PENDING` 返回 409，控制库或店库不可用返回 503。
- 全店扫描不受分页限制。任一读取失败、金额未知或身份不完整均使 `complete=false`，不能以空列表或零金额代替；空老板账号清单也必须显式阻断，避免“全部完成”的空集误判。
- 输出稳定 `snapshot_sha256`，绑定门店、通知／到期事件、各阻断项及规范化事实，供以后最终确认比对。摘要本身不是结清授权，也不证明在后续时点仍未变化。
- 预检 GET 不得自动过期订单、创建返还单、修改状态、投递资金任务或补齐审计。

## 三、老板资金预检

复用 `wallet/closure_returns.py` 的真实清单和来源核验，不另算一套余额：

- `READY_TO_PREPARE`、FROZEN、含非零本金或赠送／赔付、存在后来新增可用余额均阻断。
- 来源／批次／周期／积分读取异常、迁移待核、待投递事件或平台审计不一致均阻断。须直接核对全部历史 case 的迁移标记，不能只看被最新周期投影遮蔽的记录。
- PAID 只有在原实付、冻结、事件和审计可复算且当前无新增余额时才可作为已完成事实；历史 PAID 不遮蔽后来余额。`paid_fen`、本金、支付流水、事件及安全凭证引用必须一致；同一外部支付参考被多案使用时，没有可复算拆分就阻断。
- RELEASED 只是原冻结已解除，不代表资金已经退回；恢复后的当前可用余额仍须另行清点。
- 真实零余额须同时具备钱包记录、可用／冻结均为 0、无正余额批次、无活动返还单和完整来源读取；`ZERO_BALANCE` 可以在这些条件下作为明确事实，不与一般 `REVIEW_REQUIRED` 混同。
- 积分不折算现金；积分读取失败仍属不完整。赠送、赔付和其他非本金责任必须单独阻断，不擅自清零或折现。

## 四、其他义务预检

按实际模型分别汇总，至少覆盖：

- 进行中或未完成的订单、有效预约、开放售后、待结收益及未释放 IncomeHold。
- `PENDING`、`APPROVED`、`STORE_APPROVED`、`PLATFORM_APPROVED`、`PAYMENT_UNKNOWN` 等在途或结果未知提现。
- 陪玩退款押金缺口、未关闭补差资格、未结推荐／续单收益、未交付权益及其他可确认的未结义务。

已有 `provider_retirement.clearance_facts`、`engagement.retirement.retirement_facts` 和 `deposit_refund_eligibility` 只作具体账户视角的参考；没有锁或适用范围的调用不得冒充全店结清证明。本波无法可靠核验的类别必须返回 `UNKNOWN`／对应阻断码，不显示为 0。

## 五、API 契约

新增：

`GET /api/admin/platform/store-management/<store_id>/closure/settlement-readiness`

仅在平台管理域名开放，要求 `closure:view` 及目标门店 scope。响应 `Cache-Control: no-store`，固定返回：

- `status`: `BLOCKED` 或 `UNKNOWN`；本波没有 `READY_TO_FINALIZE`。任一事实读取不完整时为 UNKNOWN，否则因费用或写入能力门禁为 BLOCKED。
- `complete`、`can_finalize=false`、`observed_at`、`snapshot_sha256`。
- `funds` 固定包含 `complete`、`status=CLEAR/BLOCKED/UNKNOWN`、`account_count`、`completed_count`、`zero_balance_count`、`totals`、`blockers`、`facts_sha256`。`totals` 沿用返还清单的 wallet/principal/gift/compensation/points/frozen/refund/paid 字段及原单位；不完整时整体为 `null`。
- `obligations` 固定包含 `complete`、`status=CLEAR/BLOCKED/UNKNOWN`、`metrics`、`blockers`、`facts_sha256`。`metrics` 每项严格为 `{code,count}`，count 只能是整数或 `null`；无法核验的分类为 `null`，不是 0，缺失类别也必须显式报告。
- `policy` 固定包含 `complete=true`、`fee_rules='PENDING'`、`final_write_barrier='UNAVAILABLE'` 和对应阻断项。
- `blockers` 项统一为 `{code, resource, reference}`，分别使用稳定阻断码、安全资源类型及脱敏引用；不包含完整银行资料、凭证正文、内部闭店原因或其他老板隐私。
- `blocking_reasons` 至少能区分 `FUNDS_REVIEW_REQUIRED`、`BUSINESS_OBLIGATIONS`、`FEE_RULES_PENDING`、`FINAL_WRITE_BARRIER_UNAVAILABLE` 和 `READ_INCOMPLETE`。
- `snapshot_sha256` 由门店、通知／到期事件、资金和义务 `facts_sha256` 及政策门禁规范化计算，不包含 `observed_at`；同一事实重读保持一致，任何已知事实变化必须改变摘要。

匿名、店内账号、无权限及错 scope 分别按原平台契约拒绝；不得用 query/header 覆盖门店。

## 六、后台页面

在现有平台闭店页增加只读“结清预检”区域：分别显示资金、其他义务、费用规则及最终写入能力，支持加载、空、异常、有数据四态和手动刷新。

- 明确展示“预检不是结清”“当前不可最终停用”，不显示可绕过阻断的确认按钮。
- 门店或平台会话变化时取消旧请求并清理旧快照；刷新失败保留过期标记，不能将旧完整结果显示为当前可结清。
- 金额未知显示“—”，原因保留服务端代码对应文案；不从列表页局部合计补算全店金额。
- 老板端、陪玩端公开页不新增内部预检字段，不泄露费用协商、审计或阻断对象；现有闭店公示和待结清文案保持。

另修复既有 `Store.status=DISABLED` 的公开可读性，不把普通停用冒充最终结清：仅 `GET /api/stores/current/` 与 `GET /api/stores/closure/` 可使用经本店域名及可信路由核验的非 ACTIVE 只读上下文，其他登录、订单、钱包、报单、WebSocket 和任务入口保持现行拒绝。

公开闭店接口在确认门店为 DISABLED 后固定返回 `{status:'DISABLED', announcement_visible:true, clock_status:'NOT_REQUIRED', announcement:'本店已停用，不再接受新增业务。既有订单、售后与余额返还情况需另行核对；停用不代表已完成退款或结清。'}`，不读取或伪造控制时钟，不携带公示时间、内部原因、操作者、请求号或金额。即使存在旧公示也返回同一精简形态；没有最终结清证据时不得使用 `CLOSED` 或“已结清”。老板端公告和两端共享入口据此区分“暂未开放”与“已停用”，HTTP 403／404／503 和 UNKNOWN 不得推断成 DISABLED。

## 七、验证与非目标

- 覆盖独立权限、双店同数字 ID、非待结清、控制／店库故障、读取零写入和响应脱敏。
- 覆盖 READY、FROZEN、PAID、新增余额、RELEASED 后仍有余额、合法零余额、来源／迁移待核和待投递／审计异常。
- 覆盖进行中订单、预约、开放售后、未结收益、各类在途提现及无法核验的其他义务。
- 前端覆盖会话／门店变化、迟到响应、四态、未知金额和无最终动作；最终运行三端类型、测试和构建。
- 真实 MySQL 行锁、共享资金屏障、费用审批、最终结清／停用和跨库恢复留后续规格及 M7；本波签收不能写成这些能力已完成。
