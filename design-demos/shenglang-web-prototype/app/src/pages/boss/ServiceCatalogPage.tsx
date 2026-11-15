import { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowDownUp,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  CircleOff,
  Gamepad2,
  Headphones,
  SlidersHorizontal,
  Sparkles,
  Ticket,
  X,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { SearchField } from '@/components/prototype/SearchField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type CatalogScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'provider' | 'coupon'

export type CatalogService = {
  id: number
  name: string
  description: string
  game: string
  tab: string
  platform: '手游' | '端游'
  level: string
  price: number
  unit: string
  variants: number
  consult?: boolean
  providerEligible?: boolean
  coupon?: boolean
  tone: string
}

const services: CatalogService[] = [
  { id: 1, name: '王者娱乐双排', description: '轻松沟通，快乐上分', game: '王者荣耀', tab: 'experience', platform: '手游', level: '不限难度', price: 29, unit: '份', variants: 3, providerEligible: true, coupon: true, tone: 'from-sky-400/35 via-blue-600/25 to-indigo-700/30' },
  { id: 2, name: '和平精英体验局', description: '战术配合，欢乐开黑', game: '和平精英', tab: 'experience', platform: '手游', level: '不限难度', price: 39, unit: '份', variants: 1, coupon: true, tone: 'from-cyan-400/30 via-teal-500/20 to-blue-700/25' },
  { id: 3, name: '王者默契陪伴', description: '认真沟通，稳定发挥', game: '王者荣耀', tab: 'regular', platform: '手游', level: '机密', price: 68, unit: '小时', variants: 4, providerEligible: true, tone: 'from-blue-400/30 via-indigo-500/25 to-violet-700/25' },
  { id: 4, name: '金铲铲阵容研究', description: '一起研究新版本思路', game: '金铲铲之战', tab: 'regular', platform: '手游', level: '不限难度', price: 45, unit: '小时', variants: 2, tone: 'from-violet-400/25 via-blue-500/20 to-cyan-600/25' },
  { id: 5, name: '峡谷技术双排', description: '对局复盘，技术提升', game: '英雄联盟', tab: 'companion', platform: '端游', level: '绝密', price: 56, unit: '小时', variants: 3, tone: 'from-indigo-400/30 via-blue-600/25 to-slate-700/30' },
  { id: 6, name: '周末欢乐盲盒', description: '随机玩法，惊喜开局', game: '王者荣耀', tab: 'fun', platform: '手游', level: '不限难度', price: 19, unit: '份', variants: 1, providerEligible: true, tone: 'from-sky-300/30 via-violet-500/25 to-fuchsia-700/20' },
  { id: 7, name: '专属训练方案', description: '由客服确认目标与安排', game: '英雄联盟', tab: 'other', platform: '端游', level: '绝密', price: 99, unit: '份', variants: 1, consult: true, tone: 'from-slate-300/25 via-blue-600/20 to-indigo-800/30' },
]

const tabs = [
  { id: 'experience', name: '体验单' },
  { id: 'regular', name: '常规单' },
  { id: 'companion', name: '陪玩单' },
  { id: 'fun', name: '趣味单' },
  { id: 'other', name: '其他' },
]
const games = ['全部游戏', '王者荣耀', '和平精英', '英雄联盟', '金铲铲之战']

interface ServiceCatalogPageProps {
  scenario: CatalogScenario
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOpen: (service: CatalogService) => void
  onRetry: () => void
}

export function ServiceCatalogPage({ scenario, onLogin, onHome, onCompanions, onOpen, onRetry }: ServiceCatalogPageProps) {
  const [keyword, setKeyword] = useState('')
  const [game, setGame] = useState('全部游戏')
  const [tab, setTab] = useState('experience')
  const [platform, setPlatform] = useState('')
  const [level, setLevel] = useState('')
  const [sort, setSort] = useState<'price' | 'default'>('price')
  const [expanded, setExpanded] = useState(false)

  const visible = useMemo(() => {
    if (['loading', 'error', 'empty', 'guest'].includes(scenario)) return []
    const query = keyword.trim().toLowerCase()
    const rows = services.filter((item) => {
      if (scenario === 'provider' && !item.providerEligible) return false
      if (scenario === 'coupon' && !item.coupon) return false
      if (game !== '全部游戏' && item.game !== game) return false
      if (!query && item.tab !== tab) return false
      if (platform && item.platform !== platform) return false
      if (level && item.level !== level) return false
      if (query && ![item.name, item.description, item.game].join(' ').toLowerCase().includes(query)) return false
      return true
    })
    return sort === 'price' ? [...rows].sort((a, b) => a.price - b.price) : rows
  }, [game, keyword, level, platform, scenario, sort, tab])

  function resetFilters() {
    setKeyword('')
    setGame('全部游戏')
    setPlatform('')
    setLevel('')
    setTab('experience')
    setSort('price')
  }

  function chooseGame(value: string) {
    setGame(value)
    setPlatform('')
    setLevel('')
    if (value === '英雄联盟') setTab('companion')
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
  }

  const activeFilters = [
    game !== '全部游戏' ? { key: 'game', label: game, clear: () => chooseGame('全部游戏') } : null,
    platform ? { key: 'platform', label: platform, clear: () => setPlatform('') } : null,
    level ? { key: 'level', label: level, clear: () => setLevel('') } : null,
    keyword ? { key: 'keyword', label: `搜索：${keyword}`, clear: () => setKeyword('') } : null,
  ].filter(Boolean)

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="login-aura px-4 pb-5 pt-4">
          <p className="text-xs font-medium text-primary">声浪服务商城</p>
          <h1 className="mt-2 text-[28px] font-semibold leading-tight tracking-[-0.04em]">好服务，合你心意</h1>
          <p className="mt-1 text-xs text-muted-foreground">选好套餐，让下一局更有期待</p>
          <SearchField className="mt-5" value={keyword} onValueChange={setKeyword} maxLength={80} placeholder="搜索服务、玩法或游戏" ariaLabel="搜索服务" />
        </div>

        <div className="space-y-4 px-4">
          {scenario === 'guest' ? (
            <Card className="rounded-3xl py-0 text-center"><CardContent className="p-6"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Gamepad2 className="size-7" /></div><h2 className="mt-5 text-xl font-semibold">登录后查看完整服务</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">绑定声浪账号，获取专属价格与可选陪玩。</p><Button type="button" className="mt-6 h-12 w-full rounded-xl" onClick={onLogin}>微信快捷登录</Button></CardContent></Card>
          ) : (
            <>
              {scenario === 'provider' && <section className="flex items-center gap-3 rounded-2xl border border-primary/20 bg-primary/5 p-4"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Headphones className="size-5" /></div><div className="min-w-0 flex-1"><p className="text-sm font-semibold">为小澈选择服务</p><p className="mt-0.5 text-[11px] text-muted-foreground">仅展示 TA 当前可接的套餐</p></div><Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs" onClick={onRetry}>取消指定</Button></section>}
              {scenario === 'coupon' && <section className="flex items-center gap-3 rounded-2xl border border-primary/20 bg-primary/5 p-4"><Ticket className="size-5 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="text-sm font-semibold">新客体验券</p><p className="mt-0.5 text-[11px] text-muted-foreground">以下为本券适用套餐，结算时选择使用</p></div><Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs" onClick={onRetry}>全部服务</Button></section>}

              <section aria-label="游戏分类">
                <p className="mb-2 text-xs font-medium text-muted-foreground">先选游戏</p>
                <div className="phone-scrollbar -mx-4 overflow-x-auto px-4"><div className="flex w-max gap-2 pb-1">{games.map((item) => <button key={item} type="button" aria-pressed={game === item} className={cn('flex h-11 min-w-11 items-center gap-2 rounded-full border bg-card px-4 text-xs font-medium', game === item && 'border-primary bg-primary text-primary-foreground')} onClick={() => chooseGame(item)}>{item === '全部游戏' ? <Sparkles className="size-4" /> : <Gamepad2 className="size-4" />}{item}</button>)}</div></div>
              </section>

              <section className="rounded-2xl border bg-card/60 p-3" aria-label="服务分类与筛选">
                <div className="flex items-center justify-between gap-2"><p className="text-xs font-medium text-muted-foreground">选择分类</p><Button type="button" variant="ghost" className="h-11 px-2 text-xs" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><SlidersHorizontal className="size-4" />{expanded ? '收起筛选' : '更多筛选'}{expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}</Button></div>
                <div className="phone-scrollbar -mx-3 overflow-x-auto px-3"><div className="flex w-max gap-2">{tabs.map((item) => <button key={item.id} type="button" aria-pressed={tab === item.id} className={cn('h-11 rounded-xl px-4 text-xs font-medium text-muted-foreground', tab === item.id && 'bg-primary/10 text-primary')} onClick={() => { setTab(item.id); setKeyword('') }}>{item.name}</button>)}</div></div>
                {expanded && <div className="mt-2 space-y-3 border-t px-1 pt-3">
                  <div><p className="text-[11px] text-muted-foreground">平台</p><div className="mt-2 flex flex-wrap gap-2">{['', '手游', '端游'].map((item) => <button key={item || 'all'} type="button" aria-pressed={platform === item} className={cn('min-h-11 rounded-lg border px-3 text-xs', platform === item && 'border-primary bg-primary/10 text-primary')} onClick={() => setPlatform(item)}>{item || '不限'}</button>)}</div></div>
                  <div><p className="text-[11px] text-muted-foreground">难度</p><div className="mt-2 flex flex-wrap gap-2">{['', '不限难度', '机密', '绝密'].map((item) => <button key={item || 'all'} type="button" aria-pressed={level === item} className={cn('min-h-11 rounded-lg border px-3 text-xs', level === item && 'border-primary bg-primary/10 text-primary')} onClick={() => setLevel(item)}>{item || '不限'}</button>)}</div></div>
                </div>}
              </section>

              {activeFilters.length > 0 && <section><p className="text-[11px] text-muted-foreground">当前筛选 · 点击条件可取消</p><div className="mt-2 flex flex-wrap gap-2">{activeFilters.map((item) => item && <button key={item.key} type="button" className="flex min-h-11 items-center gap-1.5 rounded-full border border-primary/25 bg-primary/5 px-3 text-xs text-primary" onClick={item.clear} aria-label={`取消筛选：${item.label}`}>{item.label}<X className="size-3.5" /></button>)}</div></section>}

              <div className="flex items-center justify-between gap-3 border-t pt-4">
                <div><h2 className="text-base font-semibold">{keyword ? '搜索结果' : '精选套餐'} <span className="ml-1 text-xs font-normal text-muted-foreground">{visible.length}</span></h2><p className="mt-1 text-[11px] text-muted-foreground">价格与服务范围以详情为准</p></div>
                <div className="flex shrink-0 rounded-xl border p-1"><button type="button" aria-pressed={sort === 'price'} className={cn('flex h-11 items-center gap-1 rounded-lg px-2 text-[11px]', sort === 'price' && 'bg-primary/10 text-primary')} onClick={() => setSort('price')}><ArrowDownUp className="size-3.5" />价格</button><button type="button" aria-pressed={sort === 'default'} className={cn('h-11 min-w-11 rounded-lg px-2 text-[11px]', sort === 'default' && 'bg-primary/10 text-primary')} onClick={() => setSort('default')}>综合</button></div>
              </div>

              {scenario === 'loading' ? <div className="space-y-3" aria-busy="true" aria-label="正在加载服务目录">{[1, 2, 3].map((item) => <Card key={item} className="rounded-2xl py-0"><CardContent className="flex gap-3 p-4"><Skeleton className="h-24 w-24 rounded-xl" /><div className="flex-1 space-y-3"><Skeleton className="h-5 w-32" /><Skeleton className="h-4 w-full" /><Skeleton className="h-6 w-24" /></div></CardContent></Card>)}</div> : scenario === 'error' ? <div role="alert" className="rounded-2xl border border-destructive/25 bg-destructive/5 p-6 text-center"><AlertCircle className="mx-auto size-7 text-destructive" /><h3 className="mt-3 text-base font-semibold">服务目录暂时没有加载成功</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">请检查网络后重试，当前筛选条件会保留。</p><Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={onRetry}>重新加载</Button></div> : visible.length === 0 ? <div className="rounded-2xl border border-dashed p-6 text-center"><CircleOff className="mx-auto size-7 text-muted-foreground" /><h3 className="mt-3 text-base font-semibold">没有符合条件的服务</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">试试更换游戏、分类或清除筛选条件。</p><Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={scenario === 'empty' ? onRetry : resetFilters}>清除筛选条件</Button></div> : <div className="space-y-3">{visible.map((item) => (
                <button key={item.id} type="button" className="group block w-full overflow-hidden rounded-2xl border bg-card text-left shadow-sm transition-colors hover:border-primary/30 focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpen(item)} aria-label={`${item.name}，${formatWaveAmount(item.price)}浪花每${item.unit}${item.variants > 1 ? '起' : ''}${item.consult ? '，需客服确认' : ''}，查看详情`}>
                  <div className="flex gap-3 p-3">
                    <div className={cn('relative grid h-28 w-24 shrink-0 place-items-center overflow-hidden rounded-xl bg-gradient-to-br', item.tone)}><div className="absolute -right-5 -top-5 size-16 rounded-full border border-white/10" /><div className="absolute -bottom-7 -left-4 size-20 rounded-full border border-white/10" /><Gamepad2 className="relative size-8 text-white/85" strokeWidth={1.4} /><span className="absolute bottom-2 text-[9px] font-medium tracking-widest text-white/60">SHENGLANG</span></div>
                    <div className="flex min-w-0 flex-1 flex-col py-1"><div className="flex items-start gap-2"><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold">{item.name}</h3><p className="mt-1 truncate text-[11px] text-muted-foreground">{item.description}</p></div><ArrowRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" /></div><div className="mt-2 flex flex-wrap gap-1.5">{item.consult && <Badge variant="outline" className="border-warning/30 px-1.5 text-[9px] text-warning">需客服确认</Badge>}<Badge variant="secondary" className="px-1.5 text-[9px]">{item.platform}</Badge><Badge variant="secondary" className="px-1.5 text-[9px]">{item.game}</Badge></div><p className="mt-auto pt-2 text-xl font-semibold tracking-tight text-primary">{formatWaveAmount(item.price)}<span className="ml-1 text-[10px] font-normal text-muted-foreground">浪花 / {item.unit}{item.variants > 1 ? '起' : ''}</span></p></div>
                  </div>
                </button>
              ))}</div>}
            </>
          )}
        </div>
      </main>

    </PhoneFrame>
  )
}
