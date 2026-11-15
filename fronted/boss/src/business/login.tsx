import { useEffect, useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { ArrowLeft, ArrowRight, CalendarCheck, Headphones, LockKeyhole, MessageCircle, QrCode, ShieldCheck, Smartphone, WalletCards } from "lucide-react"
import { AccountLoginError, AccountLoginField, AccountLoginLayout, getSession, isWechatBrowser, persistSession, storeBrandLogo, useCurrentStore, useH5Auth, useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { LoginPage as LegacyLoginPage } from "./account"
import { api, useQuery } from "./ui"

type LoginData = {
  requiresSms?: boolean
  requiresOTP?: boolean
  otpChallenge?: string
  smsChallenge?: string
  maskedPhone?: string
  token?: string
  refreshToken?: string
  userInfo?: { id: string | number; role?: string }
}

function PrivacyAgreementDialog({ open, onOpenChange, onAgree }: { open: boolean; onOpenChange: (open: boolean) => void; onAgree: () => void }) {
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[85dvh] overflow-y-auto rounded-[12px] sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>隐私保护协议</DialogTitle>
        <DialogDescription>账号识别、微信绑定与登录验证的信息使用说明</DialogDescription>
      </DialogHeader>
      <div className="grid gap-5 text-sm leading-6 text-muted-foreground">
        <section><h3 className="mb-1 font-semibold text-foreground">账号识别与绑定</h3><p>信息用于识别当前门店账号、完成微信绑定、登录验证和账号安全。微信快捷登录与账号密码＋短信验证登录后，进入同一个老板账号。</p></section>
        <section><h3 className="mb-1 font-semibold text-foreground">账号密码与短信验证</h3><p>账号和密码仅用于当前门店身份校验，短信验证码仅用于登录验证。请勿向他人透露密码或验证码；不同门店的账号身份和登录状态相互隔离。</p></section>
        <section><h3 className="mb-1 font-semibold text-foreground">微信授权</h3><p>微信内打开官方授权页面后，由你确认是否继续。系统仅接收完成账号识别与绑定所需的微信渠道身份，不读取微信聊天记录。手机号授权读取及短信验证以微信实际支持能力为准。</p></section>
        <section><h3 className="mb-1 font-semibold text-foreground">你的选择</h3><p>未同意本协议时，仍可使用账号密码＋短信验证码登录。微信扫码登录尚未开放，不会以页面展示代替真实授权。</p></section>
      </div>
      <DialogFooter className="gap-3 sm:gap-2">
        <Button type="button" variant="outline" className="h-11 rounded-[8px]" onClick={() => onOpenChange(false)}>返回</Button>
        <Button type="button" className="h-11 rounded-[8px]" onClick={onAgree}>阅读并同意</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

type SupportContact = {
  name: string
  tips: string
  qrcode_url?: string
  wecom_service_url?: string
}

function SupportContactDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{ loading: boolean; error: string; data: SupportContact | null }>({ loading: false, error: "", data: null })
  useEffect(() => {
    if (!open) return
    let active = true
    setState({ loading: true, error: "", data: null })
    api.get<SupportContact>("/support/public-contact/", { public: true }).then(data => {
      if (active) setState({ loading: false, error: "", data })
    }).catch((cause: any) => {
      if (active) setState({ loading: false, error: cause?.message || "客服信息读取失败，请重试", data: null })
    })
    return () => { active = false }
  }, [open, attempt])
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[85dvh] overflow-y-auto rounded-[12px] sm:max-w-md">
      <DialogHeader>
        <DialogTitle>门店客服</DialogTitle>
        <DialogDescription>客服信息由当前门店提供，请核对账号与入驻问题。</DialogDescription>
      </DialogHeader>
      {state.loading ? <p role="status" className="py-8 text-center text-sm text-muted-foreground">正在读取门店客服…</p>
        : state.error ? <div className="grid gap-3 text-center"><p role="alert" className="text-sm text-destructive">{state.error}</p><Button type="button" variant="outline" className="h-11 rounded-[8px]" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></div>
        : state.data ? <div className="grid gap-4 text-center">
          {state.data.qrcode_url ? <img src={state.data.qrcode_url} alt="当前门店客服二维码" width={176} height={176} className="mx-auto size-44 rounded-[8px] border bg-white p-2" /> : <p className="text-sm text-muted-foreground">{state.data.name || "门店客服"}</p>}
          {state.data.tips && <p className="text-sm leading-6 whitespace-pre-wrap text-muted-foreground">{state.data.tips}</p>}
          {state.data.wecom_service_url && /^https:\/\//.test(state.data.wecom_service_url) && <Button asChild className="h-11 w-full rounded-[8px]"><a href={state.data.wecom_service_url} target="_blank" rel="noopener noreferrer">打开企业微信客服</a></Button>}
        </div>
        : <p className="py-8 text-center text-sm text-muted-foreground">门店客服资料暂未配置。</p>}
    </DialogContent>
  </Dialog>
}

export default function LoginPage() {
  const store = useCurrentStore()
  const navigate = useNavigate()
  const query = useQuery()
  const session = useSession("boss")
  const verifyPhone = query.get("verify_phone") === "1"
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [challenge, setChallenge] = useState<LoginData | null>(null)
  const [smsCode, setSmsCode] = useState("")
  const [cooldown, setCooldown] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [agreed, setAgreed] = useState(false)
  const [privacyOpen, setPrivacyOpen] = useState(false)
  const [supportOpen, setSupportOpen] = useState(false)

  useEffect(() => {
    if (session.token && !verifyPhone) {
      const next = query.get("next") || "/home"
      navigate(next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/home", { replace: true })
    }
  }, [session.token, verifyPhone, navigate])
  useEffect(() => {
    if (!challenge) return
    setCooldown(60)
    const timer = window.setInterval(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [challenge?.smsChallenge])

  function finish(data: LoginData) {
    if (data.userInfo?.role !== "customer") {
      setError("请使用老板账号登录")
      return
    }
    persistSession("boss", data)
    const next = query.get("next") || "/home"
    navigate(next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/home", { replace: true })
  }
  const wechat = useH5Auth("boss", finish)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!username.trim() || !password || (challenge && (challenge.requiresOTP ? !smsCode.trim() : smsCode.length !== 6))) {
      setError(challenge ? (challenge.requiresOTP ? "请输入动态验证码" : "请输入 6 位短信验证码") : "请输入账号和密码")
      return
    }
    const revision = getSession("boss").revision
    setBusy(true)
    setError("")
    try {
      const result = await api.post<LoginData>("/users/password-login/", {
        audience: "boss", username: username.trim(), password,
        smsChallenge: challenge?.smsChallenge, smsCode: smsCode.trim(),
        otpChallenge: challenge?.otpChallenge, otpCode: challenge?.requiresOTP ? smsCode.trim() : undefined,
      }, { public: true })
      if (getSession("boss").revision !== revision) throw new Error("登录状态已变化，请重新登录")
      if (result.requiresSms || result.requiresOTP) {
        setChallenge(result)
        setSmsCode("")
      } else finish(result)
    } catch (cause: any) {
      setError(cause.message || "登录未完成，请重试")
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    if (busy || cooldown || !challenge?.smsChallenge) return
    setBusy(true)
    setError("")
    try {
      const result = await api.post<LoginData>("/users/password-login/", {
        audience: "boss", username: username.trim(), password,
        smsChallenge: challenge.smsChallenge, resend: true,
      }, { public: true })
      setChallenge(result)
      setSmsCode("")
    } catch (cause: any) {
      setError(cause.message || "验证码发送失败，请重试")
    } finally {
      setBusy(false)
    }
  }

  async function switchToSms() {
    if (busy) return
    setBusy(true); setError("")
    try {
      const result = await api.post<LoginData>("/users/password-login/", {
        audience: "boss", username: username.trim(), password, verificationMethod: "sms",
      }, { public: true })
      setChallenge(result); setSmsCode("")
    } catch (cause: any) { setError(cause.message || "验证码发送失败，请重试") }
    finally { setBusy(false) }
  }

  if (verifyPhone) return <LegacyLoginPage />

  return <><AccountLoginLayout
    storeName={store.name}
    logoUrl={storeBrandLogo(store, import.meta.env.BASE_URL)}
    entryLabel="服务中心"
    brandTo="/login"
    brandAriaLabel={`${store.name}老板端登录`}
    title={<>每一次组队，<br />都从安心登录开始。</>}
    description="微信内可快捷登录，普通浏览器使用门店提供的账号密码和短信验证码。登录后进入同一个老板账号。"
    features={[
      { icon: <Headphones className="size-4" />, label: "选择服务" },
      { icon: <CalendarCheck className="size-4" />, label: "订单预约" },
      { icon: <WalletCards className="size-4" />, label: "权益管理" },
    ]}
    panelTitle={challenge ? (challenge.requiresOTP ? "验证动态验证码" : "验证手机号") : "老板账号登录"}
    panelDescription={challenge ? (challenge.requiresOTP ? "请输入验证器动态验证码，也可切换短信验证。" : `验证码已发送至 ${challenge.maskedPhone}`) : "账号密码登录需要完成身份验证。"}
    showBrandHeader={false}
  >
    <AccountLoginError message={error || wechat.error} />
    <form className="grid gap-4" onSubmit={submit}>
      {!challenge ? <>
        <AccountLoginField label="账号" htmlFor="username"><Input id="username" className="h-11 rounded-[8px]" autoComplete="username" value={username} disabled={busy} onChange={event => setUsername(event.target.value)} placeholder="请输入老板账号…" /></AccountLoginField>
        <AccountLoginField label="密码" htmlFor="password"><Input id="password" className="h-11 rounded-[8px]" autoComplete="current-password" type="password" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} placeholder="请输入登录密码…" /></AccountLoginField>
      </> : <AccountLoginField label={challenge.requiresOTP ? "动态验证码或恢复码" : "短信验证码"} htmlFor="sms-code"><div className="flex gap-2"><Input id="sms-code" className="h-11 min-w-0 flex-1 rounded-[8px]" inputMode={challenge.requiresOTP ? "text" : "numeric"} autoComplete="one-time-code" maxLength={challenge.requiresOTP ? 32 : 6} value={smsCode} disabled={busy} onChange={event => setSmsCode(challenge.requiresOTP ? event.target.value : event.target.value.replace(/\D/g, ""))} placeholder={challenge.requiresOTP ? "例如 123456 或恢复码…" : "例如 6 位短信验证码…"} />{!challenge.requiresOTP && <Button type="button" variant="outline" className="h-11 shrink-0 rounded-[8px]" disabled={busy || cooldown > 0} onClick={resend}>{cooldown > 0 ? `${cooldown} 秒` : "重新发送"}</Button>}</div></AccountLoginField>}
      <Button type="submit" className="h-12 w-full rounded-[8px]" disabled={busy}>{busy ? "验证中…" : challenge ? "验证并登录" : "下一步"}{!busy && <ArrowRight className="size-4" />}</Button>
      {challenge?.requiresOTP && <Button type="button" variant="outline" disabled={busy} onClick={switchToSms}>切换短信验证</Button>}
      {challenge && <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={() => { setChallenge(null); setSmsCode(""); setError("") }}><ArrowLeft className="size-4" />返回账号密码</Button>}
    </form>
    <div className="grid gap-3 border-t pt-4">
      {isWechatBrowser() ? <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" disabled={wechat.busy} onClick={() => { if (!agreed) { setError("请先阅读并同意《隐私保护协议》"); return } setError(""); void wechat.start() }}><MessageCircle className="size-4" />{wechat.busy ? "正在打开微信授权…" : "微信快捷登录"}</Button> : <div className="rounded-[8px] bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"><Smartphone className="mr-2 inline size-4" />微信内打开本页可使用快捷登录；普通浏览器请使用账号密码和短信验证码。</div>}
      {wechat.flow && <Button asChild variant="ghost" className="h-10 w-full rounded-[8px]"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权<ArrowRight className="size-4" /></a></Button>}
      <label className="flex min-h-11 items-start gap-2 text-xs leading-5 text-muted-foreground"><input name="privacy_agreement" autoComplete="off" className="mt-1 size-4 shrink-0 accent-primary" type="checkbox" checked={agreed} onChange={event => setAgreed(event.target.checked)} /><span>我已阅读并同意<button type="button" className="-my-2 inline-flex min-h-11 items-center rounded-[4px] px-2 font-medium text-primary underline decoration-2 underline-offset-4 outline-ring/50" onClick={event => { event.preventDefault(); event.stopPropagation(); setPrivacyOpen(true) }}>《隐私保护协议》</button>，授权用于账号识别与绑定。</span></label>
      <Button type="button" variant="ghost" className="h-10 w-full rounded-[8px]" disabled title="微信开放平台申请完成后开放"><QrCode className="size-4" />微信扫码登录 · 待开发</Button>
    </div>
    <div className="border-t pt-4"><Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={() => setSupportOpen(true)}><Headphones className="size-4" />账号有问题？联系客服</Button></div>
    <div className="grid gap-2">
      <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-3.5 shrink-0" />授权仅用于账号识别与绑定，不读取微信聊天记录</p>
      <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-3.5 shrink-0" />密码和短信验证码仅用于当前门店账号验证</p>
    </div>
  </AccountLoginLayout>
  <PrivacyAgreementDialog open={privacyOpen} onOpenChange={setPrivacyOpen} onAgree={() => { setAgreed(true); setPrivacyOpen(false); setError("") }} />
  <SupportContactDialog open={supportOpen} onOpenChange={setSupportOpen} />
  </>
}
