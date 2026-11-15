import { coinText } from "@xa/shared"
import { getStoreSlug } from "@xa/shared"
import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { Link, useSearchParams, useLocation } from "react-router"
import DOMPurify from "dompurify"
import { toast } from "sonner"
import { AlertCircle, CircleHelp, ChevronRight, Headphones, ImageIcon, LoaderCircle, Search, X, ArrowLeft, type LucideIcon } from "lucide-react"
import {
  createApiClient,
  getSession,
  formatMoney,
  formatDateTime,
} from "@xa/shared"
import { Button } from "@/components/ui/button"
import { EmptySurface, PageSkeleton, pageLabel } from "./visual-states"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const GuestDataContext = createContext(false)
export function GuestDataBoundary({ children }: { children: ReactNode }) {
  return <GuestDataContext.Provider value={true}>{children}</GuestDataContext.Provider>
}
export function useGuestData() {
  return useContext(GuestDataContext)
}
export type Row = Record<string, any>
export { requestId } from "@xa/shared"
export const api = createApiClient("boss")
export function money(value: unknown) {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return "—"
  const amount = Number(value)
  return Number.isFinite(amount) ? formatMoney(amount) : "—"
}
export const date = (value: unknown) =>
  value ? formatDateTime(String(value)) : "—"
export const rows = (value: any): Row[] =>
  Array.isArray(value)
    ? value
    : value?.items || value?.results || value?.list || []
export const label = (value: string) =>
  ({
    PENDING: "待处理",
    GRABBED: "已接单",
    IN_SERVICE: "服务中",
    COMPLETED: "已完成",
    CANCELLED: "已取消",
    CONFIRMED: "已确认",
    REJECTED: "已拒绝",
    EXPIRED: "已过期",
    CONVERTED: "已成单",
    FULFILLING: "交付中",
    PAID: "已支付",
    UNPAID: "待支付确认",
    REFUNDED: "已退款",
    PARTIALLY_REFUNDED: "部分退款",
    OPEN: "处理中",
  })[value] || value
export function notifyChange() {
  window.dispatchEvent(new Event("boss:changed"))
}
export const fail = (error: any) =>
  toast.error(error?.message || "操作失败，请重试")
export const uncertain = (error: any) => !error?.status || error.status >= 500
export function useQuery() {
  const [query] = useSearchParams()
  return query
}
export const queryUrl = (path: string, query: Row) =>
  path +
  "?" +
  new URLSearchParams(
    Object.entries(query)
      .filter(([, value]) => value !== "" && value != null)
      .map(([key, value]) => [key, String(value)]),
  )
export function useLoad<T = any>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  live = true,
) {
  const guest = useGuestData()
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("")
  const [version, setVersion] = useState(0),
    generation = useRef(0),
    current = useRef(loader),
    scope = useRef([...deps])
  const scopeChanged = scope.current.length !== deps.length || deps.some((value, index) => !Object.is(value, scope.current[index]))
  current.current = guest ? () => Promise.resolve(null as T) : loader
  const reload = useCallback(() => setVersion((value) => value + 1), [])
  useEffect(() => {
    const seq = ++generation.current
    let active = true
    if (scopeChanged) { scope.current = [...deps]; setData(null) }
    setLoading(true)
    setError("")
    const read = current.current
    Promise.resolve()
      .then(read)
      .then((result) => {
        if (active && seq === generation.current) setData(result)
      })
      .catch((reason) => {
        if (active && seq === generation.current)
          setError(reason?.message || "读取失败，请重试")
      })
      .finally(() => {
        if (active && seq === generation.current) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [...deps, version])
  useEffect(() => {
    if (!live) return
    window.addEventListener("boss:changed", reload)
    const focus = () => {
      if (!document.hidden) reload()
    }
    document.addEventListener("visibilitychange", focus)
    window.addEventListener("focus", focus)
    return () => {
      window.removeEventListener("boss:changed", reload)
      document.removeEventListener("visibilitychange", focus)
      window.removeEventListener("focus", focus)
    }
  }, [reload, live])
  return { data: scopeChanged ? null : data, setData, loading: scopeChanged || loading, error: scopeChanged ? "" : error, reload }
}
export function useExpiryReload(values: unknown[], reload: () => void) {
  const times = values
      .map((value) => Date.parse(String(value || "")))
      .filter(Number.isFinite),
    next = times.length ? Math.min(...times) : null
  useEffect(() => {
    if (next == null) return
    let active = true,
      timer: number
    const schedule = () => {
      timer = window.setTimeout(
        () => {
          if (!active) return
          if (Date.now() >= next) {
            if (!document.hidden) reload()
            timer = window.setTimeout(schedule, 30000)
          } else schedule()
        },
        Math.min(86400000, Math.max(250, next - Date.now() + 250)),
      )
    }
    schedule()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [next, reload])
}
export function useAction() {
  const [busy, setBusy] = useState(false),
    [actionError, setActionError] = useState(""),
    busyRef = useRef(false)
  async function run(action: () => Promise<unknown>, success = "操作已完成") {
    if (busyRef.current) return
    if (!getSession("boss").token) {
      const error = new Error("登录后才能继续此操作")
      setActionError(error.message)
      window.dispatchEvent(new CustomEvent("boss:action-feedback", { detail: error.message }))
      throw error
    }
    busyRef.current = true
    setActionError("")
    window.dispatchEvent(new CustomEvent("boss:action-feedback", { detail: "" }))
    setBusy(true)
    try {
      const result = await action()
      if (success) toast.success(success)
      notifyChange()
      return result
    } catch (error) {
      setActionError((error as any)?.message || "操作失败，请核对后重试")
      window.dispatchEvent(new CustomEvent("boss:action-feedback", { detail: (error as any)?.message || "操作失败，请核对后重试" }))
      throw error
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }
  return { busy, run, error: actionError, clearError: () => setActionError("") }
}
export function State({ loading, error, empty, reload, children, data, compact = false, emptyAction, emptyTitle, emptyDescription, errorAction, headingLevel = 2 }: { loading?: boolean; error?: string; empty?: boolean; reload?: () => void; children: ReactNode; data?: unknown; compact?: boolean; emptyAction?: ReactNode; emptyTitle?: string; emptyDescription?: string; errorAction?: ReactNode; headingLevel?: 1 | 2 }) {
  const location = useLocation(), page = location.pathname.split("/")[1] || "home", initial = !!loading && data == null, Heading = headingLevel === 1 ? "h1" : "h2", guest = useGuestData(), guestEmpty = guest && data == null
  return <>
    {error && <div role="alert" className={"boss-state-error" + (compact || data != null ? " compact" : "")}><AlertCircle size={compact ? 20 : 32} /><div><Heading>{pageLabel(page)}暂时没有加载成功</Heading><p>{error}</p></div>{reload && <Button variant={compact ? "outline" : "default"} onClick={reload}>重新加载</Button>}{errorAction}</div>}
    {loading && (initial ? <PageSkeleton page={page} compact={compact} detail={!!new URLSearchParams(location.search).get("id")} kind={new URLSearchParams(location.search).get("kind") || ""} /> : <div role="status" className="boss-updating"><LoaderCircle size={16} className="animate-spin" />正在更新最新内容…</div>)}
    {!loading && !error && (empty || guestEmpty) && <EmptySurface page={page} action={guestEmpty ? <Button asChild><Link to={"/login?next=" + encodeURIComponent(location.pathname + location.search)}>登录后查看</Link></Button> : emptyAction} title={guestEmpty ? "登录后查看具体数据" : emptyTitle} description={guestEmpty ? `${pageLabel(page)}中的账号数据与操作仅对登录老板开放，当前不展示任何个人账户信息。` : emptyDescription} />}
    {!initial && (!error || data != null || compact) && ((!empty && !guestEmpty) || loading) && children}
  </>
}
export function ActionFeedback() {
  const [error, setError] = useState("")
  const location = useLocation()
  useEffect(() => { setError("") }, [location.key])
  useEffect(() => { const update = (event: Event) => setError((event as CustomEvent<string>).detail || ""); window.addEventListener("boss:action-feedback", update); return () => window.removeEventListener("boss:action-feedback", update) }, [])
  return error ? <div role="alert" className="boss-action-error"><AlertCircle size={20} aria-hidden="true" /><p>{error}</p><Button type="button" variant="ghost" size="icon-sm" className="size-8 shrink-0 rounded-[6px] text-inherit hover:bg-destructive/10 hover:text-destructive max-sm:size-10" aria-label="关闭操作提示" onClick={() => setError("")}><X size={16} /></Button></div> : null
}

export function HeaderSupportButton() {
  return <Button asChild variant="outline" size="icon" className="boss-header-support" aria-label="联系客服" title="联系客服"><Link to="/support"><Headphones size={20} /></Link></Button>
}
export function Title({
  title,
  description,
  action,
  support = false,
}: {
  title: string
  description?: string
  action?: ReactNode
  support?: boolean
}) {
  return (
    <header className="boss-page-title">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {(action || support) && <div className="boss-page-title-action">{action}{support && <HeaderSupportButton />}</div>}
    </header>
  )
}
export function SearchField({ value, onChange, placeholder, label }: { value: string; onChange: (value: string) => void; placeholder: string; label: string }) {
  return <div className="search-shell"><Search size={16} className="shrink-0 text-muted-foreground" /><input autoComplete="off" name={label} data-search-input aria-label={label} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} maxLength={80} />{value && <button type="button" onClick={() => onChange("")} className="grid size-10 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-accent" aria-label={"清除" + label}><X size={16} /></button>}</div>
}
export function StatusBadge({ status, children }: { status: string; children: ReactNode }) {
  const tone = ["PENDING", "UNPAID", "OPEN"].includes(status) ? "warning" : ["COMPLETED", "CONVERTED", "PAID", "CONFIRMED"].includes(status) ? "success" : ["CANCELLED", "REJECTED", "EXPIRED", "REFUNDED"].includes(status) ? "muted" : "primary"
  return <span className={"boss-status-badge boss-status-" + tone}><span aria-hidden="true" />{children}</span>
}
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="boss-back-link"><ArrowLeft size={16} />{children}</Link>
}
export function Panel({
  title,
  icon: Icon,
  number,
  note,
  children,
  className = "",
}: {
  title?: string
  icon?: LucideIcon
  number?: string
  note?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`boss-panel ${Icon ? "boss-detail-section" : ""} ${className}`}>
      {title && (number ? <div className="boss-form-section-heading"><span>{number}</span>{Icon && <Icon size={16} />}<div><h2>{title}</h2>{note && <p>{note}</p>}</div></div> : <h2>{Icon && <Icon size={16} />}{title}</h2>)}
      {children}
    </section>
  )
}
export function Explanation({ title, children, returnLabel = "我知道了", icon: Icon = CircleHelp }: { title: string; children: ReactNode; returnLabel?: string; icon?: LucideIcon }) {
  const [open, setOpen] = useState(false)
  return <><button type="button" className="boss-explanation-button" onClick={() => setOpen(true)}><Icon size={16} />{title}<ChevronRight size={16} /></button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="boss-explanation-dialog" showCloseButton={false}><DialogHeader><span className="boss-explanation-icon"><Icon size={20} /></span><DialogTitle>{title}</DialogTitle><DialogDescription asChild><div>{children}</div></DialogDescription></DialogHeader><DialogFooter><Button className="w-full" onClick={() => setOpen(false)}>{returnLabel}</Button></DialogFooter></DialogContent></Dialog></>
}
export function PendingFeature({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return <><button type="button" className="boss-pending-feature" onClick={() => setOpen(true)} aria-label={`${label}，待开放，查看说明`}>{label}<span>待开放</span></button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="boss-explanation-dialog"><DialogHeader><span className="boss-explanation-icon"><CircleHelp size={20} /></span><DialogTitle>{label} · 待开放</DialogTitle><DialogDescription asChild><div>{children}</div></DialogDescription></DialogHeader><DialogFooter><Button className="w-full" onClick={() => setOpen(false)}>返回</Button></DialogFooter></DialogContent></Dialog></>
}
export function LinkButton({
  to,
  children,
  disabled = false,
  variant = "default",
}: {
  to: string
  children: ReactNode
  disabled?: boolean
  variant?: "default" | "outline" | "secondary" | "ghost"
}) {
  return (
    <Button asChild variant={variant}>
      <Link
        to={to}
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? -1 : undefined}
        onClick={(event) => {
          if (disabled) event.preventDefault()
        }}
        className={disabled ? "pointer-events-none opacity-50" : undefined}
      >
        {children}
      </Link>
    </Button>
  )
}
export function Field({
  label: text,
  value,
  onChange,
  type = "text",
  required = false,
  disabled = false,
  hint,
  multiline = false,
  ...props
}: {
  label: string
  value: any
  onChange?: (value: string) => void
  type?: string
  required?: boolean
  disabled?: boolean
  hint?: string
  multiline?: boolean
  [key: string]: any
}) {
  const id = `boss-${text.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, "")}`
  return (
    <div className="boss-field">
      <Label htmlFor={id}>
        {text}
        {required ? " *" : ""}
      </Label>
      {multiline ? (
        <Textarea
          id={id}
          value={value ?? ""}
          onChange={(event) => onChange?.(event.target.value)}
          disabled={disabled}
          required={required}
          {...props}
        />
      ) : (
        <Input
          id={id}
          value={value ?? ""}
          onChange={(event) => onChange?.(event.target.value)}
          type={type}
          disabled={disabled}
          required={required}
          {...props}
        />
      )}
      {hint && <small>{hint}</small>}
    </div>
  )
}
export function Choice({
  label: text,
  value,
  onChange,
  options,
  disabled = false,
}: {
  label: string
  value: any
  onChange: (value: string) => void
  options: Row[]
  disabled?: boolean
}) {
  const id = useId()
  const empty = "__boss_empty_choice__"
  return (
    <div className="boss-field">
      <Label htmlFor={id}>{text}</Label>
      <Select
        name={text}
        autoComplete="off"
        value={String(value ?? "") || empty}
        onValueChange={(next) => onChange(next === empty ? "" : next)}
        disabled={disabled}
      >
        <SelectTrigger id={id} aria-label={text} className="min-h-10 w-full">
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          {options.map((item, index) => (
            <SelectItem
              key={String(item.value ?? item.id ?? index)}
              value={String(item.value ?? item.id ?? "") || empty}
            >
              {item.label ?? item.name ?? item.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
export function RichContent({ html }: { html: string }) {
  return (
    <div
      className="boss-rich"
      dangerouslySetInnerHTML={{
        __html: DOMPurify.sanitize(html || "", {
          USE_PROFILES: { html: true },
        }),
      }}
    />
  )
}
export function Image({
  src,
  alt,
  className = "",
  width = className.includes("avatar") ? 48 : className.includes("code") ? 176 : 640,
  height = className.includes("avatar") ? 48 : className.includes("code") ? 176 : 360,
}: {
  src?: string
  alt: string
  className?: string
  width?: number
  height?: number
}) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return src && !failed ? (
    <img
      className={className}
      src={src}
      alt={alt}
      width={width}
      height={height}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  ) : (
    <div
      className={`${className} boss-image-placeholder`}
      role="img"
      aria-label={alt}
    >
      <ImageIcon size={24} strokeWidth={1.25} aria-hidden="true" /><span className="sr-only">{failed ? "图片暂不可用" : "暂无图片"}</span>
    </div>
  )
}
export function Info({ entries }: { entries: [string, any][] }) {
  return (
    <dl className="boss-info">
      {entries
        .filter(
          ([, value]) => value !== undefined && value !== null && value !== "",
        )
        .map(([title, value]) => (
          <div key={title}>
            <dt>{title}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
    </dl>
  )
}
export type ConfirmValue = {
  title: string
  description: ReactNode
  confirm?: string
  variant?: "default" | "destructive"
  action: () => Promise<unknown>
}
export function useConfirm() {
  const [confirmation, setConfirmation] = useState<ConfirmValue | null>(null),
    { busy, run, error, clearError } = useAction()
  return {
    confirm: (value: ConfirmValue) => { clearError(); setConfirmation(value) },
    dialog: (
      <Dialog
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!busy && !open) setConfirmation(null)
        }}
      >
        <DialogContent closeDisabled={busy}>
          <DialogHeader>
            <DialogTitle>{confirmation?.title}</DialogTitle>
            <DialogDescription asChild>
              <div>{confirmation?.description}</div>
            </DialogDescription>
          </DialogHeader>
          {error && <div role="alert" className="boss-action-error"><AlertCircle size={20} /><p>{error}</p></div>}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setConfirmation(null)}
            >
              返回修改
            </Button>
            <Button
              variant={confirmation?.variant || "default"}
              disabled={busy}
              aria-busy={busy}
              onClick={() => {
                if (confirmation)
                  void run(confirmation.action)
                    .then(() => setConfirmation(null))
                    .catch(() => {})
              }}
            >
              {busy && <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />}{busy ? "正在提交…" : confirmation?.confirm || "确认提交"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    ),
  }
}
export type Intent = {
  id: string
  path: string
  payload: Row
  display?: Row
  attempted?: boolean
  blocked?: boolean
}
function intentKey(scope: string) {
  const session = getSession("boss")
  if (!session?.user?.id) throw new Error("请先登录")
  return `xa:store:${getStoreSlug()}:xa:boss:web:intent:${session.user.id}:${scope}`
}
export function readIntent(scope: string): Intent | null {
  try {
    const raw = localStorage.getItem(intentKey(scope))
    if (!raw) return null
    const value = JSON.parse(raw)
    if (
      !value ||
      typeof value.id !== "string" ||
      typeof value.path !== "string" ||
      !value.payload ||
      typeof value.payload !== "object"
    )
      throw new Error("交易凭证不完整")
    return value
  } catch {
    // Keep a malformed or inaccessible credential blocked rather than losing a possibly committed write.
    return { id: "", path: "", payload: {}, blocked: true }
  }
}
export function storeIntent(scope: string, value: Intent | null) {
  const key = intentKey(scope)
  if (value) localStorage.setItem(key, JSON.stringify(value))
  else localStorage.removeItem(key)
}
export async function submitIntent(scope: string, intent: Intent) {
  const first = !intent.attempted
  intent.attempted = true
  storeIntent(scope, intent)
  try {
    const result = await api.post<any>(intent.path, {
      ...intent.payload,
      client_request_id: intent.id,
    })
    storeIntent(scope, null)
    notifyChange()
    return result
  } catch (error: any) {
    if (
      error.errorCode === "FUND_ALREADY_PROCESSED" &&
      error.fundResult?.code === 0
    ) {
      storeIntent(scope, null)
      notifyChange()
      return error.fundResult.data
    }
    if (
      scope === "redemption" &&
      ["REWARD_PRICE_CHANGED", "REWARD_TERMS_CHANGED"].includes(error.errorCode)
    ) {
      // The redemption service checks an existing claim before these errors,
      // so they prove this credential has not consumed points.
      storeIntent(scope, null)
      throw error
    }
    const code = typeof error.code === "number" ? error.code : error.status
    const definite =
      error.status !== 0 &&
      code >= 400 &&
      code < 500 &&
      ![409, 429].includes(code)
    if (first && definite) storeIntent(scope, null)
    else if (
      definite ||
      ["REQUEST_ID_CONFLICT", "FUND_RESULT_UNAVAILABLE"].includes(
        error.errorCode,
      )
    )
      storeIntent(scope, { ...intent, blocked: true })
    throw error
  }
}
export function Pending({
  intent,
  retry,
  resolve,
  busy = false,
}: {
  intent: Intent | null
  retry: () => void
  resolve?: () => void
  busy?: boolean
}) {
  const name =
      intent?.display?.name ||
      intent?.payload.expected_service?.name ||
      intent?.payload.expected_terms?.name,
    amount = intent?.display?.amount ?? intent?.payload.expected_amount,
    points = intent?.display?.point_cost ?? intent?.payload.expected_point_cost
  return intent ? (
    <div className="boss-feedback boss-pending-operation" role="alert">
      <strong>上次操作结果待核对</strong>
      {intent.blocked && <p>原操作凭证暂不可读取，请先查看已有记录或联系客服核对。</p>}
      {name && <p>原申请：{name}</p>}
      {intent.display?.details && <p>{intent.display.details}</p>}
      {amount != null && <p>原应付：{money(amount)} {coinText(" 浪花")}</p>}
      {points != null && <p>原申请消耗：{points} 积分</p>}
      <p>
        {intent.path.startsWith("/club/")
          ? "请保留原请求，核对积分和权益记录后再开始新的操作。"
          : "请保留原请求，核对订单和钱包记录后再开始新的操作。"}
      </p>
      <div className="boss-actions">
        <Button variant={resolve || intent.blocked ? "outline" : "default"} disabled={busy || !!intent.blocked} onClick={retry}>
          {busy ? "正在核对…" : "重试原请求"}
        </Button>
        {resolve && intent.id && (
          <Button variant={intent.blocked ? "outline" : "default"} disabled={busy} onClick={resolve}>
            核对原请求
          </Button>
        )}
        <Button asChild variant={intent.blocked ? "default" : "ghost"}>
          <Link
            to={
              intent.path.startsWith("/club/")
                ? "/points?tab=claims"
                : intent.path.includes("/reservations/")
                  ? "/reservations"
                  : "/orders"
            }
          >
            {intent.path.startsWith("/club/")
              ? "查看权益记录"
              : intent.path.includes("/reservations/")
                ? "查看预约"
                : "查看订单"}
          </Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/support">联系客服</Link>
        </Button>
      </div>
    </div>
  ) : null
}
