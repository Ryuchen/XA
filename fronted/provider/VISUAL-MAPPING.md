# 陪玩端逐页原型映射与状态核验

核对日期：2026-11-05。依据是 `design-demos/shenglang-web-prototype/app/src/pages/provider/`、`components/provider/ProviderFrame.tsx`、`components/prototype/FilterBar.tsx` 的源码。本文记录源码映射和真实状态条件；隔离运行验证及覆盖边界见 `../VISUAL-UPDATE.md`，没有使用演示Scenario或新增原型截图研究。

## 范围与计数

- 原型有 **13 个业务页面**，另有 `ProviderShared::ProviderPage` 页面容器，共 14 个名称带 Page 的导出。
- 实际有 **20 个业务页面组件**（原19页加公开试音页AuditionsPage），见后表。`/pages/audition/index` 是 `AuditionPage` 的兼容入口，不新增页面；根路由和通配路由重定向到 `/orders`。
- 原AST的21个implementation条目（19业务页）中还包括 `components/ui/dialog.tsx::Dialog`、`components/ui/alert-dialog.tsx::AlertDialog`，它们是两个基础容器，不是两个缺失业务页。新增AuditionsPage后业务页为20，不能把容器重复计数。
- `ProviderArea` 是原型路由/Scenario 调度器；实际使用 `App.tsx::App/Guard/Workspace`、真实会话与 StoreGate，不复制原型示例场景选择器、手机外框和说明面板。

## 视觉结构与断点

原型页内的实际类名采用到业务页，公共 `Panel` 只是 `rounded-[12px] border bg-card p-4 md:p-5` 的业务容器，不用它替代页面结构。

| 原型规则 | 实际页内证据 |
| --- | --- |
| 标题 26px、1.3 行高、-0.03em 字距 | `components/provider-ui.tsx::PageHeader` 的 `text-[26px] leading-[1.3] tracking-[-0.03em]` |
| 12px 面板、8px 内层信息区、4/16/24px 节奏 | `Panel`、`DetailGrid`；各页 `gap-4` / `gap-6` / `p-4 md:p-5` |
| 浅灰分段筛选、选中背景白、44px 操作区；md 起等分 | `FilterBar` 直接采用原型的 nav > div > button 结构、`bg-muted/60 p-1`、`min-h-11`、`bg-background ... shadow-sm`，保留 `aria-pressed` |
| 订单主体 + 280px 侧栏，lg 才分栏 | `OrdersPage` 的 `lg:grid-cols-[minmax(0,1fr)_280px]` |
| 报单 lg 双列；预约和评价 md 双列 | `ReportsPage` 的 `lg:grid-cols-2`；`ReservationsPage` / `EvaluationsPage` 的 `md:grid-cols-2` |
| 个人中心 md 起 300px 侧栏 | `MinePage` 的 `md:grid-cols-[300px_minmax(0,1fr)]` |
| 基本资料与档期 lg 双列 | `ProfilePage` 的 `lg:grid-cols-2`，手机保留单列 |
| 消息列表 + 260px 侧栏；客服 FAQ 280px + 会话 | `MessagesPage` / `SupportChatPage` 的两个 `lg:grid-cols-[...]` |
| 通行证 md 双列；积分商品 md 三列 | `PassPage` 的 `md:grid-cols-2`、`PointsPage` 的 `md:grid-cols-3` |
| 原型对话框 12px 圆角、5号内边距、内容可滚动 | `Confirm` 的 `rounded-[12px] p-5 sm:max-w-sm max-h-[calc(100dvh-32px)] overflow-y-auto` |
| 原型本地文件选择的虚线区域 | 业务组件 `UploadField`：`min-h-20 rounded-[8px] border-dashed bg-muted/30`，真实原生 file input、原 accept/multiple/disabled/onChange；显示已选文件名 |

全局框架由主任务移植到 `App.tsx` / `layout.css`，再次精读ProviderFrame后已纠正：业务画布没有外侧页目录或汉堡抽屉，248px目录是原型工具壳，不能进入业务。实际为`height:64px`顶栏、`max-width:1120px`（包含24px左右内边距，桌面卡片可用内宽1072px）；768px起顶部5主导航和隐藏手机底栏；手机`inset-inline:12px; bottom:calc(12px + env(safe-area-inset-bottom))`浮动底栏。用户最新批注已移除顶栏个人头像（Mine页内头像保留），主题与提醒保留原handler，按原型独立Moon/Sun与Volume图标显示，不再放自造设置Popover；真实门店与通知保留。已读当前源码，页内断点按上表保持。

## 13 个业务原型逐一落地

| 原型导出（所在文件） | 实际组件与入口 | 默认态结构证据 | 真实业务差异 |
| --- | --- | --- | --- |
| `ProviderWorkbenchPage` (`ProviderWorkbenchPage.tsx`) | `Orders.tsx::OrdersPage`，`/orders` | 用户概况及四项分隔指标、5段 FilterBar、订单号/状态在上、服务/收益主行、DetailGrid、280px 服务侧栏；操作框有 44px 图标和订单摘要 | Switch直接复用真实AVAILABLE/OFFLINE状态，BUSY保留真实文案与三态Select；“接下来服务”读取已有真实预约API，不放假档期；5种筛选实际请求（已取消重复“我的”聚合）；接单/开始/完成/拒单/确认队友/撤回使用原控制器 |
| `ProviderReportsPage` (`ProviderFulfillmentPages.tsx`) | `Reports.tsx::ReportsPage`，`/reports` | lg 双列卡、收益/凭证 DetailGrid、三类凭证 `min-h-20` 状态格、标题创建按钮、可选订单 `min-h-16` 列表 | 保留真实图片预览、最多9张战绩图、补传/删除、售后与退款信息；不采用原型本地数组或第1份报单的 ready 值 |
| `ProviderReservationsPage` (同上) | `Orders.tsx::ReservationsPage`，`/reservations`、`?id=`详情 | md 双列列表、时间图标/浅蓝时段块、详情 `max-w-2xl`、底部动作和原因表单 | 真实游标分页、服务时段/游戏账号/拒绝原因/关联订单、只执行服务器 `allowed_actions` |
| `ProviderWalletPage` (`ProviderAccountPages.tsx`) | `Wallet.tsx::WalletPage`，`/wallet`，4类tab与提现详情定位 | 标题提现按钮、深海军蓝余额卡、底部双指标→4段 FilterBar→p-4边到边记录的原型主序；FilterBar与记录在space-y-4分区内；用户要求删除附加奖惩/礼物三指标，保留账户服务MenuRow | 使用可提现/冻结/净收入真实值；税率、最低金额、人民币打款由服务端规则和试算决定，不照抄原型50浪花与6%；保留奖惩、礼物归因、提现审核/退回/打款凭证 |
| `ProviderPassPage` (同上) | `Wallet.tsx::PassPage`，`/pass` | 当前通行证及 DetailGrid、md 两列产品、TicketCheck、采购记录、确认网格 | 任意真实档位、余额限制、当前有效档只能续同档、实际时间/价格校验、失败核对 |
| `ProviderPointsPage` (同上) | `Points.tsx::PointsPage`，`/points` | 当前积分、Gift 图标商品卡、md 三列、兑换确认 | 扩展真实 KOOK 绑定、权益交付、积分流水；动态交付字段、TAG审核/修改/到期、库存和幂等请求保留 |
| `ProviderMinePage` (`ProviderProfilePages.tsx`) | `Account.tsx::MinePage`，`/mine` | md 300px 资料侧栏、头像+认证、三格分隔指标、深蓝余额入口、两组带图标 `min-h-14` MenuRow | 指标按真实provider-stats接评分/今日接单/完成率，已完成单和评价数另行保留；认证和TAG按真实资料；实际状态 Select、押金规则与缴款/退出确认保留，不放假服务等级或假在线状态 |
| `ProviderProfilePage` (同上) | `Account.tsx::ProfilePage`，`/profile` | lg 双列基本资料/档期，补充资料/附件继续同款 section；8px Field 间距 | 接单Switch接真实状态；自由服务日/任意时间保留原型UI但待开放，下方仍是实际7×4矩阵；元信息4格保留，未暴露/未实现字段明确待开放；保留性别/城市/区服/资格、附件与独立保存 |
| `ProviderSkillsPage` (同上) | `Account.tsx::SkillsPage`，`/skills` | 已选摘要；每游戏 `overflow-hidden rounded-[12px]` section、边到边 `min-h-24` 行、Checkbox、价格右对齐 | 保留资格限制、搜索/平台/难度、失效技能移除、未保存草稿/刷新与清空确认 |
| `ProviderAuditionsPage` (同上) | `Auditions.tsx::AuditionsPage` `/auditions`；`Auth.tsx::AuditionPage` `/audition?token=...` 及兼容入口 | 公开招募原型卡、游戏服务/名额/截止、mt5两列四项要求、原型报名表服务介绍+音频预览均迁移，缺数据标待公布，提交/上传待开放；下半真实历史记录 | 公开列表API/音频报名缺口见PROTOTYPE-TODO；有效邀请token流程和contact/game/remark现有提交不变，不造活动或成功回执 |
| `ProviderMessagesPage` (`ProviderCommunicationPages.tsx`) | `Messaging.tsx::MessagesPage`，`/messages` | 平台通知连续列表、Bell 40px 图标、红未读点、日期/标题/摘要、260px客服侧栏、详情框 | 使用真实未读数与通知详情；点击关联路径由业务函数解析，不写死钱包/资料；保留全部已读和向前分页 |
| `ProviderSupportPage` (同上) | `Messaging.tsx::SupportChatPage`，`/support-chat`；附属 `SupportPage`，`/support` | 280px FAQ+会话主体、12px气泡、本人主色/客服浅灰、滚动历史、底部同排 Textarea+发送图标；联系资料沿同款卡片 | 真实会话文字/图片、已送达/已读、订单上下文、未确认消息核对/原请求重试、独立草稿；不生成原型自动回复；真实客服二维码/企微单独保留 |
| `ProviderEvaluationsPage` (同上) | `Account.tsx::EvaluationsPage`，`/evaluations` | md双列、5星图标、服务和评价正文、日期、浅灰回复、全宽回复按钮、编辑框 | 保留三维分数、订单定位/分页、客服/本人回复来源、版本冲突、原草稿/读取最新/复制草稿 |

`ProviderShared::ProviderPage/ProviderHeader/ProviderSection` 的内容职责分别落在应用内容区、`PageHeader`、各页 section；`ProviderState` 映射下节真实请求状态，`ProviderNotice` 的业务结果映射 `Notice/Status`，`DetailGrid` 采用同结构同类名。

## 实际额外页面、详情和表单（全路由清单补差）

上表覆盖13个原型业务页面；实际拆分/扩展包括 **Login、Register、Applications、ApplicationDetail、WithdrawalAccounts、Support联系资料和Auditions公开列表/邀请流程分离**。它们沿明确的原型 pattern 扩展。

| 额外实际组件/入口 | 沿用原型 pattern 与本次代码证据 | 状态/行为 |
| --- | --- | --- |
| `Auth.tsx::LoginPage` `/login` | Profile 基本表单 Panel + 共享品牌；账号/密码 Field、OTP二维码/密钥、动态码、恢复码同组件 | 静态登录页没有“未登录=空页面”；未提交就是正常空表单。提交中按钮和字段禁用；OTP验证/首次设置/一次性恢复码由真实响应决定；失败为表单内 Notice；会话版本变化拒绝旧结果 |
| `Auth.tsx::RegisterPage` `/register` | 两段 FilterBar；Profile 的分组基本表单；实名/押金/审核附件都是12px分组卡 | apply/status/resubmit来自现有业务；规则请求用原型骨架/错误重试；服务端审核PENDING/APPROVED/REJECTED展示Status、审核意见和重新提交/前往登录；上传保持私密材料/类型大小限制；不造OTP或支付成功 |
| `Orders.tsx::ApplicationsPage` `/applications` | Reservation md双列列表；队伍/报名时间 DetailGrid；订单状态、右侧查看页脚 | 真实历史分页；队友未确认/老板待选择/已结束/SELECTED/NOT_SELECTED/WITHDRAWN都经 `applicationStatus`，不把历史当可接单 |
| `Orders.tsx::ApplicationDetailPage` `/applications/:orderId` | Reservation `max-w-2xl` 详情；Order picker `min-h-16` 报名选择行；DetailGrid与页脚动作 | 无application参数先选择；有参数读取真实详情；`providerApplicationPresentation`提供能否确认/撤回和解释；历史来源返回列表；撤回确认显示订单和队伍 |
| `Wallet.tsx::WithdrawalAccountsPage` `/withdraw-accounts` | Pass产品md双列/lg三列卡；Wallet DetailGrid；绑定/修改表单、解绑确认沿Wallet对话框 | 三种方式各一账户；无账户显示未绑定卡而非整页消失；版本冲突禁止保存并保留/复制草稿；解绑确认含方式/姓名/账号，已提交提现快照规则保留 |
| `Messaging.tsx::SupportPage` `/support` | ProviderSupport 的中性卡、Headphones、真实联系资料分区 | 联系资料加载/失败/无资料，复制反馈，真实企微HTTPS/二维码和账户编号；这是会话之外的账户咨询入口，不造在线状态 |

## 每个原型的四个 Scenario 与实际条件

状态只来自真实状态变量。`LoadState` 所有实际调用显式传 `hasData={data.data != null}`（接单概况是 `stats.data`、报单选择器是 `choices.data`、试音是有效 `info`），避免空的 grid/fragment 使首载骨架失效。

源码复核纠正：仅让列表使用LoadState不能满足原型整页状态。现在 `SnapshotGate`（消息页使用同样四态逻辑的整页 `LoadState`）包住 Wallet 的余额/账户入口/分类/记录、Points 的积分/绑定/分类、Orders 的概况/筛选/主体/侧栏、评价统计与内容、消息通知/客服侧栏、报单定位/主体、客服FAQ/会话和联系页的默认布局。无有效快照的首载/首次失败时，PageHeader后直接是原型Skeleton/错误卡，不先挂载“—”余额卡或完整筛选。数据已存在的刷新保留默认快照，列表LoadState显示正在更新/错误重试；Orders概况是独立请求，用自己的LoadState分区，不因统计读取失败隐藏已能使用的真实订单。

这些Gate只控制默认DOM，不卸载父页控制器；所有Confirm与消息详情Dialog仍在Gate外。Points待核对兑换、客服待核对消息/原请求核对和重试工具也在Gate外，即使首次数据读取失败也保留恢复入口。

公共表现来自 `ProviderShared::ProviderState`：

- **loading**：首载 `loading && !hasData` 展示 `[220,220,180,180]` 高度、`grid gap-4 md:grid-cols-2`、12px圆角的四块 Skeleton，`aria-busy`。已有真实记录刷新时保留记录并显示“正在更新…”，不以假数据替换。
- **error**：首次失败且无有效数据时展示 `border-destructive/25 bg-destructive/5 p-6 md:p-8 text-center`、AlertCircle、错误内容和 `h-11` 重载按钮；刷新失败保留已读真实记录，错误 Notice 的重试调用原 loader。
- **empty**：成功响应确认空列表后展示 Inbox、18px标题、说明、`rounded-[12px] border bg-card px-4 py-10`；过滤为空保留筛选入口，客服空历史保留发送入口。
- **default**：仅使用实际响应、实际字段和实际 `allowed_actions`。原型的“返回正常示例”和状态选择器不进入产品。

| 原型业务页 | default / loading / error 的数据来源 | empty 的实际条件与差异 |
| --- | --- | --- |
| Workbench | 首个primary成功前使用SnapshotGate提供四块源骨架/错误；成功后概况stats与订单列表独立LoadState保护，新查询scope清空旧卡，筛选/搜索节点常驻保留焦点 | 当前订单列表为空，按当前五种状态显示标题；筛选/侧栏仍可用 |
| Reports | SnapshotGate覆盖定位/主体/分页；真实报单data和选择器choices均LoadState | `rows.length===0`；选择器无可选订单独立空态；订单定位的创建能力继续按 eligibility 展示 |
| Reservations | 列表游标/详情请求 `data` 的状态 | 没有详情且已成功加载的 `rows` 为空；详情不存在的HTTP失败显示错误而非假预约 |
| Wallet | 真实钱包/流水/权益/账户合并读取data；SnapshotGate先控制整页余额/入口/分类，再由记录LoadState承接已有快照刷新 | 当前tab总数为0；余额/冻结保留“—”或真实值，不把空流水当没有钱包 |
| Pass | 通行证 `data` 请求与历史 | `products.length===0` 给“暂无可用通行证”；无当前档位是合法默认态，不制造生效日期；采购历史为空另有提示 |
| Points | data的真实积分/商品/交付/流水请求；SnapshotGate覆盖积分/绑定/分类/内容，持久化待核对兑换在Gate外 | shop无商品、claims无交付或定位找不到、ledger无流水分别用 EmptyState；KOOK禁用/未绑仍展示真实能力状态 |
| Mine | Promise.allSettled 的profile/deposit/evaluation/stats结果；首载骨架；全部失败以totalFailure进入完整错误重试卡；部分失败逐条Notice，刷新入口保留 | 已认证账号资料记录通常自动存在；未填写字段照实展示并可进Profile。没有服务端“无账号资料”成功分支，不能把资料请求失败伪装成未完善，也不伪造一套空账号 |
| Profile | 真实profile和slots；首载骨架/读取失败重试 | 实际已有资料记录的字段可为空，仍显示可填写表单；空档期是未选择时段矩阵。原型“无资料整页占位”无对应创建资料接口，不替代现有PATCH流程 |
| Skills | 真实groups/profile请求；首载骨架/错误重试 | groups为空“暂无开放技能”；shown为空“没有匹配技能”，筛选仍留在上方 |
| Auditions | 邀请 `loading/info/error` 接入LoadState；表单仍取真实邀请 | `!info`且没有错误才空；缺token/无效/过期等真实异常是错误重试；不生成一场默认招募 |
| Messages | 通知data的SnapshotGate先控制整个通知/侧栏布局；记录LoadState保留刷新状态，客服未读chat独立失败提示 | 成功后rows空“暂无消息”，全已读/未读来自真实数值 |
| Support | 联系资料data和会话data各自SnapshotGate覆盖默认整体布局；内层LoadState处理快照刷新；待核对消息入口在Gate外 | 无客服资料可站内咨询；会话items空“暂无客服会话”，发送入口保留，不冒充客服已回复 |
| Evaluations | 真实评价分页data的SnapshotGate覆盖统计和列表；记录LoadState保留刷新状态 | 成功列表为空“暂无服务评价”；待回复数量0是真实指标，不用假评价填空 |

## 业务分支、对话框与提交态核验

| 页面及对话框 | 实际状态证据和样式处理 |
| --- | --- |
| Orders筛选/履约阶段 | pending/all/grabbed/in_progress/completed/cancelled真实请求；PENDING待接、GRABBED开始、IN_SERVICE完成、COMPLETED评价、CANCELLED原因；按 `providerOrderActions` 显示动作，按 `orderBusinessDetails` 隐藏未授权游戏账号；报单进度、预约、退款、服务收益/分佣追回、本单剩余冻结保留 |
| 接单限制/等待队友/等待老板 | `stats.active_orders>=max_concurrent_orders` 给Notice并沿用原禁用；`my_applications`与`partner_confirmed/registration_ended`给报名状态；`completion_block_reason/team_progress`保留真实限制文字，未获授权不提供操作 |
| Orders确认接单/报名/开始/完成/拒单/确认队友/撤回 | 同一个Confirm按action变更图标、明确动作名，标题+订单号+收益/数量DetailGrid；开始/完成接真实UploadField、拒单TextArea、双陪队友Select；busy、上传未选、原因未填等原禁用条件保留 |
| Reports草稿/审核中/通过/驳回/结算 | DRAFT为黄色，PENDING审核中使用蓝色info，APPROVED绿色，REJECTED红色；三类凭证准备使用真实URL与missing；can_edit/canSubmit/服务阶段、售后冻结、审核意见、结算通知保留 |
| 报单选择/提交/删除图片框 | 选择行直接采用原型min-h-16结构并显示Check；搜索/真实分页保留。提交框显示订单/服务/三类准备；删除框显示原订单和将删图片；上传失败或部分成功/响应未知保留原恢复说明，不假报全成功 |
| 预约确认/拒绝/取消框 | 操作来自详情allowed_actions；拒绝/取消原因必填原逻辑不变；已转订单展示真实链接；结果未确认提示刷新核对 |
| Wallet余额/冻结/奖惩/礼物/提现 | 余额和冻结分区；收益方向/正负颜色；提现审核中/通过/驳回、退回余额、原申请/税额/到账、审核备注/凭证由实际presentation输出；没有原型固定值 |
| 提现确认与试算 | validQuote才展示金额/税率/税额/预计人民币打款DetailGrid；试算中与错误可重试；账户版本、余额阈值、税额、原幂等资金请求保留；规则更新/账户变化/结果未知均有核对路径 |
| 收款账户绑定/修改/解绑 | 绑定状态真实；编辑冲突不清草稿，读取最新明确说明会替换草稿；解绑DetailGrid给明确对象；busy、字段为空、conflict等禁用不变 |
| 通行证采购/续费确认 | 当前档/未生效、到期后可选、余额不足等取真实data；采购时长/扣款/当前余额DetailGrid；expected价格与原资金请求不变，未知时查看最新通行证，不自动重复扣款 |
| Mine押金/状态/退出 | 真实is_verified/TAG有效期/statusOptions；目标/已缴/待缴、收益冻结与结算说明保留；缴款确认原金额/失败核对；退出只清当前角色的既有流程 |
| Profile保存/资料未填/附件/档期 | 初次hydrated后不以刷新覆盖编辑；真实空字段照实，资格、通知、私密图片恢复、视频/语音和7×4档期均保留；独立保存busy，失败就近Notice；没有新增“资料不全禁接单”规则 |
| Skills未保存/资格/清空/刷新框 | dirty控制保存，retired需移除；qualified=false不允许新增但允许去掉原选；清空/放弃草稿的后果在Confirm；过滤与服务价格不是预计实得收益 |
| 积分兑换/交付修改/历史 | 库存/积分不足/待核对请求禁用兑换；动态交付Field、kind、TAG需修改/已提交/通过/未过/暂停/到期、details、expires_at保留；兑换确认改为DetailGrid；原幂等编号、冲突草稿/读取最新/复制不变；流水采用Wallet分隔行 |
| 评价回复框 | 首次/已回复、客服/本人来源保持；引用原评价、原回复浅灰块、Textarea、busy/内容未改/版本blocked禁用；替换客服回复需明确说明；读取最新/复制草稿仍在框内 |
| 通知详情/全部已读 | detailLoading、detailError/重试、无关联对象禁用、原可标记条件；真实通知来源不明时保留原消息；不写死跳转到演示订单 |
| 客服发送/图片/历史/未确认/草稿切换框 | 文字与Send同排，图片用UploadField；真实已读/送达；pending显示核对/原消息重试/停止追踪；停止追踪与普通草稿切换独立Confirm，原client_message_id与按订单草稿隔离保留；实时更新沿原subscription |
| 登录/OTP/恢复码、注册apply/status/resubmit、试音报名/记录 | 所有这些是实际能力扩展，均沿Profile/Audition表单pattern。busy禁用、错误Notice、字段错误、审核Status沿真状态；试音is_active禁止报名、已报显示审核与历史；不复制原型仅演示音频或示例账号 |

`Confirm` 的提交中显示“处理中…”，确认/取消禁用并隐藏关闭按钮；关闭回调保留busy保护。未确认结果永远沿既有 uncertain/pending 说明显示核对动作，不变为成功态。

## 本轮新增修改与当前无需再改的证据

新增修改：`provider-ui.tsx` 的真实四态与分业务EmptyState、UploadField、可禁用FilterBar、可选操作图标Confirm；Orders报名列表/详情/确认摘要与并发限制提示；Wallet账户卡/提现试算/解绑/采购确认；Reports选择/提交/删除框及PENDING蓝色；Points交付网格/积分流水/空态；Account附件与无技能状态、回复灰块；Auth注册筛选/规则四态与试音四态；Messaging同排输入/上传与无会话状态。所有真实读取明确传hasData。额外复核已将整页初态从列表内部上提到SnapshotGate，保留已有快照刷新与独立恢复UI；接单指标4列也恢复原型md断点。

重新读代码后无需重做的默认结构：Workbench指标和订单主体/侧栏、Reports默认双列/三凭证格、Mine300px分组菜单、Profile基本资料/档期分栏、Skills边到边选择行、Wallet深蓝卡/4段筛选/流水、Pass双列、Points商品格、Messages连续通知/260px侧栏、Support FAQ+会话、Evaluations双列星级，均已有本文件所列原型类名。为核对各真实四态，相关文件仍有状态接线变化，不把“文件未大改”当成未覆盖。

验证：本轮 `node ../node_modules/typescript/bin/tsc -b --pretty false` 通过。未执行浏览器/截图、接口与数据库场景、真实上传/提现/支付/微信/SMS或实时业务验收；本文件证明的是源码覆盖和类型一致性，不把原型示例或静态类名视为这些真实链路的成功证据。全局框架改动由主任务持有，本文件已读代码记录，最终仍由主任务独立核对。


## 去掉原型工具目录后的20页入口核验

业务画布只使用ProviderFrame的顶部5主导航/手机5项dock。没有把原型演示目录当作业务导航。页面源码中的实际入口如下；这些入口在相同页面的手机单列与Web布局都存在。

| 实际业务页 | 不依赖工具壳的可达入口 |
| --- | --- |
| OrdersPage | 顶部“接单”/手机“接单”、根路由 |
| ReportsPage | 顶部/底栏“报单”；Orders履约提醒；Mine账户服务 |
| MessagesPage | 顶部/底栏“消息”、真实通知图标 |
| WalletPage | 顶部/底栏“钱包”；Mine余额卡/账户服务 |
| MinePage | 顶部/底栏“我的” |
| ApplicationsPage | Mine接单管理“我的报名”，并由订单报名详情串联 |
| ApplicationDetailPage | 我的报名历史卡“查看报名”；订单内“报名详情”；真实报名通知关联地址 |
| ReservationsPage | Orders侧栏“我的预约”；Mine接单管理；真实预约通知；详情由?id参数进入 |
| PassPage | Mine账户服务“接单通行证” |
| ProfilePage | Mine头像/“资料与档期”；Workbench保留原型接单开关，不依赖工具壳进入资料 |
| SkillsPage | Mine“我的技能” |
| EvaluationsPage | Mine“评价管理”；已完成订单“查看评价”；真实通知关联地址 |
| PointsPage | Mine“我的积分与兑换”；真实权益消息的定位参数 |
| WithdrawalAccountsPage | Wallet主记录下方“账户服务→管理收款账户”；提现确认内“管理收款账户” |
| SupportPage | Wallet“账户服务→充值与钱包咨询”；SupportChat FAQ区“查看客服与账户信息” |
| SupportChatPage | Messages客服侧栏；Mine“联系客服”；订单“咨询客服”；Wallet的Support页站内入口；Auditions页“联系客服获取邀请” |
| LoginPage | 未登录Guard自动进入；退出当前账号后的Guard；Register返回/审核通过入口 |
| RegisterPage | Login“申请成为陪玩/查询审核”；Register内apply/status/resubmit状态切换 |
| AuditionsPage | Mine“试音报名”进入/auditions：公开招募待开放版式+真实报名记录+客服获取邀请 |
| AuditionPage | 客服提供的真实带token邀请直达（兼容/pages/audition/index）；公开页不绕过token，原鉴权/专属交换保持 |

本轮默认布局复核直接修改：Wallet清除余额与分类之间插入的账户按钮和3个附加统计，保持余额→分类→记录；附加功能完整移到记录后。Orders恢复原型概况`flex-col gap-5 p-4 sm:flex-row md:p-5`、订单列表标题/计数/北京时间徽标、标题下12px间距与卡片间16px间距，订单/报单/预约列表卡采用原型shadow-sm。所有默认体仍受SnapshotGate首载/失败控制；钱包withdraw列表没有独立mock状态，不改变真实提现记录和确认控制器。未动App/layout.css，未使用浏览器/截图。


## 13页卡内节奏复查（Panel gap 与显式 margin 不叠加）

本次逐段读取原型原生section与实际页面，并读取AST的Panel直接子节点。嵌套说明文字的mt-1不是Panel直接子节点的外边距，不把它误判为重复；border-t后的pt是分隔线内边距，也不是另一份卡间距。实际Panel的gap-0/原生section用cn合并，确认不会残留gap-4。

| 原型页 | 实际节奏及本次处理 |
| --- | --- |
| Workbench | 概况原生section：flex-col gap-5、p-4/md:p-5；指标分隔；列表标题mb-3，列表独立grid gap-4。订单Panel已进一步改为gap-0：服务主行mt-4、摘要mt-4、目标/结算/真实附加提示mt-3、操作区mt-4+border-t pt-3；原gap-4只能近似主段，不能替代原型的12px次级提示节奏。保留真实售后/结算/组队附加行 |
| Reports | 报单Panel的header、DetailGrid、三凭证格无直接mt，gap-4对应源卡的三段mt-4；子图片编辑section的pt-4只是分隔线内边距；提交footer改pt-3匹配原型。无需再叠mt-4 |
| Reservations | 列表/详情Panel均gap-0；列表日期mt-4、数量/老板mt-2、查看footer mt-4；详情才使用mt-5蓝色时段块、mt-4 DetailGrid/备注、mt-5动作区。消除把详情大块无条件放进列表的差异 |
| Wallet | 余额为原生section p-5；分类与记录在space-y-4内；流水native section/p-4行无Card gap。附加统计按用户要求删除；账户服务原生MenuGroup，无Panel间距叠加 |
| Pass | 当前卡和产品卡改原生section p-5；当前DetailGrid mt-5；产品金额mt-4、可见时间mt-2、档位提示mt-1、按钮mt-5 h-11 w-full。真实采购历史继续Panel gap-4，直接子行没有额外mt |
| Points | 当前积分改原生p-5、32px金额卡；商品原生p-4，图标→名称mt-4→说明mt-2→额外真实库存mt-1→积分价mt-4→按钮mt-4 h-11。KOOK绑定作为实际附加能力移到主商品/记录后；交付/待核对Panel没有直接mt，保留等效gap-4 |
| Mine | 用户卡、余额卡、分组菜单均原生section/Link，按源p-4/p-5与边框分隔。额外押金Panel直接子节点无mt，gap-4作为真实规则/缴款表单间距，无重复 |
| Profile | 基本资料与档期改gap-0；标题组后单一mt-5 space-y-4容器；标题说明mt-1在同组内。通知/附件为真实附加Panel，直接子节点无mt，保留gap-4。档期grid/form/Panel均min-w-0 w-full，矩阵只在有界且可聚焦的overflow-x-auto中滚动 |
| Skills | 技能原生section的组标题p-4、每行min-h-24/p-4、内层文字mt-1；无Card gap。摘要改原型原生p-4区块（14px标题、mt-1同步状态、独立价格Badge），额外真实过滤改可折叠details，默认关闭；组间space-y-4；技能行标题font-medium、元信息/资格各mt-1 |
| Auditions | 默认招募卡改原生section p-5，元信息mt-5、正文mt-4；真实报名form自有grid gap-4，Panel只有该form一个直接子节点，不产生双重gap。真实历史分隔行无额外外边距 |
| Messages | 主通知列表与客服侧栏原生section/aside：p-4、行min-h-24、文字mt-1、侧栏h2 mt-4/说明mt-2/按钮mt-4；无通用Panel gap，无需重写 |
| Support | 会话/FAQ采用原生section，聊天记录space/gap-4、气泡内部mt-1、输入边界p-3，各自归属明确；真实联系资料扩展Panel直接子节点无mt，保留gap-4，无额外叠加 |
| Evaluations | 改原生article p-4/md:p-5；老板+服务在同一header（服务mt-1），补充分数mt-2、评价正文mt-4、日期mt-2、回复块与按钮mt-4；消除等距Panel造成的日期/按钮间距，服务信息只呈现一次 |

这些修改保留同一个父页状态、服务器权限/allowed_actions、金额换算、原幂等与草稿；已保持既有业务规则，新状态开关/统计/预约/试音历史接线只复用真实既有API，未新增后端规则。所有首载/失败Gate、四态样式、未确认交易恢复入口及Gate外Confirm仍保留。只针对provider执行，未动App/layout.css或基础components/ui。


## 原型未实现能力仍迁移界面（最新范围）

详见[PROTOTYPE-TODO.md](PROTOTYPE-TODO.md)。已迁移公开试音列表/原型四字段摘要/disabled介绍与音频表单、Profile自由服务日/起止时间、常用位置/服务等级/声音标签格、门店目录待开放项。每项都有可点击/可聚焦说明或原型菜单待开放状态，不假成功、不假日期/名额、不调用不存在接口。

接单开关不是待开放：Workbench aria-label“接单状态”、Profile aria-label“本周接单状态”，Workbench使用AvailabilityControl，Profile按原型独立Switch与本周接单标题同排（避免复用Workbench整条灰底控件挤成第二行）。AVAILABLE=true，BUSY/OFFLINE=false但保留真实忙碌/休息文案，BUSY可明确打开到AVAILABLE，渲染不自动写OFFLINE；关闭发OFFLINE。现有Mine三态Select、并发限制和资格规则保留。复用原状态POST后真实GET回读，未知结果仅核对，不自动重写。

公开页新增后共20业务页；原专属邀请页不被替代。现有服务统计和近期预约已有真实API，已接入而非一律标待开放。用户最新小圆角override优先：根8px、表单/按钮/选择/Tab8px（trigger6）、标签4px、卡8–12px；仅头像和Switch保留语义圆形。全局不再有自造“界面与提醒”Popover，主题/声音为原型独立图标。Mine页内重复切店入口已移除，顶栏统一。


## 细节复查第三轮：卡片、信息点缀与真实状态（2026-11-05）

本轮重新逐段读取原型源码，发现此前“默认结构无需重做”的结论只覆盖大框架，仍漏掉了概况阴影、订单卡12px/16px节奏、技能摘要与资格行、Profile开关形态、消息详情和客服问答等差异。以下为已落地修正；前文旧轮次的概括不能替代这些具体证据。本轮实现范围只有 `pages/Orders.tsx`、`pages/Account.tsx`、`pages/Messaging.tsx`，未修改App/layout、helpers、基础UI或其他页面。

| 原型源码位置 | 本轮实际修正与代码证据 | 真实业务边界与附加内容 |
| --- | --- | --- |
| `ProviderWorkbenchPage.tsx:49–64` 页头/概况与 `Metric:97` | Orders页头恢复outline、size=icon、size-11/rounded8的RefreshCw SVG；刷新时spin。概况补源shadow-sm；认证Badge进入姓名同一flex-wrap行。4项指标用24px数字+ml-1/text-xs/font-normal单位，不把“浪花/%/单”放大 | handler仍刷新真实请求；认证只在独立GET `/users/escorts/me/` 的is_verified=true时显示，失败/未知不伪造认证。收益仍为真实累计服务及推荐净收益，不冒充源示例本月净收益 |
| `ProviderWorkbenchPage.tsx:101–112` OrderCard | `provider-order-card gap-0 shadow-sm`：编号font-mono/truncate并保留title；服务主行mt-4，真实game_category_name与UsersRound组队Badge在服务标题上方；老板/数量、预约、授权区服/账号移入mt-4 DetailGrid；Clock3目标mt-3/text-xs/leading-5；结算CircleDollarSign+mt-3/p-3；footer mt-4/border-t/pt-3 | 游戏名来自现有ServiceItemSerializer.game_category_name，不拼假游戏；无可读预约显示“未提供预约时段”；nickname/UID仍受gameVisible限制；保留退出份额、售后、退款、原规则快照、队伍提交进度及allowed_actions。仅SETTLED且非取消为success提示，其余真实结算状态为中性色 |
| `ProviderWorkbenchPage.tsx:75` 局部匹配空态 | Orders的LoadState使用emptyContent，恢复border/bg-card/py-10、Inbox size-7、h3 mt-3、说明mt-2、outline h-11 mt-5，按钮清除查询/定位并进入真实“已接单” | 原型整页ProviderState的size-8/text-lg空态不再错用为局部筛选空态；首载/无快照失败由列表LoadState处理，有数据同scope刷新保留快照 |
| `ProviderWorkbenchPage.tsx:78/98` 服务侧栏 | 接下来服务保留源shadow-sm、border-l/pl-4时间轴、10px节点、12/14/12px文字；查看全部档期改ArrowRight。履约提醒依源无阴影 | 档期来自真实pending/confirmed预约排序前2项。用户最新批注覆盖原型px-0：档期整行链接改px-3、rounded8、hover:bg-accent、active:bg-accent/80，保留focus-visible ring。不是擅自改原型 |
| `ProviderFulfillmentPages.tsx:40–49` Reservations | 列表恢复整卡button（p-4/md:p-5、shadow-sm、hover边框/键盘ring/按下反馈），底部为mt-4/border-t/pt-3的文字+Chevron；详情标题18px、编号mono12，只有详情才显示mt-5浅蓝时段块；页头列表为mt-1北京时间Badge，详情为同位置ArrowLeft+“全部预约”按钮；详情服务标题和mt-1的mono编号归同一个左侧div，与右Status同排 | 列表保留额外真实预约编号，真实服务时间/数量/老板、详情4格账号、取消/拒绝原因、关联订单、游标分页均保留。全部动作仍按allowed_actions；没有在卡片button内嵌操作button |
| `ProviderProfilePages.tsx:19/45/46` Mine | 保留源无阴影的资料卡、三统计分隔、深蓝钱包卡、带图标MenuRow；钱包次要说明恢复slate-400，真实头像补shrink-0。额外TAG标签用用户4px小圆角；必要刷新改44pxSVG，不挤入文本按钮 | 真实状态三态Select/押金规则与缴款、认证、TAG有效期仍保留；切店仅顶栏一个入口；不把真实额外字段替换为示例状态 |
| `ProviderProfilePages.tsx:48–53` Profile | 保持基本资料/本周接单两列和gap-0卡内节奏；个人介绍rows=4。认证Badge补ShieldCheck但仍只显示真实已认证。接单区恢复标题和独立Switch同排，不再套Workbench整条灰底控件；常规页头去掉原型没有的返回按钮 | Switch aria-label仍“本周接单状态”，checked=AVAILABLE；BUSY/OFFLINE=false，BUSY显示真实忙碌；打开AVAILABLE、关闭OFFLINE，调用原setAvailability与真实POST/GET，渲染不改状态；未知值禁用。自由时段待开放、固定矩阵、资料独立保存/附件/草稿保留 |
| `ProviderProfilePages.tsx:59–65` Skills | 保存技能移到页头并补Save图标；刷新保留必要草稿确认但改SVG。摘要为源原生p-4块：14px已选技能/游戏数、mt-1同步状态、独立套餐价Badge；分组计数用真实Badge，行标题font-medium、元信息mt-1、资格另起mt-1的12px行，价格不再用更小small | qualified=true才显示绿色“资格已满足”，false显示需运营认证，未知明确接单时核对；原资格限制/retired移除/多条件过滤/草稿与清空确认保留。真实目录为空且无待处理retired时显示整页ProviderState空态；过滤无匹配保留实际筛选入口 |
| `ProviderCommunicationPages.tsx:14–25` Messages | 0未读时恢复页头“均已阅读”Badge；行中type映射后端SYSTEM/ORDER/SUPPORT/PROMOTION真实类型，40px Bell与红点/14px标题/12px预览保持源样式；消息详情改专属Dialog，类型Badge在标题上方、正文14px/leading6/次要色、日期12px、双按钮h-11；详情读取显示行骨架 | 整页LoadState处理真正loading/error/empty/default，空列表不先挂完整通知栏。详情GET/已读、请求序列、失败重试仍保留；关联路径仍用providerMessageDestination，无法解析时禁用，不造跳转；有快照刷新保留记录 |
| `ProviderCommunicationPages.tsx:30–35` Support | FAQ从多个details改为源min-h-11选择按钮+aria-pressed，选中border-primary/bg-primary5，答案在下方mt-3灰底块，资金安全提示仍在其下。280px FAQ/会话分栏保持；气泡文字后才放mt-1/text-xs时间/送达元数据，恢复源信息层级；输入placeholder沿源描述问题/订单号 | 3条回答按真实凭证/组队/提现逻辑；不复制示例自动回复或在线状态。现有消息图片/已读/送达、订单上下文、历史分页、pending核对与原请求重试、停止追踪、按订单草稿与两个确认框保持；pending工具仍在Gate外 |
| `ProviderCommunicationPages.tsx:39–42` Evaluations | 星级头items-center且星星shrink-0；回复块标题12px/font-medium、正文12px/leading5/次要色，替换原14px正文；回复编辑加可见Field标题、rows=4与源placeholder；常规页头去掉额外返回 | 客服/本人/未知回复来源与修改入口保留；三维分数、真实统计、分页/定位、版本冲突、原草稿/读取最新/复制均保留，不把已有回复强制锁成原型只读 |
| 无独立原型的Applications/详情 | 沿Reservation列表/详情pattern，列表采用同款shadow-sm、详情保持无阴影，去掉通用等距gap：服务+订单号同组、状态在右、编号mono12/mt-1、摘要mt-4、页脚mt-4；详情max-w-2xl/gap-0，真实预约用CalendarClock浅蓝块，动作mt-5/border-t/pt-4 | 所有历史状态、待队友确认、等待老板、已选择/未选择/撤回、历史来源返回、选择报名、幂等/版本和confirm控制器保留 |

阴影矩阵再次按源核对：Workbench概况/订单/近期预约、预约列表使用 `shadow-sm`；Mine/Profile/Skills/Evaluations/Messages/FAQ/会话及预约详情保持无阴影；未统一给所有卡加阴影。用户最新小圆角与整行档期padding/反馈属于明确override，其余仍取源结构。根已同步body16px/1.65、Panel消除Card默认14px继承，页面已有text-xs/text-sm字段仍为12/14px。

本轮做了两次源码对照：第一次逐卡找差异并修改，第二次重读修改后的结构，检查Panel gap/直接mt重复、首载与局部空态、列表整卡button嵌套、真实字段条件及所有对话框位置。`node ../node_modules/typescript/bin/tsc -b --pretty false` 已通过。没有在本子任务使用浏览器/截图或实际触发新增状态，不把静态覆盖宣称为逐业务状态真实执行成功；主任务运行验证与具体边界记录在 `../VISUAL-UPDATE.md`。未完成的真实公开招募/结构化资料/自由档期仍见 `PROTOTYPE-TODO.md`，本轮不造接口或假数据。


### 最新尺寸约定

用户已取消全局medium限制，组件按所在区块、主次和源代码选择尺寸。清除旧全页面按钮/输入强制40px及手机全部44px覆盖；保留源明确h-11/h-12、48px搜索壳、44px标题主操作和认证区域触控空间。所有固定tab均分单行、不换行；基础组件的小号/图标尺寸可按语义使用。


### 本轮只读详情与交互复验

Orders已补原型服务/收益块及页脚outline h-11两个详情入口，独立GET当前门店订单详情，不复用列表填隐私字段。详情的加载/失败重试/保留快照/刷新/关闭使用真实条件；原动作控制器不变。卡片动作、报单创建及提交明确h-11，钱包空提现入口和账户选择采用各自源尺寸。初载四骨架、后续scope清旧卡且搜索焦点保留、两店四组详情和手机断网恢复均有主任务本机证据，不等同于资金动作验收。
