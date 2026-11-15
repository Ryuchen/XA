import { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CircleOff,
  Headphones,
  Info,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  UserRoundCheck,
  UsersRound,
  X,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { SearchField } from '@/components/prototype/SearchField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'

export type CompanionPickerScenario = 'default' | 'double' | 'guest' | 'loading' | 'error' | 'empty' | 'selection-changed' | 'validating' | 'validate-error' | 'checkout'

interface CompanionPickerPageProps {
  scenario: CompanionPickerScenario
  service: CatalogService
  fromCheckout: boolean
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onDetail: () => void
  onConfirm: (providerIds: number[]) => void
  onPlatform: () => void
  onRetry: () => void
}

interface PickerProvider {
  id: number
  name: string
  initial: string
  gender: 'MALE' | 'FEMALE'
  level: string
  monthly: number
  canOrder: boolean
  reason?: string
  recommended?: boolean
  tone: string
}

const providers: PickerProvider[] = [
  { id: 1, name: '小澈', initial: '澈', gender: 'FEMALE', level: '声浪王牌', monthly: 3280, canOrder: true, recommended: true, tone: 'from-sky-400/35 to-blue-700/25 text-sky-100' },
  { id: 2, name: '阿屿', initial: '屿', gender: 'MALE', level: '资深陪玩', monthly: 2160, canOrder: true, tone: 'from-indigo-400/30 to-slate-700/25 text-indigo-100' },
  { id: 3, name: '桃乐丝', initial: '桃', gender: 'FEMALE', level: '人气陪玩', monthly: 1890, canOrder: true, recommended: true, tone: 'from-cyan-400/30 to-blue-700/25 text-cyan-100' },
  { id: 4, name: '北夜', initial: '北', gender: 'MALE', level: '声浪王牌', monthly: 3560, canOrder: false, reason: '当前服务资格或接单状态已变化', tone: 'from-blue-300/25 to-violet-700/25 text-blue-100' },
]

function initialSelection(scenario: CompanionPickerScenario) {
  if (scenario === 'double') return [1]
  if (scenario === 'selection-changed') return [4]
  if (['validating', 'validate-error', 'checkout'].includes(scenario)) return [1]
  return []
}

export function CompanionPickerPage({ scenario, service, fromCheckout, onLogin, onBack, onHome, onCompanions, onOrders, onMine, onDetail, onConfirm, onPlatform, onRetry }: CompanionPickerPageProps) {
  const [keyword, setKeyword] = useState('')
  const [gender, setGender] = useState('不限')
  const [selected, setSelected] = useState<number[]>(() => initialSelection(scenario))
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const limit = scenario === 'double' ? 2 : 1
  const validating = busy || scenario === 'validating'

  const visible = useMemo(() => {
    if (['empty', 'loading', 'error'].includes(scenario)) return []
    const query = keyword.trim().toLowerCase()
    return providers.filter(item => {
      if (query && !`${item.name} ${item.level}`.toLowerCase().includes(query)) return false
      if (gender !== '不限' && item.gender !== (gender === '女陪' ? 'FEMALE' : 'MALE')) return false
      return true
    })
  }, [gender, keyword, scenario])

  const selectedProviders = selected.map(id => providers.find(item => item.id === id)).filter((item): item is PickerProvider => Boolean(item))
  const selectionValid = selectedProviders.every(item => item.canOrder)
  const selectionReady = selectionValid && (selected.length === 0 || selected.length === limit)
  const hint = !selectionValid ? '已选陪玩接单信息已变化，请取消后重新选择' : selected.length === 0 ? '未指定时由平台安排' : selected.length < limit ? `还需选择 ${limit - selected.length} 位陪玩` : '已选齐，套餐价格不变'

  function dock(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }

  function toggleProvider(item: PickerProvider) {
    if (validating) return
    setFeedback('')
    if (selected.includes(item.id)) {
      setSelected(ids => ids.filter(id => id !== item.id))
      return
    }
    if (!item.canOrder) {
      setFeedback(item.reason || '该陪玩暂不可接单，请更换陪玩')
      return
    }
    if (limit === 1) {
      setSelected([item.id])
      return
    }
    if (selected.length >= limit) {
      setFeedback(`最多选择 ${limit} 位陪玩，请先取消一位再更换`)
      return
    }
    setSelected(ids => [...ids, item.id])
  }

  function confirmSelection() {
    if (!selectionReady || validating) return
    if (!selected.length) {
      onPlatform()
      return
    }
    setBusy(true)
    setFeedback('')
    window.setTimeout(() => {
      setBusy(false)
      if (scenario === 'validate-error') {
        setFeedback('接单信息确认失败，请重试。当前选择已保留。')
      } else {
        onConfirm(selected)
      }
    }, 650)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="companions" onNavigate={dock} />}>
      <main className="pb-44">
        <header className="px-4 pb-4 pt-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />{fromCheckout ? '返回下单' : '返回服务'}</Button>
          <div className="mt-3 flex items-start justify-between gap-3">
            <div><p className="text-xs font-medium text-primary">声浪电竞 · 指定陪玩</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">挑选陪玩</h1></div>
            <div className="grid size-11 shrink-0 place-items-center rounded-2xl border border-primary/20 bg-primary/10 text-primary"><UsersRound className="size-5" /></div>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">选择合拍队友，也可以交给平台安排。</p>
        </header>

        <div className="space-y-4 px-4 pb-5">
          <section className="relative overflow-hidden rounded-3xl border border-primary/20 bg-[linear-gradient(145deg,rgba(30,58,138,0.34),rgba(2,6,23,0.72))] p-4" aria-labelledby="picker-service-title">
            <div className="pointer-events-none absolute -right-10 -top-12 size-32 rounded-full border border-sky-300/10" />
            <div className="relative flex items-start justify-between gap-3"><div><p className="text-[10px] text-sky-200/60">为这项服务挑选</p><h2 id="picker-service-title" className="mt-1.5 text-lg font-semibold text-white">{service.name}</h2><p className="mt-1 text-[11px] leading-5 text-sky-100/60">{service.level} · 同范围高档可接低档</p></div><Badge className="shrink-0 border border-sky-300/15 bg-sky-300/10 text-[10px] text-sky-200 hover:bg-sky-300/10">{limit === 2 ? '双陪套餐' : '单陪套餐'}</Badge></div>
          </section>

          {scenario === 'guest' ? (
            <section className="rounded-3xl border bg-card p-7 text-center"><UserRoundCheck className="mx-auto size-9 text-primary" /><h2 className="mt-4 text-xl font-semibold">登录后挑选陪玩</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看当前服务的可选陪玩，并保留本次服务上下文。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}>微信快捷登录</Button></section>
          ) : (
            <>
              <section className="rounded-2xl border bg-card p-4" aria-labelledby="picker-selection-title">
                <div className="flex items-center justify-between gap-3"><h2 id="picker-selection-title" className="text-sm font-semibold">已选 {selected.length} / {limit} 位</h2><span className="text-[10px] text-muted-foreground">指定陪玩 · 原价不变</span></div>
                {selectedProviders.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{selectedProviders.map(item => <button key={item.id} type="button" disabled={validating} aria-label={`取消选择 ${item.name}`} className={cn('flex min-h-11 items-center gap-2 rounded-full border px-3 text-xs focus-visible:ring-2 focus-visible:ring-ring', !item.canOrder && 'border-warning/25 bg-warning/5 text-warning')} onClick={() => toggleProvider(item)}><span className={cn('grid size-6 place-items-center rounded-full bg-gradient-to-br text-[10px]', item.tone)}>{item.initial}</span>{item.name}{!item.canOrder && ' · 暂不可约'}<X className="size-3.5 text-muted-foreground" /></button>)}</div>}
                <p role="status" className={cn('mt-3 flex items-start gap-1.5 text-[11px] leading-5 text-muted-foreground', !selectionValid && 'text-warning')}><Info className="mt-0.5 size-3.5 shrink-0" />{hint}</p>
                <Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" disabled={validating} onClick={onPlatform}><Headphones className="size-4" />由平台安排</Button>
              </section>

              <section className="space-y-3" aria-label="筛选可选陪玩">
                <SearchField value={keyword} onValueChange={setKeyword} placeholder="搜索陪玩昵称" ariaLabel="搜索陪玩昵称" />
                <div className="flex items-center gap-3"><p className="flex-1 text-[11px] text-muted-foreground">{scenario === 'loading' ? '正在更新陪玩…' : `${visible.length} 位可选陪玩`}</p><Select value={gender} onValueChange={setGender} disabled={fromCheckout || validating}><SelectTrigger className="h-11 min-h-11 w-[128px] rounded-xl bg-card text-xs shadow-none" aria-label={fromCheckout ? '性别（按订单）' : '陪玩性别'}><SelectValue /></SelectTrigger><SelectContent>{['不限', '男陪', '女陪'].map(item => <SelectItem key={item} value={item} className="min-h-11">{item}</SelectItem>)}</SelectContent></Select></div>
              </section>

              {feedback && <p role="alert" className="rounded-xl border border-warning/20 bg-warning/5 p-3 text-[11px] leading-5 text-warning">{feedback}</p>}

              {scenario === 'loading' ? (
                <div className="grid grid-cols-2 gap-3" aria-busy="true" aria-label="正在加载可选陪玩">{[1, 2, 3, 4].map(item => <Skeleton key={item} className="h-[224px] rounded-2xl" />)}</div>
              ) : scenario === 'error' ? (
                <div role="alert" className="rounded-2xl border border-destructive/25 bg-destructive/5 p-6 text-center"><AlertCircle className="mx-auto size-7 text-destructive" /><h2 className="mt-3 text-sm font-semibold">可选陪玩暂时没有加载成功</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">请检查网络后重试，当前选择与本次服务不会丢失。</p><Button type="button" className="mt-5 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div>
              ) : visible.length === 0 ? (
                <div className="rounded-2xl border border-dashed p-7 text-center"><CircleOff className="mx-auto size-7 text-muted-foreground" /><h2 className="mt-3 text-sm font-semibold">暂时没有符合条件的陪玩</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">可调整搜索与性别条件，或由平台安排。</p><Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={() => { setKeyword(''); setGender('不限'); if (scenario === 'empty') onRetry() }}>重置筛选</Button></div>
              ) : (
                <div className="grid grid-cols-2 gap-3" aria-label="可选陪玩列表">
                  {visible.map(item => {
                    const isSelected = selected.includes(item.id)
                    return <article key={item.id} className={cn('overflow-hidden rounded-2xl border bg-card transition-colors', isSelected && 'border-primary bg-primary/[0.045]', !item.canOrder && 'opacity-75')}>
                      <button type="button" disabled={validating} aria-pressed={isSelected} aria-label={`${isSelected ? '已选，点击取消' : '选择'} ${item.name}`} className="w-full p-3 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => toggleProvider(item)}>
                        <div className={cn('relative flex h-[104px] items-end justify-center overflow-hidden rounded-xl bg-gradient-to-br', item.tone)}><span className="absolute right-2 top-2 rounded-md border border-white/10 bg-slate-950/35 px-1.5 py-0.5 text-[9px] text-white">{isSelected ? '已选' : item.canOrder ? item.recommended ? '推荐' : '可约' : '暂不可约'}</span><span className="mb-[-8px] grid size-[72px] place-items-center rounded-full border border-white/15 bg-white/10 text-2xl font-semibold shadow-lg">{item.initial}</span>{isSelected && <span className="absolute bottom-2 right-2 grid size-6 place-items-center rounded-full bg-primary text-primary-foreground"><Check className="size-3.5" /></span>}</div>
                        <span className="mt-3 flex items-center justify-between gap-2"><strong className="truncate text-sm">{item.name}</strong><span className="shrink-0 text-[9px] text-muted-foreground">{item.gender === 'FEMALE' ? '女陪' : '男陪'}</span></span>
                        <span className="mt-1 block truncate text-[10px] text-muted-foreground">{item.level}</span>
                        <span className="mt-3 block border-t pt-2 text-[9px] text-muted-foreground">本月接单额</span><span className="mt-0.5 block text-xs font-semibold tabular-nums">{formatWaveAmount(item.monthly)} <small className="font-normal text-muted-foreground">浪花</small></span>
                      </button>
                      <button type="button" disabled={validating} className="flex min-h-11 w-full items-center justify-center gap-1 border-t text-[10px] text-muted-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={onDetail} aria-label={`查看陪玩 ${item.name} 的详情`}>查看详情<ChevronRight className="size-3" /></button>
                    </article>
                  })}
                </div>
              )}
              <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>确认前会重新核对服务人数与陪玩接单状态。本页仅操作示例数据，不创建订单。</p></div>
            </>
          )}
        </div>
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && <div className="absolute inset-x-0 bottom-[76px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl"><div className="flex items-center gap-3"><div className="min-w-[104px]"><p className="text-xs font-semibold">已选 {selected.length} / {limit} 位</p><p className={cn('mt-0.5 max-w-[128px] truncate text-[9px] text-muted-foreground', !selectionValid && 'text-warning')}>{hint}</p></div><Button type="button" className="h-12 flex-1 rounded-xl text-xs" disabled={!selectionReady || validating} onClick={confirmSelection}>{validating && <LoaderCircle className="size-4 animate-spin" />}{validating ? '正在核对接单信息' : selected.length ? `确认选择（${selected.length} / ${limit}）` : '由平台安排'}</Button></div></div>}
    </PhoneFrame>
  )
}
