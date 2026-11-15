import {
  AlertCircle,
  ArrowLeft,
  Bell,
  CalendarDays,
  ChevronRight,
  FileText,
  LockKeyhole,
  LogIn,
  Megaphone,
  Pin,
  RefreshCw,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { bossAnnouncements, type Announcement } from '@/pages/boss/announcement-data'

export type AnnouncementsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'limit' | 'long-title'
export type AnnouncementDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'invalid' | 'not-found' | 'empty-content' | 'long-content'

interface SharedProps {
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onRetry: () => void
}

interface AnnouncementsPageProps extends SharedProps {
  scenario: AnnouncementsScenario
  onBack: () => void
  onOpen: (item: Announcement) => void
}

interface AnnouncementDetailPageProps extends SharedProps {
  scenario: AnnouncementDetailScenario
  item: Announcement
  onBack: () => void
}

function dockHandlers({ onHome, onCompanions, onOrders, onMine }: Pick<SharedProps, 'onHome' | 'onCompanions' | 'onOrders' | 'onMine'>) {
  return (key: string) => {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }
}

export function AnnouncementsPage(props: AnnouncementsPageProps) {
  const { scenario, onLogin, onBack, onOpen, onRetry } = props
  const items = scenario === 'limit' ? [...bossAnnouncements, ...Array.from({ length: 16 }, (_, index) => ({ id: index + 5, title: `俱乐部活动回顾 · 第 ${index + 5} 期`, content: '活动回顾与俱乐部记录。', isPinned: false, createdAt: `2026-10-${String(19 - Math.floor(index / 2)).padStart(2, '0')}` }))] : scenario === 'long-title' ? bossAnnouncements.map((item, index) => index === 0 ? { ...item, title: '11 月俱乐部活动、服务安排、订单售后与钱包充值核对须知完整说明' } : item) : bossAnnouncements
  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">官方公告 · 示例</Badge></div>
      <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪电竞 · 信息发布</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">平台公告</h1><p className="mt-1 text-xs text-muted-foreground">服务调整、活动与使用说明</p></header>
      {scenario === 'guest' ? <GuestState title="登录查看平台公告" description="同步俱乐部服务调整、近期活动与重要使用说明。" onLogin={onLogin} /> : scenario === 'loading' ? <ListSkeleton /> : scenario === 'error' ? <ErrorState title="公告暂时没有加载成功" description="请检查网络后重试。加载失败不会改变公告或账号数据。" onRetry={onRetry} /> : scenario === 'empty' ? <section className="px-4"><div className="rounded-3xl border border-dashed bg-card/60 p-7 text-center"><Bell className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">暂无平台公告</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">新的服务通知与俱乐部活动会在这里发布。</p></div></section> : <div className="space-y-4 px-4 pb-5">
        <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.34),rgba(2,6,23,0.7)_75%)] p-5" aria-label="公告概览"><div className="relative flex items-center gap-4"><div className="grid size-12 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200"><Megaphone className="size-6" /></div><div><p className="text-[11px] text-sky-200">声浪官方信息</p><h2 className="mt-1 text-lg font-semibold">重要变化，清楚送达</h2><p className="mt-1 text-[10px] text-muted-foreground">置顶公告优先展示</p></div></div></section>
        <section className="overflow-hidden rounded-2xl border bg-card" aria-labelledby="announcement-list-title"><div className="flex min-h-12 items-center justify-between border-b px-4"><h2 id="announcement-list-title" className="text-sm font-semibold">全部公告</h2><span className="text-[10px] text-muted-foreground">{items.length} 条</span></div><div>{items.map(item => <button key={item.id} type="button" className="flex min-h-[84px] w-full items-center gap-3 border-t px-4 py-3 text-left transition-colors first:border-t-0 hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => onOpen(item)} aria-label={`${item.isPinned ? '置顶公告，' : '公告，'}${item.title}，发布于 ${item.createdAt}`}><span className={`grid size-9 shrink-0 place-items-center rounded-xl ${item.isPinned ? 'bg-primary/15 text-primary' : 'bg-muted/60 text-muted-foreground'}`}>{item.isPinned ? <Pin className="size-4" /> : <FileText className="size-4" />}</span><span className="min-w-0 flex-1"><span className="flex items-start gap-2">{item.isPinned && <Badge className="mt-0.5 shrink-0 px-1.5 text-[9px]">置顶</Badge>}<span className="min-w-0 break-words text-xs font-semibold leading-5">{item.title}</span></span><span className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground"><CalendarDays className="size-3" />发布于 {item.createdAt}</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button>)}</div></section>
        {scenario === 'limit' && <p className="text-center text-[10px] text-muted-foreground">当前展示 20 条公告</p>}
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-primary" /><p>公告由声浪电竞官方发布。涉及订单、钱包或售后时，请以对应业务页面的最新状态为准。</p></div>
      </div>}
    </main>
  </PhoneFrame>
}

export function AnnouncementDetailPage(props: AnnouncementDetailPageProps) {
  const { scenario, item, onLogin, onBack, onRetry } = props
  const failed = scenario === 'error' || scenario === 'invalid' || scenario === 'not-found'
  const errorTitle = scenario === 'invalid' ? '公告链接无效' : scenario === 'not-found' ? '公告不存在或已下线' : '公告暂时没有加载成功'
  const errorDescription = scenario === 'invalid' ? '请返回公告列表重新选择，不要继续使用无效链接。' : scenario === 'not-found' ? '这条公告可能已被停用，请返回列表查看最新官方信息。' : '请检查网络后重试。加载失败不会改变公告或账号数据。'
  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />返回</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">公告详情 · 示例</Badge></div>
      {scenario === 'guest' ? <div className="pt-4"><GuestState title="登录阅读公告详情" description="查看官方发布的完整活动内容与使用说明。" onLogin={onLogin} /></div> : scenario === 'loading' ? <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载公告详情"><Skeleton className="h-32 w-full rounded-3xl" /><Skeleton className="h-80 w-full rounded-2xl" /></div> : failed ? <div className="px-4 pt-2"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">{errorTitle}</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">{errorDescription}</p>{scenario === 'error' ? <Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button> : <Button type="button" variant="outline" className="mt-6 h-11 w-full" onClick={onBack}>返回公告列表</Button>}</div></div> : <article className="px-4 pb-5" aria-labelledby="announcement-detail-title">
        <header className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.3),rgba(2,6,23,0.64)_78%)] p-5"><div className="relative flex items-center gap-2 text-[10px] text-sky-200"><Megaphone className="size-3.5" /><span>声浪电竞 · 官方公告</span>{item.isPinned && <Badge variant="outline" className="border-sky-200/20 text-[9px] text-sky-200"><Pin className="size-3" />置顶</Badge>}</div><h1 id="announcement-detail-title" className="relative mt-4 break-words text-[25px] font-semibold leading-[1.35] tracking-[-0.035em]">{item.title}</h1><p className="relative mt-4 flex items-center gap-1.5 border-t border-white/10 pt-3 text-[10px] text-muted-foreground"><CalendarDays className="size-3.5" />发布时间 {item.createdAt}</p></header>
        <section className="mt-4 rounded-2xl border bg-card p-5" aria-labelledby="announcement-content-title"><div className="flex items-center gap-2 border-b pb-4"><FileText className="size-4 text-primary" /><h2 id="announcement-content-title" className="text-sm font-semibold">公告内容</h2></div>{scenario === 'empty-content' ? <div className="py-10 text-center"><FileText className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-xs text-muted-foreground">暂无公告正文</p></div> : <div className="space-y-4 pt-4 text-sm leading-7 text-foreground/90">{item.content.split('\n').filter(Boolean).map((paragraph, index) => <p key={index} className="break-words">{paragraph}</p>)}{scenario === 'long-content' && <><p>下单前请再次确认服务规格、数量和服务时间。如需指定陪玩，请在下单流程中核对陪玩信息；选择平台安排时，以订单最终接单记录为准。</p><p>支付结果未知时，请先核对钱包和订单状态。退款、售后及净消费统计以平台实际处理结果为准，相关金额会扣除已退款部分。</p><p>如对公告内容有疑问，可通过客服与帮助进入平台会话，并提供相关订单编号，便于快速核对。</p></>}</div>}</section>
        <div className="mt-4 flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-primary" /><p>本页为公告阅读原型，正文中的业务操作请前往对应页面完成。</p></div>
      </article>}
    </main>
  </PhoneFrame>
}

function GuestState({ title, description, onLogin }: { title: string; description: string; onLogin: () => void }) {
  return <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Bell className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
}

function ErrorState({ title, description, onRetry }: { title: string; description: string; onRetry: () => void }) {
  return <section className="px-4"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
}

function ListSkeleton() {
  return <div className="space-y-4 px-4" aria-busy="true" aria-label="正在加载平台公告"><Skeleton className="h-28 w-full rounded-3xl" /><div className="rounded-2xl border bg-card p-4"><Skeleton className="h-5 w-28" /><div className="mt-4 space-y-4">{Array.from({ length: 4 }, (_, index) => <div key={index} className="flex items-center gap-3 border-t pt-4 first:border-0"><Skeleton className="size-9 rounded-xl" /><div className="flex-1 space-y-2"><Skeleton className="h-4 w-4/5" /><Skeleton className="h-3 w-28" /></div></div>)}</div></div></div>
}
