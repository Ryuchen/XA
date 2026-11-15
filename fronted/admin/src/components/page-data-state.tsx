import { createContext, useContext, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { AlertCircle, ClipboardList, type LucideIcon } from "@/components/ui/icons"
import { Status } from "@/components/Status"

export interface PageReadState {
  loading: boolean
  hasSnapshot: boolean
  error?: string | null
  retry?: () => void
  noticeTarget?: "header" | "region"
}

export const PageDataContext = createContext<PageReadState | null>(null)
export const usePageDataState = () => useContext(PageDataContext)

/** Shared presentation inside the original table, never a substitute page. */
export function TablePlaceholder({ available, error, title, description, icon: Icon = ClipboardList, children }: {
  available: boolean
  error?: string | null
  title: string
  description?: ReactNode
  icon?: LucideIcon
  children?: ReactNode
}) {
  const StateIcon = !available && error ? AlertCircle : Icon
  return <div className="admin-table-placeholder" role="status">
    <StateIcon className="size-6 text-muted-foreground" aria-hidden="true" />
    {available ? <p className="font-medium text-foreground">{title}</p> : <Status readState={error ? "error" : "unread"} />}
    <p className="max-w-prose text-sm leading-6 text-muted-foreground">
      {available ? description : error || "尚未取得本区块的数据，请重试读取。"}
    </p>
    {available && children && <div className="mt-1">{children}</div>}
  </div>
}

/** Display state only: never changes source values, inputs or accounting. */
export function DataValue({ children, known, loading, className = "" }: {
  children: ReactNode
  known?: boolean
  loading?: boolean
  className?: string
}) {
  const state = usePageDataState()
  const available = known ?? state?.hasSnapshot ?? true
  if (available) return <>{children}</>
  const busy = loading ?? state?.loading ?? false
  return <span className={`inline-block align-middle ${className}`} data-admin-data-value={busy ? "loading" : "unavailable"} aria-label={busy ? "正在读取" : "未读取"}>
    {busy ? <span aria-hidden="true" className="inline-block h-5 w-16 animate-pulse rounded bg-current/10 motion-reduce:animate-none" /> : "—"}
  </span>
}

export function ReadErrorNotice({ error, retry, hasSnapshot = false }: Pick<PageReadState, "error" | "retry"> & { hasSnapshot?: boolean }) {
  if (!error) return null
  return <div role="alert" className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm" data-admin-read-error>
    <div className="min-w-0 flex-1">
      <div className="flex flex-wrap items-center gap-2"><Status readState="error" /><p className="font-medium">{hasSnapshot ? "刷新失败，保留上次读取的内容。" : "数据尚未读取，请重试。"}</p></div>
      <p className="mt-1 text-xs leading-5 break-words text-muted-foreground">{error}</p>
    </div>
    {retry && <Button type="button" variant="outline" size="sm" onClick={retry}>重试</Button>}
  </div>
}

/** Place inside the real card/section. Only its data-dependent body changes. */
export function DataRegion({ children, label, loading, hasSnapshot, error, retry }: PageReadState & { children: ReactNode; label: string }) {
  const state = { loading, hasSnapshot, error, retry, noticeTarget: "region" as const }
  return <PageDataContext.Provider value={state}>
    <div data-admin-data-region={label} aria-busy={loading || undefined} className="min-w-0 space-y-3">
      <ReadErrorNotice error={error} retry={retry} hasSnapshot={hasSnapshot} />
      {hasSnapshot ? children : loading ? <div className="min-h-24 space-y-3 py-3" role="status" aria-label={`${label}读取中`}>
        <div className="h-5 w-2/3 animate-pulse rounded bg-muted motion-reduce:animate-none" />
        <div className="h-10 w-full animate-pulse rounded bg-muted motion-reduce:animate-none" />
      </div> : <div className="flex min-h-24 items-center justify-between gap-3 rounded-md bg-muted/20 p-4" role="status">
        <span className="flex items-center gap-2 text-sm text-muted-foreground">{label}<Status readState="unread" /></span>
        {!error && retry && <Button type="button" variant="outline" size="sm" onClick={retry}>重试读取</Button>}
      </div>}
    </div>
  </PageDataContext.Provider>
}
