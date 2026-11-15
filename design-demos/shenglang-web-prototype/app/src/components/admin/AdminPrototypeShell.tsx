import { useState, type ReactNode } from 'react'
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  LogOut,
  MessageSquareText,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  PinOff,
  Search,
  Smartphone,
  Sun,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { type AdminDashboardScenario } from '@/components/admin/admin-config'
import {
  adminActivePage,
  adminAllNavItems,
  adminDetailParents,
  adminGroupForPage,
  adminNavigationGroups,
  adminPageLabels,
  adminStandaloneItems,
  type AdminDetailPage,
  type AdminNavigablePage,
  type AdminNavItem,
  type AdminShellPage,
} from '@/components/admin/admin-navigation'
import { StoreSwitcher } from '@/components/store/StoreSwitcher'
import { useStore } from '@/components/store/store-context'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

interface AdminPrototypeShellProps<T extends string = AdminDashboardScenario> {
  children: ReactNode
  dark: boolean
  page?: AdminShellPage
  scenario: T
  scenarioOptions?: { value: string; label: string; description: string }[]
  onThemeChange: (dark: boolean) => void
  onScenarioChange: (scenario: T) => void
  onNavigate: (page: AdminNavigablePage) => void
  onBoss: () => void
  onLogout: () => void
}

const menuSections = ['门店业务', '运营配置'] as const
const sidebarLockKey = 'shenglang.admin.sidebar.locked'
const sidebarSessionKey = 'shenglang.admin.sidebar.expanded'

export function AdminPrototypeShell<T extends string = AdminDashboardScenario>({ children, dark, page = 'dashboard', onThemeChange, onNavigate, onBoss, onLogout }: AdminPrototypeShellProps<T>) {
  const { store } = useStore()
  const [searchOpen, setSearchOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [dispatchSound, setDispatchSound] = useState(true)
  const [chatSound, setChatSound] = useState(true)
  const [feedback, setFeedback] = useState('')
  const [sidebarLocked, setSidebarLocked] = useState(() => window.localStorage.getItem(sidebarLockKey) === 'true')
  const [sidebarExpanded, setSidebarExpanded] = useState(() => sidebarLocked || window.sessionStorage.getItem(sidebarSessionKey) === 'true')
  const activePage = adminActivePage(page)
  const activeGroup = adminGroupForPage(page)
  const pageLabel = adminPageLabels[page]
  const detailParent = page in adminDetailParents ? adminDetailParents[page as AdminDetailPage] : undefined
  const normalizedSearch = search.trim().toLowerCase()
  const searchResults = adminAllNavItems.filter(item => !normalizedSearch || [item.label, ...(item.aliases ?? [])].join(' ').toLowerCase().includes(normalizedSearch))

  function changeSidebarExpanded(expanded: boolean) {
    setSidebarExpanded(expanded)
    window.sessionStorage.setItem(sidebarSessionKey, String(expanded))
  }

  function toggleSidebarLock() {
    const locked = !sidebarLocked
    setSidebarLocked(locked)
    if (locked) {
      changeSidebarExpanded(true)
      window.localStorage.setItem(sidebarLockKey, 'true')
    } else {
      window.localStorage.removeItem(sidebarLockKey)
    }
  }

  function standaloneButton(item: AdminNavItem) {
    const Icon = item.icon
    const active = activePage === item.page
    return <Button key={item.page} variant="ghost" data-admin-primary-nav={item.page} onClick={() => onNavigate(item.page)} title={!sidebarExpanded ? item.label : undefined} aria-label={!sidebarExpanded ? item.label : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', !sidebarExpanded && 'justify-center px-0', active && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={active ? 'page' : undefined}><Icon className="size-4 shrink-0" /><span className={cn(!sidebarExpanded && 'sr-only')}>{item.label}</span>{active && sidebarExpanded && <ChevronRight className="ml-auto size-4" />}</Button>
  }

  return (
    <div className={cn('min-h-screen bg-background text-foreground lg:grid', sidebarExpanded ? 'lg:grid-cols-[232px_minmax(0,1fr)]' : 'lg:grid-cols-[72px_minmax(0,1fr)]')}>
      <aside className="sticky top-0 hidden h-screen flex-col border-r bg-sidebar-background lg:flex" aria-label="后台业务导航" data-admin-sidebar data-sidebar-expanded={sidebarExpanded} data-sidebar-locked={sidebarLocked}>
        <button type="button" onClick={onBoss} className={cn('flex h-20 w-full shrink-0 items-center gap-3 text-left', sidebarExpanded ? 'px-5' : 'justify-center px-2')} aria-label="返回声浪电竞产品原型入口" title={!sidebarExpanded ? '声浪电竞 · 运营管理后台' : undefined}>
          <span className="grid size-10 shrink-0 place-items-center rounded-xl border bg-card shadow-sm"><img src="./brand.svg" alt="" className="size-7" /></span>
          {sidebarExpanded && <span><span className="block text-[15px] font-semibold tracking-tight">声浪电竞</span><span className="block text-xs text-muted-foreground">运营管理后台</span></span>}
        </button>
        <Separator />
        <div className={cn('flex-1 overflow-y-auto py-5 phone-scrollbar', sidebarExpanded ? 'px-3' : 'px-2')}>
          {sidebarExpanded ? <p className="px-3 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">平台与工作区</p> : <div className="mx-2 mb-3 border-t" aria-hidden="true" />}
          <div className="mt-3 space-y-1">{adminStandaloneItems.map(standaloneButton)}</div>
          {menuSections.map(section => <div key={section}>
            {sidebarExpanded ? <p className="mt-7 px-3 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">{section}</p> : <div className="mx-2 my-5 border-t" aria-hidden="true" />}
            <div className="mt-3 space-y-1">{adminNavigationGroups.filter(group => group.section === section && group.primary !== false).map(group => {
              const Icon = group.icon
              const active = activeGroup?.id === group.id
              const landing = group.items[0]
              return <Button key={group.id} variant="ghost" data-admin-primary-nav={landing.page} onClick={() => onNavigate(landing.page)} title={!sidebarExpanded ? group.label : undefined} aria-label={!sidebarExpanded ? group.label : undefined} className={cn('h-11 w-full justify-start px-3 text-sm', !sidebarExpanded && 'justify-center px-0', active && 'bg-accent text-accent-foreground hover:bg-accent/90')} aria-current={active ? 'page' : undefined}><Icon className="size-4 shrink-0" /><span className={cn(!sidebarExpanded && 'sr-only')}>{group.label}</span>{active && sidebarExpanded && <ChevronRight className="ml-auto size-4" />}</Button>
            })}</div>
          </div>)}
          {sidebarExpanded ? <div className="mx-3 mt-6 rounded-xl border bg-card p-4">
            <div className="flex items-center gap-2 text-xs font-medium"><span className="size-2 rounded-full bg-success" />值班状态正常</div>
            <p className="mt-2 text-[11px] leading-5 text-muted-foreground">客服与派单提醒仅在当前页面内模拟，不发送真实声音或消息。</p>
          </div> : <div className="mx-auto mt-6 flex size-9 items-center justify-center rounded-lg border bg-card" title="值班状态正常" aria-label="值班状态正常"><span className="size-2 rounded-full bg-success" /></div>}
        </div>
        <div className={cn('shrink-0 border-t', sidebarExpanded ? 'p-3' : 'p-2')}>
          {sidebarExpanded ? <div className="flex items-center gap-1">
            <Button variant="ghost" data-admin-sidebar-toggle onClick={() => changeSidebarExpanded(false)} disabled={sidebarLocked} title={sidebarLocked ? '取消锁定后可收缩侧栏' : '收缩侧栏'} className="h-10 min-w-0 flex-1 justify-start px-3 text-xs"><PanelLeftClose className="size-4 shrink-0" />收缩侧栏</Button>
            <Button variant="ghost" size="icon" data-admin-sidebar-lock onClick={toggleSidebarLock} aria-label={sidebarLocked ? '取消锁定展开' : '锁定展开侧栏'} aria-pressed={sidebarLocked} title={sidebarLocked ? '取消锁定，刷新后不再强制展开' : '锁定展开，刷新后保持展开'} className={cn('size-10 shrink-0', sidebarLocked && 'bg-primary/10 text-primary hover:bg-primary/15')}>{sidebarLocked ? <PinOff className="size-4" /> : <Pin className="size-4" />}</Button>
          </div> : <Button variant="ghost" size="icon" data-admin-sidebar-toggle onClick={() => changeSidebarExpanded(true)} aria-label="展开侧栏" title="展开侧栏" className="size-11 w-full"><PanelLeftOpen className="size-4" /></Button>}
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-40 flex min-h-16 flex-wrap items-center gap-3 border-b bg-background/90 px-4 py-2 backdrop-blur-xl md:px-6">
          <div className="flex items-center gap-3 lg:hidden"><img src="./brand.svg" alt="" className="size-8" /><span className="text-sm font-semibold">声浪电竞</span></div>
          <div className="hidden items-center gap-2 text-sm lg:flex">
            <span className="text-muted-foreground">{page === 'stores' ? '平台' : store.shortName}</span><ChevronRight className="size-4 text-muted-foreground" />
            {activeGroup && <><span className="text-muted-foreground">{activeGroup.label}</span><ChevronRight className="size-4 text-muted-foreground" /></>}
            {detailParent && <><button type="button" onClick={() => onNavigate(detailParent)} className="hover:text-primary">{adminPageLabels[detailParent]}</button><ChevronRight className="size-4 text-muted-foreground" /></>}
            <span className="font-medium">{pageLabel}</span>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            {page !== 'stores' && <StoreSwitcher product="admin" compact />}
            <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
              <DialogTrigger asChild><Button variant="outline" aria-label="搜索后台功能" className="h-10 gap-2 px-3 text-muted-foreground"><Search className="size-4" /><span className="hidden xl:inline">搜索功能</span><kbd className="hidden rounded border bg-muted px-1.5 py-0.5 text-[10px] xl:inline">⌘ K</kbd></Button></DialogTrigger>
              <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-md">
                <DialogHeader><DialogTitle>搜索后台功能</DialogTitle><DialogDescription>搜索全部业务页面；详情页从对应列表进入。</DialogDescription></DialogHeader>
                <div className="relative"><Search className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input autoFocus value={search} onChange={event => setSearch(event.target.value)} placeholder="输入功能名称" className="h-11 pl-9" /></div>
                <div className="space-y-2">
                  {searchResults.map(item => {
                    const Icon = item.icon
                    const group = adminGroupForPage(item.page)
                    return <button key={item.page} type="button" onClick={() => { onNavigate(item.page); setSearchOpen(false) }} className="flex min-h-12 w-full items-center gap-3 rounded-lg border bg-card px-3 text-left text-sm hover:bg-accent"><Icon className="size-4 text-primary" /><span>{item.label}{group && <span className="mt-0.5 block text-[11px] text-muted-foreground">{group.label}</span>}</span><CheckCircle2 className="ml-auto size-4 shrink-0 text-success" /></button>
                  })}
                  {searchResults.length === 0 && <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">没有匹配功能，请尝试其他关键词</p>}
                </div>
              </DialogContent>
            </Dialog>
            <Button variant="ghost" size="icon" className="size-10" onClick={() => setDispatchSound(value => !value)} aria-label={dispatchSound ? '关闭派单提醒音' : '开启派单提醒音'} title="派单提醒音">{dispatchSound ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}</Button>
            <Button variant="ghost" size="icon" className={cn('size-10', !chatSound && 'text-muted-foreground')} onClick={() => setChatSound(value => !value)} aria-label={chatSound ? '关闭咨询提醒音' : '开启咨询提醒音'} title="咨询提醒音"><MessageSquareText className="size-4" /><span className={cn('ml-0.5 size-1.5 rounded-full', chatSound ? 'bg-primary' : 'bg-muted-foreground')} /></Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="relative size-10" aria-label="查看通知"><Bell className="size-4" /><span className="absolute right-2 top-2 size-2 rounded-full border-2 border-background bg-primary" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-80">
                <DropdownMenuLabel>值班通知 · 示例</DropdownMenuLabel><DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setFeedback('已打开客服待派提醒：8 笔已支付订单等待处理。')}><MessageSquareText className="size-4 text-primary" /><span><span className="block text-sm">8 笔订单等待客服派单</span><span className="block text-xs text-muted-foreground">示例快照 · 5 分钟前</span></span></DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setFeedback('已打开咨询提醒：3 条客服会话尚未回复。')}><MessageSquareText className="size-4 text-primary" /><span><span className="block text-sm">3 条会话等待回复</span><span className="block text-xs text-muted-foreground">示例快照 · 12 分钟前</span></span></DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="icon" className="size-10" onClick={() => onThemeChange(!dark)} aria-label={dark ? '切换为浅色模式' : '切换为深色模式'}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" className="h-10 gap-2 px-2"><span className="text-sm">运营管理员</span><ChevronDown className="size-3.5" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>运营管理员<br /><span className="text-xs font-normal text-muted-foreground">admin · 示例账号</span></DropdownMenuLabel><DropdownMenuSeparator />
                <DropdownMenuItem onSelect={onBoss}><Smartphone className="size-4" />返回老板端原型</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setFeedback('帮助已就绪：可从页面场景检查加载、权限与错误反馈。')}><CircleHelp className="size-4" />原型使用帮助</DropdownMenuItem>
                <DropdownMenuSeparator />
                <AlertDialog>
                  <AlertDialogTrigger asChild><DropdownMenuItem variant="destructive" onSelect={event => event.preventDefault()}><LogOut className="size-4" />退出登录</DropdownMenuItem></AlertDialogTrigger>
                  <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>确认退出管理后台？</AlertDialogTitle><AlertDialogDescription>退出只会返回原型登录页，不会修改真实账号或会话。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续工作</AlertDialogCancel><AlertDialogAction onClick={onLogout}>确认退出</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
                </AlertDialog>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <main className="min-w-0 p-4 md:p-6" data-store-scope={store.slug} data-store-page={page}>
          {activeGroup && <nav aria-label={`${activeGroup.label}二级页面`} data-admin-secondary-nav={activeGroup.id} className="mb-5 flex min-w-0 gap-1 overflow-x-auto rounded-xl border bg-muted/30 p-1.5 phone-scrollbar">
            {activeGroup.items.map(item => {
              const active = activePage === item.page
              return <Button key={item.page} type="button" variant="ghost" data-admin-secondary-page={item.page} onClick={() => onNavigate(item.page)} aria-current={active ? 'page' : undefined} className={cn('h-9 shrink-0 px-3 text-xs font-medium text-muted-foreground hover:bg-background hover:text-foreground', active && 'bg-background text-foreground shadow-sm hover:bg-background')}>{item.label}</Button>
            })}
          </nav>}
          {children}
        </main>
      </div>
      {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-sm items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
    </div>
  )
}
