import { useState, type FormEvent } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, CheckCircle2, History, LogIn, Mic2, Send } from 'lucide-react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { ProviderPublicFrame } from '@/pages/provider/ProviderAuthPages'
import { useStore } from '@/components/store/store-context'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { DetailGrid, ProviderNotice } from '@/pages/provider/ProviderShared'
import type { ProviderScenario } from '@/pages/provider/provider-data'

const providerAuditionInvitePath = '/provider/audition?token=SL-AUDITION-202611&role=provider'

export function ProviderAuditionInvitePage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const { store } = useStore()
  const token = params.get('token') || ''
  const role = params.get('role') || 'provider'
  const validLink = Boolean(token) && token !== 'expired' && role === 'provider'
  const [loggedIn] = useState(Boolean((location.state as { wechatAuthenticated?: boolean } | null)?.wechatAuthenticated))
  const [contact, setContact] = useState('13800000000')
  const [game, setGame] = useState('王者荣耀 · 技术陪练')
  const [remark, setRemark] = useState('主玩打野，晚间在线稳定，擅长局内沟通与节奏配合。')
  const [submitted, setSubmitted] = useState(false)
  const [notice, setNotice] = useState('')
  const returnPath = `${location.pathname}${location.search}`

  function login() {
    navigate(`/provider-login?redirect=${encodeURIComponent(returnPath)}`)
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!contact.trim() || !game.trim()) return
    setSubmitted(true)
    setNotice('试音报名已提交 · 示例状态，等待本店运营审核。')
  }
  function recover() {
    if (validLink) onRetry()
    else navigate(providerAuditionInvitePath)
  }

  return <ProviderPublicFrame><main className="mx-auto w-full max-w-4xl px-4 py-6 md:px-6 md:py-8">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><Button variant="ghost" className="-ml-3 h-11 px-3 text-muted-foreground" onClick={() => navigate(loggedIn ? '/provider/auditions' : '/provider-login')}><ArrowLeft className="size-4" />{loggedIn ? '返回试音列表' : '返回登录'}</Button><Badge variant="outline" className="mt-3 border-primary/20 bg-primary/5 text-primary"><Mic2 className="size-3" />公开试音邀请</Badge><h1 className="mt-3 text-2xl font-semibold tracking-tight">晚间档服务试音</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">核对 {store.name} 的招募信息，使用本店陪玩微信账号报名。</p></div><Badge variant="outline" className="mt-1">示例邀请</Badge></div>

    {scenario === 'loading' ? <div className="mt-6 space-y-4" aria-busy="true" aria-label="正在读取试音邀请"><Skeleton className="h-56 rounded-[12px]" /><Skeleton className="h-64 rounded-[12px]" /></div> : scenario === 'empty' ? <section className="mt-6 rounded-[12px] border bg-card p-8 text-center"><Mic2 className="mx-auto size-8 text-muted-foreground" /><h2 className="mt-4 text-lg font-semibold">暂无试音招募</h2><p className="mt-2 text-sm text-muted-foreground">新的招募发布后会在这里显示。</p><Button className="mt-5 h-11" onClick={onRetry}>查看示例招募</Button></section> : scenario === 'error' || !validLink ? <section role="alert" className="mt-6 rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">邀请链接无效或已失效</h2><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">请确认链接包含有效 token 且 role=provider。不要修改邀请所属门店或角色。</p><div className="mt-6 flex flex-wrap justify-center gap-2"><Button className="h-11" onClick={recover}>打开有效示例邀请</Button><Button variant="outline" className="h-11" onClick={() => navigate('/provider-login')}>返回微信登录</Button></div></section> : <>
      <section className="mt-6 rounded-[12px] border bg-card p-5"><div className="flex items-start justify-between gap-3"><div><p className="flex items-center gap-2 text-sm text-primary"><Mic2 className="size-4" />试音招募</p><h2 className="mt-3 text-xl font-semibold">王者荣耀 · 技术陪练</h2></div><Badge className={submitted ? 'bg-primary/10 text-primary' : 'bg-success/10 text-success'}>{submitted ? '审核中' : '报名中'}</Badge></div><div className="mt-5"><DetailGrid items={[{ label: '截止时间', value: '11 月 6 日 20:00' }, { label: '报名状态', value: submitted ? '已提交 · 待审核' : '尚未报名' }, { label: '段位要求', value: '最强王者及以上' }, { label: '服务时段', value: '19:00–23:00' }]} /></div><p className="mt-4 text-sm leading-6">招募 3 人。请进行简短自我介绍，并展示一局游戏中的沟通方式；避免背景音乐过大或包含无关人员信息。</p></section>
      {notice && <div className="mt-4"><ProviderNotice>{notice}</ProviderNotice></div>}
      <section className="mt-4 rounded-[12px] border bg-card p-5">
        {submitted ? <div className="flex items-start gap-3"><CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" /><div><h2 className="font-semibold text-success">报名已提交，等待审核</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">当前审核状态只在本原型展示。审核结果可从试音列表与历史记录继续核对。</p><Button variant="outline" className="mt-5 h-11" onClick={() => navigate('/provider/auditions')}>返回试音列表<ArrowRight className="size-4" /></Button></div></div> : loggedIn ? <form className="grid gap-4" onSubmit={submit}><div><h2 className="font-semibold">填写报名信息</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">微信身份已验证 · {store.provider.nickname} · 仅用于本店试音报名</p></div><div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="audition-contact">联系方式</Label><Input id="audition-contact" required value={contact} onChange={event => setContact(event.target.value)} /></div><div className="space-y-2"><Label htmlFor="audition-game">意向游戏</Label><Input id="audition-game" required value={game} onChange={event => setGame(event.target.value)} /></div></div><div className="space-y-2"><Label htmlFor="audition-remark">备注与服务介绍</Label><Textarea id="audition-remark" value={remark} onChange={event => setRemark(event.target.value)} rows={4} /></div><Button type="submit" className="h-12 w-full"><Send className="size-4" />提交示例报名</Button></form> : <div className="text-center"><LogIn className="mx-auto size-7 text-primary" /><h2 className="mt-4 font-semibold">微信登录后报名</h2><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">沿用陪玩端微信快捷登录、授权手机号与短信验证。完成后返回当前邀请，已填写的公开招募信息不会丢失。</p><Button className="mt-5 h-12 w-full" onClick={login}><LogIn className="size-4" />微信登录后报名</Button></div>}
      </section>
      {loggedIn && <section className="mt-4 rounded-[12px] border bg-card p-5"><h2 className="flex items-center gap-2 font-semibold"><History className="size-4 text-primary" />我的试音记录</h2>{submitted ? <div className="mt-4 border-t pt-4"><p className="text-sm font-medium">晚间档服务试音</p><p className="mt-1 text-xs leading-5 text-muted-foreground">待审核 · {game} · 刚刚提交</p><p className="mt-2 text-xs leading-5 text-muted-foreground">报名已提交，等待本店运营审核。</p></div> : <p className="mt-3 text-sm text-muted-foreground">暂无本场报名记录，提交后可在这里核对审核状态。</p>}</section>}
    </>}
  </main></ProviderPublicFrame>
}
