import { useState } from 'react'
import {
  ArrowLeft,
  BadgeCheck,
  Banknote,
  CheckCircle2,
  CircleAlert,
  Edit3,
  Gamepad2,
  Info,
  LockKeyhole,
  Medal,
  RotateCcw,
  ShieldAlert,
  Star,
  UserRound,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminProviderDetailScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { adminPassTierLabel, adminProviderKindLabel, adminProviderStatusLabel, type AdminProvider, type AdminProviderStatus } from '@/pages/admin/admin-provider-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminProviderDetailPageProps {
  provider: AdminProvider
  scenario: AdminProviderDetailScenario
  onScenarioChange: (scenario: AdminProviderDetailScenario) => void
  onBack: () => void
}

interface OperationEvent { title: string; note: string; time: string }

function DetailRow({ label, children, note }: { label: string; children: React.ReactNode; note?: string }) {
  return <div className="grid gap-1 border-b py-3 last:border-0 sm:grid-cols-[132px_minmax(0,1fr)] sm:gap-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="min-w-0 text-sm leading-6">{children}{note && <span className="mt-0.5 block text-[11px] text-muted-foreground">{note}</span>}</dd></div>
}

export function AdminProviderDetailPage({ provider, scenario, onScenarioChange, onBack }: AdminProviderDetailPageProps) {
  const [verified, setVerified] = useState(scenario === 'unverified' ? false : provider.verified)
  const [status, setStatus] = useState<AdminProviderStatus>(scenario === 'busy' ? 'BUSY' : provider.status)
  const [displayName, setDisplayName] = useState(provider.displayName)
  const [city, setCity] = useState(provider.city)
  const [serviceArea, setServiceArea] = useState(provider.serviceArea)
  const [pricePerHour, setPricePerHour] = useState(provider.pricePerHour)
  const [recommendationSlot, setRecommendationSlot] = useState<number | null>(provider.recommendationSlot)
  const [rewardTotal, setRewardTotal] = useState(provider.rewardTotal)
  const [penaltyTotal, setPenaltyTotal] = useState(provider.penaltyTotal)
  const [walletBalance, setWalletBalance] = useState(provider.walletBalance)
  const [depositPaid, setDepositPaid] = useState(provider.depositPaid)
  const [editOpen, setEditOpen] = useState(false)
  const [editError, setEditError] = useState('')
  const [verifyOpen, setVerifyOpen] = useState(false)
  const [disposeOpen, setDisposeOpen] = useState(false)
  const [disposeType, setDisposeType] = useState<'REWARD' | 'PENALTY'>('REWARD')
  const [disposeAmount, setDisposeAmount] = useState('')
  const [disposeReason, setDisposeReason] = useState('')
  const [disposeError, setDisposeError] = useState('')
  const [disposeReview, setDisposeReview] = useState(false)
  const [depositOpen, setDepositOpen] = useState(false)
  const [depositAmount, setDepositAmount] = useState(String(provider.depositPaid))
  const [depositReason, setDepositReason] = useState('')
  const [depositError, setDepositError] = useState('')
  const [banOpen, setBanOpen] = useState(false)
  const [reviewResolved, setReviewResolved] = useState(false)
  const [reviewChecked, setReviewChecked] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [events, setEvents] = useState<OperationEvent[]>([
    { title: '档案资料更新', note: '基础资料与服务范围已核对 · 运营管理员', time: '2026-11-04 15:30' },
    { title: provider.verified ? '完成实名认证' : '保存入驻资料', note: provider.verified ? '实名资料复核通过 · 审核管理员' : '等待实名与资格复核 · 系统记录', time: '2026-11-03 18:20' },
  ])

  const reviewPending = scenario === 'review' && !reviewResolved
  const locked = scenario === 'refresh-error' || reviewPending
  const activeQualifications = provider.qualifications.filter(item => item.active)

  function addEvent(title: string, note: string) {
    setEvents(items => [{ title, note: `${note} · 运营管理员`, time: '2026-11-04 17:06' }, ...items])
  }

  function saveEdit() {
    if (!displayName.trim()) { setEditError('请输入陪玩昵称'); return }
    if (!Number.isFinite(pricePerHour) || !Number.isInteger(pricePerHour) || pricePerHour < 0) { setEditError('参考时价须为不小于 0 的整数'); return }
    setEditError(''); setEditOpen(false); addEvent('编辑陪玩资料', '更新基础资料、状态或推荐位')
    setFeedback('陪玩资料已在原型中保存，未修改真实档案。')
  }

  function confirmVerify() {
    const next = !verified
    setVerified(next); setVerifyOpen(false); addEvent(next ? '完成实名认证' : '取消实名认证', next ? '认证状态已更新，接单仍需核验其他条件' : '取消后不能接新单')
    setFeedback(next ? '认证已在原型中完成；接单仍需账号、押金、档期、服务资格和名额均满足要求。' : '已在原型中取消认证，该陪玩不能接新单。')
  }

  function previewDispose() {
    const amount = Number(disposeAmount)
    if (!Number.isInteger(amount) || amount <= 0) { setDisposeError('奖罚金额须为正整数'); setDisposeReview(false); return }
    if (disposeType === 'PENALTY' && amount > walletBalance) { setDisposeError(`罚款不可使余额为负，当前示例余额 ${formatWaveAmount(walletBalance)} 浪花`); setDisposeReview(false); return }
    setDisposeError(''); setDisposeReview(true)
  }

  function confirmDispose() {
    const amount = Number(disposeAmount)
    if (disposeType === 'REWARD') { setRewardTotal(value => value + amount); setWalletBalance(value => value + amount) }
    else { setPenaltyTotal(value => value + amount); setWalletBalance(value => value - amount) }
    addEvent(disposeType === 'REWARD' ? '奖励记账' : '罚款记账', `${formatWaveAmount(amount)} 浪花 · ${disposeReason.trim() || '未填写原因'}`)
    setDisposeOpen(false); setDisposeReview(false); setDisposeAmount(''); setDisposeReason('')
    setFeedback(`${disposeType === 'REWARD' ? '奖励' : '罚款'} ${formatWaveAmount(amount)} 浪花已完成原型记账，未操作真实钱包。`)
  }

  function confirmDepositRefund() {
    const amount = Number(depositAmount)
    if (!Number.isInteger(amount) || amount <= 0 || amount > depositPaid) { setDepositError(`退还金额须为正整数且不超过已缴押金 ${formatWaveAmount(depositPaid)} 浪花`); return }
    if (!depositReason.trim()) { setDepositError('请填写退还原因'); return }
    setDepositPaid(value => value - amount); setWalletBalance(value => value + amount)
    addEvent('退还押金至钱包', `${formatWaveAmount(amount)} 浪花 · ${depositReason.trim()}`)
    setDepositOpen(false); setDepositReason('')
    setFeedback(`押金 ${formatWaveAmount(amount)} 浪花已在原型中退回陪玩钱包。${provider.activeOrderCount ? `仍有 ${provider.activeOrderCount} 笔在途订单，请继续跟进。` : ''}`)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="陪玩档案详情加载中"><Skeleton className="h-52 rounded-xl" /><div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]"><Skeleton className="h-[760px] rounded-xl" /><Skeleton className="h-[540px] rounded-xl" /></div></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">陪玩档案加载失败</CardTitle><CardDescription className="mt-2">请重新加载，或返回列表核对档案状态。</CardDescription><div className="mt-6 flex gap-2"><Button variant="outline" onClick={onBack}><ArrowLeft className="size-4" />返回列表</Button><Button onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></div></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="flex flex-wrap items-center gap-3"><Button variant="ghost" onClick={onBack}><ArrowLeft className="size-4" />返回陪玩列表</Button><Button className="ml-auto" disabled={locked} onClick={() => { setEditError(''); setEditOpen(true) }}><Edit3 className="size-4" />编辑资料</Button></section>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>档案更新失败，当前显示上次结果</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请重新加载后再处理，编辑、认证与资金操作已锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {reviewPending && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>陪玩资料保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">原请求可能仍在处理，请读取最新档案后再继续操作。<Button variant="link" className="h-auto p-0" onClick={() => { setReviewChecked(true); setFeedback('已读取原型中的最新档案，请核对实际内容。') }}>读取最新档案</Button>{reviewChecked && <Button variant="link" className="h-auto p-0" onClick={() => setReviewResolved(true)}>已核对，解除限制</Button>}</AlertDescription></Alert>}

    <Card className="overflow-hidden border-[#10285b] bg-[#10285b] text-white"><CardContent className="grid gap-6 py-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="flex min-w-0 items-center gap-4"><span className="grid size-20 shrink-0 place-items-center rounded-2xl border border-white/15 bg-white/10 text-2xl font-semibold">{displayName.slice(0, 1)}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="text-2xl font-semibold tracking-tight">{displayName}</h1><Badge className="border-white/20 bg-white/10 text-white hover:bg-white/10">{adminProviderStatusLabel[status]}</Badge><Badge className={cn('border-white/20 hover:bg-white/10', verified ? 'bg-sky-300/15 text-sky-100' : 'bg-amber-300/15 text-amber-100')}>{verified ? '已认证' : '未认证'}</Badge></div><p className="mt-2 font-mono text-sm text-blue-100/75">{provider.escortNo} · {provider.username}</p><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">{provider.bio}</p></div></div>
      <dl className="grid grid-cols-3 gap-5 lg:min-w-[360px]">{[[provider.rating ? provider.rating.toFixed(2) : '—', '综合评分'], [String(provider.completedOrders), '完成订单'], [formatWaveAmount(pricePerHour), '参考时价 · 浪花']].map(([value, label]) => <div key={label} className="border-l border-white/15 pl-4"><dt className="text-[11px] text-blue-100/65">{label}</dt><dd className="mt-2 text-2xl font-semibold tabular-nums">{value}</dd></div>)}</dl>
    </CardContent></Card>

    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]">
      <div className="flex min-w-0 flex-col gap-4">
        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><UserRound className="size-4 text-primary" />基础资料</CardTitle><CardDescription>账号、展示资料与私密素材归档状态</CardDescription></CardHeader><CardContent><dl>
          <DetailRow label="陪玩编号">{provider.escortNo}<span className="ml-2 font-mono text-xs text-muted-foreground">档案 ID {provider.id}</span></DetailRow>
          <DetailRow label="登录账号">{provider.username}</DetailRow><DetailRow label="手机号">{provider.phone}</DetailRow>
          <DetailRow label="性别 / 城市">{provider.gender === 'FEMALE' ? '女陪' : provider.gender === 'MALE' ? '男陪' : '未知'} · {city}</DetailRow>
          <DetailRow label="服务区">{serviceArea}</DetailRow><DetailRow label="段位 / 胜率">{provider.rankTier} · {provider.winRate}%</DetailRow>
          <DetailRow label="推荐位">{recommendationSlot ? `下单页推荐位 ${recommendationSlot}` : '不推荐'}<span className="ml-2 text-xs text-muted-foreground">每个位置仅一位陪玩</span></DetailRow>
          <DetailRow label="形象素材"><div className="flex flex-wrap gap-2"><Badge variant="outline"><LockKeyhole className="size-3" />查外挂图已归档</Badge><Badge variant="outline">视频介绍 00:42</Badge></div></DetailRow>
        </dl></CardContent></Card>

        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Gamepad2 className="size-4 text-primary" />接单资格</CardTitle><CardDescription>{activeQualifications.length} 项启用 · 同范围可接低档，不同范围互不授权</CardDescription></CardHeader><CardContent className="space-y-3">
          {provider.qualifications.map(item => <section key={item.id} className="rounded-xl border bg-muted/20 p-4"><div className="flex flex-wrap items-center gap-2"><strong className="text-sm">{item.game}</strong><Badge variant={item.active ? 'secondary' : 'outline'} className={cn(item.active && 'bg-success/10 text-success')}>{item.active ? '已启用' : '已停用'}</Badge><span className="ml-auto text-xs text-muted-foreground">{item.pricePerHour == null ? '未设参考时价' : `${formatWaveAmount(item.pricePerHour)} 浪花 / 小时`}</span></div><div className="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-4"><div><span className="text-muted-foreground">服务方向</span><p className="mt-1 font-medium">{adminProviderKindLabel[item.kind]}</p></div><div><span className="text-muted-foreground">平台</span><p className="mt-1 font-medium">{item.platform === 'PC' ? '端游' : '手游'}</p></div><div><span className="text-muted-foreground">难度</span><p className="mt-1 font-medium">{item.securityLevel === 'ANY' ? '不限难度' : item.securityLevel === 'CONFIDENTIAL' ? '机密' : '绝密'}</p></div><div><span className="text-muted-foreground">最高档位</span><p className="mt-1 font-medium text-primary">{item.grade}</p></div></div></section>)}
          {provider.qualifications.length === 0 && <div className="rounded-xl border border-dashed p-8 text-center"><Gamepad2 className="mx-auto size-6 text-muted-foreground" /><p className="mt-3 text-sm font-medium">尚未配置接单资格</p><p className="mt-1 text-xs text-muted-foreground">请在编辑资料中选择游戏、服务范围和最高档位。</p></div>}
          <Alert><Info className="size-4" /><AlertDescription>参考时价仅用于展示和排序，订单价格以服务商品为准。派单提交时仍会重新核验资格。</AlertDescription></Alert>
        </CardContent></Card>

        <Card><CardHeader><CardTitle className="text-base">服务技能</CardTitle><CardDescription>{provider.games.length} 款可接游戏 · {provider.serviceItems.length} 项服务技能</CardDescription></CardHeader><CardContent><div className="flex flex-wrap gap-2">{provider.games.map(item => <Badge key={item} variant="secondary">{item}</Badge>)}{provider.games.length === 0 && <span className="text-sm text-muted-foreground">尚未配置可接游戏</span>}</div><div className="mt-4 grid gap-2 md:grid-cols-2">{provider.serviceItems.map(item => <div key={item} className="flex min-h-11 items-center gap-2 rounded-lg border px-3 text-sm"><CheckCircle2 className="size-4 text-success" />{item}</div>)}</div></CardContent></Card>

        <Card><CardHeader><CardTitle className="text-base">操作记录</CardTitle><CardDescription>资料、认证与资金操作分别留痕</CardDescription></CardHeader><CardContent><ol>{events.map((event, index) => <li key={`${event.title}-${index}`} className="relative border-l pb-5 pl-5 last:pb-0"><span className="absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-background bg-primary" /><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-sm font-medium">{event.title}</p><p className="mt-1 text-xs text-muted-foreground">{event.note}</p></div><time className="text-[11px] text-muted-foreground">{event.time}</time></div></li>)}</ol></CardContent></Card>
      </div>

      <aside className="flex min-w-0 flex-col gap-4 xl:sticky xl:top-[168px]">
        <Card><CardHeader><CardTitle className="text-base">档案操作</CardTitle><CardDescription>敏感操作均需复核，不连接真实账号或资金</CardDescription></CardHeader><CardContent className="space-y-2">
          <Button className="h-11 w-full" disabled={locked} onClick={() => setEditOpen(true)}><Edit3 className="size-4" />编辑陪玩资料</Button>
          <Button variant="outline" className="h-11 w-full" disabled={locked} onClick={() => setVerifyOpen(true)}><BadgeCheck className="size-4" />{verified ? '取消认证' : '认证陪玩'}</Button>
          <Button variant="outline" className="h-11 w-full" disabled={locked} onClick={() => { setDisposeReview(false); setDisposeError(''); setDisposeOpen(true) }}><Medal className="size-4" />奖励 / 罚款</Button>
          <Button variant="outline" className="h-11 w-full" disabled={locked || depositPaid <= 0} onClick={() => { setDepositAmount(String(depositPaid)); setDepositError(''); setDepositOpen(true) }}><WalletCards className="size-4" />{depositPaid > 0 ? '退还押金' : '暂无可退押金'}</Button>
          <Button variant="ghost" className="h-11 w-full" onClick={() => setBanOpen(true)}><ShieldAlert className="size-4" />封禁记录</Button>
        </CardContent></Card>

        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Banknote className="size-4 text-primary" />押金与钱包</CardTitle><CardDescription>钱包余额和押金分别展示</CardDescription></CardHeader><CardContent><dl><DetailRow label="缴纳模式">{provider.depositMode === 'CASH' ? '现金缴纳' : '打单抵扣'}</DetailRow><DetailRow label="当前应缴">{formatWaveAmount(provider.depositRequired)} 浪花</DetailRow><DetailRow label="已缴押金"><strong>{formatWaveAmount(depositPaid)} 浪花</strong></DetailRow><DetailRow label="示例钱包余额">{formatWaveAmount(walletBalance)} 浪花</DetailRow><DetailRow label="在途订单">{provider.activeOrderCount} 笔</DetailRow></dl><p className="mt-3 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">已有押金时须先退还才能切换缴纳模式；退押金不等于外部打款。</p></CardContent></Card>

        <Card><CardHeader><CardTitle className="text-base">业绩与奖惩</CardTitle></CardHeader><CardContent><div className="grid grid-cols-2 gap-3"><div className="rounded-xl border p-4"><Star className="size-4 fill-warning text-warning" /><p className="mt-3 text-xl font-semibold">{provider.rating ? provider.rating.toFixed(2) : '—'}</p><p className="mt-1 text-xs text-muted-foreground">综合评分</p></div><div className="rounded-xl border p-4"><CheckCircle2 className="size-4 text-success" /><p className="mt-3 text-xl font-semibold">{provider.completedOrders}</p><p className="mt-1 text-xs text-muted-foreground">完成订单</p></div></div><dl className="mt-3"><DetailRow label="累计奖励"><span className="font-medium text-success">+{formatWaveAmount(rewardTotal)} 浪花</span></DetailRow><DetailRow label="累计罚款"><span className="font-medium text-destructive">-{formatWaveAmount(penaltyTotal)} 浪花</span></DetailRow><DetailRow label="抢单通行证">{adminPassTierLabel[provider.passTier]}</DetailRow></dl></CardContent></Card>
        <p className="px-2 text-center text-[11px] leading-5 text-muted-foreground">陪玩档案与操作均为高保真示例，不代表真实人员、资质或资金结果。</p>
      </aside>
    </div>

    <Dialog open={editOpen} onOpenChange={setEditOpen}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>编辑陪玩资料</DialogTitle><DialogDescription>{provider.escortNo} · 保存后仍需重新核验接单条件。</DialogDescription></DialogHeader><div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2"><Label htmlFor="edit-provider-name">昵称</Label><Input id="edit-provider-name" value={displayName} onChange={event => setDisplayName(event.target.value)} className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="edit-provider-city">城市</Label><Input id="edit-provider-city" value={city} onChange={event => setCity(event.target.value)} className="h-11" /></div>
      <div className="space-y-2 md:col-span-2"><Label htmlFor="edit-provider-area">服务区</Label><Input id="edit-provider-area" value={serviceArea} onChange={event => setServiceArea(event.target.value)} className="h-11" /></div>
      <div className="space-y-2"><Label htmlFor="edit-provider-price">参考时价（浪花）</Label><Input id="edit-provider-price" type="number" min="0" step="1" value={pricePerHour} onChange={event => setPricePerHour(Number(event.target.value))} className="h-11" /></div>
      <div className="space-y-2"><Label>档案状态</Label><Select value={status} onValueChange={value => setStatus(value as AdminProviderStatus)}><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(adminProviderStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
      <div className="space-y-2"><Label>下单页推荐位</Label><Select value={recommendationSlot ? String(recommendationSlot) : 'NONE'} onValueChange={value => setRecommendationSlot(value === 'NONE' ? null : Number(value))}><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">不推荐</SelectItem>{[1, 2, 3, 4].map(value => <SelectItem key={value} value={String(value)}>推荐位 {value}</SelectItem>)}</SelectContent></Select></div>
      <div className="space-y-2"><Label>押金缴纳模式</Label><Input value={`${provider.depositMode === 'CASH' ? '现金缴纳' : '打单抵扣'} · ${depositPaid > 0 ? '退还后可切换' : '可编辑'}`} readOnly className="h-11" /></div>
    </div>{editError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{editError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => setEditOpen(false)}>取消</Button><Button onClick={saveEdit}>保存资料</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={verifyOpen} onOpenChange={setVerifyOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>{verified ? '确认取消认证？' : '确认认证陪玩？'}</DialogTitle><DialogDescription>{displayName} · {provider.escortNo}</DialogDescription></DialogHeader><Alert><BadgeCheck className="size-4" /><AlertDescription>{verified ? '取消后该陪玩不能接新单，历史订单与档案记录保留。' : '认证只更新实名状态，接单仍需账号、押金、档期、具体服务资格和名额均满足要求。'}</AlertDescription></Alert><DialogFooter><Button variant="outline" onClick={() => setVerifyOpen(false)}>返回</Button><Button variant={verified ? 'destructive' : 'default'} onClick={confirmVerify}>{verified ? '确认取消认证' : '确认认证'}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={disposeOpen} onOpenChange={setDisposeOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>奖励 / 罚款</DialogTitle><DialogDescription>{displayName} · 当前示例钱包余额 {formatWaveAmount(walletBalance)} 浪花</DialogDescription></DialogHeader><div className="grid grid-cols-2 gap-2">{([['REWARD', '奖励'], ['PENALTY', '罚款']] as const).map(([value, label]) => <Button key={value} type="button" variant={disposeType === value ? 'default' : 'outline'} disabled={disposeReview} onClick={() => { setDisposeType(value); setDisposeReview(false) }}>{label}</Button>)}</div><div className="space-y-2"><Label htmlFor="dispose-amount">金额（浪花）</Label><Input id="dispose-amount" type="number" min="1" step="1" value={disposeAmount} disabled={disposeReview} onChange={event => { setDisposeAmount(event.target.value); setDisposeReview(false) }} className="h-11" /><p className="text-xs text-muted-foreground">{disposeType === 'REWARD' ? '奖励将增加陪玩余额。' : '罚款将扣减陪玩余额，不可使余额为负。'}</p></div><div className="space-y-2"><Label htmlFor="dispose-reason">原因（选填）</Label><Textarea id="dispose-reason" value={disposeReason} disabled={disposeReview} onChange={event => setDisposeReason(event.target.value)} maxLength={255} /></div>{disposeError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{disposeError}</AlertDescription></Alert>}{disposeReview && <Alert><CheckCircle2 className="size-4" /><AlertTitle>请核对记账内容</AlertTitle><AlertDescription>{disposeType === 'REWARD' ? '奖励' : '罚款'} {formatWaveAmount(disposeAmount)} 浪花 · {disposeReason.trim() || '未填写原因'}。确认后生成原型记账记录。</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => disposeReview ? setDisposeReview(false) : setDisposeOpen(false)}>{disposeReview ? '返回修改' : '取消'}</Button>{disposeReview ? <Button onClick={confirmDispose}>确认记账</Button> : <Button onClick={previewDispose}>核对奖罚</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={depositOpen} onOpenChange={setDepositOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>退还押金</DialogTitle><DialogDescription>{displayName} · 当前已缴 {formatWaveAmount(depositPaid)} 浪花</DialogDescription></DialogHeader><div className="space-y-2"><Label htmlFor="deposit-amount">退还金额（浪花）</Label><Input id="deposit-amount" type="number" min="1" step="1" value={depositAmount} onChange={event => setDepositAmount(event.target.value)} className="h-11" /></div><div className="space-y-2"><Label htmlFor="deposit-reason">退还原因</Label><Textarea id="deposit-reason" value={depositReason} onChange={event => setDepositReason(event.target.value)} maxLength={200} /></div>{depositError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{depositError}</AlertDescription></Alert>}<Alert><Info className="size-4" /><AlertDescription>押金将退回陪玩钱包，外部打款需另走提现审核。{provider.activeOrderCount > 0 && `该陪玩还有 ${provider.activeOrderCount} 笔在途订单。`}</AlertDescription></Alert><DialogFooter><Button variant="outline" onClick={() => setDepositOpen(false)}>取消</Button><Button onClick={confirmDepositRefund}>确认退回钱包</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={banOpen} onOpenChange={setBanOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>封禁记录</DialogTitle><DialogDescription>{displayName} · 当前账号可正常登录</DialogDescription></DialogHeader><div className="rounded-xl border border-dashed p-8 text-center"><ShieldAlert className="mx-auto size-6 text-muted-foreground" /><p className="mt-3 text-sm font-medium">暂无封禁记录</p><p className="mt-1 text-xs leading-5 text-muted-foreground">本示例只展示历史记录，不执行真实封禁。账号状态与陪玩档案状态分别管理。</p></div><DialogFooter><Button onClick={() => setBanOpen(false)}>完成查看</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
