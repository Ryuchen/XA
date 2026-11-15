import { createPrivateMediaRecovery, pageBounds } from "@xa/shared"
import {
  Children,
  cloneElement,
  Fragment,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react"
import { Link } from "react-router"
import {
  RefreshCw,
  ChevronLeft,
  Inbox,
  AlertCircle,
  Upload,
  Info,
  LoaderCircle,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { statuses } from "@/lib/provider"
import { cn } from "@/lib/utils"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export const Panel = ({
  children,
  className = "",
}: {
  children: ReactNode
  className?: string
}) => (
  <Card
    className={`provider-panel w-full min-w-0 gap-4 rounded-[12px] border p-4 text-base leading-[1.65] shadow-none ring-0 md:p-5 ${className}`}
  >
    {children}
  </Card>
)
export function PageHeader({
  title,
  description,
  back,
  children,
  compact = false,
}: {
  title: string
  description?: string
  back?: string
  children?: ReactNode
  compact?: boolean
}) {
  return (
    <header data-compact={compact} className={cn("provider-page-header flex flex-col items-start gap-4 sm:flex-row sm:justify-between", compact && "flex-row items-center gap-3")}>
      <div className="min-w-0">
        <h1 className={cn("text-[26px] leading-[1.3] font-semibold tracking-[-0.03em]", compact && "text-[22px]")}>
          {title}
        </h1>
        {description && (
          <p className={cn("mt-2 text-sm leading-6 text-muted-foreground", compact && "mt-1 text-xs leading-5")}>
            {description}
          </p>
        )}
      </div>
      <div className={cn("flex w-full flex-wrap items-center justify-end gap-2 sm:ml-auto sm:w-auto sm:shrink-0", compact && "w-auto flex-nowrap")}>
        {back && (
          <Button variant="outline" asChild>
            <Link to={back}>
              <ChevronLeft className="size-4" />
              返回
            </Link>
          </Button>
        )}
        {children}
      </div>
    </header>
  )
}
export function Notice({
  children,
  error = false,
  tone = "info",
}: {
  children?: ReactNode
  error?: boolean
  tone?: "info" | "success" | "warning"
}) {
  return children && !(Array.isArray(children) && !children[0]) ? (
    <div
      role={error ? "alert" : "status"}
      data-tone={error ? "error" : tone}
      className={cn(
        "flex items-start gap-2 rounded-[8px] border px-3 py-2.5",
        error ? "border-destructive/30 bg-destructive/5 text-sm text-destructive"
          : tone === "success" ? "border-success/20 bg-success/5 text-base"
          : tone === "warning" ? "border-warning/25 bg-warning/5 text-sm"
          : "border-primary/20 bg-primary/5 text-sm"
      )}
    >
      {error && <AlertCircle className="mt-0.5 size-4 shrink-0" />}
      <div className="min-w-0 break-words">{children}</div>
    </div>
  ) : null
}
export function AvailabilityControl({
  status,
  disabled = false,
  busy = false,
  onChange,
  label = "接单状态",
}: {
  status: string | null | undefined
  disabled?: boolean
  busy?: boolean
  onChange: (status: "AVAILABLE" | "OFFLINE") => void
  label?: string
}) {
  const known =
    status === "AVAILABLE" || status === "BUSY" || status === "OFFLINE"
  const text =
    status === "AVAILABLE"
      ? "正在接单"
      : status === "BUSY"
        ? "忙碌"
        : status === "OFFLINE"
          ? "休息中"
          : "状态待核对"
  return (
    <label className="flex min-h-11 items-center justify-between gap-4 rounded-[8px] bg-muted/40 px-3 sm:min-w-44">
      <span className="flex items-center gap-2 text-sm">
        <span
          aria-hidden="true"
          className={`size-2 shrink-0 rounded-full ${status === "AVAILABLE" ? "bg-success" : "bg-muted-foreground"}`}
        />
        {busy ? "更新中…" : text}
      </span>
      <Switch name={label}
        checked={status === "AVAILABLE"}
        disabled={disabled || busy || !known}
        onCheckedChange={(checked) =>
          onChange(checked ? "AVAILABLE" : "OFFLINE")
        }
        aria-label={label}
      />
    </label>
  )
}

export function PendingFeature({
  label,
  description,
  action,
}: {
  label: string
  description: string
  action?: ReactNode
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="provider-pending-feature inline-flex min-h-10 items-center gap-1.5 rounded-[8px] px-1 text-xs text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`${label}待开放，查看说明`}
        >
          <Badge variant="secondary" className="rounded-[4px]">
            待开放
          </Badge>
          <Info aria-hidden="true" className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-72 rounded-[8px] p-4">
        <h3 className="text-sm font-semibold">{label}</h3>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          {description}
        </p>
        {action && <div className="mt-3">{action}</div>}
      </PopoverContent>
    </Popover>
  )
}

export function EmptyState({
  title = "这里暂时没有内容",
  description = "新的业务记录会在这里显示。",
  action,
}: {
  title?: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-[12px] border bg-card px-4 py-10 text-center">
      <Inbox className="mx-auto size-8 text-muted-foreground" />
      <h2 className="mt-4 text-lg font-semibold">{title}</h2>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
        {description}
      </p>
      {action && <div className="mt-6 flex justify-center">{action}</div>}
    </div>
  )
}
export function LoadState({
  loading,
  error,
  empty,
  reload,
  children,
  hasData,
  emptyTitle = "这里暂时没有内容",
  emptyDescription = "新的业务记录会在这里显示。",
  emptyAction,
  emptyContent,
}: {
  loading: boolean
  error?: string
  empty?: boolean
  reload?: () => unknown
  children: ReactNode
  hasData?: boolean
  emptyTitle?: string
  emptyDescription?: string
  emptyAction?: ReactNode
  emptyContent?: ReactNode
}) {
  const retained = hasData ?? Children.toArray(children).length > 0
  if (loading && !retained)
    return (
      <div
        className="grid gap-4 md:grid-cols-2"
        role="status"
        aria-busy="true"
        aria-label="正在加载页面"
      >
        {[220, 220, 180, 180].map((height, index) => (
          <Skeleton
            key={index}
            className="w-full rounded-[12px]"
            style={{ height }}
          />
        ))}
      </div>
    )
  if (error && !retained)
    return (
      <div
        role="alert"
        className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"
      >
        <AlertCircle className="mx-auto size-8 text-destructive" />
        <h2 className="mt-4 text-xl font-semibold">页面暂时没有加载成功</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{error}</p>
        {reload && (
          <Button
            className="mt-6 h-11 rounded-[8px]"
            onClick={() => void reload()}
          >
            <RefreshCw className="size-4" />
            重新加载
          </Button>
        )}
      </div>
    )
  if (!loading && !error && empty)
    return emptyContent ?? <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} />
  return (
    <>
      {error && (
        <Notice error>
          {error}{" "}
          {reload && (
            <Button
              variant="link"
              className="h-auto p-0"
              onClick={() => void reload()}
            >
              <RefreshCw className="size-3" />
              重试
            </Button>
          )}
        </Notice>
      )}
      {loading && (
        <div
          role="status"
          className="flex items-center gap-2 py-2 text-sm text-muted-foreground"
        >
          <RefreshCw aria-hidden="true" className="size-4 animate-spin" />
          <span>正在更新…</span>
        </div>
      )}
      {children}
    </>
  )
}
export function SnapshotGate({
  loading,
  error,
  hasData,
  reload,
  children,
}: {
  loading: boolean
  error?: string
  hasData: boolean
  reload?: () => unknown
  children: ReactNode
}) {
  if (!hasData && (loading || error))
    return (
      <LoadState
        loading={loading}
        error={error}
        hasData={false}
        reload={reload}
      >
        {null}
      </LoadState>
    )
  return <>{children}</>
}

export function FilterBar<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  columns,
  disabled = false,
}: {
  items: { value: T; label: string; mobileLabel?: string; count?: number }[]
  value: T
  onChange: (value: T) => void
  ariaLabel: string
  columns?: 2 | 3 | 4 | 5 | 6
  disabled?: boolean
}) {
  const columnClasses = {
    2: "md:grid-cols-2",
    3: "md:grid-cols-3",
    4: "md:grid-cols-4",
    5: "md:grid-cols-5",
    6: "md:grid-cols-6",
  }
  const mobileColumnClasses = {
    2: "grid-cols-2", 3: "grid-cols-3", 4: "grid-cols-4", 5: "grid-cols-5", 6: "grid-cols-6",
  }
  return (
    <nav
      aria-label={ariaLabel}
      data-columns={columns}
      className="provider-filter overflow-x-auto rounded-[8px] bg-muted/60 p-1"
    >
      <div
        className={cn(
          "flex min-w-max",
          columns && "grid min-w-0",
          columns && mobileColumnClasses[columns],
          columns && columnClasses[columns]
        )}
      >
        {items.map((item) => (
          <button
            key={item.value || "all"}
            type="button"
            disabled={disabled}
            aria-pressed={value === item.value}
            onClick={() => onChange(item.value)}
            className={cn(
              "flex min-h-11 min-w-0 shrink-0 items-center justify-center gap-1.5 rounded-[6px] px-4 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 md:px-2",
              value === item.value && "bg-background text-foreground shadow-sm"
            )}
          >
            <span className="min-w-0 leading-5">{item.mobileLabel ? <><span className="sm:hidden">{item.mobileLabel}</span><span className="hidden sm:inline">{item.label}</span></> : item.label}</span>
            {item.count !== undefined && item.count > 0 && (
              <span className="min-w-5 rounded-md bg-primary/10 px-1.5 py-0.5 text-xs leading-4 text-primary">
                {item.count}
              </span>
            )}
          </button>
        ))}
      </div>
    </nav>
  )
}

export function DetailGrid({
  items,
}: {
  items: { label: string; value: ReactNode }[]
}) {
  return (
    <dl className="grid grid-cols-2 gap-3 rounded-[8px] bg-muted/40 p-3 text-xs">
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-muted-foreground">{item.label}</dt>
          <dd className="mt-1 leading-5 font-medium break-words">
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function Status({
  value,
  label,
  tone: requestedTone,
}: {
  value: string
  label?: string
  tone?: "info"
}) {
  const tone =
    requestedTone === "info"
      ? "border-primary/25 bg-primary/10 text-primary"
      : /^(COMPLETED|APPROVED|SUCCESS|SELECTED|ACTIVE|SETTLED|PAID|已认证)$/i.test(
            value
          )
        ? "border-success/25 bg-success/10 text-success"
        : /^(DRAFT|PENDING|SUBMITTED|WAITING|PROCESSING)$/i.test(value)
          ? "border-warning/30 bg-warning/10 text-warning-foreground dark:text-warning"
          : /^(REJECTED|FAILED)$/i.test(value)
            ? "border-destructive/25 bg-destructive/5 text-destructive"
            : /^(GRABBED|IN_SERVICE|IN_PROGRESS)$/i.test(value)
              ? "border-primary/25 bg-primary/10 text-primary"
              : "text-muted-foreground"
  return (
    <Badge variant="outline" className={`shrink-0 ${tone}`}>
      {label || statuses[value] || value}
    </Badge>
  )
}
export function Stat({
  label,
  value,
  note,
}: {
  label: string
  value: ReactNode
  note?: string
}) {
  return (
    <Panel className="provider-stat gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <strong className="text-2xl font-semibold tabular-nums">{value}</strong>
      {note && <span className="text-xs text-muted-foreground">{note}</span>}
    </Panel>
  )
}
export function Pager({
  page,
  total,
  size = 20,
  change,
  disabled = false,
}: {
  page: number
  total: number
  size?: number
  change: (value: number) => void
  disabled?: boolean
}) {
  const bounds = pageBounds(page, total, size)
  useEffect(() => {
    if (!disabled && page > bounds.last) change(bounds.last)
  }, [page, total, size, change, disabled])
  return bounds.visible ? (
    <div className="flex items-center justify-between gap-2 text-sm">
      <Button
        variant="outline"
        disabled={disabled || !bounds.canPrevious}
        onClick={() => change(page - 1)}
      >
        上一页
      </Button>
      <span className="text-muted-foreground">
        {page} / {bounds.last} · {total} 条
      </span>
      <Button
        variant="outline"
        disabled={disabled || page * size >= total}
        onClick={() => change(page + 1)}
      >
        下一页
      </Button>
    </div>
  ) : null
}
export function Confirm({
  open,
  title,
  description,
  children,
  busy = false,
  disabled = false,
  onClose,
  onConfirm,
  label = "确认提交",
  icon,
  confirmVariant = "default",
  contentClassName,
}: {
  open: boolean
  title: string
  description: string
  children?: ReactNode
  busy?: boolean
  disabled?: boolean
  onClose: () => void
  onConfirm: () => void
  label?: string
  icon?: ReactNode
  confirmVariant?: "default" | "destructive"
  contentClassName?: string
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && !busy) onClose()
      }}
    >
      <DialogContent
        className={cn(
          "provider-confirm max-h-[calc(100dvh-32px)] overflow-y-auto rounded-[12px] p-5 sm:max-w-sm",
          contentClassName
        )}
        showCloseButton={!busy}
        closeDisabled={busy}
      >
        <DialogHeader className="flex-row items-center gap-3">
          {icon && (
            <div className={`grid size-11 shrink-0 place-items-center rounded-[8px] ${confirmVariant === "destructive" ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"}`}>
              {icon}
            </div>
          )}
          <div className="min-w-0 space-y-2">
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </div>
        </DialogHeader>
        {children}
        <DialogFooter>
          <Button className="h-10 rounded-[8px]" variant="outline" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button className="h-10 rounded-[8px]" variant={confirmVariant} disabled={busy || disabled} aria-busy={busy} onClick={onConfirm}>
            {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
            {busy ? "处理中…" : label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
export function PrivateImage({ src, alt }: { src: string; alt: string }) {
  const recovery = useRef(createPrivateMediaRecovery("provider", src))
  const [url, setUrl] = useState(src),
    [failed, setFailed] = useState(false)
  useEffect(() => {
    recovery.current.dispose()
    recovery.current = createPrivateMediaRecovery("provider", src)
    setUrl(src)
    setFailed(false)
    return () => recovery.current.dispose()
  }, [src])
  async function renew() {
    const result = await recovery.current.recover()
    if (result.stale) return
    setUrl(result.url)
    setFailed(result.failed)
  }
  return failed ? (
    <div className="grid gap-2 rounded-md border p-3 text-xs text-muted-foreground">
      <p>{alt}暂时不可用。</p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          recovery.current.retry()
          setFailed(false)
          setUrl(src)
        }}
      >
        重新加载
      </Button>
    </div>
  ) : (
    <a href={url} target="_blank" rel="noreferrer">
      <img
        className="max-h-52 w-full rounded-lg border object-contain"
        src={url}
        alt={alt}
        width={640}
        height={360}
        onError={() => void renew()}
      />
    </a>
  )
}
export const Field = ({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) => {
  const enhance = (nodes: ReactNode): ReactNode => Children.map(nodes, child => {
    if (!isValidElement<{
      name?: string
      autoComplete?: string
      type?: string
      children?: ReactNode
    }>(child)) return child
    if (child.type === Fragment) return cloneElement(child, undefined, enhance(child.props.children))
    if (typeof child.type === "string" && !["input", "textarea", "select"].includes(child.type)) {
      return cloneElement(child, undefined, enhance(child.props.children))
    }
    const skipAutocomplete = child.type === Switch || child.type === UploadField || (child.type === "input" && ["file", "checkbox", "radio", "hidden", "button", "submit"].includes(child.props.type || ""))
    return cloneElement(child, {
      name: child.props.name ?? label,
      ...(skipAutocomplete ? {} : { autoComplete: child.props.autoComplete ?? "off" }),
    })
  })
  return (
    <label className="grid min-w-0 gap-2 text-sm font-medium">
      {label}
      {enhance(children)}
    </label>
  )
}
export function UploadField({
  className,
  onChange,
  name,
  ...props
}: Omit<ComponentProps<"input">, "type">) {
  const [selected, setSelected] = useState("")
  return (
    <span
      className={cn(
        "relative flex min-h-20 w-full items-center gap-3 rounded-[8px] border border-dashed bg-muted/30 px-4 text-left text-sm font-normal focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30",
        !props.disabled && "transition-colors hover:border-primary/50 hover:bg-accent/20 active:bg-accent/30",
        props.disabled && "opacity-50",
        className
      )}
    >
      <Upload aria-hidden="true" className="size-5 shrink-0 text-primary" />
      <span className="min-w-0 break-all">
        {selected || (props.multiple ? "选择本地文件，可多选" : "选择本地文件")}
      </span>
      <input
        {...props}
        name={name ?? props["aria-label"] ?? "file_upload"}
        type="file"
        className="absolute inset-0 size-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
        onChange={(event) => {
          setSelected(
            Array.from(event.target.files || [])
              .map((file) => file.name)
              .join("、")
          )
          onChange?.(event)
        }}
      />
    </span>
  )
}
export const selectClass =
  "h-11 w-full min-w-0 rounded-lg border border-input bg-background px-3 text-sm"
