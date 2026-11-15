import { storeBrandLogo, useCurrentStore } from "@xa/shared"
import { useEffect, useRef, useState, type FormEvent } from "react"
import {
  ArrowRight,
  Check,
  Copy,
  Eye,
  EyeOff,
  LoaderCircle,
  LockKeyhole,
  Moon,
  ShieldCheck,
  Sun,
} from "@/components/ui/icons"
import { getSession, isAdminEntry, persistSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent } from "@/components/ui/card"
import { AdminStoreSwitcher } from "@/components/AdminStoreSwitcher"
import { Feedback } from "@/components/business"
import { adminClient, errorMessage } from "@/api/request"

interface LoginResult {
  store_slug?: string
  requiresOTP?: boolean
  requiresOTPSetup?: boolean
  otpChallenge?: string
  otpSecret?: string
  otpQr?: string
  recoveryCodes?: string[]
  token?: string
  refresh?: string
  profile?: any
  owner?: { revision: number; attempt: number }
}
export default function LoginPage({
  dark,
  onThemeChange,
}: {
  dark: boolean
  onThemeChange: (value: boolean) => void
}) {
  const store = useCurrentStore()
  const alive = useRef(true)
  const attempt = useRef(0)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      ++attempt.current
    }
  }, [])
  const isCurrent = (owner: { revision: number; attempt: number }) =>
    alive.current &&
    attempt.current === owner.attempt &&
    getSession("admin").revision === owner.revision
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [challenge, setChallenge] = useState<LoginResult | null>(null)
  const [recovery, setRecovery] = useState<LoginResult | null>(null)
  const [code, setCode] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [copied, setCopied] = useState(false)
  function finish(result: LoginResult) {
    if (!result.owner || !isCurrent(result.owner))
      throw new Error("登录状态已变化，请重新输入账号密码。")
    if (!result.token || !result.refresh || !result.profile)
      throw new Error("登录验证未完成，请重新输入账号密码。")
    persistSession("admin", result)
    const next =
      new URLSearchParams(location.hash.split("?")[1] || "").get("redirect") ||
      "/dashboard"
    const destination = next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard"
    if (isAdminEntry()) {
      // A verified new login starts a new tenant document, including module
      // constants, draft controllers and sockets. Token/profile sync never does.
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
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setFieldErrors({})
    if (!username.trim() || !password) {
      setError("")
      setFieldErrors({ ...(!username.trim() ? { username: "请输入管理员账号" } : {}), ...(!password ? { password: "请输入登录密码" } : {}) })
      return
    }
    if (challenge && !code.trim()) {
      setError("")
      setFieldErrors({ code: "请输入动态验证码或备用恢复码" })
      return
    }
    if (
      challenge?.owner &&
      getSession("admin").revision !== challenge.owner.revision
    ) {
      setChallenge(null)
      setRecovery(null)
      setCode("")
      setError("登录状态已变化，请重新输入账号密码。")
      return
    }
    const owner = {
      revision: getSession("admin").revision,
      attempt: ++attempt.current,
    }
    setBusy(true)
    setError("")
    try {
      const response = await adminClient.requestEnvelope<LoginResult>(
        "/admin/auth/login",
        {
          method: "POST",
          public: true,
          data: {
            username: username.trim(),
            password,
            ...(challenge
              ? { otpChallenge: challenge.otpChallenge, otpCode: code.trim() }
              : {}),
          },
        }
      )
      if (!isCurrent(owner)) return
      const result: LoginResult = { ...response.data, owner }
      if (result.requiresOTP) {
        setChallenge(result)
        setCode("")
      } else if (result.recoveryCodes?.length) {
        setRecovery(result)
        setChallenge(null)
        setCode("")
      } else finish(result)
    } catch (cause) {
      if (isCurrent(owner)) setError(errorMessage(cause))
    } finally {
      if (alive.current && attempt.current === owner.attempt) setBusy(false)
    }
  }
  async function copyRecovery() {
    try {
      await navigator.clipboard.writeText(
        recovery?.recoveryCodes?.join("\n") || ""
      )
      setCopied(true)
    } catch {
      setError("复制失败，请选择并保存恢复码后继续。")
    }
  }
  return (
    <div className="admin-login grid min-h-screen min-w-[1366px] grid-cols-[minmax(480px,.92fr)_minmax(560px,1.08fr)] bg-background">
      <section className="admin-login-brand relative flex min-h-screen flex-col overflow-hidden bg-[#071a3d] p-12 text-white">
        <div className="admin-login-grid" aria-hidden="true" />
        <div className="flex items-center gap-3">
          <span className="grid size-11 place-items-center rounded-xl border border-white/15 bg-white/10">
            <img src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" className="size-8" />
          </span>
          <div className="min-w-0">
            <p className="text-lg font-semibold break-words">{store.name}</p>
            <p className="text-xs text-blue-100/80">运营管理后台</p>
          </div>
        </div>
        <div className="my-auto max-w-lg py-16">
          <h1 className="text-[42px] leading-tight font-semibold tracking-tight">
            让每一次服务，
            <br />
            <span className="text-sky-300">都有清晰的回响。</span>
          </h1>
          <p className="mt-6 max-w-md text-[15px] leading-7 text-blue-100/80">
            从订单履约到资金核对，将关键任务、异常状态与处理进度放在同一个工作台。
          </p>
          <div className="mt-10 grid max-w-md grid-cols-3 gap-3 text-sm">
            {[
              ["订单履约", "节点可追踪"],
              ["资金核对", "口径更清楚"],
              ["权限协作", "角色有边界"],
            ].map(([title, note]) => (
              <div
                key={title}
                className="rounded-xl border border-white/15 bg-white/5 p-4"
              >
                <ShieldCheck className="mb-3 size-4 text-sky-300" />
                <p className="font-medium">{title}</p>
                <p className="mt-1 text-xs text-blue-100/80">{note}</p>
              </div>
            ))}
          </div>
        </div>
        <p className="text-xs text-blue-100/80">{store.name} · 管理后台</p>
      </section>
      <main className="relative flex items-center justify-center px-12 py-12">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="absolute top-6 right-6"
          aria-label={dark ? "切换为浅色模式" : "切换为深色模式"}
          onClick={() => onThemeChange(!dark)}
        >
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
        <Card className="admin-login-form w-full max-w-[440px] overflow-visible border-0 bg-transparent shadow-none ring-0">
          <CardContent className="px-0">
            <span className="mb-5 grid size-12 place-items-center rounded-xl border bg-card text-primary shadow-sm"><LockKeyhole className="size-5" /></span>
            <h2 className="text-2xl font-semibold tracking-tight">
              {recovery
                ? "保存备用恢复码"
                : challenge?.requiresOTPSetup
                  ? "绑定身份验证器"
                  : challenge
                    ? "验证身份"
                    : "欢迎回来"}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {recovery
                ? "恢复码只显示一次，每枚只能使用一次。"
                : challenge
                  ? `正在验证账号 ${username}`
                  : `登录${store.name}运营管理后台`}
            </p>

            {store.slug ? <section className="mt-6 rounded-xl border bg-card p-4" aria-label="当前登录门店"><div className="flex items-center justify-between gap-3"><div className="min-w-0"><p className="text-xs text-muted-foreground">当前登录门店</p><p className="mt-1 truncate text-sm font-semibold">{store.name}</p></div><AdminStoreSwitcher /></div><p className="mt-3 border-t pt-3 text-xs leading-5 text-muted-foreground">请使用当前门店授权的后台账号登录。</p></section> : <section className="mt-6 rounded-xl border bg-card p-4" aria-label="平台登录"><p className="text-sm font-medium">按账号识别所属门店</p><p className="mt-2 text-xs leading-5 text-muted-foreground">完成密码与身份验证后进入账号所属门店，门店数据独立授权。</p></section>}
            <div className="mt-6 space-y-5">
              {recovery ? (
                <>
                  <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted p-4 font-mono text-sm">
                    {recovery.recoveryCodes?.map((item) => (
                      <code key={item} className="select-all">
                        {item}
                      </code>
                    ))}
                  </div>
                  <Button
                    variant="outline"
                    className="h-12 w-full"
                    onClick={copyRecovery}
                  >
                    {copied ? (
                      <Check className="size-4" />
                    ) : (
                      <Copy className="size-4" />
                    )}
                    {copied ? "已复制" : "复制恢复码"}
                  </Button>
                  <Button
                    className="h-12 w-full"
                    onClick={() => {
                      try {
                        finish(recovery)
                      } catch (cause) {
                        setError(errorMessage(cause))
                      }
                    }}
                  >
                    已保存，进入系统
                    <ArrowRight className="size-4" />
                  </Button>
                </>
              ) : (
                <form onSubmit={submit} noValidate className="space-y-5">
                  {!challenge ? (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="username">管理员账号</Label>
                        <Input
                          id="username"
                          className="h-12"
                          autoComplete="username"
                          value={username}
                          onChange={(event) => { setUsername(event.target.value); setFieldErrors(old => ({ ...old, username: "" })) }}
                          aria-invalid={!!fieldErrors.username}
                          aria-describedby={fieldErrors.username ? "admin-username-error" : undefined}
                          disabled={busy}
                          required
                          placeholder="请输入管理员账号"
                          autoFocus
                        />
                        {fieldErrors.username && <p id="admin-username-error" role="alert" className="text-xs text-destructive">{fieldErrors.username}</p>}
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="password">登录密码</Label>
                        <div className="relative">
                          <Input
                            id="password"
                            type={showPassword ? "text" : "password"}
                            autoComplete="current-password"
                            value={password}
                            onChange={(event) =>
                              { setPassword(event.target.value); setFieldErrors(old => ({ ...old, password: "" })) }
                            }
                            aria-invalid={!!fieldErrors.password}
                            aria-describedby={fieldErrors.password ? "admin-password-error" : undefined}
                            disabled={busy}
                            required
                            placeholder="请输入登录密码"
                            className="h-12 pr-12"
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="absolute top-1 right-1 size-10"
                            onClick={() => setShowPassword(!showPassword)}
                            aria-label={showPassword ? "隐藏密码" : "显示密码"}
                          >
                            {showPassword ? (
                              <EyeOff className="size-4" />
                            ) : (
                              <Eye className="size-4" />
                            )}
                          </Button>
                        </div>
                        {fieldErrors.password && <p id="admin-password-error" role="alert" className="text-xs text-destructive">{fieldErrors.password}</p>}
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="rounded-lg border bg-muted/40 p-4 text-sm leading-6">
                        {challenge.requiresOTPSetup
                          ? "使用支持 TOTP 的验证器扫描二维码，或手动输入绑定密钥，再输入当前动态验证码。"
                          : "输入验证器中的 6 位动态码，也可以使用一个尚未使用的备用恢复码。"}
                      </div>
                      {challenge.requiresOTPSetup && (
                        <>
                          {challenge.otpQr && (
                            <img
                              src={challenge.otpQr}
                              alt="身份验证器绑定二维码"
                              className="mx-auto size-44 bg-white"
                            />
                          )}
                          <div>
                            <Label>手动绑定密钥</Label>
                            <code className="mt-2 block rounded-lg border bg-muted p-3 font-mono text-sm break-all select-all">
                              {challenge.otpSecret}
                            </code>
                          </div>
                        </>
                      )}
                      <div className="space-y-2">
                        <Label htmlFor="otp">
                          {challenge.requiresOTPSetup
                            ? "动态验证码"
                            : "动态验证码 / 恢复码"}
                        </Label>
                        <Input
                          id="otp"
                          autoComplete="one-time-code"
                          inputMode={
                            challenge.requiresOTPSetup ? "numeric" : "text"
                          }
                          maxLength={20}
                          value={code}
                          onChange={(event) => { setCode(event.target.value); setFieldErrors(old => ({ ...old, code: "" })) }}
                          aria-invalid={!!fieldErrors.code}
                          aria-describedby={fieldErrors.code ? "admin-code-error" : undefined}
                          disabled={busy}
                          autoFocus
                        />
                        {fieldErrors.code && <p id="admin-code-error" role="alert" className="text-xs text-destructive">{fieldErrors.code}</p>}
                      </div>
                    </>
                  )}
                  <Feedback message={error} />
                  <Button type="submit" disabled={busy} aria-busy={busy} className="h-12 w-full">
                    {busy ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <ArrowRight className="size-4" />
                    )}
                    {busy
                      ? "正在验证"
                      : challenge
                        ? challenge.requiresOTPSetup
                          ? "确认绑定并登录"
                          : "验证并登录"
                        : "安全登录"}
                  </Button>
                  {challenge && (
                    <Button
                      type="button"
                      variant="outline"
                      className="h-12 w-full"
                      disabled={busy}
                      onClick={() => {
                        setChallenge(null)
                        setCode("")
                        setError("")
                      }}
                    >
                      返回账号密码
                    </Button>
                  )}
                </form>
              )}
              {recovery && <Feedback message={error} />}
              <p className="pt-2 text-xs leading-5 text-muted-foreground">
                仅供已开通后台权限的账号使用。请妥善保存验证器与恢复码。
              </p>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
