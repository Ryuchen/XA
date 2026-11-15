import { useMemo, useState } from 'react'
import {
  AlertCircle,
  Bell,
  Cake,
  Check,
  ChevronRight,
  CircleHelp,
  Gift,
  Headphones,
  Heart,
  Inbox,
  Medal,
  MessageCircleMore,
  MessageSquareText,
  RefreshCw,
  Sparkles,
  Trophy,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { FilterBar } from '@/components/prototype/FilterBar'
import { HeaderSupportButton } from '@/components/prototype/HeaderSupportButton'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { initialNotices, type Notice, type NoticeType } from '@/pages/boss/message-data'

export type MessagesScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'more-error' | 'feed' | 'feed-empty' | 'feed-error'
type NoticeFilter = '' | NoticeType
type FeedKind = 'GIFT' | 'LOTTERY' | 'RECHARGE' | 'REVIEW' | 'NAMING' | 'UPGRADE' | 'BIRTHDAY'
type FeedFilter = '' | FeedKind

interface MessagesPageProps {
  scenario: MessagesScenario
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  readNoticeIds: number[]
  onMarkAllRead: () => void
  onOpenNotice: (item: Notice) => void
  onChat: () => void
  onSupport: () => void
  onAnnouncements: () => void
  onRetry: () => void
}

interface FeedItem { id: number; kind: FeedKind; content: string; time: string }
const noticeFilters: { value: NoticeFilter; label: string }[] = [{ value: '', label: '全部' }, { value: 'ORDER', label: '订单' }, { value: 'SYSTEM', label: '系统' }, { value: 'SUPPORT', label: '客服' }, { value: 'PROMOTION', label: '活动' }]
const feedFilters: { value: FeedFilter; label: string }[] = [{ value: '', label: '全部动态' }, { value: 'GIFT', label: '礼物' }, { value: 'LOTTERY', label: '转盘' }, { value: 'RECHARGE', label: '充值' }, { value: 'REVIEW', label: '好评' }, { value: 'NAMING', label: '冠名' }, { value: 'UPGRADE', label: 'VIP 升级' }, { value: 'BIRTHDAY', label: '生日' }]
const typeLabels: Record<NoticeType, string> = { ORDER: '订单', SYSTEM: '系统', SUPPORT: '客服', PROMOTION: '活动' }
const feedLabels: Record<FeedKind, string> = { GIFT: '礼物时刻', LOTTERY: '转盘中奖', RECHARGE: '充值喜报', REVIEW: '好评时刻', NAMING: '专属冠名', UPGRADE: 'VIP 升级', BIRTHDAY: '生日祝福' }
const feedIcons: Record<FeedKind, LucideIcon> = { GIFT: Gift, LOTTERY: Trophy, RECHARGE: Sparkles, REVIEW: Heart, NAMING: Medal, UPGRADE: Medal, BIRTHDAY: Cake }
const feedItems: FeedItem[] = [
  { id: 81, kind: 'GIFT', content: '海浪收藏家送给小澈 2 份星海守护者，新的心意已送达。', time: '今天 19:52' },
  { id: 79, kind: 'REVIEW', content: '北夜收到一条 5 分评价：沟通舒服，节奏配合很默契。', time: '今天 18:16' },
  { id: 77, kind: 'NAMING', content: '「海浪同行」专属冠名已续延 6 天。', time: '今天 16:40' },
  { id: 74, kind: 'UPGRADE', content: '恭喜海浪收藏家升级为星海 VIP 2，新的会员权益已解锁。', time: '昨天 21:08' },
  { id: 71, kind: 'RECHARGE', content: '一笔 200 浪花充值已完成核对并入账。', time: '昨天 16:20' },
  { id: 68, kind: 'LOTTERY', content: '幸运转盘开出一份服务优惠券，记得在有效期内使用。', time: '11 月 1 日 14:12' },
]

export function MessagesPage({ scenario, onLogin, onHome, onCompanions, onOrders, readNoticeIds, onMarkAllRead, onOpenNotice, onChat, onSupport, onAnnouncements, onRetry }: MessagesPageProps) {
  const [section, setSection] = useState<'private' | 'feed'>(scenario.startsWith('feed') ? 'feed' : 'private')
  const [filter, setFilter] = useState<NoticeFilter>('')
  const [feedKind, setFeedKind] = useState<FeedFilter>('')
  const [notices, setNotices] = useState(() => initialNotices.map(item => readNoticeIds.includes(item.id) ? { ...item, isRead: true } : item))
  const [marking, setMarking] = useState(false)
  const [readFeedback, setReadFeedback] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState(false)
  const [feedExpanded, setFeedExpanded] = useState(false)
  const [privacyOpen, setPrivacyOpen] = useState(false)
  const unread = notices.filter(item => !item.isRead).length
  const filteredNotices = useMemo(() => notices.filter(item => !filter || item.type === filter), [filter, notices])
  const visibleNotices = scenario === 'empty' ? [] : filter || expanded ? filteredNotices : filteredNotices.slice(0, 5)
  const filteredFeed = useMemo(() => feedItems.filter(item => !feedKind || item.kind === feedKind), [feedKind])
  const visibleFeed = scenario === 'feed-empty' ? [] : feedKind || feedExpanded ? filteredFeed : filteredFeed.slice(0, 5)
  const hasMore = !filter && !expanded && !['empty', 'guest', 'loading', 'error'].includes(scenario)
  const feedHasMore = !feedKind && !feedExpanded && !['feed-empty', 'feed-error'].includes(scenario)

  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') setPrivacyOpen(true)
  }

  function markAllRead() {
    if (!unread || marking) return
    setMarking(true)
    setReadFeedback(false)
    window.setTimeout(() => { setNotices(items => items.map(item => ({ ...item, isRead: true }))); onMarkAllRead(); setMarking(false); setReadFeedback(true) }, 450)
  }

  function openNotice(item: Notice) {
    setNotices(items => items.map(notice => notice.id === item.id ? { ...notice, isRead: true } : notice))
    onOpenNotice({ ...item, isRead: true })
  }

  function loadMore() {
    if (loadingMore) return
    if (scenario === 'more-error' && !moreError) { setMoreError(true); return }
    setLoadingMore(true)
    window.setTimeout(() => { setExpanded(true); setMoreError(false); setLoadingMore(false) }, 450)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="messages" onNavigate={handleDockNavigate} />}>
      <main className="pb-32 md:pb-12">
        <PageHeader
          title="消息中心"
          description="俱乐部此刻与你的服务进度。"
          action={<>{unread > 0 && scenario !== 'guest' && <Badge variant="outline" className="rounded-md border-primary/25 bg-primary/5 text-xs text-primary" aria-label={`共 ${unread} 条未读通知`}>{unread} 条未读</Badge>}<HeaderSupportButton /></>}
        >
          {scenario === 'feed' && <p className="mt-3 text-xs text-muted-foreground">动态定时更新 · 示例内容</p>}
        </PageHeader>

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6"><Card className="mx-auto max-w-2xl rounded-[12px] py-0 text-center shadow-sm"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><MessageCircleMore className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后查看消息</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后接收俱乐部动态、订单进度与专属通知。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button><div className="mt-6 grid grid-cols-2 gap-3 border-t pt-6"><Shortcut icon={Headphones} title="联系客服" note="咨询与帮助" onClick={onSupport} /><Shortcut icon={Bell} title="平台公告" note="官方发布" onClick={onAnnouncements} /></div></CardContent></Card></section>
        ) : (
          <div className="grid items-start gap-6 px-4 pb-6 md:grid-cols-[minmax(0,1fr)_300px] md:px-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            <section className="order-2 overflow-hidden rounded-[12px] border bg-card shadow-sm md:order-1" aria-label="消息内容">
              <div className="border-b p-4 md:p-5">
                <FilterBar ariaLabel="消息类型" items={[{ value: 'feed', label: '俱乐部动态' }, { value: 'private', label: '我的通知', count: unread }]} value={section} onChange={setSection} columns={2} />
                {section === 'private' ? <><div className="mt-4 flex min-h-11 items-center justify-between gap-3"><p className="text-xs text-muted-foreground">订单、账户与服务提醒</p><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-xs" disabled={!unread || marking} onClick={markAllRead}>{marking ? <RefreshCw className="size-4 animate-spin" /> : <Check className="size-4" />}{marking ? '正在标记' : '全部已读'}</Button></div><FilterBar ariaLabel="通知分类" items={noticeFilters} value={filter} onChange={value => { setFilter(value); setExpanded(false); setMoreError(false) }} columns={5} /></> : <FilterBar className="mt-4" ariaLabel="俱乐部动态分类" items={feedFilters} value={feedKind} onChange={value => { setFeedKind(value); setFeedExpanded(false) }} />}
              </div>

              {section === 'private' ? (
                <>
                  {readFeedback && <p role="status" className="border-b bg-success/5 px-4 py-3 text-xs text-success md:px-5">已将全部示例通知标记为已读</p>}
                  {scenario === 'loading' ? <div className="space-y-4 p-4 md:p-5" aria-busy="true" aria-label="正在加载消息">{[104, 104, 104, 104].map((height, index) => <Skeleton key={index} className="w-full rounded-[8px]" style={{ height }} />)}</div> : scenario === 'error' ? <ErrorState title="消息暂时没有加载成功" detail="请检查网络后重试，已有通知与未读状态不会受到影响。" onRetry={onRetry} /> : visibleNotices.length ? <div aria-label="通知列表">{visibleNotices.map(item => <NoticeRow key={item.id} item={item} onClick={() => openNotice(item)} />)}</div> : <EmptyState icon={Inbox} title="还没有这类通知" detail="新的订单、账户与服务提醒会在这里展示。" action={filter ? '查看全部通知' : undefined} onAction={() => setFilter('')} />}
                  {moreError && <p role="alert" className="border-t bg-destructive/5 p-3 text-center text-xs text-destructive">后续消息暂时没有加载成功，请重试。</p>}
                  {(hasMore || (!hasMore && visibleNotices.length > 0)) && <div className="border-t p-4 md:p-5">{hasMore ? <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={loadMore} disabled={loadingMore}>{loadingMore && <RefreshCw className="size-4 animate-spin" />}{loadingMore ? '正在加载' : moreError ? '加载失败，点击重试' : '加载更多'}</Button> : <p className="text-center text-xs text-muted-foreground">已展示当前分类下的全部示例通知</p>}</div>}
                </>
              ) : (
                <>
                  {scenario === 'feed-error' ? <ErrorState title="动态暂未更新" detail="请稍后重试，我的通知仍可正常查看。" onRetry={onRetry} /> : visibleFeed.length ? <div aria-label="俱乐部动态列表">{visibleFeed.map(item => <FeedRow key={item.id} item={item} />)}</div> : <EmptyState icon={Sparkles} title="新的精彩即将发生" detail="礼物、冠名、好评与会员动态会在这里展示。" />}
                  {(feedHasMore || visibleFeed.length > 0) && <div className="border-t p-4 md:p-5">{feedHasMore ? <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={() => setFeedExpanded(true)}>查看更多动态</Button> : <p className="text-center text-xs text-muted-foreground">已展示全部示例动态</p>}</div>}
                </>
              )}
            </section>

            <aside className="order-1 space-y-4 md:order-2" aria-label="消息快捷入口">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-1"><Shortcut icon={Headphones} title="联系客服" note="咨询与帮助" onClick={onSupport} /><Shortcut icon={MessageSquareText} title="平台会话" note="查看与回复" onClick={onChat} /></div>
              <button type="button" className="flex min-h-[76px] w-full items-center gap-3 rounded-[12px] border bg-card p-4 text-left shadow-sm transition-colors hover:bg-accent/40" onClick={onAnnouncements}><Bell className="size-5 shrink-0 text-primary" /><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">平台公告</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">官方发布 · 服务安排与重要通知</span></span><ChevronRight className="size-4 text-muted-foreground" /></button>
              <button type="button" className="flex min-h-11 w-full items-center justify-center gap-2 rounded-[8px] text-xs text-muted-foreground transition-colors hover:bg-accent" onClick={() => setPrivacyOpen(true)}><CircleHelp className="size-4" />消息与隐私说明<ChevronRight className="size-4" /></button>
            </aside>
          </div>
        )}
      </main>

      <Dialog open={privacyOpen} onOpenChange={setPrivacyOpen}><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><MessageCircleMore className="size-5" /></div><DialogTitle className="text-left text-xl">消息与隐私说明</DialogTitle><DialogDescription className="text-left text-sm leading-6">通知按订单、系统、客服与活动分类；俱乐部动态展示礼物、冠名、充值、好评等公开动态。原型仅操作本地示例状态，不读取真实消息。</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回消息中心</Button></DialogClose></DialogFooter></DialogContent></Dialog>
    </PhoneFrame>
  )
}

function Shortcut({ icon: Icon, title, note, onClick }: { icon: LucideIcon; title: string; note: string; onClick: () => void }) {
  return <button type="button" className="flex min-h-[76px] min-w-0 items-center gap-3 rounded-[12px] border bg-card p-4 text-left shadow-sm transition-colors hover:bg-accent/40" onClick={onClick}><Icon className="size-5 shrink-0 text-primary" /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{title}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{note}</span></span><ChevronRight className="hidden size-4 text-muted-foreground md:block" /></button>
}

function NoticeRow({ item, onClick }: { item: Notice; onClick: () => void }) {
  return <button type="button" className={cn('w-full border-b p-4 text-left transition-colors last:border-0 hover:bg-accent/40 md:p-5', !item.isRead && 'bg-primary/[0.035]')} onClick={onClick} aria-label={`${item.isRead ? '已读' : '未读'}，${typeLabels[item.type]}通知，${item.title}，${item.time}`}><span className="flex items-center gap-2"><Badge variant="secondary" className="rounded-md text-xs">{typeLabels[item.type]}</Badge>{!item.isRead && <span className="size-2 rounded-full bg-primary" aria-label="未读" />}<span className="ml-auto shrink-0 text-xs text-muted-foreground">{item.time}</span><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></span><span className={cn('mt-3 block text-sm leading-6', !item.isRead ? 'font-semibold' : 'font-medium text-muted-foreground')}>{item.title}</span><span className="mt-1 line-clamp-2 block text-sm leading-6 text-muted-foreground">{item.preview}</span></button>
}

function FeedRow({ item }: { item: FeedItem }) {
  const Icon = feedIcons[item.kind]
  return <article className="flex gap-3 border-b p-4 last:border-0 md:p-5"><div className="grid size-10 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary"><Icon className="size-4" /></div><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">{feedLabels[item.kind]}</h2><time className="shrink-0 text-xs text-muted-foreground">{item.time}</time></div><p className="mt-2 text-sm leading-6 text-muted-foreground">{item.content}</p></div></article>
}

function ErrorState({ title, detail, onRetry }: { title: string; detail: string; onRetry: () => void }) {
  return <div role="alert" className="p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-base font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{detail}</p><Button type="button" className="mt-6 h-11 rounded-[8px]" onClick={onRetry}>重新加载</Button></div>
}

function EmptyState({ icon: Icon, title, detail, action, onAction }: { icon: LucideIcon; title: string; detail: string; action?: string; onAction?: () => void }) {
  return <div className="px-5 py-10 text-center"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/60 text-muted-foreground"><Icon className="size-6" /></div><h2 className="mt-4 text-base font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{detail}</p>{action && <Button type="button" variant="outline" className="mt-4 h-11 rounded-[8px]" onClick={onAction}>{action}</Button>}</div>
}
