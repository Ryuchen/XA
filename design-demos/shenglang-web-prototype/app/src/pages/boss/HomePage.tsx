import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  AudioLines,
  Bell,
  CalendarDays,
  Check,
  ChevronRight,
  Gift,
  Gamepad2,
  Headphones,
  Heart,
  Megaphone,
  Search,
  ShieldCheck,
  Sparkles,
  Ticket,
  Trophy,
  X,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { HeaderSupportButton } from '@/components/prototype/HeaderSupportButton'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { useStore } from '@/components/store/store-context'
import { SearchField } from '@/components/prototype/SearchField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type HomeScenario = 'default' | 'guest' | 'loading' | 'error' | 'popup'

const gameplays = [
  { id: 1, name: '王者荣耀', note: '排位上分 · 轻松娱乐', price: '30', icon: Gamepad2 },
  { id: 2, name: '英雄联盟', note: '峡谷组队 · 默契双排', price: '35', icon: AudioLines },
  { id: 3, name: '和平精英', note: '战术配合 · 欢乐开黑', price: '28', icon: Headphones },
  { id: 4, name: '金铲铲之战', note: '阵容交流 · 休闲陪伴', price: '25', icon: Sparkles },
]

const quickEntries = [
  { label: '每日签到', icon: CalendarDays },
  { label: '领券中心', icon: Ticket },
  { label: '声浪榜单', icon: Trophy },
  { label: '心仪礼物', icon: Heart },
]

const serviceProgress: { key: 'pending' | 'grabbed' | 'in_progress'; label: string; count: number }[] = [
  { key: 'pending', label: '待接单', count: 1 },
  { key: 'grabbed', label: '已接单', count: 1 },
  { key: 'in_progress', label: '服务中', count: 1 },
]

interface HomePageProps {
  scenario: HomeScenario
  onLogin: () => void
  onCompanions: () => void
  onCatalog: () => void
  onOrders: (status?: 'pending' | 'grabbed' | 'in_progress') => void
  onGifts: () => void
  onCoupons: () => void
  onPoints: () => void
  onCheckin: () => void
  onRankings: () => void
  onAnnouncement: () => void
  onActivities: () => void
  onActivity: () => void
  onRights: () => void
  onSupport: () => void
  onRetry: () => void
}

export function HomePage({ scenario, onLogin, onCompanions, onCatalog, onOrders, onGifts, onCoupons, onPoints, onCheckin, onRankings, onAnnouncement, onActivities, onActivity, onRights, onSupport, onRetry }: HomePageProps) {
  const { store } = useStore()
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [popupOpen, setPopupOpen] = useState(scenario === 'popup')
  const [popupTab, setPopupTab] = useState<'hot' | 'rules'>('hot')
  const [slideIndex, setSlideIndex] = useState(0)
  const [carouselPaused, setCarouselPaused] = useState(false)
  const slides = [
    { label: '公告', text: store.announcement, icon: Bell, onOpen: onAnnouncement },
    { label: '活动', text: store.activity, icon: Trophy, onOpen: onActivity },
    { label: '权益', text: '老板专属权益与积分规则', icon: ShieldCheck, onOpen: onRights },
  ]
  const activeSlide = slides[slideIndex] ?? slides[0]
  const ActiveSlideIcon = activeSlide.icon

  useEffect(() => {
    if (carouselPaused || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setInterval(() => {
      setSlideIndex(current => (current + 1) % slides.length)
    }, 4500)
    return () => window.clearInterval(timer)
  }, [carouselPaused, slides.length])

  const filteredGameplays = useMemo(() => {
    const query = appliedKeyword.trim().toLowerCase()
    if (!query) return gameplays
    return gameplays.filter((item) => `${item.name}${item.note}`.toLowerCase().includes(query))
  }, [appliedKeyword])

  function submitSearch(event?: React.FormEvent) {
    event?.preventDefault()
    setAppliedKeyword(keyword.trim())
    window.setTimeout(() => {
      document.getElementById('home-gameplays')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 0)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') {
      document.querySelector('[data-testid="phone-scroll"]')?.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }
    if (key === 'companions') onCompanions()
  }

  const loading = scenario === 'loading'
  const guest = scenario === 'guest'

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={handleDockNavigate} />}>
      <main className="pb-28 md:pb-8">
        <PageHeader
          title={store.name}
          description="找到合拍的队友，现在开局"
          action={<HeaderSupportButton />}
          className="login-aura"
        >
          <form className="mt-5 flex items-center gap-2" onSubmit={submitSearch} role="search">
            <SearchField
              className="flex-1"
              value={keyword}
              onValueChange={setKeyword}
              maxLength={80}
              placeholder="搜一搜，找到你的心动服务"
              ariaLabel="搜索服务"
            />
            <Button type="submit" className="h-12 shrink-0 rounded-lg px-4 text-sm">搜索</Button>
          </form>

          {loading ? (
            <Card className="mt-4 rounded-3xl py-0">
              <CardContent className="space-y-4 p-5">
                <Skeleton className="h-5 w-28" />
                <Skeleton className="h-9 w-4/5" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-11 w-full rounded-xl" />
              </CardContent>
            </Card>
          ) : (
            <section className="home-hero relative mt-4 overflow-hidden rounded-3xl border border-primary/20 p-5 text-white" aria-labelledby="home-hero-title">
              <div className="relative z-10">
                <Badge className="border-0 bg-white/10 text-[10px] font-medium text-sky-100 hover:bg-white/10">声浪 · 游戏陪伴</Badge>
                <h2 id="home-hero-title" className="mt-4 max-w-[250px] text-[28px] font-semibold leading-[1.22] tracking-[-0.04em]">先听听声音，<br />再决定和谁组队</h2>
                <p className="mt-3 max-w-[255px] text-[13px] leading-5 text-blue-100/85">从轻松体验到默契陪伴，今晚不止是上线。</p>
                <Button type="button" className="mt-5 h-11 w-full rounded-xl bg-white text-blue-950 hover:bg-blue-50" onClick={onCompanions}>
                  看看谁在线
                  <ArrowRight className="size-4" />
                </Button>
              </div>
              <div className="absolute -right-8 -top-10 size-44 rounded-full border border-sky-300/15" aria-hidden="true" />
              <div className="absolute -right-2 -top-4 size-32 rounded-full border border-sky-300/15" aria-hidden="true" />
              <AudioLines className="absolute right-5 top-16 size-16 text-sky-300/20" strokeWidth={1.2} aria-hidden="true" />
            </section>
          )}

          {!guest && !loading && scenario !== 'error' && (
            <button
              type="button"
              className="mt-3 flex min-h-11 w-full items-center gap-2 overflow-hidden rounded-xl border bg-card/60 px-3 text-left"
              onClick={activeSlide.onOpen}
              onMouseEnter={() => setCarouselPaused(true)}
              onMouseLeave={() => setCarouselPaused(false)}
              onFocus={() => setCarouselPaused(true)}
              onBlur={() => setCarouselPaused(false)}
              aria-label={`${activeSlide.label}：${activeSlide.text}，点击查看`}
            >
              <ActiveSlideIcon className="size-4 shrink-0 text-primary" />
              <span key={slideIndex} className="flex min-w-0 flex-1 items-center gap-2 animate-in fade-in slide-in-from-y-1 duration-300">
                <span className="shrink-0 text-[11px] font-semibold text-primary">{activeSlide.label}</span>
                <span className="min-w-0 flex-1 truncate text-xs">{activeSlide.text}</span>
              </span>
              <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{slideIndex + 1}/{slides.length}</span>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            </button>
          )}
        </PageHeader>

        <div className="space-y-5 px-4 md:px-6">
          {guest && (
            <section className="flex items-center gap-3 rounded-2xl border bg-card p-4">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold">开启你的专属游戏时光</h2>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">登录查看服务价格与账号权益</p>
              </div>
              <Button type="button" className="h-11 shrink-0 rounded-xl px-4 text-xs" onClick={onLogin}>微信登录</Button>
            </section>
          )}

          {loading && (
            <Card className="rounded-2xl py-0"><CardContent className="space-y-3 p-5"><Skeleton className="h-5 w-40" /><Skeleton className="h-4 w-52" /><div className="grid grid-cols-3 gap-3 pt-2"><Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" /></div></CardContent></Card>
          )}

          {!guest && !loading && (
            <section className="overflow-hidden rounded-2xl bg-gradient-to-br from-blue-950 via-slate-900 to-slate-950 p-5 text-white shadow-lg" aria-labelledby="service-progress-title">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 id="service-progress-title" className="text-base font-semibold">你的服务有新进度</h2>
                  <p className="mt-1 text-xs text-blue-100/70">接单与履约状态，随时掌握</p>
                </div>
                <Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs text-sky-200 hover:bg-white/10 hover:text-white" onClick={() => onOrders()}>全部 <ChevronRight className="size-3.5" /></Button>
              </div>
              <div className="mt-3 grid grid-cols-3 divide-x divide-white/10 border-t border-white/10 pt-3">
                {serviceProgress.map((item) => (
                  <button key={item.key} type="button" className="flex min-h-[68px] flex-col items-center justify-center px-2 text-center" onClick={() => onOrders(item.key)}>
                    <span className="text-2xl font-semibold tabular-nums text-sky-300">{item.count}</span>
                    <span className="mt-1 text-[11px] text-blue-100/75">{item.label}</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          <section aria-labelledby="quick-title">
            <h2 id="quick-title" className="text-base font-semibold tracking-tight">快捷入口</h2>
            <div className="mt-3 grid grid-cols-4 gap-2">
              {quickEntries.map((entry) => {
                const Icon = entry.icon
                return (
                  <button key={entry.label} type="button" className="flex min-h-[76px] flex-col items-center justify-center gap-2 rounded-xl border bg-card px-1 text-[11px] font-medium shadow-sm transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" onClick={() => entry.label === '每日签到' ? onCheckin() : entry.label === '领券中心' ? onCoupons() : entry.label === '声浪榜单' ? onRankings() : onGifts()}>
                    <Icon className="size-5 text-primary" strokeWidth={1.8} />
                    {entry.label}
                  </button>
                )
              })}
            </div>
          </section>

          <section id="home-gameplays" className="scroll-mt-24" aria-labelledby="gameplay-title">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h2 id="gameplay-title" className="text-base font-semibold tracking-tight">今天，想怎么玩</h2>
                <p className="mt-1 text-xs text-muted-foreground">从轻松体验到默契陪伴</p>
              </div>
              <Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs text-primary" onClick={onCatalog}>全部服务 <ChevronRight className="size-3.5" /></Button>
            </div>

            {appliedKeyword && (
              <div className="mb-3 mt-3 flex items-center gap-2 rounded-xl bg-accent px-3 py-2 text-xs text-accent-foreground">
                <Search className="size-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate">“{appliedKeyword}” 的示例搜索结果</span>
                <Button type="button" variant="ghost" className="h-11 px-2 text-xs" onClick={() => { setKeyword(''); setAppliedKeyword('') }}>清空</Button>
              </div>
            )}

            {scenario === 'error' ? (
              <div role="alert" className="mt-4 rounded-2xl border border-destructive/25 bg-destructive/5 p-5 text-center">
                <AlertCircle className="mx-auto size-6 text-destructive" />
                <h3 className="mt-3 text-sm font-semibold">服务内容暂时没有加载成功</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">请检查网络后重试，已加载的个人服务进度不受影响。</p>
                <Button type="button" variant="outline" className="mt-4 h-11 w-full" onClick={onRetry}>重新加载</Button>
              </div>
            ) : loading ? (
              <div className="mt-4 grid grid-cols-2 gap-3"><Skeleton className="h-40 rounded-2xl" /><Skeleton className="h-40 rounded-2xl" /></div>
            ) : filteredGameplays.length ? (
              <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
                {filteredGameplays.map((item) => {
                  const Icon = item.icon
                  return (
                    <button key={item.id} type="button" className="overflow-hidden rounded-2xl border bg-card text-left shadow-sm transition-transform hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-ring" onClick={onCatalog}>
                      <div className="flex h-20 items-center justify-center bg-gradient-to-br from-primary/15 to-sky-400/5">
                        <Icon className="size-9 text-primary" strokeWidth={1.3} />
                      </div>
                      <div className="p-3">
                        <h3 className="truncate text-[13px] font-semibold">{item.name}</h3>
                        <p className="mt-1 truncate text-[10px] text-muted-foreground">{item.note}</p>
                        <p className="mt-2 text-sm font-semibold text-primary">{formatWaveAmount(item.price)}<span className="ml-1 text-[10px] font-normal text-muted-foreground">浪花起</span></p>
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="mt-4 rounded-2xl border border-dashed p-6 text-center">
                <Search className="mx-auto size-6 text-muted-foreground" />
                <p className="mt-3 text-sm font-medium">没有找到相关示例服务</p>
                <p className="mt-1 text-xs text-muted-foreground">试试“王者荣耀”或清空搜索。</p>
              </div>
            )}
          </section>

          {!guest && !loading && (
            <Card className="rounded-2xl py-0">
              <CardContent className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold">每日打卡</h2>
                    <p className="mt-1 text-xs text-muted-foreground">本月已签到 2 天 · 连续 2 天</p>
                  </div>
                  <CalendarDays className="size-5 text-primary" />
                </div>
                <div className="mt-4 grid grid-cols-7 gap-1">
                  {[1, 2, 3, 4, 5, 6, 7].map((day) => {
                    const checked = day <= 2
                    const today = day === 3
                    return (
                      <div key={day} className={cn('flex min-h-[52px] flex-col items-center justify-center rounded-lg bg-muted/60 text-[10px] text-muted-foreground', checked && 'bg-primary text-primary-foreground', today && 'border border-primary bg-primary/5 text-primary')}>
                        <span>{today ? '今天' : `${day}日`}</span>
                        <span className="mt-1 font-semibold">{checked ? <Check className="size-3.5" /> : today ? '待签' : '·'}</span>
                      </div>
                    )
                  })}
                </div>
                <Separator className="my-4" />
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-semibold">全勤奖励 · 66 浪花</p>
                    <p className="mt-1 truncate text-[11px] text-muted-foreground">查看今日消费进度与签到条件</p>
                  </div>
                  <Button type="button" variant="outline" className="h-11 shrink-0 rounded-xl px-4 text-xs" onClick={onCheckin}>去打卡</Button>
                </div>
              </CardContent>
            </Card>
          )}

          <section aria-labelledby="activities-title">
            <div className="flex items-center justify-between">
              <h2 id="activities-title" className="text-base font-semibold">俱乐部活动</h2>
              <Button type="button" variant="ghost" className="h-11 px-2 text-xs text-primary" onClick={onActivities}>全部活动 <ChevronRight className="size-3.5" /></Button>
            </div>
            <button type="button" className="mt-2 flex min-h-24 w-full items-center gap-4 rounded-2xl border bg-card p-4 text-left" onClick={onActivity}>
              <div className="grid size-16 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Trophy className="size-7" /></div>
              <div className="min-w-0 flex-1">
                <Badge variant="secondary" className="text-[10px]">当期活动</Badge>
                <h3 className="mt-2 text-sm font-semibold">{store.activity}</h3>
                <p className="mt-1 text-[11px] text-muted-foreground">了解活动与参与规则 <span className="text-primary">›</span></p>
              </div>
            </button>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Button type="button" variant="outline" className="h-20 flex-col items-start gap-1 rounded-2xl px-4" onClick={onCoupons}><Ticket className="size-4 text-primary" /><span className="text-[13px]">好券先领</span><span className="text-[10px] font-normal text-muted-foreground">下单前看看优惠</span></Button>
              <Button type="button" variant="outline" className="h-20 flex-col items-start gap-1 rounded-2xl px-4" onClick={onActivities}><Megaphone className="size-4 text-primary" /><span className="text-[13px]">活动回顾</span><span className="text-[10px] font-normal text-muted-foreground">查看往期活动</span></Button>
            </div>
          </section>

          <section aria-labelledby="rights-title">
            <div className="flex items-center justify-between">
              <h2 id="rights-title" className="text-base font-semibold">老板专属权益</h2>
              <Button type="button" variant="ghost" className="h-11 px-2 text-xs text-primary" onClick={onRights}>权益说明 <ChevronRight className="size-3.5" /></Button>
            </div>
            <button type="button" className="mt-2 flex min-h-32 w-full items-center gap-3 overflow-hidden rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/15 to-sky-400/5 p-5 text-left" onClick={onGifts}>
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">把欣赏，送给 TA</h3>
                <p className="mt-1 text-xs text-muted-foreground">一份礼物，让默契更进一步</p>
                <span className="mt-4 flex items-center text-xs font-medium text-primary">进入礼物单 <ArrowRight className="ml-1 size-3.5" /></span>
              </div>
              <Gift className="size-16 shrink-0 text-primary/50" strokeWidth={1.2} />
            </button>
            <div className="mt-2 grid grid-cols-3 divide-x divide-border">
              {[{ label: '我的好券', note: '领券享优惠', icon: Ticket }, { label: '积分好礼', note: '兑换心愿', icon: Sparkles }, { label: '专属权益', note: '了解规则', icon: ShieldCheck }].map((item) => {
                const Icon = item.icon
                return <button key={item.label} type="button" className="flex min-h-[84px] flex-col items-center justify-center gap-1 px-1" onClick={() => item.label === '专属权益' ? onRights() : item.label === '我的好券' ? onCoupons() : onPoints()}><Icon className="mb-1 size-4 text-primary" /><span className="text-xs font-medium">{item.label}</span><span className="text-[10px] text-muted-foreground">{item.note}</span></button>
              })}
            </div>
          </section>

          <footer className="pb-2 pt-1 text-center">
            <p className="text-[11px] text-muted-foreground">声浪电竞 · 每一份陪伴，都认真对待</p>
            <Button type="button" variant="ghost" className="mt-1 h-11 text-xs text-muted-foreground" onClick={onSupport}>选服务有疑问？联系客服 <ChevronRight className="size-3.5" /></Button>
          </footer>
        </div>
      </main>

      <Dialog open={popupOpen} onOpenChange={setPopupOpen}>
        <DialogContent className="prototype-dialog rounded-3xl p-5" showCloseButton={false}>
          <DialogHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs text-muted-foreground">欢迎来到声浪电竞</p>
                <DialogTitle className="mt-1 text-left text-xl">{popupTab === 'hot' ? '近期活动' : '下单须知'}</DialogTitle>
              </div>
              <DialogClose asChild><Button type="button" variant="ghost" size="icon" className="size-11 rounded-full" aria-label="关闭入场弹窗"><X className="size-4" /></Button></DialogClose>
            </div>
            <DialogDescription className="sr-only">查看近期活动与下单须知，关闭后返回首页。</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-1 rounded-xl bg-muted p-1">
            <Button type="button" variant={popupTab === 'hot' ? 'secondary' : 'ghost'} className="h-11" onClick={() => setPopupTab('hot')}>近期活动</Button>
            <Button type="button" variant={popupTab === 'rules' ? 'secondary' : 'ghost'} className="h-11" onClick={() => setPopupTab('rules')}>下单须知</Button>
          </div>
          {popupTab === 'hot' ? (
            <div className="rounded-2xl bg-gradient-to-br from-blue-950 to-slate-900 p-6 text-white">
              <Trophy className="size-9 text-sky-300" />
              <h3 className="mt-8 text-2xl font-semibold">{store.activity}</h3>
              <p className="mt-3 text-sm leading-6 text-blue-100/80">找到合拍的队友，完成一场轻松的双排体验。</p>
              <Badge className="mt-5 bg-white/10 text-sky-100 hover:bg-white/10">11 月俱乐部当期活动 · 示例</Badge>
            </div>
          ) : (
            <div className="rounded-2xl border p-5">
              <ShieldCheck className="size-7 text-primary" />
              <h3 className="mt-4 text-lg font-semibold">下单前，请确认服务内容</h3>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground"><li>核对玩法、服务时长与陪玩人选</li><li>确认订单金额与可用优惠</li><li>服务变化与履约进度以订单页为准</li></ul>
            </div>
          )}
          <DialogFooter>
            <Button type="button" className="h-11 w-full" onClick={() => popupTab === 'hot' ? setPopupTab('rules') : setPopupOpen(false)}>{popupTab === 'hot' ? '下一页 · 下单须知' : '知道了'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}
