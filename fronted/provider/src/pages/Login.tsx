import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"
import { ArrowLeft, ArrowRight, BadgeCheck, ClipboardCheck, LockKeyhole, MessageCircle, Moon, QrCode, Smartphone, Sun, UserRoundPlus, WalletCards } from "lucide-react"
import { AccountLoginError, AccountLoginField, AccountLoginLayout, getSession, isWechatBrowser, persistSession, storeBrandLogo, syncThemeColor, useCurrentStore, useH5Auth } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { anonymous, message } from "@/lib/provider"

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

export default function LoginPage() {
  const store = useCurrentStore()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const active = useRef(true)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [challenge, setChallenge] = useState<LoginData | null>(null)
  const [smsCode, setSmsCode] = useState("")
  const [cooldown, setCooldown] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("xa_fronted_provider_theme")
      if (saved) return saved === "dark"
    } catch { /* 使用当前主题 */ }
    return document.documentElement.classList.contains("dark")
  })
  const requestedDestination = params.get("redirect")
  const destination = requestedDestination?.startsWith("/") && !requestedDestination.startsWith("//") && !requestedDestination.startsWith("/login") ? requestedDestination : "/orders"

  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    return syncThemeColor()
  }, [dark])
  useEffect(() => {
    if (!challenge) return
    setCooldown(60)
    const timer = window.setInterval(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [challenge?.smsChallenge])

  function finish(data: LoginData) {
    if (!active.current) return
    if (data.userInfo?.role !== "provider") {
      setError("请使用陪玩账号登录")
      return
    }
    persistSession("provider", data)
    navigate(destination, { replace: true })
  }
  const wechat = useH5Auth("provider", finish)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!username.trim() || !password || (challenge && (challenge.requiresOTP ? !smsCode.trim() : smsCode.length !== 6))) {
      setError(challenge ? (challenge.requiresOTP ? "请输入动态验证码" : "请输入 6 位短信验证码") : "请输入账号和密码")
      return
    }
    const revision = getSession("provider").revision
    setBusy(true)
    setError("")
    try {
      const result = await anonymous<LoginData>("/users/password-login/", {
        audience: "provider", username: username.trim(), password,
        smsChallenge: challenge?.smsChallenge, smsCode: smsCode.trim(),
        otpChallenge: challenge?.otpChallenge, otpCode: challenge?.requiresOTP ? smsCode.trim() : undefined,
      })
      if (!active.current || getSession("provider").revision !== revision) return
      if (result.requiresSms || result.requiresOTP) {
        setChallenge(result)
        setSmsCode("")
      } else finish(result)
    } catch (cause) {
      if (active.current) setError(message(cause))
    } finally {
      if (active.current) setBusy(false)
    }
  }

  async function resend() {
    if (busy || cooldown || !challenge?.smsChallenge) return
    setBusy(true)
    setError("")
    try {
      const result = await anonymous<LoginData>("/users/password-login/", {
        audience: "provider", username: username.trim(), password,
        smsChallenge: challenge.smsChallenge, resend: true,
      })
      setChallenge(result)
      setSmsCode("")
    } catch (cause) {
      setError(message(cause))
    } finally {
      setBusy(false)
    }
  }

  async function switchToSms() {
    if (busy) return
    setBusy(true); setError("")
    try {
      const result = await anonymous<LoginData>("/users/password-login/", {
        audience: "provider", username: username.trim(), password, verificationMethod: "sms",
      })
      if (active.current) { setChallenge(result); setSmsCode("") }
    } catch (cause) { if (active.current) setError(message(cause)) }
    finally { if (active.current) setBusy(false) }
  }

  return <AccountLoginLayout
    storeName={store.name}
    logoUrl={storeBrandLogo(store, import.meta.env.BASE_URL)}
    entryLabel="陪玩账号入口"
    brandTo="/login"
    brandAriaLabel={`${store.name}陪玩登录`}
    title={<>专注每一次服务，<br />清楚每一份收益。</>}
    description="微信内可快捷登录，普通浏览器使用门店提供的账号密码和短信验证码。登录后进入同一个陪玩账号。"
    features={[
      { icon: <ClipboardCheck className="size-4" />, label: "接单履约" },
      { icon: <WalletCards className="size-4" />, label: "收益结算" },
      { icon: <BadgeCheck className="size-4" />, label: "本店档案" },
    ]}
    panelTitle={challenge ? (challenge.requiresOTP ? "验证动态验证码" : "验证手机号") : "陪玩账号登录"}
    panelDescription={challenge ? (challenge.requiresOTP ? "请输入验证器动态验证码，也可切换短信验证。" : `验证码已发送至 ${challenge.maskedPhone}`) : "账号密码登录需要完成身份验证。"}
    headerActions={<Button type="button" variant="ghost" size="icon" className="account-login-theme-toggle size-11 border-transparent bg-transparent shadow-none hover:bg-transparent active:bg-transparent dark:hover:bg-transparent" aria-label={dark ? "切换浅色" : "切换深色"} title="切换主题" onClick={() => {
      document.documentElement.classList.toggle("dark", !dark)
      setDark(!dark)
      try { localStorage.setItem("xa_fronted_provider_theme", !dark ? "dark" : "light") } catch { /* 保留当前主题 */ }
    }}>{dark ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}</Button>}
  >
    <AccountLoginError message={error || wechat.error} />
    <form className="grid gap-4" onSubmit={submit}>
      {!challenge ? <>
        <AccountLoginField label="账号" htmlFor="username"><Input id="username" className="h-11 rounded-[8px]" autoComplete="username" placeholder="请输入陪玩账号…" value={username} disabled={busy} onChange={event => setUsername(event.target.value)} /></AccountLoginField>
        <AccountLoginField label="密码" htmlFor="password"><Input id="password" className="h-11 rounded-[8px]" autoComplete="current-password" type="password" placeholder="请输入登录密码…" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} /></AccountLoginField>
      </> : <AccountLoginField label={challenge.requiresOTP ? "动态验证码或恢复码" : "短信验证码"} htmlFor="sms-code"><div className="flex gap-2"><Input id="sms-code" className="h-11 min-w-0 flex-1 rounded-[8px]" inputMode={challenge.requiresOTP ? "text" : "numeric"} autoComplete="one-time-code" maxLength={challenge.requiresOTP ? 32 : 6} placeholder={challenge.requiresOTP ? "例如 123456 或恢复码…" : "例如 6 位短信验证码…"} value={smsCode} disabled={busy} onChange={event => setSmsCode(challenge.requiresOTP ? event.target.value : event.target.value.replace(/\D/g, ""))} />{!challenge.requiresOTP && <Button type="button" variant="outline" className="h-11 shrink-0 rounded-[8px]" disabled={busy || cooldown > 0} onClick={resend}>{cooldown > 0 ? `${cooldown} 秒` : "重新发送"}</Button>}</div></AccountLoginField>}
      <Button type="submit" className="h-12 w-full rounded-[8px]" disabled={busy}>{busy ? "验证中…" : challenge ? "验证并登录" : "下一步"}{!busy && <ArrowRight className="size-4" />}</Button>
      {challenge?.requiresOTP && <Button type="button" variant="outline" disabled={busy} onClick={switchToSms}>切换短信验证</Button>}
      {challenge && <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={() => { setChallenge(null); setSmsCode(""); setError("") }}><ArrowLeft className="size-4" />返回账号密码</Button>}
    </form>
    <div className="grid gap-3 border-t pt-4">
      {isWechatBrowser() ? <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" disabled={wechat.busy} onClick={() => wechat.start()}><MessageCircle className="size-4" />{wechat.busy ? "正在打开微信授权…" : "微信快捷登录"}</Button> : <div className="rounded-[8px] bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"><Smartphone className="mr-2 inline size-4" />微信内打开本页可使用微信快捷登录；普通浏览器请使用账号密码和短信验证码。</div>}
      {wechat.flow && <Button asChild variant="ghost" className="h-10 w-full rounded-[8px]"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权<ArrowRight className="size-4" /></a></Button>}
      <Button type="button" variant="ghost" className="h-10 w-full rounded-[8px]" disabled title="微信开放平台申请完成后开放"><QrCode className="size-4" />微信扫码登录 · 待开发</Button>
    </div>
    <div className="grid gap-2 border-t pt-4"><Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" asChild><Link to="/register"><UserRoundPlus className="size-4" />申请成为陪玩 / 查询审核</Link></Button><p className="text-center text-xs text-muted-foreground">账号或绑定有问题，请联系门店管理员。</p></div>
    <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-3.5 shrink-0" />登录信息仅用于当前门店账号验证，请勿向他人透露密码和短信验证码。</p>
  </AccountLoginLayout>
}
