export type LoginScenario = 'default' | 'verifying' | 'failure' | 'binding' | 'unavailable'
export type BossPage = 'login' | 'home' | 'companions' | 'companion-picker' | 'companion-detail' | 'catalog' | 'service-detail' | 'checkout' | 'orders' | 'order-detail' | 'wallet' | 'gifts' | 'mine' | 'messages' | 'chat' | 'support' | 'security' | 'profile' | 'reservations' | 'reservation-detail' | 'reservation-create' | 'coupons' | 'membership' | 'points' | 'lottery' | 'reward-claim' | 'favorites' | 'checkin' | 'achievements' | 'rankings' | 'announcements' | 'announcement-detail' | 'club-content' | 'club-content-detail' | 'activities' | 'activity-detail' | 'rights' | 'rights-detail' | 'point-rules' | 'point-rules-detail' | 'message-detail' | 'audition'

export interface ScenarioOption {
  value: string
  label: string
  description: string
}

export const loginScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '默认登录', description: '微信快捷登录、手机号验证与可选二次验证' },
  { value: 'verifying', label: '账号核验中', description: '防重复提交与加载反馈' },
  { value: 'failure', label: '登录失败', description: '错误说明与客服下一步' },
  { value: 'binding', label: '找回并绑定', description: '授权手机号找回原账号' },
  { value: 'unavailable', label: '手机号不可用', description: '能力受限时的明确提示' },
]

export const homeScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '已登录首页', description: '订单进度、服务与专属权益' },
  { value: 'guest', label: '游客首页', description: '登录引导与公开服务内容' },
  { value: 'loading', label: '首页加载中', description: '主视觉、进度与服务骨架' },
  { value: 'error', label: '服务加载失败', description: '局部错误与重新加载' },
  { value: 'popup', label: '入场活动弹窗', description: '近期活动与下单须知' },
]

export const companionScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '陪玩目录', description: '完整筛选与陪玩卡片' },
  { value: 'guest', label: '游客访问', description: '登录后查看陪玩目录' },
  { value: 'loading', label: '列表加载中', description: '陪玩卡片骨架反馈' },
  { value: 'error', label: '列表加载失败', description: '保留筛选并重试' },
  { value: 'empty', label: '无匹配结果', description: '空态与重置筛选' },
  { value: 'service', label: '按服务挑选', description: '服务上下文与平台派单' },
]

export const companionPickerScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '单陪服务选人', description: '选择一位陪玩或由平台安排' },
  { value: 'double', label: '双陪套餐选人', description: '选齐两位且套餐价格不变' },
  { value: 'guest', label: '游客访问', description: '登录后保留服务并挑选陪玩' },
  { value: 'loading', label: '可选陪玩加载中', description: '陪玩卡片与筛选骨架' },
  { value: 'error', label: '可选陪玩加载失败', description: '保留选择并安全重试' },
  { value: 'empty', label: '暂无可选陪玩', description: '重置筛选或平台安排' },
  { value: 'selection-changed', label: '已选状态已变化', description: '取消失效陪玩后重新选择' },
  { value: 'validating', label: '接单信息核对中', description: '锁定选择与重复确认' },
  { value: 'validate-error', label: '接单信息核对失败', description: '保留选择并允许重试' },
  { value: 'checkout', label: '从订单重新选择', description: '保留订单性别并回传选择' },
]

export const companionDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '陪玩资料', description: '参考时价、语音与接单信息' },
  { value: 'guest', label: '游客访问', description: '登录后查看完整资料' },
  { value: 'loading', label: '详情加载中', description: '资料与服务价格骨架' },
  { value: 'error', label: '详情加载失败', description: '重试与返回列表' },
  { value: 'unavailable', label: '暂不可约', description: '不可用原因与禁用下单' },
  { value: 'service', label: '单人服务', description: '服务价格、预约与选择下单' },
  { value: 'picker', label: '多人服务挑选', description: '已选状态与返回列表' },
]

export const catalogScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '服务商城', description: '分类、筛选与精选套餐' },
  { value: 'guest', label: '游客访问', description: '登录后查看完整服务' },
  { value: 'loading', label: '目录加载中', description: '服务卡片骨架反馈' },
  { value: 'error', label: '目录加载失败', description: '保留筛选并重试' },
  { value: 'empty', label: '无匹配服务', description: '空态与清除筛选' },
  { value: 'provider', label: '指定陪玩', description: '仅展示 TA 可接的套餐' },
  { value: 'coupon', label: '优惠券适用', description: '仅展示本券适用套餐' },
]

export const serviceDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '服务详情', description: '规格、规则、评价与确认服务' },
  { value: 'guest', label: '游客访问', description: '登录后查看价格与说明' },
  { value: 'loading', label: '详情加载中', description: '服务信息骨架反馈' },
  { value: 'error', label: '详情加载失败', description: '重试与返回目录' },
  { value: 'consult', label: '客服确认方案', description: '咨询模式与服务须知' },
  { value: 'provider', label: '已指定陪玩', description: '保留陪玩选择上下文' },
  { value: 'review-error', label: '评价加载失败', description: '服务可浏览，评价局部重试' },
]

export const checkoutScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '确认下单', description: '服务安排、游戏资料与余额支付' },
  { value: 'guest', label: '游客访问', description: '登录后核对报价与下单' },
  { value: 'loading', label: '下单信息加载中', description: '服务与报价骨架反馈' },
  { value: 'error', label: '下单信息加载失败', description: '保留服务上下文并重试' },
  { value: 'provider', label: '已指定陪玩', description: '单陪直达，不进入公开接单池' },
  { value: 'double', label: '双陪套餐', description: '必须选齐两位或平台安排' },
  { value: 'quoting', label: '正在报价', description: '报价确认前禁止提交' },
  { value: 'quote-error', label: '报价失败', description: '刷新报价后再提交' },
  { value: 'insufficient', label: '余额不足', description: '充值指引与禁用提交' },
  { value: 'pending', label: '上次结果待核对', description: '防止重复下单与重复扣款' },
  { value: 'zero', label: '零元订单', description: '无需支付的确认反馈' },
  { value: 'closed', label: '自主下单关闭', description: '联系客服并禁止提交' },
]

export const ordersScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '订单列表', description: '全部服务进度与消费记录' },
  { value: 'guest', label: '游客访问', description: '登录后查看个人订单' },
  { value: 'loading', label: '订单加载中', description: '订单卡片骨架反馈' },
  { value: 'error', label: '订单加载失败', description: '明确错误与重新加载' },
  { value: 'empty', label: '暂无订单', description: '空态与服务浏览入口' },
  { value: 'pending', label: '待接单与报名', description: '接单截止时间与报名进度' },
  { value: 'review', label: '完成待评价', description: '多人服务评价进度' },
  { value: 'refund', label: '取消与退款', description: '原实付、累计退款与剩余金额' },
  { value: 'gifts', label: '礼物订单', description: '仅查看赠礼与送达记录' },
  { value: 'payment-pending', label: '支付待确认', description: '付款核对前不展示派单进度' },
  { value: 'aftersale', label: '售后处理中', description: '售后进度与实销提示' },
]

export const orderDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '订单详情', description: '费用、陪玩、游戏与服务时间线' },
  { value: 'guest', label: '游客访问', description: '登录后查看个人订单详情' },
  { value: 'loading', label: '详情加载中', description: '订单摘要与信息骨架' },
  { value: 'error', label: '详情加载失败', description: '重试或返回订单列表' },
  { value: 'pending', label: '报名选人', description: '可接状态、选择与取消订单' },
  { value: 'grabbed', label: '已接单', description: '陪玩安排与开局前进度' },
  { value: 'in-service', label: '服务中', description: '履约进度与售后申请' },
  { value: 'review', label: '多人待评价', description: '分陪玩评价与匿名反馈' },
  { value: 'refund', label: '取消与退款', description: '退款记录与钱包入口' },
  { value: 'gift', label: '礼物详情', description: '赠礼对象、备注与送达记录' },
  { value: 'payment-pending', label: '支付待确认', description: '核对前关闭选人和取消' },
  { value: 'aftersale', label: '售后处理中', description: '申请说明与实销暂停提示' },
]

export const walletScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '我的钱包', description: '可用余额、冻结金额与全部流水' },
  { value: 'guest', label: '游客访问', description: '登录后查看余额与资金明细' },
  { value: 'loading', label: '钱包加载中', description: '余额、充值与流水骨架反馈' },
  { value: 'error', label: '钱包加载失败', description: '明确错误与安全重试' },
  { value: 'empty', label: '暂无流水', description: '零余额与空态说明' },
  { value: 'refund', label: '退款流水', description: '从订单退款记录进入钱包' },
  { value: 'more-error', label: '分页加载失败', description: '保留已有流水并允许重试' },
]

export const giftsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '礼物商城', description: '收礼陪玩、多选礼物与余额赠送' },
  { value: 'guest', label: '游客访问', description: '登录后挑选并赠送礼物' },
  { value: 'loading', label: '礼物加载中', description: '收礼信息与礼物卡片骨架' },
  { value: 'error', label: '礼物加载失败', description: '安全重试，不产生重复赠送' },
  { value: 'empty', label: '暂无礼物', description: '礼物未上架时的明确空态' },
  { value: 'order', label: '从订单赠礼', description: '保留来源订单与本单陪玩' },
  { value: 'pending', label: '赠送待核对', description: '查询原结果或重试原请求' },
  { value: 'pending-blocked', label: '赠送结果受阻', description: '禁止重复提交并联系客服' },
  { value: 'insufficient', label: '余额不足', description: '保留心意清单并引导充值' },
  { value: 'wallet-inactive', label: '钱包不可用', description: '禁用赠送并说明处理入口' },
]

export const mineScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '个人中心', description: '身份、钱包、订单与会员权益' },
  { value: 'guest', label: '游客访问', description: '登录后管理个人账号与记录' },
  { value: 'loading', label: '个人中心加载中', description: '身份、钱包与订单骨架反馈' },
  { value: 'error', label: '个人中心加载失败', description: '安全重试，不修改账号数据' },
  { value: 'unbound', label: '微信未绑定', description: '突出账号安全与绑定入口' },
  { value: 'basic', label: '普通会员', description: '零积分、零订单的初始账号状态' },
]

export const messagesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '我的通知', description: '未读提醒、分类筛选与全部已读' },
  { value: 'guest', label: '游客访问', description: '登录后接收动态与专属通知' },
  { value: 'loading', label: '消息加载中', description: '通知列表骨架反馈' },
  { value: 'error', label: '消息加载失败', description: '保留未读状态并安全重试' },
  { value: 'empty', label: '暂无通知', description: '当前分类下的明确空态' },
  { value: 'more-error', label: '分页加载失败', description: '保留已有通知并允许重试' },
  { value: 'feed', label: '俱乐部动态', description: '礼物、冠名、好评与会员动态' },
  { value: 'feed-empty', label: '暂无俱乐部动态', description: '新动态发布前的明确空态' },
  { value: 'feed-error', label: '动态加载失败', description: '动态局部错误与重新加载' },
]

export const chatScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '平台会话', description: '历史消息、文字与图片输入' },
  { value: 'guest', label: '游客访问', description: '登录后查看会话并咨询客服' },
  { value: 'loading', label: '会话加载中', description: '聊天记录骨架反馈' },
  { value: 'error', label: '会话加载失败', description: '安全重试，不重复发送消息' },
  { value: 'empty', label: '暂无会话记录', description: '从第一个咨询问题开始' },
  { value: 'order', label: '关联订单咨询', description: '订单号预填与取消关联' },
  { value: 'pending', label: '发送结果待核对', description: '核对、重试原消息或放弃核对' },
  { value: 'pending-restored', label: '恢复待核对消息', description: '恢复后禁止盲目重复发送' },
  { value: 'send-error', label: '发送结果未知', description: '保留内容并进入防重发流程' },
  { value: 'history-error', label: '历史加载失败', description: '保留当前会话并重试历史记录' },
]

export const supportScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '客服与帮助', description: '咨询编号、企业微信与平台会话' },
  { value: 'guest', label: '游客访问', description: '公开联系方式与登录后平台会话' },
  { value: 'loading', label: '客服信息加载中', description: '咨询上下文与通道骨架反馈' },
  { value: 'error', label: '客服信息加载失败', description: '安全重试并保留返回入口' },
  { value: 'order', label: '关联订单咨询', description: '携带咨询编号和订单上下文' },
  { value: 'recharge', label: '钱包充值咨询', description: '付款核对与钱包流水说明' },
  { value: 'opening', label: '正在打开企业微信', description: '防重复点击与加载反馈' },
  { value: 'open-error', label: '企业微信打开失败', description: '错误说明、重试与备用通道' },
  { value: 'unsupported', label: '当前微信不支持', description: '更新微信或改用平台会话' },
  { value: 'unavailable', label: '企业微信未配置', description: '保留平台会话与其他联系方式' },
  { value: 'empty', label: '联系方式待配置', description: '无公开名片时仍可使用平台会话' },
]

export const securityScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '账号安全与二次验证', description: '微信、脱敏手机号与可选 OTP 绑定入口' },
  { value: 'guest', label: '游客访问', description: '登录后管理账号与绑定信息' },
  { value: 'loading', label: '账号信息加载中', description: '绑定状态与安全入口骨架反馈' },
  { value: 'error', label: '账号信息加载失败', description: '安全重试，不改变现有绑定' },
  { value: 'wechat-unbound', label: '微信尚未绑定', description: '绑定当前微信与本地成功反馈' },
  { value: 'phone-unverified', label: '手机号尚未验证', description: '模拟授权并更新脱敏状态' },
  { value: 'busy-wechat', label: '微信绑定处理中', description: '禁用重复提交与其他授权操作' },
  { value: 'busy-phone', label: '手机号验证处理中', description: '禁用重复提交与退出操作' },
  { value: 'phone-denied', label: '拒绝手机号授权', description: '保留原绑定信息并允许重试' },
  { value: 'bind-error', label: '微信绑定失败', description: '明确失败原因与安全重试' },
  { value: 'unsupported', label: '环境不支持授权', description: '保留状态并提供客服入口' },
  { value: 'success', label: '绑定信息已更新', description: '成功反馈与最新脱敏状态' },
  { value: 'otp-bound', label: '二次验证已开启', description: '查看 OTP 开启状态并重新绑定验证器' },
]

export const profileScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '个人与游戏资料', description: '头像、昵称、默认游戏与接单信息' },
  { value: 'guest', label: '游客访问', description: '登录后编辑账号与游戏资料' },
  { value: 'loading', label: '资料加载中', description: '账户与游戏信息骨架反馈' },
  { value: 'error', label: '资料加载失败', description: '安全重试，不覆盖已保存资料' },
  { value: 'dirty', label: '有未保存修改', description: '编辑状态、保存反馈与自动回填说明' },
  { value: 'saving', label: '资料保存中', description: '禁用编辑与重复提交' },
  { value: 'save-error', label: '资料保存失败', description: '保留已填内容并允许重试' },
  { value: 'avatar-saving', label: '头像上传中', description: '独立保存头像并锁定其他操作' },
  { value: 'avatar-error', label: '头像更新失败', description: '局部错误，不影响其他资料' },
  { value: 'phone-empty', label: '手机号未设置', description: '保留账号安全验证入口' },
  { value: 'empty-games', label: '暂无游戏分类', description: '隐藏游戏表单，保留账户资料' },
  { value: 'success', label: '资料保存成功', description: '同步状态与下单自动回填反馈' },
]

export const reservationsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '我的预约', description: '预约时间、确认状态与分页记录' },
  { value: 'guest', label: '游客访问', description: '登录后查看当前账号的预约' },
  { value: 'loading', label: '预约加载中', description: '预约卡片骨架反馈' },
  { value: 'error', label: '预约加载失败', description: '安全重试，不取消或生成订单' },
  { value: 'empty', label: '暂无预约', description: '空态说明与选择服务入口' },
  { value: 'more-loading', label: '分页加载中', description: '保留已有记录并防止重复加载' },
  { value: 'more-error', label: '分页加载失败', description: '保留已有记录并允许重试' },
  { value: 'all-statuses', label: '全部预约状态', description: '待确认、已确认、成单与结束状态' },
]

export const reservationDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '已确认 · 待报价', description: '预约安排、游戏资料与获取报价' },
  { value: 'guest', label: '游客访问', description: '登录后查看当前账号预约详情' },
  { value: 'loading', label: '详情加载中', description: '状态、安排与报价骨架反馈' },
  { value: 'error', label: '详情加载失败', description: '安全重试，不取消或生成订单' },
  { value: 'pending', label: '等待陪玩确认', description: '可取消预约，尚不能获取报价' },
  { value: 'quoting', label: '实时报价中', description: '核对服务快照与钱包余额' },
  { value: 'quote-ready', label: '报价已更新', description: '预估差异、余额与二次确认成单' },
  { value: 'quote-error', label: '服务信息已变化', description: '旧报价失效并重新获取' },
  { value: 'insufficient', label: '钱包余额不足', description: '转钱包并使旧报价失效' },
  { value: 'convert-pending', label: '成单结果待核对', description: '禁止盲目重复，先刷新预约' },
  { value: 'converted', label: '预约已成单', description: '查看关联订单与后续服务进度' },
  { value: 'cancelled', label: '预约已取消', description: '处理说明与无扣款反馈' },
  { value: 'rejected', label: '陪玩已拒绝', description: '拒绝原因与结束状态' },
  { value: 'expired', label: '预约已过期', description: '沿用原服务与陪玩重新预约' },
]

export const reservationCreateScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '新建预约', description: '时间、时长、数量与游戏资料' },
  { value: 'guest', label: '游客访问', description: '登录后保存当前账号预约' },
  { value: 'loading', label: '预约信息加载中', description: '服务、陪玩与表单骨架反馈' },
  { value: 'error', label: '预约信息加载失败', description: '安全重试或重新选择服务' },
  { value: 'invalid-time', label: '时间步长错误', description: '开始时间必须按 15 分钟选择' },
  { value: 'invalid-rounds', label: '服务数量错误', description: '数量必须是正整数' },
  { value: 'submitting', label: '预约提交中', description: '锁定表单并防止重复提交' },
  { value: 'submit-error', label: '明确提交失败', description: '保留表单并允许调整重试' },
  { value: 'uncertain', label: '提交结果待核对', description: '网络异常后先核对预约记录' },
]

export const couponsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '我的券', description: '待使用卡券、门槛与适用服务' },
  { value: 'available', label: '领券中心', description: '可领取、已领取与领完状态' },
  { value: 'guest', label: '游客访问', description: '登录后同步领取与使用记录' },
  { value: 'loading', label: '优惠券加载中', description: '票券列表骨架反馈' },
  { value: 'error', label: '优惠券加载失败', description: '安全重试，不改变卡券状态' },
  { value: 'empty', label: '暂无优惠券', description: '状态空态与选择服务入口' },
  { value: 'claiming', label: '优惠券领取中', description: '锁定领取按钮防止重复操作' },
  { value: 'claim-uncertain', label: '领取结果待核对', description: '先查看我的券，不盲目重复领取' },
  { value: 'use-error', label: '使用状态已变化', description: '使用前重新核验最新状态' },
  { value: 'used', label: '已使用', description: '历史使用记录与只读说明' },
  { value: 'expired', label: '已过期', description: '到期状态与失效卡券展示' },
  { value: 'disabled', label: '已停用', description: '停用状态与不可用说明' },
]

export const membershipScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '成长中会员', description: '确认实销、待确认金额与升级进度' },
  { value: 'guest', label: '游客访问', description: '登录后查看会员身份与专属权益' },
  { value: 'loading', label: '会员信息加载中', description: '身份、权益与名片设置骨架' },
  { value: 'error', label: '会员信息加载失败', description: '安全重试，不修改会员资料' },
  { value: 'basic', label: '普通老板', description: '零实销与第一等级成长目标' },
  { value: 'highest', label: '当前最高等级', description: '全部等级已解锁的成长状态' },
  { value: 'unconfigured', label: '等级待配置', description: '无会员等级时的明确空态' },
  { value: 'empty-tags', label: '暂无可展示标签', description: '继续积累俱乐部成就' },
  { value: 'tag-saving', label: '标签保存中', description: '锁定标签与其他会员设置' },
  { value: 'tag-error', label: '标签保存失败', description: '保留已选内容并允许重试' },
  { value: 'tag-saved', label: '标签保存成功', description: '名片展示标签更新反馈' },
  { value: 'refresh-pending', label: '会员信息待刷新', description: '先保存或撤销标签修改' },
  { value: 'birthday-empty', label: '生日未登记', description: '二次确认生日与积分规则' },
  { value: 'birthday-saving', label: '生日设置保存中', description: '防止重复提交生日设置' },
  { value: 'birthday-error', label: '生日设置失败', description: '保留原生日并提供重试反馈' },
  { value: 'save-refresh-error', label: '保存成功刷新失败', description: '区分设置结果与会员信息刷新' },
]

export const pointsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '积分与好礼', description: '积分账户、KOOK 绑定与好礼兑换' },
  { value: 'guest', label: '游客访问', description: '登录后查看积分与兑换记录' },
  { value: 'loading', label: '积分信息加载中', description: '账户与好礼内容骨架反馈' },
  { value: 'error', label: '积分信息加载失败', description: '安全重试，不扣除或改变积分' },
  { value: 'empty', label: '暂无积分好礼', description: '礼物上架前的明确空态' },
  { value: 'insufficient', label: '积分不足', description: '保留兑换入口并说明累积方式' },
  { value: 'soldout', label: '好礼已兑完', description: '库存状态与禁用兑换反馈' },
  { value: 'pending', label: '兑换结果待确认', description: '只重试原申请，避免重复扣点' },
  { value: 'redeeming', label: '兑换处理中', description: '锁定重复兑换与绑定操作' },
  { value: 'claims', label: '权益记录', description: '兑换奖品与待补充交付资料' },
  { value: 'ledger', label: '积分明细', description: '积分收入、支出与分页记录' },
  { value: 'more-error', label: '明细分页失败', description: '保留已有明细并允许重试' },
  { value: 'kook-bound', label: 'KOOK 已绑定', description: '在线积分与每日签到状态' },
]

export const lotteryScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '幸运转盘', description: '单抽、十连、保底与概率公示' },
  { value: 'loading', label: '转盘加载中', description: '奖池与保底信息骨架反馈' },
  { value: 'error', label: '转盘加载失败', description: '安全重试，不生成抽奖记录' },
  { value: 'drawing', label: '抽奖结果确认中', description: '请求期间锁定单抽与十连' },
  { value: 'revealing', label: '奖品揭晓中', description: '逐份揭晓与跳过动画' },
  { value: 'success', label: '单抽结果', description: '积分自动到账与奖励详情' },
  { value: 'ten-success', label: '十连结果', description: '非积分兜底与逐项领取' },
  { value: 'pending', label: '抽奖结果待核对', description: '重试同一笔请求或核对记录' },
  { value: 'credential-error', label: '抽奖凭证不可读', description: '恢复凭证后再处理原申请' },
  { value: 'accepted-error', label: '已受理展示异常', description: '到中奖记录核对并领取' },
  { value: 'disabled', label: '转盘暂未开放', description: '禁用抽奖并说明当前状态' },
  { value: 'insufficient', label: '积分不足', description: '明确失败反馈，不扣除积分' },
  { value: 'daily-empty', label: '今日次数已用完', description: '禁用抽奖并展示剩余次数' },
  { value: 'empty', label: '奖池准备中', description: '无奖品时的明确空态' },
]

export const rewardClaimScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '实物奖品交付', description: '收件人、电话与完整收货地址' },
  { value: 'guest', label: '游客访问', description: '登录后查看本人奖品交付' },
  { value: 'virtual', label: '虚拟物品交付', description: '游戏 UID 与补充说明' },
  { value: 'tag', label: 'TAG 待复核', description: '标签内容与客服复核规则' },
  { value: 'naming', label: '陪玩冠名交付', description: '冠名对象与交付说明' },
  { value: 'completed', label: '奖品已完成', description: '已提交资料与会员标签管理' },
  { value: 'cancelled', label: '权益已取消', description: '取消状态与积分退回说明' },
  { value: 'loading', label: '交付信息加载中', description: '状态、资料与进度骨架反馈' },
  { value: 'error', label: '交付信息加载失败', description: '安全重试，不覆盖已保存资料' },
  { value: 'empty', label: '奖品记录不存在', description: '返回记录或联系客服核对' },
  { value: 'saving', label: '交付资料保存中', description: '锁定表单与重复提交' },
  { value: 'save-error', label: '交付资料保存失败', description: '保留内容并提供恢复操作' },
  { value: 'conflict', label: '交付资料冲突', description: '复制草稿后再使用最新资料' },
  { value: 'status-changed', label: '权益状态已变化', description: '刷新状态并保护未保存资料' },
]

export const favoritesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '我的收藏', description: '收藏服务、价格与详情入口' },
  { value: 'guest', label: '游客访问', description: '登录后同步当前账号的收藏' },
  { value: 'loading', label: '收藏加载中', description: '收藏服务卡片骨架反馈' },
  { value: 'error', label: '收藏加载失败', description: '安全重试，不改变收藏状态' },
  { value: 'empty', label: '暂无收藏服务', description: '空态说明与服务目录入口' },
  { value: 'removing', label: '取消收藏中', description: '锁定重复取消与恢复操作' },
  { value: 'remove-error', label: '取消收藏失败', description: '保留卡片并允许安全重试' },
  { value: 'undo', label: '取消后可撤销', description: '恢复刚刚取消的可用服务' },
  { value: 'undoing', label: '恢复收藏中', description: '锁定重复恢复与取消操作' },
  { value: 'undo-error', label: '恢复结果待核对', description: '先刷新状态，不盲目重复切换' },
  { value: 'unavailable', label: '服务暂不可购买', description: '保留收藏管理与明确状态反馈' },
]

export const checkinScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '今日可签到', description: '消费达标、签到日历与本月奖励' },
  { value: 'guest', label: '游客访问', description: '登录后同步签到与补签卡' },
  { value: 'loading', label: '签到日历加载中', description: '概览、消费与日历骨架反馈' },
  { value: 'error', label: '签到日历加载失败', description: '安全重试，不签到或消耗补签卡' },
  { value: 'ineligible', label: '今日消费未达标', description: '展示消费差额并禁用签到' },
  { value: 'checked', label: '今日已签到', description: '连续记录与禁用重复签到' },
  { value: 'makeup', label: '可补签日期', description: '开放日期、补签卡与二次确认' },
  { value: 'no-cards', label: '暂无补签卡', description: '说明补签卡获取条件' },
  { value: 'submitting', label: '签到提交中', description: '锁定签到与全部补签操作' },
  { value: 'submit-error', label: '签到结果待核对', description: '先刷新日历，避免重复操作' },
  { value: 'empty-rewards', label: '本期奖励待公布', description: '保留签到记录与奖励空态' },
  { value: 'full-attendance', label: '本月全勤达成', description: '全勤奖励与完整签到记录' },
]

export const achievementsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '成就收集中', description: '最近目标、进行中与已达成记录' },
  { value: 'guest', label: '游客访问', description: '登录后同步订单与消费成就' },
  { value: 'loading', label: '成就加载中', description: '收集概览与成就卡片骨架' },
  { value: 'error', label: '成就加载失败', description: '安全重试，不改变成就状态' },
  { value: 'empty', label: '成就内容待配置', description: '无启用成就时的明确空态' },
  { value: 'no-progress', label: '新老板起点', description: '零完成订单与零净消费进度' },
  { value: 'near-unlock', label: '即将解锁', description: '最接近目标置顶与差额反馈' },
  { value: 'all-unlocked', label: '全部成就达成', description: '完整图鉴与会员名片入口' },
]

export const rankingsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '本月榜单', description: '陪玩服务与老板支持各取前五' },
  { value: 'guest', label: '游客访问', description: '登录后查看完整声浪榜单' },
  { value: 'loading', label: '榜单加载中', description: '双榜单与名次骨架反馈' },
  { value: 'error', label: '榜单加载失败', description: '安全重试，不改变排名数据' },
  { value: 'empty', label: '本期暂无上榜记录', description: '两类榜单的独立空态' },
  { value: 'providers-empty', label: '仅陪玩榜为空', description: '保留老板支持榜与局部空态' },
  { value: 'day', label: '今日榜单', description: '周期切换与当日完成订单' },
  { value: 'week', label: '本周榜单', description: '周期切换与本周完成订单' },
  { value: 'unavailable-provider', label: '陪玩资料不可打开', description: '保留名次但不展示无效跳转' },
]

export const announcementsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '平台公告', description: '置顶公告、发布时间与详情入口' },
  { value: 'guest', label: '游客访问', description: '登录后查看官方公告' },
  { value: 'loading', label: '公告加载中', description: '公告概览与列表骨架反馈' },
  { value: 'error', label: '公告加载失败', description: '安全重试，不改变公告数据' },
  { value: 'empty', label: '暂无平台公告', description: '公告尚未发布时的明确空态' },
  { value: 'limit', label: '展示 20 条公告', description: '列表数量上限与底部说明' },
  { value: 'long-title', label: '长公告标题', description: '置顶与长文本换行布局' },
]

export const announcementDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '公告详情', description: '官方来源、发布时间与完整正文' },
  { value: 'guest', label: '游客访问', description: '登录后阅读公告全文' },
  { value: 'loading', label: '详情加载中', description: '标题与公告正文骨架反馈' },
  { value: 'error', label: '详情加载失败', description: '安全重试并保留返回路径' },
  { value: 'invalid', label: '公告链接无效', description: '无效参数与返回列表指引' },
  { value: 'not-found', label: '公告不存在', description: '停用或缺失公告的明确反馈' },
  { value: 'empty-content', label: '暂无公告正文', description: '保留标题与发布时间的正文空态' },
  { value: 'long-content', label: '长公告正文', description: '多段内容、换行与阅读布局' },
]

export const clubContentScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '俱乐部规范', description: '下单、陪玩保障与售后内容目录' },
  { value: 'loading', label: '内容目录加载中', description: '概览与规范目录骨架反馈' },
  { value: 'error', label: '内容目录加载失败', description: '公开内容的安全重试反馈' },
  { value: 'empty', label: '暂无俱乐部内容', description: '内容尚未发布时的明确空态' },
  { value: 'long-title', label: '长规范标题', description: '长文本换行与完整入口布局' },
]

export const clubContentDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '规范图文详情', description: '发布日期、示例配图与完整说明' },
  { value: 'loading', label: '内容详情加载中', description: '标题、配图与正文骨架反馈' },
  { value: 'error', label: '内容详情加载失败', description: '安全重试并保留返回路径' },
  { value: 'not-found', label: '内容暂不可查看', description: '未发布、下架或过期内容反馈' },
  { value: 'empty', label: '正文准备中', description: '无配图与正文时的明确空态' },
  { value: 'long-content', label: '长规范正文', description: '多节长文与移动端阅读布局' },
]

export const activitiesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '俱乐部活动', description: '最近发布、往期活动与海报入口' },
  { value: 'loading', label: '活动目录加载中', description: '活动概览与海报卡片骨架' },
  { value: 'error', label: '活动目录加载失败', description: '公开活动的安全重试反馈' },
  { value: 'empty', label: '暂无俱乐部活动', description: '活动尚未发布时的明确空态' },
  { value: 'more', label: '活动归档分页', description: '首屏 30 条与查看更多活动' },
  { value: 'more-loading', label: '更多活动加载中', description: '保留已有活动并防止重复加载' },
  { value: 'more-error', label: '更多活动加载失败', description: '保留已有活动并允许重试' },
]

export const activityDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '活动海报', description: '完整海报与大图阅读体验' },
  { value: 'loading', label: '活动海报加载中', description: '海报区域骨架反馈' },
  { value: 'error', label: '活动海报加载失败', description: '安全重试并保留返回路径' },
  { value: 'not-found', label: '活动暂不可查看', description: '未发布或下架活动的明确反馈' },
  { value: 'empty', label: '活动海报准备中', description: '无海报时的空态与返回操作' },
]

export const guideContentScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '公开内容目录', description: '三篇权益或积分规则说明' },
  { value: 'loading', label: '内容目录加载中', description: '概览与文章目录骨架反馈' },
  { value: 'error', label: '内容目录加载失败', description: '公开内容的安全重试反馈' },
  { value: 'empty', label: '暂无公开内容', description: '内容尚未发布时的明确空态' },
]

export const guideContentDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '完整规则阅读', description: '发布日期、分节要点与提示块' },
  { value: 'loading', label: '内容详情加载中', description: '标题与规则正文骨架反馈' },
  { value: 'error', label: '内容详情加载失败', description: '安全重试并保留返回路径' },
  { value: 'not-found', label: '内容暂不可查看', description: '未发布、停用或过期内容反馈' },
  { value: 'empty', label: '正文准备中', description: '无正文时的明确空态' },
]

export const messageDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '订单通知详情', description: '已读状态、完整正文与关联订单' },
  { value: 'guest', label: '游客访问', description: '登录后读取当前账号通知' },
  { value: 'loading', label: '消息详情加载中', description: '通知正文与关联内容骨架' },
  { value: 'error', label: '消息详情加载失败', description: '安全重试且不改变已读状态' },
  { value: 'invalid', label: '消息链接无效', description: '无效参数与返回消息中心' },
  { value: 'empty-content', label: '暂无通知正文', description: '保留标题与时间的正文空态' },
  { value: 'wallet', label: '充值到账通知', description: '从通知进入钱包流水' },
  { value: 'claims', label: '权益到账通知', description: '从通知进入权益记录' },
]

export const auditionScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '有效试音邀请', description: '免登录核对活动与绑定身份' },
  { value: 'existing-account', label: '已有登录账号', description: '明确提示将切换当前账号' },
  { value: 'loading', label: '邀请信息加载中', description: '活动与账号确认区域骨架' },
  { value: 'error', label: '邀请信息加载失败', description: '安全重试且不切换账号' },
  { value: 'invalid', label: '邀请链接无效', description: '撤销、角色不符或参数无效' },
  { value: 'expired', label: '试音链接已过期', description: '过期反馈与活动客服入口' },
  { value: 'confirming', label: '身份确认处理中', description: '防止重复换发登录状态' },
  { value: 'exchange-error', label: '进入结果失败', description: '保留邀请并允许重试' },
  { value: 'unbound', label: '邀请未绑定账号', description: '明确提示联系客服处理' },
]
