import { useMemo, useState, type ReactNode } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, CalendarCheck2, CalendarClock, Check, Gamepad2, LoaderCircle, LogIn, ShieldCheck, UserRound } from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'

export type ReservationCreateScenario = 'default' | 'guest' | 'loading' | 'error' | 'invalid-time' | 'invalid-rounds' | 'submitting' | 'submit-error' | 'uncertain'

interface ReservationCreatePageProps {
  scenario: ReservationCreateScenario
  service: CatalogService
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onList: () => void
  onBrowse: () => void
  onCreated: () => void
  onRetry: () => void
}

const durations = Array.from({ length: 47 }, (_, index) => (index + 2) * 15)

export function ReservationCreatePage({ scenario, service, onLogin, onBack, onHome, onCompanions, onOrders, onList, onBrowse, onCreated, onRetry }: ReservationCreatePageProps) {
  const [day, setDay] = useState('2026-11-04')
  const [time, setTime] = useState(scenario === 'invalid-time' ? '20:07' : '20:00')
  const [duration, setDuration] = useState(60)
  const [rounds, setRounds] = useState(scenario === 'invalid-rounds' ? '0' : '2')
  const [region, setRegion] = useState('微信区')
  const [nickname, setNickname] = useState('海浪收藏家')
  const [uid, setUid] = useState('83921044')
  const [remark, setRemark] = useState('希望轻松沟通，开局前确认位置。')
  const [busy, setBusy] = useState(scenario === 'submitting')
  const [uncertain, setUncertain] = useState(scenario === 'uncertain')
  const [submitError, setSubmitError] = useState(scenario === 'submit-error' ? '当前时段已不可预约，请调整时间后重试。' : '')
  const [validationError, setValidationError] = useState('')
  const schedule = useMemo(() => getSchedule(day, time, duration), [day, time, duration])
  const blocked = busy || uncertain
  const normalState = !['guest', 'loading', 'error'].includes(scenario)

  function updateTime(value: string) { setTime(value); setSubmitError(''); setValidationError('') }
  function submit() {
    if (blocked) return
    const start = new Date(`${day}T${time}:00+08:00`)
    const quantity = Number(rounds)
    if (!Number.isFinite(start.getTime()) || start.getTime() <= new Date('2026-11-03T17:00:00+08:00').getTime()) { setValidationError('请选择未来的预约时间'); return }
    if (Number(time.slice(-2)) % 15 !== 0) { setValidationError('开始时间请按 15 分钟选择'); return }
    if (!Number.isInteger(quantity) || quantity < 1) { setValidationError('请填写有效的服务数量'); return }
    setValidationError('')
    setSubmitError('')
    setBusy(true)
    window.setTimeout(() => { setBusy(false); onCreated() }, 550)
  }
  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onList()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className={cn('pb-32 md:pb-12', normalState && !uncertain && 'pb-44 md:pb-12')}>
        <div className="flex items-center justify-between px-4 py-2 md:px-6"><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-sm" onClick={onBack}><ArrowLeft className="size-4" />陪玩详情</Button><Badge variant="outline" className="rounded-md text-xs">新建预约 · 示例</Badge></div>

        <header className="px-4 pb-6 pt-2 md:px-6">
          <h1 className="text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">预约陪玩</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">先锁定时间，陪玩确认后再核对价格。</p>
          <div className="mt-5 flex max-w-md items-center" aria-label="预约流程：选择时间、等待确认、转成订单"><Step number="1" label="选择时间" active /><span className="mx-3 h-px min-w-3 flex-1 bg-border" /><Step number="2" label="等待确认" /><span className="mx-3 h-px min-w-3 flex-1 bg-border" /><Step number="3" label="转成订单" /></div>
        </header>

        {scenario === 'guest' ? <GuestState onLogin={onLogin} /> : scenario === 'loading' ? (
          <div className="grid gap-6 px-4 md:grid-cols-[minmax(0,1fr)_360px] md:px-6" aria-busy="true" aria-label="正在加载预约信息"><div className="space-y-6"><Skeleton className="h-24 rounded-[12px]" /><Skeleton className="h-80 rounded-[12px]" /><Skeleton className="h-96 rounded-[12px]" /></div><Skeleton className="h-80 rounded-[12px]" /></div>
        ) : scenario === 'error' ? <ErrorState onRetry={onRetry} onBrowse={onBrowse} /> : (
          <>
            <section className="mx-4 rounded-[12px] border border-primary/15 bg-primary/[0.055] p-4 md:mx-6 md:p-5" aria-labelledby="reservation-service-title">
              <div className="flex items-center gap-3"><Avatar className="size-12 border"><AvatarFallback className="bg-primary/10 text-base font-semibold text-primary">澈</AvatarFallback></Avatar><div className="min-w-0 flex-1"><p className="text-xs text-muted-foreground">本次预约</p><h2 id="reservation-service-title" className="mt-1 break-words text-base font-semibold">{service.name}</h2><p className="mt-1 text-xs text-muted-foreground">小澈 · 专属陪玩</p></div><Badge variant="outline" className="shrink-0 rounded-md border-primary/20 bg-background text-xs text-primary">待提交</Badge></div>
            </section>

            <form id="reservation-create-form" noValidate className="grid items-start gap-6 px-4 py-6 md:grid-cols-[minmax(0,1fr)_360px] md:px-6" onSubmit={event => { event.preventDefault(); submit() }}>
              <div className="space-y-6">
                <FormSection number="01" title="选择时间" note="按你的方便时间安排" icon={CalendarClock}>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="预约日期" htmlFor="reservation-day"><Input id="reservation-day" type="date" min="2026-11-04" max="2026-12-03" value={day} onChange={event => { setDay(event.target.value); setSubmitError(''); setValidationError('') }} className="h-12 rounded-[8px] bg-background px-3 text-sm tabular-nums" disabled={blocked} required /></Field>
                    <Field label="开始时间" htmlFor="reservation-time"><Input id="reservation-time" type="time" step={900} value={time} onChange={event => updateTime(event.target.value)} className="h-12 rounded-[8px] bg-background px-3 text-sm tabular-nums" disabled={blocked} required /></Field>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">开始时间按 15 分钟选择，最多可提前 30 天预约</p>
                  <div className="mt-4"><Field label="服务时长" htmlFor="reservation-duration"><Select value={String(duration)} onValueChange={value => setDuration(Number(value))} disabled={blocked}><SelectTrigger id="reservation-duration" className="h-12 w-full rounded-[8px] bg-background text-sm"><SelectValue /></SelectTrigger><SelectContent className="max-h-72">{durations.map(minutes => <SelectItem key={minutes} value={String(minutes)} className="min-h-11">{minutes} 分钟{minutes >= 60 && minutes % 60 === 0 ? `（${minutes / 60} 小时）` : ''}</SelectItem>)}</SelectContent></Select></Field></div>
                  <div className="mt-4 rounded-[8px] bg-muted/45 p-4"><p className="text-xs text-muted-foreground">预计时段 · 北京时间</p><p className="mt-2 text-sm font-semibold leading-6 text-primary tabular-nums">{schedule.text}</p><p className="mt-2 text-xs text-muted-foreground">共 {duration} 分钟，{rounds || '0'} 份服务</p></div>
                </FormSection>

                <FormSection number="02" title="游戏资料" note="帮助陪玩提前做好准备" icon={Gamepad2}>
                  <Field label="服务数量" htmlFor="reservation-rounds"><div className="relative"><Input id="reservation-rounds" type="number" min={1} step={1} inputMode="numeric" value={rounds} onChange={event => { setRounds(event.target.value); setValidationError('') }} className="h-12 rounded-[8px] bg-background pr-12 text-sm tabular-nums" disabled={blocked} required /><span className="pointer-events-none absolute right-4 top-3.5 text-sm text-muted-foreground">份</span></div></Field>
                  <div className="mt-4 grid gap-4 sm:grid-cols-2"><Field label="游戏区服" htmlFor="reservation-region" optional><Input id="reservation-region" value={region} onChange={event => setRegion(event.target.value)} maxLength={50} placeholder="例如：微信区" className="h-12 rounded-[8px] bg-background text-sm" disabled={blocked} /></Field><Field label="游戏昵称" htmlFor="reservation-nickname" optional><Input id="reservation-nickname" value={nickname} onChange={event => setNickname(event.target.value)} maxLength={50} placeholder="方便陪玩识别你" className="h-12 rounded-[8px] bg-background text-sm" disabled={blocked} /></Field></div>
                  <div className="mt-4"><Field label="游戏 ID" htmlFor="reservation-uid" optional><Input id="reservation-uid" value={uid} onChange={event => setUid(event.target.value)} maxLength={50} placeholder="填写游戏内 ID" className="h-12 rounded-[8px] bg-background text-sm" disabled={blocked} /></Field></div>
                  <div className="mt-4"><Field label="备注" htmlFor="reservation-remark" optional><Textarea id="reservation-remark" value={remark} onChange={event => setRemark(event.target.value)} maxLength={255} placeholder="如希望的位置、打法或其他准备事项" className="min-h-24 rounded-[8px] bg-background text-sm leading-6" disabled={blocked} /><p className="mt-2 text-right text-xs text-muted-foreground tabular-nums">{remark.length} / 255</p></Field></div>
                </FormSection>
              </div>

              <aside className="space-y-4 md:sticky md:top-6">
                {uncertain ? <section role="alert" className="rounded-[12px] border border-warning/25 bg-warning/5 p-5"><div className="flex items-center gap-2"><AlertCircle className="size-5 text-warning" /><h2 className="text-base font-semibold">预约结果待核对</h2></div><p className="mt-3 text-sm leading-6 text-muted-foreground">为避免重复预约，请先查看当前账号是否已有这次记录。</p><div className="mt-4 rounded-[8px] bg-background/60 p-3 text-xs leading-5 text-warning"><p className="font-medium">待核对的预约</p><p className="mt-1 tabular-nums">{day} {time}（北京时间）</p><p className="mt-1">{duration} 分钟 · {rounds || '0'} 份服务</p></div><Button type="button" className="mt-4 h-11 w-full rounded-[8px]" onClick={onList}>查看我的预约<ArrowRight className="size-4" /></Button><Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px]" onClick={event => { event.preventDefault(); setUncertain(false) }}>已核对，继续预约</Button></section> : <section className="rounded-[12px] border bg-card p-5 shadow-sm" aria-labelledby="reservation-submit-summary"><div className="flex items-center gap-2"><ShieldCheck className="size-5 text-primary" /><h2 id="reservation-submit-summary" className="text-base font-semibold">提交前确认</h2></div><div className="mt-4"><SummaryRow label="服务" value={service.name} /><SummaryRow label="陪玩" value="小澈" /><SummaryRow label="时长" value={`${duration} 分钟`} /><SummaryRow label="数量" value={`${rounds || '0'} 份服务`} /></div><div className="mt-4 rounded-[8px] bg-primary/[0.055] p-3"><p className="text-xs text-muted-foreground">预约时段</p><p className="mt-2 text-sm font-semibold leading-6 text-primary tabular-nums">{schedule.text}</p></div><p className="mt-4 text-xs leading-5 text-muted-foreground">提交预约不会扣款。陪玩确认后，可查看实时报价并决定是否成单。</p><Button type="submit" className="mt-4 hidden h-12 w-full rounded-[8px] md:flex" disabled={blocked}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <CalendarCheck2 className="size-4" />}{busy ? '正在提交' : '提交预约'}</Button><Button type="button" variant="ghost" className="mt-2 hidden h-11 w-full rounded-[8px] md:flex" onClick={onList} disabled={busy}>查看我的预约</Button></section>}

                {(validationError || submitError) && <div role="alert" className="rounded-[8px] border border-destructive/25 bg-destructive/5 p-3 text-xs leading-5 text-destructive">{validationError || submitError}{submitError && <Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px] text-destructive" onClick={onBrowse}>重新选择服务</Button>}</div>}
                <p className="px-3 text-center text-xs leading-5 text-muted-foreground">本地交互原型 · 不会创建真实预约</p>
              </aside>
            </form>
          </>
        )}
      </main>

      {normalState && !uncertain && <div className="absolute inset-x-0 bottom-[76px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:hidden"><div className="grid grid-cols-[104px_minmax(0,1fr)] gap-3"><Button type="button" variant="outline" className="h-12 rounded-[8px]" onClick={onList} disabled={busy}>我的预约</Button><Button type="submit" form="reservation-create-form" className="h-12 rounded-[8px]" disabled={blocked}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <CalendarCheck2 className="size-4" />}{busy ? '正在提交' : '提交预约'}</Button></div></div>}
    </PhoneFrame>
  )
}

function Step({ number, label, active = false }: { number: string; label: string; active?: boolean }) {
  return <div className={cn('flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground', active && 'font-medium text-primary')}><span className={cn('grid size-6 place-items-center rounded-full bg-muted text-xs', active && 'bg-primary text-primary-foreground')}>{active ? <Check className="size-3.5" /> : number}</span>{label}</div>
}
function FormSection({ number, title, note, icon: Icon, children }: { number: string; title: string; note: string; icon: typeof UserRound; children: ReactNode }) {
  return <section className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5"><div className="mb-5 flex items-start gap-3"><span className="pt-0.5 font-mono text-xs font-semibold text-primary">{number}</span><Icon className="mt-0.5 size-4 text-primary" /><div><h2 className="text-base font-semibold">{title}</h2><p className="mt-1 text-xs text-muted-foreground">{note}</p></div></div>{children}</section>
}
function Field({ label, htmlFor, optional = false, children }: { label: string; htmlFor: string; optional?: boolean; children: ReactNode }) {
  return <div><Label htmlFor={htmlFor} className="mb-2 flex items-center gap-2 text-sm">{label}{optional && <span className="text-xs font-normal text-muted-foreground">选填</span>}</Label>{children}</div>
}
function SummaryRow({ label, value }: { label: string; value: string }) {
  return <div className="flex min-h-10 items-start justify-between gap-4 border-b py-2 text-sm last:border-0"><span className="shrink-0 text-muted-foreground">{label}</span><span className="break-words text-right font-medium">{value}</span></div>
}
function getSchedule(day: string, time: string, minutes: number) {
  const start = new Date(`${day}T${time}:00+08:00`)
  if (!Number.isFinite(start.getTime())) return { text: '请选择有效时间' }
  const end = new Date(start.getTime() + minutes * 60000)
  const endDay = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(end).replaceAll('/', '-')
  const endTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(end)
  return { text: `${day} ${time} — ${endDay === day ? '' : `${endDay} `}${endTime}` }
}
function GuestState({ onLogin }: { onLogin: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div className="mx-auto max-w-xl rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><CalendarClock className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后即可预约</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">预约记录会安全保存在当前账号中。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></div></section>
}
function ErrorState({ onRetry, onBrowse }: { onRetry: () => void; onBrowse: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">预约信息暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请重试，或重新选择服务和陪玩。当前没有提交预约。</p><div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row"><Button type="button" className="h-11 rounded-[8px]" onClick={onRetry}>重新加载</Button><Button type="button" variant="outline" className="h-11 rounded-[8px]" onClick={onBrowse}>重新选择服务</Button></div></div></section>
}
