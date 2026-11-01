# 订单与派单体验：参考与实施计划

## 2026-10-30 列表读取失败后的重试范围
- 对照 [Pinterest 筛选表格](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 和 [TanStack Query 的查询重取](https://github.com/TanStack/query/blob/main/packages/query-core/src/query.ts)。375px 隔离验收中，已提交订单号查询失败后输入新的老板账号，点击“重新加载”会悄悄应用新账号并显示 0 条。
- 列表错误的重试复用最近一次请求的条件和页码，未提交草稿继续提示；用户主动查询或清除条件时仍使用新输入。订单列表使用本页中文错误提示，避免同时弹出底层英文 503；登录失效仍沿用统一处理。沿用服务端确认和失败提示，不改变筛选或资金业务。
- 对隔离订单列表注入临时 503，恢复后通过真实接口检查原范围、草稿、主动查询和清除；同时检查桌面及无草稿重试，补充受控读取时序回归。
- 验收：375px 和 1280px 在隔离订单列表注入 503，失败后填写未提交的老板账号，恢复并点击“重新加载”仍返回原订单，草稿和待查询提示保留；主动查询返回 0 条。无草稿重试也恢复原订单；列表自身的失败仅显示中文可重试提示。3 项受控读取时序测试、15 项既有前端契约及 Vue 类型检查通过；503 仅用于模拟读取失败，正常结果来自真实隔离接口。

## 2026-10-30 订单实时刷新保留已应用筛选
- 参考 [Pinterest 筛选表格](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 和 [react-admin 列表筛选状态](https://github.com/marmelab/react-admin/blob/master/docs/FilteringTutorial.md)。订单页允许输入后手动查询，但实时更新和操作完成后的刷新目前直接读取输入中的草稿，可能在未查询时改变结果。
- 通用表格保存最近发起请求的筛选快照，订单页的后台刷新使用该快照和当前页；主动查询、筛选和翻页仍沿用现有行为。最后一页失效时回第一页并继续使用相同快照；已有请求序号继续丢弃迟到结果。未提交的文本及提示保留。
- 用隔离账号、真实订单创建／指派／开始和真实列表接口，核对填写草稿后状态变化触发刷新、原条件及页码保留，再主动查询新条件；检查 375px、桌面及末页边界。无真实支付或外部消息。
- 验收：使用独立 SQLite 副本补齐迁移，经真实下单和指派创建测试订单。375px 订单号已查询、老板账号仍为草稿时，真实开始服务后列表更新为服务中，原订单和草稿提示保留；点击查询后才按新条件返回 0 条。1280px 用 11 笔真实创建和指派的隔离订单检查末页：最后一笔开始后按原老板和已接单条件回到第 1 页、显示 10 条，未提交的订单号仍保留。2 项受控读取时序测试覆盖迟到响应和 404 回退，15 项既有前端契约及 Vue 类型检查通过；时序测试只控制读取完成顺序，不替代真实业务流程。测试未连接真实支付或外部消息。

## 2026-10-30 吸附导航下的键盘焦点
- 参考 [Pinterest 图标导航轨](https://www.pinterest.com/pin/31947478603685270/)、[W3C 对吸附头部遮挡焦点的讨论](https://github.com/w3c/wcag/issues/952) 和 [Apple 滚动界面](https://developer.apple.com/design/human-interface-guidelines/scroll-views)。375px 实测订单卡片反向 Tab 到“选择本页”时，焦点落在吸附导航下方。
- 优先通过页面滚动避让为顶栏和二级导航预留空间，保持现有吸附、横向滚动与页面操作；如原生控件的隐藏输入仍不能避让，再针对实际可见的焦点区域补充处理。不得影响弹窗内部滚动和触屏。
- 在 375px 与桌面用真实键盘正反向操作订单列表和筛选，检查焦点可见、页面滚动、导航切换及弹窗；不执行资金操作。
- 验收：修改前 375px 反向 Tab 到“选择本页”时，勾选控件处在吸附栏下方；添加页面滚动避让后，375px 与 320px 同一路径均将控件滚到导航下方并显示焦点。320px 订单详情可打开、关闭；1280px 筛选焦点和订单／报单导航切换正常。移动吸附栏的顶部位置计入安全区，CSS 检查和 Vue 类型检查通过；安全区未在实际刘海屏设备验证。

## 2026-10-30 订单搜索待应用提示
- 参考 [Pinterest 筛选表格](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 和 [WooCommerce 订单筛选](https://github.com/woocommerce/woocommerce/blob/trunk/plugins/woocommerce/src/Internal/Admin/Orders/ListTable.php)。375px 实测输入未提交的老板账号时仍显示上次列表，且翻页会隐式带上新条件；列表与输入范围容易混淆。
- 记录最近一次请求的四个文本筛选，输入改变后在筛选区提示列表尚未更新，并禁用分页直到查询或其他明确的筛选操作触发新请求；保留旧结果以便对照。保留精确定位、权限及所有服务端筛选规则，不把网络失败当成空结果。
- 使用隔离数据和真实接口检查输入、查询、有结果与无结果、清除、自动筛选、跨页及 375px／桌面；不执行资金或消息操作。
- 验收：375px 输入不存在的账号时提示列表尚未更新，下一页及各页码不可操作；查询后显示 0 条。输入隔离真实账号并回车返回对应 1 单，清除后恢复 26 条。高级昵称输入后勾选售后，立即应用两项条件并清除待查询提示。1280px 核对页码和跳转输入禁用、清除后翻至第 2 页，再按账号查询回到第 1 页。Vue 类型、15 项既有前端契约和 diff 空白检查通过；契约未专门覆盖此提示，交互以浏览器真实接口验收为准。

## 2026-10-30 移动导航与弹窗焦点
- 对照 [Pinterest 移动通知侧栏](https://in.pinterest.com/pin/task-management-notification-sidebar-mobile--432697476718166346/) 和 [GitHub focus-trap 的焦点返回及嵌套处理](https://github.com/focus-trap/focus-trap)。375px 实测从导航打开搜索后关闭，焦点落到页面空白；退出确认按 Esc 同时关闭导航。
- 移动导航打开的搜索在取消后将焦点交还主导航按钮；选择搜索结果时正常进入目标页面。退出确认打开时暂停导航的键盘处理，取消后保留导航和原操作位置；权限与退出行为不变。
- 在隔离后台核对 375px 搜索关闭、结果跳转、退出确认的 Tab、Esc 和取消，兼顾桌面搜索与菜单；不执行实际退出或资金操作。
- 验收：375px 从导航搜索后按 Esc，焦点返回主导航按钮；搜索“钱包”并按 Enter 正常进入钱包账户。退出确认的 Tab／Shift+Tab 在确认框内循环，Esc 或取消只关闭确认框，导航保持展开且焦点回到退出入口。1280px 搜索关闭后回到桌面搜索按钮；Vue 类型检查、15 项既有前端交互契约和 diff 空白检查通过。这些契约不专门覆盖焦点，本轮焦点以实际浏览器操作验收为准。

## 2026-10-30 客服待派按截止时间排序
- 参考 [Pinterest 派单管理](https://in.pinterest.com/pin/dispatch-management--1051801687959528325/) 和 [GitHub 工作队列的截止时间优先](https://github.github.com/gh-aw/specs/work-queue-specification/)。当前可派客服订单按创建时间倒序，较早到期的订单可能被推到后面；部分历史订单没有 `auto_cancel_at`，直付订单校验有数量上限。
- 仅在客服可派筛选中按实际截止时间升序、无截止时间置后；同截止时间按创建时间和 ID 稳定排序，并在有上限的直付校验前使用相同顺序。桌面待派列表将截止时间前置；服务端返回排序确认，页面明确排序或“排序待确认”；无截止时间标记未记录。排序不改变资格或保证派单完成，直付校验不完整仍提示待核对。
- 在隔离环境通过真实老板下单和客服指派构造不同截止时间，另覆盖无截止、分页、状态变化和原始普通列表；检查 375px／1280px、旧服务排序确认缺失，不连接真实资金或外部消息。
- 验收：真实下单四笔，调整隔离测试截止时间和创建时间后跨四页核对早、晚、未记录及相同时间的稳定顺序，指派首单后其余顺序正确；普通列表仍按创建时间倒序。直付测试将校验上限设为 1，验证先检查较早截止的直付单且保留待核对提示；15 项队列回归通过。375px 和 1280px 页面用三笔隔离订单核对排序、截止时间及无截止提示；模拟旧服务移除排序确认字段时显示“排序待确认”，切回全部订单恢复普通表格。Vue 类型和 15 项前端契约通过，未验证生产数据、MySQL 并发或外部消息。

## 2026-10-30 工作台每日订单追查
- 参考 [Pinterest 日趋势图](https://at.pinterest.com/pin/dashboard--368943394456665223/) 和 [Medusa 单日筛选边界问题](https://github.com/medusajs/medusa/issues/6359)。工作台七日柱图按服务端时区统计创建订单，但不能按日追查；未支付与取消订单也在统计范围内。
- 保留趋势图，在图下提供可触屏、键盘操作的每日数量入口；有订单查看权限才可进入。订单页按服务端本地日的起始（含）至次日（不含）筛选，显示日期与时区、支持清除和刷新；旧搜索及老板条件不带入。服务端确认实际应用日期与时区，旧接口不展示未经确认的结果。日统计和列表分别读取，不保证并发变化时数量完全一致。
- 用真实隔离鉴权和下单生成订单，调整测试订单创建时间以覆盖本地日零点前、零点及末尾；核对每天数量、空日、无效日期、权限、刷新和旧接口，检查 375px／1280px，不连接真实支付或外部消息。
- 验收：真实鉴权与派单创建四单，再调整隔离测试的创建时间，验证上海时区零点前、零点、日末和次日零点；指定日期的两单与工作台同日计数一致，空日为 0、无效日期为 400。375px 从 10 月 29 日 26 单入口进入 26 条，刷新保留日期、清除恢复全部；无效日期显示提示。1280px 通过移除真实响应确认字段模拟旧服务，列表隐藏并提示；仅 dashboard:view 的账号显示只读日期数量。Vue 类型、15 项现有交互契约及 diff 空白检查通过。浏览器只检验隔离数据，未验证生产数据或真实支付；前后端需配合部署。

## 2026-10-30 工作台待支付与服务中追查
- 参考 [Pinterest 运营数据面板](https://in.pinterest.com/pin/business-dashboard-with-graphs-and-charts--321585229655087662/) 与 [Medusa 订单支付状态](https://github.com/medusajs/medusa/blob/develop/packages/modules/order/src/schema/index.ts)。工作台“未支付待接单”和“服务中”已有真实状态统计，却不能直接进入对应订单；支付状态与订单状态是独立条件。
- 对有订单查看权限的人员，为这两项指标提供小尺寸“查看订单”入口。未支付入口同时筛选 PENDING 和 UNPAID，服务中沿用 IN_SERVICE 状态入口；无权限继续只读。目标页明确显示未支付待接单条件并可清除，旧搜索和老板筛选不带入；统计和列表分别读取，数量可能随状态变化。
- 使用隔离数据库真实下单、指派、开始及测试域直付订单准备形成不同状态，核对工作台和筛选结果；在 375px／1280px 检查入口、条件与权限，未调用真实支付或外部消息。
- 验收：测试数据库内真实直付准备生成未支付订单，工作台计数与 PENDING＋UNPAID 订单查询均为 1；真实派单、开始等状态回归核对状态统计与列表。375px 浏览器使用隔离展示记录核对待支付入口、两个筛选条件、禁用派单及清除；1280px 服务中 8 单进入对应列表 8 条。仅有 dashboard:view 的隔离账号不显示两个入口。两个真实业务回归、15 项前端契约、Vue 类型及 diff 空白检查通过；展示记录不是直付流程通过的依据。真实支付渠道未验证。

## 2026-10-30 老板筛选服务端确认
- 参考 [Pinterest 筛选表格](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 与 [GitHub 上未支持筛选被静默忽略的问题](https://github.com/intuitem/ciso-assistant-community/issues/4701)。本项目老板 ID 筛选依赖服务端过滤，旧服务可能忽略参数并返回全部订单；上一轮页面没有确认筛选已应用。
- 服务端在成功执行老板账号筛选时回传已应用的账号 ID；页面要求确认值与请求一致，否则不展示返回列表，提示联系管理员更新服务或清除筛选。沿用现有精确订单筛选的兼容策略，权限和业务筛选不变，前后端需配合部署。
- 使用隔离数据库与真实认证和订单接口核对两个老板、有单／无单及无效输入；通过仅移除响应确认字段模拟旧服务，验证 375px／桌面不展示不可信列表、可清除并恢复，未连接真实资金或消息。
- 验收：真实接口测试创建同名老板订单及旧映射订单，分别核对归属、已应用 ID、空结果和无效输入；375px 正常筛选返回对应订单。隔离服务在真实筛选响应后仅移除确认字段，375px 与 1280px 均显示范围未确认且不展示订单；清除筛选后恢复全部列表。Vue 类型、15 项现有交互契约及 diff 空白检查通过；未验证生产和真实外部渠道。

## 2026-10-30 老板订单筛选与刷新
- 参考 [Pinterest 筛选表格](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 与 [Medusa 筛选状态不一致问题](https://github.com/medusajs/medusa/issues/17171)。隔离环境确认从老板资料进入时有 1 条，地址参数被立即删除，刷新后变为全部订单，可能误判当前范围。
- 将已验证的老板账号 ID 保留在地址中并显示在筛选提示，刷新继续按该账号查询；清除、客服待派及其他跳转应移除该条件。仅持久化老板 ID 筛选，其他输入条件刷新后仍需重新设置；无效 ID 不发起指定老板查询，不猜测账号名称。
- 用隔离真实订单验证同名老板、有单与无单、刷新、清除、客服待派和切换老板；375px／桌面检查状态提示，不连接真实资金或外部消息。
- 验收：从 375px 老板资料进入同名老板 #159 的订单仅显示对应 1 条，#160 刷新后仍显示对应 1 条；无单老板 #139 显示 0 条及账号 ID。清除筛选恢复全部，客服待派清除地址中的老板条件；订单精确定位和状态入口也清除旧老板条件。无效 ID 显示错误及清除入口，不展示全部订单冒充筛选结果。1280px 检查提示和结果，Vue 类型检查、15 项现有交互契约通过。筛选以真实隔离接口和数据库验证；仅老板 ID 保留在地址，其他搜索条件刷新后不保留。

## 2026-10-30 订单老板身份
- 检索 [Pinterest 订单管理界面](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 与 [Medusa 订单客户信息](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-customer-section/order-customer-section.tsx)。隔离真实订单中两位同名老板在列表均只显示昵称，客服无法直接区分；现有接口已返回当前账号，手机号快照另存。
- 手机卡片和桌面老板列在昵称下方显示账号，缺失时明确待核对；详情单独标记账号与下单手机号。老板字段就近说明昵称、账号为当前资料，手机号为下单时记录，不把二者当作同一时间快照。不修改接口与业务规则，保留紧凑排列和长账号换行。
- 使用隔离数据库通过真实报价和派单接口生成同名老板订单，在 375px／1280px 核对列表、详情与账号筛选；检查缺失账号显示，不连接真实资金或外部消息。
- 验收：两笔真实隔离订单由同名老板分别派单生成；375px 卡片显示不同账号，按账号筛选只返回对应订单，详情显示当前账号和下单手机号，点击说明可见资料口径。1280px 账号过长时换行；临时将隔离账号置空，列表显示“账号待核对”，随后恢复。Vue 类型检查、15 项现有交互契约及 diff 空白检查通过；未触及真实资金或外部消息。

## 2026-10-30 待接单指派上下文
- 参考 [Pinterest 团队管理](https://in.pinterest.com/pin/643170390571670813/) 与 [FieldOpt 派单台](https://github.com/sys-ae/fieldopt) 的任务与人员并列核对。现有指派框展示订单号和服务，但老板信息缺失；双陪所选项在小屏依赖长标签，不便区分同名人员。
- 保留现有搜索和提交流程，在弹窗中补充老板、订单人数，并把已选陪玩名称、编号、账号和搜索时状态分行展示；资料缺失时明确待核对。状态属于读取快照，最终资格、名额和订单状态仍由服务端校验，不额外增加确认步骤。
- 在隔离数据库使用真实待派单和同名陪玩，检查 375px／桌面选择、移除、搜索及取消；使用真实单人和双人指派接口验证结果，不发送真实消息。
- 验收：375px 双陪订单选择两位同名账号，移除并重新选择后提交；界面显示完整订单、老板和两位账号，数据库记录的成员与选择一致。1280px 单陪选择后取消，订单仍待接单；重新选择并提交后，订单及陪玩账号与选择一致。Vue 类型检查、15 项现有交互契约和 diff 空白检查通过。使用隔离数据库及测试账号，未发送真实消息。

## 2026-10-30 流水关联订单
- 参考 [Pinterest 交易列表](https://at.pinterest.com/pin/transaction-dashboard--665688388668807179/) 与 [Medusa 订单关联](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。当前流水有真实 `order` 外键，但主要靠备注定位订单；无订单的调账等流水不能从备注推测。
- 流水接口读取关联订单号，列表及原流水抽屉展示；有订单查看权限时链接至现有精确订单筛选，无权限仅显示号码，无关联显示“—”。复用表格横向查看控件，不更改账务和订单状态。
- 通过隔离数据库真实支付、退款、冻结流水及无订单流水验证关联、无权限显示和 375px／桌面跳转，不调用真实支付或外部消息。
- 验收：375px 从冻结冲正的原流水跳转并精确定位真实隔离退款订单；仅 transaction:view 账号显示订单号但无跳转。隔离真实 API 的有订单收益与无订单押金流水分别返回正确订单号／空值；桌面表格及横向控件可用。两项数据库测试通过真实派单、取消退款和调账核对详情及列表；其中延时任务调度隔离。Vue 类型检查通过。

## 2026-10-30 冻结与余额流水口径
- 参考 [Pinterest 交易记录](https://in.pinterest.com/pin/130463720443489991/) 和 [Medusa 交易精度问题](https://github.com/medusajs/medusa/issues/13091)，核对本项目 `hold_provider_income`、`release_due_income` 与退款冲正：冻结扣余额并增加冻结金额，解冻转回余额；冻结冲正可能涉及退款追回。现有“金额／变更后”未明确余额口径，容易误读。
- 页面使用“流水金额”和“变动后余额”，原流水抽屉标出变动前后余额；冻结／解冻记录就近说明需要结合冻结资金和关联流水核对，不将符号解释为订单净收入。金额直接使用现有服务端记录，不重算或改变资金业务。
- 用隔离真实结算生成收益、冻结和到期解冻流水，核对数值和状态，在 375px 与桌面检查说明及原流水抽屉；仅隔离数据库，不调用外部支付或真实消息。
- 验收：对隔离已审核订单调整完成时间和冻结时长，调用真实结算及到期解冻，核对 +975／-975／+975 币及余额；另一隔离订单在冻结期通过真实退款接口生成冲正，375px 原流水抽屉显示 -975 币、变动前 975 币、变动后 0 币及说明。1280px 冻结说明可点击，Vue 类型和原流水时序测试通过。测试使用指定时间模拟到期，未覆盖真实时钟等待及真实支付渠道。

## 2026-10-30 冲正来源核对
- 参考 [Pinterest 流水详情](https://in.pinterest.com/pin/transaction-details-mobile-app-ui--1032802127077576223/) 和 [Medusa 订单关联详情](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。本项目冲正流水通过 `reversal_of` 指向原流水，但页面仅显示内部编号，不能直接核对来源。
- 允许在列表中打开只读原流水抽屉，调用已有流水详情接口，展示账户、流水号、类型、状态、金额及变更前后余额；明确这是原记录。异常可重试，关闭、切换会话或失去权限后忽略迟到响应；不推算冲正结果，不新增资金写入。
- 使用隔离数据库的真实订单退款冲正与原收益记录，验证 375px 和桌面读取、返回和失败重试；检查无流水权限与会话切换。
- 验收：隔离钱包 #103 的退款冲正指向原收益 #88；375px 与 1280px 均读到真实流水号、账号、+975 币及前后余额。停止隔离服务后出现失败提示，恢复后重试成功；真实接口对无流水权限账号返回 403。两项前端受控时序测试覆盖关闭、切换会话和权限变化后的迟到响应；Vue 类型检查通过。本轮未执行资金写入。

## 2026-10-30 钱包表格身份与金额
- 参考 [Pinterest 账户管理](https://in.pinterest.com/pin/trading-accounts-dashboard-design--300826450113451106/) 和 [Element Plus 固定列](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/table.md)。隔离页面在 1024px 下固定操作列遮住余额，横向滚动后账户编号也离开视野。
- 桌面将昵称、编号和账号合并到左侧固定身份列，缺编号时明确钱包 ID；余额、冻结、状态靠前，手机号、分级和累计数值仍可横向查看。复用已有的溢出提示及左右查看控件，固定操作列保留；手机卡片与资金业务保持原样。
- 使用隔离真实同名钱包，在 1024px、1280px 检查首屏金额、滚动后身份及操作对应关系；检查 375px 卡片和无权限用户布局，不执行充值或调账。
- 验收：1024px 和 1280px 首列展示身份、余额、冻结、状态；横向查看后仍能区分两位同名老板。打开 XA118640 的调账框，确认对象与账号一致后取消；375px 卡片及仅 wallet:view 的桌面表格正常。Vue 类型检查和 diff 空白检查通过，未进行资金写入。

## 2026-10-30 钱包查找与筛选层级
- 参考 [Pinterest 表格筛选](https://in.pinterest.com/pin/dashboard-tables-and-filters--9499849192792521/) 和 [Django 管理列表搜索](https://github.com/django/django/blob/main/django/contrib/admin/views/main.py)，核对现有接口：编号、昵称、手机号条件为 AND，手机首屏逐行占位且不能按账号查询。
- 增加关键词匹配编号、账号、昵称或手机号（OR）；与原独立条件及钱包类型继续组合（AND）。首屏保留类型、关键词和查询，独立条件放入可展开区域并显示已填写数，提供清除；切换类型保留筛选并显示，避免默默扩大范围。
- 在隔离 SQLite 用真实老板和陪玩、同名／不同账号验证关键词、字段组合、切换类型、收起和清除；检查 375px 与桌面布局和键盘触达，不调用资金写操作。
- 实际检查时发现切换到打手钱包后标题仍为“老板钱包”，将共用页面标题改为“钱包账户”，并保留两种类型名称作为导航搜索关键词。
- 验收：真实 API 与隔离测试库的 3 项测试覆盖同名、编号、账号、手机号、账户类型、组合条件和未绑定账户的旧编号。375px 使用隔离钱包按账号查出两位同名老板、叠加编号缩至一位，收起显示条件数并可清除；保留关键词切换到打手后返回对应账户。1280px 检查排列及标题，Vue 类型检查通过。未验证生产数据和 MySQL 性能。

## 2026-10-30 钱包到流水的上下文
- 检索 [Pinterest 钱包界面](https://in.pinterest.com/pin/wallet-dashboard-ui-design--656470083214668273/) 和 [Medusa 商城账户与流水接口](https://github.com/medusajs/medusa/blob/develop/www/apps/api-reference/specs/admin/openapi.yaml)，并检查本项目 `TransactionViewSet` 的钱包编号精确筛选。现有钱包页只在资金请求结果不明时提供流水入口，日常核对须另行查找。
- 在钱包列表每行及手机卡片提供“流水”，仅对有流水查看权限的账号显示，跳转时带钱包 ID；目标页保留明确可清除的钱包筛选。停用钱包仍可查历史；充值和调账的权限与禁用规则不变。避免从余额推算流水或更改账务逻辑。
- 使用隔离数据库中的真实老板和陪玩钱包及流水，在 375px／桌面验证入口、筛选及清除；用无流水权限账号核验入口隐藏。不得触及真实资金或消息。
- 验收：桌面从老板钱包 #147 进入显示该账户真实支付流水，清除钱包标签后显示全部流水；375px 从陪玩钱包 #103 进入显示该账户收益和冲正记录。独立只读账号仅有 wallet:view，手机卡片无流水及资金操作入口。类型检查通过；未调用资金写入和外部消息。

## 2026-10-30 报单审核结果不明
- 检索 [Pinterest 错误反馈](https://in.pinterest.com/pin/1005710160541425105/) 和 [Medusa 请求冲突与结果不一致案例](https://github.com/medusajs/medusa/issues/13962)，并对照本项目提现审核的结果核对流程。报单审核请求出错时当前只弹短提示，刷新会丢失目标，可能让客服在不知道原请求是否完成时继续审核。
- 审核提交前按账号在当前标签页记录报单编号及动作；成功或明确拒绝时清除，网络／服务异常保留并限制再次审核。提供重新读取状态和查看详情；明确读取只是快照、原请求可能仍处理，人工确认后解除。不同会话及权限变化不能使用旧响应；不把读取或等待当作请求完成。
- 在隔离环境使用真实报单审核，仅在业务成功后替换 HTTP 响应为 503，验证刷新保留、状态读取、解除及数据库状态；另检查请求未达服务与明确拒绝，不连接真实资金或消息。标记不承诺跨标签、跨设备或存储不可用后的恢复。
- 验收：隔离 SQLite 中 #15 真实审核通过后仅替换响应为 503；375px 刷新后仍保留编号，重新读取显示已通过，取消解除仍保持限制、确认后恢复操作。停止隔离服务再提交 #7，读取失败且保持限制；服务恢复后读取待审核，数据库分别为 APPROVED／PENDING。1280px 检查表格和提示。另有 17 项前端时序回归及类型检查通过；明确拒绝与账号切换由前端受控时序测试覆盖，并非真实后端场景。

## 2026-10-30 报单筛选层级
- 检索 [Pinterest 管理列表](https://in.pinterest.com/pin/user-management-dashboard--319051954833309350/) 和 [Ant Design 筛选面板](https://github.com/ant-design/ant-design/blob/master/components/table/demo/custom-filter-panel.tsx)，核对本项目报单接口：`keyword` 对项目、账号、昵称、结单名和编号做或查询；原三字段和状态可组合过滤。375px 四个输入逐行铺开，占用大量首屏。
- 首屏保留统一关键词、审核状态、查询及更多筛选；原项目、账号、昵称／编号字段放入可展开区，收起时保留值并显示已填写数量，支持清除全部。工作台待审核入口清空其他条件并显示状态；不改后端筛选口径。
- 375px 使用隔离真实报单检查关键词、状态、组合筛选、收起与清除，以及工作台跳转；1280px 检查排列。未触及真实资金或外部消息。

## 2026-10-30 报单审核桌面信息层级
- 检索 [Pinterest 审核管理](https://in.pinterest.com/pin/approval-manager--928726754383255261/) 和 [Element Plus 固定列](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/table.md)，对照真实报单状态及收益字段。1280px 当前固定操作列遮住审核状态与收益；桌面“核定收益”列把待审核预计值和驳回报单混在一起，手机卡片已区分。
- 桌面将报单编号、审核状态、陪玩、游戏、报单金额和收益前置，固定编号并收紧右侧操作，启用现有左右查看；收益分别标为预计或核定，驳回及其他状态显示无值。玩法、抽成来源、提交时间继续可横向查看；手机卡片和实际审核规则保持。
- 以隔离真实待审、通过、驳回报单核对 1280px、1024px 与 375px；滚动后验证报单编号与详情一致，并检查审核按钮和字段，不触及真实资金或外部消息。

## 2026-10-30 手机列表结果数量
- 检索 [Pinterest 移动列表](https://uk.pinterest.com/pin/simple-ui-mobile-app-list-view--439312138657424697/) 与 [Element Plus 分页总数](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/pagination.md)，并核对 [Medusa 总数与本页数量混淆的问题](https://github.com/medusajs/medusa/issues/15947)。375px 订单列表只显示页码，接口虽返回总数，首屏却无法判断筛选规模。
- 复用表格的手机卡片视图在列表前展示服务端总数及本页记录数；空结果显示 0，读取中和读取失败隐藏数量，桌面表格保留原分页。不能把本页长度当总数或把失败解释为零条。
- 使用隔离数据库和真实接口检查多页、末页、筛选为空及服务断开／恢复；复核 375px 订单、其他使用同一组件的手机页面和桌面布局，不连接真实消息或资金。

## 2026-10-30 工作台状态到订单筛选
- 检索 [Pinterest 订单管理界面](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 和 [Medusa 订单列表筛选](https://github.com/medusajs/medusa/blob/develop/packages/modules/order/src/services/order-module-service.ts)，结合现有真实状态字段与订单状态筛选。工作台当前状态分布只展示数量，无法直接追查对应订单。
- 有订单查看权限时让状态行可点击并提供键盘焦点；目标订单页只应用所选状态，清除旧筛选并明确显示条件，支持清除。无权限时保留只读统计。统计与列表独立读取，快照期间变化可能造成数量差异，不将待接单等同可派。
- 在隔离数据库通过真实创建、指派、开始、完成和取消形成多种状态，核对工作台和状态筛选；实际检查 375px 与 1280px 跳转及清除，不触及真实资金或外部消息。

## 2026-10-30 快捷派单验证修复
- 上轮完整前端契约组合暴露 3 项测试未跟随快捷派单新增的服务加载状态和提交前核对；其中一个测试假定点击后立即提交，并以替身返回成功。参考 [Medusa 真实数据库集成测试](https://github.com/medusajs/medusa) 的分层方式，先核对现有页面和真实报价、派单接口。
- 将前端测试限定为套餐切换、错误提示和无效输入不得发出请求；不以替身的成功响应证明派单。真实后端测试覆盖单陪套餐冒充双陪、双陪重复选择，以及有效双陪报价、扣款、两人归属和单次流水。保留现有确认框及业务实现。
- 运行完整前端契约和真实派单业务回归；使用隔离 SQLite 和虚构账号，不连接外部消息或真实资金。记录测试分层及尚未覆盖的并发和真实渠道。

## 2026-10-30 工作台同名陪玩
- 检索 [Pinterest 订单管理界面](https://in.pinterest.com/pin/order-management-dashboard-by-galaxy-ux-studio--872361390312299437/) 与 [Medusa 账号身份问题](https://github.com/medusajs/medusa/issues/9999)，结合本系统订单参与记录。375px 隔离真实双陪订单在工作台显示两个相同昵称，而订单页已能使用陪玩编号或账号区分。
- 工作台对有订单查看权限的账号复用订单列表身份字段，复用同名区分逻辑，仅显示未转出的当前人员；缺资料或旧服务缺字段时写身份待核对，不猜测。无订单查看权限时不新增身份字段；说明编号和账号是当前资料。原有订单、金额和跳转口径不变。
- 用隔离真实双陪订单核对工作台、订单页和详情，检查转出记录、旧字段和权限；核对 375px 换行及 1280px 表格，不连接真实资金或外部消息。

## 2026-10-30 订单横向查看时的身份
- 检索 [Pinterest 订单管理界面](https://in.pinterest.com/pin/order-management-dashboard-by-galaxy-ux-studio--872361390312299437/)；参考 [Element Plus 左右固定列实现](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/table.md)。当前横向滚动时订单号离开视野，但详情、派单和退款等操作仍固定在右侧，容易失去操作对象的上下文。
- 将桌面订单号与勾选列一起固定在左侧，保留右侧操作列，其他业务字段仍可滚动；检查固定列是否遮挡内容或造成窄屏不可用。若中间区域过窄，应改用现有卡片方案，不增加业务规则。
- 固定列占用部分可视宽度，左右按钮按扣除固定列后的区域计算步长并保留重叠，避免一次跳过中间字段。
- 在隔离订单上分别检查 1280px、1024px、窄桌面及 375px；滚动后核对同一行订单号、字段、选中状态和详情，不执行资金或外部消息操作。

## 2026-10-30 订单表格横向查看
- 延续此前检索的 [Pinterest 订单列表](https://in.pinterest.com/pin/order-list-pos-dashboard--1084171310291528374/)；参考 [Element Plus 表格](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/table.md) 的固定列和可聚焦滚动区域。1024px 实测订单表格内容宽约 1698px、容器宽约 880px，首屏缺少横向查看提示，滚动区域不可 Tab 聚焦。
- 订单桌面表格有数据且确实横向溢出时，在表格上方提供左右查看按钮，滚动到边界时禁用对应按钮；保留可聚焦滚动区域及固定操作列。容器变化、数据加载和手机卡片切换时重新检查，其他业务表格不启用。
- 用隔离真实订单检查 1024px 左右查看与键盘滚动、固定操作，检查宽视口及 375px 行为；不提交订单或连接外部消息。

## 2026-10-30 紧凑订单操作列
- 参考 [Pinterest 数据表格](https://in.pinterest.com/pin/data-table-in-2025--482940760061680503/) 与 [Medusa 可调整数据表](https://github.com/medusajs/medusa/releases)：在保持操作可辨认的同时为业务字段留出空间。当前桌面固定操作列 320px，常见三至四项短操作后有较大空白，1280px 服务列因此被遮住。
- 将固定操作列收至 220px，保留所有原有条件、操作及不可用说明；说明占单独行并允许换行。检查待接单、已接单、服务中、不可派及长错误说明的实际布局，手机卡片不受影响。
- 在隔离数据库的真实订单上检查 1280px 和较窄桌面视口的操作、服务和状态；验证详情及待派筛选，不操作真实资金或外部消息。

## 2026-10-30 订单表格关键信息顺序
- 参考 [Pinterest 订单列表](https://in.pinterest.com/pin/order-list-pos-dashboard--1084171310291528374/) 和 [Medusa 订单模型](https://github.com/medusajs/medusa/blob/develop/packages/modules/order/src/schema/index.ts)：订单状态与支付状态是独立信息，应在操作前同时可见。现有 1280px 表格先显示服务和客服，固定操作列遮住后面的支付和状态。
- 调整桌面列顺序，把订单状态、支付状态、老板和金额移到订单号附近；其余人员、服务、路径与时间仍可横向查看。保留移动端卡片、筛选、固定操作和原始状态口径，不增加新业务逻辑。
- 用隔离真实公开待接单、客服待派和已指派订单，检查 1280px 首屏的状态与操作，375px 卡片保持可读；不触及真实客户或外部消息。

## 2026-10-30 接单路径与当前状态
- 参考 [Pinterest 订单状态列表](https://in.pinterest.com/pin/232990980707709735/) 和 [FieldOpt 派单台](https://github.com/sys-ae/fieldopt)：把预设接单路径与当前人员／状态分开。实际快捷派单即使直接指定两名陪玩，订单路径仍可能为 PUBLIC；原“派单方式：公开接单”易误认为本单一定经公开抢单。
- 将订单列表、筛选及详情的字段名改为“接单路径”，保留服务端 PUBLIC／STAFF 及原筛选口径；符合可派客服待派条件时单独标记当前状态。就近说明该字段不是当前是否公开或实际指派方式，人员和状态流水以详情为准。工作台待接单分组同步使用路径名称，不改变统计。
- 通过隔离数据库真实创建公开待接单、客服待派及客服直接指派订单，核对列表／详情字段与状态，375px 检查说明可触达；不改接单规则或外部通知。

## 2026-10-30 双陪百分比与零头
- 参考 [Pinterest 分账界面](https://in.pinterest.com/pin/payment-splitting-app-ui-kit--864831934677899292/) 与 [Medusa 精度问题](https://github.com/medusajs/medusa/issues/13971)，把每位陪玩身份、抽成和最终金额就近展示，并核对最低账务单位的取整口径。
- 当前服务端先将订单金额均分，余数给靠前陪玩；各自抽成按内部整数单位向下取整，再用份额减抽成得到实得。界面直接写“份额 × 实得比例”会在零头下产生不一致；改为展示份额减实际取整后的抽成，同时说明取整规则，保留既有业务计算。
- 用隔离账号真实派单、不同百分比和带零头服务，核对 375px 两人身份、预计实得、真实订单分账及老板扣款；不模拟核心业务或连接外部消息。
- 当前 Element Plus 数字输入组件仅在挂载时写入 `aria-disabled`，动态启用后仍向辅助技术报告禁用；在有效禁用状态变化时重建该输入，验证键盘可编辑及实际禁用时的状态，不改业务规则。参考 [组件实现](https://github.com/element-plus/element-plus/blob/dev/packages/components/input-number/src/input-number.vue)。

## 2026-10-30 双陪快捷派单核对
- 参考 [Pinterest 团队管理](https://in.pinterest.com/pin/643170390571670813/) 和 [Medusa 订单摘要](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。双陪确认应把人员与其抽成／预计实得就近对应，不能只按位置列出抽成，让同名人员难以核对。
- 在既有确认框按人分行，显示账号、陪玩编号、抽成或固定额分摊和预计实得；固定额试算缺少分摊时明确待确认，不推算。金额仍是读取时预计值，最终以服务端为准。未选人或不可提交的情况保留现有校验。
- 375px 在隔离数据库中用两名同名不同编号陪玩、真实双陪服务和老板钱包完成选择、返回及提交，核对订单参与人、金额与资金流水；仅隔离外部消息，不模拟核心业务。

## 2026-10-30 快捷派单提交核对
- 参考 [Pinterest 订单工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 与 [Medusa 订单金额确认](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。现有快捷派单立即扣所选老板钱包，小屏表单的老板、服务、陪玩和预计金额分散。
- 提交前展示只读摘要，列明老板账号、服务与局数、陪玩或待接单、抽成及服务端试算扣款；取消返回保留表单。核对期间锁定表单，账号、输入或试算变化后不提交旧摘要；服务端仍以实际状态及价格复核，提示余额是查询快照。
- 375px 使用隔离数据库、虚构老板和真实派单接口，验证取消不建单扣款、核对后真实扣款建单；检查双陪与可接单对象以当前字段为准，不连接真实消息。

## 2026-10-30 快捷派单结果核对
- 参考 [Pinterest 订单工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 和 [FieldOpt 派单台](https://github.com/sys-ae/fieldopt)：提交结果不明时保留上下文与后续核对入口。现有快捷派单使用请求编号保护重复扣款，但关闭弹窗会遗失不明结果，重新打开会生成新编号。
- 提交前按当前后台账号在标签页记录老板、服务、时间和请求编号；成功或明确业务拒绝清除，其他情况保留。提醒展示待核对对象；有订单查看权限时可按老板筛选订单，无权限时提示联系有权限人员。核对并确认后才解除本标签页的快捷派单限制，明确读取是快照且请求可能仍处理。沿用现有后端与真实业务规则，不把缺少结果当作失败。
- 使用隔离数据库和真实下单、扣款业务验证，模拟只替换成功后的响应并检查刷新保留、老板筛选与解除流程；不触及真实资金或外部消息。

## 2026-10-30 陪玩试音入口
- 已检索 [Pinterest 分享入口](https://www.pinterest.com/pin/473370610812030434/) 和 [Supabase 无密码入口](https://github.com/supabase/supabase/blob/master/apps/docs/content/guides/auth/auth-email-passwordless.mdx)。区分可供已登录陪玩报名的普通链接与绑定后可能换发登录态的专属链接；页面说明绑定不会限制其他已登录陪玩报名。
- 绑定、切换、解除改用独立权限及真实账号编号核对，变更轮换陪玩 token；旧报名入口失效，但现有报名记录与老板 token 保留，已签发会话不自动撤销。已绑定的 token／URL 仅向有权限者返回；未绑定的普通招募链接保留原查看权限。旧字段直写拒绝，版本冲突需重读。
- 若系统要求陪玩验证器，专属入口不能直接换发登录态，应登录后使用。用隔离 JWT、真实报名和换发接口验证普通报名、绑定轮换、其他账号报名、已有记录、OTP 配置、权限和响应不明的核对；不向真实账号分享凭证。

## 2026-10-30 试音链接与老板绑定
- 参考 [Pinterest 邀请有效期](https://help.pinterest.com/en-gb/article/invite-people-to-follow-you) 与 [Supabase 邀请链接](https://github.com/supabase/supabase/blob/master/apps/docs/content/guides/auth/users.mdx)：显示绑定对象、入口有效期和凭证性质，操作前展示明确对象及影响；375px 使用卡片。
- 现有老板 token 可换取绑定账号的 JWT，普通新建链接未绑定，老板小程序只支持绑定入口；默认 H5 域名可能为占位。独立权限操作绑定／解除，按账号编号搜索并核对，绑定变更时轮换老板 token，保留陪玩报名 token；已签发登录态不会因此撤销。以读取版本条件更新，冲突要求重读，提交不明时禁止盲目重试。
- 旧客户端直接修改老板绑定的路径须拒绝，避免绕过权限及轮换；不更改既有陪玩入口。使用隔离账号验证绑定、换发、轮换、失效、权限、并发版本与旧报名链接，绝不发送链接给真实用户。正式 H5 域名、路由和小程序打开方式仍须按部署配置验收。

## 2026-10-30 试音报名审核
- 已检索 [Pinterest 招募看板](https://in.pinterest.com/pin/job-application-process-dashboard-pt6--326511041738936974/) 和 [Nextcloud Recruiting](https://github.com/nextcloud/recruiting)：小屏展示申请摘要，详情与审核操作贴近申请人，拒绝原因在目标上下文中填写。
- 依实际账号优先展示昵称、编号和账号，兼容旧记录；搜索同时覆盖业务账号及旧用户。审核只改变报名状态并尽力创建站内通知，不代表入职或通知送达。通过／驳回确认显示活动、身份和原因；提交期间锁定，其他客服先审核时按服务端结果刷新。
- 在本标签页保留结果未确认标记，提供读取最新报名状态和人工确认解除。使用隔离数据库、真实令牌和虚构陪玩提交报名与审核；不向真实对象发送通知。

## 2026-10-30 站内消息目标与小屏
- 参考 [Pinterest 消息列表](https://in.pinterest.com/pin/task-management-notification-sidebar-mobile--432697476718166346/) 和 [Adobe 消息预览](https://github.com/AdobeDocs/campaign-classic.en/blob/main/help/delivery/using/design-and-personalize.md)，小屏用可辨认接收人的卡片、可滚动弹窗与详情抽屉；确认前展示实际范围、类型、标题和所选账号。
- 现有全员及角色范围按启用的旧用户筛选，全员包括后台人员；站内记录数量不等于客户端送达。保持该范围并明确告知，不猜测新增业务规则。指定账号需存在真实旧用户映射，禁止按相同数字补链；被停用或无法登录的账号在提交时拒绝，发送前核对身份。
- 确认期间冻结本次内容与接收人；搜索防过期请求、失败可重试；提交期间锁定，响应不明时提示可能部分发送、先核对记录。使用隔离数据库及虚构账号验证，不向真实用户发送消息。

## 2026-10-30 促销活动页
- 已检索 [Pinterest 电商后台](https://in.pinterest.com/pin/ecommerce-back-office-dashboard--462604192972315191/) 与 [Saleor 活动管理](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)：小屏用摘要卡片，明确适用范围、时间与状态；编辑分组并将规则放在对应控件附近。
- 根据服务端时间、启用及起止时间显示“停用／未开始／时间内／已结束”；时间内只表示窗口内，不保证某商品命中。计价按优先级、开始时间、ID 选一条，体验单不参加活动，折扣活动与券互斥；抽成与业务资格以试算为准。
- 小屏改卡片及自适应弹窗；分类和商品选项按需加载，失败可重试，不使选项失败阻挡全场活动。删除确认说明历史订单关联会被清空、已保存金额不改算。保留实际业务口径及字段，不新增促销规则。

## 2026-10-30 转单提交前核对

- 参考 [Pinterest 团队管理](https://in.pinterest.com/pin/643170390571670813/) 与 [后台提示](https://in.pinterest.com/pin/1005710160541425105/)，以及 [Medusa 订单金额确认](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。现有转单表单可直接提交，会改变人员、分配收益并可能扣罚款，双陪时需要在最终提交前集中核对。
- 按所选行生成只读确认摘要：订单号、每位转出与转入陪玩的身份、旧／新份额估算、罚款及原因；固定额超过份额按服务端现有上限显示。明确以读取时份额估算、提交时服务端核验且结果为准，不承诺锁定价格。摘要使用安全文本节点并支持小屏滚动，取消保留表单；确认期间锁定编辑，订单推送、重连、权限或账号变化后旧摘要不得提交。
- 使用隔离数据库和真实双陪转单接口，在 375px 验证取消、准确摘要和实际提交后的收益、罚款流水；另一会话真实变更订单，验证旧摘要禁止提交。外部消息关闭，不触及真实资金。

## 2026-10-30 订单与转单的陪玩身份

- 参考 [Pinterest 团队管理](https://in.pinterest.com/pin/643170390571670813/) 和 [Saleor 订单关联身份](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)；本项目报单与指派搜索已用昵称、陪玩编号和账号区分同名人员，订单详情及转出选择目前只有昵称。
- 仅在具备订单查看权限的订单列表和详情补充当前关联账号的编号、账号名与身份来源；没有业务账号时才读取关联旧用户，不把旧身份冒充当前业务账号。增加相关预加载，工作台无需返回这些额外身份。列表出现同名时附编号或账号以区分；详情区分当前与已转出人员；转单选择显示转出人员编号及账号，转入结果复用指派搜索的编号、状态与账号。注明身份是当前资料而非订单创建时快照；缺失不推断。
- 用隔离真实老板下单、双陪指派、开始、转单及订单接口验证同名人员、当前／已转出与旧账号回退；375px 检查详情、候选与转单操作，1280px 保留列表。外部消息隔离，不改分账规则。

## 2026-10-30 订单实销时间口径

- 参考 [Pinterest 订单状态展示](https://in.pinterest.com/pin/232990980707709735/) 和 [Saleor 订单详情日期与状态](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)；依据项目 `engagement.confirmation` 的真实字段和处理条件。`consumption_ready_at` 仅由有效报单时间计算，实际确认还受订单、支付、报单审核、售后、账号周期、赠品和支付渠道等条件影响；全额退款可能冲回历史实销。
- 详情把 `consumption_confirmed_at` 标为“记录的实销确认时间”，缺失显示“暂无确认记录”，不推断等待哪一项。未确认且有 `consumption_ready_at` 时单列“报单计时节点”，提示时间到达不代表已确认；退款明细明确记录实销冲回时，历史确认时间旁直接标注“已冲回”，未提供记录时不推断。说明支持触屏与键盘，不修改业务或后台字段。
- 以隔离真实下单、指派、完成、报单和退款数据核对待接单／取消／有计时但未确认／已确认与冲回；在 375px 及桌面检查详情和提示。不能通过测试注入已确认字段冒充业务完成。

## 2026-10-30 指派结果核对入口

- 参考 [Pinterest 订单状态](https://in.pinterest.com/pin/232990980707709735/) 及 [状态可见性](https://guide.pinterest.com/design/examples-of-visibility-of-system-status/)，结合 [Medusa 订单事件](https://github.com/medusajs/medusa/blob/develop/packages/core/utils/src/core-flows/events.ts) 通过订单 ID 关联状态；沿用本项目已有转单待核对交互。
- 提交指派前在当前账号标签页记下订单 ID 和订单号；确定成功或明确业务拒绝清除，不确定结果或重载时保留。待核对提示可按 ID 打开订单详情与状态流水，不依赖原筛选结果；读取成功后由客服确认解除限制，说明读取只是当前状态、请求仍可能处理。仅影响本标签页继续指派，不修改订单状态、后端协议或声称获得最终回执。
- 在隔离库真实下单及指派，提交成功后仅替换响应；在客服待派筛选中核对订单从列表消失、页面重载后定位详情和人工解除；另测试网络不可读时限制仍保留，明确拒绝不残留标记。375px 检查交互，不连接真实消息。

## 2026-10-30 指派结果反馈

- 参考 [Pinterest 后台错误提示](https://in.pinterest.com/pin/1005710160541425105/) 和 [Medusa 订单上下文操作](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx) 的就近反馈；[Medusa 实际异常案例](https://github.com/medusajs/medusa/issues/13962)说明错误响应与订单已写入可同时发生。UI/UX Pro Max 建议错误明确下一步。
- 现有指派在数据库事务后仍有通知和响应序列化，500 或网络错误不能证明未指派。仅业务码 400–499 且 HTTP 非 5xx 的明确拒绝显示未完成；其他错误显示结果未确认及请求可能仍在处理，禁止弹窗内重复提交，提示刷新只是当前状态。沿用现有订单刷新和状态校验，不声称支持幂等或最终回执；指派请求使用静默模式避免同时出现误导性全局错误 toast。
- 隔离数据库执行真实下单和指派；仅在真实成功后将响应替换成带 code 500 的 503，检查 375px 弹窗、禁止重复提交与刷新结果；另验证真实业务拒绝保留具体原因，正常指派成功，桌面与类型检查正常。外部消息隔离。

## 2026-10-30 工作台当前陪玩口径

- 参考 [Pinterest 订单工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 的订单摘要和 [Medusa 订单履约摘要](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx) 的当前状态展示。真实转单订单显示：工作台把已转出和当前陪玩并列，而订单列表只显示未转出人员。
- 工作台桌面与手机及订单列表、详情共用当前陪玩名称处理：存在参与记录时只显示 `released_at` 为空的记录；全部已转出时显示空值，不回退到可能是历史人员的主记录。无参与记录的旧订单继续使用原主陪玩字段。历史转出与收益记录仍在详情保留。
- 通过隔离数据库执行真实创建、指派、开始和转单，比较工作台及订单列表的人员；验证真实双陪保留两人，375px／桌面展示及历史详情不受影响，不连接真实消息。

## 2026-10-30 详情指派的实时竞争验收

- 参考 [Pinterest 订单工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)、[Medusa 订单事件](https://docs.medusajs.com/resources/events-reference)和 [Channels 实时消息](https://channels.readthedocs.io/en/latest/)：状态变化以服务端订单为准，实时事件触发重读，不把消息本身当作最新快照。
- 单进程隔离 Daphne 同时处理真实 HTTP 与 WebSocket、使用独立 SQLite 和内存 Channels；两个页面查看同一订单。分别验证另一页面先完成指派时，详情及时变为已接单并收起指派入口；指派弹窗已打开时旧选择不可提交且显示原因。若发现实际问题，先说明处理方案并再修改。

## 2026-10-30 订单详情到指派

- 参考 [Pinterest 订单工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)、[Medusa 订单上下文操作](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)和 [Apple 工具栏建议](https://developer.apple.com/design/human-interface-guidelines/toolbars)：把当前订单的主要下一步放在详情中，滚动时仍可找到。
- 已付款且服务端允许指派的待接单订单，在详情抽屉底部提供“指派陪玩”，复用现有单双陪指派弹窗；取消指派后回到原详情，成功后刷新详情。无权限不展示，详情正在更新、读取失败或订单不可指派时禁用，并给出就近说明；提交仍由真实后端再次核验。不可将旧详情当作最新状态。
- 在隔离数据库以真实订单及 API 验证 375px 详情→指派→取消／成功、实时或刷新后状态变化，以及加载失败后的禁用与重试；不连接真实消息。

## 2026-10-30 订单详情中的游戏资料

- 参考 [Pinterest 订单详情](https://in.pinterest.com/pin/437060338859664032/)的信息分组及 [Medusa 订单摘要](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)在订单上下文中展示履约信息。项目订单实际保存区服、游戏昵称和游戏 ID，后台详情目前未返回这三项，导致客服无法核对老板下单资料。
- 仅在具备订单查看权限的订单详情响应中返回三个订单保存值，列表和工作台概览不增加这些字段。详情按“游戏资料”分组，单项缺失标记未填写，三项均空时合并提示；不从老板当前资料反推历史订单，不修改发单模板或业务规则。
- 使用隔离数据库和真实老板下单、后台详情接口验证有值／部分空值和列表范围；375px 实际抽屉检查文字换行及无横向溢出，保留其他订单、收益及状态信息。
- 实际双陪客服待派订单的 `escort_mode` 在完整指派前仍可能为 SINGLE，而 `required_team_size` 为 2；详情删除重复的“陪玩模式”，仅显示服务端确定的成交人数，避免同时写出单陪和双陪。

## 2026-10-30 待派提醒与当前操作的协调

- 参考 [Pinterest 通知侧栏与移动端](https://in.pinterest.com/pin/task-management-notification-sidebar-mobile--432697476718166346/)、[Apple 通知打断级别](https://developer.apple.com/design/human-interface-guidelines/managing-notifications)和 [GitHub 通知系统示例](https://github.com/phazurlabs/sumi/blob/main/skills/performance-states-patterns/references/notification-system-design.md)：待处理数量应有持续入口，通知不遮盖用户正在填写的弹窗。
- 保持后台真实队列读取和标题栏状态更新。弹窗、抽屉或确认框打开时暂缓悬浮提醒；已经展示的提醒在打开弹窗时收起，关闭最后一个弹层后重新查询并仅在仍有待派订单时提醒。权限或会话切换清除旧提醒，读取失败继续沿用重试。权限资料刷新但权限值未改变时不重置去重状态。
- 隔离数据库、真实后台接口在 375px 验证已有提醒→打开弹窗→关闭弹窗、待派订单已处理后不再提醒；验证权限刷新不会重复触发。浏览器不连接真实消息渠道。辅助测试只使用替身控制异步时序，不用替身证明订单业务结果。

## 2026-10-30 待接单指派候选人识别

- 参考 [Pinterest 任务详情](https://in.pinterest.com/pin/437060338859664032/)的信息上下文和 [FieldOpt 派单台](https://github.com/sys-ae/fieldopt)在选择人员时核对资格与状态的做法。项目已有选项接口返回账号、陪玩编号、当前状态；指派搜索目前只显示昵称和账号，同名情况下缺少编号确认。
- 单陪和双陪的候选项补充实际陪玩编号及读取时状态；未记录编号明确标出，未知状态不猜测。状态仅供识别与参考，搜索结果不代表可接单、名额已保留；继续由真实指派接口校验订单、资格、档期和容量。手机保持小尺寸控件及原有搜索、选择、错误恢复。
- 隔离数据中用两个同名账号、不同编号与状态验证搜索展示，再通过真实接口验证不可接单人员被拒绝、可接单人员被指派；不发送真实外部消息。

## 先研究，再修改

- [Pinterest：订单管理工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)：参考分区、轻量色彩和信息层级；不引入示例中的虚构统计数据。
- [Pinterest：配送管理工作台](https://in.pinterest.com/pin/746330969535929611/)：参考状态与待处理事项集中呈现，保留订单真实状态作为唯一依据。
- UI UX Pro Max 的表单指导：标签明确、相关说明靠近字段、提交过程展示状态。基于现有声浪海洋色和奶白主题实现。

## 现状与方案

现有快捷派单使用长弹窗：老板与套餐、抽成和打手同时出现，而余额与服务端试算位于滚动内容底部。客服在小屏或短窗口中需要上下寻找金额与提交按钮。现有服务端试算、抽成规则、套餐人数、权限、并发校验和防重复提交必须保持。

1. 按“下单信息 → 陪玩与抽成 → 核对金额”组织现有字段，用清晰标题与简短说明帮助判断下一步；不虚构进度或成功状态。
2. 将余额、服务端试算金额和等待／不足提示放在提交操作附近，让关键金额在滚动时可见；保留原有提交校验与错误反馈。
3. 对不同套餐人数、待接单与抽成选择保留现有真实规则，采用适合窄屏的布局、可见焦点与低动效兼容。
4. 真实业务验收优先使用隔离数据库和真实 API；视觉预览数据只用于检查排版，不能作为业务通过的证据。
5. 待派与返回全部订单的筛选副作用、折后试算的口径使用就近提示；双陪重复选择即时显示警告。订单详情与退款继续使用既有抽屉和资金复核弹窗。

## 工作台数据方案

- 当前字段：订单创建时间、状态、支付状态、订单金额、退款记录与客户／陪玩账号。累计金额取已支付及部分退款订单的当前净额，不等于平台利润或当日现金收入。
- 当前待处理：区分待接单中的已支付与未支付；前者只是状态快照，派单资格仍由订单详情与服务端校验。服务中采用当前订单状态。
- 近七日按服务端时区和订单创建日期计数，展示每天新增订单，不把订单金额按创建日伪装成当天支付流水。
- 最近订单按创建时间排序，显示当前状态；手动刷新并标出服务端开始读取的时间。各指标分别查询，不宣称跨查询为同一事务快照。失败时显示明确错误，不把缺失数据当成零。
- 订单中心跳转遵守现有 `order:view` 权限；不为了展示卡片绕过权限或推测待派资格。

## GitHub 业务设计参考

- [Medusa 订单摘要源码](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)：根据订单和付款状态决定哪些动作可用，并将金额明细与相应操作放在订单上下文内。项目沿用自身服务端校验，不直接照搬其他商城的付款和履约状态。
- [Saleor Dashboard 变更记录](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)：明确统计样本范围、订单日期与查看权限。这里将近七日定义为订单创建日期，将累计净额标注为当前扣除退款后的订单净额，不把它混同利润或现金流水。
- [FieldOpt 派单工作台](https://github.com/sys-ae/fieldopt)：把指派、人员和作业状态放在同一工作流；本项目仍以服务端资格校验为准，不沿用该项目允许覆盖警告的规则。
- [Pinterest 状态反馈示例](https://guide.pinterest.com/design/examples-of-visibility-of-system-status/)：区分排队、处理中、结果未知和完成，告知下一步而不把请求已提交当作送达。

## 待派指派与发单补充计划

真实隔离订单的界面检查发现：单人指派缺少资格说明且搜索复用其他弹窗的数据；消息预览初始加载失败时没有弹窗内重试，渠道状态缺少明确分区。计划统一单／双陪搜索的请求时序与会话校验、在提交中锁定指派弹窗；发单预览展示明确的加载／失败／渠道状态，谨慎处理并发刷新，保留服务端防重复和消息内容规则。外部渠道只在隔离配置下检查，不向真实群发送信息。

## 真实接口验收

隔离 SQLite 经完整迁移，测试账号通过真实登录接口进入实际后台，真实服务端试算后创建待接单订单；后台列表、今日新增、已支付待接单及累计净额由 1 单／1000 币更新为 2 单／2000 币。外部 KOOK／企业微信通知关闭，未验证实际消息发送。隔离 Django `runserver` 不提供 WebSocket，触发重连时真实权限资料刷新；修复前会关闭派单弹窗，修复后在多次刷新期间仍保留所选老板、服务和报价。SQLite 不等同生产 MySQL，未覆盖真实并发锁、消息网关或生产性能。

陪玩 H5 使用同一隔离数据库和真实登录、订单接口验收：选择游戏甲资格后抢单池显示筛选空状态，切到“已接单”仍可见通过真实派单创建的游戏乙订单；返回抢单池后可一键恢复全部资格。375px 下检查了资格标签及上述切换；标签使用横向滚动，未替代后台资格校验。

关闭隔离后端后实际点击工作台刷新，页面保留上次成功结果并显示刷新失败和重试入口；这些数据不能视为实时数据。后台 Vue 类型检查与生产构建、陪玩 H5 TypeScript 检查以及 H5／陪玩小程序构建通过；H5 构建存在包体积提示。隔离数据库中的六项真实派单／看板测试及一项部分退款统计测试通过，新增老板下单→客服待派→双陪指派的真实接口链路。延时任务使用隔离内存队列、未启动工作进程；微信真机、真实消息通道和 MySQL 并发尚未在此轮验证。

### 转单复核与实施计划（2026-10-29）
参照 Pinterest 的状态可见性及 FieldOpt 的调度操作反馈，先对照实际 `transfer` 接口：旧陪玩保留填写的比例或固定额，固定额上限为该行可分配份额；余额不足支付罚款时事务回滚；具体入账时间取决于订单结算模式。界面应显示这些边界、隔离人员搜索与其他弹窗、提交时锁定关闭和编辑，并在结果不确定时要求刷新后核对，避免重复操作。转入资格及最终金额由服务端事务核验。

### 快捷派单异步状态计划（2026-10-29）
继续参考 Pinterest 状态反馈与 Medusa 订单上下文操作：围绕老板搜索、服务加载、陪玩搜索、抽成建议和提交结果分别给出加载、失败和恢复路径。各请求绑定当前会话、弹窗与输入，旧结果不得覆盖新选择；抽成建议失败时不得把默认值冒充建议。提交结果不明时先关闭并刷新核对订单，价格变化则展示最新报价后重新确认；仍以现有真实接口和服务端校验为准。

### 简化文案与信息层级（2026-10-29）
遵循用户新增要求，对照 [Apple 设计原则](https://developer.apple.com/design/human-interface-guidelines/design-principles)、[Apple 情境帮助](https://developer.apple.com/design/human-interface-guidelines/offering-help)和[Apple 写作指南](https://developer.apple.com/design/human-interface-guidelines/writing)：保留老板、服务、陪玩、金额与关键状态，去掉重复的步骤说明，将次要抽成与金额解释贴近对应字段，以可点击和可聚焦的提示呈现；失败和结果未确认的处理直接显示。

陪玩端订单页观察（视觉演示数据只用于排版）：页面已在折叠标题中显示连接状态及收益入口，但旁边重复提示“展开查看”和“预计收益已显示”。按用户新增方向移除重复提示与标题下的泛化文案，保留可点击展开后的结算和网络细节，以及现有失败时的处理入口；不改收益、抢单或结算业务规则。

### 峰谷限流与时限设置计划（2026-10-29）
参考 [Mozilla Otari 限流配置](https://github.com/mozilla-ai/otari/blob/main/docs/configuration.md)对规则与计数窗口的说明，以及 [Apple 设计原则](https://developer.apple.com/design/human-interface-guidelines/design-principles)：分别呈现设置与监控、保留当前窗口计数的提示。实际接口支持版本号和跨午夜时段；自助下单开关独立于限流开关。界面应在刷新覆盖未保存值前确认，校验整数及重叠时段，区分服务端拒绝、版本冲突和结果未知，并锁定保存中的弹窗。监控无数据不能伪装成真实零值；具体权限及结果以服务端为准。

### 服务开始与完成计划（2026-10-29）
参考 [订单状态流转协议](https://github.com/Abrasel-Nacional/opendelivery-v2/blob/main/docs/protocol/orders.md)中状态变化以订单事件及查询结果核对的方式，以及已记录的 Pinterest 状态反馈：操作前显示具体订单，完成时按该订单 `settlement_mode` 提示旧单完成结算或报单审核；状态变化时作废待确认操作。请求中防重复，失败区分明确拒绝与结果未知，并刷新订单；最终状态与资金流水仍以服务端为准。

### 实时状态验收计划（2026-10-29）
对照 [Django Channels 文档](https://channels.readthedocs.io/en/latest/topics/channel_layers.html)：以单个 Daphne 进程同时处理 HTTP 和 WebSocket，使用隔离 SQLite 与进程内 channel layer；两份真实后台会话对同一测试订单操作。确认第一份会话显示确认框时第二份会话真实推进订单，第一份会话接收事件、撤销过期确认并以 HTTP 刷新；随后核对请求来自当前会话的更新不会误报失败。内存层仅证明单进程行为，不代表 Redis 多实例部署。
指派／转单补充：真实双页面验收发现，另一页面完成指派后，原指派弹窗仍停留在旧订单上。收到对应订单更新时，若弹窗尚未提交，应就地说明订单已变化并禁止提交，提供关闭刷新；正在提交的操作由其真实响应处理，避免把自身推送误判为外部冲突。

### 订单详情抽屉计划（2026-10-29）
参考 [Medusa 订单摘要](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)按订单信息、金额与收益、后续记录分区，以及 [Pinterest 订单管理参考](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)的清晰留白。保留真实字段与退款历史，不把店铺留存快照标为利润。订单、售后和流水的读取结果分别反馈：更新期间保留上次内容并标明正在更新，任一部分失败就近提示，旧售后数据不可操作，重试仍从服务端读取。

### 取消与售后处理计划（2026-10-29）
参考 [Apple 警示设计](https://developer.apple.com/design/human-interface-guidelines/alerts)及 [Medusa 订单操作确认](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)：确认时显示订单与实际后果，售后处理明确不会自动退款。依据现有接口，取消原因可留空并截取至 255 字，售后处理说明须 2–1000 字；前端就地校验并保留真实服务端错误。提交中防重复，遇实时变更作废旧确认，结果未知时刷新核对；微信支付状态未确认或本入口不支持退款时展示服务端原因，不冒充取消或退款成功。

### 退款复核与异常恢复计划（2026-10-29）
参考 [Medusa 退款表单](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-create-refund/components/create-refund-form/create-refund-form.tsx)将金额、原因和支付对象放在同一操作上下文，结合前期 Pinterest 订单管理参考及 Apple 警示指南，保留真实试算、追回金额与不能隐藏的结果；简化重复叙述。现有接口根据操作凭证幂等，明确业务拒绝与网络中断并不等价：前者显示服务端原因，后者锁住当前表单并引导查看订单退款记录，避免修改退款意图后误重复提交。退款与详情均以隔离数据及真实 API 验证，网络中断仅用于验证失败反馈。

### 退款响应丢失与实时联动计划（2026-10-29）
对照 [Medusa 退款重试问题](https://github.com/medusajs/medusa/issues/16681)和本项目按退款意图绑定的操作凭证：超时并不能证明退款未发生。同一隔离进程使用真实数据库、真实鉴权和业务 API 完成退款，仅在业务响应生成后替换响应并延迟，以覆盖退款已提交、WebSocket 事件先到、HTTP 结果不可用；核对页面是否保留明确的未知结果和查看记录入口，再以数据库流水核对仅发生一次。该故障注入用于异常路径，不能代替正常业务测试或证明微信渠道行为。

### 退款提交期间重连计划（2026-10-29）
参考 [Medusa 退款重试问题](https://github.com/medusajs/medusa/issues/16681)中响应不确定与幂等凭证的区别，并沿用已记录的 Apple 警示和 Pinterest 订单层级参考。实时连接恢复时继续刷新数据：尚未提交的试算作废；正在提交或结果未确认的退款保留锁定和核对入口，等待真实 HTTP 结果，不因为重连认定成功或失败。账号变化、权限撤销和页面卸载继续使旧操作失效。验收使用隔离数据库和真实退款，测试层只断开 WebSocket 并替换提交后的 HTTP 响应。

### 报单审核交互计划（2026-10-29）
参考 [Pinterest 订单管理界面](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)的信息分区、[Apple 警示指南](https://developer.apple.com/design/human-interface-guidelines/alerts)的具体动作，以及 [Temporal 审批模式](https://github.com/temporalio/documentation/blob/main/docs/design-patterns/approval.mdx)对审批上下文和并发变更的处理。现有服务端记录审核人、驳回原因和时间，订单报单审核与旧手工报单入账不同；确认应包含报单和订单身份，保留真实结算提示。确认期间报单变更或连接恢复使旧确认失效，提交期间禁重且等待接口结果；明确拒绝和结果不明分别提示并刷新。详情凭证下提供审核入口，驳回原因与服务端 255 字上限一致。

### 默认抽成配置计划（2026-10-29）
参考 [Pinterest 设置页](https://in.pinterest.com/pin/settings--368239707027009631/)的分区、[Apple 设置指导](https://developer.apple.com/design/human-interface-guidelines/settings)和 [Microsoft 审核列表](https://github.com/microsoft/BCApps/blob/main/src/Layers/W1/BaseApp/System/Workflow/RequeststoApprove.Page.al)将任务相关操作置于上下文。实际配置接口分别保存订单抽成方式、固定额和旧手工报单比例，已有订单使用快照；接口没有版本校验。每次打开先读取当前真实值，读取失败显示重试并禁止保存，账号变化丢弃旧响应。固定金额保存保留实际读取的手工报单比例；提交期间禁止关闭、重复提交，结果不明时要求重新读取。并发修改不能仅靠前端证明无覆盖，需在验收记录中说明。

### 小屏报单凭证计划（2026-10-29）
参考 [Apple 图像视图](https://developer.apple.com/design/human-interface-guidelines/image-views)、[Apple 自适应布局](https://developer.apple.com/design/human-interface-guidelines/layout)、[Element Plus 图片加载与错误](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/image.md)及已记录的 Pinterest 订单布局。375×812 实际视口中报单详情字段可阅读，但白色凭证缺乏可识别的区域，已有预览入口不支持键盘。为凭证保留边界和完整图像，提供清晰名称、键盘触发及加载／失败状态；继续使用现有私有图片续期与图片查看器，不替换业务数据和权限。

### 小屏报单列表计划（2026-10-29）
在 375×812 实际视口发现表格主要显示编号、陪玩和固定操作列，状态、金额及收益被横向遮挡。参考 [Apple 列表内容指导](https://developer.apple.com/cn/design/human-interface-guidelines/lists-and-tables)、[移动管理页面](https://github.com/shahriear/Frox_Dashboard/blob/main/COMPLETE_RESPONSIVE_SUMMARY.md)及前述 Pinterest 订单列表：小屏使用真实记录的精简卡片，优先呈现状态、陪玩、服务、金额和收益，操作沿用详情与有权限的审核入口；桌面表格、服务端筛选、分页、实时刷新和失败状态不另建业务逻辑。

### 小屏订单列表计划（2026-10-29）
375×812 实际画面中固定操作列遮盖订单字段；参考 [Pinterest 移动订单历史](https://es.pinterest.com/pin/203225001915243510/)、[Apple 列表指导](https://developer.apple.com/design/human-interface-guidelines/lists-and-tables)和 [订单状态展示参考](https://github.com/muhammadattzaz/ecommerce/blob/dev/design.md)，小屏卡片保留完整订单号、老板、服务、金额、支付与订单状态和适用动作；派单、取消及退款仍使用现有权限、真实状态和处理函数。沿用同一服务端筛选与分页，支持本页全选、单选和复制；切换屏幕尺寸或刷新时明确清空旧选择，不将跨页选择误当成本页。桌面保留表格。

### 工作台待处理入口计划（2026-10-29）
参考 [Pinterest 订单管理看板](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)、[Apple 图表指导](https://developer.apple.com/design/human-interface-guidelines/charts)及 [Medusa 订单摘要](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)的上下文动作。已支付待接单按订单 PENDING + PAID 统计，不等于可派资格；待审核报单按 ProviderReport PENDING 统计，仅对有报单查看权限者返回和显示。点击分别打开对应真实筛选；工作台为读取时快照，最终操作仍以实时列表与服务端校验为准。无权限时不提供入口。

### 工作台售后入口计划（2026-10-29）
沿用 [Pinterest 订单管理看板](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)的分区，并参考 [Medusa 订单售后上下文](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)及 [Apple 反馈指导](https://developer.apple.com/design/human-interface-guidelines/feedback)。以真实售后 OPEN 状态统计涉及的订单数，仅向有订单查看权限者返回；入口打开同口径的可见、可取消筛选，允许与其他筛选组合，队列切换时清除旧条件。处理完成并不自动退款；各查询是读取时快照。以隔离数据库、真实鉴权及实际售后提交和处理验收。

### 工作台最近订单小屏计划（2026-10-29）
实际 375×812 视口中，最近订单表格的金额、状态和时间被横向遮挡。参考先前检索的 [Pinterest 移动订单历史](https://es.pinterest.com/pin/203225001915243510/)、[Apple 列表层级建议](https://developer.apple.com/news/?id=s8sl4tpa)和 [Medusa 订单上下文](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)，小屏按订单分行显示真实订单号、状态、服务、老板、陪玩、原金额和创建时间；桌面保留表格。有订单查看权限时以订单号进入订单列表并清除旧筛选；无权限时仅显示现有摘要。数据继续使用工作台同一接口，失败及空态不冒充最新记录；双陪按现有人员字段显示，不推断结算或付款状态。

### 陪玩业绩看板数据可信度计划（2026-10-29）
沿用先前检索的 [Pinterest 管理看板](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)，参考 [Apple 图表可访问性](https://developer.apple.com/design/human-interface-guidelines/charts)和 [Metabase 筛选排查](https://github.com/metabase/metabase/blob/master/docs/troubleshooting-guide/filters.md)。实际接口包含已支付及部分退款订单净额，男女性别图仅包括已标注且有分账的陪玩；二次指标不受日期筛选，`settled_salary` 实为已通过提现申请的申请金额总和，并非工资或税后打款。修正标签和必要口径提示；筛选期间不把旧数据当作新范围，失败提供重试，过期响应和旧会话响应不得覆盖当前结果。小屏原双日历弹层被裁剪，改用开始、结束两个单日期控件及明确查询，支持接口已有单边日期、范围校验和已应用范围标记。小屏榜单改为可读列表，图表提供文本数据，保留真实后端口径，不自行推算未归属数据。

### 微信手续费配置编辑计划（2026-10-29）
沿用已检索的 [Pinterest 设置界面](https://in.pinterest.com/pin/settings--368239707027009631/)，参考 [Apple 表单验证](https://developer.apple.com/design/human-interface-guidelines/entering-data)和 [Medusa 未保存编辑提示](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/i18n/translations/en.json)。现有真实接口以版本号拒绝并发覆盖，试算只读取服务端已保存版本。标记未保存输入，刷新和离开前确认丢弃；试算明确不会使用草稿；识别明确校验／权限拒绝、版本冲突和结果未知，不将拒绝误称成功。小屏历史按版本分组显示全部真实字段；历史读取失败清空旧结果并提供重试。仅用隔离数据库和测试商户信息验证，不开启真实微信支付。

### 提现列表与规则读取计划（2026-10-29）
沿用前述 [Pinterest 移动订单列表](https://es.pinterest.com/pin/203225001915243510/)，参考 [Apple 警示文案](https://developer.apple.com/design/human-interface-guidelines/alerts)及 [Medusa 财务操作上下文](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。375px 列表固定操作列遮挡金额及状态，改为使用同一真实数据和筛选的简洁卡片，保留审核权限与现有流程；待审核记录使用“应打款”而非“实际到账”。规则配置每次打开重新读取，读取失败显示重试并禁止保存；保存中锁定，结果未知要求重读。接口没有版本控制，说明并发最后保存生效，不虚构冲突保护。只在隔离账户及测试钱包验收，不进行实际打款。

### 提现审核未知结果计划（2026-10-29）
沿用 [Pinterest 订单管理参考](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/)，参考 [Apple 错误提示](https://developer.apple.com/design/human-interface-guidelines/alerts)及 [Medusa 对响应丢失与重复退款的案例](https://github.com/medusajs/medusa/issues/16681)：该案例只启发核对流程，不能推定本系统具备同样幂等能力。本项目审核接口事务提交后还会发送内部通知，错误响应不证明事务未提交；明确的 400／403 与网络或服务端错误分别处理。提交前以账号隔离的会话存储记录待核对标记，不保存收款账号或凭证；结果未知锁定审核并提供真实详情重读，终态仅显示已核对的当前状态，待审核仍须人工核实渠道及处理情况后明确解除限制。确认未提交时实时变更使确认失效，提交中保持锁定，已提交请求的自身事件不能吞掉响应。验收用隔离钱包和真实接口，异常仅替换或延迟响应，不实际打款。

### 转单金额与上下文计划（2026-10-29）
沿用 [Pinterest 移动管理界面](https://es.pinterest.com/pin/203225001915243510/)，参考 [Apple 表单验证](https://developer.apple.com/design/human-interface-guidelines/entering-data)及 [Medusa 订单操作上下文](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)。真实转单接口按内部整数单位计算，固定份额取可分配收益上限，罚款余额不足整笔回滚。现有两位小数输入经换算会静默四舍五入至一位兴安币小数；改为明确限制一位小数并在字段旁校验，保留服务端最终校验。弹窗显示当前订单，选择比例时显示份额关系，固定额超过当前快照时提示服务端上限；不把预览当成到账或资格承诺。原因按服务端 255 字限制输入。隔离真实订单覆盖有效比例、固定额上限与非法精度；不连接外部消息或生产资金。

真实转单后的列表验收发现旧陪玩也被当作当前陪玩显示。列表和详情的陪玩字段按接口返回的转出时间区分当前人员；收益明细仍保留历史参与者，不能删除或隐去历史分成。

### 转单结果核对与重连计划（2026-10-29）
沿用先前检索的 Pinterest 移动订单参考（本次再次打开受限），结合 [Apple 就地反馈](https://developer.apple.com/design/human-interface-guidelines/feedback)和 [Medusa 订单变更上下文](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/orders/order-detail/components/order-summary-section/order-summary-section.tsx)：操作状态与后续动作靠近订单。转单接口在事务内修改人员及份额，结果丢失或服务器错误不能证明未提交，只有明确 4xx 业务拒绝按失败展示。提交前记录当前账号和标签页的待核对订单；成功或明确拒绝后清除，未知结果通过订单详情与流水核对、明确确认后解除限制，不自动推断请求是否已完成。刷新页面保留该标记，存储不可用、跨标签及跨设备不作保障。重连时未提交的转单和指派表单标记过期，提交中的请求继续等待真实响应。以隔离数据库及真实业务接口覆盖成功、明确拒绝和提交后响应丢失；异常只替换响应或断开连接。

### 商品列表小屏与配置读取计划（2026-10-29）
沿用先前 Pinterest 移动订单布局参考；本轮 Pinterest 定向检索没有得到可核验的新商品页面。参考 [Apple 加载指导](https://developer.apple.com/design/human-interface-guidelines/loading)及 [Medusa 商品列表真实分页查询](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/products/product-list/loader.ts)：375px 商品列表改为突出名称、状态、价格、人数和规格的卡片，桌面保留原表格，使用相同真实筛选与服务端分页。商品状态仅表示项目自身上下架，不推定类目是否可见；双陪标明单人价格。类目切换回到第一页。类目、服务分类、等级、玩法和资格选项分别报告读取失败并可重试；数据不全时禁止编辑或新增，避免基于缺失分类误判礼物和抽成；已成功部分仍可用于查看。失败不冒充空值或零计数。以隔离数据库和真实 API 验证筛选、分页、权限与小屏，失败由测试层注入。

### 商品保存与价格校验计划（2026-10-29）
沿用先前 Pinterest 移动管理界面参考（本轮定向搜索未取得可核验的新图），参考 [Apple 就地反馈](https://developer.apple.com/design/human-interface-guidelines/feedback)及 [Medusa 对请求结果与实际落库不一致的记录](https://github.com/medusajs/medusa/issues/13962)，后者不代表本接口支持幂等。真实验收发现数据库约束商品价格必须大于零；前后端均明确校验该既有约束，禁止空值与多于一位兴安币小数被换算或四舍五入；名称、价格错误贴近字段。明确 4xx 展示服务端原因供修正；网络或 5xx 锁定重复保存、提示核对。提交前按账号与标签页记下商品 ID 或名称，结果未知保留并提供真实商品筛选与人工确认，不自动判断是否写入；创建名称可能重复、被归档玩法关联的商品可能不在当前列表，须提示人工继续核实。列表新增服务端名称／ID 筛选，沿用既有权限及分页；该会话标记不替代服务端幂等或跨设备保障。验证采用隔离数据库及真实 API，异常只替换实际保存后的 HTTP 响应。

### 商品编辑信息层级计划（2026-10-29）
Pinterest 商品编辑检索及图片搜索本轮无可核验结果，继续沿用此前的移动管理参考；借鉴 [Apple 表单先后顺序](https://developer.apple.com/design/human-interface-guidelines/writing)及 [Medusa 商品详情分区](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/routes/products/product-detail/product-detail.tsx)。现有弹窗将关键名称和价格置于较深位置，调整为基础与价格、所属与接单、规则与权益、玩法及展示四个有语义标题的区块；名称、价格、人数和上架状态优先显示，复杂但可能影响业务的设置仍可直接访问，不默认折叠。将附加费用、历史档位和优惠券的次要解释放入支持点击与键盘的字段提示，保留重要规则、服务端拒绝及错误焦点。只重排与解释真实字段，不更改默认值、金额或业务判定；验收普通、双陪、礼物、玩法条件及已有关联订单的真实 API 和 375px 表单。

### 商品草稿与分类切换计划（2026-10-29）
参考 [Apple 撤销与反馈](https://developer.apple.com/design/human-interface-guidelines/undo-and-redo)和项目此前检索的 [Medusa 未保存编辑提示](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/i18n/translations/en.json)；本轮 Pinterest 定向搜索无可核验新图。编辑和新增打开后记录真实表单基线，修改后标记未保存；关闭、路由切换及浏览器离开前提醒，取消后保留内容；无修改、成功保存和结果未知核对路径不阻断。保存期间不允许离开；账号或权限变化仍按既有规则处理。切换到礼物临时隐藏商品自定义抽成，但保留未提交草稿以便切回；实际礼物提交仍按真实服务端规则禁用自定义抽成。隔离真实 API 验证原商品抽成切换、未保存关闭／路由、成功保存及礼物规则，浏览器关闭提示按实际可测范围记录。

### 陪玩列表信息与小屏计划（2026-10-29）
已在 Pinterest 查看 [人员列表及操作参考](https://www.pinterest.com/pin/610660030759956856/)，结合 [Cal.com 用户列表](https://github.com/calcom/cal.diy/blob/91eefbf9f460bac7d69327fea4d9560295390e0a/apps/web/modules/users/components/UserTable/UserListTable.tsx)中身份、权限和操作同列的组织方式及 [Apple 列表层级](https://developer.apple.com/news/?id=s8sl4tpa)。375px 把横向表格转为同一服务端筛选／分页的人员卡，突出身份、状态、认证、游戏与启用资格；保留原权限控制和操作，桌面表格不变。真实服务端状态显示值为英文，优先映射现有中文枚举，未知值标待核对；仅统计启用资格，区分未配置和均已停用。状态只描述档案状态，接单仍受账号、认证、押金、档期、服务与名额限制，提供可点击提示，不推断可派。隔离数据库、真实 API 验证状态／认证筛选、资格、权限及小屏；不执行押金、奖罚或对外消息。

认证操作原先点击即切换；增加包含陪玩身份及接单影响的确认、提交中防重复、失败和结果未确认反馈，并重新读取列表。后台认证接口当前无版本号，不能保证多个操作员同时修改时的冲突保护；只对隔离测试陪玩实际切换认证，不涉及资金。
### 陪玩档案押金与编辑（2026-10-29）
- 参考 Pinterest 的 [后台设置表单](https://www.pinterest.com/pin/368239707027009631/)分组与主操作、[Vue 管理端表单](https://github.com/van104/campus-finder/blob/main/web/src/views/admin/UserManagement.vue) 的明确提交，以及 Apple 对清晰标签、未保存编辑的建议；参考用于信息组织，押金业务以当前接口和模型为准。
- 现状：编辑可切换模式但请求没有该字段；金额、抵扣比例和冻结时长硬编码，与实际启用规则、配置及档案字段不一定一致。后端已有押金时禁止切换并在更新时再次核验；规则开启时计算目标，否则保留档案目标。
- 计划：模式提交真实字段；取消固定金额和比例，显示服务端返回的当前应缴／已缴，变更模式时提示保存后按服务端规则确定；已缴时说明禁用原因。仅作界面和请求修复，不自行推算新的金额或改变押金政策。验证开启／关闭规则及已缴押金冲突，使用隔离真实接口与数据库；小屏检查字段与反馈。

### 陪玩资料草稿保护（2026-10-29）
- 参考 Pinterest [未保存状态提示](https://www.pinterest.com/pin/356699232970182143/)、[Vue Router 离开守卫](https://github.com/vuejs/router/blob/main/packages/docs/guide/advanced/composition-api.md)和 [Apple 模态编辑关闭](https://developer.apple.com/videos/play/wwdc2022/10001/?time=727)，延续项目商品编辑页的明确放弃／继续编辑模式，避免只靠颜色表达状态。
- 编辑与创建表单以打开时可编辑字段和所选文件为基线；关闭、路由离开时仅对真实改动确认，取消确认保留所有字段与资格；保存及创建进行中阻止关闭和重复提交。注册浏览器离开提示，实际浏览器是否展示由其自身决定；账号切换清除表单，避免跨会话显示。创建密码仅在表单内存中使用，不写入持久存储。
- 使用隔离账号、真实接口核对无修改关闭、修改资格／昵称后取消和放弃、保存成功后关闭、创建验证及重复提交控制；不操作真实账号或发送外部消息。暂不声称创建结果未知时的跨设备幂等或服务端并发保护。

### 陪玩资料保存结果核对（2026-10-29）
- 参考 Pinterest [反馈组件](https://www.pinterest.com/pin/1005710160541425105/)的不同状态区分，以及 [真实请求结果未知时不盲目重试](https://github.com/e2b-dev/runtime/blob/main/docs/ARCHITECTURE.md)；具体请求语义按现有接口实现，不能假设服务端幂等。
- 提交前按登录管理员在本标签页记录档案 ID 或新建登录账号，不记录密码、电话、照片和表单内容；明确成功或 4xx 拒绝清除标记，网络错误／5xx 则保留并停止再次创建与编辑。关闭表单后允许从真实 API 按档案 ID 或精确登录账号读取最新档案，以只读编辑表单核对；未查到也显示请求可能仍处理中，读取失败不允许解除限制，人工确认核对后解除。刷新当前标签保留标记，无法扩展成跨标签或设备的幂等保证。
- 测试隔离数据库真实保存／创建后仅替换响应，以及真实 400、查询与恢复；不替换业务成功逻辑、不访问真实客户或外部服务。

### 接单资格编辑布局与联动（2026-10-29）
- 参考 Pinterest [移动表单标签](https://www.pinterest.com/pin/256494141260401160/)、[Element Plus 动态表单](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/form.md)和 [Apple 字段标签](https://developer.apple.com/design/human-interface-guidelines/text-fields)：每条资格独立分组，保留字段标题；375px 用两列、较宽屏幕用三列，确保选项有足够宽度和触控间距。规则说明移入可点击／聚焦提示，关键错误保留在对应资格旁。
- 根据真实 `QualificationsField`：同一游戏／方向／平台／难度只能一条、最多 80 条、停用游戏不能启用、档位必须属于服务端返回范围。切换方向或平台导致难度／档位被调整时提醒核对；加载资格选项失败应可重试，未确认的选项不能用于提交。既有低档覆盖、高档位、禁用态及保存逻辑以服务端为准。
- 通过隔离数据库及真实 API 验证 375px、范围联动、重复资格提示及有效资格保存／回读；不使用替身模拟业务成功。
- 用户追加要求所有业务组件使用小尺寸：管理端全局已是 Element Plus `small` 和 32px token；移除移动端把普通按钮及本组件控件强制增高至 44px 的覆盖，保留明确标签和控件间距，并核对弹窗、筛选和操作在 375px 的实际尺寸与可用性。

### 老板资料与订单联动（2026-10-29）
- 参考 Pinterest [移动 CRM](https://www.pinterest.com/pin/473159504615568015/)、[Medusa 客户管理](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/i18n/translations/zhCN.json)与 [Apple 列表](https://developer.apple.com/design/human-interface-guidelines/lists-and-tables)：375px 按真实列表数据分组为身份、账户与余额卡，保留小尺寸控件、服务端筛选分页及原权限，桌面保留表格；长说明移至可点击提示。
- 后端账户启用和登录许可是独立字段，列表分别呈现；状态筛选明确仅筛选 `is_active`。余额说明只描述钱包余额，不把所有订单的支付／退款路径一概而论。退店确认必须包含目标身份和真实规则，继续由服务端核验；本轮只验真实拒绝，不清空真实客户权益。
- 业务账户没有关联钱包时，列表接口返回空值，页面显示“待核对”，避免把未关联旧钱包误当成零余额；不在读取列表时自动创建或回填钱包。
- 退店接口的明确拒绝仅显示一次服务端原因，提交期间禁止再次操作；网络／服务器结果未知时提示先联系管理员核对，不把未知结果称为失败。该接口尚无请求幂等或前端持久核对机制，本轮不声称已解决重复清空风险。
- 增加有 `order:view` 权限时的“查看订单”，按业务账户 ID 精确过滤；对旧订单仅在 `customer_account` 为空且有真实旧账号映射时使用旧用户 ID，防止同名账号混入。订单页显示可清除的当前老板筛选，原待派及订单号快捷筛选切换时清除该限定。
- 以隔离账号、真实 API 与数据库验收筛选、登录受限、余额、权限、同名不同账户订单隔离及旧映射，避免触及真实资金和客户。

### 老板资料编辑：分级与推荐人（2026-10-29）
- 参考 [Pinterest 设置表单](https://www.pinterest.com/pin/349732727328176413/) 的字段分组及紧邻状态提示，以及 [Element Plus Select](https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/select.md) 的远程搜索与禁用选项；继续使用项目的小尺寸控件。
- 按服务端实际校验标记所有由 VIP 规则管理的分级（包括停用规则），当前 VIP 不能手动切换，其他分级不能切入 VIP；选项读取失败时保留原分级并提供重试。
- 推荐人按昵称、账号或编号搜索，区分同名对象，保留当前选择并防止旧请求覆盖新结果；无推荐人时保留比例但提示暂不生效。仅在真实隔离数据库中验证修改和规则。

### 老板资料草稿和结果核对（2026-10-29）
- 参考 [Vue Router 导航守卫](https://github.com/vuejs/router/blob/main/packages/docs/guide/advanced/navigation-guards.md)、[beforeunload 限制](https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event) 和 [Pinterest 设置表单](https://www.pinterest.com/pin/349732727328176413/)。未保存修改只在离开时确认，状态与下一步保持贴近表单。
- 提交前仅在本标签页按操作员记录待核对账户 ID；确定成功或 4xx 后清除，网络错误或 5xx 则保留并阻止重复编辑。读取真实账户接口后以只读表单核对，明确请求可能仍在处理，再由操作员确认解除。保留存储不可用、跨标签和缺少版本校验的限制。
- 用隔离数据库和真实接口验证草稿取消/放弃、正常保存，以及真实保存后仅替换 HTTP 响应的未知结果；读失败与手动解除需验证。

### 钱包列表与资金操作（2026-10-29）
- 参考 [Pinterest 手机财务面板](https://www.pinterest.com/pin/760334349626833654/) 的金额优先及卡片分组、[开源钱包管理](https://github.com/malsubrata/woo-wallet/blob/master/includes/class-woo-wallet-admin.php) 的身份、余额和流水关联。以当前 API 实际字段为准，余额与冻结分别展示，不推算可用余额。
- 手机钱包卡按身份、状态、余额、累计金额排列，保留权限与老板/打手区别，控件沿用 32px；桌面保留表格。调账和充值确认显示目标和金额，提交时锁定关闭与输入。成功、明确拒绝及未知结果区分；未知时按操作员仅存钱包 ID 和动作以阻止重复，提示核对资金流水或充值记录，人工确认后解除，不把当前余额当作操作成功凭据。
- 验收在隔离数据库中使用真实鉴权和资金 API；外部消息隔离，响应故障注入仅用于异常恢复，不用于代替正常业务处理。
- 验收中发现核对入口进入全量流水后仍需手工辨认钱包；补充按钱包 ID 精确筛选及 375px 流水卡片，清除条件后恢复全量。只在拥有流水权限时展示入口；筛选不能把其他钱包流水混入。

### 后台导航轨（用户 2026-10-29 新要求）
- 已观察 [Pinterest 图标导航轨](https://www.pinterest.com/pin/31947478603685270/)及 [后台菜单](https://www.pinterest.com/pin/113645590580378906/)，参考 [Vue 权限导航](https://github.com/PanJiaChen/vue-element-admin-site/blob/master/docs/guide/essentials/router-and-nav.md) 和 [Apple 导航指引](https://developer.apple.com/design/human-interface-guidelines/tab-bars)。保留现有权限过滤、路由及中心内二级导航。
- 桌面右侧紧凑轨保留图标，当前中心显示名称，其他入口支持悬停与键盘焦点提示，提供可访问名称；内容区主标题显示具体功能。顶栏使用透明／半透明背景以适应滚动可读性。移动端改为右侧展开的完整文字菜单，保留搜索、遮罩、Esc 关闭和安全区。
- 轨旁提供有权限的快捷派单，进入已有订单页并打开现有表单；已有草稿、未确认操作或无权限时不重置已有表单。无导出实现不新增虚假入口。验证 375px、1280px 和键盘焦点、权限过滤、二级导航及真实隔离派单入口。

### 充值记录与结果核对（2026-10-29）
- 参考 [Pinterest 付款历史](https://www.pinterest.com/pin/130463720443489991/) 对金额与时间的层级，以及 [GitHub 充值平台](https://github.com/rohan-js/telcopay-platform) 的账户历史与可追溯记录；本项目客服充值是同步记账，不能套用参考项目的异步运营商状态。
- 根据当前一用户对应一钱包的实际模型，按钱包关联的业务用户筛选充值记录；旧记录仍保留，明确这是关联账户历史，不把历史记录猜成某笔本次请求。收款流水号按服务端现行去重规则不区分大小写精确查询。手机卡显示到账、实充、赠送、身份、流水号、时间及凭证，桌面表格保留；复用受权限保护的凭证组件。
- 测试通过隔离 SQLite、真实认证、客服充值和记录接口；验证同名账户隔离、旧记录、空结果、流水号筛选和凭证，网络故障只用于异常流程，不替代业务成功。
# 2026-10-29 操作审计小屏规划

- 参考 Pinterest 活动日志图（https://in.pinterest.com/pin/869898484277271595/）：操作人、动作、目标与时间分层排列，完整信息按需展开；参考 GitHub 的审计日志筛选及详情（https://github.com/dimitrisganotis/strapi-plugin-audit-logs），保留原接口的组合筛选、分页和只读详情。
- 项目实际采集的是后台写请求、HTTP 状态小于 400 且审计保存成功的记录，未记录失败请求；状态码不等于业务结果。页面简洁说明范围，记录不足以证明某操作未发生。
- 375px 增加卡片：操作人、请求方法、资源及对象编号、时间、响应码；完整路径、IP 和已记录的请求参数放详情，长文本可换行。保留桌面表格及现有筛选口径，不虚构业务动作与审计覆盖率。补充清除筛选、无障碍标签与移动端不依赖悬停的说明。
- 用隔离数据库真实管理员写入产生审计记录，按方法、关键词和日期检索，在 375px 核对卡片、详情、清除筛选及布局；验证无审计权限拒绝。无外部资金或消息。

# 2026-10-29 KOOK 群派单配置规划

- Pinterest 通知设置参考（https://in.pinterest.com/pin/110056784637696139/）将配置按功能分区；Prometheus Alertmanager 开源规则（https://github.com/prometheus/alertmanager/blob/main/docs/configuration.md）强调匹配顺序和首个匹配的路由后果。本项目只有平铺的首个命中规则，不引入嵌套、继续匹配或自动兜底等额外语义。
- 根据真实 KOOK 配置接口：首次读取失败时不展示默认配置，不允许保存；保存中锁定编辑，409 和网络／服务端异常明确要求重新读取，保留当前草稿至操作员确认放弃。配置有服务端 revision，重新读取不能证明 Token 实际值；不保存 Token 至本地存储。已有明确 4xx 校验保留草稿。
- 375px 频道规则和测试记录改用卡片，突出顺序、条件、状态、频道和动作，原始接口和权限不变；桌面保留表格。只在已保存状态下校验或测试，外发测试仍需原有确认，规则移除和上下移动仅修改草稿直到保存。
- 使用隔离数据库与真实 API 验证规则新增、排序、保存、并发 409、读取失败恢复和权限；外部 KOOK 测试不发送，仅验证未提交动作及服务端规则，注明真实外部投递未验收。

# 2026-10-29 派单设置弹窗与时限历史规划

- 参考 Pinterest 设置弹窗（https://in.pinterest.com/pin/804877764680624637/）保留清晰内容区和操作区；Grafana 的未保存变更处理（https://github.com/grafana/grafana/blob/main/public/app/features/dashboard-scene/saving/DashboardPrompt.tsx）在离开时区分放弃与继续编辑。实际订单页既能嵌入 KOOK 配置也能打开时限设置，弹窗关闭和路由离开必须覆盖这两条入口。
- 时限历史按接口真实字段展示前后分钟、版本、操作者和时间，375px 用卡片，桌面保留表格与原分页、重试及审计权限；不推测未记录的配置变更。
- 时限修改未保存时关闭或切换路由先确认，保存中不关闭。KOOK 父弹窗调用子页面同一套草稿／在途保护，不把嵌入时关闭视为路由跳转；确认只丢弃未保存内容，不修改服务端配置。保存响应未知时仍提示按现有方式核对。
- 采用隔离数据库和真实配置接口实测两种弹窗的取消／确认关闭、时限保存和历史，校验小屏与权限；不发送真实群消息。

# 2026-10-29 峰谷限流弹窗规划

- 参考 Pinterest 监控面板（https://in.pinterest.com/pin/246220304607831408/）按指标区块呈现和 Grafana 未保存处理（https://github.com/grafana/grafana/blob/main/public/app/features/dashboard-scene/saving/DashboardPrompt.tsx）；Element Plus 输入控件以 model-value 为准（https://github.com/element-plus/element-plus/blob/dev/docs/en-US/component/input-number.md）。本系统只提供固定窗口的当前计数，不生成趋势或虚构健康评分。
- 订单页限流弹窗关闭、路由离开与浏览器离开保护未保存草稿，保存中不关闭；保存结果未知说明请求可能仍在处理、刷新仅为当前快照。按服务端 revision 处理冲突，明确 400 拒绝保留输入。
- 保证切换波峰／常规／波谷及保存或重新读取时数字控件与真实模型一致；手机监控按四项实际动作展示放行和限流，保留现有关闭时统计不完整、无样本及单实例限制说明。
- 隔离 SQLite 和真实鉴权接口，在 375px 测试草稿关闭、三时段切换、真实保存及回读，核对明确拒绝／冲突；不调用真实资金或外部消息。

# 2026-10-29 一键发单记录与当前配置

- 参考 Pinterest 邮件管理布局（https://in.pinterest.com/pin/762656518122260976/）将状态和内容分区；参考开源 Pulse 持久通知队列（https://github.com/rcourtman/Pulse/blob/main/docs/TROUBLESHOOTING.md）区分已排队的目标快照与当前配置。项目的发单记录按订单轮次和渠道去重，只有明确 FAILED 才能人工重试；外部消息不能用本地结果推断已读。
- 按现有数据模型，PENDING／SENDING／SENT／UNKNOWN／SKIPPED 使用记录中的频道和文案；旧记录缺文案时明确未记录，不用当前文案冒充快照。FAILED 或无记录显示当前可重试／待发送目标与文案，并在目标变化时指出上次目标。历史规则名称未保存，不用当前规则名称代替。
- 投递记录状态和当前配置状态分开显示；渠道停用不覆盖已发送或待核对状态，未配置不能选择。刷新提示说明读取的是当前快照，无法证明仍在处理的请求最终结果。
- 在隔离数据库通过真实配置、真实发单入队和预览 API 验证旧目标、模板变更、停用后的状态及失败重试目标；仅隔离异步队列，不向外部频道发送。375px 核对内容、标签、选择和复制可用状态。

# 2026-10-30 发单确认与投递快照规划

- 参考 Pinterest 发送确认（https://ca.pinterest.com/pin/256775616231537136/）以及开源通知服务预览／异步跟踪（https://github.com/bcgov/common-notify）；并发比较借鉴 GitHub 乐观并发实现（https://github.com/ParachuteComputer/parachute-patterns/blob/main/patterns/optimistic-concurrency.md）。当前业务按订单轮次和渠道去重，不能假设点击即送达。
- 当前确认只写渠道名称，后端在提交时重新解析配置，预览后规则、文案、目标、订单轮次或渠道状态变更可导致入队内容与操作者确认的不一致。预览将返回每个渠道的投递计划校验值；提交必须带所选渠道的校验值，在订单锁内重新计算并比较。匹配后用同一份服务端计划入队；不匹配整体返回 409，无任何渠道入队。已有自动发单与下游投递校验保持。
- 确认框写明订单号、渠道及可见 KOOK 目标，确认期间冻结选择；冲突明确提示刷新并重新核对。只传摘要，不回传机器人 Token 或企微密钥。旧调用方缺少校验值会被拒绝，必须先读取预览；同步更新仓库内调用与测试。
- 隔离数据库真实接口：分别验证预览后更改频道、文案、订单轮次和记录状态的拒绝；未变化的计划实际入队，队列使用进程内 broker，无外部消息。浏览器 375px 验证确认／取消、另一会话修改后的冲突恢复，以及多渠道全有或全无。

# 2026-10-30 发单提醒对象与卡片明细规划

- 参考 Pinterest 邀请对象列表（https://uk.pinterest.com/pin/50313720830470885/）突出实际对象，以及 KOOK 适配器对消息提醒与卡片的区分（https://github.com/shanfishapp/ErisPulse-KookAdapter/blob/main/platform-features.md）。项目实际卡片的身份组提醒在 KMarkdown 模块、链接在操作模块；通用复制文案本身不携带它们。
- 从真实待入队卡片或已有记录的卡片快照提取身份组 ID 和链接数量，不回传链接地址或机器人凭据；历史卡片不存在或结构无法识别时显示未知，不按当前规则猜测。当前待发卡片无法解析时禁止确认发单并提示重读。
- 在渠道附近展示提醒对象与链接数量，并在确认框重述选中 KOOK 频道的提醒对象；无身份组明确“不提醒身份组”。沿用上轮服务端投递计划校验，内容、目标、提醒及链接变化仍要求重新确认。仅 32px 控件，长 ID 自动换行。
- 使用隔离数据库、真实配置和真实入队接口核对多个身份组、无提醒、配置变化冲突及记录卡片缺失；进程内队列无 worker，不发送真实消息；375px 核对长身份组、状态和确认。

# 2026-10-30 工作台近期订单金额与支付状态规划

- 参考 Pinterest 订单管理（https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/）的紧凑列表，以及 Shopify 对销售和退款统计口径的区分（https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/report-types/analytics-fields）。订单当前服务状态与支付状态分别展示，避免部分退款的已完成订单被误读。
- 实际 `amount` 是优惠后的订单金额，`original_amount` 才是优惠前原价；近期订单显示“订单金额”，说明未扣退款，不将未支付订单称为实付。累计净额继续按已有真实退款记录计算，两者口径明确分开。
- 把工作台“开始读取”时间记录在首项统计查询之前；各项仍分别读取，并非同一事务快照。
- 使用现有后台序列化的支付状态，桌面添加小尺寸状态标签，手机卡片中保留独立支付字段；不新增虚构指标。通过隔离数据和真实退款接口核对已完成、部分退款、金额与净额，并在 375px 和桌面检查布局。

# 2026-10-30 评价管理移动布局与回复上下文

- 参考 Pinterest 评价管理页面（https://uk.pinterest.com/pin/explore-our-review-management-platform-with-our-comprehensive-walkthrough-learn-how-our-tools-can-improve-customer-feedbac-in-2025--45458277484372748/）以及开源评价模块中列表、详情、回复和删除分工（https://github.com/VirtoCommerce/vc-module-customer-review）。本项目只有一条可被覆盖的回复，没有额外审核状态，不引入其他系统的批准流程。
- 375px 以订单、陪玩、评分、内容与回复状态组成卡片，保留详情和权限允许的操作；桌面仍为表格。回复弹窗展示订单、评价和已有回复，区分首次发布与修改，说明修改覆盖当前回复；对齐服务端 1000 字限制并在请求期间锁定关闭与重复提交。删除确认展示订单号和评分及真实后果。
- 桌面表格压缩列宽并用数字显示综合评分，让回复状态、评价时间和操作在常见笔记本宽度同屏；详情保留星级及完整字段。
- 保留原五个细分字段筛选，收纳为“更多筛选”；常用入口改用接口已有的订单号、老板或陪玩账号／昵称组合搜索，并显示已填写的细分条件数量，减少手机首屏筛选占用。
- 使用隔离数据库真实下单、完成、评价和回复接口核对筛选与详情；检查手机、桌面布局及权限。接口没有并发版本校验，其他端同时修改时仍可能覆盖，不能把打开弹窗视为锁定；不触达真实客户。

# 2026-10-30 评价回复并发核对规划

- 参考 Pinterest 评价弹窗（https://ca.pinterest.com/pin/feedback-animation-ui-design-for-review-modal--414401603191423173/）的上下文和反馈，以及 Zalando 开源 API 的版本比较方案（https://github.com/zalando/restful-api-guidelines/blob/main/chapters/best-practices.adoc）。实际后台和陪玩三个入口共用一条回复，不能只在某一个入口检查。
- 为每条评价增加整数回复版本，两个回复接口必须携带读取到的版本，通过单条条件更新原子比较并递增；冲突返回 409，不执行覆盖。后台保留草稿，展示冲突并提供读取当前回复、重新核对；陪玩入口保留草稿并在冲突后锁定提交，需关闭并刷新评价后重试。缺版本的旧客户端明确拒绝，部署须同步升级；不把网络失败后的读取当作原请求最终结果。
- 以隔离数据库真实老板下单、完成和评价，后台／陪玩双方读取同一版本并交替提交，验证旧版本被拒绝、内容不丢失和再次读取后可修改；检查 375px 冲突反馈、权限及迁移。SQLite 不代表 MySQL 行锁竞争；核心业务不得替身，禁止触达真实客户。

# 2026-10-30 评价删除确认与状态核对

- 参考 Pinterest 删除确认（https://in.pinterest.com/pin/great-ui-for-deletion-confirmation-requiring-the-user-to-type-the-word-delete--66357794487827667/）突出后果，以及 Microsoft 开源示例（https://github.com/MicrosoftDocs/powerapps-docs/blob/main/powerapps-docs/developer/data-platform/webapi/web-api-conditional-operations-sample.md）按版本拒绝旧删除；本项目使用现有回复版本，不要求额外输入确认词。
- 删除框展示订单、评分、评价和已有回复，说明删除评价与回复并回退评分。服务端在行锁内对比回复版本后删除并回退聚合；记录不存在返回 404，旧版本返回 409 且不更改评分。客户端明确冲突、读取最新评价后才允许再次确认，网络／服务端异常显示结果未确认并阻止弹窗内重复提交；关闭后重读列表只表示当前快照。
- 隔离数据库用真实订单、评价和跨端回复验证冲突、刷新后删除、评分回退及重复删除；375px 核对确认框、错误和恢复。旧客户端删除请求缺版本将被拒绝，前后端须同步升级。

# 2026-10-30 评价删除响应丢失后的核对

- 参考 Pinterest 错误状态（https://in.pinterest.com/pin/system-error-pages--1110981801816748832/）提供可执行下一步，以及 Apache Kudu 对超时后执行状态未知的说明（https://github.com/apache/kudu/blob/master/docs/design-docs/rpc-retry-and-failover.md）。本项目删除有版本校验和原子评分回退，但没有可证明请求结束的操作回执。
- 提交前为当前后台账号和标签页保存待核对编号；明确成功或明确拒绝时清除，响应不确定或页面重载时保留并限制新的删除。读取目标评价：若存在显示当前快照与原请求可能仍处理，若 404 只说明评价当前不存在；操作员核对后才能主动解除限制。存储不可用时仅本页有效，不声称跨设备保护。
- 在隔离库通过真实订单、评价、删除和评分回退执行成功路径，仅在提交成功后用测试中间件替换响应为 503；核对重载后的提示、真实记录及评分。再以停止隔离后端测试记录仍存在但原请求未发出的状态，恢复后检查明确的快照说明和人工解除。

# 2026-10-30 陪玩奖罚及记录核对

- 参考 Pinterest 移动资金记录（https://in.pinterest.com/pin/130463720443489991/）和 Pretix 对重复请求恢复回执的实现（https://github.com/pretix/pretix/blob/master/doc/api/fundamentals.rst）。现有奖罚直接改余额，但没有请求去重，且人工奖罚记录未写业务账号，陪玩按账号查询可能看不到；不引入新的奖罚规则。
- 沿用项目资金幂等键和原子回执：同一请求只入账一次，原参数重放恢复结果，改参数冲突；余额、流水、陪玩累计和奖罚记录同事务，补齐记录账号与操作人，并提交后通知钱包更新。兼容已有未传键客户端的项目级短窗口策略，不把旧数据猜测为新回执。
- 后台奖罚输入使用实际 0.1 币精度，确认明确陪玩、类型、金额和原因；提交时锁定输入和关闭。结果未知在当前账号标签页保留原请求，可由操作员确认继续同一请求以恢复回执；若此前未到达则会执行原请求。记录页支持账号范围与手机卡片，不把同昵称视为同账号。
- 隔离数据库真实奖励、罚款、失败、重放、改参数冲突和陪玩端记录读取；只在业务提交后替换响应验证恢复，不对真实钱包或客户操作。兼容依赖前后端及迁移的部署状态按真实实现说明。

# 2026-10-30 客服名片列表与操作核对

- 参考 [Pinterest 客户列表](https://de.pinterest.com/pin/502362533427146695/) 的紧凑列表和 [Chatwoot 客服渠道管理](https://github.com/chatwoot/chatwoot/blob/develop/app/controllers/api/v1/accounts/inboxes_controller.rb) 的分离配置操作。以本项目实际客服模型和路由为准：启用名片按排序、更新时间选择默认，订单原客服不可用时回退默认，删除会解除订单关联。
- 手机采用紧凑名片，显示状态、企业微信配置、排序和操作；桌面保持表格。说明排序同值时按更新时间排序，配置完整不代表接待服务可用。表单收纳规则，保留企业 ID 和链接配对校验、状态变动后果及草稿保护；确认删除时说明影响。
- 保存和删除过程中锁定重复操作；未知响应时保留结果待核对提示，重新读取只是当前快照，不视为原请求回执。隔离真实接口验证创建、启停、默认与订单回退，并检查 375px 视图；不连接真实企业微信。

# 2026-10-30 客服名片待核对详情

- Pinterest 客服详情页检索可见参考标题但原站返回 403，本轮不臆称已看到其细节；[Chatwoot 渠道接口](https://github.com/chatwoot/chatwoot/blob/develop/app/controllers/api/v1/accounts/inboxes_controller.rb) 提供逐项读取与更新，结合项目现有分页列表和真实客服字段设计只读详情。
- 新增按名称、所属或微信号的服务端搜索，保留分页；列表每行能重新读取只读配置。编辑和删除请求记录目标 ID，待核对时按 ID 读取或提示当前不存在；新增结果无 ID，按名称查找并提示同名需要逐条核对。读取快照不能证明请求最终状态。
- 375px 验收已提交响应丢失后的实际配置展示、分页搜索、重名和读取失败；测试使用隔离库、真实 API 和账号权限，不接企业微信。

# 2026-10-30 订单老板查询口径与小屏筛选

- 参考 [Pinterest 移动筛选](https://in.pinterest.com/pin/sort-filter-mobile-ui--3448137192351532/) 的可收纳筛选方向与 [Saleor 后台筛选](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md) 的可展开条件；结合项目实际订单字段，保留订单号、账号、客服待派和售后快捷入口，其余条件收起并显示已选数量，增加统一清除。
- 后台订单显示使用业务账户，但老板信息筛选查询旧用户。改为优先业务账户，仅订单未绑定业务账户时回退订单关联旧用户；列表显示采用相同回退，避免旧字段与新账户不一致时串查。手机号查询为当前账户手机号，不承诺按下单时手机号快照搜索。
- 隔离数据库使用真实派单、资料更新及列表 API，构造旧／新账户字段不一致的历史场景，验证组合条件、关联账户隔离、缺绑定回退及分页；在 375px 验证展开、清除和快捷筛选。未连接外部消息或真实资金。

# 2026-10-30 工作台订单直达精确定位

- 检索 [Pinterest 订单列表与详情](https://in.pinterest.com/pin/list-view-details-page-for-dashboard--723250021386033393/) 与 [Saleor 订单号精确筛选](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)；本项目工作台链接携带完整订单号，但订单页当前 `icontains` 会将包含该编号的其他订单一起列出。
- 为工作台直达增加服务端完整订单号匹配，普通输入仍保留包含搜索。界面明确标示精确定位；编辑订单号、清除条件、待派或其他快捷入口时退出精确模式，避免隐藏约束。权限沿用订单查看权限。
- 在隔离数据库真实创建两笔订单，再设置包含关系的历史测试编号；验证精确查询与普通查询、移动端跳转、编辑和清除联动。模拟的编号只用于隔离测试，不调用外部支付或消息。

# 2026-10-30 报单陪玩身份与筛选

- 检索 [Pinterest 移动工作台](https://jp.pinterest.com/pin/926897167046319819/) 与 [Saleor 后台列表筛选](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)，以本项目陪玩业务账号与陪玩档案为准：报单卡片优先展示结单昵称、陪玩编号和账号，详情同时显示关联订单；搜索按业务账号／结单昵称，未绑定业务账号时才查旧用户及其档案。
- 名称相同不等于同一陪玩；接口保留账号昵称并支持结单昵称搜索，禁止以旧用户内容匹配已绑定的其他业务账号。保留既有审核、凭证和金额计算逻辑，不新增业务状态。增加筛选控件可访问名称。
- 使用隔离库真实派单、接单、完成、凭证上传及提交后，构造历史身份资料差异验证查询和审核；375px 检查同名卡片和详情。不会触达真实消息、支付或个人资料。
- 追加跨页一致性：验收发现订单列表以账号昵称显示同一人，与报单结单昵称不同。订单当前参与人优先读取对应业务账号的陪玩档案结单昵称；档案缺失时保留订单已有名字快照回退，不据其他账号猜测。工作台和订单查询预加载关联档案以控制查询次数。

# 2026-10-30 待接单按派单方式分析

- 检索 [Pinterest 订单管理工作台](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 和 [Saleor 订单状态与支付筛选](https://github.com/saleor/saleor-dashboard/blob/main/CHANGELOG.md)；设计依据本项目已有 PENDING／PAID 和 STAFF／PUBLIC 字段，工具数据未匹配此特定指标，采用文字标明口径的通用可访问展示。
- 在待处理区的总数下增加客服派单和公开接单两项实际快照，分别链接到同口径订单筛选。两类是已支付且待接单的订单，不代表均可指派、未超时或消息已送达；与顶部经额外校验的客服可派队列分开。订单列表对 PUBLIC 明确显示公开接单；无订单查看权限不显示跳转。
- 同一条聚合查询生成总数和分组，避免组间读取时序不同；旧服务未返回分组不显示虚构数据。通过隔离库真实下单、派单、取消和指派验证计数及筛选，375px 验证跳转与小屏排版，不连接真实支付和消息。

# 2026-10-30 工作台快照更新

- 现状：工作台只在挂载和手动刷新时读取；长时间停留时待接单、售后、报单和订单状态可能过期。服务端为多个独立查询，并非事务一致快照，WebSocket 订单／报单事件分别受相应权限约束，账号变化未必产生这两类事件。
- 参考 [Pinterest 订单管理看板](https://in.pinterest.com/pin/order-management-dashboard-ui-design-by-abdullah-noman--559994534927771408/) 的订单概览与 [MockServer 看板的事件及可见性轮询](https://github.com/mock-server/mockserver-monorepo/blob/master/docs/code/dashboard-ui.md)；沿用现有信息层级与紧凑控件，不引入额外图表。
- 方案：页面可见时合并订单／报单变动，延迟 5 秒以减少聚合查询；连接恢复、重返页面时重新读取，可见状态每 60 秒补读以覆盖未发事件的指标。请求串行，期间变动保留后续更新；失败保留旧数据、显示现有重试提示。权限或会话变化丢弃旧请求和数据，仍具备工作台权限时重读。显示更新时间及快照说明，不承诺实时或跨查询一致。
- 隔离环境验收真实下单、指派或报单事件，查看页面数量与服务端一致；验证失败后重试、页面返回、权限和卸载边界。单进程内存 Channels 不能证明生产 Redis、并发数据库或所有推送覆盖。

# 2026-10-30 陪玩注册审核

- 当前审核详情可查看实名资料与凭证，但确认框缺少账号、等级、金额和退回理由；列表在手机上依赖横向滚动，且审核失败后自动重读可能把结果不明误认为可以重新操作。后端以申请 revision、状态和数据库事务校验审核；现金通过登记押金流水，单抵不登记现金，申请人补充资料会递增 revision。
- 参考 [Pinterest 账号生命周期看板](https://in.pinterest.com/pin/dashboard-user-lifecycle-ui-in-2023--572379433906240454/)、[开源申请审批队列与详情](https://github.com/Samukhele/Submission-Approval-Workflow-Open-Ownership-) 和 [Apple 提示文案](https://developer.apple.com/design/human-interface-guidelines/alerts)：小屏列表只列账号、昵称、状态、提交时间；实名与凭证留在详情。审核必须显式选择等级，确认摘要包括申请编号、账号、等级、押金方式与金额或退回理由，确认与提交锁住编辑。
- 读取／确认／提交都校验当前会话和申请；明确拒绝展示原因，冲突要求重新读取；网络或服务错误标为结果未确认，按账号和申请记录本标签待核对标记，先读取最新状态再人工确认解除，说明请求可能仍在处理。此机制不替代跨设备幂等；不在列表或确认框暴露身份证号码。
- 使用隔离数据库与真实注册、审核接口验证现金、单抵、退回与版本冲突，并用仅替换响应的故障注入验证未知结果；检查 375px、权限及无外部资金消息。
- 真实页面发现状态和搜索参数没有被接口应用：项目没有启用通用筛选后端。列表按已声明的状态和账号／手机号／姓名／昵称条件在服务端筛选，并以提交时间及 ID 降序稳定排列；非法状态明确拒绝。

# 2026-10-30 单抵押金规则

- 当前页面无条件宣称净收益按比例抵扣，但 `hold_provider_income` 在冻结时长为零时直接返回；新订单在创建时记录当前冻结时长，旧订单保留各自时长，非订单手工报单使用当前时长。抵扣比例在实际收益入账时读取，已生成冻结记录不重算。目标额是默认值，关闭规则时陪玩档案可使用独立目标。
- 参考 [Pinterest 设置页面](https://in.pinterest.com/pin/settings--368239707027009631/) 与 [Medusa 表单变更提示](https://github.com/medusajs/medusa/blob/develop/packages/admin/dashboard/src/i18n/translations/en.json)，保留紧凑双目标卡和比例输入，将例子标为有冻结时的计算示意，清楚显示当前新订单冻结状态及旧单差异；空输入不得显示为 0%。
- 保存前显示旧／新比例和适用范围；比例比对由新接口按读取值进行条件更新，冲突保留草稿并要求重读。旧客户端仍可无条件更新，新页面在旧服务缺少能力标记时禁止保存；读取与保存失败分别提示，结果不明必须重读。提示配置读取为快照，条件比对不覆盖其他配置或跨请求 ABA 等情况。
- 隔离 SQLite 使用真实权限、配置接口和收益冻结业务验证有／无冻结、比例变化、旧记录和竞争写入；375px 检查空值、0%／100%、确认和错误恢复，外部资金与消息隔离。

# 2026-10-30 陪玩等级与删除影响

- 现状：页面称旧等级仅用于历史服务，但实际未配置分范围档位的部分商品、REGULAR／FUN 抽成及无订单手工报单仍可用等级；注册审核也设置该等级。删除级别会通过 `SET_NULL` 清除陪玩档案、商品最低等级和注册审核关联；商品未设最低等级时历史资格分支不限档。当前确认框只显示名称，未说明影响；小屏表格横向拥挤，资格选项失败表现为空。
- 参考 [Pinterest 管理卡片](https://in.pinterest.com/pin/admin-panel-dashboard-card-design--567101778070790081/)及 [React Admin 删除确认](https://github.com/marmelab/react-admin/blob/master/docs/Buttons.md)：区分分范围档位和陪玩等级的实际用途；小屏卡片展示同一列表数据；删除先读取真实影响数量，显示数量快照和清空关联的后果，不能读取时不得确认，明确删除与取消。资格选项加载失败可重试；操作中防止重复和会话切换后提交。
- 删除结果不明确时按账号和等级保存标签页待核对记录，通过服务端读取等级是否仍存在后人工确认解除；读取不保证原请求已结束。后端新增仅删除授权可访问的影响查询，兼容原删除接口及行为；数量是快照，不提供服务端锁定或跨设备保障。
- 验收：隔离库真实创建等级、陪玩、商品与注册关联，核对影响、删除后字段和关联订单资格变化；检查只读权限、失败重试、响应丢失、375px 卡片和 1280px 表格，不触及真实客户或消息。

# 2026-10-30 优惠券领取记录口径

- 当前后台将未使用但模板停用／跨业务周期的券统称“已过期”，筛选仅检查模板日期，遗漏永久券、逐张有效期和周期／停用条件。真实下单使用 `expires_at` 优先、否则模板 `valid_to`，并要求模板启用及账号当前周期；未使用不代表满足某笔订单的套餐、金额或活动条件。
- 参考 [Pinterest 移动交易记录](https://in.pinterest.com/pin/transaction-details-mobile-app-ui--1032802127077576223/)和 [WooCommerce 优惠券校验](https://github.com/woocommerce/woocommerce/blob/trunk/plugins/woocommerce/includes/class-wc-coupon.php)：后台记录按已使用、实际过期、模板停用、非当前周期及未使用区分；同一读取时刻用于筛选和标签，逐张到期优先。移动端记录卡展示领取人、状态、时间和订单；不以“未使用”承诺可以用于任意商品。旧无账号关联记录继续按已有 `is_usable` 口径，不推断归属。
- 接口增加两种管理端状态及有效截止时间，旧 `EXPIRED` 筛选不再包含未过期的停用／跨周期券，前后端同步部署；未触及真实核销及定价规则。用隔离真实领券、下单和退款覆盖无期限、逐张过期、模板停用、周期变化和已使用，按后台状态筛选核对结果与真实可用券接口；检查 375px 记录抽屉和桌面表格。

