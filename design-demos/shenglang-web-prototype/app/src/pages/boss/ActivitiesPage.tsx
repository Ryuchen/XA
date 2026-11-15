import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  CalendarDays,
  ChevronRight,
  Expand,
  FileImage,
  History,
  ImageOff,
  LoaderCircle,
  RefreshCw,
  Sparkles,
  Trophy,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { clubActivities, type ClubActivity } from '@/pages/boss/activity-data'

export type ActivitiesScenario = 'default' | 'loading' | 'error' | 'empty' | 'more' | 'more-loading' | 'more-error'
export type ActivityDetailScenario = 'default' | 'loading' | 'error' | 'not-found' | 'empty'

interface SharedProps {
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onRetry: () => void
}

interface ActivitiesPageProps extends SharedProps {
  scenario: ActivitiesScenario
  onBack: () => void
  onOpen: (item: ClubActivity) => void
}

interface ActivityDetailPageProps extends SharedProps {
  scenario: ActivityDetailScenario
  item: ClubActivity
  onBack: () => void
}

const archiveItems: ClubActivity[] = Array.from({ length: 29 }, (_, index) => ({
  id: 204 + index,
  title: `声浪俱乐部活动回顾 · 第 ${29 - index} 期`,
  date: `2026-${index < 12 ? '09' : '08'}-${String(28 - (index % 12)).padStart(2, '0')}`,
  image: index % 2 === 0 ? './activity-weekend.svg' : './activity-welcome.svg',
  archived: true,
}))

function dockHandlers({ onHome, onCompanions, onOrders, onMine }: Pick<SharedProps, 'onHome' | 'onCompanions' | 'onOrders' | 'onMine'>) {
  return (key: string) => {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }
}

export function ActivitiesPage(props: ActivitiesPageProps) {
  const { scenario, onBack, onOpen, onRetry } = props
  const [moreLoaded, setMoreLoaded] = useState(false)
  const [moreFailed, setMoreFailed] = useState(false)
  const paged = scenario === 'more' || scenario === 'more-loading' || scenario === 'more-error'
  const items = scenario === 'empty' ? [] : paged ? [...clubActivities, ...archiveItems.slice(0, 27), ...(moreLoaded ? archiveItems.slice(27) : [])] : clubActivities
  const showMore = paged && !moreLoaded

  function loadMore() {
    if (scenario === 'more-error' && !moreFailed) {
      setMoreFailed(true)
      return
    }
    setMoreFailed(false)
    setMoreLoaded(true)
  }

  return <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />首页</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">公开活动 · 示例</Badge></div>
      <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪电竞 · 俱乐部记录</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">俱乐部活动</h1><p className="mt-1 text-xs text-muted-foreground">新活动在最前面，往期精彩也在这里</p></header>
      {scenario === 'loading' ? <ListSkeleton /> : scenario === 'error' ? <ErrorState title="俱乐部活动暂时没有加载成功" description="请检查网络后重试。加载失败不会改变活动、订单或账号数据。" onRetry={onRetry} onBack={onBack} /> : items.length === 0 ? <section className="px-4"><div className="rounded-3xl border border-dashed bg-card/60 p-7 text-center"><CalendarDays className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">暂无俱乐部活动</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">新活动发布后会显示在这里，请稍后刷新。</p></div></section> : <div className="space-y-4 px-4 pb-5">
        <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.38),rgba(2,6,23,0.7)_80%)] p-5" aria-label="活动概览"><div className="absolute -right-8 -top-10 size-32 rounded-full bg-sky-400/10 blur-2xl" /><div className="relative flex items-center gap-4"><div className="grid size-12 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200"><Trophy className="size-6" /></div><div><p className="text-[11px] text-sky-200">一起玩，也一起记得</p><h2 className="mt-1 text-lg font-semibold">每场相遇，都值得记录</h2><p className="mt-1 text-[10px] text-muted-foreground">近期活动与往期回顾</p></div></div></section>
        <section aria-labelledby="activity-list-title"><div className="mb-2 flex min-h-11 items-center justify-between px-1"><h2 id="activity-list-title" className="text-sm font-semibold">活动目录</h2><span className="text-[10px] text-muted-foreground">共 {items.length} 篇</span></div><div className="space-y-3">{items.map((item, index) => <button key={item.id} type="button" className="group flex min-h-[112px] w-full items-center gap-3 overflow-hidden rounded-2xl border bg-card p-3 text-left transition-colors hover:border-primary/35 focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onOpen(item)} aria-label={`${item.title}，发布于 ${item.date}${item.archived ? '，往期活动' : '，最近活动'}，查看活动海报`}><span className="relative h-[88px] w-[72px] shrink-0 overflow-hidden rounded-xl border border-primary/15 bg-primary/5"><img src={item.image} alt="" className="size-full object-cover" /></span><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2">{index === 0 && <Badge className="px-1.5 text-[9px]">最近发布</Badge>}{item.archived && <Badge variant="secondary" className="px-1.5 text-[9px]">往期活动</Badge>}</span><span className="mt-1.5 block break-words text-sm font-semibold leading-5">{item.title}</span><span className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground"><CalendarDays className="size-3" />{item.date}</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></button>)}</div></section>
        {moreFailed && <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/5 p-3 text-center"><p className="text-xs text-destructive">更多活动暂时没有加载成功，已显示的内容不受影响。</p></div>}
        {showMore ? <Button type="button" variant="outline" className="h-11 w-full" disabled={scenario === 'more-loading'} onClick={loadMore}>{scenario === 'more-loading' ? <LoaderCircle className="size-4 animate-spin" /> : moreFailed ? <RefreshCw className="size-4" /> : <History className="size-4" />}{scenario === 'more-loading' ? '正在加载更多' : moreFailed ? '重试加载更多活动' : '查看更多活动'}</Button> : <p className="text-center text-[10px] text-muted-foreground">已显示全部活动</p>}
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><Sparkles className="mt-0.5 size-4 shrink-0 text-primary" /><p>已结束活动保留海报记录。活动参与资格与奖励以活动发布时的规则及平台核验结果为准。</p></div>
      </div>}
    </main>
  </PhoneFrame>
}

export function ActivityDetailPage(props: ActivityDetailPageProps) {
  const { scenario, item, onBack, onRetry } = props
  const [previewOpen, setPreviewOpen] = useState(false)
  const posterAvailable = scenario !== 'empty' && Boolean(item.image)
  return <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="grid min-h-[60px] grid-cols-[52px_minmax(0,1fr)_52px] items-center px-3 py-2"><Button type="button" variant="ghost" size="icon" className="size-11 rounded-full" onClick={onBack} aria-label="返回活动目录"><ArrowLeft className="size-4" /></Button><h1 className="truncate text-center text-xs font-semibold">{scenario === 'loading' ? '俱乐部活动' : item.title}</h1><Badge variant="outline" className="justify-self-center border-primary/20 px-1.5 text-[9px] text-primary">示例</Badge></div>
      {scenario === 'loading' ? <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载活动海报"><Skeleton className="h-[460px] w-full rounded-3xl" /><Skeleton className="h-11 w-full rounded-xl" /></div> : scenario === 'error' || scenario === 'not-found' ? <ErrorState title={scenario === 'not-found' ? '活动不存在或暂不可查看' : '活动海报暂时没有加载成功'} description={scenario === 'not-found' ? '这条活动可能尚未发布或已下架，请返回目录查看最新活动与往期记录。' : '请检查网络后重试。加载失败不会改变活动或订单数据。'} onRetry={onRetry} back={scenario === 'not-found'} onBack={onBack} /> : <div className="px-4 pb-5">
        {posterAvailable ? <section className="overflow-hidden rounded-3xl border bg-card" aria-label={`${item.title}活动海报`}><button type="button" className="group relative block min-h-11 w-full text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => setPreviewOpen(true)} aria-label={`查看${item.title}活动海报大图`}><img src={item.image} alt={`${item.title}活动海报，点击查看大图`} className="block h-auto w-full" /><span className="absolute bottom-4 right-4 flex min-h-11 items-center gap-1.5 rounded-full border border-white/15 bg-slate-950/80 px-4 text-[11px] text-white backdrop-blur-md"><Expand className="size-4" />查看大图</span></button><p className="flex min-h-11 items-center justify-center gap-2 border-t px-4 text-[10px] text-muted-foreground"><FileImage className="size-3.5" />活动海报 · 点击可放大查看</p></section> : <section className="rounded-3xl border border-dashed bg-card/60 px-6 py-20 text-center"><ImageOff className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-5 text-xl font-semibold">活动海报准备中</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">请稍后刷新查看，或返回活动目录选择其他内容。</p><Button type="button" variant="outline" className="mt-7 h-11 w-full" onClick={onBack}>返回活动目录</Button></section>}
        <p className="mt-4 text-center text-[10px] leading-5 text-muted-foreground">海报为活动公开记录，不在本页创建订单或发放奖励</p>
      </div>}
    </main>
    {posterAvailable && scenario !== 'loading' && scenario !== 'error' && scenario !== 'not-found' && <Dialog open={previewOpen} onOpenChange={setPreviewOpen}><DialogContent className="prototype-dialog max-w-[340px] overflow-y-auto rounded-3xl p-3" showCloseButton={false}><DialogHeader className="sr-only"><DialogTitle>{item.title}活动海报大图</DialogTitle><DialogDescription>活动公开海报预览</DialogDescription></DialogHeader><img src={item.image} alt={`${item.title}活动海报大图`} className="h-auto max-h-[680px] w-full rounded-2xl object-contain" /><DialogClose asChild><Button type="button" variant="secondary" className="absolute right-5 top-5 size-11 rounded-full border border-white/15 bg-slate-950/85 text-xs text-white hover:bg-slate-900" aria-label="关闭活动海报大图">关闭</Button></DialogClose></DialogContent></Dialog>}
  </PhoneFrame>
}

function ErrorState({ title, description, onRetry, back = false, onBack }: { title: string; description: string; onRetry: () => void; back?: boolean; onBack: () => void }) {
  return <section className="px-4 pt-2"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 break-words text-xl font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><Button type="button" variant={back ? 'outline' : 'default'} className="mt-6 h-11 w-full" onClick={back ? onBack : onRetry}>{back ? <ArrowLeft className="size-4" /> : <RefreshCw className="size-4" />}{back ? '返回活动目录' : '重新加载'}</Button></div></section>
}

function ListSkeleton() {
  return <div className="space-y-4 px-4" aria-busy="true" aria-label="正在加载俱乐部活动"><Skeleton className="h-28 w-full rounded-3xl" /><div className="space-y-3">{Array.from({ length: 3 }, (_, index) => <div key={index} className="flex items-center gap-3 rounded-2xl border bg-card p-3"><Skeleton className="h-[88px] w-[72px] rounded-xl" /><div className="flex-1 space-y-3"><Skeleton className="h-4 w-20" /><Skeleton className="h-5 w-4/5" /><Skeleton className="h-3 w-24" /></div></div>)}</div></div>
}
