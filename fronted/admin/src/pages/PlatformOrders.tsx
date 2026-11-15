import { AdminFilters } from "@/components/AdminFilters"
import { DataRegion } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"
import {
  ORDER_STATES,
  ORDER_SOURCES,
  ORDER_PAYMENTS,
  OrderJourney,
  OrderSyncBar,
  orderLifecycle,
  useResource,
  formatDateTime,
  randomUuid,
  formatMoney,
  type OrderFacts,
  type OrderEvent,
} from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Feedback, PageHeader, Status } from "@/components/business"
import { SaasField as Field } from "@/components/SaasField"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  platformRequest,
  platformSession,
  type PlatformProfile,
} from "@/lib/platform-session"
import {
  readSaasPending,
  rememberSaasPending,
  clearSaasPending,
  verifySaasReceipt,
  type SaasReceipt,
} from "@/lib/saas-intent"
import "./saas.css"

type Club = { slug: string; name: string }
type PlatformOrder = OrderFacts & {
  id: string
  store_slug: string
  store_name: string
  order_no: string
  service_name: string
  customer_name: string
  provider_names: string[]
  currency_name: string
  version: string
  allowed_actions: string[]
  events?: OrderEvent[]
}
type OrderPage = {
  readKey?: string
  results: PlatformOrder[]
  total: number
  complete: boolean
  generated_at: string
  missing_stores: string[]
}
const orderIdentity = (order: PlatformOrder) =>
  `${order.store_slug}/${order.id}`
function validOrder(order: PlatformOrder) {
  return (
    !!order &&
    !!order.id &&
    !!order.version &&
    /^[a-z][a-z0-9-]{0,47}$/.test(order.store_slug) &&
    Array.isArray(order.provider_names) &&
    Array.isArray(order.allowed_actions)
  )
}
export default function PlatformOrders({
  profile,
  stores,
}: {
  profile: PlatformProfile
  stores: Club[]
}) {
  const [params, setParams] = useSearchParams()
  const [draft, setDraft] = useState(() => ({
    store: params.get("store") || "",
    source: params.get("source") || "",
    status: params.get("status") || "",
    payment: params.get("payment") || "",
    keyword: params.get("keyword") || "",
  }))
  const page = Math.max(1, Number(params.get("page")) || 1)
  const key = params.toString()
  const [detail, setDetail] = useState<PlatformOrder | null>(null)
  const [creating, setCreating] = useState(false)
  const supported =
    profile.capabilities?.includes("PLATFORM_ORDERS_V1") === true
  const dispatchSupported =
    profile.capabilities?.includes("PLATFORM_DISPATCH_V1") === true
  const superAdmin = profile.grants.some(
    (grant) => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*"
  )
  const canReadStore = (slug: string) =>
    superAdmin ||
    profile.grants.some(
      (grant) =>
        ["ORDER_VIEWER", "ORDER_OPERATOR"].includes(grant.role) &&
        ["*", slug].includes(grant.scope_slug)
    )
  const readableStores = stores.filter((store) => canReadStore(store.slug))
  const canDispatchStore = (slug: string) =>
    superAdmin ||
    profile.grants.some(
      (grant) =>
        grant.role === "ORDER_OPERATOR" &&
        ["*", slug].includes(grant.scope_slug)
    )
  const result = useResource<OrderPage | null>(async () => {
    if (!supported) return null
    if (params.get("store") && !canReadStore(params.get("store")!))
      throw new Error("当前平台身份未获得该俱乐部订单权限")
    const query = new URLSearchParams({ page: String(page), page_size: "20" })
    for (const name of ["store", "source", "status", "payment", "keyword"]) {
      const value = params.get(name)
      if (value) query.set(name, value)
    }
    const data = await platformRequest<OrderPage>(`orders?${query}`)
    if (
      !Array.isArray(data.results) ||
      !data.results.every(validOrder) ||
      !data.results.every(
        (order) =>
          canReadStore(order.store_slug) &&
          (!params.get("store") || order.store_slug === params.get("store"))
      ) ||
      !Array.isArray(data.missing_stores) ||
      !Number.isInteger(data.total)
    )
      throw new Error("订单数据不完整，请重新读取")
    return { ...data, readKey: key }
  }, [key, supported])
  useEffect(() => {
    setDraft({
      store: params.get("store") || "",
      source: params.get("source") || "",
      status: params.get("status") || "",
      payment: params.get("payment") || "",
      keyword: params.get("keyword") || "",
    })
    setDetail(null)
  }, [key])
  const current = result.data?.readKey === key ? result.data : null
  const apply = () =>
    setParams(
      Object.fromEntries(Object.entries(draft).filter(([, value]) => value))
    )
  const tableFilters = (<form onSubmit={event => { event.preventDefault(); apply() }}><AdminFilters title="平台订单筛选" activeCount={draft.payment ? 1 : 0} primary={<><label>
            订单号或老板昵称
            <Input
              value={draft.keyword}
              maxLength={100}
              placeholder="输入订单号或老板昵称"
              onChange={(event) =>
                setDraft({ ...draft, keyword: event.target.value })
              }
            />
          </label><label>
            俱乐部
            <select
              className="saas-select"
              value={draft.store}
              onChange={(event) =>
                setDraft({ ...draft, store: event.target.value })
              }
            >
              <option value="">全部授权俱乐部</option>
              {readableStores.map((store) => (
                <option key={store.slug} value={store.slug}>
                  {store.name}
                </option>
              ))}
            </select>
          </label><label>
            下单来源
            <select
              className="saas-select"
              value={draft.source}
              onChange={(event) =>
                setDraft({ ...draft, source: event.target.value })
              }
            >
              <option value="">全部来源</option>
              {Object.entries(ORDER_SOURCES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label><label>
            订单状态
            <select
              className="saas-select"
              value={draft.status}
              onChange={(event) =>
                setDraft({ ...draft, status: event.target.value })
              }
            >
              <option value="">全部状态</option>
              {Object.entries(ORDER_STATES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label></>} actions={<><Button type="submit">查询</Button><Button
            variant="outline"
            type="button"
            onClick={() => {
              setParams({})
              setDraft({
                store: "",
                source: "",
                status: "",
                payment: "",
                keyword: "",
              })
            }}
          >
            清除筛选
          </Button></>}><label>
            支付状态
            <select
              className="saas-select"
              value={draft.payment}
              onChange={(event) =>
                setDraft({ ...draft, payment: event.target.value })
              }
            >
              <option value="">全部支付</option>
              {Object.entries(ORDER_PAYMENTS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label></AdminFilters></form>)
  return (
    <div className="space-y-5">
      <PageHeader title="全俱乐部订单与派单" description="统一追踪客服代单与老板自主下单，按俱乐部核对订单、支付及履约状态。">
        <Button disabled={!dispatchSupported} onClick={() => setCreating(true)}>
          新建客服代单
        </Button>
      </PageHeader>

      {!supported && (
        <section className="saas-section">
          <h2>统一订单服务暂未开放</h2>
          <fieldset disabled className="mt-4">{tableFilters}</fieldset>
          <p className="saas-note">
            平台已提供按俱乐部、来源、订单与支付状态的统一追踪入口。当前订单请由获授权的本店客服在门店后台处理。
          </p>
          <p className="saas-note">
            平台入口不切换店内身份；开通后只展示当前平台账号获授权的俱乐部。
          </p>
        </section>
      )}
      {supported && (
        <OrderSyncBar
          loading={result.loading}
          error={result.error}
          snapshot={current}
          reload={result.reload}
        />
      )}
      <Feedback message={result.error?.message} retry={result.reload} />
      {current && !current.complete && (
        <p role="alert" className="rounded-md border p-4 text-sm">
          以下仅为已读取俱乐部的数据。暂不可用：
          {current.missing_stores.join("、") || "请核对俱乐部连接"}。读取于{" "}
          {formatDateTime(current.generated_at)}。
        </p>
      )}
      {supported && (
        <section className="saas-section">
          <h2>订单追踪</h2>
          <div className="border-b pb-4">{tableFilters}</div>
          <div className="overflow-x-auto">
            <table className="saas-table">
              <thead>
                <tr>
                  {[
                    "俱乐部 / 订单",
                    "老板 / 服务",
                    "下单来源",
                    "订单 / 支付",
                    "派单进展",
                    "操作",
                  ].map((title) => (
                    <th key={title} scope="col" data-table-action={title === "操作" || undefined} className={title === "操作" ? "text-center" : undefined}>
                      {title}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(current?.results || []).map((order) => {
                  const view = orderLifecycle(order)
                  return (
                    <tr key={orderIdentity(order)}>
                      <td>
                        {order.store_name}
                        <p className="mt-1 text-xs text-muted-foreground">
                          {order.order_no}
                        </p>
                      </td>
                      <td>
                        {order.customer_name}
                        <p className="mt-1 text-xs text-muted-foreground">
                          {order.service_name}
                        </p>
                      </td>
                      <td>{view.source}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          <Status value={order.status} label={view.status} readState={order.status == null ? "unread" : undefined} />
                          <Status value={order.payment_status} label={view.payment} readState={order.payment_status == null && order.payment_display?.kind !== "DIRECT_TEST" ? "unread" : undefined} />
                        </div>
                      </td>
                      <td>
                        <Status value={order.status} label={view.dispatch} readState={order.status == null ? "unread" : undefined} />
                        <p className="mt-1 text-xs text-muted-foreground">
                          {Array.isArray(order.provider_names) ? order.provider_names.join("、") || "待安排" : "—"}
                        </p>
                      </td>
                      <td data-table-action>
                        <div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">
                          <Button
                            actionTone="read"
                            size="sm"
                            variant="outline"
                            disabled={result.loading || !!result.error}
                            onClick={() => setDetail(order)}
                          >
                            详情与处理
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
                {!current?.results.length && <tr><td colSpan={6}><DataRegion label="订单追踪" loading={result.loading} hasSnapshot={!!current} error={result.error?.message} retry={result.reload}><span>没有匹配的订单，请调整筛选条件。</span></DataRegion></td></tr>}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-between gap-4">
            <span className="text-xs text-muted-foreground">
              {current
                ? `${current.complete ? "共" : "已读取"} ${current.total} 笔 · 第 ${page} 页`
                : "总数待核对"}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={!current || result.loading || page <= 1}
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set("page", String(page - 1))
                  setParams(next)
                }}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                disabled={!current || result.loading || page * 20 >= current.total}
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set("page", String(page + 1))
                  setParams(next)
                }}
              >
                下一页
              </Button>
            </div>
          </div>
        </section>
      )}
      {detail && (
        <PlatformOrderDetail
          key={orderIdentity(detail)}
          target={detail}
          dispatchSupported={
            dispatchSupported && canDispatchStore(detail.store_slug)
          }
          onClose={() => setDetail(null)}
          onChanged={result.reload}
        />
      )}
      {creating && (
        <PlatformCreateOrder
          stores={stores.filter((store) => canDispatchStore(store.slug))}
          onClose={() => setCreating(false)}
          onChanged={result.reload}
        />
      )}
    </div>
  )
}

function PlatformOrderDetail({
  target,
  dispatchSupported,
  onClose,
  onChanged,
}: {
  target: PlatformOrder
  dispatchSupported: boolean
  onClose: () => void
  onChanged: () => unknown
}) {
  const path =
    `orders/${encodeURIComponent(target.store_slug)}/${encodeURIComponent(target.id)}` as const
  const pendingKey = `xa:saas:assign:${platformSession()?.sessionId}:${orderIdentity(target)}`
  const saved = useRef(readSaasPending(pendingKey))
  const detail = useResource(
    () =>
      platformRequest<PlatformOrder>(path).then((order) => {
        if (
          !validOrder(order) ||
          orderIdentity(order) !== orderIdentity(target)
        )
          throw new Error("订单详情不完整或所属俱乐部不一致，请重新打开")
        return order
      }),
    [path]
  )
  const [remark, setRemark] = useState("")
  const [providers, setProviders] = useState<string[]>([])
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(!!saved.current)
  const lock = useRef(false),
    intent = useRef(saved.current?.id || randomUuid())
  const order = detail.data
  const options = useResource<{
    results: { id: string; name: string }[]
  } | null>(async () => {
    if (!dispatchSupported || !order?.allowed_actions.includes("ASSIGN"))
      return null
    return platformRequest(`${path}/candidates`)
  }, [path, dispatchSupported, order?.version])
  async function assign() {
    if (
      lock.current ||
      uncertain ||
      !dispatchSupported ||
      !order?.allowed_actions.includes("ASSIGN") ||
      !providers.length ||
      !remark.trim() ||
      detail.loading ||
      detail.error
    )
      return
    lock.current = true
    setBusy(true)
    setError("")
    try {
      rememberSaasPending(pendingKey, {
        id: intent.current,
        operation: "ASSIGN",
      })
      const assigned = await platformRequest<PlatformOrder>(`${path}/assign`, {
        method: "POST",
        data: {
          expected_version: order.version,
          client_request_id: intent.current,
          provider_ids: providers,
          remark: remark.trim(),
        },
      })
      if (
        !validOrder(assigned) ||
        orderIdentity(assigned) !== orderIdentity(target)
      )
        throw new Error("派单结果需核对，请刷新状态")
      clearSaasPending(pendingKey)
      toast.success("指派已提交，请核对最新人员安排")
      intent.current = randomUuid()
      setProviders([])
      setRemark("")
      await detail.reload()
      await onChanged()
    } catch (cause) {
      setError((cause as Error).message)
      const status = (cause as { status?: number }).status
      const unknown = !status || status >= 500 || status === 409
      setUncertain(unknown)
      if (!unknown) clearSaasPending(pendingKey)
      await detail.reload()
    } finally {
      lock.current = false
      setBusy(false)
    }
  }
  async function verify() {
    await detail.reload()
    if (!uncertain) return
    try {
      const receipt = await platformRequest<SaasReceipt>(
        `orders/${target.store_slug}/receipts/${intent.current}`
      )
      if (!verifySaasReceipt(receipt, target.store_slug, intent.current)) {
        setError("派单结果仍待核对，请稍后再查")
        return
      }
      clearSaasPending(pendingKey)
      setUncertain(false)
      intent.current = randomUuid()
      setError(
        receipt.status === "REJECTED"
          ? receipt.message || "本次派单未执行，可调整后重新提交"
          : ""
      )
      await onChanged()
    } catch (cause) {
      setError((cause as Error).message)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {target.store_name} · {target.order_no}
          </DialogTitle>
          <DialogDescription>
            当前平台身份仅对本订单所属俱乐部执行已授权操作。
          </DialogDescription>
        </DialogHeader>
        <OrderSyncBar
          loading={detail.loading}
          error={detail.error}
          snapshot={order}
          reload={verify}
        />
        <Feedback message={detail.error?.message} retry={detail.reload} />
        {order && (
          <>
            <OrderJourney order={order} events={order.events} />
            <p className="text-sm">
              老板：{order.customer_name} · 服务：{order.service_name}
            </p>
            {dispatchSupported && order.allowed_actions.includes("ASSIGN") && (
              <section className="saas-section">
                <h2>指派本店陪玩</h2>
                <Feedback
                  message={options.error?.message}
                  retry={options.reload}
                />
                <fieldset
                  disabled={
                    busy ||
                    uncertain ||
                    detail.loading ||
                    !!detail.error ||
                    options.loading ||
                    !!options.error
                  }
                  className="mt-4 grid gap-4"
                >
                  <Field label="可指派陪玩（按服务要求选择）">
                    <div className="flex flex-wrap gap-3">
                      {options.data?.results.map((provider) => (
                        <label
                          key={provider.id}
                          className="flex min-h-10 items-center gap-2 rounded-md border px-3 text-sm"
                        >
                          <input
                            type="checkbox"
                            checked={providers.includes(provider.id)}
                            onChange={(event) =>
                              setProviders(
                                event.target.checked
                                  ? [...providers, provider.id]
                                  : providers.filter((id) => id !== provider.id)
                              )
                            }
                          />
                          {provider.name}
                        </label>
                      ))}
                    </div>
                  </Field>
                  <Field label="派单备注">
                    <Input
                      value={remark}
                      maxLength={500}
                      onChange={(event) => setRemark(event.target.value)}
                    />
                  </Field>
                </fieldset>
                <p className="saas-note">
                  确认向「{target.store_name}
                  」的本单指派以上陪玩。最终可指派人数与资格由系统核对。
                </p>
                <Button
                  className="mt-4"
                  disabled={
                    !providers.length ||
                    !remark.trim() ||
                    busy ||
                    uncertain ||
                    detail.loading ||
                    !!detail.error ||
                    !!options.error ||
                    options.loading
                  }
                  onClick={() => void assign()}
                >
                  {busy ? "指派中…" : "确认指派"}
                </Button>
              </section>
            )}
            {!order.allowed_actions.length && (
              <p className="saas-note">
                当前无可执行动作，请跟进状态或联系本店负责人。
              </p>
            )}
          </>
        )}
        <Feedback message={error} />
        {uncertain && (
          <p role="alert" className="text-sm">
            本次结果待核对，请查看最新人员与状态记录，暂不重复派单。
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}

type DispatchOptions = {
  customers: { id: string; name: string }[]
  services: { id: string; name: string }[]
}
type DispatchQuote = {
  token: string
  amount: number
  currency_name: string
  summary: string
  expires_at: string
}
function PlatformCreateOrder({
  stores,
  onClose,
  onChanged,
}: {
  stores: Club[]
  onClose: () => void
  onChanged: () => unknown
}) {
  const pendingKey = `xa:saas:create:${platformSession()?.sessionId}`
  const saved = useRef(readSaasPending(pendingKey))
  const [store, setStore] = useState(saved.current?.operation || "")
  const [customer, setCustomer] = useState("")
  const [service, setService] = useState("")
  const [quantity, setQuantity] = useState(1)
  const [confirmation, setConfirmation] = useState("")
  const [quote, setQuote] = useState<DispatchQuote | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false),
    [unknown, setUnknown] = useState(!!saved.current)
  const intent = useRef(saved.current?.id || randomUuid()),
    lock = useRef(false)
  const options = useResource<DispatchOptions | null>(
    () =>
      store
        ? platformRequest(`orders/${store}/dispatch-options`)
        : Promise.resolve(null),
    [store]
  )
  const requestKey = `${store}/${customer}/${service}/${quantity}`
  const current = useRef(requestKey)
  current.current = requestKey
  useEffect(() => {
    setQuote(null)
    setError("")
  }, [requestKey])
  async function submit(create: boolean) {
    if (
      lock.current ||
      unknown ||
      !store ||
      !customer ||
      !service ||
      !Number.isInteger(quantity) ||
      quantity < 1 ||
      quantity > 999
    )
      return
    const key = requestKey
    lock.current = true
    setBusy(true)
    setError("")
    try {
      if (!create) {
        const result = await platformRequest<DispatchQuote>(
          `orders/${store}/dispatch/quote`,
          {
            method: "POST",
            data: {
              customer_id: customer,
              service_id: service,
              quantity,
              dispatch_route: "STAFF",
            },
          }
        )
        if (
          !result.token ||
          !Number.isFinite(Date.parse(result.expires_at)) ||
          !Number.isFinite(result.amount) ||
          result.amount < 0 ||
          !result.currency_name
        )
          throw new Error("报价信息不完整，请重新核价")
        if (current.current === key) setQuote(result)
      } else {
        if (
          !quote?.token ||
          !Number.isFinite(Date.parse(quote.expires_at)) ||
          Date.parse(quote.expires_at) <= Date.now() ||
          !confirmation.trim()
        )
          throw Object.assign(
            new Error("报价已失效或尚未记录老板确认，请重新核价"),
            { status: 400 }
          )
        rememberSaasPending(pendingKey, {
          id: intent.current,
          operation: store,
        })
        const created = await platformRequest<PlatformOrder>(
          `orders/${store}/dispatch`,
          {
            method: "POST",
            data: {
              quote_token: quote.token,
              client_request_id: intent.current,
              boss_confirmation: confirmation.trim(),
              order_source: "STAFF_MANUAL",
            },
          }
        )
        if (!validOrder(created) || created.store_slug !== store)
          throw new Error("创建结果需核对，请查询本次请求")
        clearSaasPending(pendingKey)
        toast.success(`客服代单已创建：${created.order_no}`)
        await onChanged()
        onClose()
      }
    } catch (cause) {
      setError((cause as Error).message)
      if (create) {
        const status = (cause as { status?: number }).status
        const uncertain = !status || status >= 500 || status === 409
        setUnknown(uncertain)
        if (!uncertain) clearSaasPending(pendingKey)
      }
    } finally {
      lock.current = false
      setBusy(false)
    }
  }
  async function recover() {
    try {
      const receipt = await platformRequest<SaasReceipt>(
        `orders/${store}/receipts/${intent.current}`
      )
      if (!verifySaasReceipt(receipt, store, intent.current)) {
        setError("创建结果仍待核对，请稍后重查")
        return
      }
      clearSaasPending(pendingKey)
      setUnknown(false)
      intent.current = randomUuid()
      if (receipt.status === "APPLIED") {
        await onChanged()
        onClose()
      } else setError(receipt.message || "本次创建未执行，可调整后重试")
    } catch (cause) {
      setError((cause as Error).message)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建客服代单</DialogTitle>
          <DialogDescription>
            明确所属俱乐部与老板，核对服务报价后创建；付款结果以真实记账为准。
          </DialogDescription>
        </DialogHeader>
        <fieldset className="grid gap-4" disabled={busy || unknown}>
          <Field label="所属俱乐部">
            <select
              className="saas-select"
              value={store}
              onChange={(event) => {
                setStore(event.target.value)
                setCustomer("")
                setService("")
                setConfirmation("")
              }}
            >
              <option value="">请选择俱乐部</option>
              {stores.map((club) => (
                <option key={club.slug} value={club.slug}>
                  {club.name}
                </option>
              ))}
            </select>
          </Field>
          <Feedback message={options.error?.message} retry={options.reload} />
          <Field label="本店老板">
            <select
              className="saas-select"
              value={customer}
              disabled={!options.data || options.loading || !!options.error}
              onChange={(event) => setCustomer(event.target.value)}
            >
              <option value="">请选择老板</option>
              {options.data?.customers.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="本店服务">
            <select
              className="saas-select"
              value={service}
              disabled={!options.data || options.loading || !!options.error}
              onChange={(event) => setService(event.target.value)}
            >
              <option value="">请选择服务</option>
              {options.data?.services.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="数量">
            <Input
              type="number"
              min={1}
              max={999}
              step={1}
              value={quantity}
              onChange={(event) => setQuantity(Number(event.target.value))}
            />
          </Field>
          <Button
            variant="outline"
            disabled={
              !store ||
              !service ||
              !customer ||
              options.loading ||
              !!options.error ||
              !Number.isInteger(quantity) ||
              quantity < 1 ||
              quantity > 999
            }
            onClick={() => void submit(false)}
          >
            核对报价
          </Button>
          {quote && (
            <>
              <p className="text-sm">
                {quote.summary} · 应付 {formatMoney(quote.amount)}{" "}
                {quote.currency_name} · 报价有效至{" "}
                {formatDateTime(quote.expires_at)}
              </p>
              <Field label="老板确认记录">
                <Input
                  value={confirmation}
                  maxLength={500}
                  onChange={(event) => setConfirmation(event.target.value)}
                  placeholder="记录老板确认的服务、金额和付款安排"
                />
              </Field>
              <Button
                disabled={!confirmation.trim()}
                onClick={() => void submit(true)}
              >
                确认创建客服代单
              </Button>
            </>
          )}
        </fieldset>
        <Feedback message={error} />
        {unknown && (
          <div role="alert" className="space-y-3">
            <p className="text-sm">
              创建结果待核对，暂不再次提交。关闭后仍会保留请求核对标记。
            </p>
            <Button variant="outline" onClick={() => void recover()}>
              核对本次创建结果
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
