# SPEC-E2 闭店新增入口守卫与逐户余额返还

2026-11-25 第二波执行规格。依据开发 29 第 07 项、PRD 39 SA02、PRD 37 §3.4 及根指南跨库与资金规则。

## 一、新增入口守卫

- 闭店公示中、待结清及闭店证据待核，禁止新增充值、通行证购买、公开老板/陪玩注册、审核新建陪玩、后台新建老板/陪玩/员工及其他明确的新账号或新权益。
- 已有资金回执、原审核记录、退款、提现、履约和读取保持；守卫不得放入通用钱包入账、订单状态机或退款公共函数。
- 统一拒绝 `409 STORE_CLOSURE_RESTRICTED`；控制库状态不可读返回 `503 STORE_CLOSURE_STATUS_UNAVAILABLE`。不得因守卫绕过原角色权限、优惠、资金或合规校验。
- 尊享卡尚未实现，不创建伪开卡入口；将来开卡必须复用同一守卫。

## 二、逐户余额清点和可退范围

- 仅平台授权角色可在门店进入 `SETTLEMENT_PENDING` 后读取老板账户清单。账号按 `account_id + business_cycle` 定位，退店旧周期与新周期不得混合。
- 清单分别展示钱包、可核验本金、赠送、赔付、积分、已冻结和已登记实付。任何来源缺失、混合充值赠送快照不明、钱包与批次不一致或读取失败，金额为 `null` 并进入 `REVIEW_REQUIRED`，不得显示零。
- 首波只允许“全部余额均可追溯到本账号周期内真实充值记录、成功收款流水和必要收款凭证，且无赠送/赔付、无未知混合批次、无在途赠送审批”的账户建立全额本金返还单。`PREPAID`/`RECHARGE` 只是分类，不能单独充当现金凭证；推荐佣金、手工调账、历史推定 `prior-balance` 和其他无原充值证据的余额一律待核。按现行固定换算，本金内部单位数值等于人民币分；清单仍保留原单位，禁止浮点换算。
- 积分只作非现金事实展示，不自动折算现金。赠送、赔付、来源不完整、在途赠送审批或跨周期异常不得自动返还。

## 三、冻结、实付与解除

- 建立返还单前重验闭店状态、账号周期、钱包和全部本金批次；客户端须提交预期钱包和本金，锁内不一致返回 409。
- 冻结在同一店库事务内减少钱包可用余额、增加冻结额，同时把每个原批次的 `remaining` 可用额移入返还单的独立冻结批次记录；保存幂等回执、资金流水和待办事件，不得修改历史流水。冻结后其他付款只能使用后来新增的可用余额，不能继续花原冻结批次。
- 冻结后发生原路退款时，新退款可增加原批次当前 `remaining` 和钱包可用余额，但不属于原冻结份额。实付只结清返还单中的原冻结批次，不要求当前批次仍等于冻结快照；解除只把原冻结份额加回钱包和对应批次，后来退款或消费应分别保留，不能相互卡死。
- 登记实付只接受等于原可退本金的金额和线下凭证摘要/安全引用。已入账事实不可改，重复请求只回原结果；`channel_status=NOT_REQUESTED`，不代表银行或支付渠道成功。
- 未实付的冻结单可凭原因和原请求解除，精确恢复原冻结份额和原冻结凭证；已实付不得解除。差错用补偿事件，禁止覆盖历史。实付后新产生的可用余额必须继续可见并可另行清点，不能被旧 PAID 快照遮蔽或永久阻止后续返还。
- 跨控制库审计与店库写入分别留提交状态；结果未知按原请求恢复，不能重复冻结、解冻或登记实付。

## 四、平台 API 契约

路由统一使用 `/api/admin/platform/store-management/<store_id>/closure/returns`：

### `GET /`

响应 `{code:0,data:{list,total,page,page_size,has_more,summary}}`。每行字段固定为：

- `case_id`、`account_id`、`business_cycle`、`account_label`
- `status`: `READY_TO_PREPARE` / `REVIEW_REQUIRED` / `FROZEN` / `PAID` / `RELEASED`
- `wallet_units`、`principal_units`、`gift_units`、`compensation_units`、`points_hundredths`
- `frozen_units`、`refund_fen`、`paid_fen`
- `version`、`blocking_reasons`、`updated_at`

金额未知必须为 `null`。`summary` 包含 `complete`、各状态数量及合计；任一店/账户读取不完整时 `complete=false`，相关总计为 `null`。

### `POST /prepare`

请求严格为 `account_id`、`business_cycle`、`expected_wallet_units`、`expected_principal_units`、`client_request_id`。成功返回完整行及 `replayed`、`wallet_balance`、`wallet_frozen_units`。

### `GET /<case_id>/`

返回原账户行、不可变事件列表和 `channel_status`。事件不含完整银行资料或凭证正文。

### `POST /<case_id>/record-payment`

请求严格为 `amount_fen`、`external_reference`、`proof_sha256`、`occurred_at`、`client_request_id`。返回完整行、`channel_status=NOT_REQUESTED`、钱包/冻结结果及 `replayed`。

### `POST /<case_id>/release`

请求严格为 `reason`、`client_request_id`。只允许未实付冻结单，返回解冻后的完整行、钱包结果及 `replayed`。

所有列表和详情响应 `Cache-Control: no-store`，平台操作仍校验 `closure:view` 或 `closure:manage` 与门店 scope。

## 五、后台页面

在现有“保证金与闭店”中增加余额返还页签：清单、五种状态与待核原因、本金/非本金/积分分列、准备冻结、登记实付和未实付解除；`PAID` 与 `RELEASED` 均为只读终态。写前保存完整请求和请求号；网络/5xx 后先按原号恢复，不换号。平台 scope/门店/账号周期变化清理旧快照。无最终停用、手续费或真实银行成功假入口。

## 六、验证与非目标

覆盖权限、双店同数字 ID、账号周期、30 天状态、来源完整/不完整、准备冻结竞争、同号异内容、实付/解除互斥、钱包/批次/流水对账、审计失败恢复和公开脱敏；三端契约、类型和构建。

本波不实现尊享卡本身、闭店手续费、部分退还、赠送/赔付折现金、真实银行付款或最终停用。真实 MySQL、Worker 和浏览器另留联调记录。
