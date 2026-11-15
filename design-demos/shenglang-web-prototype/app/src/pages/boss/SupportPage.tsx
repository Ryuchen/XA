import { useState } from 'react'
import {
  AlertCircle,
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Copy,
  ExternalLink,
  Headphones,
  LoaderCircle,
  MessageCircleMore,
  QrCode,
  RefreshCw,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { ExampleQr } from '@/components/prototype/ExampleQr'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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

export type SupportScenario = 'default' | 'guest' | 'loading' | 'error' | 'order' | 'recharge' | 'opening' | 'open-error' | 'unsupported' | 'unavailable' | 'empty'

interface SupportPageProps {
  scenario: SupportScenario
  orderNo: string
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onChat: (withOrder: boolean) => void
  onRetry: () => void
}

const reference = 'SL-HELP-10241'
const wechatId = 'ShengLang-Service'

export function SupportPage({ scenario, orderNo, onLogin, onBack, onHome, onCompanions, onOrders, onChat, onRetry }: SupportPageProps) {
  const [opening, setOpening] = useState(scenario === 'opening')
  const [contactIssue, setContactIssue] = useState<string | null>(scenario === 'open-error' ? '微信客服暂时无法打开，请稍后重试，或使用下方平台客服。' : scenario === 'unsupported' ? '当前浏览器无法打开企业微信客服，请使用下方平台客服。' : null)
  const [copied, setCopied] = useState<'reference' | 'wechat' | null>(null)
  const [copyError, setCopyError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [qrOpen, setQrOpen] = useState(false)
  const guest = scenario === 'guest'
  const ordered = scenario === 'order'
  const recharge = scenario === 'recharge'
  const hasCard = scenario !== 'empty'
  const wecomAvailable = !['unavailable', 'empty'].includes(scenario)
  const title = recharge ? '钱包充值咨询' : '客服与帮助'

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
  }

  async function copyText(kind: 'reference' | 'wechat') {
    const text = kind === 'wechat' ? wechatId : [`咨询编号 ${reference}`, ordered ? `订单号 ${orderNo}` : ''].filter(Boolean).join('，')
    setCopyError('')
    setFeedback('')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(kind)
    } catch {
      setCopied(null)
      setCopyError('复制失败，请手动选择页面中的信息。')
    }
  }

  function openWecom() {
    if (opening || !wecomAvailable) return
    setFeedback('')
    setCopyError('')
    if (scenario === 'unsupported') {
      setContactIssue('当前浏览器无法打开企业微信客服，请使用下方平台客服。')
      return
    }
    setContactIssue(null)
    setOpening(true)
    window.setTimeout(() => {
      setOpening(false)
      setFeedback(`企业微信入口已完成本地演示，未打开真实客服。${guest ? '' : ordered ? `正式环境将携带咨询编号与订单 ${orderNo}。` : recharge ? '正式环境将携带咨询编号与钱包充值咨询场景。' : `正式环境将携带咨询编号 ${reference}。`}`)
    }, 600)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <header className="px-4 pb-4 pt-2">
          <div className="flex items-start gap-3">
            <Button type="button" variant="ghost" size="icon" className="size-11 shrink-0 rounded-full" onClick={onBack} aria-label="返回上一页"><ChevronLeft className="size-5" /></Button>
            <div className="min-w-0 flex-1 pt-1">
              <p className="text-[11px] font-medium text-primary">声浪电竞 · 服务支持</p>
              <h1 className="mt-1 text-[24px] font-semibold tracking-[-0.035em]">{title}</h1>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{recharge ? '联系客服核对付款，到账后可在钱包查看流水。' : '账号绑定、服务需求与订单问题，都可以联系我们。'}</p>
            </div>
          </div>
        </header>

        {scenario === 'loading' ? (
          <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载客服信息"><Skeleton className="h-32 w-full rounded-2xl" /><Skeleton className="h-52 w-full rounded-2xl" /><Skeleton className="h-44 w-full rounded-2xl" /><Skeleton className="h-36 w-full rounded-2xl" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-8"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">客服信息暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会修改订单、钱包或会话数据。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button><Button type="button" variant="ghost" className="mt-2 h-11 w-full" onClick={onBack}>返回上一页</Button></div></section>
        ) : (
          <div className="space-y-4 px-4 pb-5">
            {hasCard && !guest && <section className="rounded-2xl border border-primary/20 bg-primary/[0.055] p-4" aria-labelledby="support-context-title">
              <div className="flex items-center justify-between gap-3"><h2 id="support-context-title" className="text-sm font-semibold">本次咨询</h2><Badge variant="outline" className="border-primary/20 bg-primary/10 text-[10px] text-primary">{ordered ? '已关联订单' : recharge ? '充值场景' : '老板账号'}</Badge></div>
              {ordered && <div className="mt-3 border-b border-primary/10 pb-3"><p className="text-[10px] text-muted-foreground">订单号</p><p className="mt-1 break-all text-sm font-semibold tabular-nums">{orderNo}</p></div>}
              <div className="mt-3 flex items-end gap-3"><div className="min-w-0 flex-1"><p className="text-[10px] text-muted-foreground">咨询编号</p><p className="mt-1 break-all text-sm font-semibold text-primary">{reference}</p></div><Button type="button" variant="outline" className="h-11 shrink-0 px-3 text-xs" onClick={() => copyText('reference')} aria-label={ordered ? '复制咨询编号和关联订单给客服' : '复制咨询编号给客服'}>{copied === 'reference' ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}{copied === 'reference' ? '已复制' : '复制信息'}</Button></div>
              <p className="mt-3 text-[10px] leading-4 text-muted-foreground">咨询时提供编号{ordered ? '与订单号' : ''}，便于客服核对。</p>
            </section>}

            {recharge && <section className="flex gap-3 rounded-2xl border border-warning/25 bg-warning/5 p-4"><WalletCards className="mt-0.5 size-5 shrink-0 text-warning" /><div><h2 className="text-xs font-semibold">先核对，再付款</h2><p className="mt-1 text-[11px] leading-5 text-muted-foreground">请勿重复付款。客服核对后，实际到账以钱包余额和充值流水为准。</p></div></section>}

            <section aria-labelledby="support-channels-title">
              <div className="mb-3 flex items-end justify-between gap-3 px-1"><div><h2 id="support-channels-title" className="text-base font-semibold">选择咨询方式</h2><p className="mt-1 text-[10px] text-muted-foreground">企业微信与平台会话均可咨询</p></div><Badge variant="secondary" className="shrink-0 text-[9px]">示例通道</Badge></div>
              <div className="space-y-3">
                <article className="rounded-2xl border border-primary/20 bg-card p-4">
                  <div className="flex items-center gap-3"><Avatar className="size-11 rounded-xl border border-primary/20 bg-primary/10"><AvatarFallback className="rounded-xl bg-primary/10 text-primary"><Building2 className="size-5" /></AvatarFallback></Avatar><div className="min-w-0 flex-1"><h3 className="text-sm font-semibold">声浪客服 · 小澜</h3><p className="mt-1 text-[10px] text-muted-foreground">声浪电竞 · 企业微信客服</p></div><span className="text-[10px] font-medium text-primary">通道一</span></div>
                  <p className="mt-3 text-xs leading-5 text-muted-foreground">打开企业微信客服{!guest ? '，可携带当前咨询编号与关联信息' : '，在微信中继续咨询'}。</p>
                  {wecomAvailable ? <Button type="button" className="mt-4 h-12 w-full" onClick={openWecom} disabled={opening}>{opening ? <LoaderCircle className="size-4 animate-spin" /> : <ExternalLink className="size-4" />}{opening ? '正在打开客服' : contactIssue ? '重试企业微信客服' : '打开企业微信客服'}</Button> : <div className="mt-4 rounded-xl border border-dashed bg-muted/30 p-3 text-xs leading-5 text-muted-foreground" role="status">当前未配置企业微信客服，请使用下方平台会话。</div>}
                  {contactIssue && <p role="alert" className="mt-3 rounded-xl bg-destructive/10 p-3 text-[11px] leading-5 text-destructive">{contactIssue}</p>}
                </article>

                <article className="rounded-2xl border bg-card p-4">
                  <div className="flex items-center gap-3"><Avatar className="size-11 rounded-xl border bg-muted/50"><AvatarFallback className="rounded-xl bg-muted/40 text-foreground"><MessageCircleMore className="size-5" /></AvatarFallback></Avatar><div className="min-w-0 flex-1"><h3 className="text-sm font-semibold">平台客服会话</h3><p className="mt-1 text-[10px] text-muted-foreground">在 H5 内发送文字与图片</p></div><span className="text-[10px] font-medium text-muted-foreground">通道二</span></div>
                  <p className="mt-3 text-xs leading-5 text-muted-foreground">{ordered ? '会话会保留当前订单入口，便于继续核对问题。' : guest ? '登录后可查看历史会话，并发送文字与图片。' : '保留历史消息，方便继续核对服务与账号问题。'}</p>
                  <Button type="button" variant="outline" className="mt-4 h-12 w-full" onClick={() => guest ? onLogin() : onChat(ordered)}>{guest ? <ShieldCheck className="size-4" /> : <MessageCircleMore className="size-4" />}{guest ? '登录后进入平台会话' : '进入平台会话'}<ChevronRight className="ml-auto size-4" /></Button>
                </article>
              </div>
              <div className="mt-3 flex gap-2 rounded-xl bg-muted/40 p-3"><CircleHelp className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><p className="text-[10px] leading-4 text-muted-foreground"><span className="font-medium text-foreground">回复说明：</span>回复时间以实际会话为准，请勿重复提交相同问题。</p></div>
            </section>

            {feedback && <p role="status" className="rounded-xl border border-primary/20 bg-primary/5 p-3 text-[11px] leading-5 text-primary">{feedback}</p>}
            {copyError && <p role="alert" className="rounded-xl bg-destructive/10 p-3 text-[11px] leading-5 text-destructive">{copyError}</p>}

            {hasCard && <section className="rounded-2xl border bg-card p-4" aria-labelledby="support-other-title">
              <div className="flex items-center justify-between"><h2 id="support-other-title" className="text-sm font-semibold">其他联系方式</h2><QrCode className="size-4 text-muted-foreground" /></div>
              <div className="mt-3 flex items-center gap-3 border-b pb-4"><div className="min-w-0 flex-1"><p className="text-[10px] text-muted-foreground">客服微信号</p><p className="mt-1 break-all text-sm font-medium">{wechatId}</p></div><Button type="button" variant="outline" className="h-11 shrink-0 px-3 text-xs" onClick={() => copyText('wechat')}>{copied === 'wechat' ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}{copied === 'wechat' ? '已复制' : '复制微信号'}</Button></div>
              <button type="button" className="mx-auto mt-4 flex min-h-11 flex-col items-center rounded-xl focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setQrOpen(true)} aria-label="放大客服示例二维码"><span className="rounded-2xl border bg-white p-2"><ExampleQr /></span><span className="mt-2 text-[10px] text-muted-foreground">点击放大 · 示例二维码不可扫码</span></button>
            </section>}

            <section className="rounded-2xl border bg-card p-4"><div className="flex items-center gap-2"><Headphones className="size-4 text-primary" /><h2 className="text-sm font-semibold">咨询说明</h2></div><p className="mt-2 text-xs leading-5 text-muted-foreground">订单、充值与账号问题请从对应页面进入，便于携带必要的上下文。处理结果以订单、钱包和会话记录为准。</p></section>
          </div>
        )}
      </main>

      <Dialog open={qrOpen} onOpenChange={setQrOpen}>
        <DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">客服二维码</DialogTitle><DialogDescription className="text-left text-sm leading-6">这是原型示意图，不包含真实客服账号，也无法扫码。</DialogDescription></DialogHeader><div className="mx-auto rounded-3xl border bg-white p-4"><ExampleQr large /></div><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full">返回客服与帮助</Button></DialogClose></DialogFooter></DialogContent>
      </Dialog>

    </PhoneFrame>
  )
}

