import type { ReactNode } from 'react'
import {
  Check,
  ChevronRight,
  LayoutDashboard,
  LockKeyhole,
  LogIn,
  MonitorSmartphone,
  Moon,
  Smartphone,
  Sun,
} from 'lucide-react'
import { useLocation, useNavigate } from 'react-router'
import {
  adminActivePage,
  adminNavigationGroups,
  adminPageForPath,
  adminPageLabels,
  adminPathFor,
  adminStandaloneItems,
} from '@/components/admin/admin-navigation'
import { ViewportPreview } from '@/components/prototype/ViewportPreview'
import type { ScenarioOption } from '@/components/prototype/prototype-config'
import { StoreDesignPanel } from '@/components/store/StoreDesignPanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

const menuSections = ['门店业务', '运营配置'] as const

interface AdminDesignShellProps {
  children: ReactNode
  dark: boolean
  onThemeChange: (dark: boolean) => void
  scenario: string
  scenarioOptions: readonly ScenarioOption[]
  onScenarioChange: (scenario: string) => void
}

export function AdminDesignShell({ children, dark, onThemeChange, scenario, scenarioOptions, onScenarioChange }: AdminDesignShellProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const currentPage = adminPageForPath(location.pathname)
  const activePage = adminActivePage(currentPage)
  const pageLabel = location.pathname === '/admin-login' ? '后台登录' : adminPageLabels[currentPage]
  const pageKey = location.pathname === '/admin-login' ? 'login' : location.pathname.replace('/admin-', '')

  return <div className="min-h-screen bg-background text-foreground lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
    <aside className="sticky top-0 hidden h-screen flex-col border-r bg-sidebar-background lg:flex">
      <div className="flex h-[72px] items-center gap-3 px-5"><div className="grid size-10 place-items-center rounded-xl bg-primary/10"><img src="./brand.svg" alt="" className="size-7" /></div><div><div className="text-[15px] font-semibold tracking-tight">声浪电竞</div><div className="text-xs text-muted-foreground">产品设计原型</div></div></div>
      <Separator />
      <div className="flex-1 overflow-y-auto px-3 py-5 phone-scrollbar">
        <p className="px-3 text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">产品</p>
        <div className="mt-3 space-y-1">
          <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => navigate('/home')}><Smartphone className="size-4" />老板端 H5<ChevronRight className="ml-auto size-4 text-muted-foreground" /></Button>
          <Button variant="secondary" className="h-11 w-full justify-start px-3 text-sm"><LayoutDashboard className="size-4" />管理后台<Badge className="ml-auto px-1.5 text-xs">当前</Badge></Button>
          <Button variant="ghost" className="h-11 w-full justify-start px-3 text-sm" onClick={() => navigate('/provider')}><MonitorSmartphone className="size-4" />陪玩工作台<ChevronRight className="ml-auto size-4 text-muted-foreground" /></Button>
        </div>
        <p className="mt-7 px-3 text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">账号与平台</p>
        <div className="mt-3 space-y-1">
          <Button variant="ghost" onClick={() => navigate('/admin-login')} aria-current={location.pathname === '/admin-login' ? 'page' : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', location.pathname === '/admin-login' && 'bg-accent text-accent-foreground hover:bg-accent/90')}><LogIn className="size-4" />后台登录{location.pathname === '/admin-login' && <ChevronRight className="ml-auto size-4" />}</Button>
          {adminStandaloneItems.map(item => {
            const Icon = item.icon
            const active = activePage === item.page
            return <Button key={item.page} variant="ghost" data-admin-design-nav={item.page} onClick={() => navigate(adminPathFor(item.page))} aria-current={active ? 'page' : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', active && 'bg-accent text-accent-foreground hover:bg-accent/90')}><Icon className="size-4" />{item.label}{active && <ChevronRight className="ml-auto size-4" />}</Button>
          })}
        </div>
        {menuSections.map(section => <div key={section}>
          <p className="mt-7 px-3 text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">{section}</p>
          <div className="mt-3 space-y-1">{adminNavigationGroups.filter(group => group.section === section && group.primary !== false).map(group => {
            const Icon = group.icon
            const landing = group.items[0]
            const active = group.items.some(item => item.page === activePage)
            return <Button key={group.id} variant="ghost" data-admin-design-nav={landing.page} onClick={() => navigate(adminPathFor(landing.page))} aria-current={active ? 'page' : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', active && 'bg-accent text-accent-foreground hover:bg-accent/90')}><Icon className="size-4" /><span>{group.label}</span>{active && <ChevronRight className="ml-auto size-4" />}</Button>
          })}</div>
        </div>)}
      </div>
      <div className="m-4 rounded-xl border bg-card p-4"><div className="flex items-center gap-2 text-sm font-medium"><span className="size-2 rounded-full bg-success" />值班状态正常</div><p className="mt-2 text-xs leading-5 text-muted-foreground">客服与派单提醒仅在原型中演示，不发送真实消息。</p></div>
    </aside>
    <div className="min-w-0">
      <header className="sticky top-0 z-40 flex min-h-[72px] flex-wrap items-center gap-3 border-b bg-background/85 px-4 py-3 backdrop-blur-xl md:px-6">
        <div className="flex min-w-0 items-center gap-3 lg:hidden"><img src="./brand.svg" alt="" className="size-8 shrink-0" /><div><p className="text-sm font-semibold">声浪电竞</p><p className="text-xs text-muted-foreground">管理后台 · {pageLabel}</p></div></div>
        <div className="hidden items-center gap-2 text-sm lg:flex"><span className="text-muted-foreground">管理后台</span><ChevronRight className="size-4 text-muted-foreground" /><span className="font-medium">{pageLabel}</span></div>
        <div className="ml-auto flex items-center gap-2"><Badge variant="outline" className="h-7 border-warning/40 bg-warning/10 px-2.5 text-xs font-medium text-foreground">示例数据</Badge><Button variant="outline" size="icon" className="size-11 rounded-full" onClick={() => onThemeChange(!dark)} aria-label={dark ? '切换为浅色模式' : '切换为深色模式'}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</Button></div>
      </header>
      <main className="prototype-canvas min-h-[calc(100dvh-72px)] px-4 py-6 md:px-6 md:py-8">
        <div className="mx-auto flex max-w-[1600px] flex-col items-center gap-8 min-[1680px]:grid min-[1680px]:grid-cols-[minmax(0,1fr)_280px] min-[1680px]:items-start min-[1680px]:gap-10">
          <section className="flex w-full min-w-0 flex-col items-center" aria-label="管理后台页面预览"><ViewportPreview dark={dark} pageLabel={pageLabel} product="admin" fitToContainer>{children}</ViewportPreview></section>
          <aside className="w-full max-w-[390px] min-[1680px]:sticky min-[1680px]:top-[104px] min-[1680px]:max-w-none" aria-label="管理后台原型场景控制">
            <div className="rounded-2xl border bg-card p-5 shadow-sm"><div className="flex items-center gap-2"><LayoutDashboard className="size-4 text-primary" /><h2 className="text-sm font-semibold">页面状态</h2></div><p className="mt-1 text-xs leading-5 text-muted-foreground">切换业务场景，检查每一层反馈。</p>
              <div className="mt-4 min-[1680px]:hidden"><Select value={scenario} onValueChange={onScenarioChange}><SelectTrigger className="h-11 w-full" aria-label={`选择${pageLabel}页面状态`}><SelectValue /></SelectTrigger><SelectContent>{scenarioOptions.map(option => <SelectItem key={option.value} value={option.value} className="min-h-11">{option.label}</SelectItem>)}</SelectContent></Select></div>
              <div className="mt-4 hidden space-y-1 min-[1680px]:block">{scenarioOptions.map(option => <button key={option.value} type="button" className={cn('flex min-h-[58px] w-full items-center gap-3 rounded-lg px-3 text-left hover:bg-accent', scenario === option.value && 'bg-accent text-accent-foreground')} onClick={() => onScenarioChange(option.value)} aria-pressed={scenario === option.value}><span className={cn('grid size-5 shrink-0 place-items-center rounded-full border', scenario === option.value && 'border-primary bg-primary text-primary-foreground')}>{scenario === option.value && <Check className="size-3" />}</span><span><span className="block text-sm font-medium">{option.label}</span><span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{option.description}</span></span></button>)}</div>
            </div>
            <StoreDesignPanel product="admin" pageKey={pageKey} />
            <div className="mt-4 rounded-2xl border bg-card p-5"><div className="flex items-center gap-2 text-sm font-semibold"><LockKeyhole className="size-4 text-primary" />原型边界</div><ul className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground"><li>不连接真实后台账号或业务数据库</li><li>不读取或修改真实订单与资金</li><li>所有状态变化仅在当前演示中生效</li></ul></div>
          </aside>
        </div>
      </main>
    </div>
  </div>
}
