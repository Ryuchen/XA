import {
  ArrowLeftRight,
  Award,
  Banknote,
  Bell,
  Boxes,
  CalendarClock,
  ClipboardCheck,
  ContactRound,
  CreditCard,
  Dices,
  FileCheck2,
  FileText,
  Gift,
  Headphones,
  Headset,
  Images,
  LayoutDashboard,
  ListTree,
  Megaphone,
  MessageSquareText,
  PackageCheck,
  Percent,
  Scale,
  Settings2,
  Store,
  UserPlus,
  UsersRound,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'

export type AdminNavigablePage = 'stores' | 'dashboard' | 'orders' | 'reservations' | 'chat' | 'reports' | 'commission' | 'services' | 'gameplays' | 'banners' | 'announcements' | 'contents' | 'broadcast-rules' | 'broadcast-records' | 'rewards' | 'lotteries' | 'claims' | 'operation-config' | 'bosses' | 'providers' | 'registrations' | 'levels' | 'passes' | 'disposes' | 'auditions' | 'audition-signups' | 'withdraw' | 'wallets' | 'provider-wallets' | 'transactions' | 'recharges' | 'payment-fee'
export type AdminDetailPage = 'order-detail' | 'provider-detail' | 'registration-detail'
export type AdminShellPage = AdminNavigablePage | AdminDetailPage

export interface AdminNavItem {
  page: AdminNavigablePage
  label: string
  icon: LucideIcon
  aliases?: string[]
}

export interface AdminNavGroup {
  id: string
  label: string
  section: '门店业务' | '运营配置'
  icon: LucideIcon
  items: AdminNavItem[]
  primary?: boolean
}

export const adminStandaloneItems: AdminNavItem[] = [
  { page: 'stores', label: '平台门店管理', icon: Store, aliases: ['门店管理', '租户管理'] },
  { page: 'dashboard', label: '运营工作台', icon: LayoutDashboard, aliases: ['工作台', '经营看板'] },
]

export const adminNavigationGroups: AdminNavGroup[] = [
  {
    id: 'fulfillment',
    label: '订单履约',
    section: '门店业务',
    icon: Headphones,
    items: [
      { page: 'orders', label: '订单与派单', icon: Headphones, aliases: ['订单管理', '客服待派', '售后订单'] },
      { page: 'reservations', label: '预约与转单', icon: CalendarClock, aliases: ['预约管理', '代老板预约'] },
      { page: 'chat', label: '会话接待', icon: MessageSquareText, aliases: ['客服会话', '共享会话'] },
      { page: 'reports', label: '报单审核', icon: FileCheck2, aliases: ['履约凭证', '报单'] },
    ],
  },
  {
    id: 'bosses',
    label: '老板管理',
    section: '门店业务',
    icon: ContactRound,
    items: [
      { page: 'bosses', label: '老板档案', icon: ContactRound, aliases: ['老板管理', '老板账号', '客户管理', '会员管理'] },
    ],
  },
  {
    id: 'providers',
    label: '陪玩管理',
    section: '门店业务',
    icon: UsersRound,
    primary: false,
    items: [
      { page: 'providers', label: '陪玩档案', icon: UsersRound, aliases: ['陪玩管理'] },
      { page: 'registrations', label: '注册审核', icon: UserPlus, aliases: ['陪玩开户申请'] },
      { page: 'auditions', label: '试音招募', icon: Headset, aliases: ['招募链接', '试音入口'] },
      { page: 'audition-signups', label: '试音审核', icon: ClipboardCheck, aliases: ['试音报名'] },
      { page: 'levels', label: '陪玩等级', icon: Award, aliases: ['等级配置'] },
      { page: 'passes', label: '通行证', icon: CreditCard, aliases: ['陪玩权益'] },
      { page: 'disposes', label: '奖惩记录', icon: Scale, aliases: ['奖罚明细', '陪玩奖惩'] },
    ],
  },
  {
    id: 'finance',
    label: '资金管理',
    section: '门店业务',
    icon: WalletCards,
    items: [
      { page: 'wallets', label: '老板钱包', icon: WalletCards, aliases: ['老板资金', '老板钱包台账', '客服充值'] },
      { page: 'provider-wallets', label: '陪玩钱包', icon: WalletCards, aliases: ['陪玩资金', '陪玩钱包台账', '陪玩押金'] },
      { page: 'withdraw', label: '提现审核', icon: Banknote, aliases: ['提现申请', '人工打款'] },
      { page: 'recharges', label: '充值记录', icon: CreditCard, aliases: ['充值与赠送', '收款凭证'] },
      { page: 'transactions', label: '资金流水', icon: ArrowLeftRight, aliases: ['流水记录', '交易明细'] },
    ],
  },
  {
    id: 'catalog',
    label: '商品与玩法',
    section: '运营配置',
    icon: Boxes,
    items: [
      { page: 'services', label: '服务商品', icon: Boxes, aliases: ['商品管理', '游玩项目'] },
      { page: 'gameplays', label: '玩法配置', icon: ListTree, aliases: ['玩法目录', '玩法管理'] },
    ],
  },
  {
    id: 'content',
    label: '内容与播报',
    section: '运营配置',
    icon: FileText,
    items: [
      { page: 'banners', label: '首页轮播', icon: Images, aliases: ['Banner', '运营位'] },
      { page: 'announcements', label: '公告管理', icon: Bell, aliases: ['平台公告', '官方公告'] },
      { page: 'contents', label: '运营内容', icon: FileText, aliases: ['内容管理', '俱乐部活动', '老板权益'] },
      { page: 'broadcast-rules', label: '播报规则', icon: Megaphone, aliases: ['俱乐部动态', '动态模板'] },
      { page: 'broadcast-records', label: '播报记录', icon: ListTree, aliases: ['历史动态', '动态台账'] },
    ],
  },
  {
    id: 'marketing',
    label: '营销与权益',
    section: '运营配置',
    icon: Gift,
    items: [
      { page: 'rewards', label: '积分好礼', icon: Gift, aliases: ['积分商城', '兑换商品'] },
      { page: 'lotteries', label: '幸运转盘', icon: Dices, aliases: ['抽奖活动', '奖池配置'] },
      { page: 'claims', label: '权益交付', icon: PackageCheck, aliases: ['交付记录', '奖品交付'] },
    ],
  },
  {
    id: 'settings',
    label: '门店设置',
    section: '运营配置',
    icon: Settings2,
    items: [
      { page: 'operation-config', label: '运营设置', icon: Settings2, aliases: ['消费积分', '赛季配置'] },
      { page: 'commission', label: '订单抽成', icon: Percent, aliases: ['抽成配置', '订单默认抽成', '默认抽成'] },
      { page: 'payment-fee', label: '微信支付费率', icon: Percent, aliases: ['支付手续费', '商户费率'] },
    ],
  },
]

export const adminAllNavItems = [...adminStandaloneItems, ...adminNavigationGroups.flatMap(group => group.items)]

export const adminDetailParents: Record<AdminDetailPage, AdminNavigablePage> = {
  'order-detail': 'orders',
  'provider-detail': 'providers',
  'registration-detail': 'registrations',
}

export const adminPageLabels: Record<AdminShellPage, string> = {
  ...Object.fromEntries(adminAllNavItems.map(item => [item.page, item.label])),
  'order-detail': '订单详情',
  'provider-detail': '陪玩档案详情',
  'registration-detail': '注册申请详情',
} as Record<AdminShellPage, string>

export function adminPathFor(page: AdminNavigablePage) {
  return `/admin-${page}`
}

export function adminActivePage(page: AdminShellPage): AdminNavigablePage {
  return page in adminDetailParents ? adminDetailParents[page as AdminDetailPage] : page as AdminNavigablePage
}

export function adminGroupForPage(page: AdminShellPage) {
  const activePage = adminActivePage(page)
  return adminNavigationGroups.find(group => group.items.some(item => item.page === activePage))
}

export function adminPageForPath(pathname: string): AdminShellPage {
  const page = pathname.replace('/admin-', '') as AdminShellPage
  return page in adminPageLabels ? page : 'dashboard'
}
