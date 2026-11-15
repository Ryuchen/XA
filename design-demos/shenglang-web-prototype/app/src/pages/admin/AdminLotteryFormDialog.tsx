import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, CircleAlert, Clock3, Coins, Gift, Info, Plus, Save, ShieldCheck, Smartphone, Sparkles, Trash2, Trophy } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { adminRewardKindLabels } from '@/pages/admin/admin-reward-data'
import { adminLotteryRewardOptions, getAdminLotteryPublicState, getAdminLotteryRarity, getAdminLotteryReward, getAdminLotteryTenUnavailableReason, getAdminLotteryValidation, LOTTERY_PITY_LIMIT, LOTTERY_PITY_REWARD_CODE, type AdminLotteryCampaign, type AdminLotteryPrize } from '@/pages/admin/admin-lottery-data'
import { cn } from '@/lib/utils'

interface AdminLotteryFormDialogProps {
  open: boolean
  item: AdminLotteryCampaign | null
  initialError?: string
  saveFailed?: boolean
  saveUnknown?: boolean
  versionConflict?: boolean
  onClose: () => void
  onSave: (item: AdminLotteryCampaign) => void
}

interface PrizeDraft extends Omit<AdminLotteryPrize, 'probability'> { probability: string }
interface LotteryDraft {
  name: string
  pointCost: string
  enabled: boolean
  startsAt: string
  endsAt: string
  dailyLimit: string
  rules: string
  prizes: PrizeDraft[]
}

const emptyCampaign: AdminLotteryCampaign = {
  id: 0, name: '', pointCost: 60, enabled: false, startsAt: '', endsAt: '', dailyLimit: 0, rules: '', version: 0,
  prizes: [
    { id: 0, rewardId: 110, probability: 9000, hasDraws: false },
    { id: 0, rewardId: 111, probability: 1000, hasDraws: false },
  ],
}

function toDraft(item: AdminLotteryCampaign): LotteryDraft {
  return {
    name: item.name, pointCost: String(item.pointCost), enabled: item.enabled, startsAt: item.startsAt.slice(0, 16), endsAt: item.endsAt.slice(0, 16),
    dailyLimit: String(item.dailyLimit), rules: item.rules,
    prizes: item.prizes.map(prize => ({ ...prize, probability: String(prize.probability) })),
  }
}

function toInteger(value: string) {
  return /^-?\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : -1
}

export function AdminLotteryFormDialog({ open, item, initialError = '', saveFailed = false, saveUnknown = false, versionConflict = false, onClose, onSave }: AdminLotteryFormDialogProps) {
  const source = item || emptyCampaign
  const [draft, setDraft] = useState<LotteryDraft>(() => toDraft(source))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(source)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const preview = useMemo<AdminLotteryCampaign>(() => ({
    ...source, name: draft.name.trim() || '未命名活动', pointCost: toInteger(draft.pointCost), enabled: source.id ? draft.enabled : false,
    startsAt: draft.startsAt, endsAt: draft.endsAt, dailyLimit: toInteger(draft.dailyLimit), rules: draft.rules.trim(),
    prizes: draft.prizes.map(prize => ({ ...prize, probability: toInteger(prize.probability) })),
  }), [draft, source])
  const total = draft.prizes.reduce((sum, prize) => sum + (/^\d+$/.test(prize.probability) ? Number(prize.probability) : 0), 0)
  const tenReason = getAdminLotteryTenUnavailableReason(preview)
  const pityPrize = preview.prizes.find(prize => getAdminLotteryReward(prize)?.code === LOTTERY_PITY_REWARD_CODE)
  const publicState = getAdminLotteryPublicState(preview)
  const enableBlocked = !source.id || total !== 10000

  function update(next: Partial<LotteryDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function updatePrize(index: number, next: Partial<PrizeDraft>) {
    update({ prizes: draft.prizes.map((prize, prizeIndex) => prizeIndex === index ? { ...prize, ...next } : prize) })
  }

  function addPrize() {
    if (draft.prizes.length >= 20) return
    update({ prizes: [...draft.prizes, { id: 0, rewardId: 0, probability: '0', hasDraws: false }] })
  }

  function removePrize(index: number) {
    const prize = draft.prizes[index]
    if (prize.hasDraws) {
      setError('该奖项已有中奖记录，不能删除。请保留奖项并将概率设为 0。')
      return
    }
    if (draft.prizes.length <= 2) {
      setError('奖项数量应为 2–20 个，无法继续删除。')
      return
    }
    update({ prizes: draft.prizes.filter((_, prizeIndex) => prizeIndex !== index) })
  }

  function movePrize(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= draft.prizes.length) return
    const prizes = [...draft.prizes]
    ;[prizes[index], prizes[target]] = [prizes[target], prizes[index]]
    update({ prizes })
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function save() {
    if (saveUnknown || !dirty) return
    const message = getAdminLotteryValidation(preview)
    if (message) { setError(message); return }
    if (versionConflict) { setError('配置已被其他人员修改，请刷新后重试。当前输入已保留。'); return }
    if (saveFailed) { setError(initialError); return }
    onSave(preview)
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-[1240px]" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{source.id ? `配置抽奖活动 #${source.id}` : '新建抽奖活动'}</DialogTitle>
          <DialogDescription>完整保存活动与全部奖项；修改概率不会改写历史中奖记录或保底进度。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{source.id ? `配置版本 v${source.version} · 保存时校验版本，防止覆盖他人修改` : '新活动默认停用，完整配置并保存后才能启用'}</span>
          {dirty && !saveUnknown && <span role="status">有未保存的修改</span>}
        </div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_318px]">
            <div className="min-w-0 space-y-5">
              <section className="rounded-xl border p-4">
                <div className="mb-4 flex items-center gap-2"><Sparkles className="size-4 text-primary" /><h3 className="text-sm font-semibold">活动规则</h3></div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="lottery-name">活动名称</Label><Input id="lottery-name" value={draft.name} maxLength={101} onChange={event => update({ name: event.target.value })} placeholder="例如 海浪幸运季" /></div>
                  <div className="space-y-2"><Label htmlFor="lottery-point-cost">每次消耗积分</Label><div className="relative"><Input id="lottery-point-cost" inputMode="numeric" value={draft.pointCost} onChange={event => update({ pointCost: event.target.value.trim() })} className="pr-12 text-right tabular-nums" /><span className="pointer-events-none absolute right-3 top-2.5 text-xs text-muted-foreground">积分</span></div><p className="text-[11px] text-muted-foreground">至少 1；十连固定消耗 10 倍积分。</p></div>
                  <div className="space-y-2"><Label htmlFor="lottery-daily-limit">每人每天次数上限</Label><Input id="lottery-daily-limit" inputMode="numeric" value={draft.dailyLimit} onChange={event => update({ dailyLimit: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">0 表示不限；十连消耗 10 次机会。</p></div>
                  <div className="space-y-2"><Label htmlFor="lottery-starts-at">开始时间 <span className="font-normal text-muted-foreground">（选填）</span></Label><Input id="lottery-starts-at" type="datetime-local" value={draft.startsAt} onChange={event => update({ startsAt: event.target.value })} /></div>
                  <div className="space-y-2"><Label htmlFor="lottery-ends-at">结束时间 <span className="font-normal text-muted-foreground">（选填）</span></Label><Input id="lottery-ends-at" type="datetime-local" value={draft.endsAt} onChange={event => update({ endsAt: event.target.value })} /></div>
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="lottery-rules">参与说明</Label><Textarea id="lottery-rules" rows={3} value={draft.rules} onChange={event => update({ rules: event.target.value })} placeholder="说明积分消耗、奖励到账与领取方式" /></div>
                </div>
              </section>

              <section className="min-w-0 rounded-xl border p-4" aria-labelledby="prize-pool-title">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><div className="flex items-center gap-2"><Gift className="size-4 text-primary" /><h3 id="prize-pool-title" className="text-sm font-semibold">完整奖池</h3><Badge variant="secondary">{draft.prizes.length} / 20 项</Badge></div><p className="mt-1 text-[11px] text-muted-foreground">概率单位为 0.01%；0 表示不从普通随机奖池抽中。</p></div>
                  <div className={cn('rounded-lg border px-3 py-2 text-right', total === 10000 ? 'border-success/25 bg-success/10' : 'border-warning/30 bg-warning/10')}><p className="text-[10px] text-muted-foreground">概率合计</p><p className={cn('text-sm font-semibold tabular-nums', total === 10000 ? 'text-success' : 'text-warning')}>{(total / 100).toFixed(2)}% <span className="text-[10px] font-normal">/ 100%</span></p></div>
                </div>
                <div className="mt-4 space-y-2">
                  <div className="hidden grid-cols-[34px_minmax(180px,1fr)_116px_94px_104px] gap-2 px-2 text-[11px] font-medium text-muted-foreground md:grid"><span>顺序</span><span>奖品</span><span className="text-right">概率（万分比）</span><span>记录 / 状态</span><span className="text-right">调整</span></div>
                  {draft.prizes.map((prize, index) => {
                    const reward = getAdminLotteryReward(prize)
                    const probability = toInteger(prize.probability)
                    return <div key={`${prize.id}-${index}`} className="grid gap-2 rounded-lg border bg-card p-2 md:grid-cols-[34px_minmax(180px,1fr)_116px_94px_104px] md:items-center">
                      <span className="grid size-8 place-items-center rounded-md bg-muted text-xs font-medium tabular-nums">{index + 1}</span>
                      <div className="min-w-0"><Select value={prize.rewardId ? String(prize.rewardId) : ''} onValueChange={value => updatePrize(index, { rewardId: Number(value) })}><SelectTrigger aria-label={`奖项 ${index + 1} 奖品`} className="w-full"><SelectValue placeholder="选择积分好礼" /></SelectTrigger><SelectContent>{adminLotteryRewardOptions.map(option => <SelectItem key={option.id} value={String(option.id)}>{option.name} · {adminRewardKindLabels[option.kind]}</SelectItem>)}</SelectContent></Select>{reward && <p className="mt-1 truncate px-1 text-[10px] text-muted-foreground">{reward.code} · {reward.stock === null ? '不限库存' : `库存 ${reward.stock}`}{reward.inShop ? '' : ' · 商城隐藏'}</p>}</div>
                      <div className="relative"><Input aria-label={`奖项 ${index + 1} 中奖概率`} inputMode="numeric" value={prize.probability} onChange={event => updatePrize(index, { probability: event.target.value.trim() })} className={cn('pr-12 text-right tabular-nums', probability >= 0 && probability <= 10000 ? '' : 'border-destructive')} /><span className="pointer-events-none absolute right-2 top-2.5 text-[10px] text-muted-foreground">{probability >= 0 ? `${(probability / 100).toFixed(2)}%` : '无效'}</span></div>
                      <div className="flex flex-wrap gap-1">{prize.hasDraws && <Badge variant="outline" className="px-1.5 text-[9px]">已有中奖</Badge>}{reward?.code === LOTTERY_PITY_REWARD_CODE && <Badge variant="outline" className="border-warning/30 px-1.5 text-[9px] text-warning">保底</Badge>}{reward && !reward.isActive && <Badge variant="outline" className="px-1.5 text-[9px] text-destructive">已停用</Badge>}</div>
                      <div className="flex justify-end gap-0.5"><Button type="button" variant="ghost" size="icon" className="size-8" disabled={index === 0} onClick={() => movePrize(index, -1)} aria-label={`上移奖项 ${index + 1}`}><ArrowUp className="size-3.5" /></Button><Button type="button" variant="ghost" size="icon" className="size-8" disabled={index === draft.prizes.length - 1} onClick={() => movePrize(index, 1)} aria-label={`下移奖项 ${index + 1}`}><ArrowDown className="size-3.5" /></Button><Button type="button" variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive" onClick={() => removePrize(index)} aria-label={`移除奖项 ${index + 1}`}><Trash2 className="size-3.5" /></Button></div>
                    </div>
                  })}
                </div>
                <Button type="button" variant="outline" className="mt-3 w-full border-dashed" disabled={draft.prizes.length >= 20} onClick={addPrize}><Plus className="size-4" />添加奖项</Button>
                <Alert className="mt-4 border-primary/20 bg-primary/5"><Info className="size-4 text-primary" /><AlertDescription>同一奖品不能重复。已有中奖记录的奖项只能保留并将概率设为 0；启用时，正概率奖项与保底目标都必须启用、允许老板领取且有库存。</AlertDescription></Alert>
              </section>

              <section className="grid gap-3 md:grid-cols-2">
                <div className="rounded-xl border p-4"><div className="flex items-center gap-2"><Trophy className="size-4 text-warning" /><h3 className="text-sm font-semibold">240 抽保底</h3></div>{pityPrize ? <><p className="mt-3 text-xs font-medium">目标：68 体验单</p><p className="mt-2 text-[11px] leading-5 text-muted-foreground">提前获得立即重置，单抽与十连共享进度；触发保底时优先于普通随机结果。</p><Badge variant="outline" className={cn('mt-3', getAdminLotteryReward(pityPrize)?.kind === 'SERVICE' && !getAdminLotteryReward(pityPrize)?.inShop ? 'border-success/25 text-success' : 'border-destructive/30 text-destructive')}>{getAdminLotteryReward(pityPrize)?.kind === 'SERVICE' && !getAdminLotteryReward(pityPrize)?.inShop ? '专属服务权益 · 不上架商城' : '保底权益配置需修正'}</Badge></> : <p className="mt-3 text-[11px] leading-5 text-warning">当前奖池未配置「68 体验单」保底目标，请核对活动规则。</p>}</div>
                <div className="rounded-xl border p-4"><div className="flex items-center gap-2"><Coins className="size-4 text-primary" /><h3 className="text-sm font-semibold">十连保障</h3></div><p className="mt-3 text-xs font-medium">前 9 抽全为积分时，第 10 抽必得非积分奖</p><p className="mt-2 text-[11px] leading-5 text-muted-foreground">从正基础概率的可领取非积分奖中按原权重抽取；240 抽保底优先。</p><Badge variant="outline" className={cn('mt-3', tenReason ? 'border-warning/30 text-warning' : 'border-success/25 text-success')}>{tenReason ? '当前不可十连' : `${preview.prizes.filter(prize => prize.probability > 0 && getAdminLotteryReward(prize)?.kind !== 'POINTS').length} 个非积分候选`}</Badge></div>
              </section>

              <div className="flex items-center justify-between gap-4 rounded-xl border bg-muted/20 p-4"><div><Label htmlFor="lottery-enabled">启用抽奖活动</Label><p className="mt-1 text-[11px] leading-5 text-muted-foreground">{!source.id ? '新活动须先以停用状态创建。' : enableBlocked ? `当前合计 ${(total / 100).toFixed(2)}%，调整为 100% 后才能启用。` : '启用后仍受活动时间和奖品库存约束。'}</p></div><Switch id="lottery-enabled" checked={draft.enabled} disabled={!draft.enabled && enableBlocked} onCheckedChange={value => update({ enabled: value })} /></div>
            </div>

            <aside className="min-w-0 space-y-3 xl:sticky xl:top-0">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端幸运转盘预览</div>
              <div className="overflow-hidden rounded-2xl border bg-[#f7f8fc] text-[#172033] shadow-sm dark:bg-muted/30 dark:text-foreground">
                <div className="bg-[#10285b] p-4 text-white"><div className="flex items-center justify-between gap-2"><span className="text-[11px] text-blue-100/75">声浪俱乐部 · 积分好礼</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">示例</Badge></div><h3 className="mt-3 truncate text-xl font-semibold tracking-tight">{preview.name}</h3><div className="mt-2 flex items-center justify-between text-[11px] text-blue-100/75"><span>我的积分 <strong className="ml-1 text-base text-white">12,680</strong></span><span>{publicState === 'OPEN' ? '活动开放中' : publicState === 'SCHEDULED' ? '尚未开始' : publicState === 'ENDED' ? '活动已结束' : '奖池准备中'}</span></div></div>
                <div className="p-3">
                  <div className="rounded-xl border bg-white p-3 text-center dark:bg-card"><p className="text-left text-xs font-semibold">每一抽，都有好礼</p><div className="relative mx-auto my-4 size-[184px]"><div className="absolute left-1/2 top-[-3px] z-20 size-0 -translate-x-1/2 border-x-[8px] border-t-[18px] border-x-transparent border-t-sky-300" /><div className="absolute inset-0 rounded-full border-4 border-[#2563eb]/50 shadow-[0_0_36px_-12px_rgba(56,189,248,0.4)]" style={{ background: 'conic-gradient(from -30deg, #172554 0deg 60deg, #1e3a8a 60deg 120deg, #0f172a 120deg 180deg, #1e40af 180deg 240deg, #172554 240deg 300deg, #334155 300deg 360deg)' }}>{preview.prizes.slice(0, 6).map((prize, index) => <span key={`${prize.id}-${index}`} className="absolute left-1/2 top-1/2 w-[70px] origin-center text-[9px] font-medium leading-3 text-sky-50/85" style={{ transform: `translate(-50%, -50%) rotate(${index * 60}deg) translateY(-60px) rotate(${-index * 60}deg)` }}>{getAdminLotteryReward(prize)?.name || '待选奖品'}</span>)}</div><div className="absolute left-1/2 top-1/2 z-10 grid size-[68px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-4 border-white bg-[linear-gradient(145deg,#38bdf8,#2563eb)] text-white shadow-lg dark:border-card"><div><Trophy className="mx-auto size-4" /><p className="mt-1 text-[9px] font-semibold">声浪好礼</p></div></div></div><div className="grid grid-cols-2 gap-2"><Button type="button" variant="outline" size="sm" disabled className="h-12 flex-col text-[#2563eb]"><span>抽 1 次</span><span className="text-[9px] font-normal">{Math.max(0, preview.pointCost)} 积分</span></Button><Button type="button" size="sm" disabled={!!tenReason || publicState !== 'OPEN'} className="h-12 flex-col bg-[linear-gradient(135deg,#2563eb,#38bdf8)]"><span>抽 10 次</span><span className="text-[9px] font-normal text-white/75">{Math.max(0, preview.pointCost) * 10} 积分</span></Button></div><p className="mt-2 text-[9px] leading-4 text-muted-foreground">仅作配置预览，不执行抽奖</p></div>
                  <div className="mt-3 rounded-xl border bg-white p-3 dark:bg-card"><div className="flex items-center justify-between text-xs"><span className="font-semibold">68 体验单保底</span><span className="font-semibold text-warning tabular-nums">168 / {LOTTERY_PITY_LIMIT}</span></div><Progress value={70} className="mt-2 h-1.5" aria-label="保底进度预览" /><p className="mt-2 text-[9px] text-muted-foreground">单抽、十连共享进度 · 提前获得后重置</p></div>
                  <div className="mt-3 rounded-xl border bg-white p-3 dark:bg-card"><p className="text-xs font-semibold">本期奖池 · 基础概率</p><div className="mt-1 max-h-44 divide-y overflow-y-auto phone-scrollbar">{preview.prizes.map((prize, index) => { const reward = getAdminLotteryReward(prize); return <div key={`${prize.id}-preview-${index}`} className="flex min-h-9 items-center justify-between gap-2 py-1.5 text-[10px]"><span className="flex min-w-0 items-center gap-1.5"><Badge variant="secondary" className={cn('w-8 shrink-0 justify-center px-1 text-[8px]', reward && getAdminLotteryRarity(reward) === 'SSR' && 'text-warning')}>{reward ? getAdminLotteryRarity(reward) : '—'}</Badge><span className="truncate">{reward?.name || '待选奖品'}</span></span><span className="shrink-0 text-muted-foreground tabular-nums">{prize.probability >= 0 ? `${(prize.probability / 100).toFixed(2)}%` : '—'}</span></div>})}</div></div>
                  <div className="mt-3 rounded-lg bg-[#10285b]/[0.05] p-3 dark:bg-primary/10"><div className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-[#2563eb]" /><p className="text-[9px] leading-4 text-muted-foreground">{preview.rules || '尚未填写参与说明。'}</p></div></div>
                </div>
              </div>
              <p className="text-[11px] leading-5 text-muted-foreground">转盘分区仅用于视觉展示，中奖概率以公示为准。预览不扣积分、不生成抽奖或交付记录。</p>
            </aside>
          </div>
        </fieldset>
        {tenReason && preview.enabled && <Alert><Clock3 className="size-4" /><AlertDescription>十连当前不可用：{tenReason}。单抽仍按完整奖池与启用规则校验。</AlertDescription></Alert>}
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown || !dirty}><Save className="size-4" />{source.id ? '保存完整配置' : '创建停用活动'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的活动配置？</AlertDialogTitle><AlertDialogDescription>活动字段、奖项与概率修改尚未保存，放弃后将恢复版本 v{source.version} 的内容。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </>
}
