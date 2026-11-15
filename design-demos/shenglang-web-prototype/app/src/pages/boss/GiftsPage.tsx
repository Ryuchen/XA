import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Crown,
  Gift,
  Heart,
  Inbox,
  Minus,
  MoonStar,
  Plus,
  Ship,
  Sparkles,
  Star,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { FilterBar } from '@/components/prototype/FilterBar'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { SearchField } from '@/components/prototype/SearchField'
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
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type GiftsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'order' | 'pending' | 'pending-blocked' | 'insufficient' | 'wallet-inactive'

interface GiftsPageProps {
  scenario: GiftsScenario
  initialRecipient?: string
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onGiftOrders: () => void
  onClaims: () => void
  onSupport: (recharge?: boolean) => void
  onBack: () => void
  onRetry: () => void
}

interface GiftItem {
  id: number
  name: string
  price: number
  category: '常规礼物' | '节日限定' | '专属冠名'
  namingDays: number
  festival?: string
  icon: LucideIcon
  tone: string
}

const giftItems: GiftItem[] = [
  { id: 1, name: '心动荧光', price: 6, category: '常规礼物', namingDays: 0, icon: Heart, tone: 'from-sky-400/20 to-blue-700/10 text-sky-400' },
  { id: 2, name: '声浪玫瑰', price: 12, category: '常规礼物', namingDays: 0, icon: Sparkles, tone: 'from-cyan-400/20 to-blue-700/10 text-cyan-300' },
  { id: 3, name: '星海守护者', price: 52, category: '专属冠名', namingDays: 3, icon: Star, tone: 'from-blue-400/25 to-indigo-700/10 text-blue-300' },
  { id: 4, name: '冠军王座', price: 99, category: '专属冠名', namingDays: 7, icon: Crown, tone: 'from-sky-300/25 to-blue-800/15 text-sky-200' },
  { id: 5, name: '金秋邮轮', price: 66, category: '节日限定', namingDays: 0, festival: '中秋限定', icon: Ship, tone: 'from-blue-300/20 to-slate-700/15 text-blue-200' },
  { id: 6, name: '月兔巡游', price: 28, category: '节日限定', namingDays: 0, festival: '中秋限定', icon: MoonStar, tone: 'from-cyan-300/20 to-blue-800/15 text-cyan-200' },
]

const recipients = [
  { id: 1, name: '小澈', detail: '荣耀王者 · 最近一起游戏' },
  { id: 2, name: '北夜', detail: '最强王者 · 最近一起游戏' },
  { id: 3, name: '阿屿', detail: '最强王者 · 已关注陪玩' },
]

export function GiftsPage({ scenario, initialRecipient = '', onLogin, onHome, onCompanions, onOrders, onGiftOrders, onClaims, onSupport, onBack, onRetry }: GiftsPageProps) {
  const orderContext = scenario === 'order'
  const pending = scenario === 'pending' || scenario === 'pending-blocked'
  const [category, setCategory] = useState('全部')
  const [recipient, setRecipient] = useState(orderContext ? '小澈' : initialRecipient)
  const [recipientSearch, setRecipientSearch] = useState('')
  const [basket, setBasket] = useState<Record<number, number>>({})
  const [namingTitle, setNamingTitle] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [receipt, setReceipt] = useState(false)
  const [feature, setFeature] = useState<string | null>(null)

  const balance = scenario === 'insufficient' ? 10 : 326
  const walletActive = scenario !== 'wallet-inactive'
  const categories = ['全部', ...Array.from(new Set(giftItems.map(item => item.category)))]
  const visibleGifts = scenario === 'empty' ? [] : category === '全部' ? giftItems : giftItems.filter(item => item.category === category)
  const filteredRecipients = recipients.filter(item => item.name.includes(recipientSearch.trim()))
  const selectedLines = giftItems.filter(item => (basket[item.id] || 0) > 0).map(item => ({ ...item, quantity: basket[item.id], lineTotal: item.price * basket[item.id] }))
  const selectedCount = selectedLines.reduce((sum, item) => sum + item.quantity, 0)
  const namingDays = selectedLines.reduce((sum, item) => sum + item.namingDays * item.quantity, 0)
  const total = selectedLines.reduce((sum, item) => sum + item.lineTotal, 0)
  const namingMissing = namingDays > 0 && !namingTitle.trim()
  const canSend = selectedCount > 0 && !!recipient && walletActive && total <= balance && !namingMissing && !pending

  const selectedSummary = selectedLines.map(item => `${item.name} × ${item.quantity}`).join('、')

  function changeGift(id: number, delta: number) {
    setBasket(current => ({ ...current, [id]: Math.max(0, Math.min(999, (current[id] || 0) + delta)) }))
  }

  function confirmReceipt() {
    setConfirmOpen(false)
    setReceipt(true)
  }

  function resetGifts() {
    setReceipt(false)
    setBasket({})
    setNamingTitle('')
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className={cn(!['guest', 'loading', 'error'].includes(scenario) && !receipt && visibleGifts.length > 0 ? 'pb-44 md:pb-32' : 'pb-32 md:pb-12')}>
        {orderContext && <div className="px-4 pt-2 md:px-6 md:pt-4"><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-2 text-sm" onClick={onBack}><ArrowLeft className="size-4" />返回订单</Button></div>}
        <PageHeader
          className={orderContext ? 'pt-4' : undefined}
          title="把心意，送给 TA"
          description="挑选常规好礼、节日限定或专属冠名。"
          action={<Badge variant="outline" className="mt-1 shrink-0 rounded-md text-xs">示例礼物</Badge>}
        />

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6"><Card className="rounded-[12px] py-0 text-center"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><Gift className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后赠送礼物</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后挑选心意、选择收礼陪玩并使用浪花余额赠送。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button></CardContent></Card></section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 md:grid md:grid-cols-2 md:space-y-0 md:px-6" aria-busy="true" aria-label="正在加载礼物商城"><Skeleton className="h-28 w-full rounded-[12px] md:col-span-2" /><div className="grid grid-cols-2 gap-4">{[220, 220, 220, 220].map((height, index) => <Skeleton key={index} className="w-full rounded-[12px]" style={{ height }} />)}</div><Skeleton className="h-64 w-full rounded-[12px]" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">礼物暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会扣除浪花，也不会重复赠送。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px] md:max-w-64" onClick={onRetry}>重新加载</Button></div></section>
        ) : receipt ? (
          <section className="px-4 md:px-6"><div className="mx-auto max-w-[560px] rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-14 place-items-center rounded-[8px] bg-success/10 text-success"><Check className="size-7" /></div><Badge variant="outline" className="mt-4 rounded-md border-success/25 bg-success/10 text-success">心意已送达 · 原型演示</Badge><h2 className="mt-4 text-xl font-semibold">{recipient} 收到了你的礼物</h2><p className="mt-3 text-sm leading-6">{selectedSummary}</p><p className="mt-1 text-xs text-muted-foreground">共 {selectedCount} 件心意</p><p className="mt-6 text-[32px] font-semibold tabular-nums text-primary">{formatWaveAmount(total)}<span className="ml-2 text-sm font-normal text-muted-foreground">浪花</span></p><p className="mt-2 font-mono text-xs text-muted-foreground">SL202611031052</p>{namingDays > 0 && <p className="mt-4 rounded-[8px] bg-primary/10 p-3 text-sm text-primary">「{namingTitle}」冠名时长已累计增加 {namingDays} 天</p>}<p className="mt-4 text-xs leading-5 text-muted-foreground">仅展示示例回执，未生成真实礼物单，也未扣除浪花。</p><div className="mt-6 grid grid-cols-2 gap-3"><Button type="button" variant="outline" className="h-11 rounded-[8px]" onClick={resetGifts}>再送一份</Button><Button type="button" className="h-11 rounded-[8px]" onClick={onGiftOrders}>查看礼物单</Button></div><Button type="button" variant="ghost" className="mt-3 h-11 w-full rounded-[8px]" onClick={onClaims}>查看权益记录<ChevronRight className="size-4" /></Button></div></section>
        ) : (
          <div className="space-y-6 px-4 pb-6 md:grid md:grid-cols-[minmax(0,1fr)_320px] md:items-start md:gap-6 md:space-y-0 md:px-6 xl:grid-cols-[minmax(0,1fr)_360px]">
            {pending && <section className="rounded-[12px] border border-warning/25 bg-warning/5 p-4 md:col-span-2 md:p-5" role="alert"><div className="flex items-center gap-2"><Clock3 className="size-5 text-warning" /><h2 className="text-base font-semibold">上次赠送待核对</h2></div><p className="mt-2 text-sm leading-6 text-muted-foreground">{scenario === 'pending-blocked' ? '赠送结果尚未确认，请查询结果或联系客服核对，不能重复提交。' : '可先查询结果；查不到时可重试原请求，避免重复赠送。'}</p><div className="mt-4 grid grid-cols-2 gap-3 md:flex"><Button type="button" className="h-11 rounded-[8px] text-sm md:w-40" onClick={() => setFeature('赠送结果查询')}>查询赠送结果</Button>{scenario === 'pending' ? <Button type="button" variant="outline" className="h-11 rounded-[8px] text-sm md:w-36" onClick={() => setFeature('重试原赠送请求')}>重试原请求</Button> : <Button type="button" variant="outline" className="h-11 rounded-[8px] text-sm md:w-32" onClick={() => onSupport()}>联系客服</Button>}<Button type="button" variant="outline" className="col-span-2 h-11 rounded-[8px] text-sm md:w-36" onClick={onGiftOrders}>查看礼物单</Button></div></section>}

            <Card className="rounded-[12px] py-0 shadow-sm md:col-span-2"><CardContent className="p-4 md:p-5"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><UsersRound className="size-4 text-primary" /><h2 className="text-base font-semibold">收礼陪玩</h2></div><span className="text-xs text-muted-foreground">这份心意，送给谁</span></div>
              <div className="md:mt-4 md:grid md:grid-cols-2 md:gap-4">{!orderContext && <SearchField className="mt-4 md:mt-0" value={recipientSearch} onValueChange={setRecipientSearch} placeholder="搜索陪玩昵称" ariaLabel="搜索收礼陪玩昵称" />}
                <div className="mt-3 md:mt-0">{filteredRecipients.length ? <Select value={recipient} onValueChange={setRecipient}><SelectTrigger className="min-h-11 w-full rounded-[8px] text-sm" aria-label="选择收礼陪玩"><SelectValue placeholder="请选择收礼陪玩" /></SelectTrigger><SelectContent>{filteredRecipients.map(item => <SelectItem key={item.id} value={item.name} className="min-h-12"><span>{item.name}</span><span className="ml-2 text-xs text-muted-foreground">{item.detail}</span></SelectItem>)}</SelectContent></Select> : <div className="flex min-h-11 items-center justify-center rounded-[8px] border border-dashed p-3 text-center text-xs text-muted-foreground">没有找到该昵称的陪玩，请更换关键词。</div>}</div>
              </div>{orderContext && <p className="mt-4 rounded-[8px] bg-muted/45 p-3 text-xs leading-5 text-muted-foreground">来自已完成服务 SL202611021024 · 可选择本单陪玩</p>}</CardContent></Card>

            <section aria-labelledby="gift-catalog-title">
              <div className="flex items-end justify-between gap-3"><div><h2 id="gift-catalog-title" className="text-base font-semibold">挑选心意</h2><p className="mt-1 text-xs text-muted-foreground">可跨款多选，每款最多 999 件</p></div><Badge variant="secondary" className="shrink-0 rounded-md text-xs">{selectedCount} 件已选</Badge></div>
              <FilterBar className="mt-4" ariaLabel="礼物分类" items={categories.map(item => ({ value: item, label: item }))} value={category} onChange={setCategory} columns={4} />
              {visibleGifts.length ? <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3" aria-label="礼物列表">{visibleGifts.map(item => <GiftTile key={item.id} item={item} quantity={basket[item.id] || 0} onChange={changeGift} />)}</div> : <div className="mt-4 rounded-[12px] border border-dashed px-5 py-10 text-center"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/60 text-muted-foreground"><Inbox className="size-6" /></div><h3 className="mt-4 text-base font-semibold">礼物正在准备中</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">上架后即可挑选并赠送，当前不会产生任何消费。</p></div>}
            </section>

            <aside className="space-y-6" aria-label="心意清单与赠礼说明">
              {selectedCount > 0 && <Card className="rounded-[12px] border-primary/20 py-0 shadow-sm"><CardContent className="p-4 md:p-5"><div className="flex items-center justify-between border-b pb-3"><h2 className="text-base font-semibold">心意清单</h2><span className="text-xs tabular-nums text-primary">已选 {selectedCount} 件</span></div><div className="mt-3 space-y-3">{selectedLines.map(item => <div key={item.id} className="flex items-center justify-between gap-3 text-sm"><span>{item.name}<span className="ml-1 text-xs text-muted-foreground">× {item.quantity}</span></span><span className="shrink-0 tabular-nums">{formatWaveAmount(item.lineTotal)} 浪花</span></div>)}</div>
                {namingDays > 0 && <div className="mt-4 rounded-[8px] bg-primary/5 p-3"><div className="flex items-center justify-between gap-3"><Label htmlFor="gift-naming" className="flex items-center gap-1.5 text-sm"><Crown className="size-4 text-primary" />专属冠名</Label><span className="text-xs tabular-nums text-primary">累计 {namingDays} 天</span></div><Input id="gift-naming" value={namingTitle} onChange={event => setNamingTitle(event.target.value)} maxLength={30} placeholder="填写 1～30 字，在陪玩列表展示" className="mt-3 h-11 rounded-[8px] bg-card text-sm" />{namingMissing && <p role="alert" className="mt-2 text-xs text-warning">请填写想展示的冠名</p>}<p className="mt-2 text-xs leading-5 text-muted-foreground">每件礼物的时长按数量累加；已有冠名从到期日续延，已到期则从赠送时开始。</p></div>}
                <div className="mt-4 space-y-3 border-t pt-4 text-sm"><div className="flex items-center justify-between"><span className="text-muted-foreground">可用余额</span><span className="font-medium tabular-nums">{formatWaveAmount(balance)} 浪花</span></div><div className="flex items-center justify-between"><span className="text-muted-foreground">合计</span><span className="font-semibold tabular-nums text-primary">{formatWaveAmount(total)} 浪花</span></div></div>{!recipient && <p className="mt-3 text-xs text-warning">请先选择收礼陪玩</p>}{!walletActive && <p role="alert" className="mt-3 text-xs text-destructive">钱包当前不可用，请联系客服。</p>}{total > balance && <div className="mt-3"><p role="alert" className="text-xs text-destructive">余额不足，请联系客服充值。</p><Button type="button" variant="ghost" className="h-11 px-0 text-sm" onClick={() => onSupport(true)}>联系客服<ChevronRight className="size-4" /></Button></div>}</CardContent></Card>}
              <button type="button" className="flex min-h-12 w-full items-center justify-center gap-2 rounded-[8px] text-sm text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setFeature('赠礼与冠名说明')}><CircleHelp className="size-4" />赠礼与冠名说明<ChevronRight className="size-4" /></button>
            </aside>
          </div>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && !receipt && visibleGifts.length > 0 && <div className="absolute inset-x-0 bottom-[84px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:bottom-0 md:px-6"><div className="mx-auto flex max-w-[1120px] items-center justify-between gap-4"><div className="min-w-0 flex-1 md:flex md:flex-initial md:items-center md:gap-4"><p className="text-xs text-muted-foreground">已选 {selectedCount} 件 · 合计</p><p className="mt-1 text-2xl font-semibold tabular-nums text-primary md:mt-0">{formatWaveAmount(total)}<span className="ml-2 text-xs font-normal text-muted-foreground">浪花</span></p></div><Button type="button" className="h-12 min-w-28 rounded-[8px] md:w-44" disabled={!canSend} onClick={() => setConfirmOpen(true)}>{pending ? '请先核对赠送' : '送给 TA'}</Button></div></div>}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><Gift className="size-5" /></div><DialogTitle className="text-left text-xl">确认赠送礼物</DialogTitle><DialogDescription className="text-left text-sm leading-6">送给 {recipient}：{selectedLines.length} 款礼物，共 {selectedCount} 件，将扣除 {formatWaveAmount(total)} 浪花。{namingDays > 0 && `冠名「${namingTitle}」累计 ${namingDays} 天。`}</DialogDescription></DialogHeader><div className="max-h-32 overflow-y-auto rounded-[8px] bg-muted/55 p-3 text-sm leading-6 text-muted-foreground">{selectedSummary}<br />这是原型演示，确认后仅展示回执，不会生成订单或扣除浪花。</div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]">再检查一下</Button></DialogClose><Button type="button" className="h-11 rounded-[8px]" onClick={confirmReceipt}>确认赠送</Button></DialogFooter></DialogContent></Dialog>

      <Dialog open={feature !== null} onOpenChange={open => !open && setFeature(null)}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary">{feature === '赠礼与冠名说明' ? <CircleHelp className="size-5" /> : feature?.includes('客服') ? <UsersRound className="size-5" /> : <Sparkles className="size-5" />}</div><DialogTitle className="text-left text-xl">{feature}</DialogTitle><DialogDescription className="text-left text-sm leading-6">{feature === '赠礼与冠名说明' ? '礼物可跨款多选，每款最多 999 件；冠名天数随数量累计。赠送结果未明确时，请先查询原结果并核对礼物单和钱包，避免重复赠送。' : feature === '赠送结果查询' ? '原型不会查询真实赠送结果。正式页面会保留原请求凭证，并明确已送达、处理中或需要客服核对的结果。' : '原型不会重试真实赠送请求。请先核对礼物单与钱包；仍无法确认结果时，请携带原请求信息联系客服，避免重复赠送。'}</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回礼物商城</Button></DialogClose></DialogFooter></DialogContent></Dialog>
    </PhoneFrame>
  )
}

function GiftTile({ item, quantity, onChange }: { item: GiftItem; quantity: number; onChange: (id: number, delta: number) => void }) {
  const Icon = item.icon
  return (
    <article className={cn('overflow-hidden rounded-[12px] border bg-card shadow-sm transition-colors', quantity > 0 && 'border-primary bg-primary/[0.035]')}>
      <button type="button" className="flex min-h-44 w-full flex-col items-center px-3 pb-3 pt-4 text-center focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onChange(item.id, 1)} aria-label={`添加${item.name}`}>
        <span className="grid size-14 place-items-center rounded-[8px] bg-primary/10 text-primary"><Icon className="size-7" /></span>
        {item.festival && <span className="mt-2 text-xs font-medium text-primary">{item.festival}</span>}
        <span className="mt-2 text-sm font-semibold">{item.name}</span>
        <span className="mt-1 text-base font-semibold tabular-nums text-primary">{formatWaveAmount(item.price)}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花</span></span>
        <span className="mt-1 text-xs leading-5 text-muted-foreground">{item.namingDays ? `冠名 ${item.namingDays} 天 / 件` : '送一份欣赏与心意'}</span>
      </button>
      <div className="flex items-center justify-center border-t py-1"><Button type="button" variant="ghost" size="icon" className="size-11 rounded-[8px]" disabled={!quantity} onClick={() => onChange(item.id, -1)} aria-label={`减少${item.name}`}><Minus className="size-4" /></Button><span className="min-w-7 text-center text-sm font-medium tabular-nums">{quantity}</span><Button type="button" variant="ghost" size="icon" className="size-11 rounded-[8px]" disabled={quantity >= 999} onClick={() => onChange(item.id, 1)} aria-label={`增加${item.name}`}><Plus className="size-4" /></Button></div>
    </article>
  )
}
