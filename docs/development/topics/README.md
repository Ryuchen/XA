# 研发领域专题索引

版本：2026-11-17；状态：**实施记录索引**。本目录收纳 development 未编号专题文档，按业务领域分组；编号主文档（01–27）仍在 [development/](../README.md)。专题记录具体场景的实现、联调与边界结论，业务规则以 [PRD](../../prd/README.md) 与用户最新决定为准，进度口径以 [16-开发进度](../16-开发进度.md)、[18-后端剩余任务盘点](../18-后端剩余任务盘点.md)、[19-产品决策实现状态](../19-产品决策实现状态.md) 为准。微信小程序相关专题已按用户 2026-11-17 决定删除。

## 领域目录

| 领域 | 范围 | 文档数 |
| --- | --- | --- |
| [account-auth](account-auth/) | 账号认证与生命周期：登录/OTP/短信、微信绑定与头像、退店、注销、私有文件周期 | 7 |
| [config-init](config-init/) | 配置与当前读：系统字典、配置锁后核对、通行证/提现/积分抵扣配置读取 | 12 |
| [deposit](deposit/) | 押金：押金缴纳、退押金、收益解冻与补差核对 | 7 |
| [fulfillment](fulfillment/) | 履约与报单：服务段、报单审核、试音、陪玩资格与游戏资料 | 14 |
| [grab](grab/) | 抢单与派单：抢单池、单陪/双陪抢单、补差、留池、群派单与开池竞争 | 28 |
| [kook](kook/) | KOOK 接入：在线区间账本、语音积分规则、签到配置、授权核对与人工边界 | 11 |
| [license](license/) | License 与授权：控制库故障边界、旧码重发、配额并发、离线缓存与授权规则变更 | 10 |
| [membership](membership/) | 会员权益：VIP 等级与券、礼物/赠礼、成就、签到、领券中心与生日权益 | 30 |
| [misc](misc/) | 未归类专题（基线记录与验收基线） | 2 |
| [notification](notification/) | 通知与咨询：各类通知恢复/通知周期、站内消息、咨询转交与客服服务 | 54 |
| [order-combos](order-combos/) | 订单组合场景：普通/积分 × 退款 × 转单 × 加购组合、安全箱与特殊玩法加购 | 43 |
| payment | 支付与充值：充值收款恢复、赠送审批、预存退店周期（微信直付小程序方案草案已删除） | 3 |
| [payroll](payroll/) | 薪资与经营：工资核算、退款扣回、绩效、经营 ROI、续单奖励与离职结清 | 22 |
| [platform](platform/) | 平台控制面：平台授权/汇总、提现终审、公共薪资、门店域名与逐店任务 | 17 |
| [points](points/) | 积分：抵扣与冲正、转单加购、商城/抽奖授权、消费积分与补资 | 38 |
| [refund-aftersale](refund-aftersale/) | 退款与售后：协商退款、低星评价售后、结案与授权核对 | 9 |
| [reservation](reservation/) | 预约与双陪：预约确认/换人/到期、双陪席位与邀请、档期竞争 | 26 |
| [store-init](store-init/) | 建店与初始化：开业前逐项核对、新店初始化、开店配置与事务审计 | 29 |

## 全部专题

### account-auth

- [OTP绑定账号周期保护](account-auth/OTP绑定账号周期保护.md)
- [initial-admin-otp-bootstrap-20261113](account-auth/initial-admin-otp-bootstrap-20261113.md)
- [微信登录令牌认证来源](account-auth/微信登录令牌认证来源.md)
- [短信登录令牌手机号绑定](account-auth/短信登录令牌手机号绑定.md)
- [私有文件账号周期](account-auth/私有文件账号周期.md)
- [老板业务账号游戏资料](account-auth/老板业务账号游戏资料.md)
- [老板退店补签卡清空](account-auth/老板退店补签卡清空.md)

### config-init

- [CATALOG_PROVIDER_LINKAGE_20260924](config-init/CATALOG_PROVIDER_LINKAGE_20260924.md)
- [CLUB_CATALOG_TEST_DATA](config-init/CLUB_CATALOG_TEST_DATA.md)
- [CLUB_IMPORT_20261003](config-init/CLUB_IMPORT_20261003.md)
- [GAMEPLAY_CATALOG](config-init/GAMEPLAY_CATALOG.md)
- [停售通行证的无卡延迟](config-init/停售通行证的无卡延迟.md)
- [实销积分会员开关当前配置](config-init/实销积分会员开关当前配置.md)
- [推荐佣金配置草稿](config-init/推荐佣金配置草稿.md)
- [提现实际打款时间](config-init/提现实际打款时间.md)
- [提现收款方式业务周期](config-init/提现收款方式业务周期.md)
- [提现未付款解冻逐笔账务](config-init/提现未付款解冻逐笔账务.md)
- [提现配置锁后当前读](config-init/提现配置锁后当前读.md)
- [通行证积分付款配置当前读](config-init/通行证积分付款配置当前读.md)

### deposit

- [已确认预约退押金等待期](deposit/已确认预约退押金等待期.md)
- [退押金全退加购零收益结清](deposit/退押金全退加购零收益结清.md)
- [退押金加购退款责任时间](deposit/退押金加购退款责任时间.md)
- [退押金收益解冻时点](deposit/退押金收益解冻时点.md)
- [退款押金补差抢单池](deposit/退款押金补差抢单池.md)
- [退款押金补差收益释放-20261113](deposit/退款押金补差收益释放-20261113.md)
- [陪玩退押金申请](deposit/陪玩退押金申请.md)

### fulfillment

- [房间异议恢复结单核验](fulfillment/房间异议恢复结单核验.md)
- [房间自动结单周期待核对](fulfillment/房间自动结单周期待核对.md)
- [房间转单结单工段](fulfillment/房间转单结单工段.md)
- [服务中转入抽成锁定快照](fulfillment/服务中转入抽成锁定快照.md)
- [服务前改派游戏抽成当前读](fulfillment/服务前改派游戏抽成当前读.md)
- [服务履约人数与计费人数](fulfillment/服务履约人数与计费人数.md)
- [游戏资料规则下单当前读](fulfillment/游戏资料规则下单当前读.md)
- [订单试音录音库周期保护](fulfillment/订单试音录音库周期保护.md)
- [试音报名直接提交录音](fulfillment/试音报名直接提交录音.md)
- [试音邀请账号认证](fulfillment/试音邀请账号认证.md)
- [陪玩头像来源与微信周期](fulfillment/陪玩头像来源与微信周期.md)
- [陪玩展示名称接口契约](fulfillment/陪玩展示名称接口契约.md)
- [陪玩注销后端准备](fulfillment/陪玩注销后端准备.md)
- [陪玩退店后新单准入](fulfillment/陪玩退店后新单准入.md)

### grab

- [GROUP_DISPATCH](grab/GROUP_DISPATCH.md)
- [免费安全箱三角洲单陪抢单](grab/免费安全箱三角洲单陪抢单.md)
- [免费安全箱单陪抢单取消退券](grab/免费安全箱单陪抢单取消退券.md)
- [单陪抢单与开池竞争](grab/单陪抢单与开池竞争.md)
- [双陪抢单退出组队恢复](grab/双陪抢单退出组队恢复.md)
- [双陪抢单逾期占位释放](grab/双陪抢单逾期占位释放.md)
- [双陪抢单邀请分页](grab/双陪抢单邀请分页.md)
- [双陪抢单邀请失败恢复](grab/双陪抢单邀请失败恢复.md)
- [抢单人工恢复真实轮次](grab/抢单人工恢复真实轮次.md)
- [抢单仲裁候选当前读](grab/抢单仲裁候选当前读.md)
- [抢单开池恢复扫描](grab/抢单开池恢复扫描.md)
- [抢单开池收件逐条恢复](grab/抢单开池收件逐条恢复.md)
- [抢单核对前阶段记录](grab/抢单核对前阶段记录.md)
- [抢单激活收件恢复](grab/抢单激活收件恢复.md)
- [抢单群发真实截止](grab/抢单群发真实截止.md)
- [抢单逐店恢复入队隔离](grab/抢单逐店恢复入队隔离.md)
- [抢单重开群发恢复](grab/抢单重开群发恢复.md)
- [抢单首轮封存后的老板取消](grab/抢单首轮封存后的老板取消.md)
- [普通零实付订单抢单开池](grab/普通零实付订单抢单开池.md)
- [正式抢单池截止可见性](grab/正式抢单池截止可见性.md)
- [留池已失格候选处理顺序](grab/留池已失格候选处理顺序.md)
- [群派单异常与订单事务](grab/群派单异常与订单事务.md)
- [老板取消与抢单开池竞争](grab/老板取消与抢单开池竞争.md)
- [老板客服安排积分抢单](grab/老板客服安排积分抢单.md)
- [老板选人补差资格](grab/老板选人补差资格.md)
- [补差抢单分配并发](grab/补差抢单分配并发.md)
- [补差抢单最终资格当前读](grab/补差抢单最终资格当前读.md)
- [首轮封存后留池收件](grab/首轮封存后留池收件.md)

### kook

- [KOOK人工积分发放边界](kook/KOOK人工积分发放边界.md)
- [KOOK在线区间账本](kook/KOOK在线区间账本.md)
- [KOOK在线积分授权发放核对](kook/KOOK在线积分授权发放核对.md)
- [KOOK授权待决在线队列](kook/KOOK授权待决在线队列.md)
- [KOOK授权未知人工核对](kook/KOOK授权未知人工核对.md)
- [KOOK积分初始化配置](kook/KOOK积分初始化配置.md)
- [KOOK签到积分配置](kook/KOOK签到积分配置.md)
- [KOOK语音积分规则计算](kook/KOOK语音积分规则计算.md)
- [KOOK语音积分规则配置待接入](kook/KOOK语音积分规则配置待接入.md)
- [开业前KOOK配置](kook/开业前KOOK配置.md)
- [老板KOOK退店周期](kook/老板KOOK退店周期.md)

### license

- [License控制库故障边界](license/License控制库故障边界.md)
- [License故障微信登录连续性](license/License故障微信登录连续性.md)
- [License旧码重发校验](license/License旧码重发校验.md)
- [License账号名额并发计数](license/License账号名额并发计数.md)
- [SaaS内容运营授权](license/SaaS内容运营授权.md)
- [实销确认授权兜底](license/实销确认授权兜底.md)
- [授权规则变更与离线缓存](license/授权规则变更与离线缓存.md)
- [控制库故障动态门店任务恢复](license/控制库故障动态门店任务恢复.md)
- [未来授权取消与离线缓存](license/未来授权取消与离线缓存.md)
- [老板重入授权配额并发](license/老板重入授权配额并发.md)

### membership

- [CLUB_ENGAGEMENT_IMPLEMENTATION](membership/CLUB_ENGAGEMENT_IMPLEMENTATION.md)
- [QUALIFICATION_COUPON_FIX_20261006](membership/QUALIFICATION_COUPON_FIX_20261006.md)
- [VIP_EXPERIENCE_AUTOMATION_20261006](membership/VIP_EXPERIENCE_AUTOMATION_20261006.md)
- [VIP_RULES_REVIEW_20261006](membership/VIP_RULES_REVIEW_20261006.md)
- [VIP停用已发券保护](membership/VIP停用已发券保护.md)
- [VIP停用期间等级停用](membership/VIP停用期间等级停用.md)
- [VIP券付款条款固化](membership/VIP券付款条款固化.md)
- [VIP月券首版配置与发放](membership/VIP月券首版配置与发放.md)
- [VIP等级与门槛实时重算](membership/VIP等级与门槛实时重算.md)
- [vip-tier-recalculation-20261113](membership/vip-tier-recalculation-20261113.md)
- [会员加购实销联调范围](membership/会员加购实销联调范围.md)
- [会员升级发券账号周期](membership/会员升级发券账号周期.md)
- [会员升级安全箱券条款](membership/会员升级安全箱券条款.md)
- [会员授权后台重试凭证](membership/会员授权后台重试凭证.md)
- [会员授权重算任务周期隔离](membership/会员授权重算任务周期隔离.md)
- [会员等级规则变更与券列表](membership/会员等级规则变更与券列表.md)
- [已有优惠券与周期发券隔离](membership/已有优惠券与周期发券隔离.md)
- [成就实销授权与已获权益](membership/成就实销授权与已获权益.md)
- [成就授权未知平台核对](membership/成就授权未知平台核对.md)
- [成就授权未知快照异常](membership/成就授权未知快照异常.md)
- [折扣券金额字段](membership/折扣券金额字段.md)
- [生日播报退店周期](membership/生日播报退店周期.md)
- [礼物不计推荐佣金](membership/礼物不计推荐佣金.md)
- [礼物付款会员授权未知核对](membership/礼物付款会员授权未知核对.md)
- [礼物会员授权审核重算重试](membership/礼物会员授权审核重算重试.md)
- [礼物退款会员授权来源核对](membership/礼物退款会员授权来源核对.md)
- [签到配置授权边界](membership/签到配置授权边界.md)
- [赠礼累计权益](membership/赠礼累计权益.md)
- [领券中心可领取状态](membership/领券中心可领取状态.md)
- [领券授权锁后核验](membership/领券授权锁后核验.md)

### misc

- [CORE_BUSINESS_WORKING_BASELINE_20261027](misc/CORE_BUSINESS_WORKING_BASELINE_20261027.md)
- [订单派单三端状态与原型验收基线](misc/订单派单三端状态与原型验收基线.md)

### notification

- [低星评价售后通知恢复](notification/低星评价售后通知恢复.md)
- [加购付款站内通知](notification/加购付款站内通知.md)
- [加购完成确认站内通知](notification/加购完成确认站内通知.md)
- [加购接替方案站内通知](notification/加购接替方案站内通知.md)
- [加购接替结果通知恢复](notification/加购接替结果通知恢复.md)
- [加购退款方案站内通知](notification/加购退款方案站内通知.md)
- [协商方案通知周期恢复](notification/协商方案通知周期恢复.md)
- [单陪抢单结果通知恢复](notification/单陪抢单结果通知恢复.md)
- [双陪抢单结果通知恢复](notification/双陪抢单结果通知恢复.md)
- [双陪抢单结果通知校验](notification/双陪抢单结果通知校验.md)
- [双陪抢单邀请通知恢复](notification/双陪抢单邀请通知恢复.md)
- [双陪预约双方确认通知恢复](notification/双陪预约双方确认通知恢复.md)
- [双陪预约席位超时通知恢复](notification/双陪预约席位超时通知恢复.md)
- [双陪预约拒绝通知恢复](notification/双陪预约拒绝通知恢复.md)
- [双陪预约重选通知恢复](notification/双陪预约重选通知恢复.md)
- [咨询关联协商售后](notification/咨询关联协商售后.md)
- [咨询写入账号周期](notification/咨询写入账号周期.md)
- [咨询刷新通知周期](notification/咨询刷新通知周期.md)
- [咨询已读游标归属](notification/咨询已读游标归属.md)
- [咨询消息通知恢复-20261113](notification/咨询消息通知恢复-20261113.md)
- [咨询订单卡片状态](notification/咨询订单卡片状态.md)
- [咨询转交站内提醒](notification/咨询转交站内提醒.md)
- [咨询转交通知恢复-20261113](notification/咨询转交通知恢复-20261113.md)
- [售后登记处理中通知恢复](notification/售后登记处理中通知恢复.md)
- [售后结案通知恢复](notification/售后结案通知恢复.md)
- [客服开始服务通知周期恢复](notification/客服开始服务通知周期恢复.md)
- [房间异议通知恢复](notification/房间异议通知恢复.md)
- [房间确认结单通知恢复](notification/房间确认结单通知恢复.md)
- [房间结单审核通知恢复](notification/房间结单审核通知恢复.md)
- [抢单留池失格通知恢复](notification/抢单留池失格通知恢复.md)
- [抢单超时取消通知恢复](notification/抢单超时取消通知恢复.md)
- [报单审核通过通知恢复](notification/报单审核通过通知恢复.md)
- [押金事件站内通知](notification/押金事件站内通知.md)
- [普通服务评价通知恢复](notification/普通服务评价通知恢复.md)
- [普通订单完成通知恢复](notification/普通订单完成通知恢复.md)
- [服务中转单确认通知恢复](notification/服务中转单确认通知恢复.md)
- [服务前改派通知恢复](notification/服务前改派通知恢复.md)
- [特殊玩法加购退款通知恢复](notification/特殊玩法加购退款通知恢复.md)
- [积分多轮接替资格与通知](notification/积分多轮接替资格与通知.md)
- [积分订单售后退款通知恢复](notification/积分订单售后退款通知恢复.md)
- [站内消息周期展示及已读并发](notification/站内消息周期展示及已读并发.md)
- [老板主动取消退款通知恢复](notification/老板主动取消退款通知恢复.md)
- [老板指定订单通知](notification/老板指定订单通知.md)
- [订单余额退款通知恢复](notification/订单余额退款通知恢复.md)
- [订单实时通知周期-20261113](notification/订单实时通知周期-20261113.md)
- [订单客服名片与负责人](notification/订单客服名片与负责人.md)
- [订单报单驳回通知周期-20261113](notification/订单报单驳回通知周期-20261113.md)
- [订单招募试音通知恢复](notification/订单招募试音通知恢复.md)
- [订单试音报名取消通知恢复](notification/订单试音报名取消通知恢复.md)
- [订单通知待核对后台接口](notification/订单通知待核对后台接口.md)
- [试音审核通知周期](notification/试音审核通知周期.md)
- [赠礼到账通知周期恢复](notification/赠礼到账通知周期恢复.md)
- [门店路由订单通知恢复](notification/门店路由订单通知恢复.md)
- [零退款协商结案通知恢复](notification/零退款协商结案通知恢复.md)

### order-combos

- [BUSINESS_CLOSURE](order-combos/BUSINESS_CLOSURE.md)
- [ORDER_TRAFFIC_INTEGRATION_20261006](order-combos/ORDER_TRAFFIC_INTEGRATION_20261006.md)
- [免费安全箱转单完成后售后](order-combos/免费安全箱转单完成后售后.md)
- [加购会员审核重算与退款核验](order-combos/加购会员审核重算与退款核验.md)
- [加购完成确认账号周期](order-combos/加购完成确认账号周期.md)
- [加购实销后退款授权核对](order-combos/加购实销后退款授权核对.md)
- [加购报价申请状态](order-combos/加购报价申请状态.md)
- [加购授权跨周期零额结案](order-combos/加购授权跨周期零额结案.md)
- [加购未知授权全部拒绝结案](order-combos/加购未知授权全部拒绝结案.md)
- [同单主单与加购授权批量重算](order-combos/同单主单与加购授权批量重算.md)
- [同单加购授权批量重算](order-combos/同单加购授权批量重算.md)
- [安全箱三角洲本人抽成补贴](order-combos/安全箱三角洲本人抽成补贴.md)
- [安全箱已分配未开工取消](order-combos/安全箱已分配未开工取消.md)
- [普通下单默认优惠计算](order-combos/普通下单默认优惠计算.md)
- [普通主单退款后加购退款再接替-20261114](order-combos/普通主单退款后加购退款再接替-20261114.md)
- [普通主单退款后多加购进度](order-combos/普通主单退款后多加购进度.md)
- [普通加购追加协商结算时钟-20261114](order-combos/普通加购追加协商结算时钟-20261114.md)
- [普通多付款售后绑定-20261114](order-combos/普通多付款售后绑定-20261114.md)
- [普通混合付款二次协商补资与到账历史](order-combos/普通混合付款二次协商补资与到账历史.md)
- [普通混合付款全退后的结算重试](order-combos/普通混合付款全退后的结算重试.md)
- [普通混合付款协商退款后审核触发结算](order-combos/普通混合付款协商退款后审核触发结算.md)
- [普通转单前加购资金事件回放-20261114](order-combos/普通转单前加购资金事件回放-20261114.md)
- [普通转单同付款退款接替并存-20261114](order-combos/普通转单同付款退款接替并存-20261114.md)
- [普通转单多轮协商加购完成确认](order-combos/普通转单多轮协商加购完成确认.md)
- [普通转单并存独立加购售后-20261114](order-combos/普通转单并存独立加购售后-20261114.md)
- [普通转单待接替加购售后入口-20261114](order-combos/普通转单待接替加购售后入口-20261114.md)
- [普通转单接替确认到账作用域-20261114](order-combos/普通转单接替确认到账作用域-20261114.md)
- [普通转单跨付款保留段退款-20261114](order-combos/普通转单跨付款保留段退款-20261114.md)
- [普通退款会员券条款固化](order-combos/普通退款会员券条款固化.md)
- [普通退款后补资完成恢复结算](order-combos/普通退款后补资完成恢复结算.md)
- [普通退款转单后加购接替-20261114](order-combos/普通退款转单后加购接替-20261114.md)
- [普通逐段结算推荐佣金本金](order-combos/普通逐段结算推荐佣金本金.md)
- [未付款加购意向主单授权](order-combos/未付款加购意向主单授权.md)
- [第三轮后加购到账回放](order-combos/第三轮后加购到账回放.md)
- [第三轮后加购接替资金开发记录](order-combos/第三轮后加购接替资金开发记录.md)
- [第三轮后转单与加购待交开发记录](order-combos/第三轮后转单与加购待交开发记录.md)
- [第三轮退款加购权益核对](order-combos/第三轮退款加购权益核对.md)
- [转单追加售后履约段接口](order-combos/转单追加售后履约段接口.md)
- [转单退款后会员授权核对](order-combos/转单退款后会员授权核对.md)
- [默认本金加购主单授权故障](order-combos/默认本金加购主单授权故障.md)
- [默认本金转单加购授权核对](order-combos/默认本金转单加购授权核对.md)
- [默认本金转单授权核对](order-combos/默认本金转单授权核对.md)
- [默认本金退款后授权核对](order-combos/默认本金退款后授权核对.md)

### payment

- [充值收款请求结果恢复](payment/充值收款请求结果恢复.md)
- [充值赠送审批来源与待办](payment/充值赠送审批来源与待办.md)
- [预存充值退店周期](payment/预存充值退店周期.md)

### payroll

- [同月重入多版本绩效-20261113](payroll/同月重入多版本绩效-20261113.md)
- [员工今日派单原价口径](payroll/员工今日派单原价口径.md)
- [工资加购付款归期](payroll/工资加购付款归期.md)
- [工资普通退款流水直接关联](payroll/工资普通退款流水直接关联.md)
- [工资派单来源金额](payroll/工资派单来源金额.md)
- [工资确认退款核算开关一致性](payroll/工资确认退款核算开关一致性.md)
- [工资跨月付款退款来源查询](payroll/工资跨月付款退款来源查询.md)
- [工资迟核加购补发月份](payroll/工资迟核加购补发月份.md)
- [工资退款扣回顺序](payroll/工资退款扣回顺序.md)
- [工资退款真实流水核对](payroll/工资退款真实流水核对.md)
- [工资退款确认与流水屏障](payroll/工资退款确认与流水屏障.md)
- [工资退款绩效一次性处理](payroll/工资退款绩效一次性处理.md)
- [平台公共薪资与总账接口](payroll/平台公共薪资与总账接口.md)
- [离职结清与重新入职查询](payroll/离职结清与重新入职查询.md)
- [离职结清后退款绩效免追-20261113](payroll/离职结清后退款绩效免追-20261113.md)
- [积分自愿接替经营ROI](payroll/积分自愿接替经营ROI.md)
- [签到工资与退押金联调记录](payroll/签到工资与退押金联调记录.md)
- [经营ROI真实事件口径](payroll/经营ROI真实事件口径.md)
- [经营ROI退款流水期间统一](payroll/经营ROI退款流水期间统一.md)
- [续单奖励经营成本](payroll/续单奖励经营成本.md)
- [续单核实与奖励](payroll/续单核实与奖励.md)
- [重入后售后考试迟核工资-20261113](payroll/重入后售后考试迟核工资-20261113.md)

### platform

- [平台共享到账对账余额](platform/平台共享到账对账余额.md)
- [平台初始化VIP与游戏资料接口](platform/平台初始化VIP与游戏资料接口.md)
- [平台初始化老板分级](platform/平台初始化老板分级.md)
- [平台初始化通行证配置](platform/平台初始化通行证配置.md)
- [平台建店编号前缀](platform/平台建店编号前缀.md)
- [平台开店游戏抽成门店上下文](platform/平台开店游戏抽成门店上下文.md)
- [平台总览授权复核](platform/平台总览授权复核.md)
- [平台授权合同审计](platform/平台授权合同审计.md)
- [平台授权档案分页](platform/平台授权档案分页.md)
- [平台授权管理员列表](platform/平台授权管理员列表.md)
- [平台提现实际打款金额核对](platform/平台提现实际打款金额核对.md)
- [平台新店运营字典配置](platform/平台新店运营字典配置.md)
- [平台档位功能审计](platform/平台档位功能审计.md)
- [平台跨店提现汇总](platform/平台跨店提现汇总.md)
- [平台通行证退款欠款汇总](platform/平台通行证退款欠款汇总.md)
- [平台门店域名接口](platform/平台门店域名接口.md)
- [逐店定时任务入队隔离](platform/逐店定时任务入队隔离.md)

### points

- [消费积分付款时间快照](points/消费积分付款时间快照.md)
- [消费积分授权拒绝结案与来源](points/消费积分授权拒绝结案与来源.md)
- [消费积分授权未知拒绝结案](points/消费积分授权未知拒绝结案.md)
- [消费积分授权跨周期零额结案](points/消费积分授权跨周期零额结案.md)
- [积分主单多轮退款通用化方案](points/积分主单多轮退款通用化方案.md)
- [积分二次退款后加购资金版本](points/积分二次退款后加购资金版本.md)
- [积分双陪单次退款加购买换经营成本](points/积分双陪单次退款加购买换经营成本.md)
- [积分双陪多加购换人经营成本](points/积分双陪多加购换人经营成本.md)
- [积分双陪已付后加购退款](points/积分双陪已付后加购退款.md)
- [积分双陪部分结算经营成本](points/积分双陪部分结算经营成本.md)
- [积分双陪预约售后退款](points/积分双陪预约售后退款.md)
- [积分同单接替后补资](points/积分同单接替后补资.md)
- [积分商城商品授权边界](points/积分商城商品授权边界.md)
- [积分已分配订单取消预览](points/积分已分配订单取消预览.md)
- [积分抵扣配置状态](points/积分抵扣配置状态.md)
- [积分接替退款本轮进度](points/积分接替退款本轮进度.md)
- [积分改派实销联调范围](points/积分改派实销联调范围.md)
- [积分无转单双陪后续协商退款](points/积分无转单双陪后续协商退款.md)
- [积分无转单跨加购退款到账核对](points/积分无转单跨加购退款到账核对.md)
- [积分无转单部分退款后加购](points/积分无转单部分退款后加购.md)
- [积分普通单退款后平台补资](points/积分普通单退款后平台补资.md)
- [积分权益处理锁序](points/积分权益处理锁序.md)
- [积分自愿接替资金事件内核](points/积分自愿接替资金事件内核.md)
- [积分转单加购经营成本](points/积分转单加购经营成本.md)
- [积分转单双次退款ROI结清读取](points/积分转单双次退款ROI结清读取.md)
- [积分转单双次退款加购经营成本](points/积分转单双次退款加购经营成本.md)
- [积分转单后加购退款追加协商](points/积分转单后加购退款追加协商.md)
- [积分转单已发佣金退款经营成本](points/积分转单已发佣金退款经营成本.md)
- [积分转单第三轮会员实销](points/积分转单第三轮会员实销.md)
- [积分转单追加退款后报单](points/积分转单追加退款后报单.md)
- [积分转单追加退款多名已付人员](points/积分转单追加退款多名已付人员.md)
- [积分转单追加退款已付加购到账回放](points/积分转单追加退款已付加购到账回放.md)
- [积分转单部分结算与退款期间ROI](points/积分转单部分结算与退款期间ROI.md)
- [积分转单零退款后多次加购接替](points/积分转单零退款后多次加购接替.md)
- [积分退款后接替跨到账补资](points/积分退款后接替跨到账补资.md)
- [积分非转单后续退款加购](points/积分非转单后续退款加购.md)
- [积分首次退款转单加购多份额结算-20261114](points/积分首次退款转单加购多份额结算-20261114.md)
- [积分首次退款转单待接替责任版本-20261114](points/积分首次退款转单待接替责任版本-20261114.md)

### refund-aftersale

- [四维低星评价后台筛选-20261113](refund-aftersale/四维低星评价后台筛选-20261113.md)
- [房间转单协商后继续结单](refund-aftersale/房间转单协商后继续结单.md)
- [房间转单追加协商异议](refund-aftersale/房间转单追加协商异议.md)
- [推荐佣金退款补资接口](refund-aftersale/推荐佣金退款补资接口.md)
- [服务退款会员授权审核重算重试](refund-aftersale/服务退款会员授权审核重算重试.md)
- [老板退店部分退款订单结清](refund-aftersale/老板退店部分退款订单结清.md)
- [评价提交售后结果提示](refund-aftersale/评价提交售后结果提示.md)
- [退款会员成长授权核对接口](refund-aftersale/退款会员成长授权核对接口.md)
- [退款实销会员授权拒绝结案](refund-aftersale/退款实销会员授权拒绝结案.md)

### reservation

- [双陪快速退出因果顺序](reservation/双陪快速退出因果顺序.md)
- [双陪邀请确认因果顺序](reservation/双陪邀请确认因果顺序.md)
- [双陪限量折扣券计费基数](reservation/双陪限量折扣券计费基数.md)
- [双陪预约主动确认跨截止](reservation/双陪预约主动确认跨截止.md)
- [双陪预约到期与换人竞争](reservation/双陪预约到期与换人竞争.md)
- [双陪预约双席空缺替换-20261113](reservation/双陪预约双席空缺替换-20261113.md)
- [双陪预约换人确认当前席位](reservation/双陪预约换人确认当前席位.md)
- [双陪预约替换邀请重试](reservation/双陪预约替换邀请重试.md)
- [双陪预约确认重试](reservation/双陪预约确认重试.md)
- [双陪预约空席展示](reservation/双陪预约空席展示.md)
- [已付款预约改派档期](reservation/已付款预约改派档期.md)
- [已确认预约付款的当前档期](reservation/已确认预约付款的当前档期.md)
- [报价推荐与预约显式用券](reservation/报价推荐与预约显式用券.md)
- [新预约与换人的未来档期](reservation/新预约与换人的未来档期.md)
- [普通双陪后续协商经营成本](reservation/普通双陪后续协商经营成本.md)
- [普通双陪完成后本人追加售后](reservation/普通双陪完成后本人追加售后.md)
- [普通双陪跨加购退款经营成本](reservation/普通双陪跨加购退款经营成本.md)
- [普通双陪首次零退款后续售后](reservation/普通双陪首次零退款后续售后.md)
- [普通退款加购双陪结算时钟](reservation/普通退款加购双陪结算时钟.md)
- [预约优惠活动锁后核对](reservation/预约优惠活动锁后核对.md)
- [预约免费安全箱补贴接入](reservation/预约免费安全箱补贴接入.md)
- [预约历史与本人操作周期](reservation/预约历史与本人操作周期.md)
- [预约确认后付款周期-20261113](reservation/预约确认后付款周期-20261113.md)
- [预约积分服务前取消幂等](reservation/预约积分服务前取消幂等.md)
- [预约逐单到期处理](reservation/预约逐单到期处理.md)
- [预约邀请陪玩周期-20261113](reservation/预约邀请陪玩周期-20261113.md)

### store-init

- [后台门店身份切换](store-init/后台门店身份切换.md)
- [开业前VIP批量顺序停用和开关](store-init/开业前VIP批量顺序停用和开关.md)
- [开业前会员升级礼配置](store-init/开业前会员升级礼配置.md)
- [开业前会员权益及老板分级修改](store-init/开业前会员权益及老板分级修改.md)
- [开业前已有老板VIP等级重算](store-init/开业前已有老板VIP等级重算.md)
- [开业前已有老板配置预存与积分](store-init/开业前已有老板配置预存与积分.md)
- [开业前常规抽成配置](store-init/开业前常规抽成配置.md)
- [开业前抽奖配置](store-init/开业前抽奖配置.md)
- [开业前支付费率估算](store-init/开业前支付费率估算.md)
- [开业前服务加购配置](store-init/开业前服务加购配置.md)
- [开业前礼物权益规则](store-init/开业前礼物权益规则.md)
- [开业前签到配置](store-init/开业前签到配置.md)
- [开业前订单流量配置](store-init/开业前订单流量配置.md)
- [开业礼物价格抽成核对](store-init/开业礼物价格抽成核对.md)
- [开业积分发放倍率核对](store-init/开业积分发放倍率核对.md)
- [开业通行证销售配置](store-init/开业通行证销售配置.md)
- [开业预存赠送配置核对](store-init/开业预存赠送配置核对.md)
- [开店押金配置事务审计](store-init/开店押金配置事务审计.md)
- [开店时限与押金概览一致性](store-init/开店时限与押金概览一致性.md)
- [开店积分抵扣配置读取](store-init/开店积分抵扣配置读取.md)
- [成就配置门店币种](store-init/成就配置门店币种.md)
- [提现门店初审资金核对](store-init/提现门店初审资金核对.md)
- [新店会员基础配置初始化](store-init/新店会员基础配置初始化.md)
- [新店初始化商品选项](store-init/新店初始化商品选项.md)
- [新店初始化陪玩等级](store-init/新店初始化陪玩等级.md)
- [登录二次验证门店周期-20261113](store-init/登录二次验证门店周期-20261113.md)
- [门店企微群派单配置](store-init/门店企微群派单配置.md)
- [门店动态建库配置](store-init/门店动态建库配置.md)
- [首次开业抢单能力核验](store-init/首次开业抢单能力核验.md)

## 维护规则

- 新专题按领域归入对应子目录；场景名直述业务行为，跨领域专题归主领域并在相关领域索引互链。
- 专题结论被编号主文档吸收后，在专题头部标注结论落点；不删除历史记录。
- 本索引由整理脚本生成（2026-11-17）；新增文档后须同步更新对应领域清单。

Copyright (c) 2026 青岛声浪传媒有限公司. All rights reserved.
