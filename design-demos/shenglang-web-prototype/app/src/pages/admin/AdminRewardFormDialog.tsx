import { useMemo, useState } from 'react'
import { Award, Box, CircleAlert, Coins, Crown, Gamepad2, Gift, Info, PackageOpen, Save, Smartphone, Sparkles, Tag, TicketCheck } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { adminRewardAudienceLabels, adminRewardKindLabels, adminRewardServiceOptions, getAdminRewardShopBlockReason, getAdminRewardTerms, type AdminRewardItem, type AdminRewardKind } from '@/pages/admin/admin-reward-data'

interface AdminRewardFormDialogProps {
  open: boolean
  item: AdminRewardItem | null
  existingCodes: string[]
  initialError?: string
  saveFailed?: boolean
  saveUnknown?: boolean
  stockConflict?: boolean
  lotteryConflict?: boolean
  onClose: () => void
  onSave: (item: AdminRewardItem) => void
}

interface RewardDraft {
  code: string
  vipQuotaKind: AdminRewardItem['vipQuotaKind']
  audience: AdminRewardItem['audience']
  name: string
  kind: AdminRewardKind
  imageUrl: string
  description: string
  pointCost: string
  pointValue: string
  balanceCoins: string
  durationDays: string
  durationMonths: string
  highlighted: boolean
  serviceId: string
  unlimited: boolean
  stock: string
  isActive: boolean
  inShop: boolean
  sortOrder: string
}

const emptyReward: AdminRewardItem = {
  id: 0, code: '', vipQuotaKind: '', audience: 'BOSS', name: '', kind: 'PHYSICAL', imageUrl: '', description: '',
  pointCost: 0, pointValue: 0, balanceCoins: 0, durationDays: 0, durationMonths: 0, highlighted: false,
  serviceId: null, stock: null, redeemedCount: 0, isActive: false, inShop: true, sortOrder: 0,
}

function toDraft(item: AdminRewardItem): RewardDraft {
  return {
    code: item.code, vipQuotaKind: item.vipQuotaKind, audience: item.audience, name: item.name, kind: item.kind,
    imageUrl: item.imageUrl, description: item.description, pointCost: String(item.pointCost), pointValue: String(item.pointValue),
    balanceCoins: String(item.balanceCoins), durationDays: String(item.durationDays), durationMonths: String(item.durationMonths),
    highlighted: item.highlighted, serviceId: item.serviceId ? String(item.serviceId) : 'NONE', unlimited: item.stock === null,
    stock: item.stock === null ? '0' : String(item.stock), isActive: item.isActive, inShop: item.inShop, sortOrder: String(item.sortOrder),
  }
}

function RewardIcon({ kind, className = 'size-6' }: { kind: AdminRewardKind; className?: string }) {
  if (kind === 'PHYSICAL') return <PackageOpen className={className} />
  if (kind === 'VIRTUAL') return <Gamepad2 className={className} />
  if (kind === 'SERVICE') return <TicketCheck className={className} />
  if (kind === 'NAMING') return <Crown className={className} />
  if (kind === 'TAG') return <Tag className={className} />
  if (kind === 'BALANCE') return <Coins className={className} />
  return <Sparkles className={className} />
}

export function AdminRewardFormDialog({ open, item, existingCodes, initialError = '', saveFailed = false, saveUnknown = false, stockConflict = false, lotteryConflict = false, onClose, onSave }: AdminRewardFormDialogProps) {
  const source = item || emptyReward
  const [draft, setDraft] = useState<RewardDraft>(() => toDraft(source))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(source)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])
  const previewItem = useMemo<AdminRewardItem>(() => ({
    ...source,
    code: draft.code.trim(), vipQuotaKind: draft.vipQuotaKind, audience: draft.audience, name: draft.name.trim() || '未命名好礼', kind: draft.kind,
    imageUrl: draft.imageUrl.trim(), description: draft.description.trim(), pointCost: /^\d+$/.test(draft.pointCost) ? Number(draft.pointCost) : 0,
    pointValue: /^\d+$/.test(draft.pointValue) ? Number(draft.pointValue) : 0, balanceCoins: /^\d+$/.test(draft.balanceCoins) ? Number(draft.balanceCoins) : 0,
    durationDays: /^\d+$/.test(draft.durationDays) ? Number(draft.durationDays) : 0, durationMonths: /^\d+$/.test(draft.durationMonths) ? Number(draft.durationMonths) : 0,
    highlighted: draft.highlighted, serviceId: draft.serviceId === 'NONE' ? null : Number(draft.serviceId), stock: draft.unlimited ? null : /^\d+$/.test(draft.stock) ? Number(draft.stock) : 0,
    isActive: draft.isActive, inShop: draft.inShop, sortOrder: /^-?\d+$/.test(draft.sortOrder) ? Number(draft.sortOrder) : 0,
  }), [draft, source])
  const blockReason = getAdminRewardShopBlockReason(previewItem)
  const soldOut = previewItem.stock === 0

  function update(next: Partial<RewardDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validateInteger(value: string, label: string, minimum = 0) {
    if (!/^\d+$/.test(value) || Number(value) < minimum || !Number.isSafeInteger(Number(value))) return `${label}须为不小于 ${minimum} 的整数`
    return ''
  }

  function validate() {
    if (!draft.name.trim()) return '请填写好礼名称'
    if (draft.name.trim().length > 100) return '好礼名称最多 100 个字符'
    if (!source.id && !/^[a-z0-9_-]+$/i.test(draft.code.trim())) return '商品标识须包含字母、数字、连字符或下划线'
    if (draft.code.trim().length > 80) return '商品标识最多 80 个字符'
    if (!source.id && existingCodes.includes(draft.code.trim())) return '商品标识已存在，请更换后再保存'
    if (draft.imageUrl.trim() && !draft.imageUrl.trim().startsWith('https://') && !draft.imageUrl.trim().startsWith('/media/club-catalog/')) return '商品图片请使用已上传的俱乐部素材地址或 HTTPS 地址'
    for (const [value, label] of [[draft.pointCost, '兑换积分'], [draft.pointValue, '奖励积分'], [draft.durationDays, '有效天数'], [draft.durationMonths, '有效月数']] as const) {
      const message = validateInteger(value, label)
      if (message) return message
    }
    if (!/^-?\d+$/.test(draft.sortOrder) || !Number.isSafeInteger(Number(draft.sortOrder))) return '展示排序须为整数'
    if (!draft.unlimited) {
      const message = validateInteger(draft.stock, '库存')
      if (message) return message
    }
    if (!/^\d+$/.test(draft.balanceCoins) || !Number.isSafeInteger(Number(draft.balanceCoins))) return '到账浪花须为非负整数'
    if (draft.isActive && draft.inShop && Number(draft.pointCost) < 1) return '上架兑换商品前请设置积分价格'
    if (draft.isActive && draft.kind === 'BALANCE' && Number(draft.balanceCoins) < 1) return '请设置到账余额，至少 1 浪花'
    if (draft.isActive && draft.kind === 'POINTS' && Number(draft.pointValue) < 1) return '请设置奖励积分，至少 1 积分'
    return ''
  }

  function save() {
    if (saveUnknown || !dirty) return
    const message = validate()
    if (message) { setError(message); return }
    if (stockConflict) { setError('商品库存或兑换记录已变化，请刷新后再保存。'); return }
    if (lotteryConflict || (source.activeLottery && dirty)) { setError(`请先暂停使用此奖品的抽奖活动${source.activeLottery ? `「${source.activeLottery}」` : ''}，再修改奖品。`); return }
    if (saveFailed) { setError(initialError); return }
    onSave(previewItem)
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-6xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{source.id ? `配置积分好礼 #${source.id}` : '新增积分好礼'}</DialogTitle>
          <DialogDescription>维护兑换价格、交付权益与商城状态；已产生的兑换记录不会被改写。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{source.id ? `${source.code} · 已兑换 ${source.redeemedCount} 份` : '新好礼默认停用，可在核对后启用并上架'}</span>
          {dirty && !saveUnknown && <span role="status">有未保存的修改</span>}
        </div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_290px]">
            <div className="space-y-5">
              <section className="rounded-xl border p-4">
                <div className="mb-4 flex items-center gap-2"><Gift className="size-4 text-primary" /><h3 className="text-sm font-semibold">基础信息</h3></div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2"><Label htmlFor="reward-name">好礼名称</Label><Input id="reward-name" value={draft.name} maxLength={101} onChange={event => update({ name: event.target.value })} placeholder="例如 王者限定电竞耳机" /></div>
                  <div className="space-y-2"><Label htmlFor="reward-code">商品标识</Label><Input id="reward-code" value={draft.code} disabled={!!source.id} maxLength={81} onChange={event => update({ code: event.target.value.trim() })} placeholder="例如 gift-headset" className="font-mono text-xs" /><p className="text-[11px] text-muted-foreground">{source.id ? '创建后不可修改，用于稳定关联。' : '全局唯一，创建后不可修改。'}</p></div>
                  <div className="space-y-2"><Label>交付方式</Label><Select value={draft.kind} onValueChange={value => update({ kind: value as AdminRewardKind })}><SelectTrigger aria-label="好礼交付方式"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(adminRewardKindLabels).map(([kind, label]) => <SelectItem key={kind} value={kind}>{label}</SelectItem>)}</SelectContent></Select></div>
                  <div className="space-y-2"><Label>适用身份</Label><Select value={draft.audience} onValueChange={value => update({ audience: value as AdminRewardItem['audience'] })}><SelectTrigger aria-label="好礼适用身份"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(adminRewardAudienceLabels).map(([audience, label]) => <SelectItem key={audience} value={audience}>{label}</SelectItem>)}</SelectContent></Select></div>
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="reward-image">商品图片地址 <span className="font-normal text-muted-foreground">（选填）</span></Label><Input id="reward-image" value={draft.imageUrl} onChange={event => update({ imageUrl: event.target.value })} placeholder="/media/club-catalog/... 或 https://..." /><p className="text-[11px] text-muted-foreground">原型不发起图片请求，未配置图片时使用交付类型图标。</p></div>
                  <div className="space-y-2 sm:col-span-2"><Label htmlFor="reward-description">好礼说明</Label><Textarea id="reward-description" rows={3} value={draft.description} onChange={event => update({ description: event.target.value })} placeholder="说明交付内容、有效期或需要补充的资料" /></div>
                </div>
              </section>

              <section className="rounded-xl border p-4">
                <div className="mb-4 flex items-center gap-2"><Award className="size-4 text-primary" /><h3 className="text-sm font-semibold">兑换与权益</h3></div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2"><Label htmlFor="reward-point-cost">兑换积分</Label><div className="relative"><Input id="reward-point-cost" inputMode="numeric" value={draft.pointCost} onChange={event => update({ pointCost: event.target.value.trim() })} className="pr-12 text-right tabular-nums" /><span className="pointer-events-none absolute right-3 top-2.5 text-xs text-muted-foreground">积分</span></div><p className="text-[11px] text-muted-foreground">启用且上架时必须至少为 1。</p></div>
                  <div className="space-y-2"><Label>VIP 补差类别</Label><Select value={draft.vipQuotaKind || 'ITEM'} onValueChange={value => update({ vipQuotaKind: value === 'ITEM' ? '' : value as AdminRewardItem['vipQuotaKind'] })}><SelectTrigger aria-label="VIP 补差类别"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ITEM">按权益项目补差</SelectItem><SelectItem value="FRIEND">好友位：各档总额补差</SelectItem><SelectItem value="BIRTHDAY_GIFT">生日礼物：VIP4 起每年一次</SelectItem></SelectContent></Select><p className="text-[11px] text-muted-foreground">TAG 和冠名由系统按权益项目自动归类。</p></div>
                  {draft.kind === 'BALANCE' && <div className="space-y-2"><Label htmlFor="reward-balance">到账浪花</Label><Input id="reward-balance" inputMode="numeric" value={draft.balanceCoins} onChange={event => update({ balanceCoins: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">余额自动到账，不增加累计充值。</p></div>}
                  {draft.kind === 'POINTS' && <div className="space-y-2"><Label htmlFor="reward-point-value">奖励积分</Label><Input id="reward-point-value" inputMode="numeric" value={draft.pointValue} onChange={event => update({ pointValue: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">启用时必须至少为 1。</p></div>}
                  {['TAG', 'NAMING'].includes(draft.kind) && <><div className="space-y-2"><Label htmlFor="reward-days">有效天数</Label><Input id="reward-days" inputMode="numeric" value={draft.durationDays} onChange={event => update({ durationDays: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">0 表示未设到期日。</p></div><div className="space-y-2"><Label htmlFor="reward-months">有效月数</Label><Input id="reward-months" inputMode="numeric" value={draft.durationMonths} onChange={event => update({ durationMonths: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">自然月，设置后优先于天数。</p></div></>}
                  {draft.kind === 'TAG' && <div className="flex items-center justify-between gap-4 rounded-lg border p-3 sm:col-span-2"><div><Label htmlFor="reward-highlighted">高亮标签</Label><p className="mt-1 text-[11px] text-muted-foreground">发放后的自定义 TAG 使用高亮展示。</p></div><Switch id="reward-highlighted" checked={draft.highlighted} onCheckedChange={value => update({ highlighted: value })} /></div>}
                  {draft.kind === 'SERVICE' && <div className="space-y-2 sm:col-span-2"><Label>关联服务 <span className="font-normal text-muted-foreground">（选填）</span></Label><Select value={draft.serviceId} onValueChange={value => update({ serviceId: value })}><SelectTrigger aria-label="好礼关联服务"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">不关联具体服务</SelectItem>{adminRewardServiceOptions.map(service => <SelectItem key={service.id} value={String(service.id)}>{service.name} · #{service.id}</SelectItem>)}</SelectContent></Select><p className="text-[11px] text-muted-foreground">仅关联现有服务商品，本原型不新增服务或预约。</p></div>}
                </div>
              </section>

              <section className="rounded-xl border p-4">
                <div className="mb-4 flex items-center gap-2"><Box className="size-4 text-primary" /><h3 className="text-sm font-semibold">库存与展示</h3></div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-lg border p-3"><div className="flex items-center justify-between gap-3"><Label htmlFor="reward-unlimited">不限库存</Label><Switch id="reward-unlimited" checked={draft.unlimited} onCheckedChange={value => update({ unlimited: value })} /></div>{!draft.unlimited && <div className="mt-3 space-y-2"><Label htmlFor="reward-stock">可兑换库存</Label><Input id="reward-stock" inputMode="numeric" value={draft.stock} onChange={event => update({ stock: event.target.value.trim() })} className="text-right tabular-nums" /></div>}<p className="mt-2 text-[11px] text-muted-foreground">库存为 0 时老板端保留卡片并显示“已兑完”。</p></div>
                  <div className="rounded-lg border bg-muted/20 p-3"><Label>已兑换数量</Label><p className="mt-3 text-2xl font-semibold tabular-nums">{source.redeemedCount}</p><p className="mt-1 text-[11px] text-muted-foreground">只读；保存库存时用于核对记录是否变化。</p></div>
                  <div className="space-y-2"><Label htmlFor="reward-sort">展示排序</Label><Input id="reward-sort" inputMode="numeric" value={draft.sortOrder} onChange={event => update({ sortOrder: event.target.value.trim() })} className="text-right tabular-nums" /><p className="text-[11px] text-muted-foreground">按数值升序，同值再按商品编号排序。</p></div>
                  <div className="grid gap-3"><div className="flex items-center justify-between gap-3 rounded-lg border p-3"><div><Label htmlFor="reward-active">启用好礼</Label><p className="mt-1 text-[11px] text-muted-foreground">停用后不可兑换。</p></div><Switch id="reward-active" checked={draft.isActive} onCheckedChange={value => update({ isActive: value })} /></div><div className="flex items-center justify-between gap-3 rounded-lg border p-3"><div><Label htmlFor="reward-shop">在积分商城展示</Label><p className="mt-1 text-[11px] text-muted-foreground">与启用状态独立管理。</p></div><Switch id="reward-shop" checked={draft.inShop} onCheckedChange={value => update({ inShop: value })} /></div></div>
                </div>
              </section>
              {source.activeLottery && <Alert><Info className="size-4" /><AlertDescription>此好礼正被启用活动「{source.activeLottery}」使用，任何配置修改都须先暂停活动。</AlertDescription></Alert>}
            </div>

            <aside className="space-y-3 xl:sticky xl:top-0">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Smartphone className="size-4" />老板端积分商城预览</div>
              <div className="overflow-hidden rounded-2xl border bg-[#f7f8fc] text-[#172033] shadow-sm dark:bg-muted/30 dark:text-foreground">
                <div className="bg-[#10285b] p-4 text-white"><div className="flex items-center justify-between"><span className="text-[11px] text-blue-100/75">我的积分</span><Badge className="border-white/20 bg-white/10 text-[10px] text-white hover:bg-white/10">示例</Badge></div><p className="mt-3 text-2xl font-semibold tabular-nums">12,680 <span className="text-xs font-normal text-blue-100/70">积分</span></p></div>
                <div className="p-3">
                  {!blockReason ? <div className="overflow-hidden rounded-xl border bg-white shadow-sm dark:bg-card">
                    <div className="grid h-32 place-items-center bg-gradient-to-br from-blue-50 via-white to-sky-100 text-[#2563eb] dark:from-primary/15 dark:via-card dark:to-sky-500/10"><RewardIcon kind={draft.kind} className="size-12" /></div>
                    <div className="p-3"><Badge variant="secondary" className="text-[10px]">{adminRewardKindLabels[draft.kind]}</Badge><h3 className="mt-2 min-h-10 text-sm font-semibold leading-5">{previewItem.name}</h3><p className="mt-2 text-lg font-semibold text-[#2563eb] tabular-nums">{previewItem.pointCost.toLocaleString('zh-CN')} <span className="text-[11px] font-normal">积分</span></p><p className="mt-1 text-[11px] text-muted-foreground">{previewItem.stock === null ? '库存充足' : `剩余 ${previewItem.stock} 份`}</p><Button size="sm" disabled={soldOut} className="mt-3 w-full">{soldOut ? '已兑完' : '兑换'}</Button></div>
                  </div> : <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed bg-white/60 p-5 text-center dark:bg-card"><PackageOpen className="size-7 text-muted-foreground" /><p className="mt-3 text-sm font-medium">老板端暂不展示</p><p className="mt-1 text-[11px] leading-5 text-muted-foreground">{blockReason}</p></div>}
                  <div className="mt-3 rounded-lg border bg-white/70 p-3 dark:bg-card"><p className="text-[11px] font-medium">兑换确认摘要</p><p className="mt-2 text-[11px] leading-5 text-muted-foreground">{getAdminRewardTerms(previewItem) || '暂未填写权益说明。'}</p><p className="mt-2 border-t pt-2 text-[10px] leading-4 text-muted-foreground">{draft.kind === 'BALANCE' ? '兑换成功后余额自动到账。' : '兑换后可补充交付资料。'}</p></div>
                </div>
              </div>
              <p className="text-[11px] leading-5 text-muted-foreground">仅预览当前编辑内容，不产生真实兑换、扣积分或交付记录。</p>
            </aside>
          </div>
        </fieldset>
        {error && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button onClick={save} disabled={saveUnknown || !dirty}><Save className="size-4" />{source.id ? '保存好礼' : '创建好礼'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>当前好礼的字段修改尚未保存，放弃后将恢复最近读取的内容。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </>
}
