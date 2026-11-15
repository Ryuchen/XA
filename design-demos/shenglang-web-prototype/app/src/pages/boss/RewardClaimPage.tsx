import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  BadgeCheck,
  Check,
  Copy,
  Gift,
  Headphones,
  LoaderCircle,
  LogIn,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

export type RewardClaimScenario = 'default' | 'guest' | 'virtual' | 'tag' | 'naming' | 'completed' | 'cancelled' | 'loading' | 'error' | 'empty' | 'saving' | 'save-error' | 'conflict' | 'status-changed'
type ClaimKind = 'PHYSICAL' | 'VIRTUAL' | 'TAG' | 'NAMING'

interface RewardClaimPageProps {
  scenario: RewardClaimScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMembership: () => void
  onSupport: () => void
  onRetry: () => void
}

interface ClaimDetails { recipient: string; phone: string; address: string; game_uid: string; tag: string; provider_name: string; note: string }
const emptyDetails: ClaimDetails = { recipient: '', phone: '', address: '', game_uid: '', tag: '', provider_name: '', note: '' }

export function RewardClaimPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onMembership, onSupport, onRetry }: RewardClaimPageProps) {
  const kind: ClaimKind = scenario === 'virtual' ? 'VIRTUAL' : scenario === 'tag' || scenario === 'completed' ? 'TAG' : scenario === 'naming' ? 'NAMING' : 'PHYSICAL'
  const completed = scenario === 'completed'
  const cancelled = scenario === 'cancelled'
  const [details, setDetails] = useState<ClaimDetails>(scenario === 'conflict' ? { ...emptyDetails, recipient: '海浪收藏家', phone: '13800000000', address: '示例省示例市海浪路 18 号' } : emptyDetails)
  const [busy, setBusy] = useState(scenario === 'saving')
  const [dirty, setDirty] = useState(false)
  const [issue, setIssue] = useState(scenario === 'save-error' ? '资料保存失败，请检查网络后重试。' : scenario === 'conflict' ? '资料已在其他位置更新。可先复制草稿，再使用最新资料。' : scenario === 'status-changed' ? '权益状态已变化，刚填写的资料尚未保存。可复制后联系客服。' : '')
  const [feedback, setFeedback] = useState('')
  const name = kind === 'PHYSICAL' ? '声浪定制耳机' : kind === 'VIRTUAL' ? '限定游戏道具' : kind === 'TAG' ? '限定高亮 TAG' : '陪玩专属冠名'
  const status = cancelled ? '已取消' : completed ? '已完成' : '待交付'
  const editable = !completed && !cancelled && !['conflict', 'status-changed'].includes(scenario)

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function update(field: keyof ClaimDetails, value: string) {
    setDetails(current => ({ ...current, [field]: value }))
    setDirty(true)
    setIssue('')
    setFeedback('')
  }

  function save() {
    if (busy || issue) return
    setBusy(true)
    setFeedback('')
    window.setTimeout(() => { setBusy(false); setDirty(false); setFeedback(kind === 'TAG' ? '修改已提交，等待客服复核。' : '交付资料已保存，俱乐部将按此安排交付。') }, 500)
  }

  function useLatest() {
    setDetails(emptyDetails)
    setDirty(false)
    setIssue('')
    setFeedback('已使用最新交付资料。')
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />权益记录</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">奖品交付 · 示例</Badge></div>
        {scenario === 'guest' ? <section className="px-4 pt-4"><div className="rounded-3xl border bg-card p-7 text-center"><Gift className="mx-auto size-8 text-primary" /><h1 className="mt-4 text-xl font-semibold">登录查看奖品交付</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">查看本人权益进度并补充交付资料。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section> : scenario === 'loading' ? <div className="space-y-3 px-4 pt-4" aria-busy="true" aria-label="正在加载奖品交付"><Skeleton className="h-32 w-full rounded-2xl" /><Skeleton className="h-44 w-full rounded-2xl" /><Skeleton className="h-72 w-full rounded-2xl" /></div> : scenario === 'error' ? <section className="px-4 pt-4"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">交付信息暂时没有加载成功</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会改变权益状态或已保存资料。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section> : scenario === 'empty' ? <section className="px-4 pt-4"><div className="rounded-3xl border border-dashed p-7 text-center"><Gift className="mx-auto size-8 text-muted-foreground" /><h1 className="mt-4 text-xl font-semibold">没有可显示的奖品记录</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">请从中奖记录重新进入；若记录缺失，请联系俱乐部客服。</p><Button type="button" variant="outline" className="mt-6 h-11 w-full" onClick={onBack}>返回权益记录</Button></div></section> : (
          <>
            <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">积分兑换奖品 · #101</p><h1 className="mt-2 text-[28px] font-semibold leading-tight tracking-[-0.04em]">{name}</h1><div className="mt-3 flex flex-wrap items-center gap-2"><Badge variant={cancelled ? 'secondary' : 'outline'} className={cn(!cancelled && 'border-primary/20 bg-primary/5 text-primary')}>{status}</Badge>{kind === 'TAG' && <span className="text-[11px] text-muted-foreground">有效至 2026-12-03</span>}</div></header>
            <div className="space-y-4 px-4 pb-5">
              <section className="rounded-2xl border bg-card p-4" aria-labelledby="claim-progress"><h2 id="claim-progress" className="text-sm font-semibold">交付进度</h2><div className="mt-3 rounded-xl bg-primary/[0.07] p-3"><p className="text-[10px] font-medium text-primary">{completed || cancelled ? '当前安排' : '下一步安排'}</p><p className="mt-1 text-xs leading-5">{cancelled ? '本次权益已取消。' : completed ? '权益已完成交付。' : '可补充交付资料，俱乐部将为你安排。'}</p></div>{kind === 'TAG' && <p className={cn('mt-3 rounded-xl p-3 text-[11px] leading-5', completed ? 'bg-warning/[0.07] text-warning' : 'bg-muted/40 text-muted-foreground')}>{completed ? '标签待客服复核，暂不展示。可修改后提交复核，有效期不变。' : '希望展示的 TAG 最多 30 字，由俱乐部核对后发放。'}</p>}<div className="mt-3 divide-y rounded-xl bg-muted/30 px-3">{(kind === 'TAG' || kind === 'NAMING') && <ClaimFact label="权益时长" value="30 天" />}{kind === 'TAG' && <ClaimFact label="展示类型" value="高亮 TAG" />}{cancelled && <ClaimFact label="积分退回" value="已退回 2,880 积分，可在积分明细查看" />}</div></section>

              {(completed || cancelled) && <section className="rounded-2xl border bg-card p-4" aria-labelledby="submitted-title"><h2 id="submitted-title" className="text-sm font-semibold">已提交的资料</h2><div className="mt-2 divide-y">{kind === 'TAG' ? <ClaimFact label="TAG" value="星海逐浪者" /> : <><ClaimFact label="收件人" value="海浪收藏家" /><ClaimFact label="联系电话" value="138 **** 0000" /></>}</div></section>}

              {(!completed && !cancelled || issue) && <section className="rounded-2xl border bg-card p-4" aria-labelledby="delivery-form"><h2 id="delivery-form" className="text-sm font-semibold">{kind === 'TAG' && completed ? '修改 TAG' : '交付资料'}</h2><p className="mt-1 text-[10px] leading-4 text-muted-foreground">保存后，俱乐部将按此资料安排交付</p>{issue && <p role="alert" className="mt-3 rounded-xl bg-destructive/[0.07] p-3 text-[11px] leading-5 text-destructive">{issue}</p>}<div className="mt-4 space-y-4">{kind === 'PHYSICAL' && <><Field id="recipient" label="收件人" value={details.recipient} disabled={busy || !editable} onChange={update} /><Field id="phone" label="联系电话" value={details.phone} disabled={busy || !editable} onChange={update} /><AreaField id="address" label="完整收货地址" value={details.address} disabled={busy || !editable} onChange={update} /></>}{kind === 'VIRTUAL' && <Field id="game_uid" label="游戏 UID" value={details.game_uid} disabled={busy || !editable} onChange={update} />}{kind === 'TAG' && <Field id="tag" label="希望展示的 TAG" value={details.tag} disabled={busy || !editable} onChange={update} placeholder="最多 30 字，由俱乐部核对后发放" maxLength={30} />}{kind === 'NAMING' && <Field id="provider_name" label="希望冠名的陪玩" value={details.provider_name} disabled={busy || !editable} onChange={update} />}{kind !== 'TAG' && <AreaField id="note" label="补充说明" value={details.note} disabled={busy || !editable} onChange={update} placeholder="方便俱乐部为你安排交付" />}</div>{!issue ? <Button type="button" className="mt-4 h-11 w-full" disabled={busy || !editable || !dirty} onClick={save}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <PackageCheck className="size-4" />}{busy ? '正在提交' : dirty ? '保存资料' : '填写后保存资料'}</Button> : <div className="mt-4 space-y-2">{scenario === 'conflict' ? <Button type="button" className="h-11 w-full" onClick={useLatest}>使用最新资料</Button> : <Button type="button" className="h-11 w-full" onClick={() => { setIssue(''); setDetails(emptyDetails); setDirty(false); setFeedback('交付状态已刷新。') }}><RefreshCw className="size-4" />刷新交付状态</Button>}<Button type="button" variant="outline" className="h-11 w-full" onClick={() => setFeedback('未保存资料已模拟复制，可粘贴给客服核对。')}><Copy className="size-4" />复制未保存资料</Button></div>}<p className="mt-3 text-[10px] text-muted-foreground">资料仅用于本次奖品交付。</p></section>}

              {feedback && <div role="status" className="flex gap-2 rounded-xl border border-success/20 bg-success/[0.06] p-3 text-[11px] leading-5 text-success"><Check className="mt-0.5 size-4 shrink-0" />{feedback}</div>}
              {kind === 'TAG' && completed && <Button type="button" variant="outline" className="h-11 w-full" onClick={onMembership}><BadgeCheck className="size-4" />管理展示标签</Button>}
              <section className="flex items-center gap-3 rounded-2xl border bg-card p-4"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Headphones className="size-5" /></div><div className="min-w-0 flex-1"><h2 className="text-xs font-semibold">交付有疑问？</h2><p className="mt-1 text-[10px] text-muted-foreground">客服可协助核对进度与资料</p></div><Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs text-primary" onClick={onSupport}>联系客服</Button></section>
              <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>如资料在其他位置更新，请先复制草稿，再使用最新资料，避免覆盖新内容。</p></div>
            </div>
          </>
        )}
      </main>
    </PhoneFrame>
  )
}

function ClaimFact({ label, value }: { label: string; value: string }) {
  return <div className="flex min-h-12 items-center justify-between gap-3 py-2 text-xs"><span className="shrink-0 text-muted-foreground">{label}</span><span className="text-right font-medium leading-5">{value}</span></div>
}

function Field({ id, label, value, disabled, onChange, placeholder, maxLength }: { id: keyof ClaimDetails; label: string; value: string; disabled: boolean; onChange: (field: keyof ClaimDetails, value: string) => void; placeholder?: string; maxLength?: number }) {
  return <div><Label htmlFor={`claim-${id}`} className="mb-2 text-xs">{label}</Label><Input id={`claim-${id}`} value={value} disabled={disabled} maxLength={maxLength} placeholder={placeholder} className="h-11 text-xs" onChange={event => onChange(id, event.target.value)} /></div>
}

function AreaField({ id, label, value, disabled, onChange, placeholder }: { id: keyof ClaimDetails; label: string; value: string; disabled: boolean; onChange: (field: keyof ClaimDetails, value: string) => void; placeholder?: string }) {
  return <div><Label htmlFor={`claim-${id}`} className="mb-2 text-xs">{label}</Label><Textarea id={`claim-${id}`} value={value} disabled={disabled} placeholder={placeholder} className="min-h-20 text-xs" onChange={event => onChange(id, event.target.value)} /></div>
}
