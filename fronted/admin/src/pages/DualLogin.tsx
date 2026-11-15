import { useEffect, useRef, useState, type FormEvent } from "react"
import { ArrowRight, CheckCircle2, Eye, EyeOff, LoaderCircle, LockKeyhole, MessageCircle, Moon, QrCode, ShieldCheck, Smartphone, Sun } from "@/components/ui/icons"
import { getSession, isAdminEntry, isWechatBrowser, persistSession, storeBrandLogo, useCurrentStore, useH5Auth } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent } from "@/components/ui/card"
import { Feedback } from "@/components/business"
import { adminClient, errorMessage } from "@/api/request"
import { savePlatformSession } from "@/lib/platform-session"

type LoginResult = {
  store_slug?: string
  identity_kind?: "platform"
  requiresSms?: boolean
  requiresOTP?: boolean
  otpChallenge?: string
  smsChallenge?: string
  maskedPhone?: string
  requiresWechatBinding?: boolean
  bindToken?: string
  token?: string
  refresh?: string
  profile?: any
}

export default function LoginPage({ dark, onThemeChange }: { dark: boolean; onThemeChange: (value: boolean) => void }) {
  const store = useCurrentStore()
  const alive = useRef(true)
  const attempt = useRef(0)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [challenge, setChallenge] = useState<LoginResult | null>(null)
  const [bindToken, setBindToken] = useState("")
  const [verifiedLogin, setVerifiedLogin] = useState<LoginResult | null>(null)
  const [smsCode, setSmsCode] = useState("")
  const [cooldown, setCooldown] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [identityKind, setIdentityKind] = useState<"platform" | "store" | "">("")
  const [ambiguous, setAmbiguous] = useState(false)

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; attempt.current += 1 }
  }, [])
  useEffect(() => {
    if (!challenge) return
    setCooldown(60)
    const timer = window.setInterval(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [challenge?.smsChallenge])

  function finish(result: LoginResult) {
    if (!result.token || !result.refresh || !result.profile) {
      setError("登录验证未完成，请重新登录")
      return
    }
    if (result.profile.identity_type === "platform") {
      savePlatformSession({ token: result.token, refresh: result.refresh, profile: result.profile })
      location.hash = "/platform/overview"
      location.reload()
      return
    }
    persistSession("admin", result)
    const next = new URLSearchParams(location.hash.split("?")[1] || "").get("redirect") || "/workspace"
    const destination = next.startsWith("/") && !next.startsWith("//") ? next : "/workspace"
    if (isAdminEntry()) {
      const target = new URL(location.href)
      target.searchParams.delete("store")
      target.hash = destination
      const [route, query = ""] = target.hash.split("?")
      const params = new URLSearchParams(query)
      params.delete("store")
      target.hash = route + (params.size ? `?${params}` : "")
      history.replaceState(history.state, "", target.href)
      location.reload()
    } else location.hash = destination
  }
  const wechat = useH5Auth("admin", finish)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const errors: Record<string, string> = {}
    if (!username.trim()) errors.username = "请输入管理员账号"
    if (!password) errors.password = "请输入登录密码"
    if (challenge && (challenge.requiresOTP ? !smsCode.trim() : smsCode.length !== 6)) errors.code = challenge.requiresOTP ? "请输入动态验证码" : "请输入 6 位短信验证码"
    setFieldErrors(errors)
    if (Object.keys(errors).length) return
    const owner = ++attempt.current
    const revision = getSession("admin").revision
    setBusy(true)
    setError("")
    try {
      const response = await adminClient.requestEnvelope<LoginResult>("/admin/auth/password-login", {
        method: "POST", public: true,
        data: { username: username.trim(), password, identityKind: challenge?.identity_kind || identityKind || undefined, smsChallenge: challenge?.smsChallenge, smsCode: smsCode.trim(), otpChallenge: challenge?.otpChallenge, otpCode: challenge?.requiresOTP ? smsCode.trim() : undefined },
      })
      if (!alive.current || attempt.current !== owner || getSession("admin").revision !== revision) return
      const result = response.data
      if (result.bindToken && result.token) {
        setVerifiedLogin(result)
        setBindToken(result.bindToken)
        setChallenge(null)
        setSmsCode("")
      } else if (result.requiresSms || result.requiresOTP) {
        setChallenge(result)
        setSmsCode("")
      } else finish(result)
    } catch (cause) {
      if (alive.current && attempt.current === owner) {
        if ((cause as { errorCode?: string }).errorCode === "ADMIN_IDENTITY_AMBIGUOUS") { setAmbiguous(true); setIdentityKind("") }
        setError(errorMessage(cause))
      }
    } finally {
      if (alive.current && attempt.current === owner) setBusy(false)
    }
  }

  async function resend() {
    if (busy || cooldown || !challenge?.smsChallenge) return
    setBusy(true)
    setError("")
    try {
      const response = await adminClient.requestEnvelope<LoginResult>("/admin/auth/password-login", {
        method: "POST", public: true,
        data: { username: username.trim(), password, identityKind: challenge.identity_kind || identityKind || undefined, smsChallenge: challenge.smsChallenge, resend: true },
      })
      setChallenge(response.data)
      setSmsCode("")
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  async function switchToSms() {
    if (busy) return
    setBusy(true); setError("")
    try {
      const response = await adminClient.requestEnvelope<LoginResult>("/admin/auth/password-login", {
        method: "POST", public: true, data: { username: username.trim(), password, identityKind: challenge?.identity_kind || identityKind || undefined, verificationMethod: "sms" },
      })
      if (alive.current) { setChallenge(response.data); setSmsCode("") }
    } catch (cause) { if (alive.current) setError(errorMessage(cause)) }
    finally { if (alive.current) setBusy(false) }
  }

  return <div className="admin-login grid min-h-screen min-w-[1366px] grid-cols-[minmax(480px,.92fr)_minmax(560px,1.08fr)] bg-background">
    <section className="admin-login-brand relative flex min-h-screen flex-col overflow-hidden bg-[#071a3d] p-12 text-white">
      <div className="admin-login-grid" aria-hidden="true" />
      <div className="account-login-brand flex items-center gap-2 rounded-[8px]">
        <span className="account-login-logo-frame"><img src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" width={32} height={32} className="account-login-brand-mark size-8" /></span>
        <span><strong className="block text-sm font-semibold">{store.name}</strong><span className="block text-xs text-blue-100/80">运营管理后台</span></span>
      </div>
      <div className="my-auto w-full max-w-lg self-center py-16"><h1 className="text-[42px] leading-tight font-semibold tracking-tight">让每一次服务，<br /><span className="text-sky-300">都有清晰的回响。</span></h1><p className="mt-6 max-w-md text-[15px] leading-7 text-blue-100/80">使用账号密码和短信验证码登录；绑定微信后可使用微信快捷登录。</p><div className="mt-10 grid max-w-md grid-cols-3 gap-3 text-sm">{[["账号密码", "短信验证"], ["微信 H5", "绑定后快捷登录"], ["后台账号", "安全验证"]].map(([title, note]) => <div key={title} className="rounded-xl border border-white/15 bg-white/5 p-4"><ShieldCheck className="mb-3 size-4 text-sky-300" /><p className="font-medium">{title}</p><p className="mt-1 text-xs text-blue-100/80">{note}</p></div>)}</div></div>
      <p className="text-xs text-blue-100/80">{store.name} · 管理后台</p>
    </section>
    <main id="main-content" tabIndex={-1} className="relative flex items-center justify-center px-12 py-12">
      <Button type="button" size="icon" variant="ghost" className="account-login-theme-toggle absolute top-6 right-6" aria-label={dark ? "切换为浅色模式" : "切换为深色模式"} onClick={() => onThemeChange(!dark)}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</Button>
      <Card className="admin-login-form w-full max-w-[440px] overflow-visible border bg-card shadow-sm ring-0"><CardContent className="px-6">
        <div className="flex items-start gap-3">
          <div className="account-login-panel-icon grid size-12 shrink-0 place-items-center rounded-[12px] border bg-card text-primary shadow-sm">{bindToken ? <MessageCircle className="size-5" /> : <LockKeyhole className="size-5" />}</div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold">{bindToken ? "绑定微信" : challenge ? (challenge.requiresOTP ? "验证动态验证码" : "验证手机号") : "欢迎回来"}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{bindToken ? "账号验证已完成，可以绑定微信或直接进入后台。" : challenge ? (challenge.requiresOTP ? "请输入验证器动态验证码，也可切换短信验证。" : `验证码已发送至 ${challenge.maskedPhone}`) : "微信内可快捷登录；账号密码登录需要完成身份验证。"}</p>
          </div>
        </div>
        <section className="mt-6 rounded-xl border bg-card p-4" aria-label="后台账号识别"><p className="text-sm font-medium">按账号或已绑定微信识别所属门店</p><p className="mt-2 text-xs leading-5 text-muted-foreground">同名账号无法唯一识别时，请使用“门店标识/账号”登录。不会默认选择门店。</p></section>
        <div className="mt-6 space-y-5">
          <Feedback message={error || wechat.error} />
          {bindToken ? <section className="space-y-4 rounded-xl border border-primary/20 bg-primary/5 p-4" aria-label="首次微信绑定">
            <div className="flex items-start gap-3"><CheckCircle2 className="mt-1 size-5 shrink-0 text-success" /><div><h3 className="text-sm font-semibold">账号密码与手机号已确认</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">请在微信内打开本页，使用本人微信完成官方 H5 授权。绑定后，下次可直接微信快捷登录。</p></div></div>
            {isWechatBrowser() ? <Button className="h-12 w-full" disabled={wechat.busy} onClick={() => wechat.start({ purpose: "bind_wechat", bindToken })}>{wechat.busy ? <LoaderCircle className="size-4 animate-spin" /> : <MessageCircle className="size-4" />}绑定当前微信并进入后台</Button> : <div className="rounded-lg bg-background p-3 text-sm leading-6"><Smartphone className="mr-2 inline size-4 text-primary" />微信内打开管理后台可绑定微信，也可直接进入后台。</div>}
            {wechat.flow && <Button asChild variant="outline" className="h-11 w-full"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权<ArrowRight className="size-4" /></a></Button>}
            {verifiedLogin && <Button type="button" variant="outline" className="w-full" disabled={wechat.busy} onClick={() => finish(verifiedLogin)}>暂不绑定，进入后台</Button>}
            <Button type="button" variant="ghost" className="w-full" onClick={() => { setBindToken(""); setChallenge(null); wechat.stop() }}>返回账号密码</Button>
          </section> : <>
            <form onSubmit={submit} noValidate className="space-y-5">
              {!challenge ? <>
                <div className="space-y-2"><Label htmlFor="username">管理员账号</Label><Input id="username" className="h-12" autoComplete="username" value={username} disabled={busy} required placeholder="例如 admin 或 sl/admin…" onChange={event => { setUsername(event.target.value); setIdentityKind(""); setAmbiguous(false); setFieldErrors(old => ({ ...old, username: "" })) }} aria-invalid={!!fieldErrors.username} />{fieldErrors.username && <p role="alert" className="text-xs text-destructive">{fieldErrors.username}</p>}</div>
                <div className="space-y-2"><Label htmlFor="password">登录密码</Label><div className="relative"><Input id="password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} disabled={busy} required placeholder="请输入登录密码…" className="h-12 pr-12" onChange={event => { setPassword(event.target.value); setFieldErrors(old => ({ ...old, password: "" })) }} aria-invalid={!!fieldErrors.password} /><Button type="button" variant="ghost" size="icon" className="absolute top-1 right-1 size-10" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? "隐藏密码" : "显示密码"}>{showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button></div>{fieldErrors.password && <p role="alert" className="text-xs text-destructive">{fieldErrors.password}</p>}</div>
              </> : <div className="space-y-2"><div className="flex items-center justify-between"><Label htmlFor="sms-code">{challenge.requiresOTP ? "动态验证码或恢复码" : "短信验证码"}</Label><span className="text-xs text-muted-foreground">{challenge.maskedPhone}</span></div><div className="flex gap-2"><Input id="sms-code" className="h-12 min-w-0 flex-1" inputMode={challenge.requiresOTP ? "text" : "numeric"} autoComplete="one-time-code" maxLength={challenge.requiresOTP ? 32 : 6} value={smsCode} disabled={busy} placeholder={challenge.requiresOTP ? "例如 123456 或恢复码…" : "例如 6 位短信验证码…"} onChange={event => { setSmsCode(challenge.requiresOTP ? event.target.value : event.target.value.replace(/\D/g, "")); setFieldErrors(old => ({ ...old, code: "" })) }} aria-invalid={!!fieldErrors.code} />{!challenge.requiresOTP && <Button type="button" variant="outline" className="h-12 shrink-0" disabled={busy || cooldown > 0} onClick={resend}>{cooldown > 0 ? `${cooldown} 秒` : "重新发送"}</Button>}</div>{fieldErrors.code && <p role="alert" className="text-xs text-destructive">{fieldErrors.code}</p>}{challenge.requiresOTP && <Button type="button" variant="outline" disabled={busy} onClick={switchToSms}>切换短信验证</Button>}<Button type="button" variant="ghost" className="h-9 px-0" onClick={() => { setChallenge(null); setSmsCode("") }}>返回账号密码</Button></div>}
              {!challenge && ambiguous && <fieldset className="space-y-2"><legend className="text-sm font-medium">选择要登录的身份</legend><div className="flex gap-2"><Button type="button" variant={identityKind === "platform" ? "default" : "outline"} onClick={() => { setIdentityKind("platform"); setError("") }}>平台账号</Button><Button type="button" variant={identityKind === "store" ? "default" : "outline"} onClick={() => { setIdentityKind("store"); setError("") }}>门店账号</Button></div></fieldset>}
              <Button type="submit" disabled={busy} className="h-12 w-full">{busy ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}{busy ? "正在验证" : challenge ? "验证并继续" : "下一步"}</Button>
            </form>
            <div className="space-y-3 border-t pt-5">
              {isWechatBrowser() ? <Button type="button" variant="outline" className="h-12 w-full" disabled={wechat.busy} onClick={() => wechat.start()}><MessageCircle className="size-4" />{wechat.busy ? "正在打开微信授权…" : "微信快捷登录"}</Button> : <div className="rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"><Smartphone className="mr-2 inline size-4" />微信内打开本页可使用微信快捷登录；普通浏览器请使用账号密码和短信验证码。</div>}
              {wechat.flow && <Button asChild variant="ghost" className="h-10 w-full"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权<ArrowRight className="size-4" /></a></Button>}
              <Button type="button" variant="ghost" className="h-10 w-full" disabled title="微信开放平台申请完成后开放"><QrCode className="size-4" />微信扫码登录 · 待开发</Button>
            </div>
          </>}
          <p className="pt-2 text-xs leading-5 text-muted-foreground">门店管理员创建账号并分配角色。微信绑定后可快捷登录同一后台账号。</p>
        </div>
      </CardContent></Card>
    </main>
  </div>
}
