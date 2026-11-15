import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowDown,
  Check,
  ChevronLeft,
  CircleHelp,
  Clock3,
  Headphones,
  ImagePlus,
  Inbox,
  LoaderCircle,
  MessageCircleMore,
  ReceiptText,
  RefreshCw,
  Send,
  ShieldCheck,
  X,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

export type ChatScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'order' | 'pending' | 'pending-restored' | 'send-error' | 'history-error'

interface ChatPageProps {
  scenario: ChatScenario
  orderNo: string
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onRetry: () => void
}

interface ChatMessage {
  id: string
  from: 'support' | 'self'
  content: string
  time: string
  image?: string
}

const initialMessages: ChatMessage[] = [
  { id: 'm1', from: 'support', content: '你好，这里是声浪电竞平台客服。订单、账号或充值问题都可以在这里留言。', time: '今天 18:30' },
  { id: 'm2', from: 'self', content: '你好，我想确认今晚双陪服务的开局时间和游戏区。', time: '今天 18:34' },
  { id: 'm3', from: 'support', content: '已为你核对：服务计划今天 20:00 开始，使用微信区。开局前陪玩会再次确认位置与昵称。', time: '今天 18:36' },
  { id: 'm4', from: 'self', content: '好的，我会提前上线，谢谢。', time: '今天 18:38' },
]
const olderMessages: ChatMessage[] = [
  { id: 'old1', from: 'support', content: '你的咨询编号为 SL-HELP-10241，后续核对订单时可以提供。', time: '昨天 16:02' },
  { id: 'old2', from: 'self', content: '收到，我会保留这个编号。', time: '昨天 16:05' },
]

export function ChatPage({ scenario, orderNo, onLogin, onBack, onHome, onCompanions, onOrders, onRetry }: ChatPageProps) {
  const orderPrefill = `我想咨询订单 ${orderNo} 的服务情况。`
  const [messages, setMessages] = useState<ChatMessage[]>(scenario === 'empty' ? [] : initialMessages)
  const [content, setContent] = useState(scenario === 'order' ? orderPrefill : '')
  const [contextOrderNo, setContextOrderNo] = useState(scenario === 'order' ? orderNo : '')
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState(scenario === 'pending' || scenario === 'pending-restored')
  const [pendingContent, setPendingContent] = useState(scenario === 'pending' || scenario === 'pending-restored' ? '请帮我再确认一下开局时间。' : '')
  const [feedback, setFeedback] = useState<string | null>(null)
  const [feature, setFeature] = useState<string | null>(null)
  const [abandonOpen, setAbandonOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState(false)
  const [hasMore, setHasMore] = useState(!['empty', 'guest', 'loading', 'error'].includes(scenario))
  const [historyBrowsing, setHistoryBrowsing] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const restored = scenario === 'pending-restored'

  useEffect(() => {
    if (!historyBrowsing && threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight
  }, [messages, historyBrowsing, scenario])

  useEffect(() => {
    if (!composerRef.current) return
    composerRef.current.style.height = 'auto'
    composerRef.current.style.height = `${Math.min(composerRef.current.scrollHeight, 96)}px`
  }, [content, scenario])

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
  }

  function scrollLatest() {
    setHistoryBrowsing(false)
    window.requestAnimationFrame(() => { if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight })
  }

  function loadOlder() {
    if (historyLoading) return
    if (scenario === 'history-error' && !historyError) {
      setHistoryError(true)
      return
    }
    setHistoryLoading(true)
    window.setTimeout(() => {
      setMessages(items => [...olderMessages, ...items])
      setHistoryLoading(false)
      setHistoryError(false)
      setHasMore(false)
      setHistoryBrowsing(true)
    }, 450)
  }

  function clearContext() {
    setContent(value => value === orderPrefill ? '' : value.startsWith(`${orderPrefill}\n`) ? value.slice(orderPrefill.length + 1) : value)
    setContextOrderNo('')
  }

  function sendMessage() {
    const draft = content.trim()
    if (!draft || sending || pending || content.length > 1000) return
    const outgoing = contextOrderNo && !draft.includes(contextOrderNo) ? `订单号 ${contextOrderNo}\n${draft}` : draft
    setSending(true)
    setFeedback(null)
    window.setTimeout(() => {
      setSending(false)
      if (scenario === 'send-error') {
        setPendingContent(outgoing)
        setPending(true)
        return
      }
      setMessages(items => [...items, { id: `local-${Date.now()}`, from: 'self', content: outgoing, time: '刚刚' }])
      setContent('')
      setFeedback('示例消息已添加到本地会话，未发送给真实客服。')
    }, 500)
  }

  function retryPending() {
    if (sending || restored) return
    setSending(true)
    window.setTimeout(() => {
      setMessages(items => [...items, { id: `retry-${Date.now()}`, from: 'self', content: pendingContent || '请帮我再确认一下开局时间。', time: '刚刚' }])
      setPending(false)
      setPendingContent('')
      setContent('')
      setSending(false)
      setFeedback('原示例消息已核对并添加到本地会话，未重复发送。')
    }, 500)
  }

  function chooseImage(file?: File) {
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      setFeedback('图片大小不能超过 5MB，请重新选择。')
      return
    }
    if (!file.type.startsWith('image/')) {
      setFeedback('请选择图片文件。')
      return
    }
    const url = URL.createObjectURL(file)
    setSending(true)
    window.setTimeout(() => {
      setMessages(items => [...items, { id: `image-${Date.now()}`, from: 'self', content: contextOrderNo ? `订单号 ${contextOrderNo}` : '图片', image: url, time: '刚刚' }])
      setSending(false)
      setFeedback('图片仅在本地会话预览，未上传或发送。')
    }, 500)
  }

  const composerDisabled = sending || pending || scenario === 'loading'

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="messages" onNavigate={handleDockNavigate} />}>
      <main className="flex h-[calc(100%_-_80px)] min-h-0 flex-col pb-20">
        <header className="shrink-0 px-4 pb-3 pt-2">
          <div className="flex items-center gap-3">
            <Button type="button" variant="ghost" size="icon" className="size-11 shrink-0 rounded-full" onClick={onBack} aria-label="返回消息中心"><ChevronLeft className="size-5" /></Button>
            <Avatar className="size-11 border border-primary/20 bg-primary/10"><AvatarFallback className="bg-primary/10 text-primary"><Headphones className="size-5" /></AvatarFallback></Avatar>
            <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h1 className="text-lg font-semibold">平台客服</h1><Badge variant="outline" className="border-success/20 bg-success/10 px-1.5 text-[9px] text-success">示例会话</Badge></div><p className="mt-1 truncate text-[10px] text-muted-foreground">支持文字与图片 · 回复以实际会话为准</p></div>
          </div>
        </header>

        {contextOrderNo && <section className="mx-4 mb-2 flex shrink-0 items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 p-2 pl-3"><ReceiptText className="size-4 shrink-0 text-primary" /><p className="min-w-0 flex-1 text-[10px] leading-4 text-muted-foreground">已关联订单 <span className="font-medium text-foreground">{contextOrderNo}</span>，发送时附带订单号。</p><Button type="button" variant="ghost" size="icon" className="size-11 shrink-0" onClick={clearContext} disabled={composerDisabled} aria-label="取消关联订单"><X className="size-4" /></Button></section>}

        {scenario === 'guest' ? (
          <section className="grid flex-1 place-items-center px-4 pb-5"><Card className="w-full rounded-3xl py-0 text-center"><CardContent className="p-6"><MessageCircleMore className="mx-auto size-8 text-primary" /><h2 className="mt-4 text-xl font-semibold">登录后咨询客服</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看会话记录，发送文字与图片并保留订单上下文。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}>微信快捷登录</Button></CardContent></Card></section>
        ) : (
          <>
            <section className="relative mx-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border bg-card" aria-label="客服聊天记录">
              <div className="flex min-h-11 shrink-0 items-center justify-between border-b px-3"><h2 className="text-xs font-medium text-muted-foreground">聊天记录</h2><span className="text-[10px] text-muted-foreground">仅本地示例</span></div>
              {scenario === 'loading' ? (
                <div className="space-y-4 p-4" aria-busy="true" aria-label="正在加载会话"><Skeleton className="h-20 w-4/5 rounded-2xl" /><Skeleton className="ml-auto h-16 w-3/5 rounded-2xl" /><Skeleton className="h-24 w-4/5 rounded-2xl" /></div>
              ) : scenario === 'error' ? (
                <div className="grid flex-1 place-items-center p-5"><div role="alert" className="text-center"><AlertCircle className="mx-auto size-7 text-destructive" /><h2 className="mt-3 text-sm font-semibold">会话暂时没有加载成功</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">请检查网络后重试，未确认的消息不要重复发送。</p><Button type="button" className="mt-5 h-11" onClick={onRetry}>重新加载</Button></div></div>
              ) : (
                <>
                  <div ref={threadRef} className="phone-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={event => { const target = event.currentTarget; if (target.scrollHeight - target.scrollTop - target.clientHeight > 80) setHistoryBrowsing(true) }} aria-live="polite">
                    {hasMore && <div className="p-2 text-center"><Button type="button" variant="ghost" className="h-11 text-[11px]" onClick={loadOlder} disabled={historyLoading}>{historyLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-3.5" />}{historyLoading ? '正在加载' : historyError ? '加载失败，点击重试' : '加载更早消息'}</Button>{historyError && <p role="alert" className="text-[10px] text-destructive">聊天记录暂时无法加载，请重试。</p>}</div>}
                    {messages.length === 0 ? <div className="grid h-full place-items-center p-6 text-center"><div><Inbox className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-sm font-medium">写下你想咨询的问题</p><p className="mt-2 text-xs leading-5 text-muted-foreground">客服回复后会在这里展示。</p></div></div> : <div className="space-y-4 p-3">{messages.map(item => <MessageBubble key={item.id} item={item} />)}</div>}
                  </div>
                  {historyBrowsing && <Button type="button" variant="secondary" className="absolute bottom-3 left-1/2 h-11 -translate-x-1/2 rounded-full px-4 text-[11px] shadow-lg" onClick={scrollLatest}><ArrowDown className="size-4" />回到最新消息</Button>}
                </>
              )}
            </section>

            <section className="shrink-0 border-t bg-background px-4 pb-3 pt-2" aria-label="发送消息">
              {pending && <div className="mb-2 rounded-xl border border-warning/25 bg-warning/5 p-3" role="alert"><div className="flex items-center gap-2"><Clock3 className="size-4 text-warning" /><p className="text-xs font-semibold">上条消息结果未确认，请先核对</p></div>{restored && <p className="mt-1 text-[10px] text-muted-foreground">已从本地恢复核对状态，不能盲目重发。</p>}<div className="mt-2 grid grid-cols-3 gap-1"><Button type="button" variant="outline" className="h-11 px-1 text-[10px]" onClick={() => setFeature('核对消息结果')} disabled={sending}>核对结果</Button>{!restored && <Button type="button" variant="outline" className="h-11 px-1 text-[10px]" onClick={retryPending} disabled={sending}>重试原消息</Button>}<Button type="button" variant="ghost" className={cn('h-11 px-1 text-[10px]', restored && 'col-span-2')} onClick={() => setAbandonOpen(true)} disabled={sending}>放弃核对</Button></div></div>}
              {feedback && <p role="status" className={cn('mb-2 rounded-lg px-3 py-2 text-[10px] leading-4', feedback.includes('不能') || feedback.includes('请选择') ? 'bg-destructive/10 text-destructive' : 'bg-muted/55 text-muted-foreground')}>{feedback}</p>}
              <div className="flex items-end gap-2">
                <Textarea ref={composerRef} value={content} onChange={event => setContent(event.target.value)} maxLength={1000} rows={1} className="max-h-24 min-h-11 flex-1 resize-none rounded-xl bg-card px-3 py-3 text-xs" placeholder="输入你想咨询的问题" aria-label="消息内容" disabled={composerDisabled} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage() } }} />
                <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={event => { chooseImage(event.target.files?.[0]); event.target.value = '' }} aria-label="选择聊天图片" />
                <Button type="button" variant="outline" size="icon" className="size-11 shrink-0 rounded-xl" onClick={() => fileRef.current?.click()} disabled={composerDisabled} aria-label="发送图片"><ImagePlus className="size-4" /></Button>
                <Button type="button" size="icon" className="size-11 shrink-0 rounded-xl" onClick={sendMessage} disabled={composerDisabled || !content.trim()} aria-label={sending ? '正在发送消息' : '发送消息'}>{sending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}</Button>
              </div>
              <div className="mt-1 flex items-center justify-between text-[9px] text-muted-foreground"><span>Enter 发送 · Shift + Enter 换行</span><span>{content.length} / 1000</span></div>
            </section>
          </>
        )}
      </main>

      <Dialog open={feature !== null} onOpenChange={open => !open && setFeature(null)}>
        <DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-xl bg-primary/10 text-primary">{feature === '核对消息结果' ? <ShieldCheck className="size-5" /> : <CircleHelp className="size-5" />}</div><DialogTitle className="text-left text-xl">{feature}</DialogTitle><DialogDescription className="text-left text-sm leading-6">{feature === '核对消息结果' ? '暂未查到这条示例消息的结果，请稍后再核对。原型不会查询真实发送状态，也不会自动重发。' : '该入口仅展示导航反馈，不会读取真实消息或创建新的会话。'}</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full">返回会话</Button></DialogClose></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={abandonOpen} onOpenChange={setAbandonOpen}>
        <DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">放弃核对？</DialogTitle><DialogDescription className="text-left text-sm leading-6">这条消息可能已经送达。可先查看聊天记录，再决定是否发送新消息。</DialogDescription></DialogHeader><DialogFooter className="grid grid-cols-2 gap-2"><DialogClose asChild><Button type="button" variant="outline" className="h-11">继续核对</Button></DialogClose><Button type="button" variant="destructive" className="h-11" onClick={() => { setPending(false); setPendingContent(''); setAbandonOpen(false); setFeedback('已放弃本地示例核对，未执行任何真实操作。') }}>放弃核对</Button></DialogFooter></DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}

function MessageBubble({ item }: { item: ChatMessage }) {
  const self = item.from === 'self'
  return <article className={cn('flex flex-col gap-1', self ? 'items-end' : 'items-start')}><div className="flex items-center gap-2 text-[9px] text-muted-foreground"><span className={cn('font-medium', self ? 'text-primary' : 'text-foreground')}>{self ? '我' : '平台客服'}</span><time>{item.time}</time>{self && <Check className="size-3 text-primary" aria-label="本地示例已展示" />}</div><div className={cn('max-w-[88%] whitespace-pre-wrap break-words rounded-2xl border px-3 py-2 text-xs leading-5', self ? 'rounded-tr-md border-primary/20 bg-primary/10 text-foreground' : 'rounded-tl-md bg-muted/55 text-foreground')}>{item.image && <img src={item.image} alt="本地选择的聊天图片示例" className="mb-2 max-h-44 w-full rounded-xl object-cover" />}{item.content}</div></article>
}
