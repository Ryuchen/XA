# 老板端逐页视觉与状态映射（2026-11-05）

下表记录原型代码、真实组件与状态条件的映射，不表示所有状态均已触发。后续逐页迭代已增加两店桌面/手机的真实页面截图及加载、错误、恢复检查；具体覆盖见 `../VISUAL-UPDATE.md`，外部微信/SMS等链路不从静态映射或截图推定通过。

原型代码：`design-demos/shenglang-web-prototype/app/src/pages/boss/`（36个TSX文件、42个Page/Dialog函数）。实际为34个业务页组件、35个主业务路由（`CompanionsPage`包含picker分支）；清单中的另2个实现条目是基础Dialog/AlertDialog。列表与详情共接口的公开内容合并实现，不为了凑原型数量增加新业务。

## 实施边界与共享证据

- 框架由主线按`PhoneFrame`业务部分迁移：64px头、768px起五项主导航、1120px外宽含桌面px24（内宽1072）、手机五项浮动dock。业务没有左侧演示目录或汉堡菜单；不搬预览器和场景面板。切店统一由顶栏`BossStoreSwitcher`承载，Login/Mine已移除页内重复控件。
- `Private`保留真实`Navigate`到微信登录并携带`next`；原型guest卡由真实登录页接管，不改变认证流程。
- 基础UI由主线直接对齐原型代码；页内样式证据在`prototype.css`、`surfaces.css`、`business/lottery.css`。
- `ui.tsx/State`首载无data时以`visual-states.tsx/PageSkeleton`按路由显示主视觉/卡片/正文/表单骨架；成功empty仅显示`EmptySurface`和显式下一步，不继续渲染默认卡片。
- `useLoad`只在相同deps的刷新保留真实旧快照与更新条；切换对象/筛选/页码立即隐藏旧data并锁定loading。error重试保留query，未用初始0/—当快照。
- `useAction`失败显示页面`ActionFeedback`，确认框内也显示实际错误；输入与草稿不清空。成功继续用现有真实回执/toast。`Pending`显示原金额/积分/凭证、锁定busy和blocked，不能新建重复交易。
- 初始列表计数、选人数上限不在无data时冒充真实0或单陪；所有金额、状态、资格、权限、幂等、门店隔离与真实媒体保留。

## 逐原型组件映射

| 原型组件 | 实际文件/组件 | 实际入口 | 已采用的页级结构证据 | 业务差异 |
| --- | --- | --- | --- | --- |
| `AchievementsPage` | `src/business/community.tsx/AchievementsPage` | `/achievements` | boss-achievement-summary / active / unlocked；按真实进度排序与Progress | 不填入示例订单、消费数或成就。 |
| `ActivitiesPage` | `src/business/content.tsx/ContentPage` | `/club-content?kind=HOT` | boss-activities-list / boss-activity-thumb 72×88 / 归档分页 | 与公开内容接口合并；保留现有每页30条的分页。 |
| `ActivityDetailPage` | `src/business/content.tsx/ContentPage` | `/club-content?id=…（kind=HOT）` | ReadingHeader / boss-content-poster / boss-reading-body | 服务端kind区分活动；打开真实大图，不生成假海报。 |
| `AnnouncementsPage` | `src/business/content.tsx/AnnouncementsPage` | `/announcements` | boss-public-overview / boss-public-directory / Pin与日期行 | 沿用limit=20与服务端is_pinned排序。 |
| `AnnouncementDetailPage` | `src/business/content.tsx/AnnouncementPage` | `/announcement-detail?id=…` | ReadingHeader / boss-reading-body / 空正文分支 | 正文沿用DOMPurify富文本，避免将示例段落当真实公告。 |
| `AuditionPage` | `src/business/content.tsx/AuditionPage` | `/audition?token=…&role=boss` | boss-audition-event / boss-audition-account / 本人核对动作 | 真实邀请仅核对已微信登录身份，不按原型模拟换发另一账号。 |
| `ChatPage` | `src/business/content.tsx/ChatPage` | `/chat[?order_id=…]` | boss-chat-header / room / message-row / composer / 附件与待核对条 | 保留草稿作用域、消息幂等、历史游标、已读边界、私图续签。 |
| `CheckinPage` | `src/business/community.tsx/CheckinPage` | `/checkin` | boss-checkin-summary / calendar / rewards；日期分行标签 | 年月、消费门槛、补签资格、奖励全部取真实日历。 |
| `CheckoutPage` | `src/business/checkout.tsx/CheckoutPage` | `/checkout?service_id=…` | 服务安排与游戏资料分区 / boss-checkout-quote / submit | 保留指定/双陪、客服、派单、报价、超时、优惠核验与原凭证。 |
| `ClubContentPage` | `src/business/content.tsx/ContentPage` | `/club-content?kind=RULES` | boss-public-overview / directory / 图标与标题日期行 | 规范类型使用现有ORDER_RULES/PROVIDER_RULES/AFTERSALE_RULES。 |
| `ClubContentDetailPage` | `src/business/content.tsx/ContentPage` | `/club-content?id=…` | ReadingHeader / 原图链接 / 分节富文本阅读卡 | 与同接口活动、规则详情合并，不新增演示文章。 |
| `CompanionDetailPage` | `src/business/commerce.tsx/CompanionPage` | `/companion-detail?id=…` | boss-companion-profile 96头像与24px名称 / voice / price-card / work / exclusives / reserve / actionbar | 价格、资格、冠名、可约状态均取真实资料；“擅长位置”保留原型信息格，因无结构化字段显示PendingFeature待开放。 |
| `CompanionPickerPage` | `src/business/commerce.tsx/CompanionsPage picker` | `/companion-picker?service_id=…` | boss-picker-service / picker-list / 选中与失效提示 | 复用真实可接单列表；人数由billing_provider_count决定。 |
| `CompanionsPage` | `src/business/commerce.tsx/CompanionsPage` | `/companions` | SearchField / 横向游戏 / 两/三列条件 / 56头像 / 标签声音价目卡 | 保留原有资格与时价排序，不用假试听。 |
| `CouponsPage` | `src/business/community.tsx/CouponsPage` | `/coupons` | boss-coupon-benefit 96px / body / details / 领用状态按钮 | 优惠表达用couponPresentation；支持资格券、实销补贴等真实规则。 |
| `FavoritesPage` | `src/business/community.tsx/FavoritesPage` | `/favorites` | boss-favorite-undo / 横向80缩略卡 / footer取消动作 | 保留DELETE、撤销前最新状态查询和暂不可购买禁用。 |
| `GiftsPage` | `src/business/gifts.tsx/GiftsPage` | `/gifts[?order_id=…|provider_id=…]` | boss-gift-recipient / 56图标tile-main点击加件 / tile.selected / summary / receipt-icon与金额 / Explanation | 礼物限量、钱包、收礼者、冠名、赠礼凭证与回执继续用服务端。 |
| `GuideContentPage` | `src/business/content.tsx/ContentPage` | `/club-content?kind=RIGHTS 或 POINT_RULES` | 按kind选Crown/Trophy；overview / 内容目录 | 篇数来自接口，不强制复制原型的3篇示例。 |
| `GuideContentDetailPage` | `src/business/content.tsx/ContentPage` | `/club-content?id=…（RIGHTS/POINT_RULES）` | ReadingHeader / reading-body / 长文与正文准备中 | 与公开内容详情共用真实富文本，不新增独立规则数据。 |
| `HomePage` | `src/business/account.tsx/HomePage` | `/home` | 20px门店标题 / SearchField / hero徽标与源圈线 / 公告条 / service-progress标题操作 / 76px shortcuts / 80px玩法封面 | 进度、玩法、海报、公告等全部取API；游客不读私有接口。 |
| `LoginPage` | `src/business/account.tsx/LoginPage` | `/login[?next=…|verify_phone=1]` | boss-login-layout / welcome / benefits / authorization / 真实QR | 授权与短信在最小微信桥接页完成，不复制原型模拟成功步骤。 |
| `PrivacyDialog` | `src/business/account.tsx/LoginPage → 微信桥接privacy()` | `登录扫码后的微信桥接页` | 真实checkbox协议 / wx.openPrivacyContract / 错误文本 | 原型Web示例隐私弹窗不替代正式微信合同，也不另造隐私正文。 |
| `SupportDialog` | `src/business/account.tsx/LoginPage → content.tsx/SupportPage` | `/support` | 登录帮助链接 / support-context / 双咨询通道 | 用真实客服页面替代原型仅反馈的SupportDialog。 |
| `LotteryPage` | `src/business/community.tsx/LotteryPage + lottery.css` | `/lottery` | lottery-account / 动态SVG转盘 / 双抽取 / odds-row / result-card | 保留真实分区数、概率单位、单次/十连费用、保底和幂等凭证。 |
| `MembershipPage` | `src/business/community.tsx/MembershipPage` | `/membership` | boss-member-summary / 独立会员权益 / 标签 / 生日卡 | 实销人民币口径与钱包浪花分开，保留5标签和生日确认。 |
| `MessageDetailPage` | `src/business/content.tsx/MessagePage` | `/message-detail?id=…` | boss-notice-article / header / 正文 / related-content | 关联跳转用bossMessageDestination，不生成示例关联订单。 |
| `MessagesPage` | `src/business/content.tsx/MessagesPage` | `/messages` | messages-layout主内容+快捷侧栏 / notice-row / feed-row | 通知与动态保留各自游标、真实未读与read-all接口。 |
| `MinePage` | `src/business/account.tsx/MinePage` | `/mine` | mine-layout / profile-card / wallet-summary / mine-orders / 88px quick-grid / 24px纵向mine-menu-grid | 切店统一顶栏并保留整页上下文隔离；会员权益/账号服务分组，复用真实权益、规范、公告入口；不复制假会员、余额。 |
| `OrderDetailPage` | `src/business/orders.tsx/OrderPage` | `/order-detail?id=…` | order-status-hero正常深色/付款未知警示/取消中性 / 34px金额 / 编号复制分栏 / 左进度评价与右资料售后 / 固定操作条 / 报名身份与赠礼记录 | allowed_actions和bossOrderPresentation控制所有可用动作。 |
| `OrdersPage` | `src/business/orders.tsx/OrdersPage` | `/orders` | boss-orders-list / order-card-number / service / meta / footer | 支付未知时隐藏派单进度；保留原实付、退款和礼物单筛选。 |
| `PointsPage` | `src/business/community.tsx/PointsPage` | `/points[?tab=claims|ledger]` | points-summary / KOOK独立卡 / 两列rewards-grid / 权益与明细 | 使用真实库存、积分、交付、绑定状态和原兑换凭证。 |
| `ProfilePage` | `src/business/account.tsx/ProfilePage` | `/profile` | profile-edit账户/常用游戏分栏 / profile-phone / 保存卡 | 真实头像上传、完整游戏字段、默认游戏与dirty保护。 |
| `RankingsPage` | `src/business/community.tsx/RankingsPage` | `/rankings` | ranking-overview / 等宽ranking-groups / 名次头像金额行 | 沿用实际consume_rank/order_rank与total_amount，不改成示例计数。 |
| `ReservationCreatePage` | `src/business/reservations.tsx/ReservationCreatePage` | `/reservation-create?service_id=…&provider_id=…` | reservation-create-layout / 服务、时段、游戏分节 / submit摘要 | 保留北京时间15分钟、数量、真实报价前预估与未知结果核对。 |
| `ReservationDetailPage` | `src/business/reservations.tsx/ReservationPage` | `/reservation-detail?id=…` | reservation-status / 服务游戏操作卡 / 右侧核对成单 | 预估不直接扣款；实时quote与allowed_actions决定取消/成单。 |
| `ReservationsPage` | `src/business/reservations.tsx/ReservationsPage` | `/reservations` | next-reservation / reservations-list / 桌面横排时间与金额 | 下一次摘要分别读取真实待确认/已确认最早记录与服务端total，不用当前筛选游标页冒充全量进行中数。 |
| `RewardClaimPage` | `src/business/community.tsx/ClaimPage` | `/reward-claim?id=…` | claim-current / claim-progress / claim-delivery / 冲突草稿提示 | 物理/虚拟/TAG/冠名字段、期限、版本和复核以接口为准。 |
| `SecurityPage` | `src/business/account.tsx/SecurityPage` | `/security` | security-status / 微信与手机号两卡 / 帮助 / 退出确认 | 不在网页手填手机号冒充微信授权；绑定流程进入真实桥接。 |
| `ServiceCatalogPage` | `src/business/commerce.tsx/CatalogPage` | `/catalog` | catalog-header / game-filter / catalog-filters / 横向96×112服务卡 | 保留套餐分组、平台难度、指定陪玩与优惠券上下文。 |
| `ServiceDetailPage` | `src/business/commerce.tsx/ServicePage` | `/service?id=…` | service-detail-hero / facts / variant-grid / actionbar / 规则评价 | 咨询、附加项、双陪、真实规则和评价读取失败都保留。 |
| `SupportPage` | `src/business/content.tsx/SupportPage` | `/support[?scene=recharge|order_id=…]` | support-context / recharge提醒 / channels / other真实QR | 原生HTTPS客服链接不能伪造打开成功；平台会话作实际备用入口。 |
| `WalletPage` | `src/business/account.tsx/WalletPage` | `/wallet[?tx_type=REFUND]` | wallet-layout余额横跨与冻结分栏 / 带图标transactions与变动后余额 / recharge-aside / 充值确认Dialog / Explanation | 保持真实分类、流水编号、退款/赠礼/推荐收益和分页单位。 |

## 全部40组363状态的代码绑定

每行均来自当前原型ScenarioOptions清单。共同状态统一复用真实状态组件，特殊状态逐项列出条件。纯演示状态与实际业务冲突时明确记录差异，不增加scenario参数或假数据开关。

### LoginPage · loginScenarioOptions

实际：`src/business/account.tsx/LoginPage`，`/login[?next=…|verify_phone=1]`。结构：boss-login-layout / welcome / benefits / authorization / 真实QR。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 默认登录 | flow为空：真实微信扫码按钮；boss-login-authorization，无密码/手机号直登。 |
| `verifying` · 账号核验中 | busy获取票据、status=PENDING轮询：按钮禁用、二维码与有效期role=status。 |
| `failure` · 登录失败 | flowError或web-login/start/status失败：error提示、重新获取码与客服；手机号能力失败在真实微信桥接页展示。 |
| `binding` · 找回并绑定 | verify_phone=1 / purpose=bind_phone：保留当前owner与revision核验；微信桥接执行手机号授权/短信。 |
| `unavailable` · 手机号不可用 | flowError或web-login/start/status失败：error提示、重新获取码与客服；手机号能力失败在真实微信桥接页展示。 |

### HomePage · homeScenarioOptions

实际：`src/business/account.tsx/HomePage`，`/home`。结构：20px门店标题 / SearchField / hero徽标与源圈线 / 公告条 / service-progress标题操作 / 76px shortcuts / 80px玩法封面。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 已登录首页 | 真实接口数据可用时由account.tsx/HomePage渲染：20px门店标题 / SearchField / hero徽标与源圈线 / 公告条 / service-progress标题操作 / 76px shortcuts / 80px玩法封面；不加载原型示例数据。 |
| `guest` · 游客首页 | session无token：只请求公开玩法/内容，不发私有订单/钱包请求；保留登录导航。 |
| `loading` · 首页加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 服务加载失败 | 玩法请求失败以`data.gameplaysError`在玩法模块显示State错误与reload，已加载的真实服务进度保留；公开基础请求整体失败仍由页面State处理，不能用空列表冒充成功。 |
| `popup` · 入场活动弹窗 | posterIndex非null且API有活动/ORDER_RULES image_url：真实Dialog、切换海报、显式关闭记忆。 |

### CompanionsPage · companionScenarioOptions

实际：`src/business/commerce.tsx/CompanionsPage`，`/companions`。结构：SearchField / 横向游戏 / 两/三列条件 / 56头像 / 标签声音价目卡。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 陪玩目录 | 真实接口数据可用时由commerce.tsx/CompanionsPage渲染：SearchField / 横向游戏 / 两/三列条件 / 56头像 / 标签声音价目卡；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 列表加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 列表加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 无匹配结果 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `service` · 按服务挑选 | serviceId存在：按真实服务资格筛选，picker服务上下文与平台安排入口。 |

### CompanionPickerPage · companionPickerScenarioOptions

实际：`src/business/commerce.tsx/CompanionsPage picker`，`/companion-picker?service_id=…`。结构：boss-picker-service / picker-list / 选中与失效提示。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 单陪服务选人 | 真实接口数据可用时由commerce.tsx/CompanionsPage picker渲染：boss-picker-service / picker-list / 选中与失效提示；不加载原型示例数据。 |
| `double` · 双陪套餐选人 | providerLimit(service)=2：已选人数、选齐校验与套餐总价不变说明。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 可选陪玩加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 可选陪玩加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无可选陪玩 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `selection-changed` · 已选状态已变化 | invalidSelected有值：boss-feedback error；保留选择，允许取消，不静默换人。 |
| `validating` · 接单信息核对中 | busy/confirmSelection真实资格复核：锁定确认；异常保留selected并由ActionFeedback显示，可重试。 |
| `validate-error` · 接单信息核对失败 | busy/confirmSelection真实资格复核：锁定确认；异常保留selected并由ActionFeedback显示，可重试。 |
| `checkout` · 从订单重新选择 | selected_provider_ids / provider_gender / coupon_id在query中保留；确认后回checkout。 |

### CompanionDetailPage · companionDetailScenarioOptions

实际：`src/business/commerce.tsx/CompanionPage`，`/companion-detail?id=…`。结构：boss-companion-profile 96头像与24px名称 / voice / price-card / work / exclusives / reserve / actionbar。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 陪玩资料 | 真实接口数据可用时由commerce.tsx/CompanionPage渲染：boss-companion-profile 96头像与24px名称 / voice / price-card / work / exclusives / reserve / actionbar；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `unavailable` · 暂不可约 | provider.can_order=false与order_unavailable_reason：状态提示，选择按钮禁用。 |
| `service` · 单人服务 | serviceId与from_picker/selected决定单人预约入口、多陪返回列表、已选与人数上限；不改变套餐资格。 |
| `picker` · 多人服务挑选 | serviceId与from_picker/selected决定单人预约入口、多陪返回列表、已选与人数上限；不改变套餐资格。 |

### ServiceCatalogPage · catalogScenarioOptions

实际：`src/business/commerce.tsx/CatalogPage`，`/catalog`。结构：catalog-header / game-filter / catalog-filters / 横向96×112服务卡。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 服务商城 | 真实接口数据可用时由commerce.tsx/CatalogPage渲染：catalog-header / game-filter / catalog-filters / 横向96×112服务卡；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 目录加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 目录加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 无匹配服务 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `provider` · 指定陪玩 | provider_id/coupon_id真实请求与当前范围条；只展示资格/券适用服务，移除上下文按钮保留。 |
| `coupon` · 优惠券适用 | provider_id/coupon_id真实请求与当前范围条；只展示资格/券适用服务，移除上下文按钮保留。 |

### ServiceDetailPage · serviceDetailScenarioOptions

实际：`src/business/commerce.tsx/ServicePage`，`/service?id=…`。结构：service-detail-hero / facts / variant-grid / actionbar / 规则评价。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 服务详情 | 真实接口数据可用时由commerce.tsx/ServicePage渲染：service-detail-hero / facts / variant-grid / actionbar / 规则评价；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `consult` · 客服确认方案 | is_addon / purchase_mode=CONSULT：客服确认方案入口；不伪装直接下单。 |
| `provider` · 已指定陪玩 | load.data.provider存在：已指定姓名上下文条及确认服务query透传。 |
| `review-error` · 评价加载失败 | reviews.error：compact State局部错误+reload，主体服务内容保留。 |

### CheckoutPage · checkoutScenarioOptions

实际：`src/business/checkout.tsx/CheckoutPage`，`/checkout?service_id=…`。结构：服务安排与游戏资料分区 / boss-checkout-quote / submit。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 确认下单 | 真实接口数据可用时由checkout.tsx/CheckoutPage渲染：服务安排与游戏资料分区 / boss-checkout-quote / submit；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 下单信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 下单信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `provider` · 已指定陪玩 | ids与providerLimit：指定资格、单双人数和dispatch路由按现有payload/prepare实时核验。 |
| `double` · 双陪套餐 | ids与providerLimit：指定资格、单双人数和dispatch路由按现有payload/prepare实时核验。 |
| `quoting` · 正在报价 | quoting / quoteError：本次报价内compact骨架或错误重试；quote为空/失败时禁止提交。 |
| `quote-error` · 报价失败 | quoting / quoteError：本次报价内compact骨架或错误重试；quote为空/失败时禁止提交。 |
| `insufficient` · 余额不足 | quote.balance_sufficient=false：余额不足提示、充值入口、提交禁用。 |
| `pending` · 上次结果待核对 | readIntent(order)：Pending原金额/原申请与核对/重试；持久化凭证未解决不能新下单。 |
| `zero` · 零元订单 | quote.amount=0：保留实际0浪花与真实确认流程，不显示假扣款结果。 |
| `closed` · 自主下单关闭 | traffic.self_order_enabled=false：自主下单关闭告警与客服入口，提交禁用。 |

### OrdersPage · ordersScenarioOptions

实际：`src/business/orders.tsx/OrdersPage`，`/orders`。结构：boss-orders-list / order-card-number / service / meta / footer。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 订单列表 | 真实接口数据可用时由orders.tsx/OrdersPage渲染：boss-orders-list / order-card-number / service / meta / footer；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 订单加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 订单加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无订单 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `pending` · 待接单与报名 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |
| `review` · 完成待评价 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |
| `refund` · 取消与退款 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |
| `gifts` · 礼物订单 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |
| `payment-pending` · 支付待确认 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |
| `aftersale` · 售后处理中 | status/giftsOnly、allowed_actions、refund_summary、aftersale_status驱动卡片；isUnconfirmed先隐藏陪玩进度；详情入口沿用真实订单。 |

### OrderDetailPage · orderDetailScenarioOptions

实际：`src/business/orders.tsx/OrderPage`，`/order-detail?id=…`。结构：order-status-hero正常深色/付款未知警示/取消中性 / 34px金额 / 编号复制分栏 / 左进度评价与右资料售后 / 固定操作条 / 报名身份与赠礼记录。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 订单详情 | 真实接口数据可用时由orders.tsx/OrderPage渲染：order-status-hero正常深色/付款未知警示/取消中性 / 34px金额 / 编号复制分栏 / 左进度评价与右资料售后 / 固定操作条 / 报名身份与赠礼记录；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `pending` · 报名选人 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `grabbed` · 已接单 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `in-service` · 服务中 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `review` · 多人待评价 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `refund` · 取消与退款 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `gift` · 礼物详情 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `payment-pending` · 支付待确认 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |
| `aftersale` · 售后处理中 | order.status/payment_status/allowed_actions与bossOrderPresentation：报名、履约、分人评价、原实付/退款记录、礼物/售后卡；支付未知禁用危险动作。 |

### WalletPage · walletScenarioOptions

实际：`src/business/account.tsx/WalletPage`，`/wallet[?tx_type=REFUND]`。结构：wallet-layout余额横跨与冻结分栏 / 带图标transactions与变动后余额 / recharge-aside / 充值确认Dialog / Explanation。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 我的钱包 | 真实接口数据可用时由account.tsx/WalletPage渲染：wallet-layout余额横跨与冻结分栏 / 带图标transactions与变动后余额 / recharge-aside / 充值确认Dialog / Explanation；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 钱包加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 钱包加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无流水 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `refund` · 退款流水 | filter=REFUND由query或分类控件决定，真实备注、变化额、balance_after同时展示。 |
| `more-error` · 分页加载失败 | 分页page改变失败→State错误+重试/返回上一页；沿用分页替换模式，不将上一页冒充当前页。 |

### GiftsPage · giftsScenarioOptions

实际：`src/business/gifts.tsx/GiftsPage`，`/gifts[?order_id=…|provider_id=…]`。结构：boss-gift-recipient / 56图标tile-main点击加件 / tile.selected / summary / receipt-icon与金额 / Explanation。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 礼物商城 | 真实接口数据可用时由gifts.tsx/GiftsPage渲染：boss-gift-recipient / 56图标tile-main点击加件 / tile.selected / summary / receipt-icon与金额 / Explanation；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 礼物加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 礼物加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无礼物 | `visibleGifts`为空：当前分类或全目录分别显示EmptySurface；分类空态有查看全部礼物动作。无结果不渲染空网格。 |
| `order` · 从订单赠礼 | orderId→gifts/context来源订单与本单收礼者限制；返回原订单与source_order说明。 |
| `pending` · 赠送待核对 | gift intent存在/blocked：Pending警示、原回执查询/原请求重试/记录/客服；blocked禁用重试。 |
| `pending-blocked` · 赠送结果受阻 | gift intent存在/blocked：Pending警示、原回执查询/原请求重试/记录/客服；blocked禁用重试。 |
| `insufficient` · 余额不足 | total>context.balance：保留basket、余额不足告警、充值入口、赠送禁用。 |
| `wallet-inactive` · 钱包不可用 | context.wallet_active=false：明确钱包不可用告警+客服，禁止赠送。 |

### MinePage · mineScenarioOptions

实际：`src/business/account.tsx/MinePage`，`/mine`。结构：mine-layout / profile-card / wallet-summary / mine-orders / 88px quick-grid / 24px纵向mine-menu-grid。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 个人中心 | 真实接口数据可用时由account.tsx/MinePage渲染：mine-layout / profile-card / wallet-summary / mine-orders / 88px quick-grid / 24px纵向mine-menu-grid；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 个人中心加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 个人中心加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `unbound` · 微信未绑定 | profile.isWechatBound=false：账号安全入口；不把未绑定显示为已授权。 |
| `basic` · 普通会员 | 真实member无vip或积分/订单为0：直接呈现真实普通账号、0值，不生成示例权益。 |

### MessagesPage · messagesScenarioOptions

实际：`src/business/content.tsx/MessagesPage`，`/messages`。结构：messages-layout主内容+快捷侧栏 / notice-row / feed-row。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 我的通知 | 真实接口数据可用时由content.tsx/MessagesPage渲染：messages-layout主内容+快捷侧栏 / notice-row / feed-row；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 消息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 消息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无通知 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `more-error` · 分页加载失败 | before变更请求失败：通知State错误、reload；原筛选/游标保留，按现有替换分页而非模拟追加。 |
| `feed` · 俱乐部动态 | section=feed，独立feedKind/feedBefore；真实items空→指定动态空态，feed.error→独立State错误。 |
| `feed-empty` · 暂无俱乐部动态 | section=feed，独立feedKind/feedBefore；真实items空→指定动态空态，feed.error→独立State错误。 |
| `feed-error` · 动态加载失败 | section=feed，独立feedKind/feedBefore；真实items空→指定动态空态，feed.error→独立State错误。 |

### ChatPage · chatScenarioOptions

实际：`src/business/content.tsx/ChatPage`，`/chat[?order_id=…]`。结构：boss-chat-header / room / message-row / composer / 附件与待核对条。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 平台会话 | 真实接口数据可用时由content.tsx/ChatPage渲染：boss-chat-header / room / message-row / composer / 附件与待核对条；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 会话加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 会话加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无会话记录 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `order` · 关联订单咨询 | orderId→context订单读取、前缀与切换普通咨询；context失败禁发并可重试。 |
| `pending` · 发送结果待核对 | readChatPending、pending.restored、deliverChatMessage失败：原消息核对/重试、放弃后果确认，缺本机图片禁重传。 |
| `pending-restored` · 恢复待核对消息 | readChatPending、pending.restored、deliverChatMessage失败：原消息核对/重试、放弃后果确认，缺本机图片禁重传。 |
| `send-error` · 发送结果未知 | readChatPending、pending.restored、deliverChatMessage失败：原消息核对/重试、放弃后果确认，缺本机图片禁重传。 |
| `history-error` · 历史加载失败 | older()失败经ActionFeedback显示；已加载items与historyCursor不清空，原加载更早按钮可重试。 |

### SupportPage · supportScenarioOptions

实际：`src/business/content.tsx/SupportPage`，`/support[?scene=recharge|order_id=…]`。结构：support-context / recharge提醒 / channels / other真实QR。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 客服与帮助 | 真实接口数据可用时由content.tsx/SupportPage渲染：support-context / recharge提醒 / channels / other真实QR；不加载原型示例数据。 |
| `guest` · 游客访问 | session无token：请求/support/public-contact/，显示真实公开客服；平台会话仍受Private保护。 |
| `loading` · 客服信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 客服信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `order` · 关联订单咨询 | card.order_no/customer_reference与scene=recharge：咨询编号卡、复制信息、付款核对警示。 |
| `recharge` · 钱包充值咨询 | card.order_no/customer_reference与scene=recharge：咨询编号卡、复制信息、付款核对警示。 |
| `opening` · 正在打开企业微信 | 业务差异：现有HTTPS原生a链接，没有可验证打开完成回调；不复制原型setTimeout成功/失败，保持平台会话备用入口。 |
| `open-error` · 企业微信打开失败 | 业务差异：现有HTTPS原生a链接，没有可验证打开完成回调；不复制原型setTimeout成功/失败，保持平台会话备用入口。 |
| `unsupported` · 当前微信不支持 | wecom_service_url缺失或不是HTTPS：配置不可用提示，保留真实平台会话；不造不可用URL。 |
| `unavailable` · 企业微信未配置 | wecom_service_url缺失或不是HTTPS：配置不可用提示，保留真实平台会话；不造不可用URL。 |
| `empty` · 联系方式待配置 | 真实card为空且非loading：客服资料未配置说明，已登录可进入平台会话。 |

### SecurityPage · securityScenarioOptions

实际：`src/business/account.tsx/SecurityPage`，`/security`。结构：security-status / 微信与手机号两卡 / 帮助 / 退出确认。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 账号与微信绑定 | 真实接口数据可用时由account.tsx/SecurityPage渲染：security-status / 微信与手机号两卡 / 帮助 / 退出确认；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 账号信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 账号信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `wechat-unbound` · 微信尚未绑定 | isWechatBound/isPhoneVerified=false：保护提示、分别的微信与手机号卡、真实授权入口。 |
| `phone-unverified` · 手机号尚未验证 | isWechatBound/isPhoneVerified=false：保护提示、分别的微信与手机号卡、真实授权入口。 |
| `busy-wechat` · 微信绑定处理中 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |
| `busy-phone` · 手机号验证处理中 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |
| `phone-denied` · 拒绝手机号授权 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |
| `bind-error` · 微信绑定失败 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |
| `unsupported` · 环境不支持授权 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |
| `success` · 绑定信息已更新 | 业务差异：授权由LoginPage+微信桥接处理（busy/agreed/requiresPhoneAuthorization/requiresSms/error/confirmed），网页只读真实profile状态，不模拟手机号授权成功。 |

### ProfilePage · profileScenarioOptions

实际：`src/business/account.tsx/ProfilePage`，`/profile`。结构：profile-edit账户/常用游戏分栏 / profile-phone / 保存卡。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 个人与游戏资料 | 真实接口数据可用时由account.tsx/ProfilePage渲染：profile-edit账户/常用游戏分栏 / profile-phone / 保存卡；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 资料加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 资料加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `dirty` · 有未保存修改 | dirty=true保留输入，显示未保存修改；仅显式save成功后清dirty。 |
| `saving` · 资料保存中 | busy真实PATCH：表单锁定；失败ActionFeedback且dirty不清，成功toast+updateProfile并reload。 |
| `save-error` · 资料保存失败 | busy真实PATCH：表单锁定；失败ActionFeedback且dirty不清，成功toast+updateProfile并reload。 |
| `avatar-saving` · 头像上传中 | avatarBusy与upload/me/avatar：正在保存头像、上传期间禁用；失败不覆盖昵称与游戏草稿。 |
| `avatar-error` · 头像更新失败 | avatarBusy与upload/me/avatar：正在保存头像、上传期间禁用；失败不覆盖昵称与游戏草稿。 |
| `phone-empty` · 手机号未设置 | me.phone为空：boss-profile-phone显示未绑定并链接账号安全。 |
| `empty-games` · 暂无游戏分类 | profiles为空：明确无可用游戏资料说明，默认游戏Choice禁用，账户资料仍可保存。 |
| `success` · 资料保存成功 | busy真实PATCH：表单锁定；失败ActionFeedback且dirty不清，成功toast+updateProfile并reload。 |

### ReservationsPage · reservationsScenarioOptions

实际：`src/business/reservations.tsx/ReservationsPage`，`/reservations`。结构：next-reservation / reservations-list / 桌面横排时间与金额。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 我的预约 | 真实接口数据可用时由reservations.tsx/ReservationsPage渲染：next-reservation / reservations-list / 桌面横排时间与金额；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 预约加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 预约加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无预约 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `more-loading` · 分页加载中 | before游标变更：新页loading骨架/错误重试，emptyAction可返回最新；不混用上一页记录。 |
| `more-error` · 分页加载失败 | before游标变更：新页loading骨架/错误重试，emptyAction可返回最新；不混用上一页记录。 |
| `all-statuses` · 全部预约状态 | status为空读取全部真实状态；PENDING/CONFIRMED/CONVERTED/REJECTED/CANCELLED/EXPIRED使用StatusBadge。 |

### ReservationDetailPage · reservationDetailScenarioOptions

实际：`src/business/reservations.tsx/ReservationPage`，`/reservation-detail?id=…`。结构：reservation-status / 服务游戏操作卡 / 右侧核对成单。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 已确认 · 待报价 | 真实接口数据可用时由reservations.tsx/ReservationPage渲染：reservation-status / 服务游戏操作卡 / 右侧核对成单；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `pending` · 等待陪玩确认 | status=PENDING：待陪玩确认，不显示可成单；allowed_actions决定取消。 |
| `quoting` · 实时报价中 | readQuote中的busy/quote/null及失败：获取最新报价锁定、真实金额核对、ActionFeedback失败保留预约。 |
| `quote-ready` · 报价已更新 | readQuote中的busy/quote/null及失败：获取最新报价锁定、真实金额核对、ActionFeedback失败保留预约。 |
| `quote-error` · 服务信息已变化 | readQuote中的busy/quote/null及失败：获取最新报价锁定、真实金额核对、ActionFeedback失败保留预约。 |
| `insufficient` · 钱包余额不足 | quote.balance_sufficient=false：确认成单禁用，余额/实际应付与充值入口可见。 |
| `convert-pending` · 成单结果待核对 | reservation-convert-id凭证：Pending原请求，不创建另一笔成单。 |
| `converted` · 预约已成单 | 真实reservation.status与order/reject_reason/cancel_reason：对应StatusBadge/说明、关联订单/重新预约，按allowed_actions隐藏不可操作按钮。 |
| `cancelled` · 预约已取消 | 真实reservation.status与order/reject_reason/cancel_reason：对应StatusBadge/说明、关联订单/重新预约，按allowed_actions隐藏不可操作按钮。 |
| `rejected` · 陪玩已拒绝 | 真实reservation.status与order/reject_reason/cancel_reason：对应StatusBadge/说明、关联订单/重新预约，按allowed_actions隐藏不可操作按钮。 |
| `expired` · 预约已过期 | 真实reservation.status与order/reject_reason/cancel_reason：对应StatusBadge/说明、关联订单/重新预约，按allowed_actions隐藏不可操作按钮。 |

### ReservationCreatePage · reservationCreateScenarioOptions

实际：`src/business/reservations.tsx/ReservationCreatePage`，`/reservation-create?service_id=…&provider_id=…`。结构：reservation-create-layout / 服务、时段、游戏分节 / submit摘要。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 新建预约 | 真实接口数据可用时由reservations.tsx/ReservationCreatePage渲染：reservation-create-layout / 服务、时段、游戏分节 / submit摘要；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 预约信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 预约信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `invalid-time` · 时间步长错误 | submit实际校验未来时间、15分钟与正整数；ActionFeedback保留start/duration/form，HTML约束保留。 |
| `invalid-rounds` · 服务数量错误 | submit实际校验未来时间、15分钟与正整数；ActionFeedback保留start/duration/form，HTML约束保留。 |
| `submitting` · 预约提交中 | busy时表单与提交禁用；真实异常在页面/确认Dialog显示，不清输入。 |
| `submit-error` · 明确提交失败 | busy时表单与提交禁用；真实异常在页面/确认Dialog显示，不清输入。 |
| `uncertain` · 提交结果待核对 | sessionStorage pendingKey未解决：明确原时段、核对预约列表与确认已核对后再继续；不伪造提交成功。 |

### CouponsPage · couponsScenarioOptions

实际：`src/business/community.tsx/CouponsPage`，`/coupons`。结构：boss-coupon-benefit 96px / body / details / 领用状态按钮。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 我的券 | 真实接口数据可用时由community.tsx/CouponsPage渲染：boss-coupon-benefit 96px / body / details / 领用状态按钮；不加载原型示例数据。 |
| `available` · 领券中心 | tab=available读取/coupons，benefit/threshold/claimLabel来自couponPresentation。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 优惠券加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 优惠券加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无优惠券 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `claiming` · 优惠券领取中 | busy与真实领取请求；未知网络/5xx设置claimIssue，先查看我的券，finally刷新真实状态。 |
| `claim-uncertain` · 领取结果待核对 | busy与真实领取请求；未知网络/5xx设置claimIssue，先查看我的券，finally刷新真实状态。 |
| `use-error` · 使用状态已变化 | useCoupon重新读取usable券；失效/套餐待配置异常→ActionFeedback，不能按旧券强行下单。 |
| `used` · 已使用 | availability_status/status=USED/EXPIRED/DISABLED：真实我的券状态筛选与使用按钮禁用。 |
| `expired` · 已过期 | availability_status/status=USED/EXPIRED/DISABLED：真实我的券状态筛选与使用按钮禁用。 |
| `disabled` · 已停用 | availability_status/status=USED/EXPIRED/DISABLED：真实我的券状态筛选与使用按钮禁用。 |

### MembershipPage · membershipScenarioOptions

实际：`src/business/community.tsx/MembershipPage`，`/membership`。结构：boss-member-summary / 独立会员权益 / 标签 / 生日卡。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 成长中会员 | 真实接口数据可用时由community.tsx/MembershipPage渲染：boss-member-summary / 独立会员权益 / 标签 / 生日卡；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 会员信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 会员信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `basic` · 普通老板 | member.vip/boss_type/next_vip/tiers判定当前等级、最高等级、未配置；金额用真实人民币实销。 |
| `highest` · 当前最高等级 | member.vip/boss_type/next_vip/tiers判定当前等级、最高等级、未配置；金额用真实人民币实销。 |
| `unconfigured` · 等级待配置 | member.vip/boss_type/next_vip/tiers判定当前等级、最高等级、未配置；金额用真实人民币实销。 |
| `empty-tags` · 暂无可展示标签 | member.tags为空：无可展示标签说明+成就入口。 |
| `tag-saving` · 标签保存中 | busy更新selected_tags；失败保留selected/dirty，成功真实响应toast+reload。 |
| `tag-error` · 标签保存失败 | busy更新selected_tags；失败保留selected/dirty，成功真实响应toast+reload。 |
| `tag-saved` · 标签保存成功 | busy更新selected_tags；失败保留selected/dirty，成功真实响应toast+reload。 |
| `refresh-pending` · 会员信息待刷新 | 业务差异：既有useEffect在dirty时保留草稿；refresh不丢弃选择，不移植演示的强制撤销流程。 |
| `birthday-empty` · 生日未登记 | birthday为空才允许登记；确认后真实PATCH，busy锁定，失败保留输入并显示错误。 |
| `birthday-saving` · 生日设置保存中 | birthday为空才允许登记；确认后真实PATCH，busy锁定，失败保留输入并显示错误。 |
| `birthday-error` · 生日设置失败 | birthday为空才允许登记；确认后真实PATCH，busy锁定，失败保留输入并显示错误。 |
| `save-refresh-error` · 保存成功刷新失败 | 更新成功后reload失败：保留同条件旧快照与State错误，区分已保存toast和读取错误，不宣称写失败。 |

### PointsPage · pointsScenarioOptions

实际：`src/business/community.tsx/PointsPage`，`/points[?tab=claims|ledger]`。结构：points-summary / KOOK独立卡 / 两列rewards-grid / 权益与明细。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 积分与好礼 | 真实接口数据可用时由community.tsx/PointsPage渲染：points-summary / KOOK独立卡 / 两列rewards-grid / 权益与明细；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 积分信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 积分信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无积分好礼 | 真实rewards为空→EmptySurface“暂无积分好礼”；claims/ledger空则各显示独立EmptySurface，不影响真实积分概览。 |
| `insufficient` · 积分不足 | points<point_cost / stock=0：积分不足/已兑完按钮文字与禁用；不是示例固定库存。 |
| `soldout` · 好礼已兑完 | points<point_cost / stock=0：积分不足/已兑完按钮文字与禁用；不是示例固定库存。 |
| `pending` · 兑换结果待确认 | redemption intent与busy：Pending原积分、重试原申请；兑换条款expected_terms核验保留。 |
| `redeeming` · 兑换处理中 | redemption intent与busy：Pending原积分、重试原申请；兑换条款expected_terms核验保留。 |
| `claims` · 权益记录 | tab=claims/ledger：真实权益交付状态与积分明细，独立游标。 |
| `ledger` · 积分明细 | tab=claims/ledger：真实权益交付状态与积分明细，独立游标。 |
| `more-error` · 明细分页失败 | claimBefore/ledgerBefore请求失败→错误/重试/返回最新记录；当前业务采用替换分页，不复制旧页。 |
| `kook-bound` · KOOK 已绑定 | data.kook.bound与真实nickname/kook_user_id，未绑定时真实获取绑定码，不生成演示代码。 |

### LotteryPage · lotteryScenarioOptions

实际：`src/business/community.tsx/LotteryPage + lottery.css`，`/lottery`。结构：lottery-account / 动态SVG转盘 / 双抽取 / odds-row / result-card。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 幸运转盘 | 真实接口数据可用时由community.tsx/LotteryPage + lottery.css渲染：lottery-account / 动态SVG转盘 / 双抽取 / odds-row / result-card；不加载原型示例数据。 |
| `loading` · 转盘加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 转盘加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `drawing` · 抽奖结果确认中 | phase=request/summon/reveal/done由submitIntent真实response驱动，result.count与revealed决定单/十连卡；可跳过动态。 |
| `revealing` · 奖品揭晓中 | phase=request/summon/reveal/done由submitIntent真实response驱动，result.count与revealed决定单/十连卡；可跳过动态。 |
| `success` · 单抽结果 | phase=request/summon/reveal/done由submitIntent真实response驱动，result.count与revealed决定单/十连卡；可跳过动态。 |
| `ten-success` · 十连结果 | phase=request/summon/reveal/done由submitIntent真实response驱动，result.count与revealed决定单/十连卡；可跳过动态。 |
| `pending` · 抽奖结果待核对 | readIntent(lottery)存在/blocked：原凭证警示，blocked明确不可读取且禁重试；保留权益记录和客服入口。 |
| `credential-error` · 抽奖凭证不可读 | readIntent(lottery)存在/blocked：原凭证警示，blocked明确不可读取且禁重试；保留权益记录和客服入口。 |
| `accepted-error` · 已受理展示异常 | accepted=true但返回结构校验失败：drawError提示已受理、去权益核对，不重建抽奖。 |
| `disabled` · 转盘暂未开放 | campaign.enabled=false / points<cost / daily_remaining=0：真实说明和禁用，日限额及十连资格继续校验。 |
| `insufficient` · 积分不足 | campaign.enabled=false / points<cost / daily_remaining=0：真实说明和禁用，日限额及十连资格继续校验。 |
| `daily-empty` · 今日次数已用完 | campaign.enabled=false / points<cost / daily_remaining=0：真实说明和禁用，日限额及十连资格继续校验。 |
| `empty` · 奖池准备中 | campaign为空且加载成功：State专用无活动空态+返回积分。 |

### RewardClaimPage · rewardClaimScenarioOptions

实际：`src/business/community.tsx/ClaimPage`，`/reward-claim?id=…`。结构：claim-current / claim-progress / claim-delivery / 冲突草稿提示。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 实物奖品交付 | 真实接口数据可用时由community.tsx/ClaimPage渲染：claim-current / claim-progress / claim-delivery / 冲突草稿提示；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `virtual` · 虚拟物品交付 | kind_snapshot决定game_uid/note、tag、provider_name等真实字段，TAG期限/复核反馈保留。 |
| `tag` · TAG 待复核 | kind_snapshot决定game_uid/note、tag、provider_name等真实字段，TAG期限/复核反馈保留。 |
| `naming` · 陪玩冠名交付 | kind_snapshot决定game_uid/note、tag、provider_name等真实字段，TAG期限/复核反馈保留。 |
| `completed` · 奖品已完成 | claim.status=COMPLETED/CANCELLED：已提交资料、实付/积分退回信息与状态色，禁止无授权编辑。 |
| `cancelled` · 权益已取消 | claim.status=COMPLETED/CANCELLED：已提交资料、实付/积分退回信息与状态色，禁止无授权编辑。 |
| `loading` · 交付信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 交付信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 奖品记录不存在 | 业务差异：无id/404按现有接口作为无效权益错误呈现，保留返回入口，不伪造空记录。 |
| `saving` · 交付资料保存中 | busy PATCH expected_details；失败保留details/dirty并展示错误，提交按钮锁定。 |
| `save-error` · 交付资料保存失败 | busy PATCH expected_details；失败保留details/dirty并展示错误，提交按钮锁定。 |
| `conflict` · 交付资料冲突 | baseline/details比较与dirty&&!editable：草稿冲突/状态变化提示、复制草稿、确认替换最新资料；不覆盖他处更新。 |
| `status-changed` · 权益状态已变化 | baseline/details比较与dirty&&!editable：草稿冲突/状态变化提示、复制草稿、确认替换最新资料；不覆盖他处更新。 |

### FavoritesPage · favoritesScenarioOptions

实际：`src/business/community.tsx/FavoritesPage`，`/favorites`。结构：boss-favorite-undo / 横向80缩略卡 / footer取消动作。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 我的收藏 | 真实接口数据可用时由community.tsx/FavoritesPage渲染：boss-favorite-undo / 横向80缩略卡 / footer取消动作；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 收藏加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 收藏加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无收藏服务 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `removing` · 取消收藏中 | busy DELETE期间禁重复操作；失败ActionFeedback、finally reload，原卡片依据真实列表保留。 |
| `remove-error` · 取消收藏失败 | busy DELETE期间禁重复操作；失败ActionFeedback、finally reload，原卡片依据真实列表保留。 |
| `undo` · 取消后可撤销 | undoItem、busy与undo最新列表查询：可用服务可恢复，异常先刷新真实收藏，不盲目反复toggle。 |
| `undoing` · 恢复收藏中 | undoItem、busy与undo最新列表查询：可用服务可恢复，异常先刷新真实收藏，不盲目反复toggle。 |
| `undo-error` · 恢复结果待核对 | undoItem、busy与undo最新列表查询：可用服务可恢复，异常先刷新真实收藏，不盲目反复toggle。 |
| `unavailable` · 服务暂不可购买 | available=false：禁用详情购买入口，保留取消收藏；不可用服务不提供撤销。 |

### CheckinPage · checkinScenarioOptions

实际：`src/business/community.tsx/CheckinPage`，`/checkin`。结构：boss-checkin-summary / calendar / rewards；日期分行标签。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 今日可签到 | 真实接口数据可用时由community.tsx/CheckinPage渲染：boss-checkin-summary / calendar / rewards；日期分行标签；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 签到日历加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 签到日历加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `ineligible` · 今日消费未达标 | today_eligible/today_checked/makeup_available_days/makeup_cards：差额、已签、补签日期、消耗卡确认均依真实日历。 |
| `checked` · 今日已签到 | today_eligible/today_checked/makeup_available_days/makeup_cards：差额、已签、补签日期、消耗卡确认均依真实日历。 |
| `makeup` · 可补签日期 | today_eligible/today_checked/makeup_available_days/makeup_cards：差额、已签、补签日期、消耗卡确认均依真实日历。 |
| `no-cards` · 暂无补签卡 | today_eligible/today_checked/makeup_available_days/makeup_cards：差额、已签、补签日期、消耗卡确认均依真实日历。 |
| `submitting` · 签到提交中 | busy提交时签到补签禁用；finally reload核对服务器日历，错误ActionFeedback；不显示本地假成功。 |
| `submit-error` · 签到结果待核对 | busy提交时签到补签禁用；finally reload核对服务器日历，错误ActionFeedback；不显示本地假成功。 |
| `empty-rewards` · 本期奖励待公布 | rewards为空显示本期奖励待公布；checked_days与full_attendance_reward真实展示全勤，不复制固定128奖励。 |
| `full-attendance` · 本月全勤达成 | rewards为空显示本期奖励待公布；checked_days与full_attendance_reward真实展示全勤，不复制固定128奖励。 |

### AchievementsPage · achievementsScenarioOptions

实际：`src/business/community.tsx/AchievementsPage`，`/achievements`。结构：boss-achievement-summary / active / unlocked；按真实进度排序与Progress。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 成就收集中 | 真实接口数据可用时由community.tsx/AchievementsPage渲染：boss-achievement-summary / active / unlocked；按真实进度排序与Progress；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 成就加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 成就加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 成就内容待配置 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `no-progress` · 新老板起点 | current/target/unlocked：真实零进度、最近目标排序、全解锁仅显示已达成组，Progress不编造。 |
| `near-unlock` · 即将解锁 | current/target/unlocked：真实零进度、最近目标排序、全解锁仅显示已达成组，Progress不编造。 |
| `all-unlocked` · 全部成就达成 | current/target/unlocked：真实零进度、最近目标排序、全解锁仅显示已达成组，Progress不编造。 |

### RankingsPage · rankingsScenarioOptions

实际：`src/business/community.tsx/RankingsPage`，`/rankings`。结构：ranking-overview / 等宽ranking-groups / 名次头像金额行。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 本月榜单 | 真实接口数据可用时由community.tsx/RankingsPage渲染：ranking-overview / 等宽ranking-groups / 名次头像金额行；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 榜单加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 榜单加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 本期暂无上榜记录 | order_rank/consume_rank各自真实空数组：独立boss-ranking-empty，不影响另一榜单。 |
| `providers-empty` · 仅陪玩榜为空 | order_rank/consume_rank各自真实空数组：独立boss-ranking-empty，不影响另一榜单。 |
| `day` · 今日榜单 | period=day/week请求真实接口；切周期骨架，防止旧周期冒充当前榜单。 |
| `week` · 本周榜单 | period=day/week请求真实接口；切周期骨架，防止旧周期冒充当前榜单。 |
| `unavailable-provider` · 陪玩资料不可打开 | provider_account_id<=0只显示姓名不生成详情链接，保留名次与金额。 |

### AnnouncementsPage · announcementsScenarioOptions

实际：`src/business/content.tsx/AnnouncementsPage`，`/announcements`。结构：boss-public-overview / boss-public-directory / Pin与日期行。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 平台公告 | 真实接口数据可用时由content.tsx/AnnouncementsPage渲染：boss-public-overview / boss-public-directory / Pin与日期行；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 公告加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 公告加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无平台公告 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `limit` · 展示 20 条公告 | 真实limit=20列表；is_pinned、日期及break-word长标题行，不新增16条演示公告。 |
| `long-title` · 长公告标题 | 真实limit=20列表；is_pinned、日期及break-word长标题行，不新增16条演示公告。 |

### AnnouncementDetailPage · announcementDetailScenarioOptions

实际：`src/business/content.tsx/AnnouncementPage`，`/announcement-detail?id=…`。结构：ReadingHeader / boss-reading-body / 空正文分支。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 公告详情 | 真实接口数据可用时由content.tsx/AnnouncementPage渲染：ReadingHeader / boss-reading-body / 空正文分支；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `invalid` · 公告链接无效 | 无id或真实404进入State错误；BackLink返回公告列表，HTTP错误不伪造已下线状态。 |
| `not-found` · 公告不存在 | 无id或真实404进入State错误；BackLink返回公告列表，HTTP错误不伪造已下线状态。 |
| `empty-content` · 暂无公告正文 | content为空明确暂无正文；富文本长文保留段落/换行/可读行距，不复制演示补充段落。 |
| `long-content` · 长公告正文 | content为空明确暂无正文；富文本长文保留段落/换行/可读行距，不复制演示补充段落。 |

### ClubContentPage · clubContentScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?kind=RULES`。结构：boss-public-overview / directory / 图标与标题日期行。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 俱乐部规范 | 真实接口数据可用时由content.tsx/ContentPage渲染：boss-public-overview / directory / 图标与标题日期行；不加载原型示例数据。 |
| `loading` · 内容目录加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 内容目录加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无俱乐部内容 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `long-title` · 长规范标题 | 真实title完整换行、92px最小行高，不裁剪业务标题。 |

### ClubContentDetailPage · clubContentDetailScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?id=…`。结构：ReadingHeader / 原图链接 / 分节富文本阅读卡。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 规范图文详情 | 真实接口数据可用时由content.tsx/ContentPage渲染：ReadingHeader / 原图链接 / 分节富文本阅读卡；不加载原型示例数据。 |
| `loading` · 内容详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 内容详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `not-found` · 内容暂不可查看 | 真实HTTP失败由State显示；BackLink恢复真实规范目录。 |
| `empty` · 正文准备中 | 无image/content时正文准备中；有图可打开真实大图，长文RichContent分节，非演示段落。 |
| `long-content` · 长规范正文 | 无image/content时正文准备中；有图可打开真实大图，长文RichContent分节，非演示段落。 |

### ActivitiesPage · activitiesScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?kind=HOT`。结构：boss-activities-list / boss-activity-thumb 72×88 / 归档分页。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 俱乐部活动 | 真实接口数据可用时由content.tsx/ContentPage渲染：boss-activities-list / boss-activity-thumb 72×88 / 归档分页；不加载原型示例数据。 |
| `loading` · 活动目录加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 活动目录加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无俱乐部活动 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |
| `more` · 活动归档分页 | kind=HOT archive=1 page参数：每页30条；新页加载/失败保留真实page上下文，原有分页模式不替换为模拟追加。 |
| `more-loading` · 更多活动加载中 | kind=HOT archive=1 page参数：每页30条；新页加载/失败保留真实page上下文，原有分页模式不替换为模拟追加。 |
| `more-error` · 更多活动加载失败 | kind=HOT archive=1 page参数：每页30条；新页加载/失败保留真实page上下文，原有分页模式不替换为模拟追加。 |

### ActivityDetailPage · activityDetailScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?id=…（kind=HOT）`。结构：ReadingHeader / boss-content-poster / boss-reading-body。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 活动海报 | 真实接口数据可用时由content.tsx/ContentPage渲染：ReadingHeader / boss-content-poster / boss-reading-body；不加载原型示例数据。 |
| `loading` · 活动海报加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 活动海报加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `not-found` · 活动暂不可查看 | 真实404进错误；真实kind=HOT无image_url显示海报准备中，若有正文仍可阅读。 |
| `empty` · 活动海报准备中 | 真实404进错误；真实kind=HOT无image_url显示海报准备中，若有正文仍可阅读。 |

### GuideContentPage · guideContentScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?kind=RIGHTS 或 POINT_RULES`。结构：按kind选Crown/Trophy；overview / 内容目录。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 公开内容目录 | 真实接口数据可用时由content.tsx/ContentPage渲染：按kind选Crown/Trophy；overview / 内容目录；不加载原型示例数据。 |
| `loading` · 内容目录加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 内容目录加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `empty` · 暂无公开内容 | 真实列表为空：State→EmptySurface对应页面文案与下一步；不同时渲染默认空卡。 |

### GuideContentDetailPage · guideContentDetailScenarioOptions

实际：`src/business/content.tsx/ContentPage`，`/club-content?id=…（RIGHTS/POINT_RULES）`。结构：ReadingHeader / reading-body / 长文与正文准备中。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 完整规则阅读 | 真实接口数据可用时由content.tsx/ContentPage渲染：ReadingHeader / reading-body / 长文与正文准备中；不加载原型示例数据。 |
| `loading` · 内容详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 内容详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `not-found` · 内容暂不可查看 | 404由State错误；实际正文为空时阅读卡明确正文准备中；返回当前kind目录。 |
| `empty` · 正文准备中 | 404由State错误；实际正文为空时阅读卡明确正文准备中；返回当前kind目录。 |

### MessageDetailPage · messageDetailScenarioOptions

实际：`src/business/content.tsx/MessagePage`，`/message-detail?id=…`。结构：boss-notice-article / header / 正文 / related-content。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 订单通知详情 | 真实接口数据可用时由content.tsx/MessagePage渲染：boss-notice-article / header / 正文 / related-content；不加载原型示例数据。 |
| `guest` · 游客访问 | Private保留现有未登录跳转/login?next=原路由；由真实LoginPage微信引导接管，业务组件不挂载。 |
| `loading` · 消息详情加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 消息详情加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `invalid` · 消息链接无效 | 缺id真实错误；detail/content/preview全空显示暂无通知正文，保留真实标题与时间。 |
| `empty-content` · 暂无通知正文 | 缺id真实错误；detail/content/preview全空显示暂无通知正文，保留真实标题与时间。 |
| `wallet` · 充值到账通知 | bossMessageDestination按通知真实关联类型跳转钱包/权益记录，不固定演示目标。 |
| `claims` · 权益到账通知 | bossMessageDestination按通知真实关联类型跳转钱包/权益记录，不固定演示目标。 |

### AuditionPage · auditionScenarioOptions

实际：`src/business/content.tsx/AuditionPage`，`/audition?token=…&role=boss`。结构：boss-audition-event / boss-audition-account / 本人核对动作。

| 原型状态 | 真实条件与可查证的显示/动作 |
| --- | --- |
| `default` · 有效试音邀请 | 真实接口数据可用时由content.tsx/AuditionPage渲染：boss-audition-event / boss-audition-account / 本人核对动作；不加载原型示例数据。 |
| `existing-account` · 已有登录账号 | session.token存在显示当前账号；业务差异：验证本人邀请，不切换为其他账号。 |
| `loading` · 邀请信息加载中 | load.loading且无data：State→PageSkeleton对应路由结构；同条件有快照刷新仅更新条，busy/原action锁继续保留。 |
| `error` · 邀请信息加载失败 | load.error：State显示服务端/网络错误与reload；同条件真实旧快照可保留，跨对象/条件不展示旧对象。 |
| `invalid` · 邀请链接无效 | token/role参数校验或服务端真实拒绝：State/ActionFeedback明确原因与客服入口，未匹配不进入。 |
| `expired` · 试音链接已过期 | token/role参数校验或服务端真实拒绝：State/ActionFeedback明确原因与客服入口，未匹配不进入。 |
| `confirming` · 身份确认处理中 | busy调用audition/enter核对matched；处理中锁定，失败保留token，不签发模拟登录态。 |
| `exchange-error` · 进入结果失败 | busy调用audition/enter核对matched；处理中锁定，失败保留token，不签发模拟登录态。 |
| `unbound` · 邀请未绑定账号 | token/role参数校验或服务端真实拒绝：State/ActionFeedback明确原因与客服入口，未匹配不进入。 |

## 本轮新增修复与验证范围

- 重新逐页读取代码后补齐消息/通知/会话/客服/规范与权益规则/活动/公告/试音/登录/安全/礼物/收藏/榜单/交付/转盘，避免仅generic Panel换色。
- 补齐预约创建分节与摘要、钱包主列表和充值侧栏；保留所有真实校验、动作与持久化草稿。
- 首载、切条件、错误、空态、动作失败、凭证受阻、缺正文、无游戏配置、未知领取、钱包停用、积分不足/日限额等以真实条件绑定原型状态表达。
- 当前执行：`node fronted/node_modules/typescript/bin/tsc -b fronted/boss --pretty false`通过。未新增浏览器/截图验收，未将旧截图当本轮状态证明。
- 尚未证实：363个状态未逐一在真实隔离后端触发；真实微信扫码/手机号/SMS、企业微信打开结果、每种资金异常与并发链路不因本轮视觉代码检查而视为通过。
- 没有新增原型专用假场景、假余额/头像/二维码、演示计时器成功或认证方式。

补充骨架核对：按36个原型TSX提取Skeleton代码，已对应登录外的主状态形态：profile 384/520、wallet 208+384/176、service 288+160/176、邀请160/330、交付128/176/288、会员350/256/208、转盘430/128、picker四张224、公开活动详情460+44；缺kind的旧链接使用公开内容通用详情骨架，真实kind已知后使用对应正文，不注入猜测数据。


## 2026-11-05继续对齐：空间、细节与真实可达性

本轮继续读取原型源码，不使用浏览器/截图；以下是新增代码证据，不将样式文档当运行验收。

| 原型代码 | 本轮落地证据 | 状态与真实业务 |
| --- | --- | --- |
| `HomePage` hero/公告/进度/quickEntries/gameplays | `account.tsx/HomePage`；`surfaces.css`的hero badge、两条源圈线、64px AudioLines、源ocean渐变、公告44px行、service-progress-heading、76px快捷卡、80px玩法封面与md四列；进度shadow-lg/玩法shadow-sm来自原型 | 公告用真实第一条并链接真实详情；全部订单/服务可达。玩法失败单独错误重试，个人进度快照保留；真实玩法图片替代原型示例图标，无假数据。 |
| `MinePage` MenuGroup/MenuRow | `boss-mine-menu-grid`、双组标题分隔、56px行、16px muted图标、会员/积分/绑定状态右注、88px shortcuts；真实老板权益/规范/公告入口 | wallet/reservations/gifts/coupons/support/security均能从业务页面抵达；退出在Security确认。Login/Mine不再重复切店。 |
| `CompanionDetailPage` | `boss-companion-profile`中心头像96+状态点20、名字24，voice的44图标、独立price-card、关于TA13/24、work月金额与资格Check、标签/冠名分隔、真实预约卡、手机actionbar；无声音/标签/冠名有明确空内容 | can_order/原因控制可约；voice为真实audio；单人预约/多人挑选/已选/满员沿现有上下文；gift真实进入Gifts。擅长位置待开放见B-01。 |
| `WalletPage` TransactionRow及Dialogs | 余额36px/冻结20px分栏、40px流向图标、18px变动额、备注底色、余额和编号；`Explanation`与充值确认Dialog按源24px padding、44px操作、小圆角 | 金额/分类/分页保留真实口径；正负使用实际amount；确认后进入现有充值客服，不模拟充值或到账。 |
| `OrderDetailPage` 金额与状态摘要 | `boss-order-status-hero`、dark-summary/payment-unknown、中性取消、金额34px、编号/时间右栏及Copy按钮，手机分隔线重排 | 保留付款未知锁；仅已明确支付的非取消订单使用正常深色；金额标签区分待核对与实付。取消/客服/履约动作及原真实后果保留。 |
| `GiftsPage` GiftTile、回执与说明 | `boss-gift-tile-main`真实56图、176px点选区、ghost数量footer44、lg三列、选中边框/3.5%背景与shadow-sm；成功Check56/金额32，回执单独展示 | 点击图卡与+使用同一change和真实max_quantity/30款限制；成功来自send/lookup真实receipt，不同时显示购买表单；批量无单id也能进入礼物单列表。分类空态可重置。 |
| `CheckoutPage` 底部确认与说明 | `boss-checkout-submit-amount`24px实际金额与CTA，报价中/待核价文字，充值入口、`Explanation`余额与下单说明 | 全部原busy/quote/storage/intent/钱包/开关锁继续作用；不将预估当实际扣款或未知请求当失败。 |
| `MessagesPage` 说明Dialog | `Explanation`消息与隐私说明；原型位置的快捷侧栏尾部入口 | 消息、动态、会话均复用真实接口；只有文字说明，无新读写或示例切换。 |

- 页内明确卡片最大12px、选中控件6–8px、标签4px；圆形头像、状态点和源圈线保持圆形。
- 卡片阴影按原型显式`shadow-sm`/`shadow-lg`落在对应页面；订单hover使用原型`shadow-md`，未给所有Panel统一添加阴影。
- 按源恢复md768订单双列/预约四栏，lg1024礼物三列，xl1280资料左栏360；无演示目录对可用宽度的占用。
- 根据最新交互偏好，整行ghost/查看全部入口内部左右px12，小圆角，hover/active/focus-visible均有反馈；覆盖原型个别px0/px2。
- `Explanation`只提供可用的真实规则说明；`PendingFeature`仅声明未接入信息，不发POST、不假成功。未完成项与已复用能力见`PROTOTYPE-TODO.md`。

本轮最终检查命令仍为`node fronted/node_modules/typescript/bin/tsc -b fronted/boss --pretty false`。当前42个原型函数映射与40组363个Scenario键完整性已重新脚本核对，缺失键为0；这只证明静态映射齐全。真实隔离DOM、363个状态触发、微信/SMS/企业微信和并发资金链路继续由主线按各自范围验收，未因本轮tsc而宣称通过。


补充细节：`Panel`仅在显式传icon时采用原型`DetailSection`的16px图标标题、8px间距与shadow-sm；用于OrderDetail的挑选/履约/评价/售后/退款/时间线、ReservationDetail安排/游戏/报价及Checkout支付卡，未给原型无图标标题统一加装饰。ReservationCreate的`number=01/02`按源`FormSection`形成编号、图标、标题、次级note与20px下间隔；真实日期仍由原datetime-local承载，不拆字段改变提交逻辑。订单时间线采用源左竖线/最后节点强调，所有事件仍来自实际时间戳，付款未知不展示假履约事件。

最新用户覆盖：已取消全局medium限制，按所在区块、重要性和主次采用合适尺寸。源明确h-11/h-12的操作仍按源执行，固定tab手机均分单行，卡片按真实内容自适应；保留小圆角。


## 2026-11-05按钮主次与风险标识专项

按最新用户偏好使用shadcn variant表达层级；只改视觉标识/选中语义，保留原onClick、请求、禁用条件和确认流程，不借本轮调整业务规则。

| 区域 | 主操作default | 次级/辅助与选中态 | 风险最终确认 |
| --- | --- | --- | --- |
| Login | 初次/失效后获取登录码、有效授权入口 | 有效二维码期间重新获取改outline；返回首页/帮助ghost；海报类型secondary选中，下一张仍主操作 | 不改微信授权方式或会话 |
| Mine/Profile/Security | Profile保存保持唯一主提交；Security优先未完成的手机号验证，若手机号已验证而微信未绑定则强调微信核对 | 已完成验证再核对为outline；客服outline。Mine导航保留整行入口 | 退出登录确认destructive，入口outline带风险文字 |
| Checkout/CompanionPicker/会员展示标签 | 确认下单、确认选择、保存展示标签 | 已选陪玩/标签secondary+aria-pressed，未选outline；返回已选列表outline；不将选择卡当提交CTA | 原资金确认default；不把支付行为一律变红 |
| Orders/OrderDetail | 评价提交、赠礼等当前任务；列表待挑选的查看报名仍是卡片主步骤 | 状态Filter secondary/ghost；只看礼物secondary/outline；联系订单客服outline；候选组选择outline，弹窗确认选择default | 取消订单入口和最终确认destructive；退款/售后保持原确认与handler |
| Reservations | 无报价时获取报价default，有报价后的确认成单default；表单提交default | 已有报价时刷新报价ghost；状态Filter secondary/ghost；查看安排/复制/刷新outline | 取消预约最终确认destructive |
| Gifts | 核对并赠送、成功回执查看礼物单 | 数量加减ghost；查看权益outline、继续挑选ghost；充值在不足时成为可执行主下一步 | 原赠送金额、对象与凭证确认不变 |
| Coupons/Rankings/Points/Messages | 领券/兑换及当前业务提交保持卡内主操作 | 页签/周期/分类secondary选中、ghost未选；消息全部已读ghost；不将页签做primary CTA | 权益交付冲突的“使用最新资料”最终确认destructive，仍先允许复制草稿 |
| Lottery | 单抽default | 十连outline；删除原CSS强制给十连outline套主色渐变的覆盖，避免两项同时主视觉 | 原积分金额确认default；无handler变化 |
| Chat/Pending | 核对原结果优先default；无查询入口才强调原请求重试；凭证受阻时查看现有记录default | 重试outline、记录/客服ghost；Chat发送主操作保留 | 放弃消息核对的最终确认destructive，原可能已送达后果文字保留 |

`useConfirm`增加可选视觉`variant`，只在上述明确放弃/取消/覆盖处传destructive；默认仍为原提交主操作。`boss-tabs`选中样式映射到secondary并保留原型背景/文字/轻阴影，`boss-choice-button`使用轻强调边框，尺寸按源和实际区域配置。最终`tsc -b fronted/boss --pretty false`通过，未新增浏览器或业务状态触发。


## MiSans与区域对齐复核

- `prototype.css`/`surfaces.css`的5处`ui-monospace, monospace`改用`var(--app-font-sans)`：订单编号、预约编号、流水编号、订单详情编号、表单分节编号；字体文件由主线shared接入，本端不重复声明字体或伪造字重。
- 页头/搜索标题/字段左对齐；不全局强制正文或所有卡片left。身份中心卡、优惠数值、签到日历、空态、礼物回执、转盘结果保留原型center；钱包余额hero、订单状态hero与手机CTA金额保留原型左侧锚点。
- 账单变动金额和交付后的余额右对齐；浪花拆为小单位并与金额共基线。Checkout实际报价采用label左/金额右的`boss-quote-total`，单位12px同基线；其他Info报价行继续dd右对齐。
- 服务图文和动态行明确顶部对齐；目录箭头保留源mt0.5，公开目录标题中标签与标题按首行基线对齐；收藏行与陪玩身份行按源items-center保留。
- 状态标签内部居中；没有将按钮内容、表单、长正文或财务数字统一设为同一对齐方式。实际字段、处理函数、金额、状态判定均未变。
