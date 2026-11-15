import { useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  AudioLines,
  CalendarClock,
  CheckCircle2,
  Headphones,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'

export type AuditionScenario = 'default' | 'existing-account' | 'loading' | 'error' | 'invalid' | 'expired' | 'confirming' | 'exchange-error' | 'unbound'

interface AuditionPageProps {
  scenario: AuditionScenario
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onEntered: () => void
  onSupport: () => void
  onRetry: () => void
}

export function AuditionPage({ scenario, onHome, onCompanions, onOrders, onMine, onEntered, onSupport, onRetry }: AuditionPageProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(scenario === 'confirming')
  const [feedback, setFeedback] = useState('')
  const existingAccount = scenario === 'existing-account' || scenario === 'confirming'
  const hasError = ['error', 'invalid', 'expired'].includes(scenario)
  const errorTitle = scenario === 'invalid' ? '这不是有效的老板试音邀请' : scenario === 'expired' ? '试音链接已过期' : '邀请信息暂时没有加载成功'
  const errorCopy = scenario === 'invalid' ? '链接不存在、已撤销或角色不匹配，请核对客服发给你的完整地址。' : scenario === 'expired' ? '当前邀请已超过有效期，请联系活动客服确认最新安排。' : '请检查网络后重试。读取邀请失败不会切换当前账号。'

  function dock(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onMine()
  }

  function confirmEnter() {
    if (busy) return
    setConfirmOpen(false)
    setBusy(true)
    setFeedback('')
    window.setTimeout(() => {
      setBusy(false)
      if (scenario === 'exchange-error') {
        setFeedback('邀请状态确认失败，请检查网络后重试。当前账号尚未切换。')
      } else if (scenario === 'unbound') {
        setFeedback('该入口尚未绑定账号，请联系活动客服。')
      } else {
        onEntered()
      }
    }, 650)
  }

  return <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={dock} />}>
    <main className="pb-28">
      <header className="px-4 pb-4 pt-3"><div className="flex items-center justify-between gap-3"><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary"><Sparkles className="size-3" />老板专属邀请</Badge><span className="text-[10px] text-muted-foreground">免登录读取 · 示例</span></div><h1 className="mt-5 text-[28px] font-semibold tracking-[-0.04em]">确认试音邀请</h1><p className="mt-1 text-xs leading-5 text-muted-foreground">核对活动与绑定账号，确认后即可进入。</p></header>

      <div className="space-y-4 px-4 pb-5">
        {scenario === 'loading' ? <div className="space-y-4" aria-busy="true" aria-label="正在加载试音邀请"><Skeleton className="h-40 w-full rounded-3xl" /><Skeleton className="h-[330px] w-full rounded-3xl" /></div> : hasError ? <section role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">{errorTitle}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{errorCopy}</p>{scenario === 'error' ? <Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button> : <Button type="button" variant="outline" className="mt-6 h-11 w-full" onClick={onSupport}><Headphones className="size-4" />联系活动客服</Button>}</section> : <>
          <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.42),rgba(2,6,23,0.9)_72%)] p-5" aria-labelledby="audition-event-title">
            <div className="pointer-events-none absolute -right-12 -top-14 size-44 rounded-full border border-sky-300/10" /><AudioLines className="pointer-events-none absolute -right-3 top-8 size-28 text-sky-200/[0.07]" strokeWidth={1} />
            <div className="relative flex items-start justify-between gap-3"><div><p className="text-[10px] font-medium text-sky-200/65">SHENGLANG AUDITION</p><h2 id="audition-event-title" className="mt-2 max-w-[230px] text-[22px] font-semibold leading-8">声浪陪玩试音 · 暖冬专场</h2></div><Badge className="shrink-0 border border-success/20 bg-success/10 text-[10px] text-success hover:bg-success/10">邀请有效</Badge></div>
            <p className="relative mt-4 text-xs leading-6 text-sky-100/70">王者荣耀与英雄联盟陪玩试音交流，由活动客服协助安排具体时段。</p>
            <div className="relative mt-5 flex items-center gap-2 border-t border-white/10 pt-4 text-[11px] text-sky-100/65"><CalendarClock className="size-4 text-sky-300" /><span>有效期至</span><strong className="ml-auto font-medium text-white">2026-11-08 22:00</strong></div>
          </section>

          <section className="rounded-2xl border bg-card p-4" aria-labelledby="audition-account-title"><div className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl bg-primary/10 text-primary"><UserRoundCheck className="size-5" /></div><div><h2 id="audition-account-title" className="text-sm font-semibold">账号确认</h2><p className="mt-1 text-[10px] text-muted-foreground">邀请仅绑定特定老板账号</p></div></div><div className="mt-4 divide-y rounded-xl bg-muted/30 px-3"><div className="flex min-h-12 items-center justify-between gap-3 text-xs"><span className="text-muted-foreground">进入身份</span><span className="font-medium">邀请绑定的老板账号</span></div>{existingAccount && <div className="flex min-h-12 items-center justify-between gap-3 text-xs"><span className="text-muted-foreground">当前账号</span><span className="font-medium">海浪收藏家</span></div>}</div>{existingAccount && <p className="mt-3 rounded-xl bg-warning/[0.07] p-3 text-[11px] leading-5 text-warning">进入后将切换至邀请绑定的老板账号。</p>}<Button type="button" className="mt-4 h-12 w-full" disabled={busy} onClick={() => setConfirmOpen(true)}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}{busy ? '正在确认并进入' : '确认身份并进入'}{!busy && <ArrowRight className="size-4" />}</Button>{feedback && <p role="alert" className="mt-3 rounded-xl bg-destructive/[0.07] p-3 text-[11px] leading-5 text-destructive">{feedback}</p>}<p className="mt-3 text-[10px] leading-5 text-muted-foreground">此邀请绑定特定账号，请勿转发。进入后可在账号安全页绑定微信。</p></section>
        </>}

        <section className="flex items-center gap-3 rounded-2xl border bg-card/60 p-4"><div className="min-w-0 flex-1"><h2 className="text-xs font-semibold">需要协助？</h2><p className="mt-1 text-[10px] text-muted-foreground">活动安排与邀请问题</p></div><Button type="button" variant="ghost" className="h-11 shrink-0 px-2 text-xs text-primary" onClick={onSupport}><Headphones className="size-4" />联系活动客服</Button></section>
        <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>原型仅演示邀请核对与账号切换反馈，不读取分享 token，不签发登录态，也不绑定真实微信。</p></div>
      </div>
    </main>

    <Dialog open={confirmOpen} onOpenChange={open => !busy && setConfirmOpen(open)}><DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-xl bg-primary/10 text-primary"><UserRoundCheck className="size-5" /></div><DialogTitle className="text-left">使用邀请绑定的账号</DialogTitle><DialogDescription className="text-left leading-6">该邀请将登录分享链接所绑定的老板账号。{existingAccount ? '这会切换当前登录账号。' : ''}请仅使用客服发给你本人的邀请。</DialogDescription></DialogHeader><div className="rounded-xl border border-warning/20 bg-warning/[0.06] p-3 text-[11px] leading-5 text-muted-foreground">确认前请核对活动名称与有效期。原型不会切换真实账号。</div><DialogFooter><div className="grid w-full grid-cols-2 gap-2"><DialogClose asChild><Button type="button" variant="outline" className="h-11" disabled={busy}>再核对一下</Button></DialogClose><Button type="button" className="h-11" disabled={busy} onClick={confirmEnter}>{busy && <LoaderCircle className="size-4 animate-spin" />}确认进入</Button></div></DialogFooter></DialogContent></Dialog>
  </PhoneFrame>
}
