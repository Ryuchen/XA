import { useMemo, useState } from 'react'
import { CircleAlert, Coins, Info, Percent, Save } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { formatServiceRaw, parseServiceCoins, type AdminGameCategory, type AdminServiceCategory, type AdminServiceItem } from '@/pages/admin/admin-service-data'

interface AdminServiceFormDialogProps {
  open: boolean
  item: AdminServiceItem | null
  gameCategories: AdminGameCategory[]
  serviceCategories: AdminServiceCategory[]
  initialError?: string
  saveUnknown?: boolean
  onClose: () => void
  onSave: (item: AdminServiceItem) => void
}

interface ServiceDraft {
  id: number
  name: string
  description: string
  gameCategoryId: string
  serviceCategoryId: string
  gameplay: string
  platform: '' | 'MOBILE' | 'PC'
  securityLevel: '' | 'CONFIDENTIAL' | 'TOP_SECRET' | 'ANY'
  providerKind: '' | 'COMPANION' | 'GUARD' | 'GRIND' | 'RANK'
  providerGrade: string
  requiredLevel: string
  price: string
  priceUnit: string
  billingProviderCount: '1' | '2'
  purchaseMode: 'DIRECT' | 'CONSULT'
  purchaseNote: string
  isAddon: boolean
  active: boolean
  sortOrder: string
  commissionOverride: boolean
  commissionType: 'PERCENT' | 'FIXED'
  commissionRate: string
  commissionFixed: string
  commissionPolicy: AdminServiceItem['commissionPolicy']
  experienceKind: AdminServiceItem['experienceKind']
  serviceType: AdminServiceItem['serviceType']
  allowsDiscountCoupons: boolean
  benefitKind: AdminServiceItem['benefitKind']
  giftNamingDays: string
  giftFestival: string
  giftStartsAt: string
  giftEndsAt: string
  catalogFamily: string
  catalogVariant: string
  rules: string
  hasOrders: boolean
}

const blankDraft: ServiceDraft = {
  id: 0, name: '', description: '', gameCategoryId: '', serviceCategoryId: '11', gameplay: '', platform: '', securityLevel: '',
  providerKind: '', providerGrade: '', requiredLevel: '', price: '', priceUnit: '份', billingProviderCount: '1', purchaseMode: 'DIRECT', purchaseNote: '',
  isAddon: false, active: true, sortOrder: '0', commissionOverride: false, commissionType: 'PERCENT', commissionRate: '20', commissionFixed: '0',
  commissionPolicy: 'LEGACY', experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: '0',
  giftFestival: '', giftStartsAt: '', giftEndsAt: '', catalogFamily: '', catalogVariant: '', rules: '', hasOrders: false,
}

function toDraft(item: AdminServiceItem | null): ServiceDraft {
  if (!item) return { ...blankDraft }
  return {
    id: item.id, name: item.name, description: item.description, gameCategoryId: item.gameCategoryId ? String(item.gameCategoryId) : '',
    serviceCategoryId: item.serviceCategoryId ? String(item.serviceCategoryId) : '', gameplay: item.gameplay, platform: item.platform,
    securityLevel: item.securityLevel, providerKind: item.providerKind, providerGrade: item.providerGrade, requiredLevel: item.requiredLevel,
    price: formatServiceRaw(item.priceRaw), priceUnit: item.priceUnit, billingProviderCount: String(item.billingProviderCount) as '1' | '2',
    purchaseMode: item.purchaseMode, purchaseNote: item.purchaseNote, isAddon: item.isAddon, active: item.active, sortOrder: String(item.sortOrder),
    commissionOverride: item.commissionOverride, commissionType: item.commissionType, commissionRate: String(item.commissionRate ?? 20),
    commissionFixed: formatServiceRaw(item.commissionFixedRaw), commissionPolicy: item.commissionPolicy, experienceKind: item.experienceKind,
    serviceType: item.serviceType, allowsDiscountCoupons: item.allowsDiscountCoupons, benefitKind: item.benefitKind,
    giftNamingDays: String(item.giftNamingDays), giftFestival: item.giftFestival, giftStartsAt: item.giftStartsAt, giftEndsAt: item.giftEndsAt,
    catalogFamily: item.catalogFamily, catalogVariant: item.catalogVariant, rules: item.rules, hasOrders: item.hasOrders,
  }
}

export function AdminServiceFormDialog({ open, item, gameCategories, serviceCategories, initialError = '', saveUnknown = false, onClose, onSave }: AdminServiceFormDialogProps) {
  const [draft, setDraft] = useState<ServiceDraft>(() => toDraft(item))
  const [initialSignature] = useState(() => JSON.stringify(toDraft(item)))
  const [error, setError] = useState(initialError)
  const [discardOpen, setDiscardOpen] = useState(false)
  const isGift = serviceCategories.find(category => category.id === Number(draft.serviceCategoryId))?.isGift ?? false
  const wasGift = item ? serviceCategories.find(category => category.id === item.serviceCategoryId)?.isGift ?? false : false
  const dirty = useMemo(() => JSON.stringify(draft) !== initialSignature, [draft, initialSignature])

  function update(next: Partial<ServiceDraft>) {
    setDraft(current => ({ ...current, ...next }))
    setError('')
  }

  function changeCategory(value: string) {
    const gift = serviceCategories.find(category => category.id === Number(value))?.isGift ?? false
    update(gift ? {
      serviceCategoryId: value, commissionOverride: false, providerKind: '', providerGrade: '', requiredLevel: '', gameplay: '', platform: '', securityLevel: '',
      isAddon: false, purchaseMode: 'DIRECT', experienceKind: '', benefitKind: '', serviceType: 'REGULAR',
    } : { serviceCategoryId: value, giftNamingDays: '0', giftFestival: '', giftStartsAt: '', giftEndsAt: '' })
  }

  function requestClose() {
    if (dirty && !saveUnknown) setDiscardOpen(true)
    else onClose()
  }

  function validate() {
    const priceRaw = parseServiceCoins(draft.price)
    if (!draft.name.trim() || draft.name.trim().length > 100) return '请输入商品名称，最多 100 字'
    if (priceRaw === null) return '价格须为大于 0 的整数'
    if (!draft.serviceCategoryId) return '请选择服务分类'
    if (!/^\d+$/.test(draft.sortOrder)) return '排序须为不小于 0 的整数'
    if (draft.providerKind && (!draft.gameCategoryId || !draft.platform || !draft.securityLevel || !draft.providerGrade)) return '请完整配置游戏、接单方向、端手游、难度及订单档位'
    if (!draft.providerKind && draft.providerGrade) return '请先选择接单方向'
    if (draft.gameplay && draft.purchaseMode === 'DIRECT' && (!draft.platform || !draft.securityLevel)) return '玩法商品直接下单时须选择游戏平台与适用难度'
    if (!draft.gameplay && !draft.providerKind && (draft.platform || draft.securityLevel)) return '请先选择所属玩法或接单方向'
    if (isGift) {
      if (draft.purchaseMode !== 'DIRECT' || draft.isAddon || draft.providerKind || draft.providerGrade || draft.requiredLevel || draft.gameplay) return '礼物须直接赠送，不能配置接单档位、附加费用、玩法或客服确认下单'
      if (!/^\d+$/.test(draft.giftNamingDays) || Number(draft.giftNamingDays) > 3650) return '每件冠名天数须为 0–3650 的整数'
      if (draft.giftFestival && priceRaw < 2100) return '节日限定礼物最低 210 浪花'
      if (draft.giftStartsAt && draft.giftEndsAt && draft.giftStartsAt >= draft.giftEndsAt) return '礼物结束时间必须晚于开始时间'
    } else if (draft.giftFestival || Number(draft.giftNamingDays) > 0 || draft.giftStartsAt || draft.giftEndsAt) return '冠名天数、节日限定与开售时间仅适用于礼物'
    if (draft.experienceKind && (!draft.platform || draft.isAddon || draft.billingProviderCount !== '1' || draft.purchaseMode !== 'DIRECT')) return '体验套餐须为端游／手游的一份单陪主套餐，直接下单'
    if (!draft.experienceKind && draft.serviceType === 'EXPERIENCE') return '体验单须选择体验资格类型'
    if (draft.benefitKind && (draft.experienceKind || draft.isAddon || draft.billingProviderCount !== '1')) return 'VIP 服务券仅可绑定普通单陪主套餐'
    if (draft.benefitKind === 'COMPANION_HOUR' && !['小时', '时', 'hour'].includes(draft.priceUnit)) return '小时陪玩券绑定套餐须按小时计价'
    if (draft.commissionOverride && !isGift) {
      if (draft.commissionType === 'PERCENT' && (!/^(?:0|[1-9]\d{0,2})$/.test(draft.commissionRate) || Number(draft.commissionRate) > 100)) return '抽成比例须为 0–100 的整数'
      if (draft.commissionType === 'FIXED' && parseServiceCoins(draft.commissionFixed, true) === null) return '固定抽成须为不小于 0 的整数'
    }
    if (item?.hasOrders && wasGift !== isGift) return '已有订单的商品不能在服务和礼物之间转换，请新建商品'
    if (item?.hasOrders && (draft.experienceKind !== item.experienceKind || draft.benefitKind !== item.benefitKind)) return '已有订单的套餐不能变更体验资格或 VIP 权益类型，请新建套餐'
    return ''
  }

  function save() {
    if (saveUnknown) return
    const message = validate()
    if (message) { setError(message); return }
    const priceRaw = parseServiceCoins(draft.price)
    if (priceRaw === null) return
    onSave({
      id: draft.id, name: draft.name.trim(), description: draft.description.trim(), gameCategoryId: draft.gameCategoryId ? Number(draft.gameCategoryId) : null,
      serviceCategoryId: Number(draft.serviceCategoryId), gameplay: isGift ? '' : draft.gameplay, platform: isGift ? '' : draft.platform,
      securityLevel: isGift ? '' : draft.securityLevel, providerKind: isGift ? '' : draft.providerKind, providerGrade: isGift ? '' : draft.providerGrade,
      requiredLevel: isGift || draft.providerKind ? '' : draft.requiredLevel, priceRaw, priceUnit: draft.priceUnit.trim() || '份',
      billingProviderCount: Number(draft.billingProviderCount) as 1 | 2, purchaseMode: draft.purchaseMode, purchaseNote: draft.purchaseNote.trim(),
      isAddon: draft.isAddon, active: draft.active, sortOrder: Number(draft.sortOrder), commissionOverride: draft.commissionOverride && !isGift,
      commissionType: draft.commissionType, commissionRate: draft.commissionOverride && draft.commissionType === 'PERCENT' ? Number(draft.commissionRate) : null,
      commissionFixedRaw: draft.commissionOverride && draft.commissionType === 'FIXED' ? parseServiceCoins(draft.commissionFixed, true) ?? 0 : 0,
      commissionPolicy: draft.commissionPolicy, experienceKind: draft.experienceKind, serviceType: draft.experienceKind ? 'EXPERIENCE' : draft.serviceType,
      allowsDiscountCoupons: draft.allowsDiscountCoupons, benefitKind: draft.benefitKind, giftNamingDays: isGift ? Number(draft.giftNamingDays) : 0,
      giftFestival: isGift ? draft.giftFestival.trim() : '', giftStartsAt: isGift ? draft.giftStartsAt : '', giftEndsAt: isGift ? draft.giftEndsAt : '',
      catalogFamily: draft.catalogFamily.trim(), catalogVariant: draft.catalogVariant.trim(), rules: draft.rules.trim(), hasOrders: draft.hasOrders,
    })
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-4xl" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader><DialogTitle>{draft.id ? `编辑服务项 · ID ${draft.id}` : '新增服务项'}</DialogTitle><DialogDescription>配置价格、接单资格、抽成权益与前台展示；保存仅更新本地示例。</DialogDescription></DialogHeader>
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>{isGift ? '当前为礼物商品' : draft.isAddon ? '当前为附加费用' : '当前为陪玩服务'}</span>{dirty && !saveUnknown && <span role="status">有未保存的修改</span>}</div>
        <fieldset disabled={saveUnknown} className="min-w-0">
          <Tabs defaultValue="basic">
            <TabsList className="grid h-11 w-full grid-cols-3"><TabsTrigger value="basic">基础与价格</TabsTrigger><TabsTrigger value="commission">抽成与权益</TabsTrigger><TabsTrigger value="display">展示与规则</TabsTrigger></TabsList>
            <TabsContent value="basic" className="mt-5 space-y-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2 md:col-span-2"><Label htmlFor="service-name">商品名称</Label><Input id="service-name" value={draft.name} maxLength={100} onChange={event => update({ name: event.target.value })} placeholder="如：王者娱乐双排" /></div>
                <div className="space-y-2 md:col-span-2"><Label htmlFor="service-description">商品描述</Label><Input id="service-description" value={draft.description} onChange={event => update({ description: event.target.value })} placeholder="一句话说明服务内容" /></div>
                <div className="space-y-2"><Label htmlFor="service-price">价格（浪花）</Label><Input id="service-price" value={draft.price} inputMode="numeric" onChange={event => update({ price: event.target.value.trim() })} placeholder="大于 0 的整数" />{draft.billingProviderCount === '2' && <p className="text-[11px] text-muted-foreground">双陪按每人价格 × 2 计算订单总价。</p>}</div>
                <div className="space-y-2"><Label htmlFor="service-unit">计价单位</Label><Input id="service-unit" value={draft.priceUnit} maxLength={20} onChange={event => update({ priceUnit: event.target.value })} placeholder="份 / 小时 / 件" /></div>
                <div className="space-y-2"><Label>套餐人数与计价</Label><Select value={draft.billingProviderCount} onValueChange={value => update({ billingProviderCount: value as '1' | '2' })}><SelectTrigger aria-label="套餐人数与计价"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">单陪 · 1 名陪玩</SelectItem><SelectItem value="2">双陪 · 每人价格 × 2</SelectItem></SelectContent></Select></div>
                <div className="space-y-2"><Label>下单方式</Label><Select value={draft.purchaseMode} onValueChange={value => update({ purchaseMode: value as 'DIRECT' | 'CONSULT' })}><SelectTrigger aria-label="下单方式"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="DIRECT">直接下单</SelectItem><SelectItem value="CONSULT">客服确认</SelectItem></SelectContent></Select></div>
                <div className="space-y-2 md:col-span-2"><Label htmlFor="service-purchase-note">确认说明</Label><Input id="service-purchase-note" value={draft.purchaseNote} maxLength={255} onChange={event => update({ purchaseNote: event.target.value })} placeholder="预约、资格、人数或加购条件" /></div>
              </div>
              <div className="grid gap-4 border-t pt-5 md:grid-cols-2">
                <div className="space-y-2"><Label>游戏类目</Label><Select value={draft.gameCategoryId || 'NONE'} onValueChange={value => update({ gameCategoryId: value === 'NONE' ? '' : value })}><SelectTrigger aria-label="游戏类目"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">不选择</SelectItem>{gameCategories.map(category => <SelectItem key={category.id} value={String(category.id)}>{category.name}{category.active ? '' : '（已停用）'}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label>服务分类</Label><Select value={draft.serviceCategoryId} onValueChange={changeCategory}><SelectTrigger aria-label="服务分类"><SelectValue /></SelectTrigger><SelectContent>{serviceCategories.filter(category => !item?.hasOrders || category.isGift === wasGift).map(category => <SelectItem key={category.id} value={String(category.id)}>{category.isGift ? '礼物' : '陪玩'} · {category.name}</SelectItem>)}</SelectContent></Select>{item?.hasOrders && <p className="text-[11px] text-muted-foreground">已有订单，仅可选择同为{wasGift ? '礼物' : '服务'}的分类。</p>}</div>
                {!isGift && <><div className="space-y-2"><Label>所属玩法</Label><Select value={draft.gameplay || 'NONE'} onValueChange={value => update({ gameplay: value === 'NONE' ? '' : value })}><SelectTrigger aria-label="所属玩法"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">普通服务</SelectItem><SelectItem value="娱乐双排">娱乐双排</SelectItem><SelectItem value="技术提升">技术提升</SelectItem><SelectItem value="周末盲盒">周末盲盒</SelectItem></SelectContent></Select></div>
                <div className="space-y-2"><Label>接单方向</Label><Select value={draft.providerKind || 'NONE'} onValueChange={value => update({ providerKind: value === 'NONE' ? '' : value as ServiceDraft['providerKind'], providerGrade: value === 'NONE' ? '' : draft.providerGrade })}><SelectTrigger aria-label="接单方向"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">不选择</SelectItem><SelectItem value="COMPANION">陪玩</SelectItem><SelectItem value="GUARD">护航</SelectItem><SelectItem value="GRIND">代肝</SelectItem><SelectItem value="RANK">上分</SelectItem></SelectContent></Select></div></>}
                {(draft.gameplay || draft.providerKind) && !isGift && <><div className="space-y-2"><Label>游戏平台</Label><Select value={draft.platform || 'NONE'} onValueChange={value => update({ platform: value === 'NONE' ? '' : value as ServiceDraft['platform'] })}><SelectTrigger aria-label="游戏平台"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">请选择</SelectItem><SelectItem value="MOBILE">手游</SelectItem><SelectItem value="PC">端游</SelectItem></SelectContent></Select></div><div className="space-y-2"><Label>行动难度</Label><Select value={draft.securityLevel || 'NONE'} onValueChange={value => update({ securityLevel: value === 'NONE' ? '' : value as ServiceDraft['securityLevel'] })}><SelectTrigger aria-label="行动难度"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">请选择</SelectItem><SelectItem value="CONFIDENTIAL">机密</SelectItem><SelectItem value="TOP_SECRET">绝密</SelectItem><SelectItem value="ANY">不限难度</SelectItem></SelectContent></Select></div></>}
                {draft.providerKind && !isGift ? <div className="space-y-2"><Label htmlFor="service-provider-grade">订单档位</Label><Input id="service-provider-grade" value={draft.providerGrade} onChange={event => update({ providerGrade: event.target.value })} placeholder="如：王者" /></div> : !isGift && <div className="space-y-2"><Label>历史服务档位</Label><Select value={draft.requiredLevel || 'NONE'} onValueChange={value => update({ requiredLevel: value === 'NONE' ? '' : value })}><SelectTrigger aria-label="历史服务档位"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">不限档位</SelectItem><SelectItem value="资深陪玩">资深陪玩</SelectItem><SelectItem value="声浪王牌">声浪王牌</SelectItem></SelectContent></Select></div>}
              </div>
              {isGift && <div className="grid gap-4 border-t pt-5 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="gift-naming-days">每件冠名天数</Label><Input id="gift-naming-days" value={draft.giftNamingDays} inputMode="numeric" onChange={event => update({ giftNamingDays: event.target.value })} /><p className="text-[11px] text-muted-foreground">每件按数量累加，0 表示无冠名权益。</p></div><div className="space-y-2"><Label htmlFor="gift-festival">节日限定</Label><Input id="gift-festival" value={draft.giftFestival} maxLength={40} onChange={event => update({ giftFestival: event.target.value })} placeholder="留空为常规礼物" /><p className="text-[11px] text-muted-foreground">限定礼物价格不得低于 210 浪花。</p></div><div className="space-y-2"><Label htmlFor="gift-starts">礼物开售时间</Label><Input id="gift-starts" type="datetime-local" value={draft.giftStartsAt} onChange={event => update({ giftStartsAt: event.target.value })} /></div><div className="space-y-2"><Label htmlFor="gift-ends">礼物停售时间</Label><Input id="gift-ends" type="datetime-local" value={draft.giftEndsAt} onChange={event => update({ giftEndsAt: event.target.value })} /></div></div>}
              <div className="grid gap-3 border-t pt-5 md:grid-cols-2"><label className="flex min-h-14 items-center justify-between gap-3 rounded-xl border px-4"><span><span className="block text-sm font-medium">附加费用</span><span className="mt-1 block text-[11px] text-muted-foreground">关联主套餐，不能独立下单或计入玩法起价</span></span><Switch aria-label="附加费用" checked={draft.isAddon} disabled={isGift} onCheckedChange={value => update({ isAddon: value })} /></label><label className="flex min-h-14 items-center justify-between gap-3 rounded-xl border px-4"><span><span className="block text-sm font-medium">上架商品</span><span className="mt-1 block text-[11px] text-muted-foreground">最终展示还受类目、分类与玩法状态影响</span></span><Switch aria-label="上架商品" checked={draft.active} onCheckedChange={value => update({ active: value })} /></label></div>
            </TabsContent>
            <TabsContent value="commission" className="mt-5 space-y-5">
              {isGift ? <Alert><Info className="size-4" /><AlertDescription>礼物沿用独立比例规则，不使用商品自定义订单抽成；切换为陪玩分类后可配置。</AlertDescription></Alert> : <div className="space-y-4 rounded-xl border p-4"><div className="flex items-center justify-between gap-4"><div><Label>商品自定义抽成</Label><p className="mt-1 text-[11px] text-muted-foreground">只覆盖抽成，不改变接单资格、价格或活动优先级。</p></div><Switch aria-label="商品自定义抽成" checked={draft.commissionOverride} onCheckedChange={value => update({ commissionOverride: value })} /></div>{draft.commissionOverride ? <div className="grid gap-4 border-t pt-4 md:grid-cols-2"><div className="space-y-2"><Label>抽成方式</Label><div className="grid grid-cols-2 gap-2" role="group" aria-label="商品抽成方式"><Button type="button" variant={draft.commissionType === 'PERCENT' ? 'default' : 'outline'} aria-pressed={draft.commissionType === 'PERCENT'} onClick={() => update({ commissionType: 'PERCENT' })}><Percent className="size-4" />百分比</Button><Button type="button" variant={draft.commissionType === 'FIXED' ? 'default' : 'outline'} aria-pressed={draft.commissionType === 'FIXED'} onClick={() => update({ commissionType: 'FIXED' })}><Coins className="size-4" />整单固定</Button></div></div>{draft.commissionType === 'PERCENT' ? <div className="space-y-2"><Label htmlFor="service-commission-rate">抽成比例（%）</Label><Input id="service-commission-rate" value={draft.commissionRate} inputMode="numeric" onChange={event => update({ commissionRate: event.target.value.trim() })} /><p className="text-[11px] text-muted-foreground">0–100 的整数。</p></div> : <div className="space-y-2"><Label htmlFor="service-commission-fixed">整单固定抽成（浪花）</Label><Input id="service-commission-fixed" value={draft.commissionFixed} inputMode="numeric" onChange={event => update({ commissionFixed: event.target.value.trim() })} /><p className="text-[11px] text-muted-foreground">整单只扣一次，双陪按结算份额分摊。</p></div>}</div> : <p className="border-t pt-4 text-xs text-muted-foreground">当前沿用订单档位、体验或陪玩自动规则；活动命中时以派单试算为准。</p>}</div>}
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2"><Label>体验资格类型</Label><Select value={draft.experienceKind || 'NONE'} disabled={item?.hasOrders} onValueChange={value => update({ experienceKind: value === 'NONE' ? '' : value as ServiceDraft['experienceKind'], serviceType: value === 'NONE' ? draft.serviceType : 'EXPERIENCE' })}><SelectTrigger aria-label="体验资格类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">普通服务</SelectItem><SelectItem value="NEW_FIRST">新客首单</SelectItem><SelectItem value="NEW_VALUE">新客超值</SelectItem><SelectItem value="DAILY">每日一单</SelectItem><SelectItem value="DAILY_VALUE">每日超值</SelectItem></SelectContent></Select>{item?.hasOrders && <p className="text-[11px] text-muted-foreground">已有订单，体验资格已锁定。</p>}</div>
                <div className="space-y-2"><Label>服务项目类型</Label><Select value={draft.serviceType} onValueChange={value => update({ serviceType: value as ServiceDraft['serviceType'] })}><SelectTrigger aria-label="服务项目类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="REGULAR">常规单</SelectItem><SelectItem value="ACTIVITY_LIMITED">活动限定单</SelectItem><SelectItem value="PROMOTIONAL">促销单</SelectItem><SelectItem value="COMPANION">陪玩单</SelectItem><SelectItem value="EXPERIENCE">体验单</SelectItem></SelectContent></Select></div>
                <div className="space-y-2"><Label>默认订单抽成规则</Label><Select value={draft.commissionPolicy} onValueChange={value => update({ commissionPolicy: value as ServiceDraft['commissionPolicy'] })}><SelectTrigger aria-label="默认订单抽成规则"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="LEGACY">原有商品抽成规则</SelectItem><SelectItem value="EXPERIENCE">体验单自动规则</SelectItem><SelectItem value="COMPANION">陪玩单统一 20%</SelectItem><SelectItem value="REGULAR">常规单按陪玩等级</SelectItem><SelectItem value="FUN">趣味单按陪玩等级</SelectItem></SelectContent></Select></div>
                <div className="space-y-2"><Label>VIP 权益绑定类型</Label><Select value={draft.benefitKind || 'NONE'} disabled={item?.hasOrders} onValueChange={value => update({ benefitKind: value === 'NONE' ? '' : value as ServiceDraft['benefitKind'] })}><SelectTrigger aria-label="VIP 权益绑定类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="NONE">无</SelectItem><SelectItem value="COMPANION_HOUR">对应档位 1 小时陪玩</SelectItem><SelectItem value="SAFEBOX_33">33 安全箱代肝主套餐</SelectItem></SelectContent></Select>{item?.hasOrders && <p className="text-[11px] text-muted-foreground">已有订单，VIP 权益已锁定。</p>}</div>
              </div>
              <label className="flex min-h-14 items-center justify-between gap-3 rounded-xl border px-4"><span><span className="block text-sm font-medium">可使用折扣券</span><span className="mt-1 block text-[11px] text-muted-foreground">按此项目设置校验，默认关闭</span></span><Switch aria-label="可使用折扣券" checked={draft.allowsDiscountCoupons} onCheckedChange={value => update({ allowsDiscountCoupons: value })} /></label>
            </TabsContent>
            <TabsContent value="display" className="mt-5 space-y-5"><div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="service-family">同款玩法</Label><Input id="service-family" value={draft.catalogFamily} maxLength={100} onChange={event => update({ catalogFamily: event.target.value })} placeholder="如：常规物资单" /></div><div className="space-y-2"><Label htmlFor="service-variant">档位名称</Label><Input id="service-variant" value={draft.catalogVariant} maxLength={150} onChange={event => update({ catalogVariant: event.target.value })} placeholder="如：1 档 · 保底 866w" /></div><div className="space-y-2"><Label htmlFor="service-sort">排序</Label><Input id="service-sort" value={draft.sortOrder} inputMode="numeric" onChange={event => update({ sortOrder: event.target.value })} /><p className="text-[11px] text-muted-foreground">非负整数，数值越小越靠前。</p></div><div className="space-y-2 md:col-span-2"><Label htmlFor="service-rules">履约规则</Label><Textarea id="service-rules" rows={5} value={draft.rules} maxLength={5000} onChange={event => update({ rules: event.target.value })} placeholder="明确服务目标、完成条件与售后方式；留空沿用玩法规则" /></div></div><Alert><Info className="size-4" /><AlertDescription>真实项目的图片上传、图集和富文本继续沿用现有组件；本静态原型不模拟文件上传或外部图片请求。</AlertDescription></Alert></TabsContent>
          </Tabs>
        </fieldset>
        {error && <Alert variant={saveUnknown ? 'default' : 'destructive'} role="alert"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
        <DialogFooter><Button variant="outline" onClick={requestClose}>{saveUnknown ? '关闭并核对' : '取消'}</Button><Button disabled={saveUnknown} onClick={save}><Save className="size-4" />保存商品</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle><AlertDialogDescription>关闭后，本次商品修改将丢失；已保存的商品和真实业务数据不受影响。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续编辑</AlertDialogCancel><AlertDialogAction onClick={onClose}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
