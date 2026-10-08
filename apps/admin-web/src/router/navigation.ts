/** Business navigation is separate from URLs and authorization. Routes remain
 * the source of permission checks, including pages nested inside a center. */
export type MenuPermission = string | string[] | undefined
export type PermissionCheck = (permission: MenuPermission) => boolean
export interface MenuRoute {
  path: string
  meta?: Record<string, unknown>
}
export interface FeatureDefinition {
  path: string
  title: string
  keywords?: string[]
}
export interface CenterDefinition {
  id: string
  title: string
  icon: string
  features: FeatureDefinition[]
}
export interface DomainDefinition {
  id: string
  title: string
  centers: CenterDefinition[]
}
export interface NavigationCenter extends Omit<CenterDefinition, 'features'> {
  features: FeatureDefinition[]
}
export interface NavigationDomain extends Omit<DomainDefinition, 'centers'> {
  centers: NavigationCenter[]
}
export interface SearchFeature extends FeatureDefinition {
  domain: string
  center: string
}

export const navigationDomains: DomainDefinition[] = [
  { id: 'overview', title: '工作台', centers: [
    { id: 'overview', title: '数据概览', icon: 'Odometer', features: [
      { path: 'dashboard', title: '平台概览', keywords: ['平台总览', '统计', '营收'] },
      { path: 'player-dashboard', title: '陪玩业绩', keywords: ['陪玩概览', '排行', '数据分析'] },
    ] },
  ] },
  { id: 'orders', title: '订单履约', centers: [
    { id: 'orders', title: '订单中心', icon: 'Tickets', features: [
      { path: 'orders', title: '订单与派单', keywords: ['派单管理', '抢单', '指派', '转单', '退款', '售后'] },
      { path: 'reports', title: '报单审核', keywords: ['订单审核', '结单', '完成凭证'] },
      { path: 'evaluations', title: '订单评价', keywords: ['评分', '评价回复'] },
      { path: 'kook-dispatch', title: '群派单配置', keywords: ['KOOK', '机器人', '频道', '派单设置'] },
    ] },
  ] },
  { id: 'people', title: '老板与陪玩', centers: [
    { id: 'bosses', title: '老板中心', icon: 'User', features: [
      { path: 'users', title: '老板资料', keywords: ['信息管理', '用户', '账号', '手机号', '封禁'] },
      { path: 'boss-types', title: '老板类型', keywords: ['类型管理', '老板折扣'] },
    ] },
    { id: 'providers', title: '陪玩中心', icon: 'Avatar', features: [
      { path: 'escorts', title: '陪玩档案', keywords: ['陪玩管理', '打手', '认证', '封禁', '退押金'] },
      { path: 'provider-registrations', title: '注册审核', keywords: ['入驻', '报名', '身份证', '实名'] },
      { path: 'escort-levels', title: '等级配置', keywords: ['陪玩等级', '抽成'] },
      { path: 'provider-passes', title: '通行证', keywords: ['通行证管理', '陪玩权益'] },
      { path: 'dispose-records', title: '奖惩记录', keywords: ['奖励', '处罚', '罚款'] },
    ] },
    { id: 'recruitment', title: '招募与试音', icon: 'Headset', features: [
      { path: 'audition-links', title: '试音招募', keywords: ['声卡管理', '招募链接', '报名入口'] },
      { path: 'audition-signups', title: '试音审核', keywords: ['试音报名', '招募审核'] },
    ] },
  ] },
  { id: 'finance', title: '财务结算', centers: [
    { id: 'accounts', title: '资金账目', icon: 'Wallet', features: [
      { path: 'wallets', title: '老板钱包', keywords: ['钱包管理', '余额', '充值', '调账'] },
      { path: 'recharge-records', title: '充值记录', keywords: ['账目记录', '充值凭证'] },
      { path: 'transactions', title: '资金流水', keywords: ['流水记录', '收支', '账务'] },
    ] },
    { id: 'settlement', title: '结款与押金', icon: 'Money', features: [
      { path: 'withdrawals', title: '提现审核', keywords: ['打款', '结款', '收款账户'] },
      { path: 'deposit-rules', title: '押金与结款规则', keywords: ['单抵', '现金押金', '比例', '6:4', '60%', '押金设置'] },
    ] },
  ] },
  { id: 'operations', title: '商品运营', centers: [
    { id: 'catalog', title: '商品与玩法', icon: 'Goods', features: [
      { path: 'service-items', title: '服务商品', keywords: ['游玩项目', '套餐', '价目', '商品分类'] },
      { path: 'gameplays', title: '玩法配置', keywords: ['玩法管理', '游戏'] },
    ] },
    { id: 'marketing', title: '优惠营销', icon: 'Sell', features: [
      { path: 'promotions', title: '促销活动', keywords: ['营销管理', '活动价', '折扣'] },
      { path: 'coupons', title: '优惠券', keywords: ['领券', '核销', '折扣券'] },
    ] },
    { id: 'growth', title: '积分与成长', icon: 'Present', features: [
      { path: 'club-operations', title: '积分礼品与会员', keywords: ['积分礼品', 'VIP', '会员权益', '预存', '转盘', '生日', 'KOOK积分', '冠名'] },
      { path: 'achievements', title: '成就配置', keywords: ['成就', '成长', '消费目标'] },
      { path: 'checkin', title: '签到打卡', keywords: ['全勤', '补签', '签到奖励'] },
    ] },
    { id: 'content', title: '内容发布', icon: 'Picture', features: [
      { path: 'banners', title: '首页轮播', keywords: ['横幅', '首页图片', '轮播图'] },
      { path: 'announcements', title: '公告管理', keywords: ['公告', '通知发布'] },
    ] },
  ] },
  { id: 'support', title: '客服消息', centers: [
    { id: 'support', title: '客服工作台', icon: 'ChatLineRound', features: [
      { path: 'chat', title: '会话接待', keywords: ['消息与客服', '在线客服', '聊天', '回复'] },
      { path: 'support-cards', title: '客服名片', keywords: ['联系入口', '微信客服', '企微'] },
    ] },
    { id: 'messages', title: '站内消息', icon: 'ChatDotRound', features: [
      { path: 'messages', title: '站内消息', keywords: ['消息推送', '通知', '收件人'] },
    ] },
  ] },
  { id: 'system', title: '系统管理', centers: [
    { id: 'members', title: '成员与权限', icon: 'Lock', features: [
      { path: 'admins', title: '后台成员', keywords: ['客服信息管理', '管理员', '客服账号'] },
      { path: 'roles', title: '角色权限', keywords: ['职位权限管理', '角色', '职位', '授权'] },
    ] },
    { id: 'audit', title: '操作日志', icon: 'Document', features: [
      { path: 'audit-logs', title: '操作审计日志', keywords: ['审计', '操作记录', '追溯'] },
    ] },
  ] },
]

export function applyNavigationMeta(routes: MenuRoute[]) {
  for (const domain of navigationDomains) {
    for (const center of domain.centers) {
      for (const feature of center.features) {
        const route = routes.find(item => item.path === feature.path)
        if (!route) throw new Error(`Navigation route missing: ${feature.path}`)
        route.meta = { ...route.meta, title: feature.title, group: domain.title, center: center.id }
      }
    }
  }
}

export function buildNavigation(routes: MenuRoute[], hasPerm: PermissionCheck): NavigationDomain[] {
  return navigationDomains.map(domain => ({
    ...domain,
    centers: domain.centers.map(center => ({
      ...center,
      features: center.features.filter(feature => {
        const route = routes.find(item => item.path === feature.path)
        return !!route && !route.meta?.hidden && hasPerm(route.meta?.perm as MenuPermission)
      }),
    })).filter(center => center.features.length),
  })).filter(domain => domain.centers.length)
}

export function flattenNavigation(domains: NavigationDomain[]): SearchFeature[] {
  return domains.flatMap(domain => domain.centers.flatMap(center => center.features.map(feature => ({
    ...feature, domain: domain.title, center: center.title,
  }))))
}

export function searchNavigation(domains: NavigationDomain[], query: string): SearchFeature[] {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  const features = flattenNavigation(domains)
  if (!terms.length) return features
  return features.filter(feature => {
    const text = [feature.title, feature.domain, feature.center, feature.path, ...(feature.keywords || [])]
      .join(' ').toLocaleLowerCase()
    return terms.every(term => text.includes(term))
  }).sort((a, b) => {
    const rank = (item: SearchFeature) => item.title.toLocaleLowerCase() === query.trim().toLocaleLowerCase() ? 0
      : terms.every(term => item.title.toLocaleLowerCase().includes(term)) ? 1 : 2
    return rank(a) - rank(b)
  })
}
