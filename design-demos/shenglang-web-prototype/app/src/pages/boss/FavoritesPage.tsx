import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  Gamepad2,
  Headphones,
  Heart,
  LoaderCircle,
  LogIn,
  RefreshCw,
  ShoppingBag,
  Undo2,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'
import { cn } from '@/lib/utils'

export type FavoritesScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'removing' | 'remove-error' | 'undo' | 'undoing' | 'undo-error' | 'unavailable'
type FavoriteService = CatalogService & { available: boolean }

interface FavoritesPageProps {
  scenario: FavoritesScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onOpen: (service: CatalogService) => void
  onBrowse: () => void
  onRetry: () => void
}

const favoriteServices: FavoriteService[] = [
  { id: 1, name: '王者娱乐双排', description: '轻松沟通，快乐上分', game: '王者荣耀', tab: 'experience', platform: '手游', level: '不限难度', price: 29, unit: '份', variants: 3, providerEligible: true, coupon: true, tone: 'from-sky-400/35 via-blue-600/25 to-indigo-700/30', available: true },
  { id: 3, name: '王者默契陪伴', description: '认真沟通，稳定发挥', game: '王者荣耀', tab: 'regular', platform: '手游', level: '机密', price: 68, unit: '小时', variants: 4, providerEligible: true, tone: 'from-blue-400/30 via-indigo-500/25 to-slate-700/30', available: true },
  { id: 5, name: '峡谷技术双排', description: '对局复盘，技术提升', game: '英雄联盟', tab: 'companion', platform: '端游', level: '绝密', price: 56, unit: '小时', variants: 3, tone: 'from-sky-400/25 via-blue-600/25 to-slate-700/30', available: true },
  { id: 6, name: '周末欢乐盲盒', description: '随机玩法，惊喜开局', game: '王者荣耀', tab: 'fun', platform: '手游', level: '不限难度', price: 19, unit: '份', variants: 1, providerEligible: true, tone: 'from-cyan-400/25 via-blue-500/20 to-indigo-700/25', available: true },
]

export function FavoritesPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onOpen, onBrowse, onRetry }: FavoritesPageProps) {
  const source = favoriteServices.map(item => scenario === 'unavailable' && item.id === 3 ? { ...item, available: false } : item)
  const [items, setItems] = useState<FavoriteService[]>(['undo', 'undoing'].includes(scenario) ? source.filter(item => item.id !== 1) : source)
  const [removing, setRemoving] = useState(scenario === 'removing' ? 1 : 0)
  const [removeId, setRemoveId] = useState(scenario === 'remove-error' ? 1 : 0)
  const [undoItem, setUndoItem] = useState<FavoriteService | null>(['undo', 'undoing'].includes(scenario) ? source[0] : null)
  const [undoing, setUndoing] = useState(scenario === 'undoing')
  const [failedOnce, setFailedOnce] = useState(false)
  const [removeError, setRemoveError] = useState(scenario === 'remove-error' ? '未能取消「王者娱乐双排」，请重试或下拉刷新。' : scenario === 'undo-error' ? '恢复结果未确认，请刷新后查看收藏状态。' : '')
  const [feedback, setFeedback] = useState('')
  const busy = removing > 0 || undoing

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function openService(item: FavoriteService) {
    if (!item.available) {
      setFeedback('服务暂不可购买，仍可管理这条收藏。')
      return
    }
    onOpen(item)
  }

  function remove(id: number) {
    const item = items.find(row => row.id === id)
    if (!item || busy) return
    setRemoving(id)
    setRemoveId(id)
    setUndoItem(null)
    setFeedback('')
    setRemoveError('')
    window.setTimeout(() => {
      if (scenario === 'remove-error' && !failedOnce) {
        setFailedOnce(true)
        setRemoving(0)
        setRemoveError(`未能取消「${item.name}」，请重试或下拉刷新。`)
        return
      }
      setItems(current => current.filter(row => row.id !== id))
      setUndoItem(item)
      setRemoving(0)
    }, 500)
  }

  function undo() {
    if (!undoItem || !undoItem.available || busy) return
    const item = undoItem
    setUndoing(true)
    setFeedback('')
    setRemoveError('')
    window.setTimeout(() => {
      setItems(current => [...current, item].sort((a, b) => favoriteServices.findIndex(row => row.id === a.id) - favoriteServices.findIndex(row => row.id === b.id)))
      setUndoItem(null)
      setUndoing(false)
      setFeedback(`已恢复收藏「${item.name}」`)
    }, 500)
  }

  function refreshStatus() {
    setRemoveError('')
    setFeedback('收藏状态已刷新，请以当前列表为准。')
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button>
          <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">服务收藏 · 示例</Badge>
        </div>
        <header className="px-4 pb-4 pt-2">
          <div className="flex items-start justify-between gap-3">
            <div><p className="text-[11px] font-medium text-primary">常用服务，随时继续</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">我的收藏</h1><p className="mt-1 text-xs text-muted-foreground">进入服务详情后可选择陪玩并下单</p></div>
            {scenario !== 'guest' && <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-primary/10 text-primary"><Heart className="size-5 fill-current" /></div>}
          </div>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Heart className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录同步我的收藏</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">常用服务集中在这里，下次选择陪玩与下单更从容。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
        ) : (
          <div className="space-y-4 px-4 pb-5">
            {removeError && <section role="alert" className="rounded-2xl border border-destructive/25 bg-destructive/[0.06] p-4"><div className="flex gap-2"><AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" /><p className="text-[11px] leading-5 text-muted-foreground">{removeError}</p></div>{scenario === 'undo-error' ? <Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" onClick={refreshStatus}><RefreshCw className="size-4" />刷新收藏状态</Button> : !busy && !undoItem && <Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" onClick={() => remove(removeId)}>重试取消</Button>}</section>}
            {feedback && <div role="status" className="flex gap-2 rounded-xl border border-primary/20 bg-primary/[0.06] p-3 text-[11px] leading-5 text-primary"><Check className="mt-0.5 size-4 shrink-0" />{feedback}</div>}
            {undoItem && <section role="status" className="flex items-center gap-3 rounded-2xl border border-primary/25 bg-primary/[0.07] p-3"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Undo2 className="size-4" /></div><div className="min-w-0 flex-1"><h2 className="text-xs font-semibold">已取消收藏</h2><p className="mt-1 truncate text-[10px] text-muted-foreground">{undoItem.name}</p>{!undoItem.available && <p className="mt-1 text-[10px] text-warning">服务暂不可购买，无法恢复</p>}</div>{undoItem.available && <Button type="button" variant="outline" className="h-11 shrink-0 px-4 text-xs" disabled={busy} onClick={undo}>{undoing && <LoaderCircle className="size-4 animate-spin" />}{undoing ? '恢复中' : '撤销'}</Button>}</section>}

            {scenario === 'loading' ? <div className="space-y-3" aria-busy="true" aria-label="正在加载我的收藏"><Skeleton className="h-36 w-full rounded-2xl" /><Skeleton className="h-36 w-full rounded-2xl" /><Skeleton className="h-36 w-full rounded-2xl" /></div> : scenario === 'error' ? <div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">收藏暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会取消或恢复任何收藏。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div> : scenario === 'empty' || (!items.length && !undoItem) ? <div className="rounded-3xl border border-dashed p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-muted/40 text-muted-foreground"><Heart className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">还没有收藏的服务</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">在服务详情点击收藏，常用的套餐就会出现在这里。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onBrowse}>探索更多服务<ArrowRight className="size-4" /></Button></div> : (
              <>
                <div className="flex items-center justify-between"><p className="text-xs text-muted-foreground"><strong className="mr-1 text-lg font-semibold text-foreground tabular-nums">{items.length}</strong>个收藏服务</p><span className="text-[10px] text-muted-foreground">价格以详情页实时报价为准</span></div>
                <section className="space-y-3" aria-label="收藏服务列表">
                  {items.map(item => <article key={item.id} className={cn('overflow-hidden rounded-2xl border bg-card', !item.available && 'border-muted')}><button type="button" className="flex min-h-[104px] w-full items-center gap-3 p-3 text-left transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => openService(item)} aria-label={`${item.name}，${item.available ? `${formatWaveAmount(item.price)} 浪花每${item.unit}，查看服务详情` : '暂不可购买'}`}><div className={cn('relative grid size-20 shrink-0 place-items-center overflow-hidden rounded-xl bg-gradient-to-br text-sky-100', item.tone)}><Gamepad2 className="size-8" strokeWidth={1.35} /><span className="absolute inset-x-0 bottom-0 bg-slate-950/45 px-1 py-1 text-center text-[9px] text-white">{item.game}</span></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h2 className="truncate text-sm font-semibold">{item.name}</h2>{!item.available && <Badge variant="secondary" className="shrink-0 text-[9px]">暂不可购买</Badge>}</div><p className="mt-1 truncate text-[10px] text-muted-foreground">{item.description}</p>{item.available ? <p className="mt-2 text-xl font-semibold tabular-nums text-primary">{formatWaveAmount(item.price)}<span className="ml-1 text-[10px] font-normal text-muted-foreground">浪花 / {item.unit}</span></p> : <p className="mt-2 text-[10px] text-muted-foreground">仍可取消收藏</p>}</div><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button><div className="flex min-h-12 items-center justify-between gap-2 border-t px-3"><span className="flex min-w-0 items-center gap-1.5 truncate text-[10px] text-muted-foreground"><Heart className="size-3 shrink-0 fill-current text-primary" />{item.available ? '已收藏 · 可查看详情' : '服务状态已变化'}</span><Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-[11px] text-muted-foreground" disabled={busy} onClick={() => remove(item.id)} aria-label={`取消收藏：${item.name}`}>{removing === item.id && <LoaderCircle className="size-3.5 animate-spin" />}{removing === item.id ? '取消中' : '取消收藏'}</Button></div></article>)}
                </section>
                <Button type="button" variant="outline" className="h-12 w-full" disabled={busy} onClick={onBrowse}><ShoppingBag className="size-4" />探索更多服务<ArrowRight className="size-4" /></Button>
              </>
            )}
            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><Headphones className="mt-0.5 size-4 shrink-0 text-primary" /><p>收藏服务不代表锁定价格或陪玩。进入详情后，可核对最新价格并选择可接单的陪玩。</p></div>
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}
