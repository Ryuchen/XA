# 陪玩工作台迁移记录

## 依据与范围

2026-11-04：以根目录 AGENTS.md 最新标准为准，在官方 shadcn CLI 创建的 React + TypeScript + Tailwind CSS 4 脚手架中重新实现。适配桌面和手机；旧静态原型中的“仅桌面”限定已失效。

真实行为依据 `apps/provider-vue/src/main.ts`、`views/`、`lib/session.ts`、`lib/fund.ts`、`lib/realtime.ts`、`lib/orderAlerts.ts`。视觉参考 `design-demos/shenglang-web-prototype/provider/`；其中所有 mock 数据、待评审低星规则与占位权限不进入真实业务实现。后端现有授权、动作和账务结果为准。

## 页面与功能清单

| 路由 | 旧页面 | 必须保留的功能与接口 | 当前状态 |
|---|---|---|---|
| `/login` | Login.vue | 账号密码、OTP 绑定/验证、二维码/密钥、备用恢复码、试音来源回跳；`users/account-login/`、`users/refresh/` | 已实现，待实测 |
| `/register` | Register.vue | 押金规则、现金/单抵选择、实名及证件/凭证上传、字段校验、审核查询、退回补交；`provider-registration/rules/`、根 POST、`status/`、`resubmit/` | 已实现，待实测 |
| `/orders` | Orders.vue | 六类订单、抢单池缓存/声音、历史分页/搜索、双陪队友和报名、确认/撤回、拒单、入队/结单截图、结果未确认重读、收益及退款/预约/组队进度；`orders/orders/` 及动作、`users/provider-stats/` | 已实现，待实测 |
| `/applications` | ApplicationHistory.vue | 报名/邀请列表、分页、状态与成员；`orders/applications/mine/` | 已实现，待实测 |
| `/applications/:orderId` | ApplicationDetail.vue | 报名详情、保留来源、组队确认/拒绝/撤回；订单 applications 的 `mine/` 和 `action/` | 已实现，待实测 |
| `/reservations` | Reservations.vue | 游标分页、关联详情、确认/拒绝/取消、原因、已转单跳转、按 allowed_actions 控制；`orders/reservations/` | 已实现，待实测 |
| `/reports` | Reports.vue | 报单列表/焦点/分页、可选订单搜索分页、创建、三类凭证、最多9张战绩图片和逐图结果、删除/提交、can_edit/can_submit/凭证限制、状态不确定后重读；`wallet/reports/`、`wallet/report-order-choices/` | 已实现，待实测 |
| `/wallet` | Wallet.vue | 余额/冻结/待结算、流水/奖惩/礼物/提现多分页、提现焦点、金额精度、完整试算、版本化收款账户、税额/实付复核、稳定 client_request_id、规则/账户变化和未知结果恢复；`wallet/withdraw/`、`transactions/`、`provider-benefits/`、`withdraw-accounts/` | 已实现，待实测 |
| `/withdraw-accounts` | WithdrawalAccounts.vue | 微信/支付宝/银行卡独立绑定、修改/解绑、expected_id/expected_version、冲突重载与保留草稿 | 已实现，待实测 |
| `/pass` | Pass.vue | 资格/有效期、档位价格、历史记录、按 expected_daily_price 幂等购买；`users/provider-pass/` | 已实现，待实测 |
| `/points` | Points.vue | 积分余额和游标流水、KOOK绑定码、积分商品和条款快照、兑换请求恢复、权益焦点/分页及版本化交付资料/TAG修改；`club/points/`、`kook-binding/`、`rewards/`、`redeem/`、`claims/` | 已实现，待实测 |
| `/messages` | Messages.vue | 通知游标/已读/全部已读、详情、按关联对象跳转、客服未读；`messages/`、`read-all/`、`chat/unread/` | 已实现，待实测 |
| `/support` | Support.vue | 真实客服卡、企微条件入口、复制与充值须知、来源返回；`support/contact-card/` | 已实现，待实测 |
| `/support-chat` | SupportChat.vue | 历史加载、已读批次、文本/图片、订单关联、稳定 client_message_id、未知结果查询、草稿隔离/冲突；`chat/session/`、`send/`、`read/`、`message-status/` | 已实现，待实测 |
| `/mine` | Mine.vue | 身份、服务/评价概况、接单状态、押金/单抵规则及幂等补押金、关联入口和退出 | 已实现，待实测 |
| `/profile` | Profile.vue | 基本资料、头像/视频/语音卡上传/移除、档期保存、通知偏好；`users/escorts/me/`、`schedule/` | 已实现，待实测 |
| `/skills` | Skills.vue | 真实技能目录、游戏/平台/难度筛选、可选资格、已选/失效项目、保存/清空/未保存提示；`users/escorts/skill-options/`、`me/` | 已实现，待实测 |
| `/evaluations` | Evaluations.vue | 分页/待回复、详情、本人/客服/来源未知回复、reply_version 并发保护、未知结果核对、草稿保留；`orders/evaluations/mine/`、`{id}/reply/` | 已实现，待实测 |
| `/audition`（兼容 `/pages/audition/index`） | Audition.vue | 邀请 token 公开验证、登录回跳、真实报名/记录/失效反馈；`audition` 邀请、`signup`、`my-signups` | 已实现，待实测 |

## 共享约束

- 会话按角色隔离；刷新串行化、迟到刷新不得覆盖新会话，退出后关闭连接。
- 资金提交保存 request id 并恢复 FUND_ALREADY_PROCESSED；网络失败/409/429 不将“未知”误报成功或新建重复请求。
- 实时事件仅触发合并重读，HTTP 数据为准；重连/重新可见刷新，列表翻页不丢已读范围。
- 授权私有图片需要按 ticket 续签；不能绕过后端授权直接展示受限媒体。
- 所有页面必须有加载、错误/重试、空态和明确操作结果；金额以服务器整数账务单位换算，不改变现有账务精度。

## 核验

19 个页面及兼容路由已在新的 React 源码中实现，均连接真实接口，不导入原型 mock 数据。TypeScript 类型检查和 Vite 构建已通过。浏览器与真实业务接口验收由主代理协调进行；尚未据此宣称完整业务验收通过。

自审核对并修正：聊天浏览旧页后仍读取最新消息并桥接已读区间；附件和通知更新不覆盖未保存的个人资料/档期；私有图片续签识别可选末尾斜线、拒绝迟到续签并支持重试；积分和 TAG 使用后端真实事件名；提现配置变化要求重新试算，收款账户变化要求重新选择，金额/试算沿用整数与边界校验；分页数据缩减后自动回到有效页；关键履约、凭证、预约和资金动作不使用读取失败的旧快照继续提交。

当前运行约定：`src/App.tsx` 自带 HashRouter、登录保护、桌面左导航与手机底栏；共享认证/资金/实时协议位于 `@xa/shared`。预览服务与浏览器截图由主代理统一管理，避免多代理操作同一浏览器。

2026-11-04 已建立独立真实 MySQL 验收库 `xa_test_fronted_acceptance_20261104`、独占 Redis 26379、当前源码 API 18105；完整真实迁移通过。专用陪玩 JWT 的 27 个 HTTP 读取接口均返回成功，核对资料/档期/技能/统计/通行证、抢单池及历史、报名/预约/评价、报单、钱包及收款账户、站内通知/聊天、积分和权益返回结构；空列表不作为动作流程验收。仅微信二维码/授权读取/短信发送四项外部传输使用明确测试替身，核心认证/OTP/权限/账务运行真实代码。凭据只在忽略目录 `.tools/fronted-acceptance/`，原有账户和 OTP 未修改。浏览器响应式与写操作验收仍由主代理继续执行。

账号登录、OTP challenge 与恢复码确认另加 revision、请求序号、卸载失效保护，跨标签页退出/切换后，迟到的公开登录响应不会覆盖新会话。

独立队友身份另已通过真实 HTTP 密码登录、OTP challenge、加密存储密钥对应的 TOTP 验证、实际签发 JWT 读取本人资料；该认证验证未替换任何外部或核心函数，未消费主陪玩/管理员 OTP。

主代理真实浏览器已创建单陪订单并完成陪玩报名。根据该反馈修正抢单卡：报单不可用原因只在本人有实际分配/参与关联的履约或历史订单中显示，待接单池和仅报名状态不展示无关“订单不存在”文案。专用商品平台/难度及两人资格已由原业务函数校验；新增可领取优惠券、会员等级、兑换商品、内容和抽奖目录另经7个真实读取接口核对，均未预造订单、资金或权益结果。

独立队友 API 写验收已通过25项核对：版本化收款绑定/更新/旧版本拒绝/恢复；服务端提现试算→真实扣款冻结→原ID回执恢复→改金额复用ID拒绝→管理员只领取并驳回本轮申请→余额和冻结精确还原、流水FAILED；已缴清押金拒绝且不扣款；BLACK按服务器原价续1天及原ID重试不重扣；虚拟交付真实扣积分兑换、原ID恢复同一权益、资料 expected_details 冲突保护与相同资料重试；本人基本资料/有效技能/四段制档期保存与恢复。资金和兑换请求、原ID及快照保存在忽略目录私有 journal，脱敏证据为 `.tools/fronted-acceptance/partner-workflow.json`。仅使用 provider_partner，未消费OTP，管理员未操作其他记录，未接触主老板/主陪玩钱包及订单1/报单1。主陪玩仅按主代理授权纠正初始夹具的礼物技能关联。

该写验收运行原核心模型/事务/HTTP，没有 mock 资金或认证。最终队友余额95000账务、积分990，源于真实通行证续费和兑换；押金正向缴纳因夹具本已缴清未覆盖，外部真实支付/消息及浏览器响应式仍须各自验收。

最后源码遗漏复核又修正四处明确问题：查外挂凭证属于真实私有字段，资料页现复用带过期续签/重试/原图链接的 `PrivateImage`；通行证有效期内其他档位禁用并显示“到期后可选”，与旧端及后端仅同档续费一致；提现、兑换和创建报单的迟到响应须核对页面存活与路由快照，离页后不再把用户导航回旧页面，资金原编号和持久兑换记录保留原处理方式；试音邀请交换补齐旧端离页失效及并发加载序号，迟到交换不会写回会话。修改后最新 TypeScript 检查与构建通过，无新写验收或浏览器操作。

相对 `/media`、`/api` 图片/原图地址依现有同源代理解析，报单与聊天凭证原图链接随续签 URL 更新。报单接口没有转单人员列表字段，继续使用服务端按本人分配计算的收益/退款信息；订单的实际成员与组队进度使用现有授权字段，不根据订单当前人员推算历史归属。手机底栏已有安全区，状态、资金结果及错误仍在页面内显示；主代理已确认390×844订单页无横向溢出，未据此宣称其余页面全部浏览器验收完成。
