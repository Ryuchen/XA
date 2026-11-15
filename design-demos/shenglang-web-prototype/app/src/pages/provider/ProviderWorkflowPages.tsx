import { useRef, useState } from 'react'
import { ArrowLeft, CalendarClock, CheckCircle2, ChevronRight, CircleHelp, CreditCard, Headphones, ImagePlus, Landmark, MessageSquareText, Send, ShieldCheck, UsersRound, WalletCards } from 'lucide-react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { ProviderFrame } from '@/components/provider/ProviderFrame'
import { useStore } from '@/components/store/store-context'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose } from '@/components/ui/dialog'
import { ProviderDialogContent as DialogContent, ProviderDialogFooter as DialogFooter, ProviderDialogHeader } from '@/components/provider/ProviderDialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { DetailGrid, ProviderHeader, ProviderNotice, ProviderPage, ProviderSection, ProviderState } from '@/pages/provider/ProviderShared'
import type { ProviderScenario } from '@/pages/provider/provider-data'

interface WorkflowPageProps {
  scenario: ProviderScenario
  onRetry: () => void
}

type ApplicationStatus = 'INVITED' | 'WAITING_PARTNER' | 'WAITING_SELECTION' | 'WITHDRAWN'
interface ApplicationRecord {
  id: number
  orderId: number
  orderNo: string
  service: string
  game: string
  time: string
  members: string
  createdAt: string
  status: ApplicationStatus
  invited: boolean
}

function applicationStatus(record: ApplicationRecord) {
  return record.status === 'INVITED' ? '等待你确认' : record.status === 'WAITING_PARTNER' ? '等待队友确认' : record.status === 'WAITING_SELECTION' ? '等待老板选定' : '已撤回'
}

function applicationNotice(record: ApplicationRecord) {
  if (record.status === 'WITHDRAWN') return '本次报名已撤回，不再参与老板选队；订单与历史记录仍保留供核对。'
  if (record.status === 'INVITED') return '队友向你发起组队邀请。确认组队后仍需等待其他队友确认，并由老板最终选定，才会进入可服务状态。'
  if (record.status === 'WAITING_PARTNER') return '报名已提交，等待队友确认。组队确认且老板选定后才正式接单，当前不可开始服务。'
  return '队伍成员均已确认，等待老板最终选定。选定结果会通过消息中心通知。'
}

export function ProviderApplicationsPage({ scenario, onRetry }: WorkflowPageProps) {
  const navigate = useNavigate()
  const { store } = useStore()
  const prefix = store.slug.toUpperCase()
  const records: ApplicationRecord[] = [
    { id: 9001, orderId: 105, orderNo: `${prefix}202611040105`, service: '金铲铲之战 · 双人陪玩', game: '金铲铲之战', time: '明天 14:00–16:00', members: `${store.provider.nickname}、青柠`, createdAt: '今天 17:26', status: 'INVITED', invited: true },
    { id: 9002, orderId: 102, orderNo: `${prefix}202611040102`, service: '三角洲行动 · 双人护航', game: '三角洲行动', time: '今天 22:00 开始', members: `${store.provider.nickname}、北岛`, createdAt: '今天 16:08', status: 'WAITING_PARTNER', invited: false },
  ]
  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="我的报名" description="核对订单报名、组队邀请与老板选定进度。" action={<Button variant="outline" className="h-11" onClick={() => navigate('/provider')}><ArrowLeft className="size-4" />返回工作台</Button>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂无报名记录" emptyDescription="订单报名与队友邀请会在这里显示。" emptyAction={<Button onClick={() => navigate('/provider')}>浏览抢单池</Button>} /> : <ProviderSection>
      <div className="grid items-start gap-4 md:grid-cols-2">{records.map(record => <article key={record.id} className="rounded-[12px] border bg-card p-4 md:p-5">
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><Badge variant="outline">{record.invited ? '组队邀请' : '订单报名'}</Badge><h2 className="mt-3 font-semibold">{record.service}</h2><p className="mt-1 break-all font-mono text-xs text-muted-foreground">{record.orderNo}</p></div><Badge variant="outline" className="shrink-0 border-warning/25 bg-warning/10 text-warning">{applicationStatus(record)}</Badge></div>
        <div className="mt-4"><DetailGrid items={[{ label: '队伍成员', value: record.members }, { label: '报名时间', value: record.createdAt }, { label: '意向游戏', value: record.game }, { label: '服务时间', value: record.time }]} /></div>
        <Button variant="ghost" className="mt-4 h-11 w-full justify-between border-t px-0 text-primary" onClick={() => navigate(`/provider/applications/${record.orderId}?application=${record.id}&from=history`)}>查看报名<ChevronRight className="size-4" /></Button>
      </article>)}</div>
    </ProviderSection>}
  </ProviderPage></ProviderFrame>
}

export function ProviderApplicationDetailPage({ scenario, onRetry }: WorkflowPageProps) {
  const navigate = useNavigate()
  const { orderId } = useParams()
  const [params] = useSearchParams()
  const { store } = useStore()
  const prefix = store.slug.toUpperCase()
  const id = Number(orderId)
  const applicationId = Number(params.get('application')) || (id === 102 ? 9002 : 9001)
  const invited = id !== 102
  const initial: ApplicationRecord = {
    id: applicationId,
    orderId: id,
    orderNo: `${prefix}202611040${id}`,
    service: invited ? '金铲铲之战 · 双人陪玩' : '三角洲行动 · 双人护航',
    game: invited ? '金铲铲之战' : '三角洲行动',
    time: invited ? '明天 14:00–16:00' : '今天 22:00 开始',
    members: `${store.provider.nickname}、${invited ? '青柠' : '北岛'}`,
    createdAt: invited ? '今天 17:26' : '今天 16:08',
    status: invited ? 'INVITED' : 'WAITING_PARTNER',
    invited,
  }
  const [record, setRecord] = useState(initial)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const found = id === 102 || id === 105
  const backPath = params.get('from') === 'history' ? '/provider/applications' : '/provider/messages'
  function confirmTeam() {
    setRecord(old => ({ ...old, status: 'WAITING_SELECTION' }))
    setNotice('组队已确认 · 示例状态。仍需等待老板最终选定，当前不可开始服务。')
  }
  function withdraw() {
    setRecord(old => ({ ...old, status: 'WITHDRAWN' }))
    setConfirmOpen(false)
    setNotice(invited ? '已拒绝组队邀请 · 示例状态。' : '报名已撤回 · 示例状态。')
  }
  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="报名详情" description="确认队伍与订单要求，再处理本次报名。" action={<Button variant="outline" className="h-11" onClick={() => navigate(backPath)}><ArrowLeft className="size-4" />返回</Button>} />
    {scenario !== 'default' || !found ? <ProviderState scenario={found ? scenario : 'empty'} onRetry={onRetry} emptyTitle="未找到该报名" emptyDescription="请返回报名列表或消息，重新核对邀请信息。" emptyAction={<Button onClick={() => navigate('/provider/applications')}>返回我的报名</Button>} /> : <ProviderSection>
      {notice && <ProviderNotice>{notice}</ProviderNotice>}
      <article className="mx-auto max-w-3xl rounded-[12px] border bg-card p-4 md:p-5">
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><Badge variant="outline">{record.invited ? '队友邀请' : '我的报名'}</Badge><h2 className="mt-3 text-lg font-semibold">{record.service}</h2><p className="mt-1 break-all font-mono text-xs text-muted-foreground">{record.orderNo}</p></div><Badge variant="outline" className={cn('shrink-0', record.status === 'WITHDRAWN' ? 'text-muted-foreground' : 'border-warning/25 bg-warning/10 text-warning')}>{applicationStatus(record)}</Badge></div>
        <div className="mt-4"><DetailGrid items={[{ label: '队伍成员', value: record.members }, { label: '报名时间', value: record.createdAt }, { label: '意向游戏', value: record.game }, { label: '服务时间', value: record.time }]} /></div>
        <p className="mt-4 flex items-start gap-2 rounded-[8px] bg-primary/5 p-4 text-sm leading-6"><UsersRound className="mt-1 size-4 shrink-0 text-primary" /><span>{applicationNotice(record)}</span></p>
        <p className="mt-3 flex items-start gap-2 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground"><CalendarClock className="mt-0.5 size-4 shrink-0" />服务要求：{invited ? 'QQ 区 · 双人模式，保持积极沟通，按约定时间入队。' : 'PC 国服 · 绝密难度，双人组队，老板确认后成单。'}</p>
        <div className="mt-5 flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button variant="ghost" className="mr-auto h-11" onClick={() => navigate(`/provider/support-chat?order_no=${record.orderNo}`)}><Headphones className="size-4" />咨询订单</Button>
          {record.status === 'INVITED' && <Button className="h-11" onClick={confirmTeam}><CheckCircle2 className="size-4" />确认组队</Button>}
          {record.status !== 'WITHDRAWN' && <Button variant="outline" className="h-11" onClick={() => setConfirmOpen(true)}>{record.status === 'INVITED' ? '拒绝邀请' : '撤回报名'}</Button>}
        </div>
      </article>
    </ProviderSection>}
    <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><DialogContent className="rounded-[12px]"><ProviderDialogHeader icon={UsersRound} tone="danger" title={record.status === 'INVITED' ? '拒绝组队邀请？' : '撤回报名？'} description={record.status === 'INVITED' ? '拒绝后不再参与本次组队，请核对订单与队友信息。' : '撤回后不再参与本次报名；组队订单需重新报名才能继续参与。'} /><DetailGrid items={[{ label: '订单', value: record.orderNo }, { label: '队伍成员', value: record.members }]} /><DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">保留报名</Button></DialogClose><Button variant="destructive" className="h-11" onClick={withdraw}>{record.status === 'INVITED' ? '确认拒绝' : '确认撤回'}</Button></DialogFooter></DialogContent></Dialog>
  </ProviderPage></ProviderFrame>
}

type PayeeMethod = 'WECHAT' | 'ALIPAY' | 'BANK'
interface PayeeAccount { name: string; number: string }
const payeeMethods: { id: PayeeMethod; label: string; hint: string }[] = [
  { id: 'WECHAT', label: '微信', hint: '用于微信收款，账号可为微信号或绑定手机号。' },
  { id: 'ALIPAY', label: '支付宝', hint: '请核对支付宝账号与收款人姓名。' },
  { id: 'BANK', label: '银行卡', hint: '填写本人银行卡号，开户信息需与实名一致。' },
]

export function ProviderWithdrawAccountsPage({ scenario, onRetry }: WorkflowPageProps) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { store } = useStore()
  const requestedMethod = params.get('method')
  const initialMethod = payeeMethods.some(item => item.id === requestedMethod) ? requestedMethod as PayeeMethod : null
  const [accounts, setAccounts] = useState<Partial<Record<PayeeMethod, PayeeAccount>>>({ WECHAT: { name: store.provider.nickname, number: 'wx_sl_2086' }, ALIPAY: { name: store.provider.nickname, number: '138****8000' } })
  const [editor, setEditor] = useState<PayeeMethod | null>(initialMethod)
  const [removing, setRemoving] = useState<PayeeMethod | null>(null)
  const [name, setName] = useState(initialMethod ? accounts[initialMethod]?.name ?? store.provider.nickname : '')
  const [number, setNumber] = useState(initialMethod ? accounts[initialMethod]?.number ?? '' : '')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  function edit(method: PayeeMethod) {
    setEditor(method)
    setName(accounts[method]?.name ?? store.provider.nickname)
    setNumber(accounts[method]?.number ?? '')
    setError('')
    setNotice('')
  }
  function save() {
    if (!editor) return
    if (!name.trim() || !number.trim()) { setError('请完整填写收款人姓名与收款账号。'); return }
    setAccounts(old => ({ ...old, [editor]: { name: name.trim(), number: number.trim() } }))
    setEditor(null)
    setNotice('收款账户已保存 · 仅更新当前原型，提现时请重新核对。')
  }
  function unbind() {
    if (!removing) return
    setAccounts(old => { const next = { ...old }; delete next[removing]; return next })
    setNotice('收款账户已解绑 · 已提交提现的账户快照不受影响。')
    setRemoving(null)
  }
  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="收款账户" description="微信、支付宝、银行卡各可绑定一个账户。" action={<Button variant="outline" className="h-11" onClick={() => navigate('/provider/wallet')}><ArrowLeft className="size-4" />返回钱包</Button>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂未读取收款账户" emptyDescription="可返回正常示例，绑定用于后续提现的收款方式。" emptyAction={<Button onClick={onRetry}>查看账户示例</Button>} /> : <ProviderSection>
      {notice && <ProviderNotice>{notice}</ProviderNotice>}
      <div className="mb-4 flex items-start gap-2 rounded-[8px] border border-primary/20 bg-primary/5 p-3 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />收款资料变更不影响已提交提现的原账户快照；解绑后不可再用于新提现。此处不保存或验证真实账户。</div>
      <div className="grid items-start gap-4 md:grid-cols-2 lg:grid-cols-3">{payeeMethods.map(method => {
        const account = accounts[method.id]
        const Icon = method.id === 'BANK' ? Landmark : method.id === 'ALIPAY' ? CreditCard : WalletCards
        return <article key={method.id} className="flex min-h-56 flex-col rounded-[12px] border bg-card p-4 md:p-5"><div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 font-semibold"><Icon className="size-5 text-primary" />{method.label}</h2><Badge variant="outline" className={account ? 'border-success/25 text-success' : 'text-muted-foreground'}>{account ? '已绑定' : '未绑定'}</Badge></div>
          {account ? <div className="mt-4"><DetailGrid items={[{ label: '收款人', value: account.name }, { label: '收款账号', value: account.number }]} /></div> : <div className="mt-4"><p className="text-sm text-muted-foreground">绑定后可在提现时选择。</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{method.hint}</p></div>}
          <div className="mt-auto flex flex-wrap justify-end gap-2 border-t pt-4"><Button variant={account ? 'outline' : 'default'} className="h-11" onClick={() => edit(method.id)}>{account ? '修改' : '绑定'}</Button>{account && <Button variant="destructive" className="h-11" onClick={() => setRemoving(method.id)}>解绑</Button>}</div>
        </article>
      })}</div>
    </ProviderSection>}
    <Dialog open={editor !== null} onOpenChange={open => { if (!open) { setEditor(null); setError('') } }}><DialogContent className="rounded-[12px]"><ProviderDialogHeader icon={CreditCard} title={`${payeeMethods.find(item => item.id === editor)?.label ?? ''}收款账户`} description="收款资料变更不影响已提交提现的原账户快照。原型仅展示本地示例状态。" /><div className="space-y-4"><div className="space-y-2"><Label htmlFor="payee-name">收款人姓名</Label><Input id="payee-name" value={name} onChange={event => { setName(event.target.value); setError('') }} autoComplete="off" /></div><div className="space-y-2"><Label htmlFor="payee-number">收款账号</Label><Input id="payee-number" value={number} onChange={event => { setNumber(event.target.value); setError('') }} autoComplete="off" placeholder={editor === 'BANK' ? '请输入本人银行卡号' : '请输入收款账号'} /></div>{error && <p role="alert" className="text-xs text-destructive">{error}</p>}</div><DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">取消</Button></DialogClose><Button className="h-11" onClick={save}>保存示例账户</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={removing !== null} onOpenChange={open => !open && setRemoving(null)}><DialogContent className="rounded-[12px]"><ProviderDialogHeader icon={CreditCard} tone="danger" title={`解绑${payeeMethods.find(item => item.id === removing)?.label ?? ''}收款账户？`} description="解绑后不可再用于新提现，已提交提现的账户快照保留。" />{removing && accounts[removing] && <DetailGrid items={[{ label: '收款人', value: accounts[removing].name }, { label: '收款账号', value: accounts[removing].number }]} />}<DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">取消</Button></DialogClose><Button variant="destructive" className="h-11" onClick={unbind}>确认解绑</Button></DialogFooter></DialogContent></Dialog>
  </ProviderPage></ProviderFrame>
}

interface ChatMessage { id: number; own: boolean; text: string; time: string; delivered?: boolean }
const supportFaqs = [
  { q: '组队报名后为什么还不能开始？', a: '双陪订单需队友确认，并由老板最终选定。组队确认完成前不会进入可服务状态。' },
  { q: '修改收款账户会影响已提交提现吗？', a: '不会。已提交提现保留原账户快照，新账户只用于后续申请。' },
  { q: '发送结果未确认时怎么办？', a: '先核对当前会话和订单记录，不要重复提交资金申请。真实环境中仍需由服务端回执确认。' },
]

export function ProviderSupportChatPage({ scenario, onRetry }: WorkflowPageProps) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { store } = useStore()
  const orderNo = params.get('order_no') || ''
  const fileRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const [fileName, setFileName] = useState('')
  const [answer, setAnswer] = useState('')
  const [notice, setNotice] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 1, own: false, text: orderNo ? `你好，已为你关联订单 ${orderNo}。请说明需要核对的问题。` : `你好，我是${store.shortName}客服。请描述遇到的问题，涉及订单时请附上订单号。`, time: '18:45' },
    { id: 2, own: true, text: orderNo ? '我想确认组队进度和开始服务的条件。' : '你好，我想了解本周接单安排。', time: '18:46', delivered: true },
    { id: 3, own: false, text: orderNo ? '组队需队友确认，并由老板最终选定后才可服务。你可以继续补充订单截图。' : '可以在资料与档期中调整常规服务日，已有预约不受影响。', time: '18:47' },
  ])
  function send() {
    const text = draft.trim()
    if (!text && !fileName) return
    const content = [text, fileName ? `图片附件：${fileName}` : ''].filter(Boolean).join('\n')
    setMessages(rows => [...rows, { id: Date.now(), own: true, text: content, time: '刚刚', delivered: true }, { id: Date.now() + 1, own: false, text: '已收到你的示例留言。当前页面不会创建真实客服工单。', time: '刚刚' }])
    setDraft('')
    setFileName('')
    setNotice('消息已在当前页面标记为已发送 · 未发送真实消息。')
  }
  return <ProviderFrame><ProviderPage>
    <ProviderHeader title="客服会话" description="保留订单上下文，核对历史消息与发送结果。" action={<Button variant="outline" className="h-11" onClick={() => navigate('/provider/support')}><CircleHelp className="size-4" />帮助中心</Button>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂无客服会话" emptyDescription="发起新会话后，沟通记录会在这里显示。" emptyAction={<Button onClick={onRetry}>查看会话示例</Button>} /> : <ProviderSection>
      {notice && <ProviderNotice>{notice}</ProviderNotice>}
      {orderNo && <section className="mb-4 rounded-[12px] border bg-card p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="flex items-center gap-2 text-sm font-semibold"><MessageSquareText className="size-4 text-primary" />关联订单</p><p className="mt-1 break-all font-mono text-xs text-muted-foreground">{orderNo}</p></div><Button variant="outline" className="h-11" onClick={() => navigate(orderNo.endsWith('105') ? '/provider/applications/105?application=9001&from=history' : '/provider')}>{orderNo.endsWith('105') ? '查看组队报名' : '返回订单工作台'}<ChevronRight className="size-4" /></Button></div></section>}
      <div className="grid items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="rounded-[12px] border bg-card p-4"><h2 className="text-sm font-semibold">常见问题</h2><div className="mt-3 space-y-2">{supportFaqs.map(item => <button key={item.q} type="button" className={cn('min-h-11 w-full rounded-[8px] border px-3 py-2 text-left text-xs leading-5 hover:border-primary/30', answer === item.a && 'border-primary bg-primary/5')} onClick={() => setAnswer(item.a)}>{item.q}</button>)}</div>{answer && <p role="status" className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">{answer}</p>}<p className="mt-4 border-t pt-4 text-xs leading-5 text-muted-foreground">会话与附件仅保留在当前演示页面，切换页面后不保证保留草稿。</p></aside>
        <section className="overflow-hidden rounded-[12px] border bg-card" aria-label="站内客服消息"><div className="flex items-center justify-between gap-3 border-b p-4"><div><h2 className="text-sm font-semibold">{store.shortName}客服</h2><p className="mt-1 text-xs text-muted-foreground">历史消息与本地示例发送反馈</p></div><Badge variant="outline"><span className="size-2 rounded-full bg-success" />示例在线</Badge></div>
          <div className="max-h-[480px] min-h-72 space-y-4 overflow-y-auto p-4">{messages.map(message => <div key={message.id} className={cn('flex', message.own && 'justify-end')}><div className={cn('max-w-[85%] rounded-[12px] px-3 py-2.5 text-sm leading-6', message.own ? 'bg-primary text-primary-foreground' : 'bg-muted/60')}><p className="whitespace-pre-wrap break-words">{message.text}</p><p className={cn('mt-1 text-xs', message.own ? 'text-primary-foreground/70' : 'text-muted-foreground')}>{message.time}{message.delivered ? ' · 已发送（示例）' : ''}</p></div></div>)}</div>
          <div className="border-t p-3">{fileName && <div className="mb-2 flex items-center justify-between gap-3 rounded-[8px] bg-muted/40 px-3 py-2 text-xs"><span className="min-w-0 truncate">图片附件：{fileName}</span><button type="button" className="min-h-8 shrink-0 text-primary" onClick={() => setFileName('')}>移除</button></div>}<Label htmlFor="provider-chat-draft" className="sr-only">输入客服消息</Label><div className="flex flex-nowrap items-end gap-2"><input ref={fileRef} type="file" accept="image/*" className="sr-only" aria-label="选择客服图片附件" onChange={event => setFileName(event.target.files?.[0]?.name || '')} /><Textarea id="provider-chat-draft" value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} placeholder="描述问题，可附上订单号或图片" className="min-h-10 min-w-0 flex-1 resize-none rounded-[8px] py-2.5" rows={1} /><div className="flex shrink-0 gap-1.5"><Button type="button" variant="outline" size="icon-lg" className="shrink-0 rounded-[8px]" aria-label="添加图片附件" onClick={() => fileRef.current?.click()}><ImagePlus className="size-4" /></Button><Button size="icon-lg" className="shrink-0 rounded-[8px]" disabled={!draft.trim() && !fileName} onClick={send} aria-label="发送客服消息"><Send className="size-4" /></Button></div></div><p className="mt-2 text-xs text-muted-foreground">Enter 发送，Shift + Enter 换行；图片仅记录本地文件名。</p></div>
        </section>
      </div>
    </ProviderSection>}
  </ProviderPage></ProviderFrame>
}
