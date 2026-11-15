import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BellRing,
  Check,
  Clock3,
  FileText,
  LogIn,
  MessageSquareText,
  RefreshCw,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { Notice } from '@/pages/boss/message-data'

export type MessageDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'invalid' | 'empty-content' | 'wallet' | 'claims'

interface MessageDetailPageProps {
  scenario: MessageDetailScenario
  item: Notice
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onOrder: () => void
  onWallet: () => void
  onClaims: () => void
  onRetry: () => void
}

const typeLabels = { ORDER: '订单通知', SYSTEM: '系统通知', SUPPORT: '客服通知', PROMOTION: '活动通知' } as const

export function MessageDetailPage({ scenario, item, onLogin, onBack, onHome, onCompanions, onOrders, onMine, onOrder, onWallet, onClaims, onRetry }: MessageDetailPageProps) {
  const target = scenario === 'wallet' ? 'wallet' : scenario === 'claims' ? 'claims' : item.orderId ? 'order' : item.target
  const actionLabel = target === 'order' ? '查看关联订单' : target === 'wallet' ? '查看钱包' : target === 'claims' ? '查看权益记录' : '查看相关内容'
  const action = target === 'order' ? onOrder : target === 'wallet' ? onWallet : onClaims
  const empty = scenario === 'empty-content'

  function dock(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'messages') onBack()
    else if (key === 'mine') onMine()
  }

  return <PhoneFrame bottomNavigation={<BottomDock active="messages" onNavigate={dock} />}>
    <main className="pb-28">
      <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />消息中心</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">通知详情 · 示例</Badge></div>

      {scenario === 'guest' ? <section className="px-4 pt-3"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><MessageSquareText className="size-7" /></div><h1 className="mt-4 text-xl font-semibold">查看消息详情</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后读取当前账号的通知内容，未读状态与关联记录仅本人可见。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section> : scenario === 'loading' ? <div className="space-y-4 px-4 pt-2" aria-busy="true" aria-label="正在加载消息详情"><Skeleton className="h-[360px] w-full rounded-3xl" /><Skeleton className="h-32 w-full rounded-2xl" /></div> : scenario === 'error' || scenario === 'invalid' ? <section className="px-4 pt-3"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">{scenario === 'invalid' ? '消息链接无效' : '消息暂时没有加载成功'}</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">{scenario === 'invalid' ? '链接参数不完整或消息不存在，请返回消息中心查看最新通知。' : '请检查网络后重试。加载失败不会改变通知的已读状态。'}</p><Button type="button" variant={scenario === 'invalid' ? 'outline' : 'default'} className="mt-6 h-11 w-full" onClick={scenario === 'invalid' ? onBack : onRetry}>{scenario === 'invalid' ? <ArrowLeft className="size-4" /> : <RefreshCw className="size-4" />}{scenario === 'invalid' ? '返回消息中心' : '重新加载'}</Button></div></section> : <div className="space-y-4 px-4 pb-5">
        <article className="overflow-hidden rounded-3xl border bg-card" aria-labelledby="message-detail-title">
          <header className="relative border-b bg-[linear-gradient(145deg,rgba(30,58,138,0.24),rgba(2,6,23,0.18))] p-5"><BellRing className="pointer-events-none absolute -right-4 -top-4 size-24 text-primary/[0.055]" strokeWidth={1} /><div className="relative flex items-center justify-between gap-3"><Badge className="border border-primary/20 bg-primary/10 text-[10px] text-primary hover:bg-primary/10">{typeLabels[item.type]}</Badge><span className="flex items-center gap-1 text-[10px] text-success"><Check className="size-3.5" />已读</span></div><h1 id="message-detail-title" className="relative mt-5 break-words text-[25px] font-semibold leading-[1.42] tracking-[-0.035em]">{item.title}</h1><p className="relative mt-4 flex items-center gap-1.5 text-[10px] text-muted-foreground"><Clock3 className="size-3.5" />发送时间 · {item.time}</p></header>
          <section className="p-5" aria-labelledby="message-content-title"><div className="flex items-center gap-2"><FileText className="size-4 text-primary" /><h2 id="message-content-title" className="text-xs font-semibold">通知内容</h2></div>{empty || (!item.detail && !item.preview) ? <p className="mt-5 rounded-xl border border-dashed p-5 text-center text-xs text-muted-foreground">暂无通知正文</p> : <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-7 text-foreground/85">{item.detail || item.preview}</p>}</section>
        </article>

        {target && <section className="rounded-2xl border border-primary/20 bg-[linear-gradient(145deg,rgba(37,99,235,0.11),rgba(56,189,248,0.035))] p-4" aria-labelledby="message-related-title"><div className="flex items-start gap-3"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">{target === 'wallet' ? <WalletCards className="size-5" /> : <MessageSquareText className="size-5" />}</div><div><h2 id="message-related-title" className="text-sm font-semibold">关联内容</h2><p className="mt-1 text-[11px] leading-5 text-muted-foreground">查看完整信息与最新处理状态</p></div></div>{target === 'order' && item.orderId && <p className="mt-3 rounded-xl bg-background/45 p-3 text-[10px] tabular-nums text-muted-foreground">关联订单 · {item.orderId}</p>}<Button type="button" className="mt-3 h-11 w-full text-xs" onClick={action}>{actionLabel}<ArrowRight className="size-4" /></Button></section>}
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>本页为当前账号的通知阅读原型。进入详情后仅同步本地示例已读状态，不读取真实消息。</p></div>
        <Button type="button" variant="ghost" className="h-11 w-full text-xs text-muted-foreground" onClick={onBack}><ArrowLeft className="size-4" />返回消息中心</Button>
      </div>}
    </main>
  </PhoneFrame>
}
