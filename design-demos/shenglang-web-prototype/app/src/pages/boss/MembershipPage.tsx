import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  BadgeCheck,
  Cake,
  Check,
  ChevronRight,
  Crown,
  LoaderCircle,
  LogIn,
  Medal,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Ticket,
  Waves,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

export type MembershipScenario = 'default' | 'guest' | 'loading' | 'error' | 'basic' | 'highest' | 'unconfigured' | 'empty-tags' | 'tag-saving' | 'tag-error' | 'tag-saved' | 'refresh-pending' | 'birthday-empty' | 'birthday-saving' | 'birthday-error' | 'save-refresh-error'

interface MembershipPageProps {
  scenario: MembershipScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onCoupons: () => void
  onRights: () => void
  onRetry: () => void
}

interface Tier {
  id: string
  name: string
  discount: string
  threshold: number
  benefits: [string, string][]
}

const tiers: Tier[] = [
  { id: 'vip1', name: '星海 VIP 1', discount: '9.5 折', threshold: 1000, benefits: [['下单折扣', '会员优惠 9.5 折'], ['专属券包', '每月会员专属优惠券'], ['生日心意', '生日当日消费积分翻倍']] },
  { id: 'vip2', name: '星海 VIP 2', discount: '9 折', threshold: 3000, benefits: [['下单折扣', '会员优惠 9 折'], ['专属券包', '每月会员进阶券包'], ['生日心意', '生日祝福与当日积分翻倍'], ['俱乐部身份', 'VIP 2 专属名片标识']] },
  { id: 'vip3', name: '星海 VIP 3', discount: '8.5 折', threshold: 8000, benefits: [['下单折扣', '会员优惠 8.5 折'], ['专属券包', '每月会员高阶券包'], ['生日心意', '生日祝福与当日积分翻倍'], ['俱乐部身份', 'VIP 3 专属名片标识']] },
]
const availableTags = ['荣耀王者', '声浪老友', '和平精英', '上分搭子', '礼物收藏家', '星海 VIP']
const initialTags = ['荣耀王者', '声浪老友']

export function MembershipPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onCoupons, onRights, onRetry }: MembershipPageProps) {
  const highest = scenario === 'highest'
  const basic = scenario === 'basic'
  const unconfigured = scenario === 'unconfigured'
  const currentTierIndex = highest ? 2 : basic || unconfigured ? -1 : 1
  const [selectedTier, setSelectedTier] = useState(Math.max(0, currentTierIndex))
  const [selected, setSelected] = useState<string[]>(scenario === 'refresh-pending' ? [...initialTags, '和平精英'] : initialTags)
  const [saved, setSaved] = useState<string[]>(initialTags)
  const [feedback, setFeedback] = useState(scenario === 'tag-error' ? '保存失败，请检查网络后重试。' : scenario === 'tag-saved' ? '展示标签已保存' : scenario === 'save-refresh-error' ? '设置已保存，会员信息刷新失败' : scenario === 'birthday-error' ? '生日设置保存失败，请重试。' : '')
  const [feedbackType, setFeedbackType] = useState<'success' | 'error'>(['tag-error', 'birthday-error', 'save-refresh-error'].includes(scenario) ? 'error' : 'success')
  const [feedbackTarget, setFeedbackTarget] = useState<'tags' | 'birthday'>(scenario === 'birthday-error' ? 'birthday' : 'tags')
  const [savingTarget, setSavingTarget] = useState<'tags' | 'birthday' | ''>(scenario === 'tag-saving' ? 'tags' : scenario === 'birthday-saving' ? 'birthday' : '')
  const [refreshPending, setRefreshPending] = useState(scenario === 'refresh-pending' || scenario === 'save-refresh-error')
  const [birthday, setBirthday] = useState(scenario === 'birthday-empty' ? '' : '1998-08-16')
  const [pendingBirthday, setPendingBirthday] = useState('')
  const [birthdayPublic, setBirthdayPublic] = useState(true)
  const configuredTiers = unconfigured ? [] : tiers
  const currentTier = currentTierIndex >= 0 ? tiers[currentTierIndex] : null
  const previewTier = configuredTiers[selectedTier] || null
  const nextTier = highest || unconfigured ? null : basic ? tiers[0] : tiers[2]
  const consumption = highest ? 9260 : basic || unconfigured ? 0 : 1680
  const pending = basic || unconfigured ? 0 : 268
  const progress = highest ? 100 : nextTier ? Math.min(100, consumption / nextTier.threshold * 100) : 0
  const gap = nextTier ? Math.max(0, nextTier.threshold - consumption) : 0
  const dirty = JSON.stringify(selected) !== JSON.stringify(saved)
  const busy = savingTarget !== ''
  const tags = scenario === 'empty-tags' || unconfigured ? [] : availableTags

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function toggleTag(tag: string) {
    if (busy) return
    if (!selected.includes(tag) && selected.length >= 5) {
      setFeedback('最多展示 5 个标签，请先取消一个已选标签。')
      setFeedbackType('error')
      setFeedbackTarget('tags')
      return
    }
    setSelected(current => current.includes(tag) ? current.filter(item => item !== tag) : [...current, tag])
    setFeedback('')
    setFeedbackTarget('tags')
  }

  function saveTags() {
    if (busy || !dirty) return
    setSavingTarget('tags')
    setFeedback('')
    setFeedbackTarget('tags')
    window.setTimeout(() => {
      setSaved(selected)
      setSavingTarget('')
      setFeedback('展示标签已保存')
      setFeedbackType('success')
      setRefreshPending(false)
    }, 500)
  }

  function discardAndRefresh() {
    if (busy) return
    setSelected(saved)
    setRefreshPending(false)
    setFeedback('已撤销修改并刷新会员信息')
    setFeedbackType('success')
    setFeedbackTarget('tags')
  }

  function confirmBirthday() {
    if (!pendingBirthday || busy) return
    const value = pendingBirthday
    setSavingTarget('birthday')
    setFeedback('')
    setFeedbackTarget('birthday')
    window.setTimeout(() => {
      setBirthday(value)
      setPendingBirthday('')
      setSavingTarget('')
      setFeedback('生日设置已保存')
      setFeedbackType('success')
    }, 500)
  }

  function changeBirthdayPublic(value: boolean) {
    if (busy) return
    setBirthdayPublic(value)
    setSavingTarget('birthday')
    setFeedback('')
    setFeedbackTarget('birthday')
    window.setTimeout(() => {
      setSavingTarget('')
      setFeedback('生日设置已保存')
      setFeedbackType('success')
    }, 400)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button>
          <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">会员中心 · 示例</Badge>
        </div>
        <header className="px-4 pb-4 pt-2">
          <p className="text-[11px] font-medium text-primary">声浪俱乐部</p>
          <h1 className="mt-1 text-[28px] font-semibold tracking-[-0.04em]">我的会员</h1>
          <p className="mt-1 text-xs text-muted-foreground">记录每一份热爱，解锁更多专属权益</p>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Crown className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录查看会员权益</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">查看成长进度、等级权益与你的俱乐部名片。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
        ) : scenario === 'loading' ? (
          <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载会员信息"><Skeleton className="h-[350px] w-full rounded-3xl" /><Skeleton className="h-64 w-full rounded-2xl" /><Skeleton className="h-52 w-full rounded-2xl" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-5"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">会员信息暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会改变等级、标签或生日设置。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
        ) : (
          <div className="space-y-4 px-4 pb-5">
            <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.42),rgba(2,6,23,0.96)_58%)] p-5 shadow-[0_20px_60px_-32px_rgba(56,189,248,0.5)]" aria-labelledby="member-level">
              <div className="pointer-events-none absolute -right-16 -top-20 size-56 rounded-full border border-sky-300/10" /><div className="pointer-events-none absolute -right-8 -top-12 size-40 rounded-full border border-sky-300/10" /><Waves className="pointer-events-none absolute -right-4 top-12 size-32 text-sky-300/[0.055]" strokeWidth={1} />
              <div className="relative flex items-start justify-between gap-3">
                <div><p className="flex items-center gap-1.5 text-[10px] font-medium text-sky-200/70"><Crown className="size-3.5" />当前身份</p><h2 id="member-level" className="mt-2 text-[28px] font-semibold tracking-[-0.04em] text-white">{currentTier?.name || (unconfigured ? '声浪会员' : '散客')}</h2></div>
                <Badge variant="outline" className="shrink-0 border-sky-200/15 bg-sky-100/[0.07] text-[10px] text-sky-100">{unconfigured ? '等级待配置' : nextTier ? '成长中' : currentTier ? '当前最高等级' : '等级待确认'}</Badge>
              </div>
              <div className="relative mt-6 flex items-end justify-between gap-3"><div><p className="text-[10px] text-sky-100/55">累计确认实销</p><p className="mt-1 text-2xl font-semibold tabular-nums text-white"><span className="mr-1 text-sm font-normal text-sky-100/60">¥</span>{consumption.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</p></div><div className="text-right"><p className="text-[10px] text-sky-100/55">待确认实销</p><p className="mt-1 text-sm font-medium tabular-nums text-sky-100/85">¥ {pending.toFixed(2)}</p></div></div>
              <p className="relative mt-2 text-[10px] leading-4 text-sky-100/50">有效报单满 72 小时后自动计入并升级</p>
              <div className="relative mt-5 border-y border-sky-100/10 py-4">
                <div className="flex items-end justify-between gap-3"><div><p className="text-[10px] text-sky-100/55">{nextTier ? '下一等级' : '等级进度'}</p><p className="mt-1 text-sm font-medium text-white">{unconfigured ? '会员等级暂未配置' : nextTier?.name || (currentTier ? '已解锁当前全部等级' : '等待等级确认')}</p></div>{nextTier && <div className="text-right"><p className="text-[10px] text-sky-100/55">升级还差</p><p className="mt-1 text-lg font-semibold tabular-nums text-sky-200">¥ {gap.toFixed(2)}</p></div>}</div>
                <Progress value={progress} className="mt-3 h-1.5 bg-white/10 [&>div]:bg-sky-300" aria-label={`会员成长进度 ${Math.round(progress)}%`} />
                <div className="mt-2 flex justify-between gap-2 text-[9px] tabular-nums text-sky-100/45"><span>当前 ¥ {consumption.toFixed(2)}</span><span>{nextTier ? `目标 ¥ ${nextTier.threshold.toFixed(2)}` : currentTier ? '继续保持，享受会员权益' : '暂无等级配置'}</span></div>
              </div>
              <div className="relative flex items-center justify-between gap-3 pt-4"><div><p className="text-[10px] text-sky-100/55">当前权益</p><p className="mt-1 text-sm font-medium text-white">下单 {currentTier?.discount || '暂无折扣'}</p></div><Button type="button" variant="outline" className="h-11 border-sky-100/15 bg-white/[0.06] px-3 text-xs text-sky-50 hover:bg-white/10 hover:text-white" onClick={onRights}>预存福利<ChevronRight className="size-3.5" /></Button></div>
            </section>

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="benefits-title">
              <div className="flex items-start justify-between gap-3"><div><h2 id="benefits-title" className="flex items-center gap-2 text-sm font-semibold"><Medal className="size-4 text-primary" />会员权益</h2><p className="mt-1 text-[10px] leading-4 text-muted-foreground">{!previewTier ? '会员等级暂未配置' : selectedTier === currentTierIndex ? '当前等级已生效' : '正在预览，不影响当前权益'}</p></div>{configuredTiers.length > 0 && <Select value={String(selectedTier)} onValueChange={value => setSelectedTier(Number(value))}><SelectTrigger size="sm" className="h-8 w-[112px] px-2.5 text-xs" aria-label="预览会员等级"><SelectValue /></SelectTrigger><SelectContent>{configuredTiers.map((tier, index) => <SelectItem key={tier.id} value={String(index)} className="min-h-9 text-xs">{tier.name}</SelectItem>)}</SelectContent></Select>}</div>
              {previewTier ? <><div className="mt-4 flex items-center justify-between rounded-xl bg-primary/[0.07] p-3"><span className="text-xs text-muted-foreground">下单折扣</span><strong className="text-lg text-primary">{previewTier.discount}</strong></div><div className="mt-2 divide-y">{previewTier.benefits.map(([label, value]) => <div key={label} className="flex min-h-12 items-center justify-between gap-3 py-2 text-xs"><span className="text-muted-foreground">{label}</span><span className="max-w-[62%] text-right font-medium leading-5">{value}</span></div>)}</div></> : <div className="mt-4 rounded-xl border border-dashed p-5 text-center text-xs leading-5 text-muted-foreground">等级与权益配置后会展示在这里。</div>}
              <Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" onClick={onCoupons}><Ticket className="size-4" />查看我的优惠券<ChevronRight className="ml-auto size-3.5" /></Button>
            </section>

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="tags-title">
              <div className="flex items-start justify-between gap-3"><div><h2 id="tags-title" className="flex items-center gap-2 text-sm font-semibold"><BadgeCheck className="size-4 text-primary" />名片展示标签</h2><p className="mt-1 max-w-[260px] text-[10px] leading-4 text-muted-foreground">不指定时，优先展示高等级已获得标签</p></div>{tags.length > 0 && <span className="shrink-0 text-xs font-medium tabular-nums text-primary">{selected.length} / 5</span>}</div>
              {refreshPending && <div role="status" className="mt-3 rounded-xl border border-primary/20 bg-primary/[0.06] p-3"><p className="text-[11px] leading-5 text-primary">会员信息已更新，保存或撤销标签修改后刷新</p><Button type="button" variant="ghost" className="mt-1 h-11 px-0 text-xs text-primary" disabled={busy} onClick={discardAndRefresh}>撤销修改并刷新</Button></div>}
              {tags.length ? <><div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="选择名片展示标签">{tags.map(tag => { const active = selected.includes(tag); return <button key={tag} type="button" aria-pressed={active} disabled={busy} className={cn("relative flex h-9 items-center gap-1 rounded-full! border px-2.5 text-xs transition-colors before:absolute before:-inset-y-1 before:content-[''] focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", active ? 'border-primary/35 bg-primary/10 text-primary' : 'bg-muted/25 text-muted-foreground hover:text-foreground')} onClick={() => toggleTag(tag)}>{active && <Check className="size-3.5" />}{tag}</button> })}</div><Button type="button" variant="secondary" className="mt-4 h-11 w-full text-xs" disabled={busy || !dirty} onClick={saveTags}>{savingTarget === 'tags' ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}{savingTarget === 'tags' ? '正在保存…' : dirty ? '保存展示标签' : '展示标签已保存'}</Button></> : <div className="mt-4 rounded-xl bg-muted/30 p-5 text-center"><Sparkles className="mx-auto size-5 text-muted-foreground" /><p className="mt-2 text-xs leading-5 text-muted-foreground">继续积累俱乐部成就，点亮你的专属标签。</p></div>}
              {feedback && feedbackTarget === 'tags' && <p role="status" className={cn('mt-3 rounded-xl p-3 text-[11px] leading-5', feedbackType === 'error' ? 'bg-destructive/[0.07] text-destructive' : 'bg-success/[0.07] text-success')}>{feedback}</p>}
            </section>

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="birthday-title">
              <div><h2 id="birthday-title" className="flex items-center gap-2 text-sm font-semibold"><Cake className="size-4 text-primary" />生日心意</h2><p className="mt-1 text-[10px] leading-4 text-muted-foreground">首页仅展示昵称和祝福，不展示出生日期</p></div>
              <div className="mt-3 divide-y"><div className="flex min-h-[56px] items-center justify-between gap-3 py-2"><label htmlFor="member-birthday" className="text-xs">我的生日</label><input id="member-birthday" type="date" value={pendingBirthday || birthday} disabled={busy} className="h-10 w-[150px] max-w-[62%] rounded-lg border bg-background px-2.5 text-xs text-foreground [color-scheme:light] dark:[color-scheme:dark]" onChange={event => setPendingBirthday(event.target.value)} aria-label={birthday ? '修改生日' : '登记生日'} /></div><div className="flex min-h-[56px] items-center justify-between gap-3 py-2"><div><p className="text-xs">接受首页生日祝福</p><p className="mt-1 text-[10px] text-muted-foreground">登记后修改生日需客服核对</p></div><Switch checked={birthdayPublic} disabled={busy} onCheckedChange={changeBirthdayPublic} aria-label="接受首页生日祝福" className="relative before:absolute before:-inset-x-3 before:-inset-y-4 before:content-['']" /></div></div>
              {birthdayReady(birthday, pendingBirthday) && <Button type="button" className="mt-2 h-11 w-full text-xs" disabled={busy} onClick={confirmBirthday}>{savingTarget === 'birthday' ? <LoaderCircle className="size-4 animate-spin" /> : <Cake className="size-4" />}{savingTarget === 'birthday' ? '正在保存…' : birthday ? '确认修改生日' : '确认登记生日'}</Button>}
              {feedback && feedbackTarget === 'birthday' && <p role="status" className={cn('mt-3 rounded-xl p-3 text-[11px] leading-5', feedbackType === 'error' ? 'bg-destructive/[0.07] text-destructive' : 'bg-success/[0.07] text-success')}>{feedback}</p>}
            </section>
            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>等级以有效报单的确认实销为准；待确认金额不计入当前等级。优惠券与 VIP 折扣按结算规则取优。</p></div>
          </div>
        )}
      </main>

    </PhoneFrame>
  )
}

function birthdayReady(birthday: string, pendingBirthday: string) {
  return !!pendingBirthday && pendingBirthday !== birthday
}
