import { storeBrandLogo, useCurrentStore } from "@xa/shared"
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  ClipboardCheck,
  Headphones,
  LockKeyhole,
  Mic2,
  ShieldCheck,
  UserRoundPlus,
  WalletCards,
} from "lucide-react"
import { persistSession, useSession, getSession, formatRmb, SkipLink } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  FilterBar,
  Field,
  UploadField,
  Status,
  DetailGrid,
} from "@/components/provider-ui"
import {
  anonymous,
  api,
  useLoad,
  date,
  message,
  useViewGuard,
} from "@/lib/provider"

type LoginData = {
  requiresOTP?: boolean
  requiresOTPSetup?: boolean
  otpChallenge?: string
  otpSecret?: string
  otpQr?: string
  recoveryCodes?: string[]
  token?: string
  refreshToken?: string
  userInfo?: {
    id: string | number
    username?: string
    nickname?: string
    role?: string
  }
}
function AuthHeader() {
  const store = useCurrentStore()
  return (
    <header className="sticky top-0 z-30 h-16 border-b bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex h-full max-w-[1120px] items-center px-4 md:px-6">
        <Link to="/login" className="flex min-w-0 items-center gap-2 rounded-[8px]" aria-label={`${store.name}陪玩登录`}>
          <img src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" width={32} height={32} className="size-8 shrink-0" />
          <span className="min-w-0">
            <strong className="block max-w-40 truncate text-sm leading-5 font-semibold" title={store.name}>{store.name}</strong>
            <span className="block text-xs leading-4 text-muted-foreground">陪玩账号入口</span>
          </span>
        </Link>
      </div>
    </header>
  )
}
function AuthSupport() {
  const [open, setOpen] = useState(false)
  const contact = useLoad(
    () => open
      ? anonymous<{
          name: string
          tips: string
          qrcode_url?: string
          wecom_service_url?: string
        }>("/support/public-contact/")
      : Promise.resolve(null),
    [open]
  )
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" variant="ghost" className="h-11 w-full rounded-[8px] text-xs text-muted-foreground">
          <Headphones className="size-4" />账号有问题？联系客服
        </Button>
      </DialogTrigger>
      <DialogContent className="provider-confirm rounded-[12px] p-5">
        <DialogHeader>
          <DialogTitle>门店客服</DialogTitle>
          <DialogDescription>客服信息由当前门店提供，请核对账号与入驻问题。</DialogDescription>
        </DialogHeader>
        <LoadState
          loading={contact.loading}
          error={contact.error}
          hasData={contact.data != null}
          reload={() => contact.reload().catch(() => {})}
        >
          {contact.data && (
            <div className="space-y-4 text-center">
              {contact.data.qrcode_url ? (
                <img src={contact.data.qrcode_url} alt="当前门店客服二维码" width={176} height={176} className="mx-auto size-44 rounded-[8px] border bg-white p-2" />
              ) : (
                <p className="text-sm text-muted-foreground">{contact.data.name || "门店客服"}</p>
              )}
              {contact.data.tips && <p className="text-sm leading-6 whitespace-pre-wrap text-muted-foreground">{contact.data.tips}</p>}
              {contact.data.wecom_service_url && /^https:\/\//.test(contact.data.wecom_service_url) && (
                <Button asChild className="w-full rounded-[8px]">
                  <a href={contact.data.wecom_service_url} target="_blank" rel="noopener noreferrer">打开企业微信客服</a>
                </Button>
              )}
            </div>
          )}
        </LoadState>
      </DialogContent>
    </Dialog>
  )
}
function FormSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <Panel>
      <div>
        <h2 className="font-semibold">{title}</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      {children}
    </Panel>
  )
}
function LoginBenefit({ icon: Icon, label }: { icon: typeof ClipboardCheck; label: string }) {
  return <div className="flex items-center gap-2 text-sm"><Icon className="size-4 text-primary" /><span>{label}</span></div>
}
export function LoginPage() {
  const active = useRef(true),
    attempt = useRef(0),
    ownerRevision = useRef<number | null>(null)
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      attempt.current++
      ownerRevision.current = null
    }
  }, [])
  const navigate = useNavigate(),
    [params] = useSearchParams(),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [code, setCode] = useState(""),
    [step, setStep] = useState<LoginData | null>(null),
    [recovery, setRecovery] = useState<LoginData | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("")
  const destination = params.get("redirect")?.startsWith("/audition?")
    ? params.get("redirect")!
    : "/orders"
  function finish(data: LoginData) {
    if (
      !active.current ||
      ownerRevision.current === null ||
      getSession("provider").revision !== ownerRevision.current
    ) {
      if (active.current) {
        setStep(null)
        setRecovery(null)
        setError("登录状态已变化，请重新输入账号密码。")
      }
      return
    }
    if (data.userInfo?.role !== "provider") {
      setError("请使用陪玩账号登录")
      return
    }
    persistSession("provider", data)
    navigate(destination, { replace: true })
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const revision = getSession("provider").revision,
      current = ++attempt.current
    if (step && ownerRevision.current !== revision) {
      setStep(null)
      setRecovery(null)
      setError("登录状态已变化，请重新输入账号密码。")
      return
    }
    setBusy(true)
    setError("")
    try {
      const result = await anonymous<LoginData>("/users/account-login/", {
        username: username.trim(),
        password,
        otpChallenge: step?.otpChallenge,
        otpCode: code.trim(),
      })
      if (!active.current || current !== attempt.current) return
      if (getSession("provider").revision !== revision)
        throw new Error("登录状态已变化，请重新输入账号密码。")
      ownerRevision.current = revision
      if (result.requiresOTP) {
        setStep(result)
        setCode("")
      } else if (result.recoveryCodes?.length) {
        setRecovery(result)
        setStep(null)
      } else finish(result)
    } catch (cause) {
      if (active.current && current === attempt.current)
        setError(message(cause))
    } finally {
      if (active.current && current === attempt.current) setBusy(false)
    }
  }
  const store = useCurrentStore()
  return (
    <div className="min-h-dvh bg-background">
      <AuthHeader />
      <main className="mx-auto grid w-full max-w-[1120px] items-center gap-10 px-4 py-10 sm:px-6 sm:py-14 lg:min-h-[calc(100dvh-9rem)] lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-16 lg:py-16">
        <section className="max-w-xl">
          <Badge variant="secondary" className="rounded-[8px] px-2.5 py-1 font-normal">
            <ShieldCheck className="size-3.5" />账号安全登录
          </Badge>
          <h1 className="mt-5 text-3xl leading-tight font-semibold tracking-tight sm:text-4xl">
            专注每一次服务，
            <br />
            清楚每一份收益。
          </h1>
          <p className="mt-4 max-w-md text-sm leading-7 text-muted-foreground">
            使用门店提供的陪玩账号与密码登录，按账号安全设置完成动态验证。登录后可处理订单、报单与结算。
          </p>
          <div className="mt-8 hidden grid-cols-3 gap-4 border-t pt-6 lg:grid">
            <LoginBenefit icon={ClipboardCheck} label="接单履约" />
            <LoginBenefit icon={WalletCards} label="收益结算" />
            <LoginBenefit icon={BadgeCheck} label="本店档案" />
          </div>
        </section>
        <Panel className="w-full gap-5 rounded-[12px] p-5 sm:p-6 lg:max-w-[420px] lg:justify-self-end">
          <div>
            <div className="mb-4 flex size-10 items-center justify-center rounded-[8px] bg-primary/10 text-primary">
              {recovery ? <LockKeyhole className="size-5" /> : <ShieldCheck className="size-5" />}
            </div>
            <h2 className="text-xl font-semibold">
              {recovery
                ? "保存备用恢复码"
                : step?.requiresOTPSetup
                  ? "绑定验证器"
                  : step
                    ? "验证身份"
                    : "陪玩账号登录"}
            </h2>
            <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
              {recovery
                ? "恢复码仅显示一次，请妥善保存。"
                : step
                  ? "输入验证器动态码或备用恢复码。"
                  : "使用现有陪玩账号安全登录。"}
            </p>
          </div>
          <Notice error>{error}</Notice>
          {recovery ? (
            <div className="grid gap-4">
              <p className="text-sm leading-6 text-muted-foreground">恢复码可用于动态验证器不可用时登录，请保存至安全位置。</p>
              <pre className="overflow-x-auto rounded-[8px] bg-muted p-4 text-sm leading-6">
                {recovery.recoveryCodes?.join("\n")}
              </pre>
              <Button className="h-12 w-full rounded-[8px]" onClick={() => finish(recovery)}>
                已保存，进入工作台
                <ArrowRight className="size-4" />
              </Button>
            </div>
          ) : (
            <form className="grid gap-4" onSubmit={submit}>
              {!step ? (
                <>
                  <Field label="账号">
                    <Input
                      className="h-11 rounded-[8px]"
                      autoComplete="username"
                      placeholder="请输入陪玩账号…"
                      required
                      maxLength={32}
                      value={username}
                      disabled={busy}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </Field>
                  <Field label="密码">
                    <Input
                      className="h-11 rounded-[8px]"
                      autoComplete="current-password"
                      type="password"
                      placeholder="请输入登录密码…"
                      required
                      maxLength={128}
                      value={password}
                      disabled={busy}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </Field>
                </>
              ) : (
                <>
                  <div className="rounded-[8px] bg-muted px-3 py-2.5 text-sm">
                    <span className="text-muted-foreground">当前账号</span>
                    <span className="ml-2 font-medium break-all">{username}</span>
                  </div>
                  {step.requiresOTPSetup && (
                    <div className="grid gap-3">
                      <p className="text-sm leading-6 text-muted-foreground">
                        用验证器扫描二维码，或手动输入密钥。
                      </p>
                      {step.otpQr && (
                        <img
                          src={step.otpQr}
                          alt="验证器绑定二维码"
                          width={176}
                          height={176}
                          className="mx-auto size-44 rounded-[8px] border bg-white p-2"
                        />
                      )}
                      <code className="rounded-[8px] bg-muted p-3 text-sm break-all">
                        {step.otpSecret}
                      </code>
                    </div>
                  )}
                  <Field label="动态验证码">
                    <Input
                      className="h-11 rounded-[8px]"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      placeholder="例如 123456 或备用恢复码…"
                      required
                      maxLength={20}
                      autoComplete="one-time-code"
                      disabled={busy}
                    />
                  </Field>
                </>
              )}
              <Button type="submit" className="h-12 w-full rounded-[8px]" disabled={busy}>
                {busy ? "登录中…" : step ? "验证并登录" : "下一步"}
                {!busy && <ArrowRight className="size-4" />}
              </Button>
              {step && (
                <Button
                  type="button"
                  variant="outline"
                  className="h-11 w-full rounded-[8px]"
                  disabled={busy}
                  onClick={() => {
                    setStep(null)
                    setCode("")
                  }}
                >
                  <ArrowLeft className="size-4" />返回账号密码
                </Button>
              )}
            </form>
          )}
          {!recovery && (
            <div className="grid gap-2 border-t pt-4">
              <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" asChild>
                <Link to="/register"><UserRoundPlus className="size-4" />申请成为陪玩 / 查询审核</Link>
              </Button>
              <AuthSupport />
            </div>
          )}
          <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            <LockKeyhole className="mt-0.5 size-3.5 shrink-0" />
            登录信息仅用于当前门店的账号验证，请勿向他人透露密码、动态码或恢复码。
          </p>
        </Panel>
      </main>
      <footer className="mx-auto max-w-[1120px] px-4 pb-6 text-center text-xs text-muted-foreground sm:px-6">
        {store.name} · 陪玩工作台
      </footer>
    </div>
  )
}

type Rules = {
  cash_target: number
  earnings_target: number
  earnings_multiplier: number
  deposit_percent: number
  settlement_percent: number
  hold_hours: number
}
type RegistrationResult = {
  status: string
  status_display: string
  display_name: string
  review_note: string
  deposit_mode: "CASH" | "EARNINGS"
  deposit_required?: number
  level_name?: string
}
type RegistrationForm = {
  username: string
  password: string
  phone: string
  real_name: string
  id_number: string
  display_name: string
  deposit_mode: "CASH" | "EARNINGS"
}
export function RegisterPage() {
  const rules = useLoad(() =>
      anonymous<Rules>("/provider-registration/rules/")
    ),
    [mode, setMode] = useState<"apply" | "status">("apply"),
    [form, setForm] = useState<RegistrationForm>({
      username: "",
      password: "",
      phone: "",
      real_name: "",
      id_number: "",
      display_name: "",
      deposit_mode: "CASH",
    }),
    [confirmation, setConfirmation] = useState(""),
    [photos, setPhotos] = useState<Record<string, File>>({}),
    [errors, setErrors] = useState<Record<string, string>>({}),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<RegistrationResult | null>(null),
    [resubmit, setResubmit] = useState(false)
  const requiredPhotos =
    form.deposit_mode === "CASH"
      ? ["id_front", "id_back", "deposit_proof"]
      : ["id_front", "id_back"]
  const labels: Record<string, string> = {
    id_front: "身份证正面",
    id_back: "身份证反面",
    deposit_proof: "押金交付凭证",
  }
  function field(key: keyof RegistrationForm, value: string) {
    setForm((old) => ({ ...old, [key]: value }))
    if (key === "username" || key === "password") {
      setResult(null)
      if (mode === "status") setResubmit(false)
    }
  }
  function valid() {
    const errors: Record<string, string> = {}
    if (!/^[a-zA-Z0-9_]{4,32}$/.test(form.username.trim()))
      errors.username = "账号需为4至32位字母、数字或下划线"
    if (form.password.length < 8 || form.password.length > 128)
      errors.password = "密码需为8至128位"
    if (mode === "apply") {
      if (form.password !== confirmation) errors.confirm = "两次密码不一致"
      if (!/^1[3-9]\d{9}$/.test(form.phone.trim()))
        errors.phone = "请填写有效手机号"
      if (form.real_name.trim().length < 2) errors.real_name = "请填写真实姓名"
      if (!/^[1-9]\d{16}[\dXx]$/.test(form.id_number.trim()))
        errors.id_number = "请填写18位身份证号码"
      if (!form.display_name.trim()) errors.display_name = "请填写结单昵称"
      requiredPhotos.forEach((key) => {
        if (!photos[key]) errors[key] = "请上传材料"
      })
    }
    setErrors(errors)
    return !Object.keys(errors).length
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || !valid() || (mode === "apply" && !rules.data)) return
    setBusy(true)
    setError("")
    try {
      let next: RegistrationResult
      if (mode === "status")
        next = await anonymous("/provider-registration/status/", {
          username: form.username.trim(),
          password: form.password,
        })
      else {
        const data = new FormData()
        Object.entries(form).forEach(([key, value]) =>
          data.append(key, key === "password" ? value : value.trim())
        )
        requiredPhotos.forEach((key) => data.append(key, photos[key]))
        next = await anonymous(
          resubmit
            ? "/provider-registration/resubmit/"
            : "/provider-registration/",
          data
        )
        setPhotos({})
        setForm((old) => ({
          username: old.username,
          password: old.password,
          phone: "",
          real_name: "",
          id_number: "",
          display_name: "",
          deposit_mode: "CASH",
        }))
        setConfirmation("")
        setMode("status")
        setResubmit(false)
      }
      setResult(next)
    } catch (cause) {
      setError(message(cause))
      const fields = (cause as { data?: { fields?: Record<string, string[]> } })
        .data?.fields
      if (fields)
        setErrors(
          Object.fromEntries(
            Object.entries(fields).map(([key, value]) => [
              key,
              value.join("；"),
            ])
          )
        )
    } finally {
      setBusy(false)
    }
  }
  const store = useCurrentStore()
  return (
    <div className="min-h-dvh bg-background">
      <AuthHeader />
      <SkipLink />
      <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 sm:py-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {mode === "status"
                ? "申请审核进度"
                : resubmit
                  ? "补充注册资料"
                  : "陪玩入驻申请"}
            </h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {mode === "status"
                ? "使用申请时设置的账号与密码，查询本店审核结果。"
                : "提交真实资料与审核材料，审核进度可随时查询。"}
            </p>
          </div>
          <Button variant="outline" size="sm" className="shrink-0 rounded-[8px]" asChild>
            <Link to="/login"><ArrowLeft className="size-4" />返回登录</Link>
          </Button>
        </div>
        <FilterBar
          ariaLabel="入驻申请步骤"
          columns={2}
          disabled={busy}
          items={[
            { value: "apply", label: "填写申请" },
            { value: "status", label: "查询审核" },
          ]}
          value={mode}
          onChange={(value) => {
            if (busy) return
            setMode(value)
            setErrors({})
            setError("")
          }}
        />
        <div className="mt-4"><Notice error>{error}</Notice></div>
        <form onSubmit={submit} className="mt-4 grid gap-4">
          <FormSection
            title="登录信息"
            description={mode === "status" ? "账号密码仅用于查询本次入驻申请。" : "账号用于后续登录工作台与查询审核，请妥善保管。"}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              {(["username", "password"] as const).map((key) => (
                <Field
                  key={key}
                  label={key === "username" ? "登录账号" : "登录密码"}
                >
                  <Input
                    className="h-11 rounded-[8px]"
                    type={key === "password" ? "password" : "text"}
                    placeholder={key === "username" ? "4至32位字母、数字或下划线" : "8至128位密码"}
                    autoComplete={
                      key === "username"
                        ? "username"
                        : mode === "status"
                          ? "current-password"
                          : "new-password"
                    }
                    value={form[key]}
                    disabled={
                      busy || (key === "username" && resubmit && mode === "apply")
                    }
                    onChange={(e) => field(key, e.target.value)}
                  />
                  <span className="text-xs text-destructive">{errors[key]}</span>
                </Field>
              ))}
              {mode === "apply" && (
                <>
                  <Field label="确认密码">
                    <Input
                      className="h-11 rounded-[8px]"
                      type="password"
                      placeholder="再次输入登录密码…"
                      value={confirmation}
                      onChange={(e) => setConfirmation(e.target.value)}
                      autoComplete="new-password"
                    />
                    <span className="text-xs text-destructive">
                      {errors.confirm}
                    </span>
                  </Field>
                  <Field label="手机号">
                    <Input
                      className="h-11 rounded-[8px]"
                      type="tel"
                      placeholder="例如 13800138000…"
                      autoComplete="tel"
                      value={form.phone}
                      onChange={(e) => field("phone", e.target.value)}
                    />
                    <span className="text-xs text-destructive">
                      {errors.phone}
                    </span>
                  </Field>
                </>
              )}
            </div>
          </FormSection>
          {mode === "apply" && (
            <>
              <FormSection title="实名资料" description="请填写本人真实信息，结单昵称将用于订单与结算记录。">
                <div className="grid gap-4 md:grid-cols-3">
                  {(
                    [
                      ["real_name", "真实姓名", "与身份证一致"],
                      ["id_number", "身份证号码", "18位身份证号码"],
                      ["display_name", "结单昵称", "工作台展示昵称"],
                    ] as const
                  ).map(([key, label, placeholder]) => (
                    <Field key={key} label={label}>
                      <Input
                        className="h-11 rounded-[8px]"
                        placeholder={placeholder}
                        value={form[key]}
                        onChange={(e) => field(key, e.target.value)}
                      />
                      <span className="text-xs text-destructive">
                        {errors[key]}
                      </span>
                    </Field>
                  ))}
                </div>
              </FormSection>
              <FormSection title="押金方式" description="请选择本店支持的一种押金方式，提交后按审核结果办理。">
                <LoadState
                  loading={rules.loading}
                  hasData={rules.data != null}
                  error={rules.error}
                  reload={() => rules.reload().catch(() => {})}
                >
                  {rules.data && (
                    <div className="grid gap-4">
                      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="押金方式">
                        {([
                          {
                            value: "CASH" as const,
                            title: "现金缴纳",
                            amount: rules.data.cash_target,
                            description: "按门店要求缴纳现金押金，并上传交付凭证。",
                            icon: WalletCards,
                          },
                          {
                            value: "EARNINGS" as const,
                            title: "收益抵押",
                            amount: rules.data.earnings_target,
                            description: "通过订单收益逐步补足本店设置的单抵押金目标。",
                            icon: ClipboardCheck,
                          },
                        ]).map((option) => {
                          const selected = form.deposit_mode === option.value
                          const Icon = option.icon
                          return (
                            <button
                              key={option.value}
                              type="button"
                              role="radio"
                              aria-checked={selected}
                              disabled={busy}
                              onClick={() => field("deposit_mode", option.value)}
                              className={`rounded-[8px] border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected ? "border-primary bg-primary/5 ring-1 ring-primary" : "bg-background hover:border-primary/40 hover:bg-accent/20"}`}
                            >
                              <span className="flex items-start justify-between gap-3">
                                <span className="flex items-center gap-2 font-medium"><Icon className="size-4 text-primary" />{option.title}</span>
                                <span className={`flex size-4 shrink-0 items-center justify-center rounded-full border ${selected ? "border-primary" : "border-input"}`}>
                                  {selected && <span className="size-2 rounded-full bg-primary" />}
                                </span>
                              </span>
                              <strong className="mt-3 block text-xl font-semibold">¥{formatRmb(option.amount)}</strong>
                              <span className="mt-2 block text-xs leading-5 text-muted-foreground">{option.description}</span>
                            </button>
                          )
                        })}
                      </div>
                      <p className="rounded-[8px] bg-muted/60 p-3 text-xs leading-5 text-muted-foreground">
                        押金未缴足时，净收益按 {rules.data.deposit_percent}% 抵押金、{rules.data.settlement_percent}% 结款。
                        {rules.data.hold_hours > 0
                          ? `新订单收益冻结 ${rules.data.hold_hours} 小时。`
                          : "当前新订单不启用收益冻结，历史订单按创建时规则处理。"}
                      </p>
                    </div>
                  )}
                </LoadState>
              </FormSection>
              <FormSection title="审核材料" description="上传清晰、完整的本人材料，仅审核人员可查看。">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {requiredPhotos.map((key) => (
                    <Field key={key} label={labels[key]}>
                      <UploadField
                        className="min-h-32 flex-col justify-center gap-2 text-center"
                        accept="image/jpeg,image/png,image/webp"
                        disabled={busy}
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          if (!file) return
                          if (
                            !["image/jpeg", "image/png", "image/webp"].includes(
                              file.type
                            ) ||
                            file.size > 5 * 1024 * 1024
                          ) {
                            setErrors((old) => ({
                              ...old,
                              [key]: "请选择5MB以内的 JPG、PNG 或 WebP",
                            }))
                            return
                          }
                          setPhotos((old) => ({ ...old, [key]: file }))
                          setErrors((old) => ({ ...old, [key]: "" }))
                        }}
                      />
                      <span className="block text-xs leading-5 text-muted-foreground">
                        {photos[key] ? "已选择，可点击重新上传" : "JPG、PNG 或 WebP，每张最多 5MB"}
                      </span>
                      <span className="text-xs text-destructive">
                        {errors[key]}
                      </span>
                    </Field>
                  ))}
                </div>
              </FormSection>
            </>
          )}
          <Button
            type="submit"
            className="h-12 w-full rounded-[8px]"
            disabled={busy || (mode === "apply" && !rules.data)}
          >
            {busy
              ? "提交中…"
              : mode === "status"
                ? "查询审核"
                : resubmit
                  ? "补充提交"
                  : "提交入驻申请"}
            {!busy && <ArrowRight className="size-4" />}
          </Button>
        </form>
        {result && (
          <Panel className="mt-4 gap-5 rounded-[12px] border-primary/30">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <BadgeCheck className="size-5 text-primary" />
                  <h2 className="font-semibold">{result.display_name}</h2>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{store.name} · 入驻审核结果</p>
              </div>
              <Status value={result.status} label={result.status_display} />
            </div>
            <dl className="grid gap-3 rounded-[8px] bg-muted/50 p-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">押金方式</dt>
                <dd className="mt-1 font-medium">{result.deposit_mode === "CASH" ? "现金缴纳" : "收益抵押"}</dd>
              </div>
              {result.deposit_required != null && (
                <div>
                  <dt className="text-xs text-muted-foreground">押金目标</dt>
                  <dd className="mt-1 font-medium">¥{formatRmb(result.deposit_required)}</dd>
                </div>
              )}
              {result.level_name && (
                <div>
                  <dt className="text-xs text-muted-foreground">陪玩等级</dt>
                  <dd className="mt-1 font-medium">{result.level_name}</dd>
                </div>
              )}
              <div className="sm:col-span-2">
                <dt className="text-xs text-muted-foreground">审核意见</dt>
                <dd className="mt-1 leading-6">{result.review_note || "请留意审核进度。"}</dd>
              </div>
            </dl>
            {result.status === "REJECTED" && (
              <Button
                className="h-11 w-full rounded-[8px] sm:w-auto"
                onClick={() => {
                  setResubmit(true)
                  setMode("apply")
                  setForm((old) => ({
                    ...old,
                    display_name: result.display_name,
                    deposit_mode: result.deposit_mode,
                  }))
                }}
              >
                补充资料重新提交<ArrowRight className="size-4" />
              </Button>
            )}
            {result.status === "APPROVED" && (
              <Button className="h-11 w-full rounded-[8px] sm:w-auto" asChild>
                <Link to="/login">前往登录<ArrowRight className="size-4" /></Link>
              </Button>
            )}
          </Panel>
        )}
        <div className="mt-6 grid items-center gap-2 border-t pt-3 sm:grid-cols-2">
          <AuthSupport />
          <p className="flex items-start justify-center gap-2 px-3 text-xs leading-5 text-muted-foreground sm:justify-end sm:text-right">
            <LockKeyhole className="mt-0.5 size-3.5 shrink-0" />实名资料仅用于当前门店入驻审核，请勿重复提交。
          </p>
        </div>
      </main>
    </div>
  )
}

type AuditionInfo = {
  id: number
  title: string
  remark: string
  expire_at: string | null
  is_active: boolean
}
type Signup = {
  id: number
  link: number
  link_title: string
  contact: string
  game: string
  status: string
  status_display: string
  audit_remark: string
  created_at: string
}
export function AuditionPage() {
  const guardView = useViewGuard(),
    loadSequence = useRef(0)
  const [params] = useSearchParams(),
    navigate = useNavigate(),
    token = params.get("token") || "",
    role = params.get("role") || "provider",
    session = useSession("provider"),
    [info, setInfo] = useState<AuditionInfo | null>(null),
    [rows, setRows] = useState<Signup[]>([]),
    [contact, setContact] = useState(""),
    [game, setGame] = useState(""),
    [remark, setRemark] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false)
  async function load() {
    const currentView = guardView(),
      current = ++loadSequence.current
    const valid = () => currentView() && current === loadSequence.current
    setLoading(true)
    setError("")
    try {
      if (!token || role !== "provider") throw new Error("链接参数缺失或无效")
      const data = await anonymous<AuditionInfo>(
        `/audition/info?token=${encodeURIComponent(token)}&role=provider`
      )
      if (!valid()) return
      setInfo(data)
      if (!getSession("provider").token) {
        const revision = getSession("provider").revision
        try {
          const exchanged = await anonymous<LoginData>("/audition/exchange", {
            token,
            role: "provider",
          })
          if (
            valid() &&
            !getSession("provider").token &&
            getSession("provider").revision === revision
          )
            persistSession("provider", exchanged)
        } catch {
          /* 普通邀请需要登录 */
        }
      }
      if (valid() && getSession("provider").token) {
        const signups = await api<Signup[]>("/audition/my-signups")
        if (valid()) setRows(signups)
      }
    } catch (cause) {
      if (valid()) setError(message(cause))
    } finally {
      if (valid()) setLoading(false)
    }
  }
  useEffect(() => {
    void load()
  }, [token, role])
  const current = rows.find((row) => row.link === info?.id)
  async function submit(event: FormEvent) {
    event.preventDefault()
    const currentView = guardView()
    if (!session.token) {
      navigate(
        `/login?redirect=${encodeURIComponent(`/audition?token=${encodeURIComponent(token)}&role=provider`)}`
      )
      return
    }
    setBusy(true)
    try {
      await api("/audition/signup", {
        method: "POST",
        data: {
          token,
          contact: contact.trim(),
          game: game.trim(),
          remark: remark.trim(),
        },
      })
      if (!currentView()) return
      setNotice("报名已提交")
      const signups = await api<Signup[]>("/audition/my-signups")
      if (currentView()) setRows(signups)
    } catch (cause) {
      if (!currentView()) return
      setNotice(message(cause))
      void load()
    } finally {
      setBusy(false)
    }
  }
  return (
    <main id="main-content" tabIndex={-1} className="provider-public mx-auto max-w-4xl space-y-6 px-4 py-8">
      <SkipLink />
      <PageHeader title="试音报名" back={session.token ? "/mine" : "/login"} />
      <LoadState
        loading={loading}
        hasData={!!info && !error}
        error={error}
        reload={() => void load()}
        empty={!info}
        emptyTitle="暂无试音招募"
        emptyDescription="请使用有效邀请链接进入报名。"
      >
        {info && (
          <section className="rounded-[12px] border bg-card p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <span className="inline-flex items-center gap-2 text-sm text-primary">
                  <Mic2 className="size-4" />
                  试音招募
                </span>
                <h2 className="mt-3 text-xl font-semibold">{info.title}</h2>
              </div>
              <Status
                value={info.is_active ? "ACTIVE" : "CLOSED"}
                label={info.is_active ? "报名中" : "已结束"}
              />
            </div>
            <div className="mt-5">
              <DetailGrid
                items={[
                  {
                    label: "截止时间",
                    value: info.expire_at ? date(info.expire_at) : "长期有效",
                  },
                  {
                    label: "报名状态",
                    value: current?.status_display || "尚未报名",
                  },
                ]}
              />
            </div>
            <p className="mt-4 text-sm leading-6 whitespace-pre-wrap">
              {info.remark}
            </p>
          </section>
        )}
        <Notice>{notice}</Notice>
        {info && !error && (
          <Panel>
            {current ? (
              <>
                <Status value={current.status} label={current.status_display} />
                <p>{current.audit_remark || "报名已提交，等待运营审核。"}</p>
              </>
            ) : (
              <form className="grid gap-4" onSubmit={submit}>
                <Field label="联系方式">
                  <Input
                    required
                    value={contact}
                    onChange={(e) => setContact(e.target.value)}
                  />
                </Field>
                <Field label="意向游戏">
                  <Input
                    required
                    value={game}
                    onChange={(e) => setGame(e.target.value)}
                  />
                </Field>
                <Field label="备注">
                  <Textarea
                    value={remark}
                    onChange={(e) => setRemark(e.target.value)}
                  />
                </Field>
                <Button disabled={busy || !info.is_active}>
                  {session.token
                    ? busy
                      ? "提交中…"
                      : "提交报名"
                    : "登录后报名"}
                </Button>
              </form>
            )}
          </Panel>
        )}
        {rows.length > 0 && (
          <Panel>
            <h2 className="font-semibold">我的试音记录</h2>
            {rows.map((row) => (
              <div key={row.id} className="space-y-1 border-t pt-3">
                <strong>{row.link_title}</strong>
                <p className="text-sm text-muted-foreground">
                  {row.status_display} · {row.game} · {date(row.created_at)}
                </p>
                {row.audit_remark && <p>{row.audit_remark}</p>}
              </div>
            ))}
          </Panel>
        )}
      </LoadState>
    </main>
  )
}
