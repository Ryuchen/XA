import { useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowRight, CalendarClock, Camera, CheckCircle2, ChevronRight, CircleDollarSign, Clock3, Crosshair, Crown, FileCheck2, Gamepad2, Inbox, ShieldCheck, Sparkles, Swords, Upload, UsersRound } from 'lucide-react'
import { useNavigate } from 'react-router'
import { ProviderFrame } from '@/components/provider/ProviderFrame'
import { useStore } from '@/components/store/store-context'
import { FilterBar } from '@/components/prototype/FilterBar'
import { SearchField } from '@/components/prototype/SearchField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose } from '@/components/ui/dialog'
import { ProviderDialogContent as DialogContent, ProviderDialogFooter as DialogFooter, ProviderDialogHeader } from '@/components/provider/ProviderDialog'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import { DetailGrid, ProviderHeader, ProviderNotice, ProviderPage, ProviderSection, ProviderState } from '@/pages/provider/ProviderShared'
import { orderFilters, providerOrders, statusLabels, type ProviderOrder, type ProviderOrderFilter, type ProviderScenario } from '@/pages/provider/provider-data'

type OrderAction = 'detail' | 'grab' | 'signup' | 'start' | 'complete' | 'reject'

export function ProviderWorkbenchPage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const navigate = useNavigate()
  const { store } = useStore()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [filter, setFilter] = useState<ProviderOrderFilter>('pending')
  const [search, setSearch] = useState('')
  const [orders, setOrders] = useState(() => providerOrders.map(order => ({ ...order, orderNo: order.orderNo.replace(/^XA/, store.slug.toUpperCase()), boss: store.boss.nickname })))
  const [selected, setSelected] = useState<ProviderOrder | null>(null)
  const [action, setAction] = useState<OrderAction | null>(null)
  const [fileName, setFileName] = useState('')
  const [online, setOnline] = useState(true)
  const [notice, setNotice] = useState('')

  const visible = useMemo(() => orders.filter(order => {
    const statusMatch = filter === 'pending' ? order.status === 'PENDING' : filter === 'all' ? order.status !== 'PENDING' : order.status === ({ grabbed: 'GRABBED', in_progress: 'IN_SERVICE', completed: 'COMPLETED', cancelled: 'CANCELLED' } as const)[filter]
    return statusMatch && `${order.orderNo}${order.service}${order.boss}${order.game}`.toLowerCase().includes(search.trim().toLowerCase())
  }), [filter, orders, search])

  function open(order: ProviderOrder, nextAction: OrderAction) {
    if (nextAction === 'detail' && order.team) {
      navigate(`/provider/applications/${order.id}?application=${order.id === 102 ? 9002 : 9001}&from=history`)
      return
    }
    setSelected(order); setAction(nextAction); setFileName(''); setNotice('')
  }
  function confirm() {
    if (!selected || !action) return
    if ((action === 'start' || action === 'complete') && !fileName) return
    const nextStatus = action === 'grab' || action === 'signup' ? 'GRABBED' : action === 'start' ? 'IN_SERVICE' : action === 'complete' ? 'COMPLETED' : action === 'reject' ? 'CANCELLED' : selected.status
    setOrders(items => items.map(item => item.id === selected.id ? { ...item, status: nextStatus, teamWaiting: action === 'signup' ? true : item.teamWaiting, target: action === 'complete' ? '服务已完成，凭证可在报单中核对' : item.target } : item))
    setNotice(action === 'grab' ? '抢单成功，订单已进入已接单。' : action === 'signup' ? '组队报名已提交，等待队友与老板确认。' : action === 'start' ? '已开始服务，入队截图已关联到报单。' : action === 'complete' ? '订单已完成，结单截图已关联到报单。' : '已拒单，该订单不再出现在可接列表。')
    if (action === 'start' || action === 'complete') setFilter(action === 'start' ? 'in_progress' : 'completed')
    else if (action === 'grab' || action === 'signup') setFilter('grabbed')
    setAction(null); setSelected(null); setFileName('')
  }

  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="接单工作台" description="把新单、履约和结算安排在同一个工作流里。" />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂时没有可接订单" emptyDescription="保持接单状态，新订单到来后会在这里显示。" emptyAction={<Button className="h-11" onClick={onRetry}>返回正常示例</Button>} /> : <>
      <ProviderSection>
        {notice && <ProviderNotice>{notice}</ProviderNotice>}
        <div className="overflow-hidden rounded-[12px] border bg-card shadow-sm">
          <div className="flex flex-col gap-5 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
            <div className="flex items-center gap-3"><span className="grid size-12 shrink-0 place-items-center rounded-full bg-primary/10 text-lg font-semibold text-primary">{store.provider.nickname.slice(0, 1)}</span><div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold">晚上好，{store.provider.nickname}</h2><Badge variant="outline" className="border-success/25 bg-success/10 text-success">{store.shortName}已认证</Badge></div><p className="mt-1 text-xs text-muted-foreground">本店完成率 {store.provider.completion}% · 评分 {store.provider.rating}</p></div></div>
            <label className="flex min-h-11 items-center justify-between gap-4 rounded-[8px] bg-muted/40 px-3 sm:min-w-44"><span className="flex items-center gap-2 text-sm"><span className={cn('size-2 rounded-full', online ? 'bg-success' : 'bg-muted-foreground')} />{online ? '正在接单' : '暂停接单'}</span><Switch checked={online} onCheckedChange={value => { setOnline(value); setNotice(value ? '已恢复接单，可浏览抢单池。' : '已暂停接单，已有订单不受影响。') }} aria-label="切换接单状态" /></label>
          </div>
          <div className="grid grid-cols-2 border-t md:grid-cols-4">
            <Metric label="今日接单" value={store.provider.todayOrders} unit="单" /><Metric label="服务中" value={store.metrics.inService} unit="单" /><Metric label="完成率" value={store.provider.completion} unit="%" /><Metric label="本月净收益" value={formatWaveAmount(store.provider.monthIncome)} unit="浪花" last />
          </div>
        </div>
      </ProviderSection>

      <ProviderSection className="pb-4">
        <FilterBar ariaLabel="陪玩订单状态筛选" items={orderFilters.map(item => ({ ...item, count: item.value === 'pending' ? 2 : undefined }))} value={filter} onChange={value => { setFilter(value); setNotice('') }} columns={6} />
        <SearchField className="mt-3" value={search} onValueChange={setSearch} placeholder="搜索订单号、服务或老板" ariaLabel="搜索陪玩订单" />
      </ProviderSection>

      <div className="grid items-start gap-6 px-4 pb-6 md:px-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section aria-label="陪玩订单列表">
          <div className="mb-3 flex items-center justify-between gap-3"><div><h2 className="text-base font-semibold">{orderFilters.find(item => item.value === filter)?.label}</h2><p className="mt-1 text-xs text-muted-foreground">{filter === 'pending' ? '抢单前核对时间、要求与预计收益' : `${visible.length} 笔示例订单`}</p></div><Badge variant="outline">北京时间</Badge></div>
          {visible.length ? <div className="grid gap-4">{visible.map(order => <OrderCard key={order.id} order={order} onOpen={open} />)}</div> : <div className="rounded-[12px] border bg-card py-10 text-center"><Inbox className="mx-auto size-7 text-muted-foreground" /><h3 className="mt-3 font-semibold">没有匹配的订单</h3><p className="mt-2 text-sm text-muted-foreground">试试其他状态或搜索词。</p><Button variant="outline" className="mt-5 h-11" onClick={() => { setSearch(''); setFilter('all') }}>查看我的订单</Button></div>}
        </section>
        <aside className="space-y-4 lg:sticky lg:top-20" aria-label="今日服务安排">
          <section className="rounded-[12px] border bg-card p-4 shadow-sm"><div className="flex items-center gap-2"><CalendarClock className="size-4 text-primary" /><h2 className="text-sm font-semibold">接下来服务</h2></div><div className="mt-4 space-y-4"><Schedule time="19:30" title="无畏契约 · 排位上分" note="青柠 · 3 小时" active /><Schedule time="22:00" title="三角洲行动 · 双人护航" note="等待组队确认" /></div><Button variant="ghost" className="mt-4 h-11 w-full justify-between px-0 text-xs" onClick={() => navigate('/provider/reservations')}>查看全部档期<ArrowRight className="size-4" /></Button></section>
          <section className="rounded-[12px] border bg-card p-4"><div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><h2 className="text-sm font-semibold">履约提醒</h2></div><p className="mt-3 text-xs leading-5 text-muted-foreground">开始服务前上传入队截图，完成时上传结单截图；战绩凭证在报单中补齐。</p><Button variant="outline" className="mt-4 h-11 w-full" onClick={() => navigate('/provider/reports')}><FileCheck2 className="size-4" />核对待提交报单</Button></section>
        </aside>
      </div>
    </>}

    <Dialog open={action !== null} onOpenChange={openState => { if (!openState) { setAction(null); setSelected(null); setFileName('') } }}>
      <DialogContent className="sm:max-w-xl">
        {selected && action ? <>
          <ProviderDialogHeader
            icon={action === 'complete' ? CheckCircle2 : action === 'start' ? Camera : action === 'reject' ? AlertCircle : FileCheck2}
            tone={action === 'reject' ? 'danger' : 'default'}
            title={action === 'detail' ? '订单详情' : action === 'grab' ? '确认抢单' : action === 'signup' ? '确认组队报名' : action === 'start' ? '开始服务' : action === 'complete' ? '完成订单' : '确认拒单'}
            description={action === 'detail' ? `订单 ${selected.orderNo}` : action === 'grab' ? '抢单后请按约定时间服务，履约要求会同步到订单。' : action === 'signup' ? '报名不代表成单，需等待队友确认并由老板最终选择。' : action === 'start' ? '上传入队截图后开始计时，截图会同步至该订单报单。' : action === 'complete' ? '上传结单截图后完成订单，后续在报单中补充战绩凭证。' : action === 'reject' ? '拒单后将释放当前履约安排，请确认无法继续服务。' : '核对服务信息与预计净收益。'}
          />
          {action === 'detail' && <OrderGamePoster order={selected} />}
          <div>
            <h3 className="font-semibold">{action === 'detail' ? '订单与履约' : selected.service}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{selected.game} · 老板 {selected.boss}</p>
            <div className="mt-3"><DetailGrid items={[{ label: '数量', value: selected.quantity }, { label: '预计净收益', value: `${formatWaveAmount(selected.income)} 浪花` }, { label: '服务时间', value: selected.time }, { label: '区服 / 身份', value: `${selected.region} · ${selected.identity}` }]} /></div>
            <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">履约目标：{selected.target}</p>
          </div>
          {(action === 'start' || action === 'complete') && <div className="space-y-2"><Label>{action === 'start' ? '入队截图' : '结单截图'} <span className="text-destructive">*</span></Label><input ref={uploadRef} type="file" accept="image/*" className="sr-only" onChange={event => setFileName(event.target.files?.[0]?.name || '')} aria-label={`选择${action === 'start' ? '入队' : '结单'}截图`} /><button type="button" onClick={() => uploadRef.current?.click()} className="flex min-h-20 w-full items-center gap-3 rounded-[8px] border border-dashed bg-muted/30 px-4 text-left hover:border-primary/50"><Upload className="size-5 shrink-0 text-primary" /><span><span className="block text-sm font-medium">{fileName || '选择本地图片'}</span><span className="mt-1 block text-xs text-muted-foreground">仅演示文件选择，不会上传</span></span></button></div>}
          <DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">返回</Button></DialogClose>{action !== 'detail' && <Button className="h-11" variant={action === 'reject' ? 'destructive' : 'default'} disabled={(action === 'start' || action === 'complete') && !fileName} onClick={confirm}>{action === 'grab' ? '确认抢单' : action === 'signup' ? '提交报名' : action === 'start' ? '确认开始' : action === 'complete' ? '确认完成' : '确认拒单'}</Button>}</DialogFooter>
        </> : <ProviderDialogHeader icon={FileCheck2} title="陪玩订单" description="订单操作窗口正在关闭。" />}
      </DialogContent>
    </Dialog>
  </ProviderPage></ProviderFrame>
}

function Metric({ label, value, unit, last = false }: { label: string; value: string; unit: string; last?: boolean }) { return <div className={cn('border-b border-r p-4 last:border-r-0 md:border-b-0', last && 'border-b-0')}><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{value}<span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span></p></div> }
function Schedule({ time, title, note, active = false }: { time: string; title: string; note: string; active?: boolean }) { return <div className="relative border-l pl-4"><span className={cn('absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-card', active ? 'bg-primary' : 'bg-muted-foreground')} /><p className="text-xs font-medium text-primary">{time}</p><p className="mt-1 text-sm font-medium leading-5">{title}</p><p className="mt-1 text-xs text-muted-foreground">{note}</p></div> }

function OrderGamePoster({ order }: { order: ProviderOrder }) {
  const theme = order.game === '三角洲行动'
    ? { icon: Crosshair, background: 'radial-gradient(circle at 88% 20%, rgba(56,189,248,.28), transparent 42%), linear-gradient(120deg, #082f49, #0c4a6e 58%, #155e75)' }
    : order.game === '无畏契约'
      ? { icon: Crosshair, background: 'radial-gradient(circle at 88% 20%, rgba(125,211,252,.25), transparent 42%), linear-gradient(120deg, #172554, #1e3a8a 58%, #0369a1)' }
      : order.game === '英雄联盟'
        ? { icon: Swords, background: 'radial-gradient(circle at 88% 20%, rgba(186,230,253,.24), transparent 42%), linear-gradient(120deg, #020617, #172554 58%, #1e40af)' }
        : order.game === '金铲铲之战'
          ? { icon: Sparkles, background: 'radial-gradient(circle at 88% 20%, rgba(125,211,252,.28), transparent 42%), linear-gradient(120deg, #0f172a, #164e63 58%, #0369a1)' }
          : { icon: order.game === '王者荣耀' ? Crown : Gamepad2, background: 'radial-gradient(circle at 88% 20%, rgba(125,211,252,.28), transparent 42%), linear-gradient(120deg, #020617, #1e3a8a 58%, #0369a1)' }
  const PosterIcon = theme.icon
  return <section data-testid="order-game-poster" aria-label={`${order.game}${order.service}玩法详情海报`} className="relative isolate min-h-44 overflow-hidden rounded-[8px] border border-sky-300/20 p-4 text-white shadow-[0_16px_32px_-22px_rgba(2,6,23,.9)] sm:p-5" style={{ background: theme.background }}>
    <div aria-hidden="true" className="pointer-events-none absolute -right-7 -top-9 -z-10 size-40 rounded-full border border-sky-200/15"><div className="absolute inset-5 rounded-full border border-sky-200/15" /><PosterIcon className="absolute bottom-9 left-9 size-16 text-sky-100/20" /></div>
    <div className="relative max-w-[calc(100%-3rem)] sm:max-w-[calc(100%-6rem)]">
      <h3 className="text-balance text-xl font-semibold leading-7">{order.service}</h3>
      <p className="mt-2 flex items-center gap-2 text-sm font-medium text-sky-100"><PosterIcon className="size-4 shrink-0" />{order.game}</p>
      <p className="mt-2 text-xs leading-5 text-sky-100">{order.region} · {order.identity}</p>
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <span className="inline-flex items-center gap-1.5 rounded-[6px] border border-white/15 bg-slate-950/20 px-2 py-1 text-sky-50"><Clock3 className="size-3.5 shrink-0" />{order.time}</span>
        <span className="rounded-[6px] border border-white/15 bg-slate-950/20 px-2 py-1 text-sky-50">{order.quantity}</span>
      </div>
    </div>
  </section>
}

function OrderCard({ order, onOpen }: { order: ProviderOrder; onOpen: (order: ProviderOrder, action: OrderAction) => void }) {
  const navigate = useNavigate()
  const primaryAction: OrderAction | null = order.status === 'PENDING' ? order.team ? 'signup' : 'grab' : order.status === 'GRABBED' && !order.teamWaiting ? 'start' : order.status === 'IN_SERVICE' ? 'complete' : null
  return <article className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5">
    <div className="flex items-center justify-between gap-3"><span className="truncate font-mono text-xs text-muted-foreground">{order.orderNo}</span><Badge variant="outline" className={cn('shrink-0', order.status === 'PENDING' || order.teamWaiting ? 'border-warning/25 bg-warning/10 text-warning' : order.status === 'COMPLETED' ? 'border-success/25 bg-success/10 text-success' : order.status === 'CANCELLED' ? 'text-muted-foreground' : 'border-primary/25 bg-primary/10 text-primary')}>{order.teamWaiting ? '组队确认中' : statusLabels[order.status]}</Badge></div>
    <button type="button" className="mt-4 flex w-full items-start justify-between gap-4 text-left" onClick={() => onOpen(order, 'detail')}><span className="min-w-0"><span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">{order.game}{order.team && <Badge variant="secondary" className="text-xs"><UsersRound className="size-3" />双陪</Badge>}</span><span className="mt-1 block text-base font-semibold leading-6">{order.service}</span></span><span className="shrink-0 text-right"><span className="block text-xs text-muted-foreground">预计净收益</span><span className="mt-1 block text-xl font-semibold">{formatWaveAmount(order.income)}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花</span></span></span></button>
    <div className="mt-4"><DetailGrid items={[{ label: '老板 / 数量', value: `${order.boss} · ${order.quantity}` }, { label: '服务时间', value: order.time }, { label: '区服', value: order.region }, { label: '游戏身份', value: order.identity }]} /></div>
    <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-muted-foreground"><Clock3 className="mt-0.5 size-4 shrink-0" />{order.target}</p>
    {order.settlement && <p className={cn('mt-3 rounded-[8px] p-3 text-xs leading-5', order.status === 'CANCELLED' ? 'bg-muted/40 text-muted-foreground' : 'bg-success/5 text-success')}><CircleDollarSign className="mr-1 inline size-4" />{order.settlement}</p>}
    <div className="mt-4 border-t pt-3">
      <div className="flex flex-nowrap items-stretch gap-1 rounded-[8px] bg-muted/30 p-1" aria-label="订单操作">
        <Button size="sm" variant="ghost" className="min-w-0 flex-1 rounded-[6px] px-1 text-[11px] text-muted-foreground sm:px-2 sm:text-xs" onClick={() => navigate(`/provider/support-chat?order_no=${order.orderNo}`)}>咨询订单</Button>
        {order.status === 'PENDING' && <Button size="sm" variant="ghost" className="min-w-0 flex-1 rounded-[6px] px-1 text-[11px] text-muted-foreground sm:px-2 sm:text-xs" onClick={() => onOpen(order, 'reject')}>不接此单</Button>}
        <Button size="sm" variant="outline" className="min-w-0 flex-1 rounded-[6px] bg-card px-1 text-[11px] sm:px-2 sm:text-xs" onClick={() => onOpen(order, 'detail')}>订单详情<ChevronRight className="hidden size-3.5 sm:block" /></Button>
        {primaryAction && <Button size="sm" className="min-w-0 flex-1 rounded-[6px] px-1 text-[11px] sm:px-2 sm:text-xs" onClick={() => onOpen(order, primaryAction)}>{primaryAction === 'grab' ? '立即抢单' : primaryAction === 'signup' ? '组队报名' : primaryAction === 'start' ? '开始服务' : '完成订单'}<ChevronRight className="hidden size-3.5 sm:block" /></Button>}
      </div>
    </div>
  </article>
}
