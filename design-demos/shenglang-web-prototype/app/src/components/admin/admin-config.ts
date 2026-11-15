import type { ScenarioOption } from '@/components/prototype/prototype-config'

export type AdminLoginScenario = 'default' | 'verifying' | 'failure' | 'otp' | 'binding' | 'recovery'
export type AdminDashboardScenario = 'default' | 'loading' | 'error' | 'refresh-error' | 'support' | 'finance' | 'no-todos' | 'refreshing'

export const adminLoginScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '账号密码登录', description: '账号、密码与字段级校验' },
  { value: 'verifying', label: '账号核验中', description: '锁定表单并防止重复提交' },
  { value: 'failure', label: '登录失败', description: '保留账号并提供可执行反馈' },
  { value: 'otp', label: '动态码验证', description: '已绑定验证器的二次认证' },
  { value: 'binding', label: '首次绑定验证器', description: '扫码、手动密钥与绑定验证' },
  { value: 'recovery', label: '备用恢复码', description: '一次性恢复码与剩余凭证' },
]

export const adminDashboardScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '管理员工作台', description: '完整待办、经营指标与最近订单' },
  { value: 'loading', label: '首次加载中', description: '工作台模块骨架反馈' },
  { value: 'error', label: '首次加载失败', description: '无快照时的明确错误与重试' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次成功数据并提示' },
  { value: 'support', label: '客服视角', description: '聚焦订单履约、咨询与报单' },
  { value: 'finance', label: '财务视角', description: '聚焦提现、退款与平台补贴' },
  { value: 'no-todos', label: '暂无待办', description: '各队列空态与已完成反馈' },
  { value: 'refreshing', label: '快照刷新中', description: '保留内容并锁定重复刷新' },
]

export type AdminOrdersScenario = 'default' | 'staff' | 'aftersale' | 'loading' | 'error' | 'empty' | 'refresh-error'
export type AdminOrderDetailScenario = 'default' | 'pending' | 'in-service' | 'aftersale' | 'refunded' | 'loading' | 'error' | 'refresh-error'

export const adminOrdersScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部订单', description: '完整列表、搜索筛选与订单操作' },
  { value: 'staff', label: '客服待派', description: '已支付且符合待派筛选的订单' },
  { value: 'aftersale', label: '处理中售后', description: '按申请时间核对售后订单' },
  { value: 'loading', label: '订单加载中', description: '筛选与订单表格骨架反馈' },
  { value: 'error', label: '订单加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配订单', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的订单' },
]

export const adminOrderDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '订单履约详情', description: '订单、金额、游戏资料与状态流水' },
  { value: 'pending', label: '待接单调派', description: '核对资格并指派陪玩' },
  { value: 'in-service', label: '服务中订单', description: '履约信息、完成与退款入口' },
  { value: 'aftersale', label: '处理中售后', description: '处理说明与退款结案入口' },
  { value: 'refunded', label: '退款与追回', description: '实际退款、收益追回与权益结果' },
  { value: 'loading', label: '详情加载中', description: '订单摘要与详情模块骨架' },
  { value: 'error', label: '详情加载失败', description: '无快照时的错误与返回列表' },
  { value: 'refresh-error', label: '更新失败保留详情', description: '保留上次结果并锁定资金操作' },
]

export type AdminBossesScenario = 'default' | 'loading' | 'error' | 'empty'

export const adminBossesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部老板', description: '本店账号、会员、绑定与消费关系' },
  { value: 'loading', label: '档案加载中', description: '摘要、筛选与老板列表骨架反馈' },
  { value: 'error', label: '档案加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配老板', description: '保留筛选并支持清除条件' },
]

export type AdminProvidersScenario = 'default' | 'available' | 'unverified' | 'loading' | 'error' | 'empty' | 'refresh-error'
export type AdminProviderDetailScenario = 'default' | 'unverified' | 'busy' | 'review' | 'loading' | 'error' | 'refresh-error'

export const adminProvidersScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部陪玩', description: '完整档案、搜索筛选与认证状态' },
  { value: 'available', label: '当前空闲', description: '筛选档案状态为空闲的陪玩' },
  { value: 'unverified', label: '未认证陪玩', description: '核对实名与服务资格完善情况' },
  { value: 'loading', label: '档案加载中', description: '筛选与陪玩表格骨架反馈' },
  { value: 'error', label: '档案加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配陪玩', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的档案' },
]

export const adminProviderDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '陪玩档案详情', description: '资料、资格、押金、业绩与操作记录' },
  { value: 'unverified', label: '未认证档案', description: '认证复核与其他接单条件说明' },
  { value: 'busy', label: '忙碌与在途订单', description: '在途订单下的档案与退押金提醒' },
  { value: 'review', label: '保存结果待核对', description: '读取最新档案后解除操作限制' },
  { value: 'loading', label: '详情加载中', description: '陪玩摘要与详情模块骨架' },
  { value: 'error', label: '详情加载失败', description: '无快照时的错误与返回列表' },
  { value: 'refresh-error', label: '更新失败保留详情', description: '保留上次结果并锁定敏感操作' },
]

export type AdminRegistrationsScenario = 'default' | 'all' | 'approved' | 'rejected' | 'loading' | 'error' | 'empty' | 'refresh-error'
export type AdminRegistrationDetailScenario = 'default' | 'earnings' | 'approved' | 'rejected' | 'readonly' | 'review' | 'conflict' | 'loading' | 'error' | 'refresh-error'

export const adminRegistrationsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '待审核申请', description: '开户队列、身份资料与押金方式' },
  { value: 'all', label: '全部申请', description: '查看全部状态与审核记录' },
  { value: 'approved', label: '已通过申请', description: '核对开通等级与押金登记结果' },
  { value: 'rejected', label: '需补充资料', description: '查看退回原因与重新提交版本' },
  { value: 'loading', label: '申请加载中', description: '筛选与申请表格骨架反馈' },
  { value: 'error', label: '申请加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配申请', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的申请' },
]

export const adminRegistrationDetailScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '现金押金待审核', description: '实名、现金到账与开通等级复核' },
  { value: 'earnings', label: '单抵申请待审核', description: '实名核对，不登记现金押金' },
  { value: 'approved', label: '已通过申请', description: '开通等级、押金与审核结果' },
  { value: 'rejected', label: '需补充资料', description: '退回原因与重新提交记录' },
  { value: 'readonly', label: '仅查看申请', description: '可核对资料与材料，但不显示审核操作' },
  { value: 'review', label: '审核结果待核对', description: '读取最新申请后解除操作限制' },
  { value: 'conflict', label: '申请状态冲突', description: '他人已处理时要求重新读取' },
  { value: 'loading', label: '详情加载中', description: '申请摘要与审核模块骨架' },
  { value: 'error', label: '详情加载失败', description: '无快照时的错误与返回列表' },
  { value: 'refresh-error', label: '更新失败保留详情', description: '保留上次结果并锁定审核操作' },
]

export type AdminLevelsScenario = 'default' | 'readonly' | 'edit-only' | 'inactive' | 'save-review' | 'delete-review' | 'impact-loading' | 'impact-error' | 'scope-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminLevelsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部等级配置', description: '等级抽成、排序、启停与档位规则' },
  { value: 'readonly', label: '仅查看权限', description: '可查看等级与档位规则，不显示新增、编辑或删除' },
  { value: 'edit-only', label: '可编辑不可删除', description: '可新增和编辑等级，但不显示删除操作' },
  { value: 'inactive', label: '已停用等级', description: '查看历史等级与当前关联' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单后核对列表，避免重复保存' },
  { value: 'delete-review', label: '删除结果待核对', description: '读取当前状态后解除删除限制' },
  { value: 'impact-loading', label: '关联读取中', description: '读取完成前不可确认删除' },
  { value: 'impact-error', label: '关联读取失败', description: '重试成功前不可确认删除' },
  { value: 'scope-error', label: '档位规则读取失败', description: '独立重试，不影响等级列表' },
  { value: 'loading', label: '等级加载中', description: '配置与等级表格骨架反馈' },
  { value: 'error', label: '等级加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配等级', description: '保留筛选并支持查看全部' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的配置' },
]

export type AdminPassesScenario = 'default' | 'readonly' | 'saving' | 'save-error' | 'loading' | 'error' | 'empty'

export const adminPassesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '通行证规则与记录', description: '四档价格、可见延迟与购买流水' },
  { value: 'readonly', label: '只读权限', description: '仅查看规则与购买记录，不可修改配置' },
  { value: 'saving', label: '规则保存中', description: '锁定输入并防止重复提交' },
  { value: 'save-error', label: '规则保存失败', description: '保留当前输入并允许重试' },
  { value: 'loading', label: '规则加载中', description: '配置骨架反馈，购买记录仍可查询' },
  { value: 'error', label: '规则加载失败', description: '独立重试配置，不影响购买记录' },
  { value: 'empty', label: '无匹配购买记录', description: '保留筛选并支持清除条件' },
]

export type AdminDisposesScenario = 'default' | 'rewards' | 'penalties' | 'account' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminDisposesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部奖惩记录', description: '奖励、罚款、原因与经办审计记录' },
  { value: 'rewards', label: '仅查看奖励', description: '筛选正向钱包记账及对应原因' },
  { value: 'penalties', label: '仅查看罚款', description: '筛选负向钱包记账及对应原因' },
  { value: 'account', label: '指定陪玩账户', description: '查看单一陪玩账户并支持清除范围' },
  { value: 'loading', label: '记录加载中', description: '筛选与奖惩明细骨架反馈' },
  { value: 'error', label: '记录加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配记录', description: '保留筛选并支持恢复全部记录' },
  { value: 'refresh-error', label: '刷新失败保留台账', description: '保留上次成功读取的奖惩记录' },
]

export type AdminAuditionLinksScenario = 'default' | 'readonly' | 'edit-only' | 'save-review' | 'binding-review' | 'binding-search-error' | 'placeholder' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminAuditionLinksScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部试音活动', description: '双入口、有效期、账号绑定与活动管理' },
  { value: 'readonly', label: '仅查看权限', description: '不显示活动编辑和账号绑定操作' },
  { value: 'edit-only', label: '仅活动编辑权限', description: '可新增编辑活动，不可删除或管理账号绑定' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单后核对活动，避免重复新建' },
  { value: 'binding-review', label: '绑定结果待核对', description: '读取当前绑定后解除操作限制' },
  { value: 'binding-search-error', label: '账号搜索失败', description: '保留绑定弹窗并支持独立重试' },
  { value: 'placeholder', label: '入口域名待配置', description: '显示部署配置提醒并阻止复制无效链接' },
  { value: 'loading', label: '试音活动加载中', description: '页面与活动列表骨架反馈' },
  { value: 'error', label: '试音活动加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配试音活动', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的活动与绑定快照' },
]

export type AdminAuditionSignupsScenario = 'default' | 'approved' | 'rejected' | 'readonly' | 'review' | 'conflict' | 'read-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminAuditionSignupsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部试音报名', description: '报名资料、审核状态与待办处理' },
  { value: 'approved', label: '已通过报名', description: '核对审核人、审核时间与历史资料' },
  { value: 'rejected', label: '已驳回报名', description: '查看驳回原因与报名记录' },
  { value: 'readonly', label: '仅查看报名', description: '可查看详情，不显示通过或驳回操作' },
  { value: 'review', label: '审核结果待核对', description: '读取当前状态后解除审核限制' },
  { value: 'conflict', label: '报名状态冲突', description: '他人已审核时要求查看最新状态' },
  { value: 'read-error', label: '报名读取失败', description: '审核前读取失败，不产生状态变更' },
  { value: 'loading', label: '报名加载中', description: '筛选与报名表格骨架反馈' },
  { value: 'error', label: '报名加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配报名', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的报名快照' },
]

export type AdminWithdrawScenario = 'default' | 'pending' | 'readonly' | 'audit-only' | 'review' | 'conflict' | 'read-error' | 'config-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminWithdrawScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部提现申请', description: '领取队列、收款信息与人工打款登记' },
  { value: 'pending', label: '待审核队列', description: '按提交时间从早到晚处理申请' },
  { value: 'readonly', label: '仅查看权限', description: '可查看申请与凭证，不显示资金操作' },
  { value: 'audit-only', label: '仅审核权限', description: '可领取和审核，不可释放他人领取' },
  { value: 'review', label: '审核结果待核对', description: '核对状态和凭证前锁定全部资金操作' },
  { value: 'conflict', label: '领取状态冲突', description: '审核前发现状态变化，不修改申请' },
  { value: 'read-error', label: '审核前读取失败', description: '状态读取失败，不进入打款登记' },
  { value: 'config-error', label: '提现规则读取失败', description: '独立重试规则，不影响申请列表' },
  { value: 'loading', label: '提现列表加载中', description: '页面与申请表格骨架反馈' },
  { value: 'error', label: '提现列表加载失败', description: '无列表数据时的错误与重试' },
  { value: 'empty', label: '无匹配提现', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次成功读取的申请快照' },
]

export type AdminWalletsScenario = 'default' | 'readonly' | 'review' | 'conflict' | 'read-error' | 'platform-disabled' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminWalletsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '当前资金域台账', description: '按独立钱包页核对账户、充值与资金记录' },
  { value: 'readonly', label: '仅查看权限', description: '可核对钱包和记录，不显示任何资金操作' },
  { value: 'review', label: '充值结果待核对', description: '核对流水与充值记录前锁定全部资金操作' },
  { value: 'conflict', label: '钱包状态冲突', description: '余额或凭据发生变化时不执行资金操作' },
  { value: 'read-error', label: '钱包读取失败', description: '提交前读取失败，不修改余额或流水' },
  { value: 'platform-disabled', label: '平台补贴未开放', description: '保留钱包操作，停用平台线下补入' },
  { value: 'loading', label: '钱包加载中', description: '平台摘要、指标与台账骨架反馈' },
  { value: 'error', label: '钱包加载失败', description: '无资金快照时的错误与重试' },
  { value: 'empty', label: '无匹配钱包', description: '保留筛选并支持清除条件' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次成功数据并锁定资金操作' },
]

export type AdminTransactionsScenario = 'default' | 'wallet' | 'order' | 'reversals' | 'original-error' | 'original-loading' | 'no-order-permission' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminTransactionsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部资金流水', description: '18 类资金变动、余额快照与关联订单' },
  { value: 'wallet', label: '指定钱包流水', description: '仅查看老板钱包 #1001 的收支记录' },
  { value: 'order', label: '指定订单流水', description: '核对订单 SL202611040031 的资金链路' },
  { value: 'reversals', label: '冲正记录', description: '查看冲正流水并读取原记录' },
  { value: 'original-error', label: '原流水读取失败', description: '独立重试原记录，不影响流水列表' },
  { value: 'original-loading', label: '原流水读取中', description: '原记录骨架反馈与读取恢复' },
  { value: 'no-order-permission', label: '无订单查看权限', description: '保留订单号文本，不显示订单跳转' },
  { value: 'loading', label: '流水加载中', description: '摘要、筛选与记录表格骨架反馈' },
  { value: 'error', label: '流水加载失败', description: '无记录快照时的错误与重试' },
  { value: 'empty', label: '无匹配流水', description: '保留筛选并支持恢复全部记录' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次成功读取的资金流水' },
]

export type AdminRechargesScenario = 'default' | 'wallet' | 'trade' | 'proof-loading' | 'proof-error' | 'no-proof' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminRechargesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部充值记录', description: '实收、赠送、到账、收款流水号与经办记录' },
  { value: 'wallet', label: '指定钱包充值', description: '仅查看老板钱包 #1001 的充值与赠送' },
  { value: 'trade', label: '完整流水号查询', description: '按收款流水号精确匹配单笔到账记录' },
  { value: 'proof-loading', label: '凭证读取中', description: '凭证独立加载与读取完成反馈' },
  { value: 'proof-error', label: '凭证读取失败', description: '独立重试凭证，不影响充值台账' },
  { value: 'no-proof', label: '凭证未上传', description: '明确标记待补充，不使用替代图片' },
  { value: 'loading', label: '充值记录加载中', description: '汇总、筛选与记录表格骨架反馈' },
  { value: 'error', label: '充值记录加载失败', description: '无记录快照时的错误与重试' },
  { value: 'empty', label: '无匹配充值记录', description: '保留筛选并支持恢复全部记录' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次成功读取的充值记录' },
]

export type AdminReservationsScenario = 'default' | 'pending' | 'confirmed' | 'converted' | 'readonly' | 'create' | 'create-review' | 'quote-loading' | 'quote-error' | 'insufficient' | 'convert-review' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminReservationsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部预约', description: '六类预约状态、详情与关联订单' },
  { value: 'pending', label: '待确认预约', description: '确认、拒绝、取消与档期释放' },
  { value: 'confirmed', label: '已确认待转单', description: '实时报价、余额核对与正式转单' },
  { value: 'converted', label: '已转正式订单', description: '核对一笔预约对应的关联订单' },
  { value: 'readonly', label: '仅查看权限', description: '隐藏代约、状态变更与转单操作' },
  { value: 'create', label: '代老板预约', description: '双方、服务、档期与游戏资料表单' },
  { value: 'create-review', label: '创建结果待核对', description: '先核对列表，避免重复创建预约' },
  { value: 'quote-loading', label: '实时报价读取中', description: '报价完成前不可确认转单' },
  { value: 'quote-error', label: '实时报价读取失败', description: '独立重试报价，不展示推测金额' },
  { value: 'insufficient', label: '老板余额不足', description: '保留报价明细并阻止转单扣款' },
  { value: 'convert-review', label: '转单结果待核对', description: '重新读取预约与关联订单后解除限制' },
  { value: 'loading', label: '预约列表加载中', description: '汇总、筛选与预约卡片骨架反馈' },
  { value: 'error', label: '预约列表加载失败', description: '无预约快照时的错误与重试' },
  { value: 'empty', label: '当前状态暂无预约', description: '保留状态筛选并支持查看全部' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次成功读取的预约记录' },
]

export type AdminPaymentFeeScenario = 'default' | 'readonly' | 'forbidden' | 'unconfigured' | 'saving' | 'save-error' | 'conflict' | 'save-review' | 'preview-loading' | 'preview-error' | 'history-loading' | 'history-error' | 'history-empty' | 'loading' | 'error' | 'refresh-error'

export const adminPaymentFeeScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '当前费率配置', description: '商户信息、版本化规则、试算与变更记录' },
  { value: 'readonly', label: '仅查看权限', description: '可查看和试算，不显示保存操作' },
  { value: 'forbidden', label: '无费率查看权限', description: '隐藏商户信息与全部配置内容' },
  { value: 'unconfigured', label: '尚未配置费率', description: '首次保存创建版本，保存前不可试算' },
  { value: 'saving', label: '配置保存中', description: '锁定输入并防止重复提交' },
  { value: 'save-error', label: '保存失败保留输入', description: '保留当前修改并支持重试' },
  { value: 'conflict', label: '配置版本冲突', description: '他人已修改时要求刷新核对' },
  { value: 'save-review', label: '保存结果待核对', description: '刷新确认结果，避免重复保存' },
  { value: 'preview-loading', label: '手续费试算中', description: '锁定试算并展示结果骨架' },
  { value: 'preview-error', label: '手续费试算失败', description: '独立重试，不沿用旧结果' },
  { value: 'history-loading', label: '变更记录加载中', description: '历史记录独立加载，不影响配置' },
  { value: 'history-error', label: '变更记录加载失败', description: '独立重试记录，不影响配置与试算' },
  { value: 'history-empty', label: '暂无变更记录', description: '保留当前配置并展示历史空态' },
  { value: 'loading', label: '配置加载中', description: '商户规则与试算模块骨架反馈' },
  { value: 'error', label: '配置加载失败', description: '无配置快照时的错误与重试' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次配置并锁定保存和试算' },
]

export type AdminChatScenario = 'default' | 'unread' | 'readonly' | 'no-order-permission' | 'session-loading' | 'session-error' | 'refresh-error' | 'empty' | 'message-loading' | 'message-error' | 'history-loading' | 'history-error' | 'history-browsing' | 'sending' | 'send-error' | 'pending' | 'pending-unsaved' | 'pending-unknown'

export const adminChatScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '共享会话接待', description: '会话搜索、历史消息、独立草稿与客服回复' },
  { value: 'unread', label: '仅看待回复', description: '筛选团队共享未读会话并核对已读范围' },
  { value: 'readonly', label: '仅查看权限', description: '可查看消息和订单，不可回复或标记已读' },
  { value: 'no-order-permission', label: '无订单查看权限', description: '保留完整订单号文本，但不显示订单跳转' },
  { value: 'session-loading', label: '会话列表加载中', description: '列表骨架反馈，不改变消息已读状态' },
  { value: 'session-error', label: '会话列表加载失败', description: '无会话快照时的错误与独立重试' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留当前会话、消息和独立草稿' },
  { value: 'empty', label: '无匹配会话', description: '保留搜索筛选并支持恢复全部会话' },
  { value: 'message-loading', label: '消息加载中', description: '当前会话消息骨架，列表保持可用' },
  { value: 'message-error', label: '消息加载失败', description: '独立重试消息，不影响其他会话' },
  { value: 'history-loading', label: '历史消息加载中', description: '游标读取反馈并防止重复加载' },
  { value: 'history-error', label: '历史消息加载失败', description: '保留当前消息与草稿并支持重试' },
  { value: 'history-browsing', label: '正在浏览历史', description: '回到最新前不自动标记已读' },
  { value: 'sending', label: '回复发送中', description: '锁定当前回复并防止重复提交' },
  { value: 'send-error', label: '确定发送失败', description: '保留草稿并允许明确重试' },
  { value: 'pending', label: '发送结果待核对', description: '按已保存编号查询结果，避免重复发送' },
  { value: 'pending-unsaved', label: '待核对编号未保存', description: '无法可靠核对时先查看当前消息记录' },
  { value: 'pending-unknown', label: '发送结果仍未知', description: '保留原编号稍后核对，不自动重发' },
]

export type AdminReportsScenario = 'default' | 'pending' | 'approved' | 'rejected' | 'blocked' | 'aftersale' | 'readonly' | 'no-order-permission' | 'review' | 'conflict' | 'detail-loading' | 'detail-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminReportsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部报单', description: '凭证队列、预计收益、订单阻塞与审核详情' },
  { value: 'pending', label: '待审核报单', description: '按提交时间核对入队、结单和战绩截图' },
  { value: 'approved', label: '已通过报单', description: '核对审核快照、结算状态与审核记录' },
  { value: 'rejected', label: '已驳回报单', description: '查看驳回原因与后续补交说明' },
  { value: 'blocked', label: '订单状态待核对', description: '筛选未完成、未支付或退款订单的报单' },
  { value: 'aftersale', label: '售后中的已通过报单', description: '凭证与退款资金分开核对，净收益以流水为准' },
  { value: 'readonly', label: '仅查看权限', description: '可查看凭证和结算快照，不显示审核操作' },
  { value: 'no-order-permission', label: '无订单查看权限', description: '保留订单号文本，不显示订单跳转' },
  { value: 'review', label: '审核结果待核对', description: '重新读取当前状态后再解除审核限制' },
  { value: 'conflict', label: '报单状态冲突', description: '他人已处理时停止重复审核与入账' },
  { value: 'detail-loading', label: '详情更新中', description: '列表保持可用，凭证详情独立加载' },
  { value: 'detail-error', label: '详情更新失败', description: '保留列表快照并支持独立重试详情' },
  { value: 'loading', label: '报单加载中', description: '审核队列与筛选模块骨架反馈' },
  { value: 'error', label: '报单加载失败', description: '无列表快照时的错误与重新加载' },
  { value: 'empty', label: '无匹配报单', description: '保留筛选并支持恢复全部报单' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留当前列表与详情，不执行重复审核' },
]

export type AdminServicesScenario = 'default' | 'readonly' | 'edit-only' | 'metadata-loading' | 'metadata-error' | 'save-error' | 'save-review' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminServicesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部服务商品', description: '商品筛选、类目配置与新增编辑' },
  { value: 'readonly', label: '仅查看权限', description: '可核对商品，不显示新增、编辑或删除' },
  { value: 'edit-only', label: '可编辑不可删除', description: '开放商品与类目配置，隐藏删除操作' },
  { value: 'metadata-loading', label: '配置字典读取中', description: '保留商品列表，锁定新增与编辑' },
  { value: 'metadata-error', label: '配置字典读取失败', description: '独立重试字典，不展示不可靠编辑选项' },
  { value: 'save-error', label: '确定保存失败', description: '保留商品输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询商品，避免重复保存' },
  { value: 'loading', label: '商品加载中', description: '商品摘要、筛选与列表骨架反馈' },
  { value: 'error', label: '商品加载失败', description: '无可靠快照时不展示推测商品' },
  { value: 'empty', label: '无匹配商品', description: '保留筛选并支持恢复全部商品' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次商品快照并支持重新加载' },
]

export type AdminGameplaysScenario = 'default' | 'archived' | 'readonly' | 'edit-only' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminGameplaysScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '当前玩法目录', description: '玩法筛选、主题封面、新增编辑与商品配置' },
  { value: 'archived', label: '已归档玩法', description: '核对历史目录并恢复到停用状态' },
  { value: 'readonly', label: '仅查看权限', description: '可查看目录与商品，不显示编辑、归档或恢复' },
  { value: 'edit-only', label: '可编辑不可归档', description: '开放玩法编辑与恢复，隐藏归档操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留玩法输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询玩法，避免重复保存' },
  { value: 'action-error', label: '归档或恢复失败', description: '保留当前目录并提供明确失败反馈' },
  { value: 'loading', label: '玩法加载中', description: '目录摘要、筛选与列表骨架反馈' },
  { value: 'error', label: '玩法加载失败', description: '无可靠快照时不展示推测目录' },
  { value: 'empty', label: '无匹配玩法', description: '保留筛选并支持恢复当前目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次玩法快照并支持重新加载' },
]

export type AdminBannersScenario = 'default' | 'unavailable' | 'readonly' | 'edit-only' | 'target-error' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminBannersScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部首页 Banner', description: '轮播筛选、目标核对、新增编辑与启停' },
  { value: 'unavailable', label: '目标需核对', description: '筛选目标已下架、停用或缺失的投放' },
  { value: 'readonly', label: '仅查看权限', description: '可查看轮播与目标，不显示编辑、启停或删除' },
  { value: 'edit-only', label: '可编辑不可删除', description: '开放轮播编辑与启停，隐藏删除操作' },
  { value: 'target-error', label: '目标字典读取失败', description: '保留当前目标并支持独立重试' },
  { value: 'save-error', label: '确定保存失败', description: '保留 Banner 输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询 Banner，避免重复保存' },
  { value: 'action-error', label: '启停或删除失败', description: '保留当前列表并提供明确失败反馈' },
  { value: 'loading', label: '轮播加载中', description: '轮播摘要、筛选与列表骨架反馈' },
  { value: 'error', label: '轮播加载失败', description: '无可靠快照时不展示推测投放' },
  { value: 'empty', label: '无匹配 Banner', description: '保留筛选并支持恢复轮播列表' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次轮播快照并锁定写操作' },
]

export type AdminAnnouncementsScenario = 'default' | 'pinned' | 'readonly' | 'edit-only' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminAnnouncementsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部平台公告', description: '公告筛选、正文阅读、新增编辑与启停' },
  { value: 'pinned', label: '置顶公告', description: '核对优先进入老板首页的公告顺序' },
  { value: 'readonly', label: '仅查看权限', description: '可阅读公告，不显示编辑、启停或删除' },
  { value: 'edit-only', label: '可编辑不可删除', description: '开放公告编辑与启停，隐藏删除操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留公告输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询公告，避免重复保存' },
  { value: 'action-error', label: '启停或删除失败', description: '保留当前公告并提供明确失败反馈' },
  { value: 'loading', label: '公告加载中', description: '公告摘要、筛选与列表骨架反馈' },
  { value: 'error', label: '公告加载失败', description: '无可靠快照时不展示推测公告' },
  { value: 'empty', label: '无匹配公告', description: '保留筛选并支持恢复公告列表' },
  { value: 'refresh-error', label: '刷新失败保留列表', description: '保留上次公告快照并锁定写操作' },
]

export type AdminContentsScenario = 'default' | 'activities' | 'readonly' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminContentsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部运营内容', description: '内容筛选、海报预览、新增编辑与启停' },
  { value: 'activities', label: '活动与历史归档', description: '核对当前、待上架及已结束活动的展示状态' },
  { value: 'readonly', label: '仅查看权限', description: '可阅读内容，不显示新增、编辑、启停或删除' },
  { value: 'save-error', label: '确定保存失败', description: '保留内容输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询内容，避免重复保存' },
  { value: 'action-error', label: '启停或删除失败', description: '保留当前内容并提供明确失败反馈' },
  { value: 'loading', label: '内容加载中', description: '内容摘要、筛选与目录骨架反馈' },
  { value: 'error', label: '内容加载失败', description: '无可靠快照时不展示推测内容' },
  { value: 'empty', label: '无匹配运营内容', description: '保留筛选并支持恢复内容目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次内容快照并锁定写操作' },
]

export type AdminBroadcastRulesScenario = 'default' | 'readonly' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminBroadcastRulesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部播报规则', description: '固定事件、模板、展示时长、脱敏与启停配置' },
  { value: 'readonly', label: '仅查看权限', description: '可阅读规则，不显示配置或启停操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留规则输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询规则，避免重复保存' },
  { value: 'action-error', label: '启停失败', description: '保留当前规则状态并提供明确失败反馈' },
  { value: 'loading', label: '规则加载中', description: '播报摘要、筛选与规则目录骨架反馈' },
  { value: 'error', label: '规则加载失败', description: '无可靠快照时不展示推测规则' },
  { value: 'empty', label: '无匹配播报规则', description: '保留筛选并支持恢复固定规则目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次规则快照并锁定写操作' },
]

export type AdminBroadcastRecordsScenario = 'default' | 'readonly' | 'save-error' | 'save-review' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminBroadcastRecordsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部播报记录', description: '历史动态、公开状态、前台显示与截止时间管理' },
  { value: 'readonly', label: '仅查看权限', description: '可阅读记录，不显示配置或前台显示操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留记录输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询记录，避免重复保存' },
  { value: 'action-error', label: '前台显示更新失败', description: '保留当前记录状态并提供明确失败反馈' },
  { value: 'loading', label: '记录加载中', description: '播报摘要、筛选与历史目录骨架反馈' },
  { value: 'error', label: '记录加载失败', description: '无可靠快照时不展示推测记录' },
  { value: 'empty', label: '无匹配播报记录', description: '保留筛选并支持恢复历史动态目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次记录快照并锁定写操作' },
]

export type AdminRewardsScenario = 'default' | 'readonly' | 'edit-only' | 'save-error' | 'save-review' | 'stock-conflict' | 'lottery-conflict' | 'action-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminRewardsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部积分好礼', description: '七类好礼、库存、交付权益与商城状态管理' },
  { value: 'readonly', label: '仅查看权限', description: '可核对商品，不显示新增、编辑或删除' },
  { value: 'edit-only', label: '可编辑不可删除', description: '开放新增和编辑，隐藏删除操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留好礼输入并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询好礼，避免重复保存' },
  { value: 'stock-conflict', label: '库存记录冲突', description: '兑换数量变化时要求刷新并重新核对' },
  { value: 'lottery-conflict', label: '启用抽奖活动占用', description: '暂停关联活动前阻止修改奖品' },
  { value: 'action-error', label: '删除失败', description: '保留当前好礼并提供明确失败反馈' },
  { value: 'loading', label: '好礼加载中', description: '商品摘要、筛选与目录骨架反馈' },
  { value: 'error', label: '好礼加载失败', description: '无可靠快照时不展示推测商品' },
  { value: 'empty', label: '无匹配好礼', description: '保留筛选并支持恢复完整目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次商品快照并锁定写操作' },
]

export type AdminLotteriesScenario = 'default' | 'readonly' | 'save-error' | 'save-review' | 'version-conflict' | 'metadata-loading' | 'metadata-error' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminLotteriesScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部抽奖活动', description: '活动版本、完整奖池、十连保障与体验单保底' },
  { value: 'readonly', label: '仅查看权限', description: '可核对活动与概率，不显示新增或编辑操作' },
  { value: 'save-error', label: '确定保存失败', description: '保留完整活动与奖池输入，允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '关闭表单查询活动版本，避免重复保存' },
  { value: 'version-conflict', label: '活动版本冲突', description: '他人已修改时保留输入并要求刷新核对' },
  { value: 'metadata-loading', label: '奖品字典读取中', description: '保留活动快照，读取完成前锁定编辑' },
  { value: 'metadata-error', label: '奖品字典读取失败', description: '独立重试字典，不使用不可靠奖品选项' },
  { value: 'loading', label: '抽奖活动加载中', description: '活动摘要、规则核对与目录骨架反馈' },
  { value: 'error', label: '抽奖活动加载失败', description: '无可靠快照时不展示推测概率或保底' },
  { value: 'empty', label: '无匹配抽奖活动', description: '保留筛选并支持恢复完整活动目录' },
  { value: 'refresh-error', label: '刷新失败保留目录', description: '保留上次活动快照并锁定写操作' },
]

export type AdminClaimsScenario = 'default' | 'tag-review' | 'tag-suspend' | 'readonly' | 'save-error' | 'save-review' | 'details-conflict' | 'status-conflict' | 'terms-conflict' | 'loading' | 'error' | 'empty' | 'refresh-error'

export const adminClaimsScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '全部权益交付记录', description: '交付队列、组合筛选、历史快照与处理详情' },
  { value: 'tag-review', label: '历史 TAG 待复核', description: '已完成但无有效审核记录，确认后恢复展示' },
  { value: 'tag-suspend', label: '暂停已审核 TAG', description: '填写暂停原因，保留权益并进入待复核' },
  { value: 'readonly', label: '仅查看权限', description: '可核对交付资料，不显示状态或 TAG 操作' },
  { value: 'save-error', label: '确定处理失败', description: '保留处理结果与说明，允许修正后重试' },
  { value: 'save-review', label: '处理结果待核对', description: '查询当前记录后解除限制，避免重复交付' },
  { value: 'details-conflict', label: '交付资料冲突', description: '资料已更新时保留输入并要求重新核对' },
  { value: 'status-conflict', label: '交付状态冲突', description: '他人已处理时停止重复状态流转' },
  { value: 'terms-conflict', label: '权益规格冲突', description: '时长或高亮类型变化时要求重新核对' },
  { value: 'loading', label: '交付记录加载中', description: '摘要、筛选和交付队列表格骨架反馈' },
  { value: 'error', label: '交付记录加载失败', description: '无可靠快照时不展示推测资料或状态' },
  { value: 'empty', label: '无匹配交付记录', description: '保留筛选并支持恢复完整交付队列' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次记录并锁定交付和 TAG 操作' },
]

export type AdminOperationConfigScenario = 'default' | 'readonly' | 'saving' | 'save-error' | 'save-review' | 'loading' | 'error' | 'refresh-error'

export const adminOperationConfigScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '当前运营配置', description: '消费积分、生日倍率、VIP 与赛季设置' },
  { value: 'readonly', label: '仅查看权限', description: '可读取配置与试算，不显示保存或重置操作' },
  { value: 'saving', label: '配置保存中', description: '锁定全部输入并防止重复提交' },
  { value: 'save-error', label: '确定保存失败', description: '保留当前修改并允许修正后重试' },
  { value: 'save-review', label: '保存结果待核对', description: '重新读取当前配置，避免重复保存' },
  { value: 'loading', label: '配置加载中', description: '运营摘要、配置表单与试算骨架反馈' },
  { value: 'error', label: '配置加载失败', description: '无可靠快照时不展示推测配置' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '保留上次配置并锁定保存和重置' },
]

export type AdminCommissionScenario = 'default' | 'fixed' | 'readonly' | 'forbidden' | 'loading' | 'error' | 'save-error' | 'save-review' | 'refresh-error' | 'preview-loading' | 'preview-error'

export const adminCommissionScenarioOptions: ScenarioOption[] = [
  { value: 'default', label: '百分比默认抽成', description: '全局配置、规则优先级与单陪／双陪分账试算' },
  { value: 'fixed', label: '整单固定抽成', description: '固定金额只扣一次，旧报单仍保留默认比例' },
  { value: 'readonly', label: '仅查看权限', description: '可读取与试算，不显示保存操作' },
  { value: 'forbidden', label: '无配置查看权限', description: '隐藏比例、固定金额和分账试算' },
  { value: 'loading', label: '配置加载中', description: '独立显示配置与试算区域骨架' },
  { value: 'error', label: '配置加载失败', description: '没有可靠快照时不展示推测配置' },
  { value: 'save-error', label: '确定保存失败', description: '保留当前输入并允许明确重试' },
  { value: 'save-review', label: '保存结果待核对', description: '重新读取当前配置，避免重复提交' },
  { value: 'refresh-error', label: '刷新失败保留快照', description: '锁定编辑和试算，保留上次读取配置' },
  { value: 'preview-loading', label: '分账试算中', description: '试算独立加载，不影响配置读取' },
  { value: 'preview-error', label: '分账试算失败', description: '保留输入并支持独立重试试算' },
]
