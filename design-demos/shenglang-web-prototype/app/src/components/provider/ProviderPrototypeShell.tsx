import type { ReactNode } from 'react'
import { Award, CalendarClock, Check, ChevronRight, CircleUserRound, ClipboardCheck, Coins, FileCheck2, Gamepad2, Headphones, LayoutDashboard, LockKeyhole, LogIn, MessageSquareText, MonitorSmartphone, Moon, ShieldCheck, Smartphone, Star, Sun, TicketCheck, UserRound, UserRoundPlus, WalletCards } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router'
import { ViewportPreview } from '@/components/prototype/ViewportPreview'
import { StoreDesignPanel } from '@/components/store/StoreDesignPanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { providerScenarioOptions, type ProviderScenario } from '@/pages/provider/provider-data'

const groups = [
  { label: '账号入口', items: [
    { path: '/provider-login', label: '陪玩登录', icon: LogIn },
    { path: '/provider-register', label: '入驻申请与审核', icon: UserRoundPlus },
  ] },
  { label: '履约工作区', items: [
    { path: '/provider', label: '接单工作台', icon: ClipboardCheck },
    { path: '/provider/applications', label: '我的报名', icon: ClipboardCheck },
    { path: '/provider/applications/105', label: '报名详情', icon: CalendarClock },
    { path: '/provider/reports', label: '我的报单', icon: FileCheck2 },
    { path: '/provider/reservations', label: '我的预约', icon: CalendarClock },
    { path: '/provider/messages', label: '消息中心', icon: MessageSquareText },
    { path: '/provider/support-chat', label: '站内客服会话', icon: MessageSquareText },
  ] },
  { label: '服务与成长', items: [
    { path: '/provider/skills', label: '我的技能', icon: Gamepad2 },
    { path: '/provider/evaluations', label: '评价管理', icon: Star },
    { path: '/provider/pass', label: '接单通行证', icon: TicketCheck },
    { path: '/provider/points', label: '积分与兑换', icon: Coins },
    { path: '/provider/auditions', label: '试音列表', icon: Award },
    { path: '/provider/audition', label: '公开试音邀请', icon: Award },
  ] },
  { label: '账户服务', items: [
    { path: '/provider/wallet', label: '我的钱包', icon: WalletCards },
    { path: '/provider/withdraw-accounts', label: '收款账户', icon: WalletCards },
    { path: '/provider/mine', label: '个人中心', icon: UserRound },
    { path: '/provider/profile', label: '资料与档期', icon: CircleUserRound },
    { path: '/provider/security', label: '账号安全', icon: ShieldCheck },
    { path: '/provider/support', label: '客服与帮助', icon: Headphones },
  ] },
]

const providerPageLabels: Record<string, string> = Object.fromEntries(groups.flatMap(group => group.items.map(item => [item.path, item.label])))
const authScenarioOptions = {
  '/provider-login': [
    { value: 'default', label: '微信快捷登录', description: '绑定微信、授权手机号、短信验证与可选二次验证' },
    { value: 'loading', label: '登录校验中', description: '防止重复提交并保留当前微信授权进度' },
    { value: 'empty', label: '微信未绑定账号', description: '引导入驻或联系本店管理人员' },
    { value: 'error', label: '微信授权失败', description: '展示失败原因与重新授权入口' },
  ],
  '/provider-register': [
    { value: 'default', label: '填写入驻申请', description: '微信绑定、手机号验证、实名与材料提交' },
    { value: 'loading', label: '申请提交中', description: '锁定表单，避免重复创建申请' },
    { value: 'empty', label: '审核已通过', description: '账号开通后使用微信快捷登录' },
    { value: 'error', label: '退回补充资料', description: '展示审核意见与重新提交入口' },
  ],
} satisfies Record<string, readonly { value: ProviderScenario; label: string; description: string }[]>

export function ProviderPrototypeShell({ children, dark, onThemeChange, scenario, onScenarioChange }: { children: ReactNode; dark: boolean; onThemeChange: (dark: boolean) => void; scenario: ProviderScenario; onScenarioChange: (scenario: ProviderScenario) => void }) {
  const location = useLocation()
  const navigate = useNavigate()
  const pageLabel = providerPageLabels[location.pathname] || '接单工作台'
  const scenarioOptions = authScenarioOptions[location.pathname as keyof typeof authScenarioOptions] || providerScenarioOptions
  function go(path: string) { onScenarioChange('default'); navigate(path) }
  return <div className="min-h-screen bg-background text-foreground lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
    <aside className="sticky top-0 hidden h-screen flex-col border-r bg-sidebar-background lg:flex">
      <div className="flex h-[72px] items-center gap-3 px-5"><div className="grid size-10 place-items-center rounded-xl bg-primary/10"><img src="./brand.svg" alt="" className="size-7" /></div><div><div className="text-[15px] font-semibold tracking-tight">声浪电竞</div><div className="text-xs text-muted-foreground">产品设计原型</div></div></div>
      <Separator />
      <div className="flex-1 overflow-y-auto px-3 py-5 phone-scrollbar">
        <p className="px-3 text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">产品</p>
        <div className="mt-3 space-y-1">
          <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => go('/home')}><Smartphone className="size-4" />老板端 H5<ChevronRight className="ml-auto size-4 text-muted-foreground" /></Button>
          <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => go('/admin-login')}><LayoutDashboard className="size-4" />管理后台<ChevronRight className="ml-auto size-4 text-muted-foreground" /></Button>
          <Button variant="secondary" className="h-11 w-full justify-start px-3 text-sm"><MonitorSmartphone className="size-4" />陪玩工作台<Badge className="ml-auto px-1.5 text-xs">当前</Badge></Button>
        </div>
        {groups.map(group => <div key={group.label}><p className="mt-7 px-3 text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">{group.label}</p><div className="mt-3 space-y-1">{group.items.map(item => { const Icon = item.icon; const active = location.pathname === item.path; return <Button key={item.path} variant="ghost" onClick={() => go(item.path === '/provider/audition' ? '/provider/audition?token=SL-AUDITION-202611&role=provider' : item.path)} aria-current={active ? 'page' : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', active && 'bg-accent text-accent-foreground hover:bg-accent/90')}><Icon className="size-4" />{item.label}{active && <ChevronRight className="ml-auto size-4" />}</Button> })}</div></div>)}
      </div>
      <div className="m-4 rounded-xl border bg-card p-4"><div className="flex items-center gap-2 text-sm font-medium"><span className="size-2 rounded-full bg-success" />今日可接单</div><p className="mt-2 text-xs leading-5 text-muted-foreground">服务提醒仅在原型中演示，不发送真实通知。</p></div>
    </aside>
    <div className="min-w-0">
      <header className="sticky top-0 z-40 flex min-h-[72px] flex-wrap items-center gap-3 border-b bg-background/85 px-4 py-3 backdrop-blur-xl md:px-6">
        <div className="flex min-w-0 items-center gap-3 lg:hidden"><img src="./brand.svg" alt="" className="size-8 shrink-0" /><div><p className="text-sm font-semibold">声浪电竞</p><p className="text-xs text-muted-foreground">陪玩端 · {pageLabel}</p></div></div>
        <div className="hidden items-center gap-2 text-sm lg:flex"><span className="text-muted-foreground">陪玩工作台</span><ChevronRight className="size-4 text-muted-foreground" /><span className="font-medium">{pageLabel}</span></div>
        <div className="ml-auto flex items-center gap-2"><Badge variant="outline" className="h-7 border-warning/40 bg-warning/10 px-2.5 text-xs font-medium text-foreground">示例数据</Badge><Button variant="outline" size="icon" className="size-11 rounded-full" onClick={() => onThemeChange(!dark)} aria-label={dark ? '切换为浅色模式' : '切换为深色模式'}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</Button></div>
      </header>
      <main className="prototype-canvas min-h-[calc(100dvh-72px)] px-4 py-6 md:px-6 md:py-8 xl:px-10">
        <div className="mx-auto flex max-w-[1440px] flex-col items-center gap-8 xl:grid xl:grid-cols-[minmax(0,1fr)_280px] xl:items-start xl:gap-10">
          <section className="flex w-full min-w-0 flex-col items-center" aria-label="陪玩端页面预览"><ViewportPreview dark={dark} pageLabel={pageLabel} product="provider" fitToContainer>{children}</ViewportPreview></section>
          <aside className="w-full max-w-[390px] xl:sticky xl:top-[104px] xl:max-w-none" aria-label="陪玩端原型场景控制">
            <div className="rounded-2xl border bg-card p-5 shadow-sm"><div className="flex items-center gap-2"><ClipboardCheck className="size-4 text-primary" /><h2 className="text-sm font-semibold">页面状态</h2></div><p className="mt-1 text-xs leading-5 text-muted-foreground">切换业务场景，检查每一层反馈。</p>
              <div className="mt-4 xl:hidden"><Select value={scenario} onValueChange={value => onScenarioChange(value as ProviderScenario)}><SelectTrigger className="h-11 w-full" aria-label={`选择${pageLabel}页面状态`}><SelectValue /></SelectTrigger><SelectContent>{scenarioOptions.map(option => <SelectItem key={option.value} value={option.value} className="min-h-11">{option.label}</SelectItem>)}</SelectContent></Select></div>
              <div className="mt-4 hidden space-y-1 xl:block">{scenarioOptions.map(option => <button key={option.value} type="button" className={cn('flex min-h-[58px] w-full items-center gap-3 rounded-lg px-3 text-left hover:bg-accent', scenario === option.value && 'bg-accent text-accent-foreground')} onClick={() => onScenarioChange(option.value)} aria-pressed={scenario === option.value}><span className={cn('grid size-5 shrink-0 place-items-center rounded-full border', scenario === option.value && 'border-primary bg-primary text-primary-foreground')}>{scenario === option.value && <Check className="size-3" />}</span><span><span className="block text-sm font-medium">{option.label}</span><span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{option.description}</span></span></button>)}</div>
            </div>
            <StoreDesignPanel product="provider" pageKey={location.pathname} />
            <div className="mt-4 rounded-2xl border bg-card p-5"><div className="flex items-center gap-2 text-sm font-semibold"><LockKeyhole className="size-4 text-primary" />原型边界</div><ul className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground"><li>不读取或修改真实陪玩订单</li><li>不上传凭证、不发起资金操作</li><li>所有状态变化仅在当前演示中生效</li></ul></div>
          </aside>
        </div>
      </main>
    </div>
  </div>
}
