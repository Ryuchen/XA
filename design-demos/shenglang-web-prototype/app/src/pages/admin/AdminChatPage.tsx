import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowDown,
  Check,
  CheckCheck,
  CircleAlert,
  Clock3,
  ExternalLink,
  FileImage,
  Headset,
  ImagePlus,
  Inbox,
  Info,
  LoaderCircle,
  LockKeyhole,
  MessageCircleMore,
  MessagesSquare,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  X,
} from 'lucide-react'
import type { AdminChatScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { adminChatConversations, type AdminChatConversation, type AdminChatMessage } from '@/pages/admin/admin-chat-data'
import { cn } from '@/lib/utils'

interface AdminChatPageProps {
  scenario: AdminChatScenario
  onScenarioChange: (scenario: AdminChatScenario) => void
  onOpenOrder: (orderNo: string) => void
}

interface PendingReply {
  body: string
  clientMessageId: string
  saved: boolean
}

interface Attachment {
  name: string
  url: string
  size: number
}

const orderPattern = /(\bORD[0-9A-F]{20}\b)/gi
const exactOrderPattern = /^ORD[0-9A-F]{20}$/i

function initialMessages() {
  return Object.fromEntries(adminChatConversations.map(conversation => [conversation.id, [...conversation.messages]])) as Record<number, AdminChatMessage[]>
}

function initialUnreadMessageIds() {
  return adminChatConversations.flatMap(conversation => conversation.unreadSupport > 0 ? conversation.messages.filter(message => message.sender === 'user').slice(-conversation.unreadSupport).map(message => message.id) : [])
}

export function AdminChatPage({ scenario, onScenarioChange, onOpenOrder }: AdminChatPageProps) {
  const [selectedId, setSelectedId] = useState(adminChatConversations[0].id)
  const [conversations, setConversations] = useState(adminChatConversations)
  const [messages, setMessages] = useState<Record<number, AdminChatMessage[]>>(() => {
    const initial = initialMessages()
    if (scenario === 'history-browsing') initial[adminChatConversations[0].id] = [...adminChatConversations[0].olderMessages, ...initial[adminChatConversations[0].id]]
    return initial
  })
  const [drafts, setDrafts] = useState<Record<number, string>>(() => ({
    [adminChatConversations[0].id]: scenario === 'send-error' ? '请再帮我确认一下，今晚还是 20:00 开局。' : scenario === 'sending' ? '收到，我会准时提醒老板上线。' : '',
    [adminChatConversations[1].id]: '已收到双陪进队反馈，我会继续核对结单状态。',
  }))
  const [attachments, setAttachments] = useState<Record<number, Attachment | undefined>>({})
  const [search, setSearch] = useState('')
  const [unreadOnly, setUnreadOnly] = useState(scenario === 'unread')
  const [unreadMessageIds, setUnreadMessageIds] = useState<number[]>(initialUnreadMessageIds)
  const [historyLoaded, setHistoryLoaded] = useState<number[]>(scenario === 'history-browsing' ? [adminChatConversations[0].id] : [])
  const [historyResolved, setHistoryResolved] = useState(false)
  const [messageResolved, setMessageResolved] = useState(false)
  const [historyBrowsing, setHistoryBrowsing] = useState(scenario === 'history-browsing')
  const [sendingIds, setSendingIds] = useState<number[]>([])
  const [pendingReplies, setPendingReplies] = useState<Record<number, PendingReply | undefined>>(() => scenario === 'pending' || scenario === 'pending-unknown' || scenario === 'pending-unsaved' ? {
    [adminChatConversations[0].id]: {
      body: '收到，我会准时提醒老板上线。',
      clientMessageId: 'staff_501_20261104_01',
      saved: scenario !== 'pending-unsaved',
    },
  } : {})
  const [pendingNotice, setPendingNotice] = useState('')
  const [composerIssue, setComposerIssue] = useState(scenario === 'send-error' ? '发送失败，当前草稿已保留。可以重试，发送结果未知时才会进入待核对。' : '')
  const [notice, setNotice] = useState('')
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  const [abandonOpen, setAbandonOpen] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const nextMessageId = useRef(10000)
  const nextClientId = useRef(2)

  const selected = conversations.find(conversation => conversation.id === selectedId) ?? conversations[0]
  const selectedMessages = messages[selected.id] ?? []
  const draft = drafts[selected.id] ?? ''
  const attachment = attachments[selected.id]
  const pending = pendingReplies[selected.id]
  const readOnly = scenario === 'readonly'
  const canViewOrders = scenario !== 'no-order-permission'
  const sending = scenario === 'sending' || sendingIds.includes(selected.id)
  const messageLoading = scenario === 'message-loading' && !messageResolved
  const messageError = scenario === 'message-error' && !messageResolved
  const sessionLoading = scenario === 'session-loading'
  const sessionError = scenario === 'session-error'
  const historyLoading = scenario === 'history-loading' && !historyResolved
  const historyError = scenario === 'history-error' && !historyResolved
  const hasMore = selected.olderMessages.length > 0 && !historyLoaded.includes(selected.id)
  const visibleUnreadIds = selectedMessages.filter(message => message.sender === 'user' && unreadMessageIds.includes(message.id)).map(message => message.id)
  const totalUnread = conversations.reduce((total, conversation) => total + conversation.unreadSupport, 0)

  const filteredConversations = useMemo(() => {
    if (scenario === 'empty') return []
    const keyword = search.trim().toLowerCase()
    return conversations.filter(conversation => {
      if (unreadOnly && conversation.unreadSupport <= 0) return false
      if (keyword && !`${conversation.nickname} ${conversation.username} ${conversation.accountNo}`.toLowerCase().includes(keyword)) return false
      return true
    })
  }, [conversations, scenario, search, unreadOnly])

  useEffect(() => {
    if (scenario === 'history-browsing' || !threadRef.current) return
    threadRef.current.scrollTop = threadRef.current.scrollHeight
  }, [selectedId, scenario])

  function selectConversation(id: number) {
    setSelectedId(id)
    setHistoryBrowsing(false)
    setComposerIssue('')
    setPendingNotice('')
    setNotice('')
  }

  function scrollLatest() {
    setHistoryBrowsing(false)
    window.requestAnimationFrame(() => {
      if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight
    })
  }

  function loadOlder() {
    if (historyLoading || !hasMore) return
    const previousHeight = threadRef.current?.scrollHeight ?? 0
    setMessages(current => ({ ...current, [selected.id]: [...selected.olderMessages, ...current[selected.id]] }))
    setHistoryLoaded(ids => [...ids, selected.id])
    setHistoryResolved(true)
    setHistoryBrowsing(true)
    window.requestAnimationFrame(() => {
      if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight - previousHeight
    })
  }

  function markVisibleRead() {
    if (readOnly || historyBrowsing || visibleUnreadIds.length === 0) return
    setUnreadMessageIds(ids => ids.filter(id => !visibleUnreadIds.includes(id)))
    setConversations(items => items.map(item => item.id === selected.id ? { ...item, unreadSupport: Math.max(0, item.unreadSupport - visibleUnreadIds.length) } : item))
    setNotice(`仅将当前已加载的 ${visibleUnreadIds.length} 条用户消息标为已读；未加载的历史消息不受影响。`)
  }

  function updateDraft(value: string) {
    setDrafts(current => ({ ...current, [selected.id]: value }))
    setComposerIssue('')
    setNotice('')
  }

  function chooseImage(file?: File) {
    if (!file) return
    const allowed = ['image/jpeg', 'image/png', 'image/webp']
    if (!allowed.includes(file.type)) {
      setComposerIssue('仅支持 JPG、PNG 或 WebP 图片，请重新选择。')
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setComposerIssue('图片不能超过 5 MB，请压缩后重新选择。')
      return
    }
    setAttachments(current => ({ ...current, [selected.id]: { name: file.name, url: URL.createObjectURL(file), size: file.size } }))
    setComposerIssue('')
  }

  function sendMessage() {
    const body = draft.trim()
    if (readOnly || sending || pending || (!body && !attachment) || draft.length > 1000) return
    const conversationId = selected.id
    const clientMessageId = `staff_${conversationId}_20261104_${String(nextClientId.current++).padStart(2, '0')}`
    setSendingIds(ids => [...ids, conversationId])
    setComposerIssue('')
    setNotice('')
    window.setTimeout(() => {
      const message: AdminChatMessage = {
        id: nextMessageId.current++,
        sender: 'staff',
        body,
        image: attachment?.url,
        createdAt: '刚刚',
        clientMessageId,
      }
      setMessages(current => ({ ...current, [conversationId]: [...current[conversationId], message] }))
      setConversations(items => items.map(item => item.id === conversationId ? { ...item, lastMessage: body || '[图片]', lastMessageAt: '刚刚' } : item))
      setDrafts(current => ({ ...current, [conversationId]: '' }))
      setAttachments(current => ({ ...current, [conversationId]: undefined }))
      setSendingIds(ids => ids.filter(id => id !== conversationId))
      setNotice('示例回复已加入当前会话，未发送给真实用户。')
      if (conversationId === selectedId) window.requestAnimationFrame(() => { if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight })
    }, 450)
  }

  function checkPending() {
    if (!pending?.saved) return
    if (scenario === 'pending-unknown') {
      setPendingNotice('暂未查到这条消息，结果仍未知。已保留原编号，可稍后再次核对，不会自动重发。')
      return
    }
    const message: AdminChatMessage = {
      id: nextMessageId.current++,
      sender: 'staff',
      body: pending.body,
      createdAt: '16:49',
      clientMessageId: pending.clientMessageId,
    }
    setMessages(current => current[selected.id].some(item => item.clientMessageId === pending.clientMessageId) ? current : ({ ...current, [selected.id]: [...current[selected.id], message] }))
    setConversations(items => items.map(item => item.id === selected.id ? { ...item, lastMessage: pending.body, lastMessageAt: '16:49' } : item))
    setPendingReplies(current => ({ ...current, [selected.id]: undefined }))
    setPendingNotice('')
    setNotice('已按原客户端编号查到发送结果，消息仅补录一次，没有重复发送。')
    scrollLatest()
  }

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-5 px-5 py-4 md:px-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">会话接待</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">共享会话池</Badge></div><p className="mt-1.5 text-sm text-blue-100/75">站内会话由客服团队共同接待；微信客服请前往企业微信工作台。</p></div>
        <dl className="flex items-center gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-xl font-semibold tabular-nums">{conversations.length}</dd><dt className="text-[11px] text-blue-100/65">示例会话</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-xl font-semibold tabular-nums">{totalUnread}</dd><dt className="text-[11px] text-blue-100/65">团队未读</dt></div><div className="hidden border-l border-white/15 pl-4 sm:block"><dd className="text-sm font-semibold leading-7">共同接待</dd><dt className="text-[11px] text-blue-100/65">无需领取分配</dt></div></dl>
      </div>
    </section>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>会话列表刷新失败，当前保留上次成功快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">现有草稿与消息仍可查看，恢复后再继续接待。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><CheckCheck className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}

    <section className="grid h-[calc(100vh-272px)] min-h-[620px] max-h-[820px] overflow-hidden rounded-xl border bg-card shadow-sm lg:grid-cols-[320px_minmax(0,1fr)]" aria-label="客服共享会话工作台">
      <aside className="flex min-h-0 flex-col border-r">
        <div className="shrink-0 space-y-3 border-b p-4">
          <div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">站内会话</h2><p className="mt-0.5 text-[11px] text-muted-foreground">按最后消息时间倒序</p></div><Badge variant="outline" className="gap-1"><span className="size-1.5 rounded-full bg-success" />团队在线</Badge></div>
          <div data-admin-filter className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input value={search} onChange={event => setSearch(event.target.value)} className="h-10 pl-9 pr-8" placeholder="账号、昵称或账号编号" aria-label="搜索会话" />{search && <Button variant="ghost" size="icon" className="absolute right-1 top-1/2 size-8 -translate-y-1/2" onClick={() => setSearch('')} aria-label="清除会话搜索"><X className="size-3.5" /></Button>}</div>
          <Button variant={unreadOnly ? 'secondary' : 'outline'} size="sm" className="h-9 w-full justify-start" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(value => !value)}><MessageCircleMore className="size-4" />仅看待回复{totalUnread > 0 && <Badge className="ml-auto px-1.5">{totalUnread}</Badge>}</Button>
        </div>

        <div className="phone-scrollbar min-h-0 flex-1 overflow-y-auto">
          {sessionLoading ? <div className="space-y-2 p-3" aria-label="会话列表加载中">{[0, 1, 2, 3, 4].map(item => <div key={item} className="flex gap-3 p-3"><Skeleton className="size-10 rounded-full" /><div className="flex-1 space-y-2"><Skeleton className="h-4 w-2/3" /><Skeleton className="h-3 w-full" /></div></div>)}</div> : sessionError ? <div className="flex h-full min-h-72 flex-col items-center justify-center p-6 text-center"><CircleAlert className="size-7 text-destructive" /><h3 className="mt-3 text-sm font-semibold">会话列表加载失败</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">没有展示推测会话，也不会自动标记已读。</p><Button variant="outline" size="sm" className="mt-4" onClick={() => onScenarioChange('default')}><RefreshCw className="size-4" />重新加载</Button></div> : filteredConversations.length > 0 ? <div className="p-2">{filteredConversations.map(conversation => <ConversationItem key={conversation.id} conversation={conversation} active={selected.id === conversation.id} hasDraft={Boolean(drafts[conversation.id]?.trim() || attachments[conversation.id])} onClick={() => selectConversation(conversation.id)} />)}</div> : <div className="flex h-full min-h-64 flex-col items-center justify-center p-6 text-center"><Inbox className="size-7 text-muted-foreground" /><h3 className="mt-3 text-sm font-semibold">没有匹配的会话</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">可搜索完整或部分账号信息，或清除未读筛选。</p><Button variant="outline" size="sm" className="mt-4" onClick={() => { setSearch(''); setUnreadOnly(false); if (scenario === 'empty') onScenarioChange('default') }}>查看全部会话</Button></div>}
        </div>
        <div className="shrink-0 border-t px-4 py-2.5 text-[11px] text-muted-foreground"><span>每页 20 个会话 · 共享未读状态</span><span className="float-right">第 1 / 1 页</span></div>
      </aside>

      <div className="flex min-w-0 min-h-0 flex-col bg-muted/15">
        <header className="flex min-h-[73px] shrink-0 flex-wrap items-center gap-3 border-b bg-card px-4 py-3">
          <Avatar className="size-10 border bg-primary/10"><AvatarFallback className="bg-primary/10 text-sm font-semibold text-primary">{selected.nickname.slice(0, 1)}</AvatarFallback></Avatar>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold">{selected.nickname}</h2><Badge variant="outline" className="font-mono text-[10px]">{selected.accountNo}</Badge></div><p className="mt-0.5 truncate text-[11px] text-muted-foreground">{selected.username} · 用户 ID {selected.userId} · 全体客服可见</p></div>
          {!readOnly && visibleUnreadIds.length > 0 && <Button variant="outline" size="sm" disabled={historyBrowsing || messageLoading || messageError} onClick={markVisibleRead} title={historyBrowsing ? '回到最新后才可标记当前可见消息' : '仅标记当前已加载的用户消息'}><CheckCheck className="size-4" />{historyBrowsing ? '浏览历史中' : `标为已读 ${visibleUnreadIds.length}`}</Button>}
          {readOnly && <Badge variant="outline" className="gap-1.5"><LockKeyhole className="size-3.5" />仅查看</Badge>}
        </header>

        {scenario === 'session-error' ? <div className="grid flex-1 place-items-center p-8 text-center"><div><MessagesSquare className="mx-auto size-8 text-muted-foreground" /><p className="mt-3 text-sm font-medium">等待恢复会话列表</p><p className="mt-1.5 text-xs text-muted-foreground">列表恢复前不读取消息，也不改变未读状态。</p></div></div> : <>
          <div className="relative flex min-h-0 flex-1 flex-col">
            <div ref={threadRef} className="phone-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={event => { const target = event.currentTarget; if (target.scrollHeight - target.scrollTop - target.clientHeight > 96) setHistoryBrowsing(true) }}>
              {messageLoading ? <div className="space-y-5 p-5" aria-label="会话消息加载中"><Skeleton className="h-20 w-3/5 rounded-2xl" /><Skeleton className="ml-auto h-16 w-1/2 rounded-2xl" /><Skeleton className="h-24 w-2/3 rounded-2xl" /></div> : messageError ? <div className="grid h-full place-items-center p-6 text-center"><div><CircleAlert className="mx-auto size-7 text-destructive" /><h3 className="mt-3 text-sm font-semibold">消息加载失败</h3><p className="mt-2 text-xs text-muted-foreground">会话列表和其他会话不受影响，可独立重试。</p><Button variant="outline" size="sm" className="mt-4" onClick={() => setMessageResolved(true)}><RefreshCw className="size-4" />重新加载消息</Button></div></div> : <>
                {hasMore && <div className="border-b bg-card/60 p-2 text-center"><Button variant="ghost" size="sm" disabled={historyLoading} onClick={loadOlder}>{historyLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}{historyLoading ? '正在读取更早消息' : historyError ? '重试加载更早消息' : '加载更早消息'}</Button>{historyError && <p role="alert" className="text-[11px] text-destructive">历史消息读取失败，当前消息与草稿不受影响。</p>}<p className="text-[10px] text-muted-foreground">游标分页 · 每页 50 条 · created_at + id 稳定排序</p></div>}
                <div className="space-y-5 p-4 md:p-5">
                  <div className="flex items-center gap-3"><span className="h-px flex-1 bg-border" /><span className="text-[10px] text-muted-foreground">今天 · 站内客服会话</span><span className="h-px flex-1 bg-border" /></div>
                  {selectedMessages.map(message => <ChatMessageBubble key={message.id} message={message} nickname={selected.nickname} canViewOrders={canViewOrders} onOpenOrder={onOpenOrder} onPreviewImage={setPreviewImage} />)}
                  {sending && <div className="flex items-center justify-end gap-2 text-[11px] text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />正在发送当前会话回复…</div>}
                </div>
              </>}
            </div>
            {historyBrowsing && !messageLoading && !messageError && <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center"><Button variant="secondary" size="sm" className="pointer-events-auto rounded-full shadow-lg" onClick={scrollLatest}><ArrowDown className="size-4" />回到最新{visibleUnreadIds.length > 0 ? ` · ${visibleUnreadIds.length} 条未读` : ''}</Button></div>}
          </div>

          <footer className="shrink-0 border-t bg-card p-3 md:px-4">
            {readOnly ? <Alert><LockKeyhole className="size-4" /><AlertTitle>当前账号只有 chat:view 权限</AlertTitle><AlertDescription>可查看会话和完整订单链接，但不能回复、上传图片或标记已读。</AlertDescription></Alert> : <>
              {pending && <div className="mb-3 rounded-lg border border-warning/30 bg-warning/5 p-3" role="alert"><div className="flex flex-wrap items-start gap-3"><Clock3 className="mt-0.5 size-4 shrink-0 text-warning" /><div className="min-w-0 flex-1"><p className="text-xs font-semibold">上条回复结果待核对</p><p className="mt-1 text-[11px] leading-5 text-muted-foreground">{pending.saved ? `已保存客户端编号 ${pending.clientMessageId}，先查询发送结果，不能盲目重发。` : '本地未保存客户端编号，无法可靠核对；请先查看当前消息记录，再决定是否新建回复。'}</p>{pendingNotice && <p className="mt-2 text-[11px] leading-5 text-warning-foreground">{pendingNotice}</p>}</div><div className="flex flex-wrap gap-2">{pending.saved ? <Button variant="outline" size="sm" onClick={checkPending}><ShieldCheck className="size-4" />核对发送结果</Button> : <Button variant="outline" size="sm" onClick={scrollLatest}>查看当前消息</Button>}<Button variant="ghost" size="sm" onClick={() => setAbandonOpen(true)}>放弃本地核对</Button></div></div></div>}
              {composerIssue && <Alert variant="destructive" className="mb-3 py-2"><CircleAlert className="size-4" /><AlertDescription className="text-xs">{composerIssue}</AlertDescription></Alert>}
              {attachment && <div className="mb-2 flex items-center gap-3 rounded-lg border bg-muted/30 p-2"><button type="button" onClick={() => setPreviewImage(attachment.url)} className="size-12 shrink-0 overflow-hidden rounded-md border"><img src={attachment.url} alt="待发送图片预览" className="size-full object-cover" /></button><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium">{attachment.name}</p><p className="mt-0.5 text-[10px] text-muted-foreground">{(attachment.size / 1024 / 1024).toFixed(2)} MB · 仅本地预览</p></div><Button variant="ghost" size="icon" className="size-8" disabled={sending || Boolean(pending)} onClick={() => setAttachments(current => ({ ...current, [selected.id]: undefined }))} aria-label="移除待发送图片"><X className="size-4" /></Button></div>}
              <div className="flex items-end gap-2">
                <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" aria-label="选择回复图片" onChange={event => { chooseImage(event.target.files?.[0]); event.target.value = '' }} />
                <Button variant="outline" size="icon" className="size-11 shrink-0" disabled={sending || Boolean(pending)} onClick={() => fileRef.current?.click()} aria-label="添加回复图片"><ImagePlus className="size-4" /></Button>
                <Textarea value={draft} onChange={event => updateDraft(event.target.value)} disabled={sending || Boolean(pending)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage() } }} className={cn('max-h-24 min-h-11 flex-1 resize-none bg-background px-3 py-3 text-xs', draft.length > 1000 && 'border-destructive')} placeholder={pending ? '请先核对上条回复结果' : `回复 ${selected.nickname}，不同会话独立保存草稿`} aria-label="客服回复内容" />
                <Button className="h-11 shrink-0 px-4" disabled={sending || Boolean(pending) || (!draft.trim() && !attachment) || draft.length > 1000} onClick={sendMessage}>{sending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}{sending ? '发送中' : scenario === 'send-error' && composerIssue ? '重试发送' : '发送'}</Button>
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-3 text-[10px] text-muted-foreground"><span>Enter 发送 · Shift + Enter 换行 · 图片最大 5 MB</span><span className={cn(draft.length > 1000 && 'font-medium text-destructive')}>{draft.length} / 1000</span></div>
            </>}
          </footer>
        </>}
      </div>
    </section>

    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted-foreground"><span className="flex items-center gap-1.5"><Info className="size-3.5" />已读仅覆盖当前已加载且可见的用户消息，每批最多 200 条</span><span>订单链接仅识别完整 ORD 编号</span><span>全部消息、图片与状态均为本地示例</span></div>

    <Dialog open={previewImage !== null} onOpenChange={open => !open && setPreviewImage(null)}><DialogContent className="sm:max-w-2xl"><DialogHeader><DialogTitle>聊天图片预览</DialogTitle><DialogDescription>本地示例图片，不上传或发送到真实会话。</DialogDescription></DialogHeader>{previewImage && <img src={previewImage} alt="聊天图片大图预览" className="max-h-[65vh] w-full rounded-lg border object-contain" />}</DialogContent></Dialog>
    <AlertDialog open={abandonOpen} onOpenChange={setAbandonOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃本地结果核对？</AlertDialogTitle><AlertDialogDescription>这条回复可能已经送达。放弃后应先查看消息记录，再决定是否撰写新回复；原型不会自动重发。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setPendingReplies(current => ({ ...current, [selected.id]: undefined })); setPendingNotice(''); setAbandonOpen(false); setNotice('已放弃本地示例核对，未执行任何真实发送。') }}>放弃核对</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}

function ConversationItem({ conversation, active, hasDraft, onClick }: { conversation: AdminChatConversation; active: boolean; hasDraft: boolean; onClick: () => void }) {
  return <button type="button" onClick={onClick} aria-current={active ? 'true' : undefined} aria-label={`打开与 ${conversation.nickname} 的会话`} className={cn('flex min-h-[82px] w-full items-start gap-3 rounded-lg border border-transparent p-3 text-left transition-colors hover:bg-accent/60', active && 'border-primary/20 bg-accent text-accent-foreground')}>
    <Avatar className="size-10 shrink-0 border bg-background"><AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">{conversation.nickname.slice(0, 1)}</AvatarFallback></Avatar>
    <span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="truncate text-xs font-semibold">{conversation.nickname}</span><time className="ml-auto shrink-0 text-[10px] font-normal text-muted-foreground">{conversation.lastMessageAt}</time></span><span className="mt-1 flex items-center gap-2"><span className="line-clamp-1 flex-1 text-[11px] leading-5 text-muted-foreground">{conversation.lastMessage}</span>{conversation.unreadSupport > 0 && <Badge className="size-5 shrink-0 justify-center rounded-full p-0 text-[10px]">{conversation.unreadSupport}</Badge>}</span><span className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground"><span>{conversation.username}</span>{hasDraft && <span className="flex items-center gap-1 text-primary"><FileImage className="size-3" />有草稿</span>}</span></span>
  </button>
}

function ChatMessageBubble({ message, nickname, canViewOrders, onOpenOrder, onPreviewImage }: { message: AdminChatMessage; nickname: string; canViewOrders: boolean; onOpenOrder: (orderNo: string) => void; onPreviewImage: (url: string) => void }) {
  const staff = message.sender === 'staff'
  return <article className={cn('flex gap-2.5', staff && 'flex-row-reverse')}>
    <Avatar className="size-8 shrink-0 border bg-card"><AvatarFallback className={cn('text-[11px] font-semibold', staff ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground')}>{staff ? <Headset className="size-4" /> : nickname.slice(0, 1)}</AvatarFallback></Avatar>
    <div className={cn('min-w-0 max-w-[78%]', staff && 'text-right')}>
      <div className={cn('mb-1 flex items-center gap-2 text-[10px] text-muted-foreground', staff && 'justify-end')}><span className="font-medium text-foreground">{staff ? '客服小岚' : nickname}</span><time>{message.createdAt}</time>{staff && <Check className="size-3 text-primary" aria-label="本地示例消息" />}</div>
      <div className={cn('inline-block whitespace-pre-wrap break-words rounded-xl border px-3 py-2 text-left text-xs leading-5 shadow-sm', staff ? 'rounded-tr-sm border-primary/20 bg-primary text-primary-foreground' : 'rounded-tl-sm bg-card')}>
        {message.image && <button type="button" onClick={() => onPreviewImage(message.image!)} className="mb-2 block max-w-72 overflow-hidden rounded-lg border bg-background text-left" aria-label="预览聊天图片"><img src={message.image} alt="用户在会话中上传的订单说明示例" className="max-h-48 w-full object-cover" /></button>}
        {message.body && <MessageText body={message.body} staff={staff} canViewOrders={canViewOrders} onOpenOrder={onOpenOrder} />}
      </div>
      {message.clientMessageId && <p className="mt-1 font-mono text-[9px] text-muted-foreground">{message.clientMessageId}</p>}
    </div>
  </article>
}

function MessageText({ body, staff, canViewOrders, onOpenOrder }: { body: string; staff: boolean; canViewOrders: boolean; onOpenOrder: (orderNo: string) => void }) {
  const parts: ReactNode[] = body.split(orderPattern).map((part, index) => {
    if (!exactOrderPattern.test(part)) return part
    if (!canViewOrders) return <span key={`${part}-${index}`} className="font-mono">{part}</span>
    return <button key={`${part}-${index}`} type="button" onClick={() => onOpenOrder(part.toUpperCase())} className={cn('inline-flex items-center gap-1 font-mono underline decoration-current/40 underline-offset-2', staff ? 'text-primary-foreground' : 'text-primary')} aria-label={`查看订单 ${part}`}>{part}<ExternalLink className="inline size-3" /></button>
  })
  return <>{parts}</>
}
