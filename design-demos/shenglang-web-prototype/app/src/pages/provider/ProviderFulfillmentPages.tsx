import { useState } from 'react'
import { ArrowLeft, CalendarClock, ChevronRight } from 'lucide-react'
import { ProviderFrame } from '@/components/provider/ProviderFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose } from '@/components/ui/dialog'
import { ProviderDialogContent as DialogContent, ProviderDialogFooter as DialogFooter, ProviderDialogHeader } from '@/components/provider/ProviderDialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { DetailGrid, ProviderHeader, ProviderNotice, ProviderPage, ProviderSection, ProviderState } from '@/pages/provider/ProviderShared'
import type { ProviderScenario } from '@/pages/provider/provider-data'

export { ProviderReportsPage } from '@/pages/provider/ProviderReportsPage'

const initialReservations = [
  { id: 1, no: 'YY202611040021', service: '王者荣耀 · 技术陪练', boss: '星野', time: '11 月 5 日 20:00–22:00', duration: '120 分钟 · 5 局', status: '待确认', region: '微信区 · 荣耀王者', remark: '希望主玩打野，开始前会提前上线。' },
  { id: 2, no: 'YY202611030018', service: '无畏契约 · 娱乐匹配', boss: '青柠', time: '11 月 6 日 19:30–21:30', duration: '120 分钟 · 2 小时', status: '已确认', region: '国服 · 钻石', remark: '档期已确认，等待老板转为订单。' },
  { id: 3, no: 'YY202611010009', service: '英雄联盟 · 灵活组排', boss: '晚风', time: '11 月 3 日 18:00–20:00', duration: '120 分钟 · 4 局', status: '已转订单', region: '艾欧尼亚 · 大师', remark: '关联订单 XA202611030096' },
]

export function ProviderReservationsPage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const [items, setItems] = useState(initialReservations)
  const [selected, setSelected] = useState<(typeof initialReservations)[number] | null>(null)
  const [action, setAction] = useState<'confirm' | 'reject' | null>(null)
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState('')
  function act() { if (!selected || !action) return; const status = action === 'confirm' ? '已确认' : '已拒绝'; setItems(rows => rows.map(row => row.id === selected.id ? { ...row, status } : row)); setSelected({ ...selected, status }); setAction(null); setReason(''); setNotice(action === 'confirm' ? '预约已确认，请预留这段档期。' : '预约已拒绝，处理说明已记录。') }
  return <ProviderFrame><ProviderPage>
    <ProviderHeader title={selected ? '预约详情' : '我的预约'} description="按时段核对服务安排与关联订单。" action={selected ? <Button variant="outline" className="h-11" onClick={() => setSelected(null)}><ArrowLeft className="size-4" />全部预约</Button> : <Badge variant="outline" className="mt-1">北京时间</Badge>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂无预约" emptyDescription="老板预约后，档期与游戏要求会显示在这里。" /> : <ProviderSection>{notice && <ProviderNotice>{notice}</ProviderNotice>}{selected ? <div className="mx-auto max-w-2xl space-y-4"><section className="rounded-[12px] border bg-card p-4 md:p-5"><div className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{selected.service}</h2><p className="mt-1 font-mono text-xs text-muted-foreground">{selected.no}</p></div><Badge>{selected.status}</Badge></div><div className="mt-5 rounded-[8px] bg-primary/5 p-4"><p className="flex items-center gap-2 text-sm font-medium"><CalendarClock className="size-4 text-primary" />{selected.time}</p><p className="mt-2 text-xs text-muted-foreground">{selected.duration}</p></div><div className="mt-4"><DetailGrid items={[{ label: '老板', value: selected.boss }, { label: '游戏信息', value: selected.region }]} /></div><p className="mt-4 text-sm leading-6 text-muted-foreground">{selected.remark}</p>{selected.status === '待确认' && <div className="mt-5 flex justify-end gap-2 border-t pt-4"><Button variant="outline" className="h-11" onClick={() => setAction('reject')}>拒绝预约</Button><Button className="h-11" onClick={() => setAction('confirm')}>确认预约</Button></div>}</section></div> : <div className="grid gap-4 md:grid-cols-2">{items.map(item => <button key={item.id} type="button" className="rounded-[12px] border bg-card p-4 text-left shadow-sm hover:border-primary/30 md:p-5" onClick={() => { setSelected(item); setNotice('') }}><div className="flex items-start justify-between gap-3"><h2 className="font-semibold">{item.service}</h2><Badge variant="outline" className={cn(item.status === '待确认' && 'border-warning/25 bg-warning/10 text-warning')}>{item.status}</Badge></div><p className="mt-4 flex items-center gap-2 text-sm"><CalendarClock className="size-4 text-primary" />{item.time}</p><p className="mt-2 text-xs text-muted-foreground">{item.duration} · {item.boss}</p><span className="mt-4 flex items-center justify-end border-t pt-3 text-xs font-medium text-primary">查看预约<ChevronRight className="size-4" /></span></button>)}</div>}</ProviderSection>}
    <Dialog open={action !== null} onOpenChange={open => { if (!open) setAction(null) }}><DialogContent className="rounded-[12px]"><ProviderDialogHeader icon={CalendarClock} tone={action === 'reject' ? 'danger' : 'default'} title={action === 'confirm' ? '确认预约' : '拒绝预约'} description={action === 'confirm' ? '确认后请预留档期，预约转为订单后再开始服务。' : '可填写原因，方便老板了解并重新安排。'} />{action === 'reject' && <div className="space-y-2"><Label htmlFor="reservation-reason">处理说明</Label><Textarea id="reservation-reason" value={reason} onChange={event => setReason(event.target.value)} placeholder="例如：该时段已有服务安排" /> </div>}<DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">返回</Button></DialogClose><Button className="h-11" onClick={act}>{action === 'confirm' ? '确认档期' : '确认拒绝'}</Button></DialogFooter></DialogContent></Dialog>
  </ProviderPage></ProviderFrame>
}
