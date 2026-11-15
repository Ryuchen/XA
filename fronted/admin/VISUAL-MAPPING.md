# 管理后台原型迁移映射

更新：2026-11-06。原型根目录为 `design-demos/shenglang-web-prototype/app/src`；实际代码根目录为 `fronted/admin/src`。下表的原型名均对应 `pages/admin/<名称>.tsx`。本文件记录源码和接口映射，不把类型检查、原型场景清单或页面数量当成真实业务状态验收。

## 框架与公共状态

- `components/admin/AdminPrototypeShell.tsx` → `App.tsx`：72px 收缩／232px 展开左栏、展开锁定与刷新恢复、64px 顶栏、一级业务域和当前域二级页面导航、`main min-w-0 p-4 md:p-6`；搜索移到顶栏，支持 Ctrl+K，并只展示当前权限允许的功能。账号菜单显示真实身份、权限说明与退出确认；保留实际菜单、角色过滤、站内消息和通知。门店选择集中顶栏和登录页的 AdminStoreSwitcher；门店管理页保留管理入口。
- 原型 `index.css` / `components/ui` → 共享主题与本端基础组件。最新用户覆盖优先：根圆角8，控件6–8、badge4、卡片8–12；业务控件按区块及主次选尺寸；已取消全局medium限制。`App.css` 不再对桌面筛选区强制44。
- 页头按对应原型分别实现：工作台与登录使用原型普通标题；目录、订单、财务、客服等保留各自深色摘要与真实指标，不再以一个通用页头代替全部原型。必要业务说明直接靠近字段，解释性文案压缩。只标明“本页”的统计才从 `rows` 汇总，真实总数取接口 `total`，不复制演示全量统计。
- 本端 `Card` 保持原型 `text-sm` 的14/20正文、上下24px及24px内部间距，`CardHeader` 按实际 grid 行为迁移；表头48px/左右12px，单元格12px padding。身份与状态默认按原型靠左，金额/数量/费率靠右，动作靠右；明确指定的列对齐优先。
- `DataPageState`：按显式 `skeleton` 键与源 `scope` 选择整页或局部状态；首次 `loading && !hasSnapshot` 只替换对应数据区，整页首次错误给居中错误卡与重试；有真实快照才保留统计和列表。骨架按各原型分支逐项移植，不再用list/settings/dashboard三类代替。`DataTable` 对读取中/失败快照的行操作使用禁用 fieldset，横向滚动在表格区域内。
- `Feedback` / `LoadingPanel` / `DataTable`：错误、详情骨架、空列表为真实请求条件；空列表不展示推测记录。`ReadOnlyNotice` 由当前权限生成，额外独立删除权限保留。
- `MutationDialog`：标题/关闭、可滚正文与固定操作区分区；DialogTitle按原型16/500，AlertDialogTitle为18/500。提交中禁用字段和确认并显示进行反馈；关闭已修改表单前确认放弃，结构增删/排序采用实际草稿快照，纯预览tab不触发。只读同时锁字段、按钮和处理函数。`trackReview` 在请求前按门店和身份保存结果待核对记录，网络/408/409/429/5xx不当成成功；业务字典也纳入集中核对。
- `OptionPicker` / `QualificationFilters`：选项加载、失败、重试、禁用状态独立。支持的接口保留服务端搜索、分页；`/club-rewards/` 没有搜索能力，选择器采用真实分页浏览，不展示无效搜索。资源/商品/轮播编辑器在可见选项读取失败时不能保存。
- 普通枚举、单选业务选项和分页大小使用本端现有shadcn Select；空选项在边界还原为空字符串/null，不把UI哨兵值发送接口。单选ID继续为数字；业务多选保留原有多选语义。
- `PrivateImage`：签名私图加载骨架、刷新签名、失败重试、无材料状态；不把原型示例证件放入生产。

## 全部原型页面、详情与表单

| 原型组件 | 当前路由 / 实际组件 | 布局与视觉证据；保留的业务差异 |
| --- | --- | --- |
| AdminLoginPage | `/login` → `pages/Login.tsx::LoginPage` | 双栏品牌区、右侧登录卡、OTP/绑定二维码/恢复码；保留真实密码+TOTP挑战和恢复码确认，删除演示场景选择。 |
| AdminDashboardPage | `/dashboard` → `pages/Dashboard.tsx::DashboardPage` | 三列角色待办、三种视角各四项指标、账户规模、220px真实Recharts柱图、状态条、最近四笔订单表；只使用 `/api/admin/dashboard/stats` 真实允许指标，权限缺失不凑数。最近订单CardHeader保留原型实际grid，不推测TSX的flex-row意图。 |
| AdminOrdersPage | `/orders` → `pages/Orders.tsx::OrdersPage` | 页头操作、状态/队列筛选、带金额与多状态徽标的宽表；真实订单字段、双陪/团队凭证、退款与派单全部保留。 |
| AdminOrderDetailPage | `/orders?detail=<id>` → `Orders.tsx::OrderDetail` | 深色身份摘要、主履约区/右侧金额区、`minmax(0,1.5fr)` 双栏、证据与时间记录；实际详情为1120级弹窗，保留跨列表返回位置。 |
| AdminProvidersPage | `/escorts` → `pages/People.tsx::EscortsPage` | 资料头像/昵称/编号组合、状态徽标、资格筛选和宽表；原有奖惩、押金、停用/封禁、技能与认证操作不删除。 |
| AdminProviderDetailPage | 陪玩“详情” → `People.tsx::EscortDetail` | 深色头像摘要与真实评分/完成数，资料/技能主栏+340px押金/材料侧栏；使用真实 `GET /admin/escorts/{id}/`，所有字段来自 `AdminEscortSerializer`。 |
| AdminRegistrationsPage | `/provider-registrations` → `People.tsx::RegistrationsPage` | 审核状态与搜索、编号/身份/押金/提交时间列表；真实 PENDING/APPROVED/REJECTED。 |
| AdminRegistrationDetailPage | 注册“核对资料” → `People.tsx::RegistrationsPage` 内详情/审核弹窗 | 深色申请摘要、资料左栏/私图及审核右栏、确认后开通；保留真实 `revision`、实名核对、CASH/EARNINGS押金差异、人民币现金录入和恢复码流程。等级、实名和押金核对保持真实校验。 |
| AdminLevelsPage | `/escort-levels` → `pages/Catalog.tsx::ResourcePage` + `QualificationScopeSummary` | 等级图标、身份摘要、自动接单范围卡、配置表、真实删除影响弹窗骨架/错误/三类关联数；关联字段均为SET_NULL，读取影响后按真实权限允许确认清空；原型后果提示对应现有模型，不另加业务限制。各档抽成直接取真实STAFF元数据commission_rate，按原型折叠卡展示。 |
| AdminPassesPage | `/provider-passes` → `pages/Settings.tsx::PassesPage` | 档位卡网格、价格/延迟、无证延迟、购买记录；配置与购买记录分别处理加载和失败，保存中/保存失败内联状态，真实keyword/tier参数保持。 |
| AdminDisposesPage | `/dispose-records` → `pages/Financials.tsx::RecordsPage` | 奖惩筛选、账户/订单关联与金额宽表；保留实际奖励/扣款/押金退回类型和账务链路。 |
| AdminAuditionLinksPage | `/audition-links` → `pages/Support.tsx::AuditionsPage` + `AuditionEditor` / `AuditionAction` | 活动列表、招募编辑、账号绑定选择器和入口弹窗；真实占位入口不可复制，绑定带 `expected_updated_at`，保留搜索失败/结果待核对。 |
| AdminAuditionSignupsPage | `/audition-signups` → `Support.tsx::AuditionsPage` / `AuditionSignupDetail` / `AuditionAction` | 报名宽表、只读详情、最新报名资料卡与审核；真实读取后才可审核，预读失败/并发409/未知结果分别显示且锁定。 |
| AdminWithdrawPage | `/withdrawals` → `Financials.tsx::WithdrawalsPage` / `WithdrawConfig` | 待审核摘要、筛选表、收款/领取/处理详情与确认；保留领取人、actual payout、claim token和审核权限，读失败弹窗可重试。 |
| AdminWalletPage | `/wallets` → `Financials.tsx::WalletsPage` / `WalletMutation` | 账户余额、平台钱包区、充值/赠送/调账表单及后果核对；人民币与浪花转换和幂等请求保持，禁用的平台补入不伪造成功。 |
| AdminTransactionsPage | `/transactions` → `Financials.tsx::RecordsPage` | 流水筛选、冲正徽标/原流水详情、订单权限链接；日期和金额不强行折行，宽表独立横滚。 |
| AdminRechargesPage | `/recharge-records` → `Financials.tsx::RecordsPage` | 充值身份/交易号/实充赠送/凭据表；私图独立骨架、错误、无凭据，原始交易信息保持。 |
| AdminReservationsPage | `/reservations` → `pages/Reservations.tsx::ReservationsPage` / `CreateReservation` | 预约状态列表、创建与转履约确认、报价卡；只使用真实 `allowed_actions` 与报价，余额不足时不能转单。 |
| AdminPaymentFeePage | `/payment-fee` → `Settings.tsx::PaymentFeePage` | 深色头、3:2商户费率/已保存配置试算两栏、历史表；真实 `revision` 与服务端试算，保留未配置/并发/历史独立错误。 |
| AdminChatPage | `/chat` → `Support.tsx::ChatPage` | 320px会话栏、73px消息头、头像/气泡/阴影、未读筛选、消息图、历史阅读与回到最新；真实ORD完整编号链接只对 `order:view` 开放。只读不发已读/回复写请求；待发送保留原 `client_message_id` 并查询状态。 |
| AdminReportsPage | `/reports` → `Financials.tsx::ReportsPage` | 审核列表、详情左侧业务/右侧资金、三类私密凭证；真实封锁/售后/协商报酬状态使用 `reportPayoutDisplay`，不把未结算金额当实得。 |
| AdminCommissionPage | 报单页“默认抽成” → `components/CommissionEditor.tsx::DefaultCommission` | 5xl双栏配置/已保存快照；真实 GET/PUT `/config/commission`。原型独立页和演示试算未照搬为新后端功能，实际派单报价仍在 `OrderDispatch.tsx`。 |
| AdminServicesPage | `/service-items` → `Catalog.tsx::ServicesPage` | 游戏卡筛选、商品身份图标、价格/范围/抽成宽表、独立游戏/分类子页；真实所有目录和分账字段保留。 |
| AdminServiceFormDialog | “编辑套餐” → `Catalog.tsx::ServiceEditor` | 按原型4xl与三组tab组织编辑及预览；实际计价人数、资格、券/礼物、转盘和多图字段保留，不删除字段迎合演示数据。 |
| AdminGameplaysPage | `/gameplays` → `Catalog.tsx::ResourcePage` | 封面/名称/标识、归档筛选、状态Switch、归档/恢复；真实 `is_archived` 参数，未提供的keyword/kind筛选保留待开放。 |
| AdminGameplayFormDialog | 玩法“新增/编辑” → `Catalog.tsx::ResourceEditor` | 4xl、入口/规则图文/展示三分区、250px预览侧栏；真实上传与富文本白名单，不采用演示图片。 |
| AdminBannersPage | `/banners` → `Catalog.tsx::BannersPage` | 126×54封面+阴影、轮播表标题图标、类型/目标/可用性、启停Switch、原型筛选区；服务端不支持的三个筛选禁用待开放。 |
| AdminBannerFormDialog | 轮播“新增/编辑” → `Catalog.tsx::BannerEditor` | 5xl、290px预览、图片与跳转配置；真实750×320尺寸校验、multipart上传、目标元数据错误阻止提交。目标可用性取 `link_available`，不猜测缺失目标原因。 |
| AdminAnnouncementsPage | `/announcements` → `Catalog.tsx::ResourcePage` | 标题/图标/内容摘要、置顶/状态/时间、Switch、排序；keyword/pinned/status服务端未支持，筛选待开放。 |
| AdminAnnouncementFormDialog | 公告“新增/编辑” → `Catalog.tsx::ResourceEditor` / `ResourcePreview` | 5xl、310px官方公告预览、深色标题/时间/置顶、正文/排序展示两分区；实际富文本编辑与净化保留。 |
| AdminContentsPage | `/club-operations?tab=content` → `Operations.tsx::operationsSpecs.content` + `ResourcePage` | 内容身份、类型/时间窗/状态列表、筛选占位；真实kind/启停编辑保留，活动分类查询待接口开放。 |
| AdminContentFormDialog | 运营内容编辑 → `Catalog.tsx::ResourceEditor` / `ResourcePreview` | 5xl、310px内容预览、基础内容/展示窗口分区；真实图片上传、图文、nullable起止时间保留。 |
| AdminBroadcastRulesPage | `/club-operations?tab=rules` → `operationsSpecs.rules` + `ResourcePage` | 事件、宽390模板、时长Clock图标、排序、昵称保护和Switch；原型列表筛选待接口开放，后端不提供DELETE，保留原有默认规则管理。 |
| AdminBroadcastRuleFormDialog | 播报规则编辑 → `ResourceEditor` / `ResourcePreview` | 5xl、310px模板预览、事件模板/展示隐私分区；显示实际模板变量，不用原型假账号生成预览事件。 |
| AdminBroadcastRecordsPage | `/club-operations?tab=feed` → `Operations.tsx::VisibilityRecords` | 历史表、事件/公开状态筛选占位；没有后端聚合时不复制原型全量公开/到期统计。 |
| AdminBroadcastRecordFormDialog | 播报“编辑展示” → `VisibilityRecords` 内编辑弹窗 | 5xl、310px深色动态预览、只读正文/发布时间/开始展示卡；保留实际排序，截止转成有效datetime-local再回传ISO，业务事件正文不可修改。 |
| AdminRewardsPage | `/club-operations?tab=rewards` → `operationsSpecs.rewards` + `ResourcePage` | 好礼图标/封面、库存/已兑换/身份、启停Switch、交付类型/身份筛选占位；没有服务端过滤时不筛当前分页。 |
| AdminRewardFormDialog | 好礼编辑 → `ResourceEditor` / `ResourcePreview` | 6xl、290px商城预览、基础/兑换权益/库存展示三个分区、真实已兑换数只读；`redeemed_count`并发校验和活动引用禁止修改均保留。预览不伪造会员积分。 |
| AdminLotteriesPage | `/club-operations?tab=lotteries` → `Operations.tsx::LotteriesPage` / `CampaignRuleCards` | 原型1.4:1规则卡、概率条/奖项分布、220px保底区、十连可用性卡、版本表；数据来自真实 `CampaignSerializer`。第一条仅标“活动快照”，不假称为全局开放活动。 |
| AdminLotteryFormDialog | 转盘“配置奖池” → `Operations.tsx::LotteryEditor` | 1240px、318px预览、规则与完整奖池；真实version、2–20项和总概率校验由当前服务端保持，已中奖奖项约束不删除。 |
| AdminClaimsPage | `/club-operations?tab=claims` → `Operations.tsx::ClaimsPage` | 四列状态卡/图标与筛选、交付表；真实status/source/kind/account_type/tag筛选保留。卡片明确本页汇总，非原型伪全局计数。 |
| AdminClaimProcessDialog | 权益详情/处理 → `ClaimsPage` 内1120px弹窗 | 左资料/私图与右操作/状态栏、TAG审核/停用；保留 `expected_details` / expected_status / terms_snapshot 三类冲突和积分退回后果。 |
| AdminOperationConfigPage | `/club-operations?tab=settings` → `Operations.tsx::OperationConfig` / `MutationInline` | 分区设置左侧+360px保存前核对；真实固定业务配置仍禁用、赛季字段校验、保存结果全局核对，不开放原型未授权写项。 |
| AdminStoresPage | `/stores` → `pages/Stores.tsx::StoresPage` / `StoreEditor` | 沿用真实门店登记/入口/状态表和确认，按现有平台授权使用；独立业务库选择、初始化限制、不能停用当前店保留。遵循用户门店功能不重新设计的约定。 |

`AdminClaimsPage.tsx::closeDialog` 是内部函数，不是额外交付页面，不计入44个页面/详情/表单映射。

## 原型之外的实际页面及弹窗

| 当前文件 / 路由 | 同套视觉来源与保留能力 |
| --- | --- |
| `Dashboard.tsx::PlayerDashboardPage`，`/player-dashboard` | Dashboard指标/图表/排名扩展；保留陪玩排行真实接口和权限。 |
| `People.tsx::UsersPage/UserEditor/AccountAction`，`/users` | Providers的身份表、分区编辑/确认；真实推荐关系、停用/封禁结果和活跃业务限制保留。 |
| `People.tsx::EscortEditor/QualificationsEditor/EscortMutation` | ProviderDetail资料/资格/资金分区扩展；保留资格矩阵、奖惩、单抵/现金、退押金与幂等。 |
| `Settings.tsx::DepositRulesPage`，`/deposit-rules` | Passes规则卡/保存核对风格；不更改押金范围或金额单位。 |
| `Settings.tsx::MembersPage/MemberEditor`，`/admins`；`RolesPage/RoleEditor`，`/roles`；`AuditPage`，`/audit-logs` | Services配置表与分区表单扩展，角色权限网格；现有成员授权、独立角色和审计过滤完整保留。 |
| `Catalog.tsx::ResourcePage/Editor`，`/boss-types`、`/achievements`、`/support-cards` | Levels/Announcement/Content分区表单及预览扩展；老板分级折扣、成就金额目标转换、客服二维码/企业微信保持。 |
| `Catalog.tsx::ServicesPage` 子页 `games/categories` | Services列表与ResourceEditor扩展；实际游戏/服务分类、礼物分类字段保留。 |
| `Catalog.tsx::PromotionsPage/CouponsPage`，`/promotions`、`/coupons` | Rewards配置表/ResourceEditor和领取记录风格；真实促销适用范围、券模板和领取记录保留。 |
| `Support.tsx::EvaluationsPage/MessagesPage/PushMessage`，`/evaluations`、`/messages` | 审核列表/消息列表/分区弹窗；保留审核与真实接收人选择和发送确认，不调用演示广播。 |
| `Operations.tsx::TiersPage/TierEditor`，`tab=tiers` | Levels配置表、5xl权益编辑分区；消费门槛、服务绑定、月券/生日/赛季权益真实字段保留。 |
| `operationsSpecs.packages/giftBenefits`，`tab=packages/giftBenefits` | Rewards配置和Content展示窗口扩展；充值档位、赠礼附送、累计/一次性规则不造新后端。 |
| `VisibilityRecords`，`tab=namings` | BroadcastRecords双栏记录编辑；实际只可修改冠名正文和显示，不能改到期快照。 |
| `ExternalPoints/MutationInline/KookStatus`，`tab=external/kook` | OperationConfig分区表单和状态卡扩展；来源编号幂等、真实连接状态和权限保留。 |
| `Operations.tsx::CheckinPage`，`/checkin` 的rule/gifts/progress | Passes设置/Rewards目录/记录表扩展；按当前选择页读取状态，实际签到礼品、规则与进度保持。 |
| `KookDispatch.tsx::KookDispatchPage/RouteEditor`，`/kook-dispatch` | OperationConfig设置、通道表与确认；测试消息的“非真实订单”后果说明保留。 |
| `Dictionaries.tsx::DictionariesPage/DictionaryEditor`，`/dictionaries` | Levels配置表和分组表单扩展；真实字典类型/停用/引用保护不删。 |
| `Payroll.tsx::SalaryPlansPage/PlanEditor`，`/salary-plans` | ServiceForm分区与真实条款卡扩展；岗位薪资独立于ACL，人民币底薪/绩效/提成保持。 |
| `Payroll.tsx::SalaryAssignmentsPage/AssignmentEditor`，`/salary-assignments` | Members身份列表/配置表单扩展；按人员和生效月分配、历史版本与确认保留。 |
| `Payroll.tsx::SalaryActivitiesPage/ActivityEditor`，`/salary-activities` | Disposes事实记录/业务归属表单扩展；真实来源核实、考试50元扣30%实得35元逻辑保持。 |
| `Payroll.tsx::PayrollPage/NightConfigEditor`，`/payroll` | Reports审计表/PaymentFee版本配置扩展；月份切换不展示上一月金额为当前月，未确认成本和北京时间夜班配置保持。 |
| `OperatingCosts.tsx::OperatingCostsPage/CostEditor`，`/operating-costs` | Records表/分区表单扩展；人民币投流和其他成本原字段保持。 |
| `Roi.tsx::RoiPage`，`/operating-roi` | Dashboard指标/PaymentFee双口径卡扩展；口径说明收进可点击区，缺成本/未确认薪资警告显式保留。 |
| `Orders.tsx::AssignDialog/OrderAction/TransferDialog`；`OrderDispatch.tsx::DispatchDialog`；`OrderRefund.tsx::RefundDialog` | OrderDetail主信息/资金侧栏扩展；转单分配、原计划token退款预览、协商冻结报酬、幂等和人工核对完整保留。 |
| `OrderMessages.tsx::OrderMessages`；`OrderConfig.tsx::TimeoutConfig/TrafficConfig` | Chat消息/OperationConfig分区设置扩展；原有消息状态、流控、到期退款规则保持。 |
| `App.tsx` `/403` | 相同居中无权限空态、明确返回工作区按钮；保留 `canAccess` 重定向。未授权页不伪造只读数据。 |

## 状态映射约定

下节逐组列出原型全部33组、347个键。`loading/error/empty/refresh-error` 按页面/独立子资源作用范围处理；其他特殊状态在各组说明中给实际条件。“保留差异”属于业务边界或后端暂未提供能力，见 `PROTOTYPE-TODO.md`。后续已补充双店浏览器、局部故障和提交前中断验证，覆盖范围以文末证据为准，未逐个复现全部347个场景。


## 逐组 Scenario → 实际条件

| 原型 ScenarioOptions 组 | 全部键 | 实际条件与视觉实现 |
| --- | --- | --- |
| `adminLoginScenarioOptions` | `default`、`verifying`、`failure`、`otp`、`binding`、`recovery` | `Login.tsx`：无challenge为default；busy为verifying；error为failure；requiresOTP为otp；requiresOTPSetup为binding；真实recoveryCodes为recovery。不会通过切换演示状态绕过验证。 |
| `adminDashboardScenarioOptions` | `default`、`loading`、`error`、`refresh-error`、`support`、`finance`、`no-todos`、`refreshing` | `DashboardPage`：权限决定support/finance任务与可见指标，服务端队列均空为no-todos；有快照且请求中为refreshing，首次加载和刷新失败按公共状态。 |
| `adminOrdersScenarioOptions` | `default`、`staff`、`aftersale`、`loading`、`error`、`empty`、`refresh-error` | `OrdersPage/orderQuery`：staff队列映射actionable_staff、aftersale映射aftersale_open；状态与付款信息来自订单。首次无快照整页骨架/错误，有快照才保留并锁表格动作。 |
| `adminOrderDetailScenarioOptions` | `default`、`pending`、`in-service`、`aftersale`、`refunded`、`loading`、`error`、`refresh-error` | `OrderDetail`：pending/in-service/refunded取实际订单和支付状态，aftersale取售后记录；独立详情useLoad/LoadingPanel/Feedback保留读取和刷新错误。退款/转单有独立真实预览及确认。 |
| `adminProvidersScenarioOptions` | `default`、`available`、`unverified`、`loading`、`error`、`empty`、`refresh-error` | `EscortsPage`：available/unverified来自真实status/is_verified筛选；角色决定资料/认证/资金操作。列表加载与空结果使用公共状态。 |
| `adminProviderDetailScenarioOptions` | `default`、`unverified`、`busy`、`review`、`loading`、`error`、`refresh-error` | `EscortDetail`：is_verified为false展示认证警告，busy来自真实status；资金/状态处理进入现有EscortMutation并按真实权限显示。review由幂等资金请求与待核对记录承接，不能通过详情示例按钮伪造认证。 |
| `adminRegistrationsScenarioOptions` | `default`、`all`、`approved`、`rejected`、`loading`、`error`、`empty`、`refresh-error` | `RegistrationsPage`：query.status默认PENDING，空值为all，APPROVED/REJECTED对应已处理队列；来源为真实注册列表，不把原型样本当待审核数量。 |
| `adminRegistrationDetailScenarioOptions` | `default`、`earnings`、`approved`、`rejected`、`readonly`、`review`、`conflict`、`loading`、`error`、`refresh-error` | `RegistrationsPage`详情：deposit_mode决定earnings/CASH字段，status决定approved/rejected/待审核；registration:audit决定readonly；提交带revision，冲突/未知结果由服务器错误和PendingReviews阻止重复操作。押金后果与申请版本校验保留。 |
| `adminLevelsScenarioOptions` | `default`、`readonly`、`edit-only`、`inactive`、`save-review`、`delete-review`、`impact-loading`、`impact-error`、`scope-error`、`loading`、`error`、`empty`、`refresh-error` | `ResourcePage`：escort_level:edit与独立delete权限决定readonly/edit-only，is_active=false为inactive；deletion-impact读取中显示弹窗骨架，失败可重试且禁止删除；QualificationScopeSummary独立scope-error；save/delete-review由trackReview。删除关联按模型SET_NULL清空，影响未读成功时锁定确认。 |
| `adminPassesScenarioOptions` | `default`、`readonly`、`saving`、`save-error`、`loading`、`error`、`empty` | `PassesPage`：配置与购买列表独立；规则loading只出现4张h52骨架、error仅影响规则，购买记录仍可读取；saving锁表单，saveError保留输入，escort:edit缺失为readonly；empty仅购买记录。keyword/tier均接真实服务端参数。 |
| `adminDisposesScenarioOptions` | `default`、`rewards`、`penalties`、`account`、`loading`、`error`、`empty`、`refresh-error` | `RecordsPage(kind=dispose-records)`：真实处置类型/账户参数形成rewards/penalties/account范围，数据与冲正/账务链路保持；列表状态使用公共实现。 |
| `adminAuditionLinksScenarioOptions` | `default`、`readonly`、`edit-only`、`save-review`、`binding-review`、`binding-search-error`、`placeholder`、`loading`、`error`、`empty`、`refresh-error` | `AuditionsPage/AuditionAction`：audition:edit/delete各自控制readonly/edit-only；bind-boss/provider传expected_updated_at，绑定搜索失败由OptionPicker独立重试；placeholder字段使入口不可复制。save/binding-review由门店身份范围PendingReviews承接。 |
| `adminAuditionSignupsScenarioOptions` | `default`、`approved`、`rejected`、`readonly`、`review`、`conflict`、`read-error`、`loading`、`error`、`empty`、`refresh-error` | `AuditionsPage/AuditionSignupDetail/AuditionAction`：status为APPROVED/REJECTED展示审核快照，signup_audit控制readonly；详情和审核预读分别有loading/error/retry；PENDING才可提交，409置reviewConflict，网络/408/5xx置reviewUnknown并全局核对。 |
| `adminWithdrawScenarioOptions` | `default`、`pending`、`readonly`、`audit-only`、`review`、`conflict`、`read-error`、`config-error`、`loading`、`error`、`empty`、`refresh-error` | `WithdrawalsPage`：status=PENDING及真实claim state决定领取/审核；withdraw:view与audit权限控制readonly/audit-only，detailTarget保留读错误弹窗；config-error由WithdrawConfig独立读取显示；核对latest状态/领取token后才审核，unknown与PendingReviews锁重复。 |
| `adminWalletsScenarioOptions` | `default`、`readonly`、`review`、`conflict`、`read-error`、`platform-disabled`、`loading`、`error`、`empty`、`refresh-error` | `WalletsPage/WalletMutation`：钱包读取、身份、配置及allowed权限决定readonly/platform-disabled；入账前再读最新钱包/配置，unknown锁定并定位流水；冲突与读失败保留输入和错误，不显示模拟入账。 |
| `adminTransactionsScenarioOptions` | `default`、`wallet`、`order`、`reversals`、`original-error`、`original-loading`、`no-order-permission`、`loading`、`error`、`empty`、`refresh-error` | `RecordsPage(kind=transactions)`：wallet/order筛选接现有查询，冲正标识与reversal_of呈现reversals；source useLoad承接original-loading/error；order:view决定订单链接，有权限才读取订单。 |
| `adminRechargesScenarioOptions` | `default`、`wallet`、`trade`、`proof-loading`、`proof-error`、`no-proof`、`loading`、`error`、`empty`、`refresh-error` | `RecordsPage(kind=recharge-records)`：wallet_id/trade_no参数、真实交易字段与私图；PrivateImage用onLoad骨架、签名失败重试和urls为空的no-proof。 |
| `adminReservationsScenarioOptions` | `default`、`pending`、`confirmed`、`converted`、`readonly`、`create`、`create-review`、`quote-loading`、`quote-error`、`insufficient`、`convert-review`、`loading`、`error`、`empty`、`refresh-error` | `ReservationsPage/CreateReservation`：状态和allowed_actions决定pending/confirmed/converted/readonly；quoteLoading/quoteError仅报价区，balance_sufficient=false阻止转履约；创建/转单保留client_request_id及unknown；原型状态不可成为客户端强制转单依据。 |
| `adminPaymentFeeScenarioOptions` | `default`、`readonly`、`forbidden`、`unconfigured`、`saving`、`save-error`、`conflict`、`save-review`、`preview-loading`、`preview-error`、`history-loading`、`history-error`、`history-empty`、`loading`、`error`、`refresh-error` | `PaymentFeePage`：payment_fee:view不足由路由403或接口错误承接forbidden，edit不足为readonly，configured=false为unconfigured；confirming MutationDialog处理saving/save-error，提交revision；服务端previewBusy/error与history独立useLoad处理试算/历史各状态；未知保存由PendingReviews承接。 |
| `adminChatScenarioOptions` | `default`、`unread`、`readonly`、`no-order-permission`、`session-loading`、`session-error`、`refresh-error`、`empty`、`message-loading`、`message-error`、`history-loading`、`history-error`、`history-browsing`、`sending`、`send-error`、`pending`、`pending-unsaved`、`pending-unknown` | `ChatPage`：keyword/only_unread是真实列表查询，chat:reply不足不发reply/read；order:view不足不渲染ORD链接。列表、当前消息、旧页错误各在其区域显示；older读取保持滚动位置，history-browsing有回到最新。sending锁输入，明确4xx保留草稿可改，408/409/429/网络/5xx保留pending；按门店/账号/会话sessionStorage恢复客户端编号，损坏/缺号记录展示pending-unsaved且只能核对消息后放弃；status未FOUND为pending-unknown。存储失败发生于请求前时不发送。 |
| `adminReportsScenarioOptions` | `default`、`pending`、`approved`、`rejected`、`blocked`、`aftersale`、`readonly`、`no-order-permission`、`review`、`conflict`、`detail-loading`、`detail-error`、`loading`、`error`、`empty`、`refresh-error` | `ReportsPage`：真实status与order_approval_block_reason形成pending/approved/rejected/blocked；aftersale与冻结/协商金额来自reportPayoutDisplay。report:audit和order:view分别控制审核/链接；详情独立骨架/错误，latest状态核对及PendingReviews处理review/conflict。 |
| `adminServicesScenarioOptions` | `default`、`readonly`、`edit-only`、`metadata-loading`、`metadata-error`、`save-error`、`save-review`、`loading`、`error`、`empty`、`refresh-error` | `ServicesPage/ServiceEditor`：service:edit/delete分别控制readonly/edit-only；OptionPicker/QualificationFilters元数据独立加载/错误/重试；可见选项未就绪时编辑器不能提交；字段错误保留输入、上传失败可重试；save-review由全局真实核对记录代替演示横幅。 |
| `adminGameplaysScenarioOptions` | `default`、`archived`、`readonly`、`edit-only`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `ResourcePage/ResourceEditor`：is_archived真实参数形成archived；已归档无法通过Switch启用，只能先restore；权限独立；上传/字段/请求失败保持表单，归档/恢复错误显示原结果；save-review由trackReview。关键词/kind筛选待开放，非当前分页过滤。 |
| `adminBannersScenarioOptions` | `default`、`unavailable`、`readonly`、`edit-only`、`target-error`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `BannersPage/BannerEditor`：link_available=false为unavailable，真实目标OptionPicker失败为target-error并锁保存；banner:edit/delete分别控制readonly/edit-only；Switch不乐观改状态，PATCH成功再刷新，错误原地提示；表单保存核对、上传错误、全局save-review均保留。 |
| `adminAnnouncementsScenarioOptions` | `default`、`pinned`、`readonly`、`edit-only`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `ResourcePage/ResourceEditor/ResourcePreview`：is_pinned呈现置顶徽标/预览；announcement:edit/delete分别控制readonly/edit-only；富文本编辑、保存错误和未知结果使用公共实现；pinned列表筛选因API缺项为待开放，不暗中只筛本页。 |
| `adminContentsScenarioOptions` | `default`、`activities`、`readonly`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `operationsSpecs.content`：kind=HOT等真实类型在表格、编辑/预览展示activities；engagement:edit控制readonly；保存错误/启停错误保持服务器快照，save-review持久核对。kind和展示状态全量过滤暂未有API。 |
| `adminBroadcastRulesScenarioOptions` | `default`、`readonly`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `operationsSpecs.rules`：模板、时长、mask_nickname和enabled均真实；readonly基于engagement:edit；Switch动作失败内联错误，保存错误/待核对与公共机制一致。后端不允许DELETE，未新增删除入口。 |
| `adminBroadcastRecordsScenarioOptions` | `default`、`readonly`、`save-error`、`save-review`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `VisibilityRecords(kind=feed)`：只读事件正文、发布时间、开始时间；只能PATCH is_visible/expires_at/sort_order。engagement:edit控制readonly；表单失败保留输入，待核对由trackReview；记录筛选API未实现，控件标待开放。 |
| `adminRewardsScenarioOptions` | `default`、`readonly`、`edit-only`、`save-error`、`save-review`、`stock-conflict`、`lottery-conflict`、`action-error`、`loading`、`error`、`empty`、`refresh-error` | `operationsSpecs.rewards`：readonly/edit-only按实际engagement:edit（该模块同一权限含删除）而非伪造额外权限；保存库存携带source.redeemed_count，库存并发和活动引用校验来自RewardSerializer；stock-conflict/lottery-conflict错误原地保留输入，save-review由全局机制。 |
| `adminLotteriesScenarioOptions` | `default`、`readonly`、`save-error`、`save-review`、`version-conflict`、`metadata-loading`、`metadata-error`、`loading`、`error`、`empty`、`refresh-error` | `LotteriesPage/LotteryEditor`：engagement:edit控制readonly；奖品字典metadata useLoad失败/加载时锁新增和编辑，选择器独立失败也锁提交；configure提交真实version，服务器版本冲突可能是400，保留服务器错误。pity和ten_guarantee只展示服务端规则，不显示admin上下文count=0为客户进度。 |
| `adminClaimsScenarioOptions` | `default`、`tag-review`、`tag-suspend`、`readonly`、`save-error`、`save-review`、`details-conflict`、`status-conflict`、`terms-conflict`、`loading`、`error`、`empty`、`refresh-error` | `ClaimsPage`：tag_reviewed/tag_editable/needs_tag_review决定TAG审核/暂停操作；engagement:fulfill控制所有处理入口；提交expected_details/expected_status/terms_snapshot保持三类冲突，blocked保留核对错误；取消积分返还后果显式显示，未知结果由PendingReviews锁定。 |
| `adminOperationConfigScenarioOptions` | `default`、`readonly`、`saving`、`save-error`、`save-review`、`loading`、`error`、`refresh-error` | `OperationConfig/MutationInline`：engagement:edit控制readonly，确认弹窗busy为saving，错误保留form、字段检查仍运行；club-config PUT由trackReview处理save-review；首次无快照不显示默认0配置。 |
| `adminCommissionScenarioOptions` | `default`、`fixed`、`readonly`、`forbidden`、`loading`、`error`、`save-error`、`save-review`、`refresh-error`、`preview-loading`、`preview-error` | `DefaultCommission`：commission_type=FIXED切换固定浪花，初始/刷新读取失败不显示默认0；编辑入口依报单审核权限，后端拒绝返回forbidden错误。readonly不另造公开配置页；原型preview-loading/error属于演示独立试算，当前无该API，真实试算由OrderDispatch报价承接，见TODO。 |

## 首轮新增只读调用与验证范围（历史记录）

- `GET /api/admin/escorts/{id}/`：`AdminEscortSerializer`和`EscortViewSet`现有retrieve能力，默认`escort:view`；陪玩详情只读。
- `GET /api/admin/audition-signups/{id}/`：`AuditionSignupViewSet`现有retrieve，默认`audition:signup_view`；只读详情及审核预读。
- `GET /api/admin/qualification-options/`：现有资格元数据，用于等级范围卡；各档commission_rate按STAFF返回值展示，不写入配置。
- `GET /api/admin/club-rewards/?page_size=1`：复用现有奖品列表作转盘元数据就绪检查，奖品选择仍走真实分页。
- `GET /api/stores/current/`：`AdminStoreSwitcher`在credentials:omit下发送目标`X-Store-Slug`核对公开门店，只读成功后弹出原型式切店确认。普通后台不请求`stores/manage`；平台入口只在既有`can_manage_stores`为true时显示。
- 本轮没有执行资金、审核、消息或配置写请求；运行时隔离只读核验由总任务执行。源码对照、类型检查与生产构建不能替代这些业务状态的实际复现。

## 首轮构建记录（历史记录）

2026-11-05：`node ../node_modules/typescript/bin/tsc -b --pretty false` 通过；`node ../node_modules/vite/bin/vite.js build` 通过（2309 modules）。Vite保留大chunk提示；本轮未为视觉迁移增加懒加载或改变业务路由。

## 2026-11-05 骨架结构再次精确对齐

`components/AdminPageSkeleton.tsx`保存41个原型骨架分支及其源文件/行号，`adminSkeletonSources`记录page/region作用范围。39个DataPageState调用已改为必填显式`skeleton`，不再按三个variant推测页面。下面尺寸以CSS像素计；字体、圆角及控件适应仍服从最新全局用户偏好，不额外固定medium尺寸。

| 原型来源 | 实际页面/区域 | 骨架几何 |
| --- | --- | --- |
| AdminOrdersPage:105 | OrdersPage | 96、160、520；space-y-4 |
| AdminProvidersPage:128 | EscortsPage | 96、176、560 |
| AdminRegistrationsPage:78 | RegistrationsPage | 112、128、520 |
| AdminLevelsPage:149 | escort-levels ResourcePage | 112、96、480 |
| AdminAuditionLinksPage:233 / AdminWithdrawPage:173 | AuditionsPage活动 / WithdrawalsPage | 112、80、520 |
| AdminAuditionSignupsPage:105 | AuditionsPage报名 | 112、80、500 |
| AdminReportsPage:146 | ReportsPage | 96、128、520 |
| AdminServicesPage:136 | ServicesPage | 128、112、520 |
| AdminGameplaysPage:98 / AdminBannersPage:108 / AdminAnnouncementsPage:102 | 对应ResourcePage / BannersPage | 128、96、520 |
| AdminContentsPage:112 / AdminBroadcastRulesPage:82 / AdminBroadcastRecordsPage:93 / AdminRewardsPage:100 | 对应运营子页 | 128、96、560 |
| AdminLotteriesPage:88 | LotteriesPage | 128、112、500 |
| AdminWalletPage | WalletsPage | 128；四列各112，gap16；500 |
| AdminTransactionsPage:105 / AdminRechargesPage / AdminReservationsPage:191 | 流水 / 充值 / 预约 | 128；md四列各112，gap16；520 |
| AdminClaimsPage:111 | ClaimsPage | 128；四列各96，gap16；520 |
| AdminDashboardPage:98 | DashboardPage | 三列224；四列144；1.45fr:1fr两列320；底部288；无额外128头占位 |
| AdminOperationConfigPage:127 | OperationConfig | 128、96；1fr:360px两列各560 |
| AdminOrderDetailPage:103 | OrderDetail | 144；1.6fr:360px两栏720/520 |
| AdminProviderDetailPage:129 | EscortDetail | 208；1.6fr:360px两栏760/540 |
| AdminRegistrationDetailPage | 注册详情 | 208；1.6fr:380px两栏720/560 |
| AdminPassesPage:144 | 通行证配置区 | md两列/xl四列各208、gap12；购买列表独立 |
| AdminPaymentFeePage:189 | 费率配置区 | 3fr:最小320px/2fr两栏各440；页头和变更记录保留 |
| AdminCommissionPage:145 | 默认抽成配置区 | 3fr:最小340px/2fr两栏各450；弹窗标题保留 |
| AdminDisposesPage:119 | RecordsPage奖惩列表区 | 5行各80、gap12、border-t/p16；保留页头和筛选 |
| AdminPaymentFeePage:229 | 费率历史区 | 3行各56、gap12、border-t/p16 |
| AdminPaymentFeePage:217 | 费率试算区 | 16×112、32×全宽、32×全宽、40×全宽；gap12/p16 |
| AdminReportsPage详情分支 | 报单详情区 | 96、256、112；gap16 |
| AdminTransactionsPage:137 | 原流水详情 | 三行各48；gap12 |
| AdminLevelsPage:187 | 删除关联影响 | 80、112；gap12 |
| AdminChatPage:260 | 会话列表区 | 5行，每行40头像+16/12文本条；根p12、行p12 |
| AdminChatPage:276 | 消息区 | 80×3/5宽、64×1/2宽右对齐、96×2/3宽；gap20/p20；消除外围重复padding |
| AdminRechargesPage凭证分支 | 收款凭证放大区 | aspect3/4、max-h58dvh、全宽；沿用真实图片onLoad/签名重试 |
| AdminReservationsPage:232 | 预约重新报价区 | 128、96；gap12；仅真实quoteLoading时显示 |
| AdminCommissionPage:164 | 来源保留，未创建独立假试算 | 32、96、64；gap12/p16。实际没有独立试算接口，仍使用派单真实报价，不造运行分支 |

额外实际页采用显式来源：老板资料→Providers；成员/角色/门店/字典/VIP/额外资源目录→Levels；薪资方案→Services；薪资安排/业绩/成本/评价/审计/签到进度/领券记录→Reports；薪资核算/押金规则/签到配置/KOOK配置与状态→OperationConfig；经营ROI/陪玩排行→Dashboard；站内消息/陪玩冠名→BroadcastRecords；额外财务/预约/报名详情读取→Reports详情。对应别名在`extensionSources`逐项记录，不凭路由兜底三种通用骨架。

该骨架批次只对`DataPageState`、独立Skeleton组件及其消费位置做局部修改。静态AST对照：41/41分支的容器层次、class集合与Skeleton几何匹配原型；TypeScript检查通过。此对照不代表41种运行状态均已在浏览器复现。后续区块与基础组件的修改见下节。

## 2026-11-05 区块、组件及运行状态复核

此次以原型页面、`index.css`以及Card/Table/Chart/Dialog实际组件代码共同为依据，逐页迁移区块，不用“无溢出”替代视觉一致性。管理后台以1366×1024为最小桌面视口，并验证1920宽度。未修改原型，未复制其示例账户、金额或本地成功状态。

- 框架与工作台：恢复顶栏功能搜索、原型普通标题、三列待办、三视角各四指标、账户规模、220px Recharts真实日期柱图、状态分布和最近四笔订单。额外真实ROI、客服派单、门店、身份与权限继续保留。
- 订单与人员：分别迁移订单筛选及折叠、摘要/详情/操作侧栏、真实退款与派单表单、预约卡、陪玩资格档案、注册材料与审核。注册单抵材料两列，现金材料三列；原型与实际模型冲突处按真实字段扩展，现金人民币不冒充门店币。
- 财务与设置：分别迁移钱包四指标和账户类型tab、待结算读取弹窗、提现详情、报单与流水、费率双栏与真实试算、通行证档位、角色权限组和抽成配置。薪资、成本、ROI等额外页面沿用同套视觉，保留实际人民币口径。
- 目录与运营：分别迁移套餐/玩法/等级/公告/轮播/内容/好礼/播报/转盘/领取/运营配置，采用各原型的标题、身份图标、状态/排序、筛选、卡片和对应表单预览。未支持的查询、时间线及试算明确待开放，不对当前分页进行伪全局过滤。
- 客服与登录：Chat保留320px会话栏、40px带图标搜索、真实头像/未读/消息与附件；登录采用原型双栏、字段层级与行内错误，保留真实密码/TOTP与恢复码机制。未伪造OTP成功或低权限身份。
- 基础组件只改本端：小圆角、14/20表格与卡片正文、48px表头/12px单元格内边距、单选shadcn Select、Dialog/Alert标题、关闭与正文/底部按钮分区、进行中与未知结果反馈。Alert最终操作与取消也覆盖8px圆角；结构排序/增删用草稿快照判断放弃确认。

证据全部使用18105隔离环境的声浪及兴安门店身份。下列路径相对于仓库根目录；截图与JSON记录应一并阅读。发送前中断会在网络路由中阻止业务写入，只能证明UI反馈，不能证明真实交易成功。

| 范围 | 可复查证据 | 证明范围 |
| --- | --- | --- |
| 框架、工作台、门店、客服、试音 | `.tools/visual-update/admin-block-root/results.json`；`.tools/visual-preview/admin-independent-blocks/retest.json` | 初轮64组读取及截图；独立复核后8组检查源Card实际grid、各已授权视角四指标、搜索40px、登录行内错误及注册材料。Chat自动已读POST发送前被中断，相关截图是读取/错误反馈状态。兴安未授权视角未冒充覆盖。 |
| 单选、清空、数字ID、分页与放弃确认 | `.tools/visual-update/admin-common-controls/results.json` | 双店×两宽度共4组，真实GET查询/清空/page_size=50，Switch修改→放弃确认；登录密码显隐不提交；业务写入0。 |
| 订单、预约、人员和审核材料 | `.tools/visual-update/admin-orders-people/README.md`；`.tools/visual-update/admin-registration-refinement/results.json` | 页面、详情、操作弹窗、发送前中断及最后8组材料/Checkbox修正；真实现金分支未造数据触发。 |
| 财务、角色与薪资扩展 | `.tools/visual-preview/admin-financial-round1/review.json`；`admin-financial-states/review.json`；`admin-financial-dialogs/review.json`；`admin-financial-final/review.json`（均位于同一visual-preview目录） | 144页面读取、局部读取失败及弹窗/提交前中断；未伪造平台补入启用、已配置费率或真实到账。 |
| 目录与运营页面和表单 | `.tools/visual-preview/admin-directory-final/review.json`；`admin-directory-states-final/review-verified.json`；`admin-directory-dictionary-abort/review.json`；`admin-directory-refresh/review.json`（同目录） | 8组合144项最终读取；34条加载/失败及发送前中断记录、刷新保留快照并锁写/重试、字典集中待核对和真实空查询清除。早期选择器误报不算产品通过，另有最终重验覆盖。 |
| 奖池排序与关闭前确认 | `.tools/visual-preview/admin-directory-last/review-final.json`；`.tools/visual-preview/admin-directory-last-retry/review.json` | 双店×两宽度，下移→继续编辑保留顺序→关闭显示destructive放弃确认；4组合业务写入0。一次本机ERR_NO_BUFFER_SPACE只重试受影响组合，通过后未扩展全量重复。 |
| 最后整页回归 | `.tools/visual-update/read-sweep-admin-shenglang-admin-source-block-final.json`；`.tools/visual-update/read-sweep-admin-south-admin-source-block-final.json` | 每店120次读取，总240次；无脚本异常、页级横向溢出、缺标题、破图或读取错误alert。此项是回归检查，不证明所有组件像素与全部状态一致。 |

最后TypeScript检查、Vite生产构建（2683 modules）及既有67项共享测试通过；构建保留大chunk提示。本轮未为视觉工作改动业务路由或增加假后端能力。未执行的OTP、低权限、真实资金成功写入、并发冲突与硬件声音场景继续保留验收边界。

用户已将老板端交由另一个agent负责；此任务停止老板端视觉修改，之后仅收尾后台，不修改老板端文件或三端共享视觉样式。

## 挂页与局部更新修复

后台已取消通用60秒轮询及健康待派队列轮询，已有实时事件按当前业务区域筛选和合并；跨标签同步资料/令牌不再误增身份版本而清空整页快照。真实换号/重新登录/退出仍失效旧请求。连续双标签75秒及通知突发、断线重连、草稿保留验证见 [REALTIME.md](REALTIME.md)。该修复涉及共享会话逻辑，没有修改共享视觉样式或老板端页面。


## 原型框架补齐与验证（2026-11-06）

- 左栏由全部页面平铺改为原型的一级业务域导航，当前业务域下的真实授权页面显示在内容区二级导航；平台与工作区保留独立入口。72px 收缩、232px 展开、会话内展开状态及本地锁定偏好均已接入，未改变任何业务路由、权限判断或页面请求。
- 顶栏补齐真实账号菜单、角色信息、操作边界说明和退出确认；功能搜索展示业务域与页面分组，派单和咨询提示音沿用既有真实开关及试听逻辑，仅同步原型图标层级。
- 使用 `admin.esports.club` 隔离环境和既有测试身份完成真实密码与 TOTP 登录。1366×1024 与 1920 宽度均无整页横向溢出；订单、预约、财务二级导航、搜索、收缩、展开、刷新后锁定和退出确认通过。页面脚本异常 0，业务写请求 0；未执行审核、资金、消息或配置写入。
- 证据：`.tools/visual-update/admin-prototype-shell-results.json`、`admin-prototype-shell-1366.png`、`admin-prototype-shell-1920.png`。TypeScript 检查、Vite 生产构建（2684 modules）及 95 项共享回归测试通过；构建仍保留大 chunk 提示。
