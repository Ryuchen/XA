import {
  AlertCircle,
  ArrowLeft,
  BookOpenText,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Crown,
  FileText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trophy,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { getGuideItems, type GuideContentItem, type GuideKind } from '@/pages/boss/guide-content-data'

export type GuideContentScenario = 'default' | 'loading' | 'error' | 'empty'
export type GuideContentDetailScenario = 'default' | 'loading' | 'error' | 'not-found' | 'empty'

interface SharedProps {
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onRetry: () => void
}

interface GuideContentPageProps extends SharedProps {
  kind: GuideKind
  scenario: GuideContentScenario
  backLabel: string
  onBack: () => void
  onOpen: (item: GuideContentItem) => void
}

interface GuideContentDetailPageProps extends SharedProps {
  kind: GuideKind
  scenario: GuideContentDetailScenario
  item: GuideContentItem
  onBack: () => void
}

const guideMeta = {
  RIGHTS: {
    title: '老板权益',
    subtitle: '每一份支持，都有专属回应',
    kicker: '声浪俱乐部 · 会员礼遇',
    heroTitle: '把偏爱，写成清楚的权益',
    heroCopy: '会员优惠、生日心意与俱乐部身份',
    icon: Crown,
    accentIcon: Sparkles,
    countLabel: '项权益说明',
  },
  POINT_RULES: {
    title: '积分规则',
    subtitle: '积累热爱，也清楚每一次兑换',
    kicker: '声浪俱乐部 · 成长计划',
    heroTitle: '每一分，都有来处与去向',
    heroCopy: '积分获取、社区互动与奖品交付',
    icon: Trophy,
    accentIcon: CircleDollarSign,
    countLabel: '篇规则说明',
  },
} as const

function dockHandlers({ onHome, onCompanions, onOrders, onMine }: Pick<SharedProps, 'onHome' | 'onCompanions' | 'onOrders' | 'onMine'>) {
  return (key: string) => {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }
}

export function GuideContentPage(props: GuideContentPageProps) {
  const { kind, scenario, backLabel, onBack, onOpen, onRetry } = props
  const meta = guideMeta[kind]
  const HeroIcon = meta.icon
  const AccentIcon = meta.accentIcon
  const items = scenario === 'empty' ? [] : getGuideItems(kind)

  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2">
        <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />{backLabel}</Button>
        <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">公开内容 · 示例</Badge>
      </div>
      <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">{meta.kicker}</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">{meta.title}</h1><p className="mt-1 text-xs text-muted-foreground">{meta.subtitle}</p></header>

      {scenario === 'loading' ? <GuideListSkeleton title={meta.title} /> : scenario === 'error' ? <GuideError title={`${meta.title}暂时没有加载成功`} description="请检查网络后重试。公开说明加载失败不会改变会员、积分或订单数据。" onRetry={onRetry} /> : items.length === 0 ? <section className="px-4"><div className="rounded-3xl border border-dashed bg-card/60 p-7 text-center"><BookOpenText className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">暂无{meta.title}内容</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">内容发布后会显示在这里，请稍后刷新。</p></div></section> : <div className="space-y-4 px-4 pb-5">
        <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.42),rgba(2,6,23,0.82)_76%)] p-5" aria-label={`${meta.title}概览`}>
          <div className="pointer-events-none absolute -right-10 -top-14 size-40 rounded-full border border-sky-300/10" /><div className="pointer-events-none absolute -right-3 -top-7 size-28 rounded-full border border-sky-300/10" /><AccentIcon className="pointer-events-none absolute -right-3 bottom-0 size-24 text-sky-200/[0.055]" strokeWidth={1} />
          <div className="relative flex items-center gap-4"><div className="grid size-12 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200"><HeroIcon className="size-6" /></div><div><p className="text-[10px] font-medium text-sky-200/70">{kind === 'RIGHTS' ? 'CLUB PRIVILEGES' : 'POINTS & REWARDS'}</p><h2 className="mt-1 text-lg font-semibold leading-6">{meta.heroTitle}</h2></div></div>
          <p className="relative mt-4 border-t border-white/10 pt-3 text-[11px] leading-5 text-sky-100/60">{meta.heroCopy} · 无需登录</p>
        </section>

        <section className="overflow-hidden rounded-2xl border bg-card" aria-labelledby="guide-content-list-title"><div className="flex min-h-12 items-center justify-between border-b px-4"><h2 id="guide-content-list-title" className="text-sm font-semibold">内容目录</h2><span className="text-[10px] text-muted-foreground">{items.length} {meta.countLabel}</span></div><div>{items.map((item, index) => <button key={item.id} type="button" className="group flex min-h-[108px] w-full items-center gap-3 border-t px-4 py-4 text-left transition-colors first:border-t-0 hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={() => onOpen(item)} aria-label={`${item.title}，查看完整说明`}><span className="relative grid size-11 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><HeroIcon className="size-5" /><span className="absolute -right-1 -top-1 grid size-5 place-items-center rounded-full border bg-card text-[9px] font-semibold tabular-nums text-muted-foreground">{String(index + 1).padStart(2, '0')}</span></span><span className="min-w-0 flex-1"><span className="block text-[10px] font-medium text-primary">{item.eyebrow}</span><span className="mt-1 block break-words text-[13px] font-semibold leading-5">{item.title}</span><span className="mt-1 block text-[10px] leading-4 text-muted-foreground">{item.summary}</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></button>)}</div></section>
        <p className="text-center text-[10px] text-muted-foreground">已显示全部内容</p>
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>{kind === 'RIGHTS' ? '具体会员等级、卡券状态与优惠结果，请以会员中心、我的券和订单结算页面的最新记录为准。' : '具体积分数量、兑换结果与交付状态，请以积分明细、权益记录和钱包流水的最新记录为准。'}</p></div>
      </div>}
    </main>
  </PhoneFrame>
}

export function GuideContentDetailPage(props: GuideContentDetailPageProps) {
  const { kind, scenario, item, onBack, onRetry } = props
  const meta = guideMeta[kind]
  const HeroIcon = meta.icon
  const sections = scenario === 'empty' ? [] : item.sections
  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dockHandlers(props)} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />{meta.title}</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">完整阅读 · 示例</Badge></div>
      {scenario === 'loading' ? <div className="space-y-3 px-4 pt-2" aria-busy="true" aria-label={`正在加载${meta.title}详情`}><Skeleton className="h-48 w-full rounded-3xl" /><Skeleton className="h-64 w-full rounded-2xl" /><Skeleton className="h-72 w-full rounded-2xl" /></div> : scenario === 'error' || scenario === 'not-found' ? <GuideError title={scenario === 'not-found' ? '内容不存在或暂不可查看' : `${meta.title}暂时没有加载成功`} description={scenario === 'not-found' ? '这条内容可能尚未发布、已停用或已过期，请返回目录查看最新说明。' : '请检查网络后重试。加载失败不会改变会员、积分或订单数据。'} onRetry={onRetry} back={scenario === 'not-found'} onBack={onBack} /> : <article className="px-4 pb-5" aria-labelledby="guide-content-detail-title">
        <header className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.4),rgba(2,6,23,0.78)_78%)] p-5"><HeroIcon className="pointer-events-none absolute -right-5 -top-3 size-28 text-sky-200/[0.055]" strokeWidth={1} /><div className="relative flex items-center gap-2 text-[10px] text-sky-200"><HeroIcon className="size-3.5" /><span>{meta.title} · {item.eyebrow}</span></div><h1 id="guide-content-detail-title" className="relative mt-4 break-words text-[24px] font-semibold leading-[1.4] tracking-[-0.035em]">{item.title}</h1><p className="relative mt-4 flex items-center gap-1.5 border-t border-white/10 pt-3 text-[10px] text-muted-foreground"><CalendarDays className="size-3.5" />发布于 {item.date}</p></header>
        {sections.length > 0 ? <section className="mt-4 rounded-2xl border bg-card p-5" aria-labelledby="guide-content-body-title"><div className="flex items-center gap-2 border-b pb-4"><FileText className="size-4 text-primary" /><h2 id="guide-content-body-title" className="text-sm font-semibold">完整说明</h2></div><div className="pt-1">{sections.map((section, index) => <section key={`${section.heading}-${index}`} className="pt-4">{section.heading && <h3 className="break-words text-[15px] font-semibold leading-6">{section.heading}</h3>}{section.paragraphs?.map(paragraph => <p key={paragraph} className="mt-3 break-words text-sm leading-7 text-foreground/85">{paragraph}</p>)}{section.bullets && <ul className="mt-3 space-y-2.5">{section.bullets.map(bullet => <li key={bullet} className="flex gap-2.5 text-[13px] leading-6 text-foreground/85"><CheckCircle2 className="mt-1 size-4 shrink-0 text-primary" /><span>{bullet}</span></li>)}</ul>}{section.quote && <blockquote className="mt-4 rounded-r-xl border-l-2 border-primary bg-primary/[0.06] px-4 py-3 text-[13px] leading-6 text-muted-foreground">{section.quote}</blockquote>}</section>)}</div></section> : <section className="mt-4 rounded-3xl border border-dashed bg-card/60 p-8 text-center"><FileText className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">正文准备中</h2><p className="mt-2 text-xs leading-6 text-muted-foreground">请稍后刷新查看。</p></section>}
        <div className="mt-4 flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>本页为公开说明阅读原型，不执行会员变更、积分兑换或订单操作。</p></div>
      </article>}
    </main>
  </PhoneFrame>
}

function GuideError({ title, description, onRetry, back = false, onBack }: { title: string; description: string; onRetry: () => void; back?: boolean; onBack?: () => void }) {
  return <section className="px-4 pt-2"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 break-words text-xl font-semibold">{title}</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><Button type="button" variant={back ? 'outline' : 'default'} className="mt-6 h-11 w-full" onClick={back ? onBack : onRetry}>{back ? <ArrowLeft className="size-4" /> : <RefreshCw className="size-4" />}{back ? '返回内容目录' : '重新加载'}</Button></div></section>
}

function GuideListSkeleton({ title }: { title: string }) {
  return <div className="space-y-4 px-4" aria-busy="true" aria-label={`正在加载${title}目录`}><Skeleton className="h-36 w-full rounded-3xl" /><div className="rounded-2xl border bg-card p-4"><Skeleton className="h-5 w-28" /><div className="mt-4 space-y-4">{Array.from({ length: 3 }, (_, index) => <div key={index} className="flex items-center gap-3 border-t pt-4 first:border-0"><Skeleton className="size-11 rounded-xl" /><div className="flex-1 space-y-2"><Skeleton className="h-3 w-16" /><Skeleton className="h-4 w-4/5" /><Skeleton className="h-3 w-3/5" /></div></div>)}</div></div></div>
}
