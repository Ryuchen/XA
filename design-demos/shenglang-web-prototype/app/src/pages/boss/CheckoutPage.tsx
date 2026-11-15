import { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Gamepad2,
  Headphones,
  Minus,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Ticket,
  UserRound,
  UsersRound,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'

export type CheckoutScenario = 'default' | 'guest' | 'loading' | 'error' | 'provider' | 'double' | 'quoting' | 'quote-error' | 'insufficient' | 'pending' | 'zero' | 'closed'

interface CheckoutPageProps {
  scenario: CheckoutScenario
  service: CatalogService
  unitPrice: number
  variantName: string
  initialProviderIds?: number[]
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onSupport: (recharge?: boolean) => void
  onRetry: () => void
}

const shortcuts = [
  { id: 1, name: '小澈', initial: '澈', gender: '女陪', available: true },
  { id: 2, name: '阿屿', initial: '屿', gender: '男陪', available: true },
  { id: 3, name: '桃乐丝', initial: '桃', gender: '女陪', available: true },
  { id: 4, name: '北夜', initial: '北', gender: '男陪', available: false },
]

export function CheckoutPage({ scenario, service, unitPrice, variantName, initialProviderIds, onLogin, onBack, onHome, onCompanions, onOrders, onSupport, onRetry }: CheckoutPageProps) {
  const [rounds, setRounds] = useState(1)
  const [route, setRoute] = useState<'PUBLIC' | 'STAFF'>('PUBLIC')
  const [dispatch, setDispatch] = useState('老板选人')
  const [gender, setGender] = useState('不限')
  const [contact, setContact] = useState('由平台安排客服')
  const [coupon, setCoupon] = useState('不使用优惠券')
  const [providers, setProviders] = useState<number[]>(initialProviderIds ?? (scenario === 'provider' || scenario === 'double' ? [1] : []))
  const [region, setRegion] = useState('微信区')
  const [nickname, setNickname] = useState('海浪收藏家')
  const [uid, setUid] = useState('')
  const [remark, setRemark] = useState('')
  const [feature, setFeature] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [formError, setFormError] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const limit = scenario === 'double' ? 2 : 1
  const selectionComplete = providers.length === 0 || providers.length === limit
  const quoting = scenario === 'quoting'
  const quoteError = scenario === 'quote-error'
  const zero = scenario === 'zero'
  const insufficient = scenario === 'insufficient'
  const closed = scenario === 'closed'
  const pending = scenario === 'pending'
  const original = zero ? 0 : unitPrice * rounds
  const bossDiscount = zero ? 0 : Math.min(5, original)
  const promoDiscount = zero ? 0 : Math.min(3, Math.max(0, original - bossDiscount))
  const couponDiscount = zero || coupon === '不使用优惠券' ? 0 : Math.min(10, Math.max(0, original - bossDiscount - promoDiscount))
  const amount = Math.max(0, original - bossDiscount - promoDiscount - couponDiscount)
  const balance = insufficient ? Math.max(0, amount - 10) : 326
  const canSubmit = !pending && !quoting && !quoteError && !insufficient && !closed && selectionComplete

  const selectedNames = useMemo(() => shortcuts.filter(item => providers.includes(item.id)).map(item => item.name).join('、'), [providers])

  function changeRounds(next: number) {
    setRounds(Math.max(1, Math.min(99, Number.isFinite(next) ? Math.round(next) : 1)))
  }

  function toggleProvider(id: number) {
    if (providers.includes(id)) {
      setProviders(providers.filter(item => item !== id))
      return
    }
    if (providers.length >= limit) {
      setFeature(limit === 2 ? '双陪套餐已选齐' : '单陪套餐已选择')
      return
    }
    setProviders([...providers, id])
  }

  function submit() {
    if (!canSubmit) return
    if (!region.trim() || !nickname.trim()) {
      setFormError('请填写游戏昵称与大区')
      return
    }
    setFormError('')
    setConfirmOpen(true)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="orders" onNavigate={handleDockNavigate} />}>
      <main className="pb-44 md:pb-32">
        <header className="px-4 pb-6 pt-2 md:px-6 md:pt-4">
          <Button type="button" variant="ghost" className="h-11 rounded-[8px] px-2 text-sm" onClick={onBack}><ArrowLeft className="size-4" />返回服务</Button>
          <h1 className="mt-4 text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">确认下单</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{zero ? '核对陪玩安排、游戏资料，本单无需支付。' : '核对陪玩安排、游戏资料与本次实付。'}</p>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6">
            <Card className="rounded-[12px] py-0 text-center"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><WalletCards className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后下单</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后核对服务报价、浪花余额与订单进度。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button></CardContent></Card>
          </section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 md:grid md:grid-cols-2 md:space-y-0 md:px-6" aria-busy="true" aria-label="正在加载下单信息">{[180, 250, 220, 180].map((height, index) => <Skeleton key={index} className="w-full rounded-[12px]" style={{ height }} />)}</div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-8 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">下单信息暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，或返回服务详情重新进入。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px]" onClick={onRetry}>重新加载</Button><Button type="button" variant="ghost" className="mt-3 h-11 w-full rounded-[8px]" onClick={onBack}>返回服务详情</Button></div></section>
        ) : (
          <>
            {pending && (
              <section className="mx-4 mb-6 rounded-[12px] border border-warning/30 bg-warning/5 p-4 md:mx-6 md:p-5" role="alert">
                <div className="flex items-center gap-2"><Clock3 className="size-5 text-warning" /><h2 className="text-base font-semibold">上次下单结果待核对</h2></div>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">请先重试上次下单或查看订单和钱包记录，确认结果前不能提交新订单。</p>
                <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4"><Button type="button" className="h-11 rounded-[8px] text-sm" onClick={() => setFeature('重试上次下单')}>重试上次下单</Button><Button type="button" variant="outline" className="h-11 rounded-[8px] text-sm" onClick={() => setFeature('核对或停止上次下单')}>核对或停止</Button><Button type="button" variant="outline" className="h-11 rounded-[8px] text-sm" onClick={onOrders}>查看订单</Button><Button type="button" variant="outline" className="h-11 rounded-[8px] text-sm" onClick={() => onSupport()}>联系客服</Button></div>
              </section>
            )}

            <div className="space-y-6 px-4 pb-6 md:grid md:grid-cols-[minmax(0,1fr)_320px] md:items-start md:gap-6 md:space-y-0 md:px-6 xl:grid-cols-[minmax(0,1fr)_360px]">
              <section className="md:col-span-2" aria-labelledby="checkout-service-title">
                <div className="mb-3 flex items-end justify-between gap-3"><div><h2 id="checkout-service-title" className="text-base font-semibold">本单服务</h2><p className="mt-1 text-xs text-muted-foreground">服务与规格来自上一步选择</p></div><Badge variant="outline" className="rounded-md text-xs">示例订单</Badge></div>
                <Card className="overflow-hidden rounded-[12px] border-slate-700 py-0" style={{ backgroundColor: '#0F172A' }}>
                  <CardContent className="p-5">
                    <div className="flex items-start gap-4">
                      <div className="grid size-12 shrink-0 place-items-center rounded-[8px] border border-white/15 bg-white/10 text-sky-300"><Gamepad2 className="size-6" /></div>
                      <div className="min-w-0 flex-1"><h3 className="text-base font-semibold text-white">{service.name}</h3><p className="mt-2 text-sm leading-6 text-slate-300">{variantName} · {service.platform} · {service.level}</p></div>
                      <p className="shrink-0 text-right text-[28px] font-semibold leading-none tabular-nums text-white">{formatWaveAmount(unitPrice)}<span className="ml-2 text-xs font-normal text-slate-300">浪花 / {service.unit}</span></p>
                    </div>
                  </CardContent>
                </Card>
              </section>

              <div className="space-y-6">
                <Card className="rounded-[12px] py-0">
                  <CardContent className="p-4 md:p-5">
                    <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Clock3 className="size-4 text-primary" /><h2 className="text-base font-semibold">服务安排</h2></div><Badge variant="secondary" className="rounded-md text-xs">常规时段</Badge></div>
                    {closed && <p role="alert" className="mt-4 rounded-[8px] bg-destructive/5 p-3 text-sm leading-6 text-destructive">自主下单暂时关闭，请联系客服</p>}
                    <div className="mt-4 flex items-center justify-between gap-3">
                      <Label htmlFor="checkout-rounds" className="text-sm">购买数量</Label>
                      <div className="flex items-center rounded-[8px] border p-1"><Button type="button" variant="ghost" size="icon" className="size-11 rounded-md" onClick={() => changeRounds(rounds - 1)} disabled={rounds <= 1} aria-label="减少购买数量"><Minus className="size-4" /></Button><Input id="checkout-rounds" type="number" min={1} max={99} value={rounds} onChange={event => changeRounds(Number(event.target.value))} className="h-11 w-12 border-0 px-1 text-center text-base tabular-nums shadow-none focus-visible:ring-0" aria-label="购买数量" /><Button type="button" variant="ghost" size="icon" className="size-11 rounded-md" onClick={() => changeRounds(rounds + 1)} disabled={rounds >= 99} aria-label="增加购买数量"><Plus className="size-4" /></Button></div>
                    </div>

                    {providers.length === 0 ? (
                      <div className="mt-5 border-t pt-4">
                        <p className="text-sm font-medium">派单方式</p>
                        <div className="mt-3 grid grid-cols-2 gap-3">
                          <button type="button" aria-pressed={route === 'PUBLIC'} className={cn('min-h-[92px] rounded-[8px] border p-3 text-left transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring', route === 'PUBLIC' && 'border-primary bg-primary/10')} onClick={() => setRoute('PUBLIC')}><span className="flex items-center gap-2 text-sm font-medium"><UsersRound className="size-4 text-primary" />公开接单</span><span className="mt-2 block text-xs leading-5 text-muted-foreground">符合条件的陪玩可报名</span></button>
                          <button type="button" aria-pressed={route === 'STAFF'} className={cn('min-h-[92px] rounded-[8px] border p-3 text-left transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring', route === 'STAFF' && 'border-primary bg-primary/10')} onClick={() => setRoute('STAFF')}><span className="flex items-center gap-2 text-sm font-medium"><Headphones className="size-4 text-primary" />客服指定</span><span className="mt-2 block text-xs leading-5 text-muted-foreground">不进入公开接单池</span></button>
                        </div>
                        <p className="mt-3 text-xs leading-5 text-muted-foreground">{zero ? '下单后' : '付款后'}待接单时限为 30 分钟；到期仍未安排将自动取消{zero ? '。' : '，实付金额退回余额。'}</p>
                      </div>
                    ) : <p className="mt-4 rounded-[8px] bg-primary/5 p-3 text-sm leading-6 text-primary">已指定陪玩，本单直接安排给所选陪玩，不进入公开接单池。</p>}

                    <div className="mt-5 space-y-4 border-t pt-4">
                      {providers.length === 0 && route === 'PUBLIC' && <CheckoutSelect label="接单方式" value={dispatch} onChange={setDispatch} options={['老板选人', '直接抢单']} />}
                      <CheckoutSelect label="男陪 / 女陪" value={gender} onChange={setGender} options={['不限', '男陪', '女陪']} />
                      <CheckoutSelect label="对接客服" value={contact} onChange={setContact} options={['由平台安排客服', '声浪客服 · 小澜']} />
                    </div>
                  </CardContent>
                </Card>

                <Card className="rounded-[12px] py-0">
                  <CardContent className="p-4 md:p-5">
                    <div className="flex items-center justify-between gap-3"><div><h2 className="text-base font-semibold">挑选陪玩</h2><p className="mt-1 text-xs text-muted-foreground">指定陪玩或交由平台安排</p></div><Badge variant="secondary" className="rounded-md text-xs">{limit === 2 ? '双陪 · 2位' : '单陪 · 1位'}</Badge></div>
                    <button type="button" className="mt-4 flex min-h-16 w-full items-center gap-3 rounded-[8px] border bg-muted/35 p-3 text-left transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" onClick={onCompanions} aria-label={providers.length ? `已选陪玩${selectedNames}，点击重新选择` : '指定陪玩，当前由平台安排'}>
                      <div className="grid size-10 shrink-0 place-items-center rounded-full bg-primary/10 text-primary"><UserRound className="size-5" /></div>
                      <div className="min-w-0 flex-1"><p className="text-sm font-medium">{providers.length ? selectedNames : '指定陪玩'}</p><p className={cn('mt-1 text-xs leading-5', selectionComplete ? 'text-muted-foreground' : 'text-warning')}>{providers.length ? `已选 ${providers.length} / ${limit} 位${selectionComplete ? '，套餐价格不变' : '，请再选择陪玩'}` : `可指定${limit}位陪玩，也可由平台安排`}</p></div>
                      <span className="shrink-0 text-xs text-primary">{providers.length ? '重新选择' : '平台安排'}</span><ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                    </button>
                    <div className="mt-5 border-t pt-4"><p className="text-xs font-medium text-muted-foreground">最近点过</p><div className="mt-3 grid grid-cols-4 gap-2">{shortcuts.map(item => <button key={item.id} type="button" disabled={!item.available} aria-pressed={providers.includes(item.id)} className={cn('flex min-h-[96px] flex-col items-center justify-center rounded-[8px] border px-1 transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50', providers.includes(item.id) && 'border-primary bg-primary/10')} onClick={() => toggleProvider(item.id)} aria-label={`${item.name}${providers.includes(item.id) ? '，已选，点击取消' : item.available ? '，选择陪玩' : '，暂不可接'}`}><span className="grid size-9 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{item.initial}</span><span className="mt-2 text-xs font-medium">{item.name}</span><span className="mt-1 text-xs text-muted-foreground">{providers.includes(item.id) ? '已选择' : item.available ? item.gender : '暂不可接'}</span></button>)}</div></div>
                  </CardContent>
                </Card>
              </div>

              <aside className="space-y-6" aria-label="游戏资料与订单费用">
                <Card className="rounded-[12px] py-0">
                  <CardContent className="p-4 md:p-5">
                    <div className="flex items-center justify-between gap-3"><h2 className="text-base font-semibold">游戏信息</h2><span className="text-xs text-muted-foreground">* 为必填项</span></div>
                    <div className="mt-4 space-y-4">
                      <div><Label htmlFor="checkout-region" className="text-sm">游戏大区 <span className="text-destructive">*</span></Label><Input id="checkout-region" value={region} onChange={event => setRegion(event.target.value)} maxLength={50} placeholder="微信区 / QQ区 / 国服" className="mt-2 h-12 rounded-[8px] text-sm" aria-label="游戏大区，必填" /></div>
                      <div><Label htmlFor="checkout-nickname" className="text-sm">游戏昵称 <span className="text-destructive">*</span></Label><Input id="checkout-nickname" value={nickname} onChange={event => setNickname(event.target.value)} maxLength={50} placeholder="填写本次游戏昵称" className="mt-2 h-12 rounded-[8px] text-sm" aria-label="游戏昵称，必填" /></div>
                      <div><Label htmlFor="checkout-uid" className="text-sm">游戏 UID</Label><Input id="checkout-uid" value={uid} onChange={event => setUid(event.target.value)} maxLength={50} placeholder="便于陪玩找到你（选填）" className="mt-2 h-12 rounded-[8px] text-sm" aria-label="游戏 UID，选填" /></div>
                      <div><Label htmlFor="checkout-remark" className="text-sm">服务备注</Label><Textarea id="checkout-remark" value={remark} onChange={event => setRemark(event.target.value)} maxLength={255} placeholder="说明你的偏好与需求（选填）" className="mt-2 min-h-24 rounded-[8px] text-sm leading-6" aria-label="服务备注，选填" /></div>
                    </div>
                    {formError && <p role="alert" className="mt-3 text-sm leading-6 text-destructive">{formError}</p>}
                  </CardContent>
                </Card>

                <Card className="rounded-[12px] py-0">
                  <CardContent className="p-4 md:p-5">
                    <div className="flex items-center gap-2"><Ticket className="size-4 text-primary" /><h2 className="text-base font-semibold">优惠与支付</h2></div>
                    <div className="mt-4"><CheckoutSelect label="优惠券" value={coupon} onChange={setCoupon} options={['不使用优惠券', '新客立减券 · 减10浪花']} /></div>
                    {quoting ? (
                      <div className="mt-4 rounded-[8px] bg-muted/45 p-5 text-center"><RefreshCw className="mx-auto size-5 animate-spin text-primary" /><p className="mt-2 text-sm text-muted-foreground">正在获取最新报价…</p></div>
                    ) : quoteError ? (
                      <div className="mt-4 rounded-[8px] border border-destructive/20 bg-destructive/5 p-4 text-center" role="alert"><p className="text-sm leading-6 text-destructive">报价暂时无法确认，请刷新后重试</p><Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={onRetry}>刷新报价</Button></div>
                    ) : (
                      <div className="mt-4 border-t pt-4">
                        <div className="space-y-3 text-sm"><FeeRow label="服务原价" value={`${formatWaveAmount(original)} 浪花`} /><FeeRow label="会员优惠" value={`-${formatWaveAmount(bossDiscount)} 浪花`} discount /><FeeRow label="活动优惠" value={`-${formatWaveAmount(promoDiscount)} 浪花`} discount />{couponDiscount > 0 && <FeeRow label="优惠券抵扣" value={`-${formatWaveAmount(couponDiscount)} 浪花`} discount />}</div>
                        <Separator className="my-4" />
                        <div className="flex items-end justify-between gap-3"><span className="text-sm font-medium">应付金额</span><span className="text-[30px] font-semibold leading-none tabular-nums text-primary">{formatWaveAmount(amount)}<span className="ml-2 text-xs font-normal text-muted-foreground">浪花</span></span></div>
                        <div className="mt-4 rounded-[8px] bg-muted/50 p-3"><FeeRow label={zero ? '支付方式' : '余额支付'} value={zero ? '本单无需支付' : `可用 ${formatWaveAmount(balance)} 浪花`} />{insufficient && <p className="mt-2 text-sm leading-6 text-destructive">余额不足，请先联系客服充值</p>}</div>
                        {insufficient && <Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={() => onSupport(true)}>联系客服充值</Button>}
                      </div>
                    )}
                  </CardContent>
                </Card>

                <button type="button" className="flex min-h-12 w-full items-center justify-center gap-2 rounded-[8px] text-sm text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setFeature('费用与下单说明')}><CircleHelp className="size-4" />费用与下单说明<ChevronRight className="size-4" /></button>
                {submitted && <div role="status" className="rounded-[12px] border border-success/25 bg-success/5 p-4 text-center text-sm text-success"><p><Check className="mr-1 inline size-4" />演示订单已核对，未发起真实支付</p><Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px] border-success/25 bg-background/60" onClick={onOrders}>查看订单列表</Button></div>}
              </aside>
            </div>
          </>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && (
        <div className="absolute inset-x-0 bottom-[84px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:bottom-0 md:px-6">
          <div className="mx-auto max-w-[1120px]">
            <div className="flex items-center gap-3 md:justify-between">
              <div className="min-w-[100px]"><p className="text-xs text-muted-foreground">应付</p><p className="mt-1 text-2xl font-semibold leading-none tabular-nums text-primary">{quoting || quoteError ? '—' : formatWaveAmount(amount)}<span className="ml-2 text-xs font-normal text-muted-foreground">浪花</span></p></div>
              <Button type="button" className="h-12 flex-1 rounded-[8px] text-sm md:max-w-64" disabled={!canSubmit} onClick={submit}>{pending ? '请先核对上次订单' : quoting ? '正在报价' : '提交订单'}</Button>
            </div>
            {!selectionComplete ? <p className="mt-2 text-center text-xs text-warning md:text-right">请选齐陪玩，或改为平台安排</p> : quoteError ? <p className="mt-2 text-center text-xs text-destructive md:text-right">报价异常，请核对费用明细</p> : closed ? <p className="mt-2 text-center text-xs text-destructive md:text-right">自主下单暂时关闭，请联系客服</p> : insufficient ? <p className="mt-2 text-center text-xs text-destructive md:text-right">余额不足，请先联系客服充值</p> : null}
          </div>
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}>
          <DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><ShieldCheck className="size-5" /></div><DialogTitle className="text-left text-xl">{zero ? '确认下单' : '确认余额支付'}</DialogTitle><DialogDescription className="text-left text-sm leading-6">{zero ? '本单实付 0 浪花。' : `将支付 ${formatWaveAmount(amount)} 浪花。`}{providers.length ? `已指定陪玩：${selectedNames}。` : route === 'STAFF' ? '由客服指定陪玩，不进入公开接单池。' : dispatch === '直接抢单' ? '公开给符合条件的陪玩直接接单。' : '公开报名后，由你在订单详情选人。'}{providers.length === 0 && `付款后待接单时限为 30 分钟，到期仍未安排将自动取消${zero ? '。' : '并退回余额。'}`}</DialogDescription></DialogHeader>
          <div className="rounded-[8px] bg-muted/60 p-3 text-sm leading-6 text-muted-foreground">这是高保真原型，确认后只展示完成反馈，不会创建订单或扣除浪花。</div>
          <DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 w-full rounded-[8px]">再检查一下</Button></DialogClose><Button type="button" className="h-11 w-full rounded-[8px]" onClick={() => { setConfirmOpen(false); setSubmitted(true) }}>{zero ? '确认下单' : '确认支付'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={feature !== null} onOpenChange={(open) => !open && setFeature(null)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}>
          <DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><Sparkles className="size-5" /></div><DialogTitle className="text-left text-xl">{feature}</DialogTitle><DialogDescription className="text-left text-sm leading-6">{feature === '费用与下单说明' ? '金额以最新报价为准；切换套餐、陪玩、数量或优惠券后需重新报价。结果未明确时请先核对订单与钱包，避免重复购买。' : '原型不会查询或重试真实订单请求。请先核对订单与钱包；仍无法确认结果时，请携带原请求信息联系客服，避免重复购买。'}</DialogDescription></DialogHeader>
          <DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回确认下单</Button></DialogClose></DialogFooter>
        </DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}

function CheckoutSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return <div className="flex items-center justify-between gap-3"><span className="shrink-0 text-sm text-muted-foreground">{label}</span><Select value={value} onValueChange={onChange}><SelectTrigger className="h-11 min-h-11 max-w-[210px] rounded-[8px] bg-muted/35 text-sm shadow-none" aria-label={label}><SelectValue /></SelectTrigger><SelectContent>{options.map(item => <SelectItem key={item} value={item} className="min-h-11 text-sm">{item}</SelectItem>)}</SelectContent></Select></div>
}

function FeeRow({ label, value, discount = false }: { label: string; value: string; discount?: boolean }) {
  return <div className="flex items-center justify-between gap-3"><span className="text-muted-foreground">{label}</span><span className={cn('tabular-nums', discount && 'text-success')}>{value}</span></div>
}
