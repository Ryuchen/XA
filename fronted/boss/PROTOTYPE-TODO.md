# 老板端原型接入待办

更新：2026-11-05。以原型逐页代码为视觉依据，真实业务功能优先。未接入的控件保留原型位置并明确“待开放”，不填示例值，不调用不存在的接口，不以假成功代替业务结果。原型Scenario切换器、示例账号、预览器与演示目录不作为业务功能迁移。

## 待接入

| ID / 状态 | 原型位置 | 缺口证据 | 当前UI | 接入事项与验收 |
| --- | --- | --- | --- | --- |
| B-01 待开放 | `CompanionDetailPage.tsx`“接单信息”右格“擅长位置” | `services/backend/users/views.py`的陪玩公开序列化提供rank、city、game_category_names、qualifications、display_tags；无按游戏定义的结构化常用位置。rank是段位，资格是接单资格，后台权益TAG也不能直接冒充射手/辅助等位置。陪玩端同缺口已记P-04 | `commerce.tsx/CompanionPage`的`boss-companion-work-amount`右格保留，`PendingFeature label="擅长位置"`显示待开放，可点击/聚焦说明；真实游戏仍在身份卡，真实资格Check照常显示 | 与陪玩端复用同一真实字段与字典，明确按游戏的位置、多选与维护权限；读取当前门店允许公开资料。未提供字段前不展示假位置，不把游戏/段位/权益TAG转换成位置；接入后无值显示未填写，不影响已有挑选、报价与预约。 |

## 原型只演示反馈、实际已经具备的功能

以下继续使用现有能力，不标“待开放”，也不照抄原型“后续页面尚未设计”的说明。

| 原型入口 | 真实复用位置 |
| --- | --- |
| 首页“全部订单”、每日签到、领券、榜单、礼物、玩法 | `account.tsx/HomePage`→真实orders/checkin/coupons/rankings/gifts/catalog路由；海报/公告取当前门店真实发布内容 |
| 陪玩详情“预约TA”“送礼物”“选择TA” | `commerce.tsx`保留service_id、selected_provider_ids、providerLimit、coupon上下文；进入现有ReservationCreate/Gifts/Checkout或Picker |
| 平台派单/指定陪玩与多人服务 | Checkout现有dispatch/客服/资格/人数与原报价规则；不能因原型只弹反馈而关掉真实入口 |
| 服务附加项目、需要客服确认的方案 | `ServicePage`已有is_addon变体与咨询路径；不展示固定20浪花，不新增不经核价的直接购买 |
| 赠礼结果查询/重试原请求/权益记录 | `GiftsPage`调用真实receipt与submitIntent，`Pending`保存原凭证并受blocked/busy限制；权益走现有`/points?tab=claims` |
| 订单复制编号、取消、挑选、评价、售后与客服 | `OrderPage`继续使用allowed_actions、付款未知锁、原确认后果与真实API；剪贴板失败显示手动复制下一步 |
| 客服充值与钱包流水 | Wallet/Support现有真实客服通道，到账按接口余额/流水；新增充值确认与说明只是原型交互迁移 |
| 聊天核对/重试/放弃 | Chat原消息幂等、lookup、草稿、历史/已读/私图续签与放弃后果确认保留 |
| 老板权益、规范、公告与积分规则 | `ContentPage`按真实kind合并目录/详情，`AnnouncementsPage`独立读发布公告；Mine补齐入口，不新造三篇固定文章 |
| 登录隐私协议与手机号授权/SMS | 真正授权同意、`wx.openPrivacyContract`、微信手机号读取和SMS在现有最小微信桥接页；Web只真实扫码/核对，不假造授权，也不以原型示例合同替代正式合同 |
| 原型SupportDialog、消息/钱包/赠礼/余额说明 | 登录帮助复用真实SupportPage；说明类UI用`Explanation`真实文案迁移，无新业务接口依赖，无需标待开放 |

## 与原型Scenario的真实业务差异

- guest：受保护页面沿用既有`Private`→微信登录并携带next；Home/公开内容/公开客服按当前权限读取，不为演示guest卡改变认证流程。
- 试音邀请：真实token/角色/本人关联核对继续生效；原型“换入另一个演示账号”不适用于现微信身份规则，不伪造身份交换。
- 企业微信opening/open-error：原生HTTPS链接没有可靠回执时不能定时宣布已打开或打开失败；保留配置不可用提示和实际平台会话备用入口。
- 404/失效内容：真实接口错误与重试/返回目录，不伪造演示文章；空正文/海报显示准备中。
- 月金额、价格、订单进度、余额、权益、优惠券、限量、期限、日限额及支付未知结果都由真实接口决定，不接原型场景假值。

## 检查边界

`VISUAL-MAPPING.md`列出42个原型Page/Dialog、40组363个Scenario键与真实谓词。静态键完整性检查不能代替视觉保真检查。本轮已增加入口、首页、陪玩目录、套餐、订单详情、个人中心与钱包的实际两店桌面/手机截图和状态检查，范围见 `../VISUAL-UPDATE.md`；真实支付、微信/SMS及全部363状态仍不能从代码映射、tsc或只读截图推定通过。

## 本轮已接入的门店目录（原B-02）

顶部下拉使用真实 `/api/stores/available/`，只显示启用、已初始化且业务库已配置的公开门店信息；声浪电竞为浪花，兴安电竞为星辉。当前项禁用并勾选，另一门店确认后整页切换至其登录页，不携带旧店业务路由、身份令牌或草稿。目录不请求平台管理权限接口，保留真实加载及失败重试状态。
