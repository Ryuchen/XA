import { AdminFilters } from "@/components/AdminFilters"
import { DataValue, PageDataContext, ReadErrorNotice, TablePlaceholder, usePageDataState } from "@/components/page-data-state"
import { coinText, coinUnit } from "@xa/shared"
import {
  AdminPageSkeleton,
  resourceSkeleton,
} from "@/components/AdminPageSkeleton"
import CatalogImage from "@/components/CatalogImage"
import CatalogRichContent from "@/components/CatalogRichContent"
import { useEffect, useState, type ReactNode } from "react"
import { Link, useSearchParams } from "react-router"
import {
  Archive,
  ArchiveRestore,
  CalendarClock,
  Eye,
  EyeOff,
  Megaphone,
  Percent,
  Search,
  Sparkles,
  Trash2,
  type LucideIcon,
} from "@/components/ui/icons"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { pendingReviews, reviewActive } from "@/lib/reviews"
import { enumLabel, isEnumField } from "@/lib/labels"
import {
  Award,
  ChevronDown,
  Layers3,
  Boxes,
  Gift,
  Images,
  FileText,
  Gamepad2,
  Plus,
} from "@/components/ui/icons"
import { toast } from "sonner"
import DOMPurify from "dompurify"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  DataPageState,
  DraftPreview,
  FormSection,
  textValue,
  ReadOnlyNotice,
  Feedback,
  LoadingPanel,
  Field,
  MutationDialog,
  OptionPicker,
  PageHeader,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLoad,
  type Column,
  type FormField,
  isStatusField,
  statusFieldLabel,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"
import QualificationFilters from "@/components/QualificationFilters"
import RichTextEditor from "@/components/RichTextEditor"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from "@/lib/format"

export interface ResourceField extends FormField {
  file?: string
  richText?: boolean
  resource?: string
  multiple?: boolean
  upload?: boolean
  optionKey?: string
  optionPath?: string
}
export interface ResourceSpec {
  canCreate?: boolean
  canDelete?: boolean
  resource: string
  title: string
  description: string
  permission: string
  fields: ResourceField[]
  columns: Column[]
  defaults: Row
  money?: string[]
  transform?: (data: Row, source: Row) => Row
  deletePermission?: string
  archived?: boolean
  extra?: ReactNode
}
const active: ResourceField = {
  key: "is_active",
  label: "启用",
  type: "boolean",
}
const sort: ResourceField = {
  key: "sort_order",
  label: "排序",
  type: "number",
  min: 0,
  step: 1,
}
const stateColumn: Column = {
  key: "is_active",
  label: "状态",
  render: (row) => (
    <Status value={row.is_active} />
  ),
}
const nameColumn: Column = {
  key: "name",
  label: "名称",
  render: (row) => (
    <div>
      <p className="font-medium">{row.name || row.title}</p>
      <p className="text-xs text-muted-foreground">
        {row.code || `#${row.id}`}
      </p>
    </div>
  ),
}
function contentSummary(value: unknown) {
  const document = new DOMParser().parseFromString(
    DOMPurify.sanitize(String(value || "")),
    "text/html"
  )
  return (
    document.body.textContent?.replace(/\s+/g, " ").trim() ||
    (document.body.querySelector("img") ? "图片内容" : "—")
  )
}

export function useSourceReviewState(prefix: string) {
  const [reviews, setReviews] = useState(pendingReviews)
  useEffect(() => {
    const update = () => setReviews(pendingReviews())
    update()
    window.addEventListener("xa:admin-reviews", update)
    window.addEventListener("storage", update)
    return () => {
      window.removeEventListener("xa:admin-reviews", update)
      window.removeEventListener("storage", update)
    }
  }, [prefix])
  const relevant = reviews.filter((item) => item.key.startsWith(prefix))
  return {
    locked: relevant.length > 0,
    sending: relevant.some((item) => reviewActive(item.key)),
  }
}

export function SourcePageHeader(props: {
  title: string
  tag?: string
  description?: string
  metrics?: Array<{ label: string; value: ReactNode }>
  children?: ReactNode
}) {
  return <PageHeader {...props} tone="feature" />
}

export function SourceDirectoryCard({
  title,
  description,
  icon: Icon,
  actions,
  filters,
  rows,
  columns,
  loading,
  error,
  locked,
  footer,
  minWidth = 1040,
  empty = "没有匹配的记录",
  emptyDescription = "请调整筛选条件，或稍后刷新查看。",
  emptyAction,
  emptyIcon: EmptyIcon = Boxes,
  clearFilters,
  retry,
  snapshotAvailable,
}: {
  title: string
  description?: ReactNode
  icon?: LucideIcon
  actions?: ReactNode
  filters?: ReactNode
  rows: Row[]
  columns: Column[]
  loading?: boolean
  error?: string
  locked?: boolean
  footer?: ReactNode
  minWidth?: number
  empty?: string
  emptyDescription?: string
  emptyAction?: ReactNode
  emptyIcon?: LucideIcon
  clearFilters?: () => void
  retry?: () => void
  snapshotAvailable?: boolean
}) {
  const parentState = usePageDataState()
  const available = snapshotAvailable ?? (rows.length > 0 || parentState?.hasSnapshot || (!parentState && !loading && !error))
  const busy = loading ?? (snapshotAvailable === undefined ? parentState?.loading : false) ?? false
  const readError = error ?? (snapshotAvailable === undefined ? parentState?.error : undefined) ?? undefined
  const reload = retry ?? parentState?.retry
  const isActionColumn = (column: Column) =>
    column.key === "actions" || column.label === "操作"
  const alignment = (column: Column) =>
    column.align ||
    (isActionColumn(column)
      ? "right"
      : ["is_active", "enabled", "status", "visibility", "is_pinned"].includes(
            column.key
          )
        ? "center"
        : /(?:amount|price|balance|stock|count|point_cost|sort_order|_fen|_bps)$/.test(
              column.key
            )
          ? "right"
          : "left")
  return (
    <PageDataContext.Provider value={{ loading: busy, hasSnapshot: available, error: readError, retry: reload, noticeTarget: "region" }}>
    <Card
      className="admin-source-directory min-w-0 overflow-hidden"
      data-admin-table-state={available ? "ready" : busy ? "loading" : "unavailable"}
      aria-busy={busy || undefined}
    >
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <div>
          <CardTitle
            role="heading"
            aria-level={2}
            className="flex items-center gap-2 text-base"
          >
            {Icon && <Icon className="size-4 text-primary" />}
            {title}
          </CardTitle>
          {(description || !available) && (
            <CardDescription className="mt-1">{available ? description : busy ? "记录与总数读取中" : "记录与总数未读取"}</CardDescription>
          )}
        </div>
        {actions && <fieldset disabled={!available} inert={!available || undefined}>{actions}</fieldset>}
      </CardHeader>
      {filters && (
        <fieldset
          disabled={busy}
          className="admin-directory-filters min-w-0 border-t p-4"
          aria-label={`${title}筛选条件`}
        >
          {filters}
        </fieldset>
      )}
      {busy && <p className="sr-only" role="status">{available ? "正在更新列表，保留已读取的内容。" : "正在读取列表。"}</p>}
      {readError && (parentState?.error !== readError || parentState.noticeTarget !== "header") && <div className="px-4 py-3"><ReadErrorNotice error={readError} retry={reload} hasSnapshot={available} /></div>}
      <fieldset disabled={busy || !!readError || locked || !available} className="min-w-0">
        <div className="border-t">
          <Table style={{ minWidth }}>
            <caption className="sr-only">{title}</caption>
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead
                    key={column.key}
                    scope="col"
                    data-table-action={isActionColumn(column) ? true : undefined}
                    className={`whitespace-nowrap ${column.className || ""}`}
                    style={{ textAlign: isActionColumn(column) ? "center" : column.headerAlign || alignment(column) }}
                  >
                    {column.label}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow key={row.id ?? index}>
                  {columns.map((column) => (
                    <TableCell
                      key={column.key}
                      data-table-action={isActionColumn(column) ? true : undefined}
                      className={`max-w-[390px] ${isActionColumn(column) ? "whitespace-nowrap [&>*]:justify-end" : "break-words whitespace-normal"} ${column.className || ""}`}
                      style={{ textAlign: alignment(column) }}
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
              {!rows.length && busy && !available && Array.from({ length: 5 }, (_, index) => <TableRow key={`loading-${index}`}>
                {columns.map(column => <TableCell key={column.key}><span className="block h-5 w-full animate-pulse rounded bg-muted motion-reduce:animate-none" /></TableCell>)}
              </TableRow>)}
              {!rows.length && (!busy || available) && <TableRow><TableCell colSpan={columns.length} className="p-4 text-center">
                <TablePlaceholder available={available} error={readError} title={empty} description={emptyDescription} icon={EmptyIcon}>
                  {!readError && (clearFilters ? <Button type="button" variant="outline" onClick={clearFilters}>清除筛选</Button> : emptyAction)}
                </TablePlaceholder>
              </TableCell></TableRow>}
            </TableBody>
          </Table>
        </div>
      </fieldset>
      {!available && !busy && reload && <div className="flex justify-center border-t p-3"><Button type="button" variant="outline" onClick={reload}>重试读取本区块</Button></div>}
      {footer && (
        <div className="admin-source-directory-footer border-t px-4 py-3">
          {footer}
        </div>
      )}
    </Card>
    </PageDataContext.Provider>
  )
}

const directoryPresentation: Record<
  string,
  {
    title: string
    tag: string
    description: string
    tableTitle: string
    icon: LucideIcon
    width: number
  }
> = {
  gameplays: {
    title: "玩法配置",
    tag: "目录与规则",
    description: "组织玩法入口、主题封面与通用履约说明。",
    tableTitle: "玩法目录",
    icon: Layers3,
    width: 1000,
  },
  "escort-levels": {
    title: "陪玩等级",
    tag: "等级与档位",
    description: "核对陪玩等级、等级抽成与接单档位。",
    tableTitle: "等级配置",
    icon: Award,
    width: 900,
  },
  announcements: {
    title: "公告管理",
    tag: "官方公告",
    description: "维护公告正文、置顶与公开状态。",
    tableTitle: "公告列表",
    icon: Megaphone,
    width: 1040,
  },
  "club-content": {
    title: "运营内容管理",
    tag: "首页、活动与规则内容",
    description: "维护活动海报、会员权益与业务规范，核对上架时间和公开状态。",
    tableTitle: "内容目录",
    icon: FileText,
    width: 1080,
  },
  "club-rewards": {
    title: "积分好礼",
    tag: "积分兑换与权益",
    description: "核对好礼、兑换积分、库存与商城展示。",
    tableTitle: "好礼目录",
    icon: Gift,
    width: 1040,
  },
  "broadcast-rules": {
    title: "播报规则",
    tag: "动态与隐私",
    description: "配置播报事件、内容模板、展示时长与昵称保护。",
    tableTitle: "自动播报规则",
    icon: Megaphone,
    width: 1040,
  },
}

function contentVisibility(row: Row) {
  if (typeof row.is_active !== "boolean") return { label: "未读取", readState: "unread" as const }
  if (!row.is_active) return { label: "已停用", tone: "muted" as const }
  if (row.starts_at && Date.parse(row.starts_at) > Date.now())
    return { label: "待上架", tone: "primary" as const }
  if (row.ends_at && Date.parse(row.ends_at) <= Date.now())
    return {
      label: row.kind === "HOT" ? "活动归档" : "已到期",
      tone: "muted" as const,
    }
  return { label: "展示中", tone: "success" as const }
}

function resourceDirectoryColumns(
  spec: ResourceSpec,
  canEdit: boolean,
  locked: boolean,
  reload: () => void
): Column[] {
  const optionLabel = (key: string, value: unknown) =>
    spec.fields
      .find((field) => field.key === key)
      ?.options?.find(([id]) => id === value)?.[1] || String(value || "—")
  const state = (row: Row) => (
    <Status
      value={row.is_archived ? "ARCHIVED" : row.is_active}
      label={row.is_archived ? "已归档" : row.is_active ? "已启用" : "已停用"}
      readState={row.is_archived || typeof row.is_active === "boolean" ? undefined : "unread"}
      tone={row.is_active && !row.is_archived ? "success" : "muted"}
    />
  )
  const identity = (row: Row, Icon: LucideIcon, subtitle?: ReactNode) => (
    <div className="flex items-start gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
        <Icon className="size-5" />
      </span>
      <div className="min-w-0">
        <h2 className="text-sm font-medium">{row.name || row.title}</h2>
        {subtitle}
        <p className="mt-1 text-[10px] text-muted-foreground">
          ID {row.id}
          {row.code ? ` · ${row.code}` : ""}
        </p>
      </div>
    </div>
  )
  if (spec.resource === "gameplays")
    return [
      {
        key: "name",
        label: "玩法与封面",
        render: (row) => (
          <div className="flex items-center gap-3">
            <div
              className="admin-source-gameplay-cover relative grid h-16 w-24 shrink-0 place-items-center overflow-hidden rounded-xl text-white shadow-sm"
              data-theme={row.theme}
            >
              {row.cover_url ? (
                <CatalogImage
                  src={row.cover_url}
                  alt=""
                  className="h-full w-full object-cover"
                />
              ) : (
                <>
                  <Gamepad2 className="absolute -top-3 -right-2 size-16 text-white/15" />
                  <Gamepad2 className="relative size-8" />
                </>
              )}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">{row.name}</h2>
                {row.cover_url && <Badge variant="outline">自定义封面</Badge>}
              </div>
              <p className="mt-1 max-w-64 text-xs text-muted-foreground">
                {row.subtitle || "暂无介绍"}
              </p>
              <p className="mt-1 text-[10px] text-muted-foreground">
                ID {row.id} · {row.code}
              </p>
            </div>
          </div>
        ),
      },
      {
        key: "kind",
        label: "类型",
        render: (row) => (
          <div>
            <Badge
              variant="outline"
              className={
                row.kind === "ROULETTE"
                  ? "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300"
                  : ""
              }
            >
              {row.kind === "ROULETTE" ? (
                <Sparkles className="size-3" />
              ) : (
                <Gamepad2 className="size-3" />
              )}
              {row.kind === "ROULETTE" ? "转盘单" : "普通玩法"}
            </Badge>
            {row.kind === "ROULETTE" && (
              <p className="mt-2 text-[11px] text-muted-foreground">
                商品内配置奖池
              </p>
            )}
          </div>
        ),
      },
      {
        key: "service_count",
        label: "关联商品",
        align: "right",
        render: (row) => (
          <div>
            <p className="text-sm font-semibold tabular-nums">
              {row.service_count}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {row.service_count ? "个服务商品" : "尚未关联"}
            </p>
          </div>
        ),
      },
      { key: "sort_order", label: "排序", align: "right" },
      {
        key: "is_active",
        label: "状态",
        align: "center",
        render: (row) => (
          <div>
            {state(row)}
            <p className="mt-2 text-[11px] text-muted-foreground">
              {row.is_archived
                ? "历史快照保留"
                : row.is_active
                  ? "允许新订单"
                  : "阻止新下单"}
            </p>
          </div>
        ),
      },
    ]
  if (spec.resource === "escort-levels")
    return [
      {
        key: "name",
        label: "陪玩等级",
        render: (row) =>
          identity(
            row,
            Award,
            row.remark && (
              <p className="mt-1 max-w-xl text-xs leading-5 text-muted-foreground">
                {row.remark}
              </p>
            )
          ),
      },
      {
        key: "commission_rate",
        label: "等级抽成",
        align: "right",
        render: (row) => (
          <div>
            <div className="flex items-center justify-end gap-2">
              <Percent className="size-4 text-primary" />
              <strong className="text-lg font-semibold tabular-nums">
                {row.commission_rate}%
              </strong>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              部分业务使用
            </p>
          </div>
        ),
      },
      { key: "sort_order", label: "排序", align: "right" },
      {
        key: "is_active",
        label: "状态",
        align: "center",
        render: (row) => (
          <div>
            {state(row)}
            {row.is_active === false && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                不用于新申请
              </p>
            )}
          </div>
        ),
      },
    ]
  if (spec.resource === "announcements")
    return [
      {
        key: "title",
        label: "公告标题与正文",
        render: (row) =>
          identity(
            row,
            Megaphone,
            <>
              <p className="mt-1 line-clamp-2 max-w-lg text-xs leading-5 text-muted-foreground">
                {contentSummary(row.content)}
              </p>
              {row.content && (
                <details className="mt-1 text-xs">
                  <summary className="w-fit cursor-pointer rounded text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    查看正文原文
                  </summary>
                  <CatalogRichContent
                    className="rich-document mt-2 max-h-48 overflow-y-auto rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"
                    html={row.content}
                  />
                </details>
              )}
            </>
          ),
      },
      {
        key: "is_pinned",
        label: "置顶",
        align: "center",
        render: (row) => <Status label={row.is_pinned ? "置顶公告" : "普通公告"} tone={row.is_pinned ? "primary" : "muted"} readState={typeof row.is_pinned === "boolean" ? undefined : "unread"} />,
      },
      { key: "sort_order", label: "排序", align: "right" },
      {
        key: "updated_at",
        label: "最近更新",
        className: "min-w-36 whitespace-nowrap",
        render: (row) => (
          <div>
            <p className="text-xs tabular-nums">
              {formatDateTime(row.updated_at)}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              创建 {formatDateTime(row.created_at)}
            </p>
          </div>
        ),
      },
      {
        key: "is_active",
        label: "状态",
        align: "center",
        render: (row) => (
          <ResourceToggle
            resource={spec.resource}
            row={row}
            field="is_active"
            canEdit={canEdit}
            locked={locked}
            onSaved={reload}
          />
        ),
      },
    ]
  if (spec.resource === "club-content")
    return [
      {
        key: "title",
        label: "内容",
        render: (row) => (
          <div className="flex max-w-[390px] items-center gap-3">
            {row.image_url ? (
              <CatalogImage
                src={row.image_url}
                alt=""
                width={row.kind === "HOT" ? 64 : 80}
                height={48}
                className={`shrink-0 rounded-lg border bg-[#020617] object-cover ${row.kind === "HOT" ? "h-16 w-12" : "h-12 w-20"}`}
              />
            ) : (
              <span className="grid size-12 shrink-0 place-items-center rounded-lg border bg-muted/40">
                <FileText className="size-5 text-muted-foreground" />
              </span>
            )}
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">{row.title}</h2>
              <p className="mt-1 text-[10px] text-muted-foreground">
                #{row.id} · {row.code}
              </p>
              {row.kind !== "HOT" && (
                <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                  {contentSummary(row.content)}
                </p>
              )}
            </div>
          </div>
        ),
      },
      {
        key: "kind",
        label: "展示位置",
        render: (row) => (
          <Badge variant="secondary">{optionLabel("kind", row.kind)}</Badge>
        ),
      },
      {
        key: "starts_at",
        label: "上架与结束",
        className: "min-w-40",
        render: (row) => (
          <div>
            <p className="text-xs whitespace-nowrap tabular-nums">
              {row.starts_at ? formatDateTime(row.starts_at) : "立即上架"}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {row.ends_at
                ? `${row.kind === "HOT" ? "结束" : "下架"} ${formatDateTime(row.ends_at)}`
                : "未设结束时间"}
            </p>
          </div>
        ),
      },
      {
        key: "sort_order",
        label: "排序",
        align: "right",
        render: (row) =>
          row.kind === "HOT" ? (
            <span className="text-xs text-muted-foreground">按上架时间</span>
          ) : (
            row.sort_order
          ),
      },
      {
        key: "visibility",
        label: "公开状态",
        align: "center",
        render: (row) => (
          <Status {...contentVisibility(row)} />
        ),
      },
      {
        key: "is_active",
        label: "启用",
        align: "center",
        render: (row) => (
          <ResourceToggle
            resource={spec.resource}
            row={row}
            field="is_active"
            canEdit={canEdit}
            locked={locked}
            onSaved={reload}
          />
        ),
      },
    ]
  if (spec.resource === "club-rewards")
    return [
      { key: "name", label: "好礼", render: (row) => identity(row, Gift) },
      {
        key: "kind",
        label: "交付与身份",
        render: (row) => (
          <div className="min-w-28">
            <Badge variant="secondary">
              {row.kind_label || optionLabel("kind", row.kind)}
            </Badge>
            <p className="mt-2 text-[11px] text-muted-foreground">
              {optionLabel("audience", row.audience)}
            </p>
            {row.service && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                关联服务 #{row.service}
              </p>
            )}
          </div>
        ),
      },
      {
        key: "point_cost",
        label: "兑换 / 权益",
        align: "right",
        render: (row) => (
          <div className="min-w-32">
            <p className="text-sm font-semibold tabular-nums">
              {Number(row.point_cost).toLocaleString()}{" "}
              <span className="text-[11px] font-normal text-muted-foreground">
                积分
              </span>
            </p>
            <p className="mt-2 text-[11px] text-muted-foreground">
              {row.kind === "BALANCE"
                ? `到账 ${amountToXaCoin(row.balance_amount)} ${coinUnit()}`
                : row.kind === "POINTS"
                  ? `奖励 ${row.point_value} 积分`
                  : row.duration_months
                    ? `${row.duration_months} 个自然月`
                    : row.duration_days
                      ? `${row.duration_days} 天`
                      : ["TAG", "NAMING"].includes(row.kind)
                        ? "未设到期日"
                        : "兑换后补充交付资料"}
            </p>
          </div>
        ),
      },
      {
        key: "stock",
        label: "库存 / 已兑换",
        align: "right",
        render: (row) => (
          <div className="min-w-24">
            <p
              className={`text-xs font-medium tabular-nums ${row.stock === 0 ? "text-destructive" : ""}`}
            >
              {row.stock == null
                ? "不限库存"
                : row.stock === 0
                  ? "已兑完"
                  : `剩余 ${row.stock} 份`}
            </p>
            <p className="mt-2 text-[11px] text-muted-foreground">
              已兑换 {row.redeemed_count}
            </p>
          </div>
        ),
      },
      {
        key: "visibility",
        label: "商城状态",
        align: "center",
        render: (row) => (
          <div>
            <div className="flex min-w-32 flex-wrap justify-center gap-2">
              <Status
                value={row.is_active}
                label={row.is_active ? "已启用" : "已停用"}
                readState={typeof row.is_active === "boolean" ? undefined : "unread"}
                tone={row.is_active ? "success" : "muted"}
              />
              <Status label={row.in_shop ? "商城展示" : "商城隐藏"} tone={row.in_shop ? "primary" : "muted"} readState={typeof row.in_shop === "boolean" ? undefined : "unread"} />
            </div>
            {(row.is_active === false ||
              row.in_shop === false ||
              row.audience === "PROVIDER") && (
              <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                老板端不可见：
                {row.is_active === false
                  ? "好礼已停用"
                  : row.in_shop === false
                    ? "未在商城展示"
                    : "仅陪玩可用"}
              </p>
            )}
          </div>
        ),
      },
      { key: "sort_order", label: "排序", align: "right" },
    ]
  if (spec.resource === "broadcast-rules")
    return [
      {
        key: "kind",
        label: "播报事件",
        render: (row) =>
          identity(
            { ...row, name: optionLabel("kind", row.kind), code: row.kind },
            Megaphone
          ),
      },
      {
        key: "template",
        label: "内容模板",
        render: (row) => (
          <p className="max-w-lg text-sm leading-6 break-words whitespace-pre-wrap">
            {row.template}
          </p>
        ),
      },
      {
        key: "visible_hours",
        label: "展示时长",
        align: "right",
        render: (row) => (
          <span className="tabular-nums">{row.visible_hours} 小时</span>
        ),
      },
      { key: "sort_order", label: "排序", align: "right" },
      {
        key: "mask_nickname",
        label: "昵称保护",
        align: "center",
        render: (row) => (
          <Status label={row.mask_nickname ? "已脱敏" : "完整昵称"} tone={row.mask_nickname ? "primary" : "muted"} readState={typeof row.mask_nickname === "boolean" ? undefined : "unread"} />
        ),
      },
      {
        key: "enabled",
        label: "状态",
        align: "center",
        render: (row) => (
          <ResourceToggle
            resource={spec.resource}
            row={row}
            field="enabled"
            canEdit={canEdit}
            locked={locked}
            onSaved={reload}
          />
        ),
      },
    ]
  return spec.columns
}

export function ResourcePage({
  spec,
  embedded = false,
}: {
  spec: ResourceSpec
  embedded?: boolean
}) {
  const [keyword, setKeyword] = useState("")
  const [query, setQuery] = useState<Row>({
    ...(spec.archived ? { is_archived: false } : {}),
  })
  const list = useList(spec.resource, query)
  const [editor, setEditor] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  const [restore, setRestore] = useState<Row | null>(null)
  const [impact, setImpact] = useState<Row | null>(null)
  const [impactLoading, setImpactLoading] = useState<number | null>(null)
  const [impactError, setImpactError] = useState("")
  const auth = useAdminAuth()
  const canEdit = auth.hasPerm(`${spec.permission}:edit`)
  const canDelete = spec.canDelete !== false && auth.hasPerm(spec.deletePermission || `${spec.permission}:delete`)
  const keywordSupported = ["promotions", "support-cards"].includes(
    spec.resource
  )
  const prototypeFilters = [
    "gameplays",
    "escort-levels",
    "announcements",
    "club-content",
    "broadcast-rules",
    "club-rewards",
  ].includes(spec.resource)
  const searchable =
    keywordSupported || (prototypeFilters && spec.resource !== "escort-levels")
  const statusSupported = [
    "promotions",
    "coupons",
    "game-categories",
    "checkin-gifts",
    "escort-levels",
  ].includes(spec.resource)
  const activeFilter =
    statusSupported || (prototypeFilters && spec.resource !== "gameplays")
  const unavailableFilters =
    spec.resource === "announcements"
      ? [
          {
            key: "is_pinned",
            label: "置顶状态",
            options: [
              ["true", "仅置顶"],
              ["false", "普通公告"],
            ],
          },
        ]
      : spec.resource === "club-rewards"
        ? spec.fields.filter((field) =>
            ["kind", "audience"].includes(field.key)
          )
        : ["gameplays", "club-content", "broadcast-rules"].includes(
              spec.resource
            )
          ? spec.fields.filter((field) => field.key === "kind")
          : []
  function open(row: Row = {}) {
    const initial = spec.resource === "boss-types" && row.id != null ? { ...row } : { ...spec.defaults, ...row }
    for (const key of spec.money || [])
      initial[key] = Number(amountToXaCoin(initial[key]))
    for (const field of spec.fields)
      if (field.type === "datetime-local" && initial[field.key]) {
        const date = new Date(initial[field.key])
        if (!Number.isNaN(date.valueOf()))
          initial[field.key] = new Date(
            date.valueOf() - date.getTimezoneOffset() * 60000
          )
            .toISOString()
            .slice(0, 16)
      }
    if (spec.resource === "achievements" && initial.metric === "amount")
      initial.target = Number(amountToXaCoin(row.target))
    setEditor(initial)
  }
  async function deleteItem(row: Row) {
    setImpactError("")
    if (spec.resource === "escort-levels") {
      setRemove(row)
      setImpact(null)
      setImpactLoading(row.id)
      try {
        setImpact(
          (
            await request({
              url: "/escort-levels/" + row.id + "/deletion-impact/",
            })
          ).data
        )
      } catch (cause: any) {
        setImpactError(
          (cause.message || "删除影响读取失败。") + " 请重新点击删除以重试。"
        )
        return
      } finally {
        setImpactLoading(null)
      }
    }
    setRemove(row)
  }
  const presentation = directoryPresentation[spec.resource]
  const directoryIcon = presentation?.icon || (spec.resource === "boss-types" ? Percent : Boxes)
  const review = useSourceReviewState(`/admin/${spec.resource}/`)
  const locked = list.loading || !!list.error || review.locked
  const hasAppliedFilters = Boolean(query.keyword?.trim()) || (query.is_active != null && query.is_active !== "")
  const emptyBossTypes = spec.resource === "boss-types" && list.data?.total === 0
  const clearFilters = () => {
    setKeyword("")
    setQuery(spec.archived ? { is_archived: !!query.is_archived } : {})
  }
  const create =
    spec.canCreate !== false && canEdit ? (
      <Button
        variant={spec.resource === "club-rewards" ? "default" : "secondary"}
        disabled={locked}
        onClick={() => open()}
      >
        <Plus className="size-4" />
        {spec.resource === "gameplays"
          ? "新增玩法"
          : spec.resource === "club-rewards"
            ? "新增好礼"
            : spec.resource === "announcements"
              ? "新增公告"
              : spec.resource === "club-content"
                ? "新增内容"
                : spec.resource === "boss-types" ? "新增老板分级" : "新增"}
      </Button>
    ) : null
  const statusFilter = (
    <Select name="status_filter" autoComplete="off"
      value={String(query.is_active ?? "") || "ALL"}
      onValueChange={(value) =>
        setQuery((old) => ({ ...old, is_active: value === "ALL" ? "" : value }))
      }
      disabled={!statusSupported}
    >
      <SelectTrigger
        id={`resource-status-${spec.resource}`}
        aria-label={statusSupported ? "状态筛选" : "状态筛选（待开放）"}
        className="w-full"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="ALL">
          {statusSupported ? "全部状态" : "全部状态 · 待开放"}
        </SelectItem>
        <SelectItem value="true">启用</SelectItem>
        <SelectItem value="false">停用</SelectItem>
      </SelectContent>
    </Select>
  )
  const filters =
    searchable ||
    unavailableFilters.length > 0 ||
    (activeFilter && spec.resource !== "escort-levels") ? (
      <form
        className="w-full"
        onSubmit={(event) => {
          event.preventDefault()
          if (keywordSupported) setQuery((old) => ({ ...old, keyword }))
        }}
      >
        <AdminFilters title="目录筛选" primary={<>
        {searchable && (
          <div className="space-y-2">
            <Label htmlFor={`resource-search-${spec.resource}`}>名称 / 标识</Label>
            <div className="relative">
            <Search className="pointer-events-none absolute top-3 left-3 size-4 text-muted-foreground" />
            <Input
              id={`resource-search-${spec.resource}`}
              aria-label={
                keywordSupported ? "名称或标识" : "名称或标识（待开放）"
              }
              className="pl-9"
              disabled={!keywordSupported}
              placeholder={
                keywordSupported ? "搜索名称或标识" : "搜索名称或标识 · 待开放"
              }
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
            </div>
          </div>
        )}
        {unavailableFilters.map((field) => (
          <div key={field.key} className="space-y-2">
          <Label htmlFor={`resource-filter-${spec.resource}-${field.key}`}>{field.label}</Label>
          <Select autoComplete="off" name="__xa_unavailable_filter__" value="__xa_unavailable_filter__" disabled>
            <SelectTrigger id={`resource-filter-${spec.resource}-${field.key}`} aria-label={`${field.label}筛选（待开放）`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__xa_unavailable_filter__">{field.label} · 待开放</SelectItem>
              {field.options?.map(([value, label]) => (
                <SelectItem key={String(value)} value={String(value)}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          </div>
        ))}
        {activeFilter && spec.resource !== "escort-levels" && <div className="space-y-2"><Label htmlFor={`resource-status-${spec.resource}`}>状态</Label>{statusFilter}</div>}
        </>} actions={<Button type="submit" disabled={!keywordSupported && !statusSupported}>
          <Search className="size-4" />
          查询
        </Button>} />

      </form>
    ) : undefined
  const columns: Column[] = [
    ...resourceDirectoryColumns(
      spec,
      canEdit,
      locked,
      list.reload
    ),
    {
      key: "actions",
      label: "操作",
      align: "right",
      render: (row) => (
        <div className="flex flex-wrap justify-end gap-2">
          {spec.resource === "gameplays" &&
            !row.is_archived &&
            auth.hasPerm("service:view") && (
              <Button actionTone="edit" variant="outline" size="sm" asChild>
                <Link to={`/service-items?gameplay=${row.id}`}>
                  <Boxes className="size-3.5" />
                  配置商品
                </Link>
              </Button>
            )}
          {canEdit && (
            <Button
              actionTone="edit"
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() => open(row)}
            >
              {spec.resource === "club-rewards" ? "配置" : "编辑"}
            </Button>
          )}
          {spec.archived &&
            query.is_archived &&
            canEdit && (
              <Button
                actionTone="approve"
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() => setRestore(row)}
              >
                <ArchiveRestore className="size-3.5" />
                恢复
              </Button>
            )}
          {canDelete && (
              <Button
                actionTone={spec.archived ? "adjust" : "reject"}
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                disabled={impactLoading !== null || locked}
                onClick={() => void deleteItem(row)}
              >
                {spec.archived ? (
                  <Archive className="size-3.5" />
                ) : (
                  <Trash2 className="size-3.5" />
                )}
                {impactLoading === row.id
                  ? "读取影响中…"
                  : spec.archived
                    ? "归档"
                    : "删除"}
              </Button>
            )}
        </div>
      ),
    },
  ]
  const metrics = [
    { label: "符合条件", value: list.data ? list.total.toLocaleString() : "—" },
    ...(presentation
      ? [
          {
            label: "本页启用",
            value: list.data && list.rows.every(row => typeof (row.is_active ?? row.enabled) === "boolean")
              ? list.rows.filter((row) => row.is_active ?? row.enabled).length
              : "—",
          },
        ]
      : []),
    ...(spec.resource === "gameplays"
      ? [
          {
            label: "本页关联商品",
            value: list.data
              ? list.rows.reduce(
                  (total, row) => total + Number(row.service_count || 0),
                  0
                )
              : "—",
          },
        ]
      : spec.resource === "club-rewards"
        ? [
            {
              label: "本页已兑完",
              value: list.data
                ? list.rows.filter((row) => row.stock === 0).length
                : "—",
            },
          ]
        : []),
  ]
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton={resourceSkeleton(spec.resource)}
    >
      <div
        className="admin-source-page space-y-4"
        data-resource={spec.resource}
      >
        {!embedded && (
          <SourcePageHeader
            title={presentation?.title || spec.title}
            tag={presentation?.tag}
            description={presentation?.description || spec.description}
            metrics={metrics}
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {spec.resource !== "club-rewards" && create}
          </SourcePageHeader>
        )}
        {spec.resource === "boss-types" && !canEdit && canDelete ? (
          <p role="status" className="text-sm text-muted-foreground">当前可查看、删除老板分级。</p>
        ) : (
          <ReadOnlyNotice canEdit={canEdit} canDelete={spec.canDelete === false ? undefined : canDelete} subject={spec.title} />
        )}
        <Feedback message={impactError} />
        {spec.extra}
        <Feedback message={list.error} retry={list.reload} />
        <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
          title={presentation?.tableTitle || `${spec.title}列表`}
          icon={directoryIcon}
          description={`本页 ${list.rows.length} 项`}
          minWidth={presentation?.width || 960}
          filters={filters}
          actions={
            <>
              {spec.archived && (
                <div
                  className="flex rounded-lg border bg-muted/50 p-1"
                  role="group"
                  aria-label="玩法归档状态"
                >
                  <Button
                    disabled={locked}
                    size="sm"
                    variant={!query.is_archived ? "secondary" : "ghost"}
                    aria-pressed={!query.is_archived}
                    onClick={() =>
                      setQuery((old) => ({ ...old, is_archived: false }))
                    }
                  >
                    当前玩法
                    {!query.is_archived && (
                      <Badge variant="outline"><DataValue>{list.total}</DataValue></Badge>
                    )}
                  </Button>
                  <Button
                    disabled={locked}
                    size="sm"
                    variant={query.is_archived ? "secondary" : "ghost"}
                    aria-pressed={!!query.is_archived}
                    onClick={() =>
                      setQuery((old) => ({ ...old, is_archived: true }))
                    }
                  >
                    已归档
                    {query.is_archived && (
                      <Badge variant="outline"><DataValue>{list.total}</DataValue></Badge>
                    )}
                  </Button>
                </div>
              )}
              {spec.resource === "escort-levels" && (
                <div className="w-36 space-y-2"><Label htmlFor={`resource-status-${spec.resource}`}>状态</Label>{statusFilter}</div>
              )}
              {(embedded || spec.resource === "club-rewards") && create}
              {spec.resource === "club-content" && (
                <Badge variant="outline">
                  <CalendarClock className="size-3.5" />
                  本页{" "}
                  {
                    list.rows.filter(
                      (row) => contentVisibility(row).label === "活动归档"
                    ).length
                  }{" "}
                  条活动归档
                </Badge>
              )}
            </>
          }
          rows={list.rows}
          columns={spec.resource === "boss-types" && !canEdit && !canDelete ? spec.columns : columns}
          loading={list.loading}
          error={list.error}
          locked={review.locked}
          empty={hasAppliedFilters ? `没有匹配的${spec.title}` : `暂无${spec.title}记录`}
          emptyDescription={hasAppliedFilters ? "清除筛选后可查看完整列表。" : emptyBossTypes
            ? create ? "添加第一个分级，设置名称和下单折扣。" : "可联系有分级编辑权限的管理员添加。"
            : "可以刷新列表查看最新记录。"}
          emptyIcon={directoryIcon}
          emptyAction={emptyBossTypes && !hasAppliedFilters ? create : undefined}
          clearFilters={hasAppliedFilters ? clearFilters : undefined}
          footer={
            <Pagination snapshotAvailable={!!list.data}
              page={list.page}
              total={list.total}
              loading={list.loading}
              onChange={list.setPage}
              pageSize={list.pageSize}
              onPageSizeChange={list.setPageSize}
            />
          }
        />
        {editor && (
          <ResourceEditor
            spec={spec}
            locked={locked || !canEdit}
            initial={editor}
            onClose={() => setEditor(null)}
            onSaved={() => {
              toast.success("已保存")
              void list.reload()
            }}
          />
        )}
        {restore && (
          <MutationDialog
            title="恢复玩法"
            description={`恢复「${restore.name}」后，请核对启用状态与所属商品后再上架。历史订单快照保持原样。`}
            onClose={() => setRestore(null)}
            readOnly={locked}
            submitText="确认恢复"
            onSubmit={async () => {
              await request({
                url: `/${spec.resource}/${restore.id}/restore/`,
                method: "POST",
              })
              toast.success("已恢复，请核对启用状态")
              void list.reload()
            }}
          />
        )}
        {remove && (
          <MutationDialog
            title={`${spec.archived ? "归档" : "删除"}${spec.title}`}
            submitVariant="destructive"
            readOnly={locked || !canDelete}
            description={`确认${spec.archived ? "归档" : "删除"}「${remove.name || remove.title || `#${remove.id}`}」？${spec.archived ? "关联商品将隐藏，历史订单保留，可以在已归档列表恢复。" : "请先核对关联使用情况。"}`}
            onClose={() => {
              setRemove(null)
              setImpact(null)
            }}
            disabled={!!impactError || impactLoading !== null}
            onSubmit={async () => {
              await request({
                url: `/${spec.resource}/${remove.id}/`,
                method: "DELETE",
              })
              toast.success(spec.archived ? "已归档" : "已删除")
              void list.reload()
            }}
          >
            {impactLoading !== null && (
              <AdminPageSkeleton page="level-impact" />
            )}
            <Feedback
              message={impactError}
              retry={() => void deleteItem(remove)}
            />
            {impact && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-3">
                  {[
                    [impact.providers, "陪玩档案"],
                    [impact.services, "服务商品"],
                    [impact.registrations, "注册申请"],
                  ].map(([count, label]) => (
                    <div
                      key={label}
                      className="rounded-lg border bg-muted/20 p-3 text-center"
                    >
                      <p className="text-xl font-semibold tabular-nums">
                        {count}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {label}
                      </p>
                    </div>
                  ))}
                </div>
                <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm leading-6">
                  删除后将清空这些关联的等级字段。商品最低等级清空后，未设置分范围资格的商品将不再按该等级限制接单；历史订单保留原快照。
                </p>
              </div>
            )}
          </MutationDialog>
        )}
      </div>
    </DataPageState>
  )
}

function ResourceToggle({
  resource,
  row,
  field,
  canEdit,
  locked,
  onSaved,
}: {
  resource: string
  row: Row
  field: string
  canEdit: boolean
  locked: boolean
  onSaved: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Switch name={`${row.name || row.title || `#${row.id}`}启停`}
          aria-label={`${row.name || row.title || `#${row.id}`}启停`}
          checked={!!row[field]}
          disabled={!canEdit || locked || busy}
          onCheckedChange={async (value) => {
            if (busy || locked || !canEdit) return
            setBusy(true)
            setError("")
            try {
              await request({
                url: `/${resource}/${row.id}/`,
                method: "PATCH",
                data: { [field]: value },
              })
              toast.success(value ? "已启用" : "已停用")
              await onSaved()
            } catch (cause: any) {
              setError(cause.message || "状态更新失败，请重试。")
            } finally {
              setBusy(false)
            }
          }}
        />
        {busy ? <Status tone="primary" label="更新中…" /> : <Status value={row[field]} />}
      </div>
      {error && (
        <p role="alert" className="max-w-56 text-xs leading-5 text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
function SourceFormSection({
  bare,
  title,
  children,
}: {
  bare: boolean
  title: string
  children: ReactNode
}) {
  const Icon = (
    {
      基础信息: Gift,
      兑换与权益: Percent,
      库存与展示: Boxes,
      公告正文: Megaphone,
      排序与展示: Eye,
      内容信息: FileText,
      展示窗口与排序: CalendarClock,
      事件与播报模板: Megaphone,
      展示与隐私: Eye,
    } as Record<string, LucideIcon>
  )[title]
  return bare ? (
    <div className="min-w-0">{children}</div>
  ) : (
    <section className="min-w-0 rounded-xl border p-4">
      <div className="mb-4 flex items-center gap-2">
        {Icon && <Icon className="size-4 text-primary" />}
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {children}
    </section>
  )
}

function SourceSwitch({
  field,
  value,
  onChange,
}: {
  field: ResourceField
  value: unknown
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex min-h-14 items-center justify-between gap-3 rounded-lg border px-4 py-3">
      <span className="text-sm font-medium">{field.label}</span>
      <Switch name={field.label}
        aria-label={field.label}
        checked={Boolean(value)}
        disabled={field.disabled}
        onCheckedChange={onChange}
      />
    </label>
  )
}

function bossTypeNumberError(field: ResourceField, value: unknown) {
  if (value == null || value === "") {
    return field.key === "discount_rate" ? "请填写下单折扣。" : "请填写排序。"
  }
  const number = Number(value)
  if (!Number.isInteger(number) || (field.min != null && number < field.min) || (field.max != null && number > field.max)) {
    return field.key === "discount_rate" ? "下单折扣须为 1 至 100 的整数。" : "排序须为不小于 0 的整数。"
  }
  return ""
}

function BossTypeNumberField({ field, value, onChange }: {
  field: ResourceField
  value: number | string | null | undefined
  onChange: (value: string | number) => void
}) {
  const [touched, setTouched] = useState(false)
  const id = `field-${field.key}`
  const error = touched ? bossTypeNumberError(field, value) : ""
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{field.label}<span aria-hidden="true" className="ml-1 text-destructive">*</span></Label>
      <Input
        id={id} type="number" required min={field.min} max={field.max} step={field.step} value={value ?? ""}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-help ${id}-error` : `${id}-help`}
        onChange={(event) => onChange(event.target.value === "" ? "" : event.target.valueAsNumber)}
        onBlur={() => setTouched(true)}
        onInvalid={() => setTouched(true)}
      />
      <p id={`${id}-help`} className="text-xs leading-5 text-muted-foreground">{field.hint}</p>
      {error && <p id={`${id}-error`} role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

export function ResourceEditor({
  spec,
  initial,
  locked = false,
  onClose,
  onSaved,
}: {
  spec: ResourceSpec
  initial: Row
  locked?: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({ ...initial })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [entryTab, setEntryTab] = useState("entry")
  const [unlimitedStock, setUnlimitedStock] = useState(
    initial.stock == null || initial.stock === ""
  )
  const [files, setFiles] = useState<Record<string, File>>({})
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState("")
  const [optionReady, setOptionReady] = useState<Record<string, boolean>>({})
  const auth = useAdminAuth()
  const sections: Record<string, Array<[string, string[]]>> = {
    "club-rewards": [
      [
        "基础信息",
        ["name", "code", "kind", "audience", "image_url", "description"],
      ],
      [
        "兑换与权益",
        [
          "point_cost",
          "vip_quota_kind",
          "balance_amount",
          "point_value",
          "duration_days",
          "duration_months",
          "highlighted",
          "service",
        ],
      ],
      ["库存与展示", ["stock", "sort_order", "is_active", "in_shop"]],
    ],
    gameplays: [
      [
        "入口与展示",
        [
          "name",
          "code",
          "kind",
          "subtitle",
          "theme",
          "sort_order",
          "cover_url",
          "is_active",
        ],
      ],
      ["规则与图文详情", ["rules", "details_html"]],
    ],
    "escort-levels": [
      [
        "等级配置",
        ["name", "commission_rate", "remark", "sort_order", "is_active"],
      ],
    ],
    announcements: [
      ["公告正文", ["title", "content"]],
      ["排序与展示", ["sort_order", "is_pinned", "is_active"]],
    ],
    "club-content": [
      ["内容信息", ["title", "code", "kind", "image_url", "content"]],
      ["展示窗口与排序", ["starts_at", "ends_at", "sort_order", "is_active"]],
    ],
    "broadcast-rules": [
      ["事件与播报模板", ["kind", "template"]],
      [
        "展示与隐私",
        ["visible_hours", "mask_nickname", "sort_order", "enabled"],
      ],
    ],
  }
  const fieldVisible = (field: ResourceField) =>
    spec.resource !== "club-rewards" ||
    ((field.key !== "balance_amount" || form.kind === "BALANCE") &&
      (field.key !== "point_value" || form.kind === "POINTS") &&
      (field.key !== "service" || form.kind === "SERVICE") &&
      (field.key !== "highlighted" || form.kind === "TAG"))
  const configured = sections[spec.resource] || [
    ["内容与展示配置", spec.fields.map((field) => field.key)],
  ]
  const groupedKeys = new Set(configured.flatMap(([, keys]) => keys))
  const groups = [
    ...configured,
    ...(spec.fields.some((field) => !groupedKeys.has(field.key))
      ? [
          [
            "其他业务配置",
            spec.fields
              .filter((field) => !groupedKeys.has(field.key))
              .map((field) => field.key),
          ] as [string, string[]],
        ]
      : []),
  ]
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      confirmDiscard
      contentClassName={
        spec.resource === "escort-levels"
          ? "sm:max-w-2xl"
          : spec.resource === "gameplays"
            ? "sm:max-w-4xl"
            : spec.resource === "club-rewards"
              ? "sm:max-w-6xl"
              : "sm:max-w-5xl"
      }
      title={`${initial.id ? "编辑" : "新增"}${spec.title}`}
      description="核对配置内容、适用范围与启用状态后保存。"
      onClose={onClose}
      disabled={
        uploading ||
        locked ||
        spec.fields
          .filter((field) => field.resource && fieldVisible(field))
          .some((field) => optionReady[field.key] === false)
      }
      readOnly={locked}
      onSubmit={async () => {
        if (
          spec.resource === "club-rewards" &&
          !unlimitedStock &&
          (form.stock === "" ||
            !Number.isInteger(Number(form.stock)) ||
            Number(form.stock) < 0)
        )
          throw new Error("请填写不小于 0 的整数库存。")
        const data: Row = {}
        for (const field of spec.fields) {
          if (field.file) continue
          const value = form[field.key]
          if (spec.resource === "boss-types" && field.type === "number") {
            const error = bossTypeNumberError(field, value)
            if (error) throw new Error(error)
          }
          if (
            field.required &&
            (value == null ||
              value === "" ||
              (Array.isArray(value) && !value.length))
          )
            throw new Error(`请填写${field.label}`)
          if (
            field.type === "number" &&
            value !== "" &&
            value != null &&
            (!Number.isFinite(Number(value)) ||
              (field.min !== undefined && Number(value) < field.min) ||
              (field.max !== undefined && Number(value) > field.max))
          )
            throw new Error(`请检查${field.label}的数值范围`)
          data[field.key] =
            value ??
            (field.type === "boolean" ? false : field.multiple ? [] : "")
        }
        for (const key of spec.money || [])
          data[key] = xaCoinToAmount(data[key])
        const payload = spec.transform ? spec.transform(data, form) : data
        let body: Row | FormData = payload
        if (Object.keys(files).length) {
          const multipart = new FormData()
          for (const [key, value] of Object.entries(payload))
            multipart.append(
              key,
              typeof value === "object"
                ? JSON.stringify(value)
                : String(value ?? "")
            )
          for (const [key, file] of Object.entries(files))
            multipart.append(key, file)
          body = multipart
        }
        await request({
          url: `/${spec.resource}/${initial.id ? `${initial.id}/` : ""}`,
          method: initial.id ? "PATCH" : "POST",
          data: body,
        })
        onSaved()
      }}
    >
      {spec.resource === "gameplays" && (
        <Tabs value={entryTab} onValueChange={setEntryTab}>
          <TabsList className="grid h-11 w-full grid-cols-2">
            <TabsTrigger value="entry">入口与展示</TabsTrigger>
            <TabsTrigger value="content">规则与详情</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      <div
        data-resource={spec.resource}
        className={`admin-source-editor grid items-start gap-6 ${spec.resource === "escort-levels" ? "" : spec.resource === "gameplays" ? (entryTab === "entry" ? "md:grid-cols-[minmax(0,1fr)_250px]" : "") : spec.resource === "club-rewards" ? "xl:grid-cols-[minmax(0,1fr)_290px]" : "lg:grid-cols-[minmax(0,1fr)_310px]"}`}
      >
        <div className="space-y-5">
          {groups
            .filter(
              ([title]) =>
                spec.resource !== "gameplays" ||
                (entryTab === "content"
                  ? title === "规则与图文详情"
                  : title !== "规则与图文详情")
            )
            .map(([title, keys]) => (
              <SourceFormSection
                key={title}
                bare={["gameplays", "escort-levels"].includes(spec.resource)}
                title={title}
              >
                <div className="grid grid-cols-2 gap-4">
                  {keys
                    .map((key) =>
                      spec.fields.find((field) => field.key === key)
                    )
                    .filter((field): field is ResourceField => Boolean(field))
                    .filter(fieldVisible)
                    .map((field) => (
                      <div
                        key={field.key}
                        className={
                          field.full ||
                          (spec.resource === "gameplays" &&
                            field.key === "kind")
                            ? "col-span-2"
                            : ""
                        }
                      >
                        {spec.resource === "club-rewards" &&
                        field.key === "stock" ? (
                          <div className="rounded-lg border p-3">
                            <div className="flex items-center justify-between gap-3">
                              <Label>不限库存</Label>
                              <Switch name="不限库存"
                                aria-label="不限库存"
                                checked={unlimitedStock}
                                onCheckedChange={(value) => {
                                  setUnlimitedStock(value)
                                  setForm((old) => ({
                                    ...old,
                                    stock: value ? null : (initial.stock ?? 0),
                                  }))
                                }}
                              />
                            </div>
                            {!unlimitedStock && (
                              <div className="mt-3">
                                <Field
                                  field={{ ...field, label: "可兑换库存" }}
                                  value={form.stock}
                                  onChange={(value) =>
                                    setForm((old) => ({ ...old, stock: value }))
                                  }
                                />
                              </div>
                            )}
                          </div>
                        ) : spec.resource === "gameplays" &&
                          field.key === "kind" ? (
                          <div className="space-y-2">
                            <Label>玩法类型</Label>
                            <div
                              className="grid grid-cols-2 gap-3"
                              role="radiogroup"
                              aria-label="玩法类型"
                            >
                              {[
                                ["STANDARD", "普通玩法", "按目录组织服务商品"],
                                ["ROULETTE", "转盘单", "每单确定一个服务目标"],
                              ].map(([value, label, detail]) => (
                                <label
                                  key={value}
                                  className={`flex min-h-16 items-center gap-3 rounded-xl border p-3 ${form.kind === value ? "border-primary bg-primary/5" : ""} ${Number(initial.service_count) > 0 ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
                                >
                                  <input
                                    type="radio"
                                    name="gameplay-kind"
                                    value={value}
                                    checked={form.kind === value}
                                    disabled={Number(initial.service_count) > 0}
                                    onChange={() =>
                                      setForm((old) => ({
                                        ...old,
                                        kind: value,
                                      }))
                                    }
                                    className="size-4 shrink-0 accent-primary"
                                  />
                                  <span>
                                    <span className="block text-sm font-medium">
                                      {label}
                                    </span>
                                    <span className="mt-1 block text-[11px] text-muted-foreground">
                                      {detail}
                                    </span>
                                  </span>
                                </label>
                              ))}
                            </div>
                            {Number(initial.service_count) > 0 && (
                              <p className="text-[11px] text-muted-foreground">
                                已关联 {initial.service_count}{" "}
                                个商品，类型已锁定。
                              </p>
                            )}
                          </div>
                        ) : field.file ? (
                          <div className="space-y-2">
                            <Label>{field.label}</Label>
                            <Input
                              type="file"
                              accept="image/*"
                              aria-label={field.label}
                              onChange={(event) => {
                                const file = event.target.files?.[0]
                                if (file)
                                  setFiles((old) => ({
                                    ...old,
                                    [field.file!]: file,
                                  }))
                              }}
                            />
                            {form[field.key] && (
                              <CatalogImage
                                src={form[field.key]}
                                alt={`${field.label}当前图片`}
                                className="max-h-24 rounded-lg object-contain"
                              />
                            )}
                            {files[field.file] && (
                              <p className="text-xs text-muted-foreground">
                                待保存：{files[field.file].name}
                              </p>
                            )}
                          </div>
                        ) : field.richText ? (
                          <div className="space-y-2">
                            <Label>{field.label}</Label>
                            <RichTextEditor
                              label={field.label}
                              value={form[field.key] || ""}
                              onChange={(value) =>
                                setForm((old) => ({
                                  ...old,
                                  [field.key]: value,
                                }))
                              }
                              uploadPath={
                                spec.permission === "engagement"
                                  ? "/club-content-image/"
                                  : auth.hasPerm("service:edit")
                                    ? "/catalog-image/"
                                    : undefined
                              }
                              onUploading={setUploading}
                            />
                          </div>
                        ) : field.resource ? (
                          <OptionPicker
                            resource={field.resource}
                            endpoint={field.optionPath}
                            label={field.label}
                            value={form[field.key]}
                            keyField={field.optionKey || "id"}
                            multiple={field.multiple}
                            onAvailabilityChange={(ready) =>
                              setOptionReady((old) =>
                                old[field.key] === ready
                                  ? old
                                  : { ...old, [field.key]: ready }
                              )
                            }
                            onChange={(value) =>
                              setForm((old) => ({ ...old, [field.key]: value }))
                            }
                          />
                        ) : spec.resource === "boss-types" && field.type === "number" ? (
                          <BossTypeNumberField
                            field={field}
                            value={form[field.key]}
                            onChange={(value) => setForm((old) => ({ ...old, [field.key]: value }))}
                          />
                        ) : field.type === "boolean" ? (
                          <SourceSwitch
                            field={field}
                            value={form[field.key]}
                            onChange={(value) =>
                              setForm((old) => ({ ...old, [field.key]: value }))
                            }
                          />
                        ) : (
                          <Field
                            field={field}
                            value={form[field.key]}
                            onChange={(value) =>
                              setForm((old) => ({ ...old, [field.key]: value }))
                            }
                          />
                        )}
                        {field.upload && (
                          <div className="mt-2 space-y-2">
                            <Input
                              type="file"
                              accept="image/*"
                              aria-label={`上传${field.label}`}
                              disabled={uploading}
                              onChange={async (event) => {
                                const file = event.target.files?.[0]
                                if (!file) return
                                setUploading(true)
                                setUploadError("")
                                try {
                                  const body = new FormData()
                                  body.append("image", file)
                                  const result = await request({
                                    url:
                                      spec.permission === "engagement"
                                        ? "/club-content-image/"
                                        : "/catalog-image/",
                                    method: "POST",
                                    data: body,
                                  })
                                  setForm((old) => ({
                                    ...old,
                                    [field.key]: result.data.url,
                                  }))
                                } catch (cause: any) {
                                  setUploadError(cause.message)
                                } finally {
                                  setUploading(false)
                                }
                              }}
                            />
                            {form[field.key] && (
                              <CatalogImage
                                src={form[field.key]}
                                alt={`${field.label}预览`}
                                className="max-h-32 max-w-full rounded border object-contain"
                              />
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                </div>
                {spec.resource === "club-rewards" && title === "库存与展示" && (
                  <div className="mt-4 rounded-lg border bg-muted/20 p-3">
                    <p className="text-xs text-muted-foreground">已兑换数量</p>
                    <p className="mt-2 text-2xl font-semibold tabular-nums">
                      {initial.redeemed_count ?? 0}
                    </p>
                  </div>
                )}
              </SourceFormSection>
            ))}
        </div>
        {spec.resource === "escort-levels" ? null : spec.resource ===
            "gameplays" && entryTab === "content" ? (
          <div className="rounded-xl border bg-muted/40 p-4">
            <p className="text-xs font-medium">详情内容预览</p>
            <CatalogRichContent
              className="rich-document mt-3 text-sm leading-7 break-words text-muted-foreground"
              html={form.details_html || "暂无通用详情"}
            />
          </div>
        ) : [
            "gameplays",
            "announcements",
            "club-content",
            "club-rewards",
            "broadcast-rules",
          ].includes(spec.resource) ? (
          <ResourcePreview spec={spec} form={form} />
        ) : (
          <DraftPreview
            title={form.name || form.title || spec.title}
            subtitle={form.subtitle || form.description || form.remark}
            image={form.cover_url || form.image_url || form.icon_url}
            fields={spec.fields
              .filter(
                (field) =>
                  !field.full &&
                  !field.file &&
                  !field.resource &&
                  field.type !== "password"
              )
              .slice(0, 7)
              .map((field) => [
                field.label,
                field.options?.find(
                  ([value]) => String(value) === String(form[field.key])
                )?.[1] || textValue(form[field.key]),
              ])}
          >
            {spec.fields
              .filter((field) => field.richText)
              .map((field) => (
                <CatalogRichContent
                  key={field.key}
                  className="rich-document text-sm leading-6"
                  html={form[field.key] || ""}
                />
              ))}
          </DraftPreview>
        )}
      </div>
      <Feedback message={uploadError} />
    </MutationDialog>
  )
}

function ResourcePreview({ spec, form }: { spec: ResourceSpec; form: Row }) {
  const label = (key: string) =>
    spec.fields
      .find((field) => field.key === key)
      ?.options?.find(([value]) => String(value) === String(form[key]))?.[1] ||
    textValue(form[key])
  if (spec.resource === "gameplays")
    return (
      <aside className="space-y-3 self-start">
        <p className="text-xs font-medium text-muted-foreground">
          老板端入口预览
        </p>
        <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
          <div
            className="admin-source-gameplay-cover relative flex aspect-[5/3] items-end overflow-hidden p-4 text-white"
            data-theme={form.theme}
          >
            {form.cover_url && (
              <CatalogImage
                src={form.cover_url}
                alt="玩法封面"
                className="absolute inset-0 size-full object-cover"
              />
            )}
            <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent" />
            <Gamepad2 className="absolute top-4 right-4 size-12 text-white/25" />
            <div className="relative">
              <p className="text-lg font-semibold">{form.name || "玩法名称"}</p>
              <p className="mt-1 text-xs text-white/80">
                {form.subtitle || "暂无副标题"}
              </p>
            </div>
          </div>
          <div className="space-y-3 p-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">{label("kind")}</Badge>
              <Status
                value={form.is_active}
                label={form.is_active ? "启用" : "停用"}
              />
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              关联 {form.service_count ?? 0} 个商品 · 排序{" "}
              {form.sort_order ?? "—"}
            </p>
          </div>
        </div>
      </aside>
    )
  const reward = spec.resource === "club-rewards"
  const rule = spec.resource === "broadcast-rules"
  const content = spec.resource === "club-content"
  const hot = content && form.kind === "HOT"
  const soldOut =
    form.stock !== null &&
    form.stock !== "" &&
    form.stock !== undefined &&
    Number(form.stock) <= 0
  const hidden =
    reward && (!form.is_active || !form.in_shop || form.audience === "PROVIDER")
  return (
    <aside className="min-w-0 space-y-3 self-start xl:sticky xl:top-0">
      <p className="text-xs font-medium text-muted-foreground">
        {rule
          ? "播报模板预览"
          : reward
            ? "老板端积分商城预览"
            : "老板端展示预览"}
      </p>
      <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
        <header className="bg-[#10285b] p-4 text-white">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] text-blue-100/75">
              {reward
                ? "兑换消耗"
                : rule
                  ? "俱乐部动态"
                  : content
                    ? label("kind")
                    : "官方公告"}
            </p>
            {!reward && (
              <Status value={rule ? form.enabled : form.is_active} />
            )}
          </div>
          {reward ? (
            <p className="mt-3 text-2xl font-semibold tabular-nums">
              {Number(form.point_cost || 0).toLocaleString()}{" "}
              <span className="text-xs font-normal text-blue-100/70">积分</span>
            </p>
          ) : (
            <h3 className="mt-3 text-lg leading-7 font-semibold break-words">
              {form.title || form.name || spec.title}
            </h3>
          )}
          {spec.resource === "announcements" && (
            <p className="mt-3 text-[11px] text-blue-100/70">
              {form.is_pinned ? "置顶公告 · " : ""}
              {form.created_at ? formatDateTime(form.created_at) : "尚未发布"}
            </p>
          )}
        </header>
        {reward ? (
          hidden ? (
            <div className="flex min-h-64 flex-col items-center justify-center gap-3 p-5 text-center">
              <EyeOff className="size-8 text-muted-foreground" />
              <p className="text-sm font-medium">当前不在老板端商城展示</p>
              <p className="text-xs text-muted-foreground">
                {!form.is_active
                  ? "好礼已停用"
                  : !form.in_shop
                    ? "未在商城展示"
                    : "仅面向陪玩"}
              </p>
            </div>
          ) : (
            <div className="p-4">
              <div className="overflow-hidden rounded-xl border">
                <div className="flex h-32 items-center justify-center bg-primary/5">
                  {form.image_url ? (
                    <CatalogImage
                      src={form.image_url}
                      alt="当前好礼图片"
                      className="size-full object-cover"
                    />
                  ) : (
                    <Gift className="size-12 text-primary/50" />
                  )}
                </div>
                <div className="p-3">
                  <Badge variant="secondary">{label("kind")}</Badge>
                  <h3 className="mt-2 min-h-10 text-sm leading-5 font-semibold">
                    {form.name || "好礼名称"}
                  </h3>
                  <p className="mt-2 text-lg font-semibold text-primary tabular-nums">
                    {Number(form.point_cost || 0).toLocaleString()}{" "}
                    <span className="text-[11px] font-normal">积分</span>
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {form.stock == null || form.stock === ""
                      ? "不限库存"
                      : `剩余 ${form.stock} 份`}
                  </p>
                  <div
                    className={`mt-3 flex h-8 items-center justify-center rounded-md text-sm font-medium ${soldOut ? "bg-muted text-muted-foreground" : "bg-primary text-primary-foreground"}`}
                  >
                    {soldOut ? "已兑完" : "兑换"}
                  </div>
                </div>
              </div>
              <p className="mt-3 text-xs leading-5 break-words whitespace-pre-wrap text-muted-foreground">
                {form.description || "暂无交付说明"}
              </p>
            </div>
          )
        ) : (
          <>
            {content && (
              <div
                className={
                  hot
                    ? "flex min-h-52 items-center justify-center bg-slate-950 p-3"
                    : "bg-muted/20 p-3"
                }
              >
                {form.image_url ? (
                  <CatalogImage
                    src={form.image_url}
                    alt="当前内容图片"
                    className={
                      hot
                        ? "max-h-80 w-full object-contain"
                        : "max-h-52 w-full rounded-lg object-cover"
                    }
                  />
                ) : (
                  <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-muted-foreground">
                    <Images className="size-8" />
                    <span className="text-xs">尚未设置图片</span>
                  </div>
                )}
              </div>
            )}
            {!hot && (
              <div className="p-4">
                {rule ? (
                  <p className="text-sm leading-7 break-words whitespace-pre-wrap">
                    {form.template || "尚未填写播报模板"}
                  </p>
                ) : (
                  <CatalogRichContent
                    className="rich-document max-h-64 overflow-y-auto text-sm leading-7 break-words"
                    html={form.content || "暂无正文"}
                  />
                )}
              </div>
            )}
            {(content || rule) && (
              <div className="space-y-1 border-t p-4 text-xs leading-5 text-muted-foreground">
                {rule ? (
                  <>
                    <p>
                      展示 {form.visible_hours ?? 0} 小时 · 排序{" "}
                      {form.sort_order ?? 0}
                    </p>
                    <p>{form.mask_nickname ? "昵称已保护" : "完整显示昵称"}</p>
                  </>
                ) : (
                  <>
                    <p>
                      上架：
                      {form.starts_at
                        ? formatDateTime(form.starts_at)
                        : "立即上架"}
                    </p>
                    <p>
                      结束：
                      {form.ends_at ? formatDateTime(form.ends_at) : "长期展示"}
                    </p>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  )
}

function QualificationScopeSummary() {
  const [expanded, setExpanded] = useState(false)
  const result = useLoad(
    async () => (await request({ url: "/qualification-options/" })).data
  )
  const data = result.data
  return (
    <section className="overflow-hidden rounded-xl border bg-card text-sm leading-5 shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 p-6">
        <div>
          <h2 className="flex items-center gap-2 text-base font-medium">
            <Layers3 className="size-4 text-primary" />
            接单档位与自动抽成
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            同范围高档可接低档
          </p>
        </div>
        <Button
          variant="outline"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "收起规则" : "查看规则"}
          <ChevronDown
            className={
              expanded
                ? "size-4 rotate-180 transition-transform"
                : "size-4 transition-transform"
            }
          />
        </Button>
      </header>
      {expanded && (
        <div className="border-t p-6">
          <Feedback message={result.error} retry={result.reload} />
          {result.loading && !data ? (
            <LoadingPanel />
          ) : (
            data && (
              <div className="grid gap-3 lg:grid-cols-2">
                {(data.scopes || []).map((scope: Row, index: number) => (
                  <section
                    key={index}
                    className="rounded-xl border bg-muted/20 p-4"
                  >
                    <h3 className="text-sm font-medium">
                      {(data.kinds || []).find(
                        (item: Row) => item.value === scope.provider_kind
                      )?.label || scope.provider_kind}{" "}
                      ·{" "}
                      {(data.platforms || []).find(
                        (item: Row) => item.value === scope.platform
                      )?.label || scope.platform}{" "}
                      ·{" "}
                      {(
                        {
                          CONFIDENTIAL: "机密",
                          TOP_SECRET: "绝密",
                          ANY: "不限难度",
                        } as Row
                      )[scope.security_level] || scope.security_level}
                    </h3>
                    <ul className="mt-3 flex flex-wrap gap-2">
                      {(scope.grades || []).map((grade: Row) => (
                        <li
                          key={grade.value}
                          className="flex min-w-28 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 text-xs"
                        >
                          <span>{grade.label}</span>
                          <strong className="text-primary tabular-nums">
                            {grade.commission_rate == null
                              ? "按项目"
                              : String(grade.commission_rate) + "%"}
                          </strong>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )
          )}
        </div>
      )}
    </section>
  )
}

export const resourceSpecs: Record<string, ResourceSpec> = {
  "boss-types": {
    resource: "boss-types",
    title: "老板分级",
    description: "维护本店老板分级及下单折扣。VIP 成长门槛与会员权益在「VIP 等级」配置。",
    permission: "boss_type",
    columns: [
      { ...nameColumn, label: "分级名称" },
      {
        key: "discount_rate",
        label: "下单折扣",
        align: "right",
        render: (row) => row.discount_rate == null || row.discount_rate === "" ? "—" : `${row.discount_rate}%`,
      },
      { key: "remark", label: "说明" },
      { key: "sort_order", label: "排序" },
      { ...stateColumn, render: (row) => row.is_active == null ? "—" : stateColumn.render!(row) },
    ],
    defaults: {
      name: "",
      discount_rate: 100,
      color: "#2563eb",
      remark: "",
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "name", label: "分级名称", required: true, maxLength: 50 },
      {
        key: "discount_rate",
        label: "下单折扣（%）",
        type: "number",
        required: true,
        min: 1,
        max: 100,
        step: 1,
        hint: "填 90 表示按原价的 90% 计价，填 100 为原价。",
      },
      { key: "color", label: "显示颜色" },
      { ...sort, required: true, hint: "数值越小越靠前，相同时按分级编号升序排列。" },
      { key: "remark", label: "说明", type: "textarea", full: true, maxLength: 255 },
      active,
    ],
  },
  "escort-levels": {
    resource: "escort-levels",
    title: "陪玩等级",
    extra: <QualificationScopeSummary />,
    description: "管理等级与建议抽成。删除前核对陪玩、商品与注册申请的关联。",
    permission: "escort_level",
    columns: [
      nameColumn,
      {
        key: "commission_rate",
        label: "建议抽成",
        render: (row) => `${row.commission_rate}%`,
      },
      { key: "remark", label: "说明" },
      { key: "sort_order", label: "排序" },
      stateColumn,
    ],
    defaults: {
      name: "",
      commission_rate: 20,
      remark: "",
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "name", label: "等级名称", required: true },
      {
        key: "commission_rate",
        label: "建议抽成（%）",
        type: "number",
        min: 0,
        max: 100,
      },
      sort,
      active,
      { key: "remark", label: "说明", type: "textarea", full: true },
    ],
  },
  achievements: {
    resource: "achievements",
    title: "成就规则",
    description: coinText("按完成订单数或累计消费解锁，金额目标以浪花输入。"),
    permission: "achievement",
    columns: [
      nameColumn,
      {
        key: "metric",
        label: "目标口径",
        render: (row) => (row.metric === "amount" ? "累计消费" : "完成订单数"),
      },
      {
        align: "right",
        key: "target",
        label: "目标值",
        render: (row) =>
          row.metric === "amount"
            ? `${amountToXaCoin(row.target)} ${coinUnit()}`
            : `${row.target} 单`,
      },
      { key: "desc", label: "描述" },
      stateColumn,
    ],
    defaults: {
      code: "",
      title: "",
      desc: "",
      icon: "",
      metric: "orders",
      target: 1,
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "code", label: "英文标识", required: true },
      { key: "title", label: "名称", required: true },
      {
        key: "metric",
        label: "目标口径",
        type: "select",
        options: [
          ["orders", "完成订单数"],
          ["amount", coinText("累计消费（浪花）")],
        ],
      },
      { key: "target", label: "目标值", type: "number", min: 0, step: 0.1 },
      { key: "icon", label: "图标地址" },
      sort,
      { key: "desc", label: "描述", type: "textarea", full: true },
      active,
    ],
    transform: (data) => ({
      ...data,
      target:
        data.metric === "amount" ? xaCoinToAmount(data.target) : data.target,
    }),
  },
  announcements: {
    resource: "announcements",
    title: "公告管理",
    description: "发布平台公告，管理置顶、排序和可见状态。",
    permission: "announcement",
    columns: [
      { key: "title", label: "标题" },
      {
        key: "content",
        label: "内容",
        render: (row) => (
          <p className="max-w-lg truncate">{contentSummary(row.content)}</p>
        ),
      },
      {
        key: "is_pinned",
        label: "置顶",
        render: (row) =>
          row.is_pinned ? (
            <Status value="AVAILABLE" label="置顶" />
          ) : (
            <span className="text-xs text-muted-foreground">普通</span>
          ),
      },
      {
        key: "created_at",
        label: "发布时间",
        render: (row) => formatDateTime(row.created_at),
      },
      stateColumn,
    ],
    defaults: {
      title: "",
      content: "",
      is_pinned: false,
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "title", label: "公告标题", full: true, required: true },
      {
        key: "content",
        label: "公告内容",
        richText: true,
        type: "textarea",
        full: true,
      },
      { key: "is_pinned", label: "置顶公告", type: "boolean" },
      sort,
      active,
    ],
  },
  gameplays: {
    resource: "gameplays",
    title: "玩法配置",
    description: "维护玩法、说明与封面。归档会隐藏玩法及其商品，历史订单保留。",
    permission: "service",
    archived: true,
    columns: [
      nameColumn,
      { key: "kind", label: "类型" },
      { key: "subtitle", label: "副标题" },
      { key: "service_count", label: "关联商品数" },
      stateColumn,
    ],
    defaults: {
      name: "",
      code: "",
      kind: "STANDARD",
      subtitle: "",
      theme: "custom",
      cover_url: "",
      rules: "",
      details_html: "",
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "name", label: "玩法名称", required: true },
      { key: "code", label: "英文标识", required: true },
      {
        key: "kind",
        label: "玩法类型",
        type: "select",
        options: [
          ["STANDARD", "普通玩法"],
          ["ROULETTE", "转盘单"],
        ],
      },
      {
        key: "theme",
        label: "视觉主题",
        type: "select",
        options: [
          ["custom", "定制"],
          ["roulette", "转盘"],
          ["experience", "体验"],
          ["companion", "陪玩"],
          ["fun", "趣味"],
          ["mission", "任务"],
          ["extraction", "带出"],
          ["prison", "监狱"],
        ],
      },
      { key: "subtitle", label: "副标题", full: true },
      { key: "cover_url", label: "封面图片", upload: true, full: true },
      { key: "rules", label: "玩法规则", type: "textarea", full: true },
      {
        key: "details_html",
        label: "玩法图文详情",
        richText: true,
        type: "textarea",
        full: true,
      },
      sort,
      active,
    ],
    transform: (data) => {
      if (!/^[a-zA-Z0-9_-]+$/.test(data.code))
        throw new Error("标识只允许英文字母、数字、下划线与连字符。")
      return data
    },
  },
  "support-cards": {
    resource: "support-cards",
    title: "客服名片",
    description: "维护客服微信、企业微信入口及二维码，移动端通过明确入口联系。",
    permission: "support",
    columns: [
      nameColumn,
      { key: "company", label: "所属" },
      { key: "wechat_id", label: "微信号" },
      { key: "wecom_service_url", label: "企业微信客服入口" },
      { key: "sort_order", label: "排序" },
      stateColumn,
    ],
    defaults: {
      name: "",
      company: "",
      wechat_id: "",
      wecom_corp_id: "",
      wecom_service_url: "",
      avatar_url: "",
      qrcode_url: "",
      tips: "",
      sort_order: 0,
      is_active: true,
    },
    fields: [
      { key: "name", label: "客服名称", required: true },
      { key: "company", label: "所属" },
      { key: "wechat_id", label: "微信号" },
      { key: "wecom_corp_id", label: "企业 ID" },
      { key: "wecom_service_url", label: "企业微信客服链接", full: true },
      { key: "avatar_url", label: "头像地址", full: true },
      { key: "qrcode_url", label: "二维码地址", full: true },
      { key: "tips", label: "联系说明", type: "textarea", full: true },
      sort,
      active,
    ],
  },
}

export function ServicesPage() {
  const [params, setParams] = useSearchParams()
  const auth = useAdminAuth()
  const config = params.get("config")
  const tab = auth.hasPerm("service:edit") && (config === "games" || config === "categories") ? config : "services"
  function setTab(next: "services" | "games" | "categories") {
    const search = new URLSearchParams(params)
    if (next === "services") search.delete("config")
    else search.set("config", next)
    setParams(search, { replace: true })
  }
  const [draft, setDraft] = useState({ keyword: "", item_id: "" })
  const [query, setQuery] = useState<Row>({
    keyword: "",
    is_active: "",
    gameplay: params.get("gameplay") || "",
  })
  const list = useList("service-items", query)
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  const categorySpec: ResourceSpec = {
    resource: tab === "games" ? "game-categories" : "service-categories",
    title: tab === "games" ? "游戏类目" : "服务分类",
    description: "维护商品与陪玩的所属游戏和服务类别。",
    permission: "service",
    defaults: {
      name: "",
      remark: "",
      sort_order: 0,
      is_active: true,
      is_gift: false,
      order_kind: "",
    },
    fields: [
      { key: "name", label: "名称", required: true },
      ...(tab === "games"
        ? [{ key: "icon_url", label: "游戏图标", file: "icon", full: true }]
        : []),
      { key: "remark", label: "说明", type: "textarea", full: true },
      ...(tab === "categories"
        ? [
            { key: "is_gift", label: "礼物分类", type: "boolean" as const },
            {
              key: "order_kind",
              label: "订单类别",
              type: "select" as const,
              options: [
                ["", "其他"],
                ["GUARD", "护航"],
                ["COMPANION", "陪玩"],
                ["GRIND", "代肝"],
                ["RANK", "上分"],
              ] as Array<[string, string]>,
            },
          ]
        : []),
      sort,
      active,
    ],
    columns: [
      nameColumn,
      { key: "remark", label: "说明" },
      { key: "sort_order", label: "排序" },
      stateColumn,
    ],
  }
  const review = useSourceReviewState("/admin/service-items/")
  const metadata = useLoad(
    async () => (await request({ url: "/options/gameplays/" })).data
  )
  const gameplayOptions: Row[] = Array.isArray(metadata.data)
    ? metadata.data
    : metadata.data?.list || []
  const locked =
    list.loading ||
    !!list.error ||
    metadata.loading ||
    !!metadata.error ||
    review.locked
  const gameplayParam = params.get("gameplay") || ""
  useEffect(() => {
    setQuery((old) => ({ ...old, gameplay: gameplayParam }))
  }, [gameplayParam])
  const advancedCount = [
    "service_category",
    "provider_kind",
    "platform",
    "security_level",
    "provider_grade",
  ].filter((key) => query[key]).length
  const columns: Column[] = [
    {
      key: "name",
      label: "商品",
      render: (row) => (
        <div className="flex max-w-[320px] items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
            {row.is_gift ? (
              <Gift className="size-5" />
            ) : row.is_addon ? (
              <Plus className="size-5" />
            ) : (
              <Gamepad2 className="size-5" />
            )}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">{row.name}</h2>
              {row.is_gift && <Badge variant="secondary">礼物</Badge>}
              {row.is_addon && <Badge variant="outline">附加费用</Badge>}
            </div>
            <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
              {row.description || row.game_category_name || "—"}
            </p>
            <p className="mt-1 text-[10px] text-muted-foreground">
              ID {row.id} · 排序 {row.sort_order}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: "gameplay_name",
      label: "玩法与规格",
      render: (row) => (
        <div>
          <p className="text-sm font-medium">
            {row.gameplay_name || row.catalog_family || "未绑定玩法"}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {[row.catalog_family, row.catalog_variant]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {[row.game_category_name, row.service_category_name]
              .filter(Boolean)
              .join(" · ") || "未分类"}
          </p>
        </div>
      ),
    },
    {
      key: "provider_grade",
      label: "接单要求",
      className: "min-w-28",
      render: (row) => (
        <div>
          <p className="text-sm">
            {row.provider_kind
              ? enumLabel(row.provider_kind)
              : row.required_level_name || "按现有技能"}
            {row.provider_grade ? ` · ${enumLabel(row.provider_grade)}` : ""}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {[
              row.platform && enumLabel(row.platform),
              row.security_level && enumLabel(row.security_level),
            ]
              .filter(Boolean)
              .join(" · ") || "未限定平台"}
          </p>
        </div>
      ),
    },
    {
      key: "price",
      label: "价格",
      align: "right",
      className: "min-w-32 whitespace-nowrap",
      render: (row) => (
        <div>
          <p className="text-sm font-semibold tabular-nums">
            {amountToXaCoin(row.price)} {coinUnit()}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            / {row.price_unit || "份"}
            {row.billing_provider_count === 2 ? " · 每人" : ""}
          </p>
          {row.billing_provider_count === 2 && (
            <p className="mt-1 text-[11px] text-primary">
              双陪合计 {amountToXaCoin(row.price * 2)} {coinUnit()}
            </p>
          )}
        </div>
      ),
    },
    {
      key: "purchase_mode",
      label: "下单方式",
      className: "min-w-28",
      render: (row) => (
        <div>
          <Badge variant="outline">
            {row.purchase_mode === "CONSULT" ? "客服确认" : "直接下单"}
          </Badge>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {row.billing_provider_count === 2 ? "双陪" : "单陪"}
            {row.is_addon ? " · 不可独立购买" : ""}
          </p>
        </div>
      ),
    },
    {
      key: "commission_policy",
      label: "抽成配置",
      className: "min-w-28",
      render: (row) => (
        <div>
          <p className="text-xs font-medium">
            {enumLabel(row.commission_policy)}
          </p>
          {row.commission_override && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {row.commission_type === "FIXED"
                ? `固定 ${amountToXaCoin(row.commission_fixed)} ${coinUnit()}`
                : `比例 ${row.commission_rate}%`}
            </p>
          )}
        </div>
      ),
    },
    {
      key: "is_active",
      label: "状态",
      align: "center",
      render: (row) => (
        <Status
          value={row.is_active}
          tone={row.is_active ? "success" : "muted"}
          label={row.is_active ? "已启用" : "已停用"}
          readState={typeof row.is_active === "boolean" ? undefined : "unread"}
        />
      ),
    },
    {
      key: "actions",
      label: "操作",
      align: "right",
      render: (row) => (
        <div className="flex justify-end gap-2">
          {auth.hasPerm("service:edit") && (
            <Button
              actionTone="edit"
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() => setEditing(row)}
            >
              编辑套餐
            </Button>
          )}
          {auth.hasPerm("service:delete") && (
            <Button
              actionTone="reject"
              size="sm"
              variant="ghost"
              className="text-destructive hover:text-destructive"
              disabled={locked}
              onClick={() => setRemove(row)}
            >
              <Trash2 className="size-3.5" />
              删除
            </Button>
          )}
        </div>
      ),
    },
  ]
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="services"
    >
      <div
        className="admin-source-page space-y-4"
        data-resource="service-items"
      >
        <SourcePageHeader
          title="服务商品"
          tag="商品与价目"
          description="核对服务、附加费用和礼物的价格、下单方式、接单资格与抽成规则。"
          metrics={[
            {
              label: "符合条件",
              value: list.data ? list.total.toLocaleString() : "—",
            },
            {
              label: "本页启用",
              value: list.data && list.rows.every(row => typeof row.is_active === "boolean")
                ? list.rows.filter((row) => row.is_active).length
                : "—",
            },
            {
              label: "本页礼物",
              value: list.data && list.rows.every(row => typeof row.is_gift === "boolean")
                ? list.rows.filter((row) => row.is_gift).length
                : "—",
            },
          ]}
        >
          <RefreshButton loading={list.loading} reload={list.reload} />
          {auth.hasPerm("service:edit") && (
            <Button
              variant="secondary"
              disabled={locked}
              onClick={() => setEditing({})}
            >
              <Plus className="size-4" />
              新增服务项
            </Button>
          )}
        </SourcePageHeader>
        <ReadOnlyNotice
          canEdit={auth.hasPerm("service:edit")}
          canDelete={auth.hasPerm("service:delete")}
          subject="服务商品"
        />
        {metadata.loading && (
          <p
            role="status"
            className="rounded-lg border bg-muted/20 p-3 text-sm leading-5 text-muted-foreground"
          >
            正在读取商品配置…
          </p>
        )}
        <Feedback message={metadata.error} retry={metadata.reload} />
        <Feedback message={list.error} retry={list.reload} />

        <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
          title="商品列表"
          icon={Boxes}
          description={`本页 ${list.rows.length} 项 · 归档玩法商品不在此列表`}
          minWidth={1080}
          actions={
            auth.hasPerm("service:edit") && (
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setTab("games")}>
                  游戏类目
                </Button>
                <Button variant="outline" onClick={() => setTab("categories")}>
                  服务分类
                </Button>
              </div>
            )
          }
          filters={
            <form onSubmit={event => { event.preventDefault(); setQuery(old => ({ ...old, ...draft })) }}>
              <AdminFilters title="服务商品筛选" activeCount={advancedCount + (draft.item_id ? 1 : 0)} primary={<>
                <div className="space-y-2">
                  <Label htmlFor="service-keyword">商品名称</Label>
                  <div className="relative"><Search className="pointer-events-none absolute top-3 left-3 size-4 text-muted-foreground" /><Input id="service-keyword" className="pl-9" aria-label="商品名称" placeholder="例如 王者荣耀陪练" maxLength={100} value={draft.keyword} onChange={event => setDraft(old => ({ ...old, keyword: event.target.value }))} /></div>
                </div>
                <OptionPicker allLabel="全部游戏" resource="game-categories" label="游戏类目" value={query.game_category} onChange={value => setQuery(old => ({ ...old, game_category: value }))} />
                <div className="space-y-2"><Label htmlFor="service-gameplay">玩法</Label>
                  <Select name="gameplay_filter" autoComplete="off" value={String(query.gameplay || "ALL")} disabled={metadata.loading || !!metadata.error} onValueChange={value => setQuery(old => ({ ...old, gameplay: value === "ALL" ? "" : value }))}>
                    <SelectTrigger id="service-gameplay" aria-label="玩法筛选"><SelectValue placeholder="全部玩法" /></SelectTrigger>
                    <SelectContent><SelectItem value="ALL">全部玩法</SelectItem>{gameplayOptions.map(row => <SelectItem key={row.id} value={String(row.id)}>{row.name}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </>} actions={<><Button type="submit">查询</Button><Button type="button" variant="ghost" onClick={() => { setDraft({ keyword: "", item_id: "" }); setQuery({ keyword: "", item_id: "", gameplay: "" }) }}>清除筛选</Button></>}>
                <div className="space-y-2"><Label htmlFor="service-item-id">商品编号</Label><Input id="service-item-id" aria-label="商品编号" placeholder="例如 SL1234567890" inputMode="numeric" maxLength={18} value={draft.item_id} onChange={event => setDraft(old => ({ ...old, item_id: event.target.value }))} /></div>
                <OptionPicker resource="service-categories" label="服务分类" value={query.service_category} onChange={value => setQuery(old => ({ ...old, service_category: value }))} />
                <QualificationFilters embedded service value={{ ...query, grade: query.provider_grade }} onChange={value => setQuery({ ...value, provider_grade: value.grade })} />
              </AdminFilters>
            </form>
          }
          rows={list.rows}
          columns={columns}
          loading={list.loading}
          error={list.error}
          locked={review.locked}
          empty="没有匹配的服务商品"
          emptyIcon={Boxes}
          clearFilters={() => {
            setDraft({ keyword: "", item_id: "" })
            setQuery({ keyword: "", item_id: "", gameplay: "" })
          }}
          footer={
            <Pagination snapshotAvailable={!!list.data}
              page={list.page}
              total={list.total}
              loading={list.loading}
              onChange={list.setPage}
              pageSize={list.pageSize}
              onPageSizeChange={list.setPageSize}
            />
          }
        />
        {editing && (
          <ServiceEditor
            row={editing}
            locked={locked}
            onClose={() => setEditing(null)}
            onSaved={list.reload}
          />
        )}
        {remove && (
          <MutationDialog
            title="删除服务商品"
            submitVariant="destructive"
            readOnly={locked}
            description={`确认删除「${remove.name}」？有关联订单的商品无法删除，请先下架并保留历史。`}
            onClose={() => setRemove(null)}
            onSubmit={async () => {
              await request({
                url: `/service-items/${remove.id}/`,
                method: "DELETE",
              })
              toast.success("已删除")
              void list.reload()
            }}
          />
        )}
        <Dialog
          open={tab !== "services"}
          onOpenChange={(open) => {
            if (!open) setTab("services")
          }}
        >
          <DialogContent className="sm:max-w-6xl">
            <DialogHeader>
              <DialogTitle>
                {tab === "games" ? "游戏类目" : "服务分类"}
              </DialogTitle>
            </DialogHeader>
            <ResourcePage key={tab} spec={categorySpec} embedded />
            <DialogFooter>
              <Button variant="outline" onClick={() => setTab("services")}>
                关闭配置
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </DataPageState>
  )
}

function ServiceEditor({
  row,
  locked,
  onClose,
  onSaved,
}: {
  row: Row
  locked: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    name: "",
    description: "",
    game_category: null,
    service_category: null,
    gameplay: null,
    required_level: null,
    price_unit: "份",
    price: "",
    catalog_family: "",
    catalog_variant: "",
    purchase_mode: "DIRECT",
    purchase_note: "",
    is_addon: false,
    billing_provider_count: 1,
    provider_kind: "",
    provider_grade: "",
    platform: "",
    security_level: "",
    service_type: "REGULAR",
    experience_kind: "",
    benefit_kind: "",
    commission_policy: "LEGACY",
    commission_override: false,
    commission_type: "PERCENT",
    commission_rate: 0,
    commission_fixed: 0,
    allows_discount_coupons: false,
    cover_url: "",
    rules: "",
    details_html: "",
    rules_images: [],
    introduction_images: [],
    images: [],
    highlights: [],
    roulette_pool: [],
    gift_naming_days: 0,
    gift_festival: "",
    gift_starts_at: "",
    gift_ends_at: "",
    sort_order: 0,
    is_active: true,
    ...row,
    price_input: row.id ? Number(amountToXaCoin(row.price)) : "",
    fixed_input: Number(amountToXaCoin(row.commission_fixed || 0)),
  })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [tab, setTab] = useState("main")
  const [uploading, setUploading] = useState(false)
  const [optionReady, setOptionReady] = useState<Record<string, boolean>>({})
  function set(key: string, value: any) {
    setForm((old) => ({ ...old, [key]: value }))
  }
  const definitions: Record<string, ResourceField[]> = {
    main: [
      { key: "name", label: "商品名称", required: true },
      {
        key: "price_input",
        label: coinText("价格（浪花）"),
        type: "number",
        min: 0.1,
        step: 0.1,
        required: true,
      },
      { key: "price_unit", label: "计价单位" },
      {
        key: "billing_provider_count",
        label: "计价人数",
        type: "select",
        options: [
          [1, "单陪"],
          [2, "双陪"],
        ],
      },
      { key: "game_category", label: "游戏类目", resource: "game-categories" },
      {
        key: "service_category",
        label: "服务分类",
        resource: "service-categories",
      },
      { key: "gameplay", label: "所属玩法", resource: "gameplays" },
      { key: "catalog_family", label: "合并展示的玩法名称" },
      { key: "catalog_variant", label: "套餐档位名称" },
      {
        key: "purchase_mode",
        label: "购买方式",
        type: "select",
        options: [
          ["DIRECT", "直接下单"],
          ["CONSULT", "客服确认"],
        ],
      },
      { key: "purchase_note", label: "购买说明", type: "textarea", full: true },
      { key: "description", label: "商品描述", type: "textarea", full: true },
      { key: "is_addon", label: "附加费用商品", type: "boolean" },
      sort,
      active,
    ],
    qualification: [
      {
        key: "provider_kind",
        label: "接单方向",
        type: "select",
        options: [
          ["", "未配置"],
          ["GUARD", "护航"],
          ["COMPANION", "陪玩"],
          ["GRIND", "代肝"],
          ["RANK", "上分"],
        ],
      },
      {
        key: "provider_grade",
        label: "订单档位",
        type: "select",
        options: [
          ["ENTERTAINMENT", "娱乐"],
          ["TECHNICAL", "技术"],
          ["MASTER", "大师"],
          ["KING", "王者"],
          ["LEGEND", "传奇"],
          ["GLORY", "荣耀"],
          ["STAR", "明星"],
          ["OPEN", "不分档"],
        ],
      },
      {
        key: "platform",
        label: "平台",
        type: "select",
        options: [
          ["PC", "端游"],
          ["MOBILE", "手游"],
        ],
      },
      {
        key: "security_level",
        label: "难度",
        type: "select",
        options: [
          ["ANY", "不限难度"],
          ["CONFIDENTIAL", "机密"],
          ["TOP_SECRET", "绝密"],
        ],
      },
      {
        key: "required_level",
        label: "旧技能所需等级",
        resource: "escort-levels",
      },
    ],
    pricing: [
      {
        key: "service_type",
        label: "服务类型",
        type: "select",
        options: [
          ["REGULAR", "常规单"],
          ["ACTIVITY_LIMITED", "活动限定单"],
          ["PROMOTIONAL", "促销单"],
          ["COMPANION", "陪玩单"],
          ["EXPERIENCE", "体验单"],
        ],
      },
      {
        key: "experience_kind",
        label: "体验资格",
        type: "select",
        options: [
          ["", "普通服务"],
          ["NEW_FIRST", "新客首单"],
          ["NEW_VALUE", "新客超值"],
          ["DAILY", "每日一单"],
          ["DAILY_VALUE", "每日超值"],
        ],
      },
      {
        key: "benefit_kind",
        label: "权益类型",
        type: "select",
        options: [
          ["", "无"],
          ["COMPANION_HOUR", "小时陪玩"],
          ["SAFEBOX_33", "33 安全箱代肝主套餐"],
        ],
      },
      {
        key: "commission_policy",
        label: "抽成策略",
        type: "select",
        options: [
          ["LEGACY", "原有规则"],
          ["EXPERIENCE", "体验单规则"],
          ["COMPANION", "陪玩单规则"],
          ["REGULAR", "常规单等级抽成"],
          ["FUN", "趣味单等级抽成"],
        ],
      },
      {
        key: "allows_discount_coupons",
        label: "允许普通折扣券",
        type: "boolean",
      },
      { key: "commission_override", label: "自定义抽成", type: "boolean" },
      {
        key: "commission_type",
        label: "自定义方式",
        type: "select",
        options: [
          ["PERCENT", "按比例"],
          ["FIXED", "固定金额"],
        ],
      },
      {
        key: "commission_rate",
        label: "抽成比例（%）",
        type: "number",
        min: 0,
        max: 100,
      },
      {
        key: "fixed_input",
        label: coinText("固定抽成（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
    ],
    content: [
      { key: "cover_url", label: "封面图片", full: true },
      { key: "rules", label: "玩法规则", type: "textarea", full: true },
      {
        key: "details_html",
        label: "图文详情",
        richText: true,
        type: "textarea",
        full: true,
      },
    ],
    gift: [
      { key: "gift_naming_days", label: "冠名天数", type: "number", min: 0 },
      { key: "gift_festival", label: "节日标识" },
      { key: "gift_starts_at", label: "节日开始时间", type: "datetime-local" },
      { key: "gift_ends_at", label: "节日结束时间", type: "datetime-local" },
    ],
  }
  const fieldsByTab: Record<string, ResourceField[]> = {
    main: [
      ...definitions.main.filter(
        (field) =>
          !["catalog_family", "catalog_variant", "sort_order"].includes(
            field.key
          )
      ),
      ...definitions.qualification,
      ...definitions.gift,
    ],
    pricing: definitions.pricing,
    content: [
      ...definitions.content,
      ...definitions.main.filter((field) =>
        ["catalog_family", "catalog_variant", "sort_order"].includes(field.key)
      ),
    ],
  }
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      confirmDiscard
      contentClassName="sm:max-w-4xl"
      readOnly={locked}
      title={row.id ? "编辑服务套餐" : "新增服务套餐"}
      description="配置商品、价格、接单范围与展示规则。"
      onClose={onClose}
      disabled={
        uploading ||
        (fieldsByTab[tab] || [])
          .filter((field) => field.resource)
          .some((field) => optionReady[field.key] === false)
      }
      onSubmit={async () => {
        if (!form.name.trim() || !(Number(form.price_input) > 0))
          throw new Error("请填写商品名称与有效价格。")
        const payload: Row = {
          ...form,
          price: xaCoinToAmount(form.price_input),
          commission_fixed: xaCoinToAmount(form.fixed_input),
          required_level: form.provider_kind
            ? null
            : form.required_level || null,
          game_category: form.game_category || null,
          service_category: form.service_category || null,
          gameplay: form.gameplay || null,
          billing_provider_count: Number(form.billing_provider_count),
          provider_grade: form.provider_kind ? form.provider_grade : "",
          gift_starts_at: form.gift_starts_at || null,
          gift_ends_at: form.gift_ends_at || null,
        }
        delete payload.price_input
        delete payload.fixed_input
        delete payload.id
        ;[
          "game_category_name",
          "service_category_name",
          "gameplay_name",
          "is_gift",
          "created_at",
          "updated_at",
          "required_level_name",
        ].forEach((key) => delete payload[key])
        await request({
          url: `/service-items/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: payload,
        })
        toast.success("商品已保存")
        onSaved()
      }}
    >
      <div className="admin-source-editor space-y-5">
        <div className="min-w-0 space-y-4">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="grid h-11 w-full grid-cols-3">
              <TabsTrigger value="main">基础与价格</TabsTrigger>
              <TabsTrigger value="pricing">抽成与权益</TabsTrigger>
              <TabsTrigger value="content">展示与规则</TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="grid grid-cols-2 gap-4">
            {(fieldsByTab[tab] || []).map((field) => (
              <div key={field.key} className={field.full ? "col-span-2" : ""}>
                {field.richText ? (
                  <div className="space-y-2">
                    <Label>{field.label}</Label>
                    <RichTextEditor
                      value={form[field.key] || ""}
                      label={field.label}
                      onChange={(value) => set(field.key, value)}
                      uploadPath="/catalog-image/"
                      onUploading={setUploading}
                    />
                  </div>
                ) : field.resource ? (
                  <OptionPicker
                    resource={field.resource}
                    endpoint={field.optionPath}
                    label={field.label}
                    value={form[field.key]}
                    onChange={(value) => set(field.key, value)}
                    onAvailabilityChange={(ready) =>
                      setOptionReady((old) =>
                        old[field.key] === ready
                          ? old
                          : { ...old, [field.key]: ready }
                      )
                    }
                  />
                ) : field.type === "boolean" ? (
                  <SourceSwitch
                    field={field}
                    value={form[field.key]}
                    onChange={(value) => set(field.key, value)}
                  />
                ) : (
                  <Field
                    field={field}
                    value={form[field.key]}
                    onChange={(value) => set(field.key, value)}
                  />
                )}
              </div>
            ))}
          </div>
          {tab === "content" && (
            <>
              <div className="space-y-2">
                <Label>上传封面（最大 10MB）</Label>
                <Input
                  type="file"
                  accept="image/*"
                  disabled={uploading}
                  onChange={async (event) => {
                    const file = event.target.files?.[0]
                    if (!file) return
                    setUploading(true)
                    try {
                      const data = new FormData()
                      data.append("image", file)
                      set(
                        "cover_url",
                        (
                          await request({
                            url: "/catalog-image/",
                            method: "POST",
                            data,
                          })
                        ).data.url
                      )
                    } catch (cause: any) {
                      toast.error(cause.message)
                    } finally {
                      setUploading(false)
                    }
                  }}
                />
                {form.cover_url && (
                  <CatalogImage
                    src={form.cover_url}
                    alt="商品封面预览"
                    className="max-h-32 rounded-lg object-contain"
                  />
                )}
              </div>
              {[
                ["rules_images", "规则配图地址，每行一张"],
                ["introduction_images", "介绍配图地址，每行一张"],
                ["images", "商品图片地址，每行一张"],
                ["highlights", "商品亮点，每行一条"],
              ].map(([key, label]) => (
                <Field
                  key={key}
                  field={{ key, label, type: "textarea" }}
                  value={(form[key] || []).join("\n")}
                  onChange={(value) =>
                    set(
                      key,
                      value
                        .split("\n")
                        .map((item: string) => item.trim())
                        .filter(Boolean)
                    )
                  }
                />
              ))}
            </>
          )}
          {tab === "content" && (
            <FormSection title="转盘奖池">
              <div className="space-y-3">
                {form.roulette_pool.map((prize: Row, index: number) => (
                  <div
                    key={index}
                    className="grid grid-cols-[1fr_1fr_80px_auto] gap-2"
                  >
                    <Input
                      aria-label={`奖项 ${index + 1} 名称`}
                      value={prize.name}
                      onChange={(event) =>
                        set(
                          "roulette_pool",
                          form.roulette_pool.map((p: Row, i: number) =>
                            i === index ? { ...p, name: event.target.value } : p
                          )
                        )
                      }
                    />
                    <Input
                      aria-label={`奖项 ${index + 1} 说明`}
                      value={prize.description}
                      onChange={(event) =>
                        set(
                          "roulette_pool",
                          form.roulette_pool.map((p: Row, i: number) =>
                            i === index
                              ? { ...p, description: event.target.value }
                              : p
                          )
                        )
                      }
                    />
                    <Input
                      aria-label={`奖项 ${index + 1} 权重`}
                      type="number"
                      min="1"
                      step="1"
                      value={prize.weight}
                      onChange={(event) =>
                        set(
                          "roulette_pool",
                          form.roulette_pool.map((p: Row, i: number) =>
                            i === index
                              ? { ...p, weight: Number(event.target.value) }
                              : p
                          )
                        )
                      }
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        set(
                          "roulette_pool",
                          form.roulette_pool.filter(
                            (_: any, i: number) => i !== index
                          )
                        )
                      }
                    >
                      移除
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  onClick={() =>
                    set("roulette_pool", [
                      ...form.roulette_pool,
                      { name: "", description: "", weight: 1 },
                    ])
                  }
                >
                  添加转盘奖项
                </Button>
              </div>
            </FormSection>
          )}
        </div>
        {tab === "content" && (
          <details className="rounded-lg border p-4">
            <summary className="cursor-pointer text-sm font-medium">
              商品展示预览
            </summary>
            <div className="mt-4 max-w-sm">
              <DraftPreview
                title={form.name}
                subtitle={form.description || form.rules}
                image={form.cover_url}
                fields={[
                  [
                    "价格",
                    String(form.price_input) +
                      ` ${coinUnit()} / ` +
                      (form.price_unit || "份"),
                  ],
                  [
                    "计价人数",
                    String(form.billing_provider_count || 1) + " 人",
                  ],
                  ["商品状态", form.is_active ? "启用" : "停用"],
                ]}
              />
            </div>
          </details>
        )}
      </div>
    </MutationDialog>
  )
}

export function PromotionsPage() {
  const spec: ResourceSpec = {
    resource: "promotions",
    title: "促销活动",
    description: "按全场、服务分类或指定商品配置活动时间、优惠与抽成。",
    permission: "promotion",
    defaults: {
      title: "",
      scope: "ALL",
      category: null,
      items: [],
      discount_rate: 90,
      commission_rate: null,
      commission_type: "PERCENT",
      commission_fixed: 0,
      start_at: "",
      end_at: "",
      priority: 0,
      remark: "",
      is_active: true,
    },
    fields: [
      { key: "title", label: "活动名称", required: true },
      {
        key: "scope",
        label: "适用范围",
        type: "select",
        options: [
          ["ALL", "全场"],
          ["CATEGORY", "服务分类"],
          ["ITEMS", "指定商品"],
        ],
      },
      {
        key: "category",
        label: "适用服务分类",
        resource: "service-categories",
      },
      {
        key: "items",
        label: "适用商品",
        resource: "service-items",
        multiple: true,
      },
      {
        key: "discount_rate",
        label: "折扣率（%，留空不改价）",
        type: "number",
        min: 1,
        max: 100,
      },
      {
        key: "commission_type",
        label: "抽成方式",
        type: "select",
        options: [
          ["PERCENT", "按比例"],
          ["FIXED", "固定金额"],
        ],
      },
      {
        key: "commission_rate",
        label: "抽成比例（%，留空沿用）",
        type: "number",
        min: 0,
        max: 100,
      },
      {
        key: "commission_fixed",
        label: coinText("固定抽成（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "start_at",
        label: "开始时间",
        type: "datetime-local",
        required: true,
      },
      {
        key: "end_at",
        label: "结束时间",
        type: "datetime-local",
        required: true,
      },
      { key: "priority", label: "优先级", type: "number", min: 0 },
      active,
      { key: "remark", label: "备注", type: "textarea", full: true },
    ],
    columns: [
      { key: "title", label: "活动" },
      { key: "scope", label: "范围" },
      {
        key: "discount_rate",
        label: "折扣率",
        render: (row) =>
          row.discount_rate == null ? "不改价" : `${row.discount_rate}%`,
      },
      {
        key: "start_at",
        label: "开始",
        render: (row) => formatDateTime(row.start_at),
      },
      {
        key: "end_at",
        label: "结束",
        render: (row) => formatDateTime(row.end_at),
      },
      { key: "priority", label: "优先级" },
      stateColumn,
    ],
    money: ["commission_fixed"],
    transform: (data) => {
      if (new Date(data.end_at).getTime() <= new Date(data.start_at).getTime())
        throw new Error("结束时间须晚于开始时间。")
      if (
        (data.scope === "CATEGORY" && !data.category) ||
        (data.scope === "ITEMS" && !data.items?.length)
      )
        throw new Error("请选择活动适用分类或商品。")
      return {
        ...data,
        category: data.scope === "CATEGORY" ? data.category : null,
        items: data.scope === "ITEMS" ? data.items : [],
        discount_rate: data.discount_rate === "" ? null : data.discount_rate,
        commission_rate:
          data.commission_rate === "" ? null : data.commission_rate,
      }
    },
  }
  return <ResourcePage spec={spec} />
}
export function CouponsPage() {
  const [tab, setTab] = useState("coupons")
  const [claimsQuery, setClaimsQuery] = useState<Row>({
    coupon: "",
    status: "",
  })
  const claims = useList("user-coupons", claimsQuery)
  const spec: ResourceSpec = {
    resource: "coupons",
    title: "优惠券",
    description: "管理满减、无门槛、折扣及套餐资格券，核对适用套餐和领取数量。",
    permission: "coupon",
    defaults: {
      name: "",
      discount_type: "THRESHOLD",
      threshold: 0,
      amount: 0,
      discount_rate: 100,
      max_discount: 0,
      scope: "ALL",
      scope_variant: "",
      limit_units: 0,
      is_public: true,
      service_ids: null,
      total_qty: 0,
      experience_kind: "",
      platform: "",
      valid_to: "",
      sort_order: 0,
      is_active: true,
    },
    money: ["threshold", "amount", "max_discount"],
    fields: [
      { key: "name", label: "券名称", required: true },
      {
        key: "discount_type",
        label: "优惠类型",
        type: "select",
        options: [
          ["THRESHOLD", "满减"],
          ["DIRECT", "无门槛"],
          ["RATE", "折扣 / 服务券"],
          ["QUALIFICATION", "套餐资格券"],
        ],
      },
      {
        key: "threshold",
        label: coinText("使用门槛（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "amount",
        label: coinText("面额（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "discount_rate",
        label: "折扣率（%）",
        type: "number",
        min: 0,
        max: 100,
      },
      {
        key: "max_discount",
        label: coinText("优惠上限（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "service_ids",
        label: "适用套餐（资格券必选）",
        resource: "service-items",
        optionPath: "/coupons/service-options/",
        multiple: true,
        full: true,
      },
      {
        key: "scope",
        label: "适用范围",
        type: "select",
        options: [
          ["ALL", "通用"],
          ["COMPANION", "陪玩"],
          ["SAFE_BOX", "33 安全箱"],
          ["EXPERIENCE", "68 体验单"],
        ],
      },
      { key: "scope_variant", label: "范围细分标识" },
      {
        key: "limit_units",
        label: "限用份数（0 为不限）",
        type: "number",
        min: 0,
      },
      {
        key: "total_qty",
        label: "发放总量（0 为不限）",
        type: "number",
        min: 0,
      },
      { key: "valid_to", label: "有效截止时间", type: "datetime-local" },
      {
        key: "experience_kind",
        label: "体验资格",
        type: "select",
        options: [
          ["", "无"],
          ["NEW_VALUE", "新客超值"],
          ["DAILY_VALUE", "每日超值"],
        ],
      },
      {
        key: "platform",
        label: "平台",
        type: "select",
        options: [
          ["", "不限"],
          ["PC", "端游"],
          ["MOBILE", "手游"],
        ],
      },
      { key: "is_public", label: "公开领取", type: "boolean" },
      sort,
      active,
    ],
    columns: [
      nameColumn,
      { key: "discount_type_display", label: "类型" },
      {
        align: "right",
        key: "threshold",
        label: "使用门槛",
        render: (row) => `${amountToXaCoin(row.threshold)} ${coinUnit()}`,
      },
      {
        key: "amount",
        label: "优惠",
        render: (row) =>
          row.discount_type === "RATE"
            ? `${row.discount_rate}%`
            : row.discount_type === "QUALIFICATION"
              ? "套餐资格"
              : `${amountToXaCoin(row.amount)} ${coinUnit()}`,
      },
      {
        key: "claimed_qty",
        label: "已领取 / 总量",
        render: (row) => `${row.claimed_qty} / ${row.total_qty || "不限"}`,
      },
      {
        key: "valid_to",
        label: "截止时间",
        render: (row) => formatDateTime(row.valid_to),
      },
      stateColumn,
    ],
    transform: (data) => ({
      ...data,
      valid_to: data.valid_to || null,
      service_ids: data.service_ids?.length ? data.service_ids : null,
      ...(data.discount_type === "QUALIFICATION"
        ? { threshold: 0, amount: 0, discount_rate: 100, limit_units: 1 }
        : {}),
      ...(data.discount_type === "DIRECT" ? { threshold: 0 } : {}),
    }),
  }
  return (
    <DataPageState
      loading={tab !== "coupons" && claims.loading}
      hasSnapshot={tab === "coupons" || !!claims.data}
      error={tab === "coupons" ? "" : claims.error}
      retry={claims.reload}
      skeleton="coupon-claims"
    >
      {
        <div className="space-y-4">
          <div className="flex gap-2 border-b pb-3">
            <Button
              variant={tab === "coupons" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setTab("coupons")}
            >
              券配置
            </Button>
            <Button
              variant={tab === "claims" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setTab("claims")}
            >
              领取与核销记录
            </Button>
          </div>
          {tab === "coupons" ? (
            <ResourcePage spec={spec} />
          ) : (
            <>
              <SourcePageHeader
                title="优惠券领取与核销"
                description="核对领取人、券状态与使用订单。"
              />
              <div className="grid max-w-3xl grid-cols-2 gap-4">
                <OptionPicker
                  resource="coupons"
                  endpoint="/coupons/"
                  label="优惠券"
                  value={claimsQuery.coupon}
                  onChange={(value) =>
                    setClaimsQuery((old) => ({ ...old, coupon: value }))
                  }
                />
                <Field
                  field={{
                    key: "claim-status",
                    label: "领取状态",
                    type: "select",
                    options: [
                      ["UNUSED", "未使用"],
                      ["USED", "已使用"],
                      ["EXPIRED", "已过期"],
                      ["DISABLED", "模板停用"],
                      ["PREVIOUS_CYCLE", "非当前周期"],
                    ],
                  }}
                  value={claimsQuery.status}
                  onChange={(value) =>
                    setClaimsQuery((old) => ({ ...old, status: value }))
                  }
                />
              </div>
              <Feedback message={claims.error} retry={claims.reload} />
              <SourceDirectoryCard snapshotAvailable={!!claims.data} retry={claims.reload}
                title="领取与核销记录"
                icon={Gift}
                rows={claims.rows}
                loading={claims.loading}
                columns={[
                  { key: "id", label: "记录" },
                  { key: "user_name", label: "领取人" },
                  { key: "coupon_name", label: "优惠券" },
                  { align: "center", key: "status_display", label: "状态" },
                  { key: "order_no", label: "使用订单" },
                  {
                    key: "claimed_at",
                    label: "领取时间",
                    render: (row) => formatDateTime(row.claimed_at),
                  },
                  {
                    key: "used_at",
                    label: "使用时间",
                    render: (row) => formatDateTime(row.used_at),
                  },
                  {
                    key: "effective_valid_to",
                    label: "有效期",
                    render: (row) => formatDateTime(row.effective_valid_to),
                  },
                ]}
              />
              <Pagination snapshotAvailable={!!claims.data}
                page={claims.page}
                total={claims.total}
                onChange={claims.setPage}
                pageSize={claims.pageSize}
                onPageSizeChange={claims.setPageSize}
                loading={claims.loading}
              />
            </>
          )}
        </div>
      }
    </DataPageState>
  )
}

export function BannersPage() {
  const list = useList("banners")
  const auth = useAdminAuth()
  const review = useSourceReviewState("/admin/banners/")
  const locked = list.loading || !!list.error || review.locked
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="banners"
    >
      <div className="admin-source-page space-y-4" data-resource="banners">
        <SourcePageHeader
          title="首页 Banner"
          tag="首页运营"
          description="配置首页轮播、跳转目标与展示顺序。"
          metrics={[
            { label: "符合条件", value: list.total },
            {
              label: "本页启用",
              value: list.rows.filter((row) => row.is_active).length,
            },
          ]}
        >
          <RefreshButton loading={list.loading} reload={list.reload} />
          {auth.hasPerm("banner:edit") && (
            <Button
              variant="secondary"
              disabled={locked}
              onClick={() =>
                setEditing({
                  title: "",
                  link_type: "NONE",
                  link_value: "",
                  sort_order: 0,
                  is_active: true,
                })
              }
            >
              <Plus className="size-4" />
              新增 Banner
            </Button>
          )}
        </SourcePageHeader>
        <ReadOnlyNotice
          canEdit={auth.hasPerm("banner:edit")}
          canDelete={auth.hasPerm("banner:delete")}
          subject="轮播"
        />
        <Feedback message={list.error} retry={list.reload} />
        <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
          title="Banner 列表"
          icon={Images}
          description={`本页 ${list.rows.length} 项 · 图片标准 750 × 320`}
          minWidth={1040}
          filters={
            <AdminFilters title="轮播筛选" primary={<>
              <div className="space-y-2">
              <Label htmlFor="banner-keyword">标题 / 跳转目标</Label>
              <Input
                id="banner-keyword"
                aria-label="标题或目标筛选待开放"
                placeholder="待开放：标题或目标…"
                disabled
              />
              </div>
              <div className="space-y-2">
              <Label htmlFor="banner-target-type">跳转类型</Label>
              <Select name="banner_target_type" autoComplete="off" disabled>
                <SelectTrigger id="banner-target-type" aria-label="跳转类型筛选待开放">
                  <SelectValue placeholder="全部类型 · 待开放" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部类型</SelectItem>
                </SelectContent>
              </Select>
              </div>
              <div className="space-y-2">
              <Label htmlFor="banner-status">状态</Label>
              <Select name="banner_status" autoComplete="off" disabled>
                <SelectTrigger id="banner-status" aria-label="状态筛选待开放">
                  <SelectValue placeholder="全部状态 · 待开放" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部状态</SelectItem>
                </SelectContent>
              </Select>
              </div>
            </>} actions={<Button disabled>
                <Search className="size-4" />
                查询
              </Button>} />
          }
          rows={list.rows}
          loading={list.loading}
          error={list.error}
          locked={review.locked}
          empty="没有匹配的 Banner"
          columns={[
            {
              key: "image_url",
              label: "Banner 与封面",
              render: (row) => (
                <div className="flex items-center gap-3">
                  {row.image_url ? (
                    <CatalogImage
                      src={row.image_url}
                      alt={row.title || "轮播图"}
                      className="h-[54px] w-[126px] shrink-0 rounded-lg border object-cover shadow-sm"
                    />
                  ) : (
                    <span className="grid h-[54px] w-[126px] shrink-0 place-items-center rounded-lg border bg-muted">
                      <Images className="size-6 text-muted-foreground" />
                    </span>
                  )}
                  <div>
                    <p className="text-sm font-semibold">
                      {row.title || `Banner #${row.id}`}
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      ID {row.id}
                    </p>
                  </div>
                </div>
              ),
            },
            {
              key: "link_type",
              label: "跳转目标",
              render: (row) => (
                <div>
                  <Badge variant="outline">{enumLabel(row.link_type)}</Badge>
                  <p className="mt-1 max-w-52 text-xs break-all text-muted-foreground">
                    {row.link_value || "不跳转"}
                  </p>
                </div>
              ),
            },
            {
              key: "link_available",
              label: "目标可用性",
              align: "center",
              render: (row) =>
                row.link_type === "NONE" ? (
                  <span className="text-xs text-muted-foreground">
                    无需目标
                  </span>
                ) : (
                  <Status
                    readState={typeof row.link_available === "boolean" ? undefined : "unread"}
                    value={
                      row.link_available === false ? "PENDING" : "AVAILABLE"
                    }
                    label={
                      row.link_available === false ? "目标需核对" : "目标可用"
                    }
                  />
                ),
            },
            { key: "sort_order", label: "排序", align: "right" },
            {
              key: "is_active",
              label: "状态",
              align: "center",
              render: (row) => (
                <ResourceToggle
                  resource="banners"
                  row={row}
                  field="is_active"
                  canEdit={auth.hasPerm("banner:edit")}
                  locked={locked}
                  onSaved={list.reload}
                />
              ),
            },
            {
              key: "actions",
              label: "操作",
              align: "right",
              render: (row) => (
                <div className="flex justify-end gap-1">
                  {auth.hasPerm("banner:edit") && (
                    <Button
                      actionTone="edit"
                      size="sm"
                      variant="outline"
                      disabled={locked}
                      onClick={() => setEditing(row)}
                    >
                      编辑
                    </Button>
                  )}
                  {auth.hasPerm("banner:delete") && (
                    <Button
                      actionTone="reject"
                      size="sm"
                      variant="ghost"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      disabled={locked}
                      onClick={() => setRemove(row)}
                    >
                      删除
                    </Button>
                  )}
                </div>
              ),
            },
          ]}
          footer={
            <Pagination snapshotAvailable={!!list.data}
              page={list.page}
              total={list.total}
              loading={list.loading}
              onChange={list.setPage}
              pageSize={list.pageSize}
              onPageSizeChange={list.setPageSize}
            />
          }
        />
        {editing && (
          <BannerEditor
            row={editing}
            locked={locked}
            onClose={() => setEditing(null)}
            onSaved={list.reload}
          />
        )}
        {remove && (
          <MutationDialog
            title="删除 Banner"
            submitVariant="destructive"
            readOnly={locked}
            description={`确认删除「${remove.title || `Banner #${remove.id}`}」？删除后老板端不再展示。`}
            onClose={() => setRemove(null)}
            onSubmit={async () => {
              await request({ url: `/banners/${remove.id}/`, method: "DELETE" })
              toast.success("已删除轮播")
              void list.reload()
            }}
          />
        )}
      </div>
    </DataPageState>
  )
}
function BannerEditor({
  row,
  locked,
  onClose,
  onSaved,
}: {
  row: Row
  locked: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({ ...row })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState(row.image_url || "")
  const [error, setError] = useState("")
  const [targetReady, setTargetReady] = useState(false)
  useEffect(
    () => () => {
      if (preview.startsWith("blob:")) URL.revokeObjectURL(preview)
    },
    [preview]
  )
  const resource = (
    {
      PRODUCT: "service-items",
      ANNOUNCEMENT: "announcements",
      GAMEPLAY: "gameplays",
      CONTENT: "club-content",
    } as Row
  )[form.link_type]
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft || !!file}
      confirmDiscard
      contentClassName="sm:max-w-5xl"
      readOnly={locked}
      title={row.id ? "编辑轮播" : "新增轮播"}
      description="上传标准尺寸图片，并选择真实业务目标。"
      onClose={onClose}
      disabled={(!row.id && !file) || !!error || (!!resource && !targetReady)}
      onSubmit={async () => {
        if (form.link_type !== "NONE" && !form.link_value)
          throw new Error("请选择跳转目标或填写链接。")
        const body = new FormData()
        if (file) body.append("image", file)
        for (const key of [
          "title",
          "link_type",
          "link_value",
          "sort_order",
          "is_active",
        ])
          body.append(key, String(form[key] ?? ""))
        const result = await request({
          url: `/banners/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: body,
        })
        if (result.data.link_available === false)
          toast.warning("轮播已保存，但目标当前不可用，请核对或停用")
        else toast.success("轮播已保存")
        onSaved()
      }}
    >
      <div className="admin-source-editor grid gap-6 lg:grid-cols-[minmax(0,1fr)_290px]">
        <FormSection title="图片与跳转配置">
          <div className="space-y-4">
            <Field
              field={{ key: "banner-title", label: "标题" }}
              value={form.title}
              onChange={(value) => setForm((old) => ({ ...old, title: value }))}
            />
            <div className="space-y-2">
              <Label htmlFor="banner-image">轮播图片（750 × 320 像素）</Label>
              <Input
                id="banner-image"
                type="file"
                accept="image/*"
                onChange={async (event) => {
                  const selected = event.target.files?.[0]
                  if (!selected) return
                  const url = URL.createObjectURL(selected)
                  const image = new Image()
                  image.onload = () => {
                    if (
                      image.naturalWidth !== 750 ||
                      image.naturalHeight !== 320
                    ) {
                      setError(
                        `图片尺寸须为 750 × 320，当前为 ${image.naturalWidth} × ${image.naturalHeight}`
                      )
                      URL.revokeObjectURL(url)
                      setFile(null)
                      return
                    }
                    setError("")
                    setFile(selected)
                    setPreview(url)
                  }
                  image.onerror = () => {
                    setError("图片无法读取，请重新选择。")
                    URL.revokeObjectURL(url)
                  }
                  image.src = url
                }}
              />
              {preview && (
                <CatalogImage
                  src={preview}
                  alt="轮播图片预览"
                  className="max-h-40 rounded-lg border object-contain"
                />
              )}
            </div>
            <Feedback message={error} />
            <Field
              field={{
                key: "link_type",
                label: "跳转类型",
                type: "select",
                options: [
                  ["NONE", "无跳转"],
                  ["PRODUCT", "商品详情"],
                  ["ANNOUNCEMENT", "公告详情"],
                  ["GAMEPLAY", "玩法服务"],
                  ["CONTENT", "活动与权益"],
                  ["PAGE", "业务入口"],
                  ["URL", "外部链接"],
                ],
              }}
              value={form.link_type}
              onChange={(value) =>
                setForm((old) => ({ ...old, link_type: value, link_value: "" }))
              }
            />
            {resource ? (
              <OptionPicker
                resource={resource}
                label="跳转目标"
                value={form.link_value}
                onAvailabilityChange={setTargetReady}
                onChange={(value) =>
                  setForm((old) => ({
                    ...old,
                    link_value: value ? String(value) : "",
                  }))
                }
              />
            ) : form.link_type === "PAGE" ? (
              <Field
                field={{
                  key: "banner-page",
                  label: "业务入口",
                  type: "select",
                  options: [
                    ["catalog", "服务商城"],
                    ["companions", "陪玩列表"],
                    ["gifts", "礼物单"],
                    ["coupons", "领券中心"],
                    ["checkin", "每日签到"],
                    ["points", "积分好礼"],
                    ["membership", "会员中心"],
                    ["support", "联系客服"],
                  ],
                }}
                value={form.link_value}
                onChange={(value) =>
                  setForm((old) => ({ ...old, link_value: value }))
                }
              />
            ) : (
              form.link_type !== "NONE" && (
                <Field
                  field={{
                    key: "link_value",
                    label:
                      form.link_type === "URL"
                        ? "完整 HTTPS 链接"
                        : "业务入口标识",
                  }}
                  value={form.link_value}
                  onChange={(value) =>
                    setForm((old) => ({ ...old, link_value: value }))
                  }
                />
              )
            )}
            <div className="grid grid-cols-2 gap-4">
              <Field
                field={sort}
                value={form.sort_order}
                onChange={(value) =>
                  setForm((old) => ({ ...old, sort_order: value }))
                }
              />
              <Field
                field={active}
                value={form.is_active}
                onChange={(value) =>
                  setForm((old) => ({ ...old, is_active: value }))
                }
              />
            </div>
          </div>
        </FormSection>
        <aside className="space-y-3">
          <p className="text-xs font-medium text-muted-foreground">
            老板端轮播预览
          </p>
          <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
            <div className="relative aspect-[750/320] bg-[#10285b]">
              {preview ? (
                <CatalogImage
                  src={preview}
                  alt="轮播预览"
                  className="size-full object-cover"
                />
              ) : (
                <div className="grid h-full place-items-center p-6 text-center text-xs text-blue-100/60">
                  选择图片后预览实际比例
                </div>
              )}
            </div>
            <div className="space-y-3 p-4">
              <p className="line-clamp-2 min-h-10 text-sm leading-5 font-medium">
                {form.title || "无标题 Banner"}
              </p>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">{enumLabel(form.link_type)}</Badge>
                <Status
                  value={form.is_active}
                  label={form.is_active ? "启用" : "停用"}
                />
              </div>
              <p className="text-[11px] leading-5 break-words text-muted-foreground">
                {form.link_value || "不跳转"}
              </p>
            </div>
          </div>
        </aside>
      </div>
    </MutationDialog>
  )
}
