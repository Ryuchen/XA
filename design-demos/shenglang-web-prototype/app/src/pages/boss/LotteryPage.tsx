import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  CircleOff,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trophy,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

export type LotteryScenario = 'default' | 'loading' | 'error' | 'drawing' | 'revealing' | 'success' | 'ten-success' | 'pending' | 'credential-error' | 'accepted-error' | 'disabled' | 'insufficient' | 'daily-empty' | 'empty'
type Phase = 'idle' | 'request' | 'reveal' | 'done'
interface DrawResult { id: number; name: string; rarity: 'R' | 'SR' | 'SSR'; status: string; pity?: boolean; ten?: boolean }

interface LotteryPageProps {
  scenario: LotteryScenario
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onRecords: () => void
  onClaim: () => void
  onRetry: () => void
}

const prizes = [
  { name: '20 积分', rarity: 'R', probability: '62.00' },
  { name: '68 积分', rarity: 'R', probability: '24.00' },
  { name: '限定头像框', rarity: 'SR', probability: '8.00' },
  { name: '限定高亮 TAG', rarity: 'SR', probability: '4.00' },
  { name: '68 元体验单', rarity: 'SSR', probability: '1.50' },
  { name: '声浪定制耳机', rarity: 'SSR', probability: '0.50' },
]
const singleResult: DrawResult[] = [{ id: 1, name: '20 积分', rarity: 'R', status: '已到账' }]
const tenResults: DrawResult[] = [
  { id: 1, name: '20 积分', rarity: 'R', status: '已到账' }, { id: 2, name: '68 积分', rarity: 'R', status: '已到账' },
  { id: 3, name: '20 积分', rarity: 'R', status: '已到账' }, { id: 4, name: '限定头像框', rarity: 'SR', status: '待领取' },
  { id: 5, name: '20 积分', rarity: 'R', status: '已到账' }, { id: 6, name: '20 积分', rarity: 'R', status: '已到账' },
  { id: 7, name: '68 积分', rarity: 'R', status: '已到账' }, { id: 8, name: '20 积分', rarity: 'R', status: '已到账' },
  { id: 9, name: '20 积分', rarity: 'R', status: '已到账' }, { id: 10, name: '限定高亮 TAG', rarity: 'SR', status: '待领取', ten: true },
]

export function LotteryPage({ scenario, onBack, onHome, onCompanions, onOrders, onRecords, onClaim, onRetry }: LotteryPageProps) {
  const initialResults = ['success', 'revealing'].includes(scenario) ? singleResult : scenario === 'ten-success' ? tenResults : []
  const initialPhase: Phase = scenario === 'drawing' ? 'request' : scenario === 'revealing' ? 'reveal' : ['success', 'ten-success'].includes(scenario) ? 'done' : 'idle'
  const [phase, setPhase] = useState<Phase>(initialPhase)
  const [rotation, setRotation] = useState(0)
  const [results, setResults] = useState<DrawResult[]>(initialResults)
  const [revealed, setRevealed] = useState(scenario === 'revealing' ? 0 : initialResults.length)
  const [simpleMotion, setSimpleMotion] = useState(false)
  const [points, setPoints] = useState(scenario === 'insufficient' ? 20 : 1280)
  const [daily, setDaily] = useState(scenario === 'daily-empty' ? 0 : 18)
  const [pity, setPity] = useState(168)
  const [pending, setPending] = useState(['pending', 'credential-error'].includes(scenario))
  const [credentialError, setCredentialError] = useState(scenario === 'credential-error')
  const [drawError, setDrawError] = useState(scenario === 'pending' ? '上次抽奖结果尚未确认。请重试同一笔抽奖，不会重复扣除积分，也可先查看中奖记录。' : scenario === 'credential-error' ? '无法读取抽奖凭证，请检查设备存储后重新读取，或先查看中奖记录。' : scenario === 'accepted-error' ? '本次抽奖已受理，展示结果暂不可用。请到中奖记录核对并领取奖品。' : '')
  const [retryCount, setRetryCount] = useState<1 | 10>(scenario === 'pending' || scenario === 'credential-error' ? 10 : 1)
  const timers = useRef<number[]>([])
  const busy = phase === 'request' || phase === 'reveal'
  const enabled = !['disabled', 'empty'].includes(scenario)
  const tenAvailable = scenario !== 'disabled'
  const singleCost = 60

  useEffect(() => () => { timers.current.forEach(clearTimeout) }, [])

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function later(action: () => void, delay: number) {
    const timer = window.setTimeout(action, delay)
    timers.current.push(timer)
  }

  function finish(nextResults: DrawResult[], count: 1 | 10, retry: boolean) {
    setResults(nextResults)
    setRevealed(simpleMotion ? nextResults.length : 0)
    setPhase(simpleMotion ? 'done' : 'reveal')
    setPending(false)
    setCredentialError(false)
    setDrawError('')
    if (!retry) {
      setPoints(current => Math.max(0, current - singleCost * count + (count === 1 ? 20 : 248)))
      setDaily(current => Math.max(0, current - count))
      setPity(current => (current + count) % 240)
    }
    if (!simpleMotion) {
      nextResults.forEach((_, index) => later(() => setRevealed(index + 1), 220 + index * 100))
      later(() => setPhase('done'), 400 + nextResults.length * 100)
    }
  }

  function draw(count: 1 | 10, retry = false) {
    if (busy || (!retry && pending)) return
    if (!retry && !enabled) return
    if (!retry && count === 10 && !tenAvailable) return
    if (!retry && daily < count) { setDrawError(`今日剩余 ${daily} 次，无法${count === 10 ? '十连抽' : '继续抽奖'}`); return }
    if (!retry && points < singleCost * count) { setDrawError('积分不足，继续累积后再来。'); return }
    setPhase('request')
    setDrawError('')
    setResults([])
    setRevealed(0)
    setRetryCount(count)
    setRotation(current => current + 1440)
    later(() => finish(count === 10 ? tenResults : singleResult, count, retry), simpleMotion ? 300 : 900)
  }

  function skipAnimation() {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setRevealed(results.length)
    setPhase('done')
  }

  function reloadCredential() {
    setCredentialError(false)
    setPending(true)
    setDrawError('已重新读取抽奖凭证。请重试同一笔十连，不会重复扣除积分。')
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的积分</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">幸运转盘 · 示例</Badge></div>
        <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪俱乐部 · 积分好礼</p><h1 className="mt-1 text-[28px] font-semibold tracking-[-0.04em]">海浪幸运季</h1><div className="mt-2 flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">我的积分 <strong className="ml-1 text-lg text-primary tabular-nums">{points.toLocaleString()}</strong></p><Button type="button" variant="ghost" className="h-11 px-2 text-xs text-primary" onClick={onRecords}>中奖记录<ChevronRight className="size-3.5" /></Button></div></header>

        {scenario === 'loading' ? <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载幸运转盘"><Skeleton className="h-[430px] w-full rounded-3xl" /><Skeleton className="h-32 w-full rounded-2xl" /></div> : scenario === 'error' ? <section className="px-4"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">转盘暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会扣除积分或生成抽奖。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section> : scenario === 'empty' ? <section className="px-4"><div className="rounded-3xl border border-dashed p-7 text-center"><CircleOff className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">奖池准备中</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">本期奖品与概率发布后即可参与，请先查看中奖记录或积分好礼。</p><Button type="button" variant="outline" className="mt-6 h-11 w-full" onClick={onRecords}>查看中奖记录</Button></div></section> : (
          <div className="space-y-4 px-4 pb-5">
            <section className={cn('overflow-hidden rounded-3xl border bg-card p-4 text-center', phase === 'reveal' && 'border-primary/35')} aria-labelledby="wheel-title">
              <div className="flex items-start justify-between gap-3 text-left"><h2 id="wheel-title" className="text-sm font-semibold">每一抽，都有好礼</h2><span className="text-[10px] text-muted-foreground">十连至少 1 份非积分奖</span></div>
              <div className="relative mx-auto my-5 size-[232px]">
                <div className="absolute left-1/2 top-[-5px] z-20 size-0 -translate-x-1/2 border-x-[10px] border-t-[24px] border-x-transparent border-t-sky-300" />
                <div className={cn('absolute inset-0 rounded-full border-4 border-primary/50 shadow-[0_0_50px_-12px_rgba(56,189,248,0.38)] transition-transform [transition-duration:1400ms] ease-out', simpleMotion && 'duration-0')} style={{ transform: `rotate(${rotation}deg)`, background: 'conic-gradient(from -30deg, #172554 0deg 60deg, #1e3a8a 60deg 120deg, #0f172a 120deg 180deg, #1e40af 180deg 240deg, #172554 240deg 300deg, #334155 300deg 360deg)' }}>
                  {prizes.map((prize, index) => <span key={prize.name} className="absolute left-1/2 top-1/2 w-[86px] origin-center text-[10px] font-medium leading-4 text-sky-50/85" style={{ transform: `translate(-50%, -50%) rotate(${index * 60}deg) translateY(-78px) rotate(${-index * 60}deg)` }}>{prize.name}</span>)}
                </div>
                <div className="absolute left-1/2 top-1/2 z-10 grid size-[84px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-4 border-card bg-[linear-gradient(145deg,#38bdf8,#2563eb)] text-white shadow-lg"><div><Trophy className="mx-auto size-5" /><p className="mt-1 text-[10px] font-semibold">{phase === 'request' ? '结果确认中' : phase === 'reveal' ? '好运将至' : '声浪好礼'}</p></div></div>
              </div>
              <div className="grid grid-cols-2 gap-2"><Button type="button" variant="outline" className="h-14 flex-col border-primary/40 text-sm text-primary" disabled={busy || pending || !enabled || daily < 1} onClick={() => draw(1)}><span>抽 1 次</span><span className="text-[10px] font-normal text-muted-foreground">60 积分</span></Button><Button type="button" className="h-14 flex-col bg-[linear-gradient(135deg,#2563eb,#38bdf8)] text-sm" disabled={busy || pending || !enabled || !tenAvailable || daily < 10} onClick={() => draw(10)}><span>抽 10 次</span><span className="text-[10px] font-normal text-white/70">600 积分</span></Button></div>
              {!enabled && <p className="mt-3 text-[11px] text-muted-foreground">奖池准备中，开放后即可参与</p>}
              <p className="mt-3 text-[10px] text-muted-foreground">今日剩余 {daily} 次 · 十连消耗 10 次机会</p>
              {phase === 'request' && <p role="status" className="mt-2 flex items-center justify-center gap-1.5 text-[11px] text-primary"><LoaderCircle className="size-3.5 animate-spin" />正在确认{retryCount === 10 ? '十连' : '单抽'}结果，请勿重复操作…</p>}
              <div className="mt-2 flex min-h-11 items-center justify-between"><label className="flex min-h-11 items-center gap-2 text-[11px] text-muted-foreground"><Switch checked={simpleMotion} disabled={busy && phase === 'request'} onCheckedChange={value => { setSimpleMotion(value); if (value && phase === 'reveal') skipAnimation() }} aria-label="简化动画" className="relative before:absolute before:-inset-x-3 before:-inset-y-4 before:content-['']" />简化动画</label>{phase === 'reveal' && <Button type="button" variant="ghost" className="h-11 px-2 text-xs text-primary" onClick={skipAnimation}>跳过动画<ArrowRight className="size-3.5" /></Button>}</div>
            </section>

            <section className="rounded-2xl border bg-card p-4" aria-label="体验单保底进度"><div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">68 体验单保底</h2><span className="text-sm font-semibold text-warning tabular-nums">{pity} / 240</span></div><Progress value={pity / 240 * 100} className="mt-3 h-2" aria-label="体验单保底进度" /><div className="mt-2 flex justify-between gap-2 text-[10px] text-muted-foreground"><span>最多再 <strong className="text-primary">{240 - pity}</strong> 抽获得</span><span>单抽、十连共享进度</span></div><p className="mt-2 text-[10px] leading-4 text-muted-foreground">连续 240 抽必得；提前获得后重新累计。</p></section>

            {drawError && <section role="alert" className="rounded-2xl border border-warning/25 bg-warning/[0.06] p-4"><div className="flex gap-2"><AlertCircle className="mt-0.5 size-4 shrink-0 text-warning" /><p className="text-[11px] leading-5 text-muted-foreground">{drawError}</p></div><div className="mt-3 flex flex-wrap gap-2">{credentialError ? <Button type="button" variant="outline" className="h-11 flex-1 text-xs" onClick={reloadCredential}>重新读取凭证</Button> : pending && <Button type="button" variant="outline" className="h-11 flex-1 text-xs" disabled={busy} onClick={() => draw(retryCount, true)}>重试同一{retryCount === 10 ? '十连' : '单抽'}</Button>}<Button type="button" variant="ghost" className="h-11 flex-1 text-xs text-primary" onClick={onRecords}>查看中奖记录<ChevronRight className="size-3.5" /></Button></div></section>}

            {results.length > 0 && phase !== 'request' && <section id="lottery-results" className="rounded-2xl border bg-card p-4" aria-labelledby="results-title"><div className="flex items-center justify-between gap-3"><h2 id="results-title" className="text-sm font-semibold">{results.length === 10 ? '本次十连奖品' : '本次获得'}</h2><span className="text-[10px] text-muted-foreground">{revealed} / {results.length} 已揭晓</span></div>{results.length === 10 && <p className="mt-2 text-[11px] font-medium text-primary">已获 2 份非积分奖</p>}<div className={cn('mt-3 grid grid-cols-2 gap-2', results.length === 1 && 'grid-cols-1')}>{results.map((result, index) => <article key={result.id} className={cn('min-h-[142px] rounded-xl border p-3 text-left', index < revealed ? result.rarity === 'SSR' ? 'border-warning/35 bg-warning/[0.06]' : result.rarity === 'SR' ? 'border-primary/30 bg-primary/[0.06]' : 'bg-muted/25' : 'border-dashed bg-muted/20')}>{index < revealed ? <><div className="flex items-center justify-between gap-2"><span className={cn('text-[10px] font-bold', result.rarity === 'SSR' ? 'text-warning' : 'text-primary')}>{result.rarity} · {result.rarity === 'R' ? '积分' : result.rarity === 'SR' ? '稀有' : '典藏'}</span><span className="text-[10px] text-muted-foreground">{String(index + 1).padStart(2, '0')}</span></div><h3 className="mt-3 text-sm font-semibold">{result.name}</h3><p className="mt-1 text-[10px] text-muted-foreground">{result.status}{result.ten ? ' · 十连保底' : ''}</p><Button type="button" variant="outline" className="mt-3 h-11 w-full text-[11px]" onClick={onClaim}>{result.status === '已到账' ? '查看奖励' : '领取奖励'}<ChevronRight className="size-3.5" /></Button></> : <div className="grid h-full min-h-[116px] place-items-center content-center text-muted-foreground"><Sparkles className="size-5" /><p className="mt-2 text-[10px]">等待揭晓 · {String(index + 1).padStart(2, '0')}</p></div>}</article>)}</div><p className="mt-3 text-[10px] leading-5 text-muted-foreground">积分奖励自动到账；其他奖励请点击领取。所有结果已保存至中奖记录。</p></section>}

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="odds-title"><h2 id="odds-title" className="text-sm font-semibold">本期奖池 · 基础概率</h2><div className="mt-2 divide-y">{prizes.map(prize => <div key={prize.name} className="flex min-h-11 items-center justify-between gap-3 py-2 text-xs"><span className="flex items-center gap-2"><Badge variant="secondary" className={cn('w-10 justify-center px-1 text-[9px]', prize.rarity === 'SSR' && 'text-warning')}>{prize.rarity}</Badge>{prize.name}</span><span className="text-muted-foreground tabular-nums">{prize.probability}%</span></div>)}</div><p className="mt-3 text-[10px] leading-5 text-muted-foreground">转盘分区仅用于展示，基础概率以公示为准。若十连前 9 抽均为积分奖，第 10 抽按正基础权重从非积分奖中抽取；同时触发体验单保底时优先获得体验单。</p></section>
            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>抽奖结果以服务器受理记录为准。结果未知时只重试同一笔请求，不会重复扣除积分。</p></div>
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}
