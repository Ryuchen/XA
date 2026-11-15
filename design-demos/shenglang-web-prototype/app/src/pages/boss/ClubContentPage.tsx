import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  BookOpenText,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Expand,
  FileCheck2,
  FileText,
  Headphones,
  RefreshCw,
  ShieldCheck,
  ShoppingBag,
  UsersRound,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { clubRuleItems, type ClubContentItem } from '@/pages/boss/club-content-data'

export type ClubContentScenario = 'default' | 'loading' | 'error' | 'empty' | 'long-title'
export type ClubContentDetailScenario = 'default' | 'loading' | 'error' | 'not-found' | 'empty' | 'long-content'

interface SharedProps {
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onRetry: () => void
}

interface ClubContentPageProps extends SharedProps {
  scenario: ClubContentScenario
  onBack: () => void
  onOpen: (item: ClubContentItem) => void
}

interface ClubContentDetailPageProps extends SharedProps {
  scenario: ClubContentDetailScenario
  item: ClubContentItem
  onBack: () => void
}

const kindLabels = {
  ORDER_RULES: '下单须知',
  PROVIDER_RULES: '陪玩保障',
  AFTERSALE_RULES: '售后标准',
}

const kindIcons = {
  ORDER_RULES: ShoppingBag,
  PROVIDER_RULES: UsersRound,
  AFTERSALE_RULES: Headphones,
}

function dockHandlers({ onHome, onCompanions, onOrders, onMine }: Pick<SharedProps, 'onHome' | 'onCompanions' | 'onOrders' | 'onMine'>) {
  return (key: string) => {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }
}

export function ClubContentPage(props: ClubContentPageProps) {
  const { scenario, onBack, onOpen, onRetry } = props
  const items = scenario === 'empty' ? [] : scenario === 'long-title' ? clubRuleItems.map((item, index) => index === 0 ? { ...item, title: '下单须知：服务规格、多人陪玩、预约时间与游戏资料完整确认说明' } : item) : clubRuleItems
  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">公开内容 · 示例</Badge></div>
      <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪电竞 · 服务约定</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">俱乐部规范</h1><p className="mt-1 text-xs text-muted-foreground">下单、服务与售后，都有清楚依据</p></header>
      {scenario === 'loading' ? <ListSkeleton /> : scenario === 'error' ? <ErrorState title="俱乐部内容暂时没有加载成功" description="请检查网络后重试。公开内容加载失败不会影响订单或账号数据。" onRetry={onRetry} /> : items.length === 0 ? <section className="px-4"><div className="rounded-3xl border border-dashed bg-card/60 p-7 text-center"><BookOpenText className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">暂无俱乐部内容</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">内容发布后会显示在这里，请稍后刷新。</p></div></section> : <div className="space-y-4 px-4 pb-5">
        <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.36),rgba(2,6,23,0.72)_78%)] p-5" aria-label="规范概览">
          <div className="absolute -right-8 -top-10 size-32 rounded-full bg-sky-400/10 blur-2xl" />
          <div className="relative flex items-center gap-4"><div className="grid size-12 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200"><ShieldCheck className="size-6" /></div><div><p className="text-[11px] text-sky-200">清楚约定，放心开黑</p><h2 className="mt-1 text-lg font-semibold">每一步，都有明确说明</h2><p className="mt-1 text-[10px] text-muted-foreground">三类公开规范 · 无需登录</p></div></div>
        </section>
        <section className="overflow-hidden rounded-2xl border bg-card" aria-labelledby="club-content-list-title"><div className="flex min-h-12 items-center justify-between border-b px-4"><h2 id="club-content-list-title" className="text-sm font-semibold">内容目录</h2><span className="text-[10px] text-muted-foreground">共 {items.length} 篇</span></div><div>{items.map(item => {
          const Icon = kindIcons[item.kind]
          return <button key={item.id} type="button" className="flex min-h-[92px] w-full items-center gap-3 border-t px-4 py-3 text-left transition-colors first:border-t-0 hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => onOpen(item)} aria-label={`${kindLabels[item.kind]}，${item.title}，查看完整说明`}><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Icon className="size-[18px]" /></span><span className="min-w-0 flex-1"><span className="block text-[10px] font-medium text-primary">{kindLabels[item.kind]}</span><span className="mt-1 block break-words text-xs font-semibold leading-5">{item.title}</span><span className="mt-1 block text-[10px] leading-4 text-muted-foreground">{item.summary}</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button>
        })}</div></section>
        <p className="text-center text-[10px] text-muted-foreground">已显示全部内容</p>
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><FileCheck2 className="mt-0.5 size-4 shrink-0 text-primary" /><p>规范用于说明服务流程。具体订单状态、金额与售后结果，请以对应业务页面的最新记录为准。</p></div>
      </div>}
    </main>
  </PhoneFrame>
}

export function ClubContentDetailPage(props: ClubContentDetailPageProps) {
  const { scenario, item, onBack, onRetry } = props
  const [previewOpen, setPreviewOpen] = useState(false)
  const empty = scenario === 'empty'
  const sections = scenario === 'long-content' ? [...item.sections, { heading: '补充说明：异常结果核对', paragraphs: ['页面反馈与预期不一致时，请先保留当前订单编号，并刷新对应业务页面。不要在结果未知时反复提交支付、取消或售后申请。', '如仍需协助，可通过客服与帮助进入平台会话，提供订单编号与具体问题，便于核对处理。'] }] : item.sections
  const hasBody = !empty && (Boolean(item.image) || sections.length > 0)
  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />规范</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">图文阅读 · 示例</Badge></div>
      {scenario === 'loading' ? <div className="space-y-3 px-4 pt-2" aria-busy="true" aria-label="正在加载俱乐部内容"><Skeleton className="h-40 w-full rounded-3xl" /><Skeleton className="h-56 w-full rounded-2xl" /><Skeleton className="h-80 w-full rounded-2xl" /></div> : scenario === 'error' || scenario === 'not-found' ? <ErrorState title={scenario === 'not-found' ? '内容不存在或暂不可查看' : '俱乐部内容暂时没有加载成功'} description={scenario === 'not-found' ? '这条内容可能尚未发布、已下架或已过期，请返回目录查看最新规范。' : '请检查网络后重试。加载失败不会改变订单或账号数据。'} onRetry={onRetry} back={scenario === 'not-found'} onBack={onBack} /> : <article className="px-4 pb-5" aria-labelledby="club-content-detail-title">
        <header className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.32),rgba(2,6,23,0.66)_78%)] p-5"><div className="relative flex items-center gap-2 text-[10px] text-sky-200"><BookOpenText className="size-3.5" /><span>俱乐部规范 · {kindLabels[item.kind]}</span></div><h1 id="club-content-detail-title" className="relative mt-4 break-words text-[24px] font-semibold leading-[1.4] tracking-[-0.035em]">{item.title}</h1><p className="relative mt-4 flex items-center gap-1.5 border-t border-white/10 pt-3 text-[10px] text-muted-foreground"><CalendarDays className="size-3.5" />发布于 {item.date}</p></header>
        {!empty && item.image && <div className="mt-4 overflow-hidden rounded-2xl border bg-card"><button type="button" className="group relative block min-h-11 w-full text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => setPreviewOpen(true)} aria-label="查看下单流程示例大图"><img src={item.image} alt="下单前确认服务规格、陪玩时间与游戏资料的示例流程图" className="block h-auto w-full" /><span className="absolute bottom-3 right-3 flex min-h-11 items-center gap-1.5 rounded-full border border-white/15 bg-slate-950/80 px-3 text-[10px] text-white backdrop-blur-md"><Expand className="size-3.5" />查看大图</span></button><p className="border-t px-4 py-3 text-[10px] leading-4 text-muted-foreground">示例配图 · 点击图片可放大查看</p></div>}
        {hasBody && !empty && sections.length > 0 && <section className="mt-4 rounded-2xl border bg-card p-5" aria-labelledby="club-content-body-title"><div className="flex items-center gap-2 border-b pb-4"><FileText className="size-4 text-primary" /><h2 id="club-content-body-title" className="text-sm font-semibold">完整说明</h2></div><div className="pt-1">{sections.map((section, index) => <section key={`${section.heading}-${index}`} className="pt-4">{section.heading && <h3 className="break-words text-[15px] font-semibold leading-6 text-foreground">{section.heading}</h3>}{section.paragraphs?.map(paragraph => <p key={paragraph} className="mt-3 break-words text-sm leading-7 text-foreground/85">{paragraph}</p>)}{section.bullets && <ul className="mt-3 space-y-2.5">{section.bullets.map(bullet => <li key={bullet} className="flex gap-2.5 text-[13px] leading-6 text-foreground/85"><CheckCircle2 className="mt-1 size-4 shrink-0 text-primary" /><span>{bullet}</span></li>)}</ul>}{section.quote && <blockquote className="mt-4 rounded-r-xl border-l-2 border-primary bg-primary/[0.06] px-4 py-3 text-[13px] leading-6 text-muted-foreground">{section.quote}</blockquote>}</section>)}</div></section>}
        {!hasBody && <section className="mt-4 rounded-3xl border border-dashed bg-card/60 p-8 text-center"><FileText className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">正文准备中</h2><p className="mt-2 text-xs leading-6 text-muted-foreground">请稍后刷新查看。</p></section>}
        <div className="mt-4 flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>本页为公开规范阅读原型。涉及具体订单或售后时，请前往对应业务页面处理。</p></div>
      </article>}
    </main>
    {!empty && item.image && <Dialog open={previewOpen} onOpenChange={setPreviewOpen}><DialogContent className="prototype-dialog max-w-[340px] rounded-3xl p-3" showCloseButton={false}><DialogHeader className="sr-only"><DialogTitle>下单流程示例大图</DialogTitle><DialogDescription>下单前确认服务规格、陪玩时间与游戏资料</DialogDescription></DialogHeader><img src={item.image} alt="下单流程示例大图" className="h-auto w-full rounded-2xl" /><DialogClose asChild><Button type="button" variant="secondary" className="absolute right-5 top-5 size-11 rounded-full border border-white/15 bg-slate-950/85 text-xs text-white hover:bg-slate-900" aria-label="关闭大图">关闭</Button></DialogClose></DialogContent></Dialog>}
  </PhoneFrame>
}

function ErrorState({ title, description, onRetry, back = false, onBack }: { title: string; description: string; onRetry: () => void; back?: boolean; onBack?: () => void }) {
  return <section className="px-4 pt-2"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 break-words text-xl font-semibold">{title}</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><Button type="button" variant={back ? 'outline' : 'default'} className="mt-6 h-11 w-full" onClick={back ? onBack : onRetry}>{back ? <ArrowLeft className="size-4" /> : <RefreshCw className="size-4" />}{back ? '返回内容目录' : '重新加载'}</Button></div></section>
}

function ListSkeleton() {
  return <div className="space-y-4 px-4" aria-busy="true" aria-label="正在加载俱乐部内容目录"><Skeleton className="h-28 w-full rounded-3xl" /><div className="rounded-2xl border bg-card p-4"><Skeleton className="h-5 w-28" /><div className="mt-4 space-y-4">{Array.from({ length: 3 }, (_, index) => <div key={index} className="flex items-center gap-3 border-t pt-4 first:border-0"><Skeleton className="size-10 rounded-xl" /><div className="flex-1 space-y-2"><Skeleton className="h-3 w-16" /><Skeleton className="h-4 w-4/5" /><Skeleton className="h-3 w-3/5" /></div></div>)}</div></div></div>
}
