import { AdminFilters } from "@/components/AdminFilters"
import { DataRegion } from "@/components/page-data-state"
import {
  DataTable,
  Feedback,
  PageHeader,
  Pagination,
  Status,
  type Column,
  type Row,
} from "@/components/business"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  PLATFORM_AFTERSALE_STATUS_LABELS,
  PLATFORM_DISPATCH_ROUTE_LABELS,
  PLATFORM_ORDER_MAX_PAGE,
  PLATFORM_ORDER_PAGE_SIZE,
  PLATFORM_ORDER_SOURCE_LABELS,
  PLATFORM_ORDER_STATUS_LABELS,
  PLATFORM_PAYMENT_STATUS_LABELS,
  PLATFORM_SETTLEMENT_STATUS_LABELS,
  buildPlatformOrderQuery,
  canReadPlatformOrderStore,
  hasPlatformOrderReadAccess,
  hasPlatformOrderRole,
  initialKeyedRead,
  keyedReadReducer,
  orderIdentity,
  parsePlatformOrderDetail,
  parsePlatformOrderPage,
  parsePlatformOrderSearch,
  parsePlatformStoreOptions,
  platformOrderListAvailable,
  platformOrderSearchWithFilters,
  type KeyedReadState,
  type PlatformOrder,
  type PlatformOrderFilters,
} from "@/lib/platform-orders"
import { platformRequest, type PlatformProfile } from "@/lib/platform-session"
import { formatDateTime } from "@xa/shared"
import { X } from "lucide-react"
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useReducer,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
  type RefObject,
} from "react"
import { useSearchParams } from "react-router"
import "./saas.css"

type Club = { slug: string; name: string }
type OrderRow = Row & PlatformOrder & { row_key: string }

const EMPTY_FILTERS: PlatformOrderFilters = {
  store: "",
  source: "",
  status: "",
  payment: "",
  keyword: "",
}

export default function PlatformOrders({
  profile,
  stores,
  storesLoading,
  storesError,
  storesRetry,
}: {
  profile: PlatformProfile
  stores: Club[]
  storesLoading: boolean
  storesError?: string
  storesRetry: () => void
}) {
  if (!hasPlatformOrderRole(profile)) {
    return (
      <OrderGate
        title="暂无访问权限"
        description="当前平台身份未获得统一订单读取授权，请联系平台超级管理员。"
      />
    )
  }
  if (!hasPlatformOrderReadAccess(profile)) {
    return (
      <OrderGate
        title="统一订单服务暂未开放"
        description="当前账号尚未获得 PLATFORM_ORDERS_V1 能力，未发起订单读取。"
      />
    )
  }
  return (
    <PlatformOrdersView
      profile={profile}
      stores={stores}
      storesLoading={storesLoading}
      storesError={storesError}
      storesRetry={storesRetry}
    />
  )
}

function OrderGate({
  title,
  description,
}: {
  title: string
  description: string
}) {
  return (
    <div className="platform-orders-page max-w-full min-w-0 space-y-5">
      <PageHeader
        title="统一订单"
        description="按授权俱乐部只读核对订单、支付和履约状态。"
      />
      <section className="saas-section">
        <h2>{title}</h2>
        <p className="saas-note">{description}</p>
      </section>
    </div>
  )
}

function PlatformOrdersView({
  profile,
  stores,
  storesLoading,
  storesError,
  storesRetry,
}: {
  profile: PlatformProfile
  stores: Club[]
  storesLoading: boolean
  storesError?: string
  storesRetry: () => void
}) {
  const [params, setParams] = useSearchParams()
  const searchKey = params.toString()
  const parsed = useMemo(() => {
    try {
      return { value: parsePlatformOrderSearch(searchKey), error: "" }
    } catch (cause) {
      return {
        value: null,
        error:
          cause instanceof Error
            ? cause.message
            : "筛选参数无效，请清除后重试。",
      }
    }
  }, [searchKey])
  const filters = parsed.value?.filters ?? EMPTY_FILTERS
  const page = parsed.value?.page ?? 1
  const queryKey = parsed.value ? buildPlatformOrderQuery(filters, page) : ""
  const [draft, setDraft] = useState<PlatformOrderFilters>(filters)
  const [formError, setFormError] = useState("")
  const [detailTarget, setDetailTarget] = useState<PlatformOrder | null>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const filterId = useId()

  useEffect(() => {
    try {
      setDraft(parsePlatformOrderSearch(searchKey).filters)
      setFormError("")
    } catch {
      setDraft(EMPTY_FILTERS)
    }
    setDetailTarget(null)
  }, [searchKey])

  const loadPage = useCallback(async () => {
    if (!queryKey) throw new Error("筛选条件无效，未发起订单读取")
    const value = await platformRequest<unknown>(`orders?${queryKey}`)
    return parsePlatformOrderPage(value, {
      store: filters.store || undefined,
      pageSize: PLATFORM_ORDER_PAGE_SIZE,
    })
  }, [filters.store, queryKey])
  const [read, retry] = useKeyedRead(queryKey, loadPage, !!queryKey)
  const current = read.data

  const directory = useMemo(() => {
    try {
      return { data: parsePlatformStoreOptions(stores), error: "" }
    } catch (cause) {
      return {
        data: [] as Club[],
        error: cause instanceof Error ? cause.message : "门店目录格式异常。",
      }
    }
  }, [stores])
  const readableStores = directory.data.filter((store) =>
    canReadPlatformOrderStore(profile, store.slug)
  )
  const directoryError = storesError || directory.error
  const activeCount = Object.values(draft).filter(Boolean).length

  const closeDetail = useCallback(() => {
    setDetailTarget(null)
  }, [])

  function openDetail(order: PlatformOrder) {
    returnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    setDetailTarget(order)
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault()
    try {
      const next = platformOrderSearchWithFilters(searchKey, draft)
      setFormError("")
      if (next !== searchKey) setParams(next)
      else retry()
    } catch (cause) {
      setFormError(
        cause instanceof Error ? cause.message : "筛选条件无效，请检查后重试。"
      )
    }
  }

  function clearFilters() {
    setDraft(EMPTY_FILTERS)
    setFormError("")
    setParams({})
  }

  function changePage(next: number) {
    const nextParams = new URLSearchParams(searchKey)
    nextParams.set("page", String(next))
    setParams(nextParams)
  }

  const filterForm = (
    <form onSubmit={applyFilters} className="min-w-0">
      <AdminFilters
        title="统一订单筛选"
        activeCount={activeCount}
        primary={
          <>
            <label htmlFor={`${filterId}-keyword`}>
              订单号或老板昵称
              <Input
                id={`${filterId}-keyword`}
                value={draft.keyword}
                maxLength={100}
                placeholder="输入订单号或老板昵称"
                onChange={(event) =>
                  setDraft({ ...draft, keyword: event.target.value })
                }
              />
            </label>
            <label htmlFor={`${filterId}-store`}>
              门店
              <select
                id={`${filterId}-store`}
                className="saas-select"
                value={draft.store}
                disabled={storesLoading || !!directoryError}
                aria-describedby={`${filterId}-store-state`}
                onChange={(event) =>
                  setDraft({ ...draft, store: event.target.value })
                }
              >
                <option value="">全部授权门店</option>
                {readableStores.map((store) => (
                  <option key={store.slug} value={store.slug}>
                    {store.name}
                  </option>
                ))}
              </select>
            </label>
          </>
        }
        actions={
          <>
            <Button type="submit">查询</Button>
            <Button type="button" variant="outline" onClick={clearFilters}>
              清除筛选
            </Button>
          </>
        }
      >
        <label htmlFor={`${filterId}-source`}>
          下单来源
          <select
            id={`${filterId}-source`}
            className="saas-select"
            value={draft.source}
            onChange={(event) =>
              setDraft({
                ...draft,
                source: event.target.value as PlatformOrderFilters["source"],
              })
            }
          >
            <option value="">全部来源</option>
            {Object.entries(PLATFORM_ORDER_SOURCE_LABELS).map(
              ([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              )
            )}
          </select>
        </label>
        <label htmlFor={`${filterId}-status`}>
          订单状态
          <select
            id={`${filterId}-status`}
            className="saas-select"
            value={draft.status}
            onChange={(event) =>
              setDraft({
                ...draft,
                status: event.target.value as PlatformOrderFilters["status"],
              })
            }
          >
            <option value="">全部状态</option>
            {Object.entries(PLATFORM_ORDER_STATUS_LABELS).map(
              ([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              )
            )}
          </select>
        </label>
        <label htmlFor={`${filterId}-payment`}>
          支付状态
          <select
            id={`${filterId}-payment`}
            className="saas-select"
            value={draft.payment}
            onChange={(event) =>
              setDraft({
                ...draft,
                payment: event.target.value as PlatformOrderFilters["payment"],
              })
            }
          >
            <option value="">全部支付</option>
            {Object.entries(PLATFORM_PAYMENT_STATUS_LABELS).map(
              ([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              )
            )}
          </select>
        </label>
      </AdminFilters>
      {formError && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {formError}
        </p>
      )}
      <p
        id={`${filterId}-store-state`}
        className="mt-2 text-xs text-muted-foreground"
      >
        {storesLoading
          ? "正在读取平台门店目录…"
          : directoryError
            ? "门店目录暂不可用，门店筛选已停用。"
            : readableStores.length
              ? `门店选项来自平台目录，共 ${readableStores.length} 家授权门店。`
              : "暂无可选门店，订单范围仍由服务端授权决定。"}
      </p>
    </form>
  )

  const columns: Column[] = [
    {
      key: "order",
      label: "门店 / 订单",
      render: (row) => {
        const order = row as OrderRow
        return (
          <div className="min-w-0">
            <p className="font-medium">{order.store_name}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {order.order_no}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {formatDateTime(order.created_at)}
            </p>
          </div>
        )
      },
    },
    {
      key: "people",
      label: "老板 / 服务 / 陪玩",
      render: (row) => {
        const order = row as OrderRow
        return (
          <div className="min-w-0">
            <p>{order.customer_name || "老板未记录"}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {order.service_name || "服务未记录"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              陪玩：{order.provider_names.join("、") || "待安排"}
            </p>
          </div>
        )
      },
    },
    {
      key: "source",
      label: "来源 / 派单方式",
      render: (row) => {
        const order = row as OrderRow
        return (
          <div className="min-w-0 text-xs">
            <p>
              {order.order_source
                ? PLATFORM_ORDER_SOURCE_LABELS[order.order_source]
                : "来源待核对"}
            </p>
            <p className="mt-1 text-muted-foreground">
              {PLATFORM_DISPATCH_ROUTE_LABELS[order.dispatch_route]}
            </p>
          </div>
        )
      },
    },
    {
      key: "state",
      label: "订单 / 支付 / 结算",
      render: (row) => {
        const order = row as OrderRow
        return (
          <div className="min-w-0 space-y-2">
            <Status
              value={order.status}
              label={PLATFORM_ORDER_STATUS_LABELS[order.status]}
            />
            <div>
              <Status
                value={order.payment_display ? "REVIEW" : order.payment_status}
                label={
                  order.payment_display
                    ? "支付状态待核对"
                    : PLATFORM_PAYMENT_STATUS_LABELS[order.payment_status]
                }
              />
            </div>
            <p className="text-xs text-muted-foreground">
              结算：{PLATFORM_SETTLEMENT_STATUS_LABELS[order.settlement_status]}
            </p>
          </div>
        )
      },
    },
    {
      key: "actions",
      label: "详情",
      align: "center",
      render: (row) => {
        const order = row as OrderRow
        return (
          <Button
            type="button"
            actionTone="read"
            variant="outline"
            size="sm"
            disabled={read.phase !== "ready"}
            aria-label={`查看${order.store_name}订单${order.order_no}详情`}
            onClick={() => openDetail(order)}
          >
            查看详情
          </Button>
        )
      },
    },
  ]

  const rows: OrderRow[] = (current?.results ?? []).map((order) => ({
    ...order,
    row_key: orderIdentity(order),
  }))
  const readError = parsed.error || read.error
  const announcement = parsed.error
    ? `筛选参数无效：${parsed.error}`
    : read.phase === "loading"
      ? "正在读取统一订单。"
      : read.phase === "error"
        ? `订单读取失败：${read.error}`
        : current
          ? current.complete
            ? `订单读取完成，共 ${current.total} 笔。`
            : `订单部分读取，已读取 ${current.total} 笔，缺失门店 ${current.missing_stores.join("、")}。`
          : ""

  return (
    <div className="platform-orders-page max-w-full min-w-0 space-y-5">
      <PageHeader
        title="统一订单"
        description="按授权俱乐部只读核对订单、支付和履约状态；不提供派单、编辑、代单或资金处理。"
        tag="只读"
      />
      <p
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {announcement}
      </p>
      <div className="xa-role-scope">
        <strong>平台只读视图</strong>
        <span>
          门店范围由服务端授权；列表与详情仅展示安全投影，不包含金额、联系方式和内部备注。
        </span>
      </div>
      {directoryError && (
        <Feedback message={directoryError} retry={storesRetry} />
      )}
      {current && !current.complete && (
        <div
          role="note"
          className="rounded-md border border-warning/40 bg-warning/5 p-4 text-sm"
        >
          <p className="font-medium">
            部分门店数据未读取，当前总数不是全平台完整总数。
          </p>
          <p className="mt-1 text-muted-foreground">
            缺失门店：{current.missing_stores.join("、")}
            。以下仅包含已读取门店的数据，缺失部分不按零计算。
          </p>
        </div>
      )}
      <DataTable
        title="订单列表"
        description={
          current
            ? `读取于 ${formatDateTime(current.generated_at)}；每页 ${PLATFORM_ORDER_PAGE_SIZE} 条。`
            : "仅展示后端安全投影。"
        }
        rows={rows}
        columns={columns}
        rowKey="row_key"
        loading={read.phase === "loading"}
        error={readError}
        retry={parsed.error ? clearFilters : retry}
        snapshotAvailable={platformOrderListAvailable(current)}
        empty="没有匹配的订单"
        emptyDescription="请调整门店、来源、状态或关键词后重新查询。"
        emptyAction={
          <Button type="button" variant="outline" onClick={clearFilters}>
            清除筛选
          </Button>
        }
        toolbar={filterForm}
        tableClassName="table-fixed"
        columnWidths={{
          order: "22%",
          people: "27%",
          source: "15%",
          state: "24%",
          actions: "12%",
        }}
      />
      <div className="min-w-0">
        <p className="mb-2 text-xs text-muted-foreground">
          {current
            ? `${current.complete ? "完整数据" : "部分数据"} · 第 ${page} 页 · 读取于 ${formatDateTime(current.generated_at)}`
            : read.phase === "loading"
              ? "总数与页码读取中…"
              : "总数与页码尚未读取"}
        </p>
        <Pagination
          page={page}
          total={current?.total ?? 0}
          pageSize={PLATFORM_ORDER_PAGE_SIZE}
          maximumPage={PLATFORM_ORDER_MAX_PAGE}
          loading={read.phase === "loading"}
          snapshotAvailable={!!current}
          onChange={changePage}
        />
      </div>
      {detailTarget && (
        <PlatformOrderDetailDrawer
          target={detailTarget}
          returnFocus={returnFocus}
          onClose={closeDetail}
        />
      )}
    </div>
  )
}

function PlatformOrderDetailDrawer({
  target,
  returnFocus,
  onClose,
}: {
  target: PlatformOrder
  returnFocus: RefObject<HTMLElement | null>
  onClose: () => void
}) {
  const path =
    `orders/${encodeURIComponent(target.store_slug)}/${encodeURIComponent(target.id)}` as const
  const loadDetail = useCallback(async () => {
    const value = await platformRequest<unknown>(path)
    const detail = parsePlatformOrderDetail(value)
    if (orderIdentity(detail) !== orderIdentity(target)) {
      throw new Error("订单详情所属门店或编号不一致，已停止展示，请重新打开。")
    }
    return detail
  }, [path, target])
  const [read, retry] = useKeyedRead(path, loadDetail, true)
  const order = read.data

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        className="w-full max-w-[calc(100vw-1rem)] sm:max-w-xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocus.current?.focus()
        }}
      >
        <SheetClose asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="absolute top-4 right-4 z-10"
            aria-label="关闭订单详情"
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        </SheetClose>
        <SheetHeader className="pr-14">
          <SheetTitle>
            {target.store_name} · {target.order_no}
          </SheetTitle>
          <SheetDescription>
            只读安全投影；状态事件仅包含时间、动作和操作人。
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          <DataRegion
            label="订单详情"
            loading={read.phase === "loading"}
            hasSnapshot={!!order}
            error={read.error}
            retry={retry}
          >
            {order && <OrderDetailContent order={order} />}
          </DataRegion>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function OrderDetailContent({
  order,
}: {
  order: PlatformOrder & {
    events: { id: string; at: string; title: string; actor: string | null }[]
  }
}) {
  return (
    <div className="min-w-0 space-y-6">
      <dl className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
        <DetailField label="门店">
          {order.store_name}
          <span className="mt-1 block text-xs text-muted-foreground">
            {order.store_slug}
          </span>
        </DetailField>
        <DetailField label="订单编号">{order.order_no}</DetailField>
        <DetailField label="订单 ID">{order.id}</DetailField>
        <DetailField label="记录版本">
          {formatDateTime(order.version)}
        </DetailField>
        <DetailField label="老板">
          {order.customer_name || "未记录"}
        </DetailField>
        <DetailField label="服务">{order.service_name || "未记录"}</DetailField>
        <DetailField label="陪玩">
          {order.provider_names.join("、") || "待安排"}
        </DetailField>
        <DetailField label="币种">{order.currency_name}</DetailField>
        <DetailField label="下单来源">
          {order.order_source
            ? PLATFORM_ORDER_SOURCE_LABELS[order.order_source]
            : "来源待核对"}
        </DetailField>
        <DetailField label="派单方式">
          {PLATFORM_DISPATCH_ROUTE_LABELS[order.dispatch_route]}
        </DetailField>
        <DetailField label="订单状态">
          <Status
            value={order.status}
            label={PLATFORM_ORDER_STATUS_LABELS[order.status]}
          />
        </DetailField>
        <DetailField label="支付状态">
          <Status
            value={order.payment_display ? "REVIEW" : order.payment_status}
            label={
              order.payment_display
                ? "支付状态待核对"
                : PLATFORM_PAYMENT_STATUS_LABELS[order.payment_status]
            }
          />
        </DetailField>
        <DetailField label="售后状态">
          {order.aftersale_status ? (
            <Status
              value={order.aftersale_status}
              label={PLATFORM_AFTERSALE_STATUS_LABELS[order.aftersale_status]}
            />
          ) : (
            "无售后记录"
          )}
        </DetailField>
        <DetailField label="结算状态">
          <Status
            value={order.settlement_status}
            label={PLATFORM_SETTLEMENT_STATUS_LABELS[order.settlement_status]}
          />
        </DetailField>
        <DetailField label="需要老板选陪玩">
          {order.requires_boss_selection ? "是" : "否"}
        </DetailField>
        <DetailField label="派单时限已过">
          {order.pending_deadline_expired ? "是" : "否"}
        </DetailField>
        <DetailField label="创建时间">
          {formatDateTime(order.created_at)}
        </DetailField>
        <DetailField label="接单时间">
          {formatDateTime(order.grabbed_at)}
        </DetailField>
        <DetailField label="开始时间">
          {formatDateTime(order.started_at)}
        </DetailField>
        <DetailField label="服务中时间">
          {formatDateTime(order.in_service_at)}
        </DetailField>
        <DetailField label="完成时间">
          {formatDateTime(order.completed_at)}
        </DetailField>
        <DetailField label="取消时间">
          {formatDateTime(order.cancelled_at)}
        </DetailField>
        <DetailField label="更新时间">
          {formatDateTime(order.updated_at)}
        </DetailField>
      </dl>
      <section
        aria-labelledby="platform-order-events"
        className="min-w-0 border-t pt-5"
      >
        <h3 id="platform-order-events" className="text-sm font-semibold">
          状态事件
        </h3>
        {order.events.length ? (
          <ol className="mt-4 space-y-4">
            {order.events.map((event) => (
              <li
                key={event.id}
                className="min-w-0 border-l-2 border-border pl-4"
              >
                <p className="text-sm font-medium">{event.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <time dateTime={event.at}>{formatDateTime(event.at)}</time> ·
                  操作人：{event.actor || "未记录"}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground">暂无状态事件。</p>
        )}
      </section>
      <p className="saas-note">
        当前无可执行动作。需要派单、编辑、代单或资金处理时，请由对应门店的授权人员在本店后台办理。
      </p>
    </div>
  )
}

function DetailField({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 min-w-0 text-sm [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  )
}

function useKeyedRead<T>(
  key: string,
  loader: () => Promise<T>,
  enabled: boolean
): readonly [KeyedReadState<T>, () => void] {
  const [state, dispatch] = useReducer(
    keyedReadReducer<T>,
    key,
    initialKeyedRead<T>
  )
  const [revision, setRevision] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const retry = useCallback(() => setRevision((value) => value + 1), [])

  useEffect(() => {
    if (!enabled) {
      dispatch({ type: "reset", key })
      return
    }
    let active = true
    dispatch({ type: "start", key })
    loaderRef
      .current()
      .then((data) => {
        if (active) dispatch({ type: "success", key, data })
      })
      .catch((cause: unknown) => {
        if (active)
          dispatch({
            type: "failure",
            key,
            error:
              cause instanceof Error ? cause.message : "读取失败，请重试。",
          })
      })
    return () => {
      active = false
    }
  }, [enabled, key, revision])

  const visible = state.key === key ? state : initialKeyedRead<T>(key)
  return [
    enabled && visible.phase === "idle" ? { key, phase: "loading" } : visible,
    retry,
  ] as const
}
