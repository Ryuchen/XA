const f = {
  orders: [['orders', '订单与派单'], ['reservations', '预约管理']],
  fulfillment: [['reports', '报单审核'], ['evaluations', '订单评价']],
  providerRecords: [['escorts', '陪玩档案'], ['player-dashboard', '陪玩业绩']],
  recruitment: [['audition-links', '试音招募'], ['audition-signups', '试音审核'], ['provider-registrations', '入驻审核']],
  providerBenefits: [['escort-levels', '等级配置'], ['provider-passes', '通行证']],
  providerRules: [['deposit-rules', '押金与结款规则'], ['dispose-records', '奖惩记录']],
  customerRecords: [['users', '老板资料'], ['boss-types', '老板分级']],
  vip: [['club-operations', '积分礼品与会员']],
  customerActivities: [['achievements', '成就配置'], ['checkin', '签到打卡']],
  memberRetention: [['club-operations', '积分礼品与会员'], ['achievements', '成就配置'], ['checkin', '签到打卡']],
  products: [['service-items', '服务商品'], ['gameplays', '玩法配置']],
  marketingContent: [['coupons', '优惠券'], ['promotions', '促销活动'], ['banners', '首页轮播'], ['announcements', '公告管理']],
  campaigns: [['coupons', '优惠券'], ['promotions', '促销活动']],
  content: [['banners', '首页轮播'], ['announcements', '公告管理']],
  support: [['chat', '会话接待'], ['support-cards', '客服名片']],
  supportAll: [['chat', '会话接待'], ['support-cards', '客服名片'], ['messages', '站内消息']],
  messages: [['messages', '站内消息']],
  accounts: [['wallets', '钱包账户'], ['recharge-records', '充值记录'], ['transactions', '资金流水'], ['withdrawals', '提现审核'], ['payment-fee', '微信支付费率']],
  walletRecords: [['wallets', '钱包账户'], ['recharge-records', '充值记录'], ['transactions', '资金流水']],
  payout: [['withdrawals', '提现审核'], ['payment-fee', '微信支付费率']],
  payroll: [['salary-plans', '岗位薪资方案'], ['salary-assignments', '员工薪资安排'], ['salary-activities', '业绩归属核实'], ['payroll', '月度薪资核算']],
  analysis: [['operating-costs', '每日投流与成本'], ['operating-roi', '经营 ROI']],
  storeRules: [['kook-dispatch', '群派单配置'], ['dictionaries', '统一字典']],
  members: [['admins', '后台成员'], ['roles', '角色权限']],
  audit: [['audit-logs', '操作审计日志']],
}
const globals = [
  { id: 'overview', title: '运营工作台', icon: 'grid', features: [['dashboard', '运营工作台']] },
  { id: 'platform', title: '平台管理', icon: 'store', features: [['stores', '门店管理']] },
]
function d(id, title, icon, centers, section = '业务管理') {
  return { id, title, icon, section, centers: centers.map(([centerId, centerTitle, features]) => ({ id: `${id}-${centerId}`, title: centerTitle, features })) }
}
const orderModule = () => d('orders', '订单管理', 'order', [
  ['processing', '订单处理', f.orders],
  ['aftersales', '履约与售后', f.fulfillment],
])
const providerModule = () => d('providers', '陪玩管理', 'users', [
  ['records', '陪玩资料', f.providerRecords],
  ['recruitment', '招募审核', f.recruitment],
  ['benefits', '等级与权益', f.providerBenefits],
  ['rules', '押金与奖惩', f.providerRules],
])
const customerModule = () => d('customers', '客户管理', 'member', [
  ['records', '客户资料', f.customerRecords],
  ['vip', '会员管理', f.vip],
  ['activities', '会员活动', f.customerActivities],
])
const supportModule = () => d('support', '客服管理', 'chat', [
  ['service', '客服管理', f.support],
  ['messages', '消息管理', f.messages],
])
const systemModule = () => d('system', '系统管理', 'settings', [
  ['store', '门店设置', f.storeRules],
  ['permissions', '权限管理', f.members],
  ['audit', '日志管理', f.audit],
], '系统与配置')
const payrollCenter = ['payroll', '薪资管理', f.payroll]
const analysisModule = () => d('analytics', '经营分析', 'grid', [['reports', '经营报表', f.analysis]])

module.exports = {
  globals,
  directions: [
    {
      key: 'A', name: '标准功能模块', defaultDomain: 'orders', defaultPath: 'orders',
      summary: '8 个业务模块：商品与营销相邻，财务包含薪资，经营分析独立。',
      domains: [
        orderModule(), providerModule(), customerModule(),
        d('catalog-marketing', '商品与营销', 'gift', [
          ['catalog', '商品管理', f.products],
          ['marketing', '营销与内容', f.marketingContent],
        ]),
        supportModule(),
        d('finance', '财务管理', 'wallet', [
          ['accounts', '资金管理', f.accounts], payrollCenter,
        ]),
        analysisModule(), systemModule(),
      ],
    },
    {
      key: 'B', name: '业务归类模块', defaultDomain: 'orders-support', defaultPath: 'orders',
      summary: '6 个业务模块：订单与客服、客户与营销相邻，财务与经营统一。',
      domains: [
        d('orders-support', '订单与客服', 'order', [
          ['processing', '订单处理', f.orders],
          ['aftersales', '履约与售后', f.fulfillment],
          ['support', '客服与消息', f.supportAll],
        ]),
        providerModule(),
        d('customer-marketing', '客户与营销', 'member', [
          ['records', '客户资料', f.customerRecords],
          ['retention', '会员管理', f.memberRetention],
          ['campaigns', '优惠与促销', f.campaigns],
          ['content', '内容管理', f.content],
        ]),
        d('catalog', '商品管理', 'gift', [['catalog', '商品管理', f.products]]),
        d('finance', '财务与经营', 'wallet', [
          ['accounts', '资金管理', f.accounts], payrollCenter, ['analysis', '经营分析', f.analysis],
        ]),
        systemModule(),
      ],
    },
    {
      key: 'C', name: '细分业务模块', defaultDomain: 'orders', defaultPath: 'orders',
      summary: '10 个业务模块：商品、营销、财务、薪资和经营分析各自独立。',
      domains: [
        orderModule(), providerModule(), customerModule(),
        d('catalog', '商品管理', 'gift', [['catalog', '商品管理', f.products]]),
        d('marketing', '营销管理', 'gift', [
          ['campaigns', '活动管理', f.campaigns], ['content', '内容管理', f.content],
        ]),
        supportModule(),
        d('finance', '财务管理', 'wallet', [
          ['records', '资金流水', f.walletRecords], ['payout', '提现与费率', f.payout],
        ]),
        d('payroll', '薪资管理', 'wallet', [['payroll', '薪资核算', f.payroll]]),
        analysisModule(), systemModule(),
      ],
    },
  ],
}
