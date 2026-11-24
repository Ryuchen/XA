# 声浪更名：后端、陪玩端与存量数据技术评审底稿

日期：2026-11-03。依据 `shenglang-rebrand-prd-20261103.md` 的 SHA-256 `6A0CF6559485F48D68421836AB4F4B73F3CAEC5E7775232EE70F8803E5021F9D` 只读审查。本文是供七角色讨论的实现建议，**未获评审通过，不代表已实施或已执行测试**。用户确认仅更名，1 元 = 10 浪花 = 100 内部账务单位，历史余额及数值不变；历史聊天与评价原文照常显示，上传凭证保留原件，系统文案及运营内容改为新名称。

## 展示数据分类与候选处理

| 数据来源和具体字段 | 已知读取 / 风险 | 推荐处理与待评审项 |
| --- | --- | --- |
| 应用固定文案、服务端新生成消息和错误 | 当前交付为 `apps/provider-vue`、`apps/admin-web` 和 `services/backend`；新消息后端创建并由站内消息接口读取。 | 按 PRD 更新展示字符串及测试断言，禁止改 API 键、金额公式或技术标识。新生成的标题、内容和失败信息以新名称输出。对未识别输出列清单，不凭源码搜索断言动态内容已完成。 |
| `announcements.Announcement.title/content` | 运营可编辑，`is_active` 控制当前公告；`seed_club_catalog` 以标题为键 `update_or_create`，不能直接改历史名称后重跑导入而不核查重复和覆盖。 | 先在隔离场景库只读清点在用公告，记录 ID、状态、更新时间、匹配字段和原文哈希，输出具体候选变更供产品与运营核对；采用可审计的版本化变更或经批准的展示覆盖，保留原文和操作记录。不可批量改写或将用户原话伪装为当时原文。 |
| `orders.ServiceItem` 的 `name/description/rules/details_html/purchase_note/highlights`、`Gameplay` 的标题/说明/规则 | `ServiceItemSerializer` 输出名称、介绍与封面；详情序列化器在 `rules` / `details_html` 缺省时可能回落 `Gameplay`。后台可维护内容；HTML 和 JSON 混有文字与链接。 | 分字段按当前上架状态和 fallback 关系清点，保留原值与内容哈希；只转换已核验的目标文本节点，URL、HTML 属性、模板变量、游戏专名和金额不动。必要时以结构化的逐项映射覆盖展示，而非全表 `replace`；修改已有运营正文须有可追溯前后版本及并发版本检查。 |
| `site_messages.Message.title/preview/detail`、`type` 与 `related_order_id` | `MessageListItemSerializer` 暴露 `preview`，详情同时暴露 `detail`；`SYSTEM/ORDER/SUPPORT/PROMOTION` 分类本身**不能**证明是系统模板或用户原话。`action_url` 是导航信息。 | 用户已确认历史聊天与评价原文照常显示、上传凭证保留原件。对于能核实为系统文案的历史消息，建议逐条建立“消息 ID + 字段 + 原文哈希 + 展示值 + 审核人/时间/原因”的可追溯覆盖；请求时必须先按原规则授权，再应用仍匹配原文哈希的覆盖。原文变化则停用该覆盖并列异常；不能自动改写聊天、评价原话、上传凭证或审计原件。来源不明先核实，不以消息类型自动判断，不将全局字符串替换当作安全方案。 |
| `console.KookDispatchConfig.routes` 内的 `template` 及 `provider_order_url`；派单历史/探针 | 已保存路由可覆盖代码默认模板，JSON 中混合频道、变量、URL 和文案；模板由后台配置。 | 在隔离库只读列配置 ID、revision、路由标识及模板是否命中；不输出 token 密文、收件人或正文隐私。保留路由、频道、变量、URL，具体模板由有权限运营核对并按版本机制修改；只向隔离接收端测试。已发送记录作为历史证据保留，不重新发送。真实平台的现有模板不可由本地源码变更推断完成。 |
| `wallet.Transaction.remark`、`RechargeRecord`、`WithdrawRequest.remark/audit_remark`、`orders.Order.remark` 与退款原因 | 自由文本可能包含操作人、客户描述、凭证号、承诺和历史金额；来源不能靠关键词可靠判断。 | 原文和资金记录保持不变；结构化金额单位由展示代码提供“浪花”。自由文本列核查结果和可执行的精确覆盖项，未经核实不自动替换客户输入、流水号、原因或审计证据；不可通过改文案改变金额与交易状态。 |

清点过程只访问本轮持久合成场景库，不连接真实客户或生产数据库；报告默认只含类型、计数、合成记录 ID 和哈希，不记录真实正文、密钥或收款信息。未来处理实际运营内容须先核实数据归属、访问权限及变更机制。历史聊天和评价原文照常显示、上传凭证保留原件；系统文案与运营内容更名。来源不明及可审计编辑实现须逐项核实，未解决项保持未完成，不静默跳过。

## 商品图片及引用

`orders/data/club_catalog_20260922.json` 顶层 `brand/currency` 含旧字样；商品 `cover` 是历史相对文件名，并不等同当前线上 URL。`orders/management/commands/seed_club_catalog.py` 仅在明确允许的本地数据库和提供 `--asset-root` 时读取文件，以内容 SHA-256 前 24 位生成 `club-catalog/` 媒体名，再设置 `ServiceItem.cover_url`；同时导入 `rules`、`Announcement.content`。该命令当前不允许直接对 `xa_round01` 执行，不能绕过守卫对持续库重跑。

按以下步骤形成可验证映射：①从合成场景库列出 `ServiceItem.cover_url/images/rules_images/introduction_images` 和相应 `Gameplay` 图片引用、活跃状态与页面位置；②按实际加载的图片检查旧标志/文字，记录来源与内容哈希；③UI/UX 确认可用的替换视觉和比例后，建立“旧内容哈希/引用记录 → 新资源哈希/相应对象与字段”的映射，保留原始证据和旧路径，不用搜索替换 URL；④验证三端页面读取、图片响应、无障碍名称、缓存更新及必要回退。缺失资源、受版权/外部权限限制或原始图片是凭证时标为未完成，不以更名文件名或目录文本代替图像更名。工作区陪玩端 `public/brand.svg` 已出现暂用声波图形，须由 UI/UX 按用户指定技能正式审查；不得据此称所有旧标记已更名。

## 持续场景与金额不变：可执行对照设计

1. 保留 `/var/lib/xa-round01/state` 所属 Compose 项目的持久 MySQL 和媒体卷；不执行清库、重新 seed 或覆盖用户场景。每轮变更前先取得一致性数据库备份及媒体快照，记录项目名、迁移状态、备份校验值与镜像 ID，验证可恢复到**单独项目和卷**。失败时保留原卷并停止升级。
2. 在隔离容器内通过真实接口创建并保留两组确定的合成场景：基础账本覆盖充值、下单、折扣券、退款、押金、冻结、报单收益及提现；另一组覆盖零值、非整浪花内部单位及失败/幂等重试。记录相关合成账号 ID、订单号、交易号及预期；不伪造核心业务结果，不向真实渠道打款或发消息。
3. 停止对被对照数据写入并等待真实 worker 收敛，在同一数据快照分别由可识别的更名前后版本读取；更名前镜像或对应源码、迁移不具备时明确记录**无法证明前后行为一致**，不能将当前空库当旧数据。将快照恢复到两个互不相连的隔离副本，分别执行对应版本的真实迁移和相同只读查询；禁止两个版本同时写同一业务库。单元测试数据和一次性测试库另行隔离，不取代持续场景库。
4. 以主键排序逐行比较和汇总校验，至少覆盖：`wallet.Wallet` 的 `balance/frozen_amount/total_recharge/total_gift`；`wallet.Transaction` 的 ID、钱包/订单、`tx_no/tx_type/status/amount/balance_before/balance_after`；`wallet.RechargeRecord` 的充值/赠送金额及关联流水；`wallet.WithdrawRequest` 的 `amount/tax_rate/tax_amount/actual_amount/status/transaction`；`users.EscortProfile.deposit_required/deposit_paid`；`orders.Order` 的 `amount/original_amount/boss_discount/promo_discount/coupon_discount/unit_price_snapshot/provider_income/inviter_commission/shop_income/status/payment_status/settled_at`；`orders.OrderProvider` 的 `settlement_base/provider_income/settled_at`；`orders.OrderRefund` 的 `amount/recovered_amount/negotiated_allocation` 与 `RefundRecoveryLine`；`wallet.ProviderReport.amount/payout_amount/status`、`IncomeHold.amount/deposit_amount/recovered_amount`；`coupons.Coupon` 的 `threshold/amount/discount_rate/max_discount` 和 `UserCoupon.status`。同时记录行数、主键、关联及关键非金额状态。导出仅限合成数据并排除收款账号、令牌与个人敏感字段。
5. 当前 `scripts/fingerprint-finances.py` 只覆盖钱包、流水、提现和订单的部分列，并会静默忽略库中缺失字段；**不足以证明上述范围一致**。验收工具应对预定字段缺失显式失败或标不可比较，输出被比较字段、版本、行数和逐行或规范化哈希；任何数值、精度、身份关联或流水数异常均失败，不能只比总数或只比 UI 标签。检查新增 `coupons.0012`、`users.0035` 与前置迁移状态及模型差异，再核对重试不重复写入。

## 需要产品和评审会明确

- 系统文案及运营内容的具体“展示映射”或可审计编辑方案、字段和授权界面；无法可靠判定来源的记录需先核实，不得自行改写原意。历史聊天、评价原文照常显示，上传凭证保留原件。
- 品牌素材和 UI 方案、逐项在用资源清单与素材来源；外部平台的访问与更新依赖，不能伪称已覆盖。
- 两份前后对照版本、可恢复副本、对照工具和持久场景夹具的具体责任人与证据保存位置。未取得基线时作为测试阻塞，不能认定通过。

本底稿没有进行数据库查询、测试、开发或任何外部平台操作。业务实现及测试须等待七角色针对最终同版 PRD 全部确认。

（微信小程序相关内容已按用户 2026-11-17 决定删除，git 历史可追溯。）
