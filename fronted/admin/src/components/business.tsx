import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import SearchableSelect from "@/components/SearchableSelect"
import { AdminFilterContext } from "@/components/AdminFilters"
import {
  AlertCircle,
  CircleHelp,
  Gamepad2,
  Layers3,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  RefreshCw,
} from "@/components/ui/icons"
import { createRefreshCoordinator, useRealtime, useSession } from "@xa/shared"
import { useLocation } from "react-router"
import { adminRealtimeTypes, isAdminRealtimeInvalidation } from "@/lib/realtime"
import { pageIntroduction } from "@/lib/page-introductions"
import { DataValue, PageDataContext, ReadErrorNotice, TablePlaceholder, usePageDataState } from "@/components/page-data-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useTableSelection,
  type TableSelectionControl,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { type AdminSkeletonKind } from "@/components/AdminPageSkeleton"
import CatalogImage from "@/components/CatalogImage"
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { errorMessage, request, type PageResult } from "@/api/request"
import { formatDateTime } from "@/lib/format"
import { enumLabel, isEnumField } from "@/lib/labels"
import { Status } from "@/components/Status"

export type Row = Record<string, any>
const SnapshotErrorContext = createContext<string | null>(null)
export function FormSection({
  title,
  children,
  className = "",
  note,
}: {
  title: string
  children: ReactNode
  className?: string
  note?: string
}) {
  return (
    <section className={`min-w-0 rounded-xl border p-4 ${className}`}>
      <div className="mb-4">
        <h2 className="text-sm font-semibold">{title}</h2>
        {note && (
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{note}</p>
        )}
      </div>
      {children}
    </section>
  )
}
export function DraftPreview({
  title,
  subtitle,
  image,
  fields = [],
  children,
}: {
  title: string
  subtitle?: string
  image?: string
  fields?: Array<[string, ReactNode]>
  children?: ReactNode
}) {
  return (
    <aside className="sticky top-0 min-w-0 space-y-4 self-start rounded-xl border bg-muted/20 p-4">
      <p className="text-xs font-medium text-muted-foreground">展示预览</p>
      {image && (
        <CatalogImage
          src={image}
          alt="当前编辑内容预览"
          className="aspect-[16/10] w-full rounded-xl object-cover"
        />
      )}
      <div>
        <h3 className="text-xl leading-7 font-semibold tracking-tight break-words">
          {title || "未填写名称"}
        </h3>
        {subtitle && (
          <p className="mt-2 text-sm leading-6 break-words text-muted-foreground">
            {subtitle}
          </p>
        )}
      </div>
      {fields.length > 0 && (
        <dl className="space-y-3 border-t pt-4">
          {fields.map(([label, value]) => (
            <div
              key={label}
              className="flex items-start justify-between gap-4 text-xs"
            >
              <dt className="shrink-0 text-muted-foreground">{label}</dt>
              <dd className="min-w-0 text-right break-words">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
    </aside>
  )
}
export function ReadOnlyNotice({
  canEdit,
  canDelete,
  subject,
}: {
  canEdit: boolean
  canDelete?: boolean
  subject: string
}) {
  if (canEdit && canDelete !== false) return null
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-xl border bg-muted/20 px-4 py-3 text-sm"
    >
      <CircleHelp className="mt-1 size-4 shrink-0 text-muted-foreground" />
      <div>
        <p className="font-medium">
          {canEdit ? "当前可编辑，不可删除" : "当前为仅查看权限"}
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {canEdit
            ? `${subject}资料与配置可调整，删除需要独立权限。`
            : `可筛选并核对${subject}，编辑与处理操作需要对应授权。`}
        </p>
      </div>
    </div>
  )
}
export function DataPageState({
  loading,
  hasSnapshot,
  error,
  retry,
  skeleton,
  children,
}: {
  loading: boolean
  hasSnapshot: boolean
  error?: string | null
  retry: () => void
  skeleton: AdminSkeletonKind
  children: ReactNode
}) {
  return (
    <PageDataContext.Provider value={{ loading, hasSnapshot, error, retry, noticeTarget: "header" }}>
      <SnapshotErrorContext.Provider value={hasSnapshot ? error || null : null}>
        <span hidden data-admin-read-scope={skeleton} data-admin-read-state={hasSnapshot ? (loading ? "refreshing" : "ready") : loading ? "loading" : error ? "unavailable" : "unread"} />
        {children}
      </SnapshotErrorContext.Provider>
    </PageDataContext.Provider>
  )
}
export type Column = {
  key: string
  label: string
  render?: (row: Row) => ReactNode
  className?: string
  align?: "left" | "center" | "right"
  headerAlign?: "left" | "center" | "right"
}
function isActionColumn(column: Column) {
  return column.key === "actions" || column.label === "操作"
}
export function isStatusField(key: string) {
  return key === "status" || key.endsWith("_status") || ["enabled", "is_active", "connected", "configured"].includes(key)
}
export function statusFieldLabel(key: string, value: unknown) {
  const booleanLabels: Record<string, [string, string]> = {
    enabled: ["未启用", "已启用"],
    is_active: ["已停用", "已启用"],
    connected: ["未连接", "已连接"],
    configured: ["未配置", "已配置"],
    can_login: ["登录封禁", "允许登录"],
  }
  return typeof value === "boolean" ? booleanLabels[key]?.[Number(value)] : undefined
}
function columnAlignment(column: Column) {
  let alignment = "text-left"
  if (column.align) alignment = `text-${column.align}`
  else if (column.className && /\btext-(?:left|center|right)\b/.test(column.className)) alignment = ""
  else if (isActionColumn(column)) alignment = "text-right"
  else if (
    column.key === "status" ||
    /_status$/.test(column.key) ||
    ["is_active", "enabled", "visibility", "is_pinned", "is_verified", "is_default", "is_archived", "can_login", "can_view", "can_order", "available", "is_available"].includes(column.key)
  ) alignment = "text-center"
  else if (
    [
      "amount",
      "balance",
      "price",
      "stock",
      "count",
      "sort_order",
      "point_cost",
      "point_value",
      "points",
      "probability",
      "daily_limit",
      "days",
      "commission_rate",
      "discount_rate",
      "deposit_paid",
      "deposit_required",
      "rate",
      "ratio",
      "quantity",
      "game_rounds",
      "billing_provider_count",
    ].includes(column.key) ||
    /_(?:amount|balance|count|price|cost|fen|bps|rmb)$/.test(column.key)
  ) alignment = "text-right tabular-nums"
  if (isActionColumn(column)) return `${alignment} whitespace-nowrap [&>*]:justify-end`
  if (/(?:^|_)(?:id|no|code)$/.test(column.key)) return `${alignment} whitespace-nowrap tabular-nums`
  return alignment
}
export function useLiveReload(reload: () => unknown, enabled = true, types?: readonly string[]) {
  const { revision } = useSession("admin")
  const location = useLocation()
  const eventTypes = types || adminRealtimeTypes(location.pathname, location.search)
  const eventKey = eventTypes.join(",")
  const latest = useRef(reload)
  latest.current = reload
  const coordinator = useRef<ReturnType<typeof createRefreshCoordinator> | null>(null)
  useRealtime(
    "admin",
    (event) => {
      if (isAdminRealtimeInvalidation(event.type, eventTypes))
        coordinator.current?.event(event.type)
    },
    enabled
  )
  useEffect(() => {
    if (!enabled) return
    const current = createRefreshCoordinator({
      role: "admin",
      reload: async () => { await latest.current() },
      isVisible: () => !document.hidden,
      types: () => eventKey ? eventKey.split(",") : [],
    })
    coordinator.current = current
    const visible = () => {
      if (!document.hidden) current.resume()
    }
    document.addEventListener("visibilitychange", visible)
    return () => {
      current.dispose()
      if (coordinator.current === current) coordinator.current = null
      document.removeEventListener("visibilitychange", visible)
    }
  }, [enabled, revision, location.pathname, location.search, eventKey])
}
export function useLoad<T>(
  loader: () => Promise<T>,
  dependencies: unknown[] = []
) {
  const { revision } = useSession("admin")
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const currentLoader = useRef(loader)
  currentLoader.current = loader
  const sequence = useRef(0)
  const mounted = useRef(false)
  const reload = useCallback(async () => {
    const current = ++sequence.current
    if (mounted.current) {
      setLoading(true)
      setError("")
    }
    try {
      const result = await currentLoader.current()
      if (mounted.current && current === sequence.current) setData(result)
    } catch (cause) {
      if (mounted.current && current === sequence.current)
        setError(errorMessage(cause))
    } finally {
      if (mounted.current && current === sequence.current) setLoading(false)
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    setData(null)
    void reload()
    return () => {
      mounted.current = false
      ++sequence.current
    }
  }, [reload, revision, ...dependencies])
  return { data, loading, error, reload, setData }
}
export function useList(resource: string, query: Row = {}) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const key = JSON.stringify(query)
  const result = useLoad(
    async () =>
      (
        await request<PageResult>({
          url: `/${resource}/`,
          params: { ...query, page, page_size: pageSize },
        })
      ).data,
    [resource, key, page, pageSize]
  )
  useEffect(() => {
    setPage(1)
  }, [key, resource, pageSize])
  return {
    ...result,
    page,
    setPage,
    pageSize,
    setPageSize,
    rows: result.data?.list || [],
    total: result.data?.total || 0,
  }
}
export function PageHeader({
  title,
  description,
  children,
  tone = "feature",
  tag,
  metrics,
  metricsColumns,
  headingLevel = 1,
  filters,
  filterDescription,
}: {
  title: string
  description?: string
  children?: ReactNode
  tone?: "plain" | "feature"
  tag?: string
  metrics?: Array<{ label: string; value: ReactNode }>
  metricsColumns?: number
  headingLevel?: 1 | 2
  filters?: ReactNode
  filterDescription?: string
}) {
  const location = useLocation()
  const purpose = description || pageIntroduction(location.pathname, title)?.description
  const dataState = usePageDataState()
  const unresolved = !!dataState && !dataState.hasSnapshot
  const feature = tone === "feature"
  const Heading = headingLevel === 2 ? "h2" : "h1"
  return (<>
    <header
      data-admin-page-header
      className={
        feature
          ? "admin-page-header admin-feature-header overflow-hidden rounded-xl border p-6"
          : "admin-page-header flex flex-wrap items-start justify-between gap-3"
      }
    >
      <div
        className={
          feature
            ? `admin-feature-layout grid w-full gap-5 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center ${filters ? "has-global-filters" : ""}`
            : "contents"
        }
      >
        <div className={feature ? "space-y-4" : "contents"}>
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <Heading className="text-2xl font-semibold tracking-[-0.03em]">
                {title}
              </Heading>
              {tag && (
                <Badge
                  className={
                    feature
                      ? "border-white/20 bg-white/10 text-xs text-white hover:bg-white/10"
                      : "border-primary/20 bg-primary/5 text-primary"
                  }
                >
                  {tag}
                </Badge>
              )}
            </div>
            {purpose && (
              <p data-admin-page-purpose className={feature ? "admin-feature-purpose mt-2 max-w-3xl text-sm leading-6" : "mt-2 max-w-3xl text-sm leading-6 text-muted-foreground"}>
                {purpose}
              </p>
            )}
          </div>
          {children && (
            <fieldset disabled={unresolved} inert={unresolved || undefined}
              className="admin-page-actions flex shrink-0 flex-wrap items-center gap-2"
            >
              {children}
            </fieldset>
          )}
        </div>
        {(filters || (feature && metrics)) && <div className="admin-header-side">
          {filters && <div className="admin-header-filters" data-admin-global-filters>
            {filters}
            {filterDescription && <p className="admin-page-filter-description">{filterDescription}</p>}
          </div>}
        {feature && metrics && (
          <dl
            className={`grid gap-5 ${metricsColumns === 2 ? "xl:min-w-[430px]" : ""}`}
            style={{
              gridTemplateColumns: `repeat(${metricsColumns || metrics.length}, minmax(0, 1fr))`,
            }}
          >
            {metrics.map((metric) => (
              <div key={metric.label} className="border-l border-white/15 pl-4">
                <dd className="text-2xl font-semibold tabular-nums">
                  <DataValue>{metric.value}</DataValue>
                </dd>
                <dt className="admin-feature-purpose mt-1 text-xs">
                  {metric.label}
                </dt>
              </div>
            ))}
          </dl>
        )}
        </div>}
      </div>
    </header>
    {headingLevel === 1 && dataState?.noticeTarget === "header" && <ReadErrorNotice error={dataState.error} retry={dataState.retry} hasSnapshot={dataState.hasSnapshot} />}
  </>)
}
export function Feedback({
  message,
  retry,
  variant = "inline",
}: {
  message?: string | null
  retry?: () => void
  variant?: "inline" | "panel"
}) {
  const snapshotError = useContext(SnapshotErrorContext)
  const dataState = usePageDataState()
  if (!message || (dataState?.noticeTarget && message === dataState.error)) return null
  if (variant === "panel")
    return (
      <section
        role="alert"
        className="mx-auto mt-12 flex min-h-72 max-w-xl flex-col items-center justify-center rounded-xl border bg-card p-8 text-center"
      >
        <AlertCircle className="size-10 text-destructive" />
        <h2 className="mt-5 text-xl font-semibold">暂时无法读取数据</h2>
        <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
          {message}
        </p>
        {retry && (
          <Button className="mt-6" onClick={retry}>
            <RefreshCw className="size-4" />
            重新加载
          </Button>
        )}
      </section>
    )
  return (
    <div
      role="alert"
      className="flex items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm"
    >
      <AlertCircle className="size-4 shrink-0 text-destructive" />
      <span className="flex-1">
        {snapshotError === message ? (
          <>
            <strong className="block font-medium">
              刷新失败，当前保留上次读取的内容。
            </strong>
            <span className="mt-1 block text-xs text-muted-foreground">
              {message}
            </span>
          </>
        ) : (
          message
        )}
      </span>
      {retry && (
        <Button variant="outline" size="sm" onClick={retry}>
          重试
        </Button>
      )}
    </div>
  )
}
export function RefreshButton(_props: {
  loading?: boolean
  reload: () => void
}) {
  // Page-level refresh actions are hidden; initial loads and error retries remain intact.
  return null
}
export function LoadingPanel({
  variant = "list",
}: {
  variant?: "list" | "detail"
}) {
  return (
    <div className="space-y-4" role="status" aria-label="正在读取数据">
      {variant === "detail" ? (
        <>
          <Skeleton className="h-40 rounded-xl" />
          <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(280px,1fr)] gap-4">
            <Skeleton className="h-80 rounded-xl" />
            <Skeleton className="h-80 rounded-xl" />
          </div>
        </>
      ) : (
        <>
          <Skeleton className="h-28 rounded-xl" />
          <div className="space-y-3 rounded-xl border p-5">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
export function DataTable({
  rows,
  columns,
  loading,
  empty = "暂无符合条件的记录",
  emptyDescription = "请调整筛选条件，或稍后刷新查看。",
  emptyAction,
  emptyIcon: EmptyIcon,
  rowKey = "id",
  title,
  description,
  toolbar,
  minWidth,
  error,
  retry,
  titleIcon: TitleIcon,
  loadingSkeleton,
  selection: selectionControl,
  rowSelectable,
  selectionDisabled = false,
  tableClassName,
  columnWidths,
  snapshotAvailable,
}: {
  rows: Row[]
  columns: Column[]
  loading?: boolean
  empty?: string
  emptyDescription?: string
  emptyAction?: ReactNode
  emptyIcon?: typeof CircleHelp
  rowKey?: string
  title?: ReactNode
  description?: ReactNode
  toolbar?: ReactNode
  minWidth?: number
  error?: string
  retry?: () => void
  titleIcon?: typeof CircleHelp
  loadingSkeleton?: ReactNode
  selection?: TableSelectionControl
  rowSelectable?: (row: Row) => boolean
  selectionDisabled?: boolean
  tableClassName?: string
  columnWidths?: Record<string, string | number>
  snapshotAvailable?: boolean
}) {
  const dataState = usePageDataState()
  const available = snapshotAvailable ?? (rows.length > 0 || dataState?.hasSnapshot || (!dataState && !loading && !error))
  const busy = loading ?? (snapshotAvailable === undefined ? dataState?.loading : false) ?? false
  const readError = error ?? (snapshotAvailable === undefined ? dataState?.error : undefined) ?? undefined
  const unresolvedError = !available ? readError : undefined
  const reload = retry ?? dataState?.retry
  const tableColumns = columns.filter(column => column.key !== "select")
  const tableSelection = useTableSelection(
    rows.flatMap((row, index) => !selectionDisabled && (!rowSelectable || rowSelectable(row)) ? [row[rowKey] ?? index] : []),
    selectionControl,
  )
  return (
    <div
      className="admin-table-panel overflow-hidden rounded-lg border bg-card shadow-sm"
      data-admin-table-state={available ? "ready" : busy ? "loading" : "unavailable"}
      aria-busy={busy || undefined}
      aria-label={typeof title === "string" ? title : "数据列表"}
    >
      {title && (
        <div className={`flex flex-wrap items-center justify-between gap-3 px-6 pt-6 ${toolbar ? "pb-4" : "pb-6"} ${toolbar || (loading && !rows.length && loadingSkeleton) ? "" : "border-b"}`}>
          <div>
            <h2 className="flex items-center gap-2 text-base font-medium">
              {TitleIcon && <TitleIcon className="size-4 text-primary" />}
              {title}
            </h2>
            {(description || !available) && (
              <p className="mt-1 text-sm text-muted-foreground">
                {!available ? (busy ? "记录与总数读取中" : "记录与总数未读取") : description}
              </p>
            )}
          </div>
        </div>
      )}
      {toolbar && <div className={`border-b px-6 pb-6 ${title ? "" : "pt-6"}`}>{toolbar}</div>}
      {busy && <p className="sr-only" role="status">{available ? "正在更新列表，保留已读取的内容。" : "正在读取列表。"}</p>}
      {readError && dataState?.error !== readError && <div className="px-4 py-3"><ReadErrorNotice error={readError} retry={reload} hasSnapshot={available} /></div>}
      {tableSelection.selectedKeys.length > 0 && <div className="flex items-center justify-between gap-3 border-b bg-primary/5 px-6 py-2 text-xs" role="status"><span>已选择 {tableSelection.selectedKeys.length} 条记录</span><Button type="button" size="sm" variant="ghost" onClick={tableSelection.clear}>清除选择</Button></div>}
      <fieldset disabled={!!readError || busy} className="min-w-0">
        {<Table className={tableClassName} style={minWidth ? { minWidth } : undefined}>
          <caption className="sr-only">{typeof title === "string" ? title : "数据列表"}</caption>
          <TableHeader>
            <TableRow>
              {tableColumns.map((column) => (
                <TableHead
                  key={column.key}
                  scope="col"
                  data-table-action={isActionColumn(column) ? true : undefined}
                  className={`h-11 px-4 text-xs font-medium whitespace-nowrap ${columnAlignment(column)} ${column.className || ""}`}
                  style={{ width: columnWidths?.[column.key], textAlign: isActionColumn(column) ? "center" : column.headerAlign }}
                >
                  {column.label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => (
              <TableRow key={row[rowKey] ?? index}>
                {tableColumns.map((column) => (
                  <TableCell
                    key={column.key}
                    data-table-action={isActionColumn(column) ? true : undefined}
                    style={{
                      width: columnWidths?.[column.key],
                      ...(columnAlignment(column).includes("tabular-nums") ? { minWidth: 96 } : {}),
                    }}
                    className={`max-w-80 px-4 py-3 text-sm [overflow-wrap:anywhere] whitespace-normal ${columnAlignment(column)} ${column.className || ""}`}
                  >
                    {isActionColumn(column) ? <div className="admin-table-actions">{column.render ? column.render(row) : textValue(row[column.key])}</div> : column.render
                      ? column.render(row)
                      : isStatusField(column.key)
                        ? <Status value={row[column.key]} label={statusFieldLabel(column.key, row[column.key])} />
                      : isEnumField(column.key) &&
                          typeof row[column.key] === "string"
                        ? enumLabel(row[column.key])
                        : textValue(row[column.key])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
            {!rows.length &&
              busy && !available &&
              Array.from({ length: 5 }, (_, index) => (
                <TableRow key={`loading-${index}`}>
                  {tableColumns.map((column) => (
                    <TableCell key={column.key} className="p-3">
                      <Skeleton className="h-6 w-full min-w-16" />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            {!rows.length && (!busy || available) && (
              <TableRow>
                <TableCell
                  colSpan={tableColumns.length}
                  className="p-4 text-center text-sm text-muted-foreground"
                >
                  <TablePlaceholder available={available} error={unresolvedError} title={empty} description={emptyDescription} icon={EmptyIcon}>
                    {!readError && emptyAction}
                  </TablePlaceholder>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>}
      </fieldset>
      {!available && !busy && reload && <div className="flex justify-center border-t py-3">
        <Button type="button" variant="outline" size="sm" onClick={reload}>重试读取本区块</Button>
      </div>}
    </div>
  )
}
export function Pagination({
  page,
  total,
  onChange,
  loading,
  pageSize = 10,
  onPageSizeChange,
  snapshotAvailable,
  maximumPage,
}: {
  page: number
  total: number
  onChange: (page: number) => void
  loading?: boolean
  pageSize?: number
  onPageSizeChange?: (value: number) => void
  snapshotAvailable?: boolean
  maximumPage?: number
}) {
  const state = usePageDataState()
  const available = snapshotAvailable ?? state?.hasSnapshot ?? true
  const busy = loading ?? (snapshotAvailable === undefined ? state?.loading : false) ?? false
  return (
    <div className="admin-pagination flex items-center justify-between text-xs text-muted-foreground">
      <span>{available ? <>共 {total.toLocaleString()} 条</> : busy ? "总数读取中" : "总数未读取"}</span>
      <div className="flex items-center gap-3">
        {onPageSizeChange ? (
          <Select name="page_size" autoComplete="off"
            value={String(pageSize)}
            disabled={busy || !available}
            onValueChange={value => onPageSizeChange(Number(value))}
          >
            <SelectTrigger aria-label="每页条数" className="h-9 w-28"><SelectValue /></SelectTrigger><SelectContent>{[10, 20, 50, 100].map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size} 条/页
              </SelectItem>
            ))}</SelectContent>
          </Select>
        ) : (
          <span>每页 {pageSize} 条</span>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !available || page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft className="size-4" />
          上一页
        </Button>
        <span>第 {available ? page : "—"} 页</span>
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !available || page * pageSize >= total || (maximumPage !== undefined && page >= maximumPage)}
          onClick={() => onChange(page + 1)}
        >
          下一页
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </div>
  )
}
export function textValue(value: any): ReactNode {
  if (value == null || value === "") return "—"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (typeof value === "object")
    return Array.isArray(value)
      ? value
          .map((item) =>
            typeof item === "object"
              ? item.name || item.nickname || item.id
              : item
          )
          .join(" / ") || "—"
      : value.name || value.nickname || value.label || "—"
  return String(value)
}
export { Status } from "@/components/Status"
export function Details({
  data,
  fields,
}: {
  data: Row
  fields: Array<[string, string]>
}) {
  const read = usePageDataState()
  return (
    <dl className="admin-detail-list grid grid-cols-2 gap-x-8 gap-y-4">
      {fields.map(([key, label]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-1 text-sm break-words">
            {key === "status" || key.endsWith("_status") || ["enabled", "is_active", "connected", "configured", "can_login"].includes(key)
              ? <Status value={data[key]} label={statusFieldLabel(key, data[key])} tone={key === "can_login" && data[key] === false ? "destructive" : undefined} readState={read && !read.hasSnapshot ? read.loading ? "loading" : read.error ? "error" : "unread" : undefined} />
              : key.endsWith("_at")
              ? formatDateTime(data[key])
              : isEnumField(key) && typeof data[key] === "string"
                ? enumLabel(data[key])
                : textValue(data[key])}
          </dd>
        </div>
      ))}
    </dl>
  )
}
export function OptionPicker({
  resource,
  endpoint,
  label,
  value,
  onChange,
  keyField = "id",
  query = {},
  disabled,
  multiple,
  presentation = "select",
  allLabel,
  onAvailabilityChange,
}: {
  resource: string
  endpoint?: string
  label: string
  value: any
  onChange: (value: any, selected?: Row) => void
  keyField?: string
  query?: Row
  disabled?: boolean
  multiple?: boolean
  presentation?: "select" | "cards"
  allLabel?: string
  onAvailabilityChange?: (ready: boolean) => void
}) {
  const filterMode = useContext(AdminFilterContext)
  const emptyLabel = allLabel ?? (filterMode ? "全部" : "请选择")
  const [keyword, setKeyword] = useState("")
  const [optionPage, setOptionPage] = useState(1)
  const searchSupported = endpoint !== "/club-rewards/"
  useEffect(
    () => setOptionPage(1),
    [resource, endpoint, keyword, JSON.stringify(query)]
  )
  const result = useLoad(
    async () =>
      (
        await request<PageResult | Row[]>({
          url: endpoint || `/options/${resource}/`,
          params: {
            ...query,
            ...(searchSupported ? { keyword } : {}),
            page: optionPage,
            page_size: 50,
          },
        })
      ).data,
    [resource, endpoint, keyword, optionPage, JSON.stringify(query)]
  )
  const availabilityCallback = useRef(onAvailabilityChange)
  availabilityCallback.current = onAvailabilityChange
  useEffect(() => {
    availabilityCallback.current?.(!result.loading && !result.error)
  }, [result.loading, result.error])
  const fetched = Array.isArray(result.data)
    ? result.data
    : result.data?.list || []
  const selected = (Array.isArray(value) ? value : value ? [value] : []).map(
    Number
  )
  const choices: Row[] = [
    ...fetched,
    ...selected
      .filter((id) => !fetched.some((row: Row) => Number(row[keyField]) === id))
      .map((id) => ({ [keyField]: id, name: `当前选择 #${id}` })),
  ]
  if (presentation === "cards")
    return (
      <section aria-label={label} className="space-y-3">
        <div className="grid grid-cols-5 gap-3">
          <button
            type="button"
            disabled={disabled || result.loading}
            aria-pressed={!value}
            onClick={() => onChange(null)}
            className={`rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent ${!value ? "border-primary ring-1 ring-primary" : ""}`}
          >
            <span className="flex items-center gap-2 text-sm font-medium">
              <Layers3 className="size-4 text-primary" />
              {emptyLabel}
            </span>
          </button>
          {choices.map((row) => (
            <button
              type="button"
              key={row[keyField]}
              disabled={disabled || result.loading}
              aria-pressed={String(value) === String(row[keyField])}
              onClick={() => onChange(row[keyField], row)}
              className={`rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent ${String(value) === String(row[keyField]) ? "border-primary ring-1 ring-primary" : ""}`}
            >
              <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                <Gamepad2 className="size-4 shrink-0 text-primary" />
                <span className="min-w-0 break-words">{row.name || row.title}</span>
              </span>
            </button>
          ))}
        </div>
        {result.loading && (
          <p className="text-xs text-muted-foreground">正在读取游戏类目…</p>
        )}
        <Feedback message={result.error} retry={result.reload} />
      </section>
    )
  return (
    <>
      <SearchableSelect
        label={label}
        choices={choices}
        keyField={keyField}
        value={value}
        onChange={onChange}
        multiple={multiple}
        disabled={disabled || !!result.error}
        loading={result.loading}
        searchSupported={searchSupported}
        onSearch={setKeyword}
        allLabel={emptyLabel}
        pagination={!Array.isArray(result.data) && Number(result.data?.total) > 50 ? {
          page: optionPage,
          total: Number(result.data?.total),
          loading: result.loading,
          onPrevious: () => setOptionPage(page => page - 1),
          onNext: () => setOptionPage(page => page + 1),
        } : undefined}
      />
      <Feedback message={result.error} retry={result.reload} />
    </>
  )
}
export function MutationDialog({
  title,
  description,
  onClose,
  onSubmit,
  children,
  submitText = "确认",
  disabled = false,
  contentClassName,
  confirmDiscard = false,
  dirty: externalDirty = false,
  readOnly = false,
  submitVariant = "default",
}: {
  title: string
  description: string
  onClose: () => void
  onSubmit: () => Promise<void>
  children?: ReactNode
  submitText?: string
  disabled?: boolean
  contentClassName?: string
  confirmDiscard?: boolean
  dirty?: boolean
  readOnly?: boolean
  submitVariant?: React.ComponentProps<typeof Button>["variant"]
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [dirty, setDirty] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)
  function requestClose() {
    if (busy) return
    if (confirmDiscard && (dirty || externalDirty)) setDiscardOpen(true)
    else onClose()
  }
  return (
    <>
      <Dialog
        open
        onOpenChange={(value) => {
          if (!value) requestClose()
        }}
      >
        <DialogContent
          className={`max-h-[92dvh] overflow-y-auto ${contentClassName || "sm:max-w-2xl"}`}
          closeDisabled={busy}
          onInteractOutside={(event) => {
            if (confirmDiscard) event.preventDefault()
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <form
            className="space-y-5"
            onInputCapture={() => {
              if (confirmDiscard) setDirty(true)
            }}
            onChangeCapture={() => {
              if (confirmDiscard) setDirty(true)
            }}
            onClickCapture={(event) => {
              if (!confirmDiscard) return
              const control = (event.target as HTMLElement).closest<HTMLButtonElement>('button[aria-pressed], button[role="radio"]')
              if (!control || control.disabled || control.closest('[data-slot="dialog-footer"]')) return
              const attribute = control.hasAttribute("aria-pressed") ? "aria-pressed" : "aria-checked"
              const previous = control.getAttribute(attribute)
              window.setTimeout(() => {
                if (control.isConnected && previous !== control.getAttribute(attribute)) setDirty(true)
              }, 0)
            }}
            onSubmit={async (event) => {
              event.preventDefault()
              if (busy || disabled || readOnly) return
              setBusy(true)
              setError("")
              try {
                await onSubmit()
                onClose()
              } catch (cause) {
                setError(errorMessage(cause))
              } finally {
                setBusy(false)
              }
            }}
          >
            <fieldset disabled={busy || readOnly} className="min-w-0 space-y-5">
              {children}
            </fieldset>
            <Feedback message={error} />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={requestClose}
              >
                返回
              </Button>
              <Button
                type="submit"
                variant={submitVariant}
                disabled={busy || disabled || readOnly}
                aria-busy={busy}
              >
                {busy && <LoaderCircle className="size-4 animate-spin" />}
                {submitText}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃当前填写内容？</AlertDialogTitle>
            <AlertDialogDescription>
              关闭后将退出本次填写。已提交请求的结果仍需在相关记录中核对，关闭不会撤销已提交的操作。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setDiscardOpen(false)
                setDirty(false)
                onClose()
              }}
            >
              放弃并关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
export type FormField = {
  key: string
  label: string
  type?:
    | "text"
    | "number"
    | "textarea"
    | "select"
    | "boolean"
    | "date"
    | "datetime-local"
    | "password"
  options?: Array<[string | number, string]>
  required?: boolean
  min?: number
  max?: number
  step?: number
  maxLength?: number
  disabled?: boolean
  hint?: string
  full?: boolean
  allowEmpty?: boolean
}
export function AdvancedFilters({
  fields,
  value,
  onChange,
}: {
  fields: FormField[]
  value: Row
  onChange: (value: Row) => void
}) {
  return (
    <details className="basis-full rounded-lg border p-3">
      <summary className="cursor-pointer text-sm font-medium">更多筛选</summary>
      <div className="mt-4 grid grid-cols-4 gap-3">
        {fields.map((field) => (
          <Field
            key={field.key}
            field={field}
            value={value[field.key]}
            onChange={(next) => onChange({ ...value, [field.key]: next })}
          />
        ))}
      </div>
    </details>
  )
}
export function Field({
  field,
  value,
  onChange,
  disabled,
}: {
  field: FormField
  value: any
  onChange: (value: any) => void
  disabled?: boolean
}) {
  const id = `field-${field.key}`
  const filterMode = useContext(AdminFilterContext)
  if (
    field.type === "datetime-local" &&
    value &&
    /Z$|[+-]\d\d:\d\d$/.test(String(value))
  ) {
    const date = new Date(value)
    if (!Number.isNaN(date.valueOf()))
      value = new Date(date.valueOf() - date.getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16)
  }
  return (
    <div className={`space-y-2 ${field.full ? "col-span-2" : ""}`}>
      <Label htmlFor={id}>
        {field.label}
        {field.required && <span className="ml-1 text-destructive">*</span>}
      </Label>
      {field.type === "textarea" ? (
        <Textarea
          id={id}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled || field.disabled}
          rows={4}
          maxLength={field.maxLength}
        />
      ) : field.type === "select" ? (
        <Select
          name={field.key}
          autoComplete="off"
          value={value == null || value === "" ? "__xa_empty__" : String(value)}
          onValueChange={next => onChange(next === "__xa_empty__" ? "" : next)}
          disabled={disabled || field.disabled}
        >
          <SelectTrigger id={id} aria-label={field.label} className="h-9 w-full"><SelectValue placeholder={filterMode ? "全部" : "请选择"} /></SelectTrigger><SelectContent>{field.allowEmpty !== false && <SelectItem value="__xa_empty__">{field.options?.find(([key]) => String(key) === "")?.[1] || (filterMode ? "全部" : "请选择")}</SelectItem>}
          {field.options?.filter(([key]) => String(key) !== "").map(([key, label]) => (
            <SelectItem key={key} value={String(key)}>
              {label}
            </SelectItem>
          ))}</SelectContent>
        </Select>
      ) : field.type === "boolean" ? (
        <div className="flex min-h-10 items-center gap-2">
          <Checkbox
            id={id}
            checked={!!value}
            onCheckedChange={(checked) => onChange(checked === true)}
            disabled={disabled || field.disabled}
          />
          <span className="text-xs text-muted-foreground">
            {value ? "启用" : "停用"}
          </span>
        </div>
      ) : (
        <Input
          id={id}
          type={field.type || "text"}
          value={value ?? ""}
          onChange={(event) =>
            onChange(
              field.type === "number"
                ? event.target.value === ""
                  ? ""
                  : Number(event.target.value)
                : event.target.value
            )
          }
          required={field.required}
          min={field.min}
          max={field.max}
          step={field.step}
          maxLength={field.maxLength}
          disabled={disabled || field.disabled}
        />
      )}
      {field.hint && (
        <p className="text-xs leading-5 text-muted-foreground">{field.hint}</p>
      )}
    </div>
  )
}
export function EditDialog({
  open,
  title,
  description,
  fields,
  initial,
  onClose,
  onSave,
  children,
}: {
  open: boolean
  title: string
  description?: string
  fields: FormField[]
  initial: Row
  onClose: () => void
  onSave: (values: Row) => Promise<void>
  children?: ReactNode
}) {
  const [values, setValues] = useState<Row>(initial)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => {
    if (open) {
      setValues({ ...initial })
      setError("")
    }
  }, [open, initial])
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (saving) return
    setSaving(true)
    setError("")
    try {
      await onSave(values)
      onClose()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && !saving) onClose()
      }}
    >
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description || "核对下列资料后保存。"}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={save} className="space-y-5">
          <div className="grid grid-cols-2 gap-4">
            {fields.map((field) => (
              <Field
                key={field.key}
                field={field}
                value={values[field.key]}
                onChange={(value) =>
                  setValues((draft) => ({ ...draft, [field.key]: value }))
                }
                disabled={saving}
              />
            ))}
          </div>
          {children}
          <Feedback message={error} />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <LoaderCircle className="size-4 animate-spin" />}保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
