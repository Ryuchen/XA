import { DataRegion, DataValue } from "@/components/page-data-state"
import { RecordHeading as PageHeader, RecordNotice } from "./OrderRecordVisual"
import { coinText, coinUnit } from "@xa/shared"
import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"
import {
  CalendarClock,
  Clock3,
  CalendarCheck2,
  ClipboardCheck,
  UserRound,
  Headphones,
  Plus,
  ChevronRight,
} from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import {
  DataPageState,
  Details,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  Pagination,
  RefreshButton,
  Status,
  useLiveReload,
  useLoad,
  type Row,
} from "@/components/business"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { request, reviewedFundRequest } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin } from "@/lib/format"

const reservationLabel = (status: string) => ({ PENDING: "待确认", CONFIRMED: "已确认", REJECTED: "已拒绝", CANCELLED: "已取消", CONVERTED: "已转单", EXPIRED: "已过期" }[String(status).toUpperCase()] || status)
const api = (url = "", method = "GET", data?: unknown, params?: Row) =>
  request({
    baseURL: "/api",
    url: `/orders/reservations/${url}`,
    method,
    data,
    params,
  })
const beijing = (value?: string) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "short",
        timeStyle: "short",
        hour12: false,
      }).format(new Date(value))
    : "—"
export default function ReservationsPage() {
  const auth = useAdminAuth()
  const [params] = useSearchParams()
  const [status, setStatus] = useState(params.get("status") || "")
  const [page, setPage] = useState(1)
  const list = useLoad(
    async () =>
      (
        await api("", "GET", undefined, {
          page,
          page_size: 10,
          status,
          ...(["pending", "confirmed"].includes(status)
            ? { sort_start: "asc" }
            : {}),
        })
      ).data,
    [status, page]
  )
  const [id, setId] = useState<number | null>(null)
  const current = useLoad(
    async () => (id ? (await api(`${id}/`)).data : null),
    [id]
  )
  const [action, setAction] = useState<string | null>(null)
  const [reason, setReason] = useState("")
  const [creating, setCreating] = useState(false)
  const [quote, setQuote] = useState<Row | null>(null)
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [giftBalance, setGiftBalance] = useState<any>(0)
  const [compensationBalance, setCompensationBalance] = useState<any>(0)
  const [bossConfirmation, setBossConfirmation] = useState("")
  const [discountToken, setDiscountToken] = useState("")
  const [fundingDirty, setFundingDirty] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [error, setError] = useState("")
  useLiveReload(() => Promise.all([list.reload(), ...(id ? [current.reload()] : [])]))
  const row = current.data
  async function conversion() {
    if (quoteLoading) return
    setQuoteLoading(true)
    setError("")
    try {
      const value = (await api(`${id}/quote/`)).data
      setGiftBalance(0); setCompensationBalance(0); setBossConfirmation(""); setFundingDirty(false)
      setQuote(value)
      setDiscountToken(value.discount_selection_token || "")
      setReviewed(false)
      setUnknown(false)
      setAction("convert")
    } catch (cause: any) {
      setError(cause.message)
    } finally {
      setQuoteLoading(false)
    }
  }
  async function refreshFundingQuote() {
    if ([giftBalance, compensationBalance].some(value => !Number.isSafeInteger(value) || value < 0 || value * 10 > 2147483647))
      throw new Error("赠送与赔付须填写非负整数店币")
    setQuoteLoading(true)
    setError("")
    try {
      const value = (await api(`${id}/quote/`, "GET", undefined, {
        gift_balance_amount: giftBalance * 10, compensation_balance_amount: compensationBalance * 10,
        ...(discountToken ? {discount_selection_token: discountToken} : {}),
      })).data
      setQuote(value); setFundingDirty(false); setReviewed(false)
    } finally { setQuoteLoading(false) }
  }
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="reservations"
    >
      {
        <div className="admin-reservations-page space-y-4">
          <PageHeader
            tone="feature"
            tag="档期与履约"
            title="预约与转单"
            description="先锁定陪玩档期，再核对双方安排与实时报价。成功转单后才扣款并进入正式订单。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("order:dispatch") && (
              <Button size="sm" onClick={() => setCreating(true)}>
                <Plus className="size-4" />
                代老板预约
              </Button>
            )}
          </PageHeader>
          <dl className="admin-reservation-metrics">
            {[
              {
                label: "符合筛选",
                value: list.data?.total ?? "—",
                note: "预约与订单独立编号",
                icon: CalendarClock,
              },
              {
                label: "本页等待确认",
                value: (list.data?.items || []).filter(
                  (item: Row) => item.status === "PENDING"
                ).length,
                note: "占用档期，不占用资金",
                icon: Clock3,
              },
              {
                label: "本页已确认待转单",
                value: (list.data?.items || []).filter(
                  (item: Row) => item.status === "CONFIRMED"
                ).length,
                note: "转单前重新报价",
                icon: CalendarCheck2,
              },
              {
                label: "本页已转正式订单",
                value: (list.data?.items || []).filter(
                  (item: Row) => item.status === "CONVERTED"
                ).length,
                note: "一笔预约最多转一笔订单",
                icon: ClipboardCheck,
              },
            ].map((item) => (
              <div key={item.label}>
                <div>
                  <dt>{item.label}</dt>
                  <item.icon size={16} />
                </div>
                <dd>
                  {item.value}
                  <small>笔</small>
                </dd>
                <p>{item.note}</p>
              </div>
            ))}
          </dl>
          <RecordNotice title="创建时估价不等于最终扣款">
            转单报价会核对当前服务、优惠与钱包余额。过期、拒绝、取消和已转单均为终态。
          </RecordNotice>
          <Feedback message={list.error || error} retry={list.reload} />
          <section className="admin-reservation-records">
            <div className="admin-reservation-list-heading">
              <div>
                <h2>
                  <CalendarClock size={16} />
                  预约列表
                </h2>
                <p>
                  本页 <DataValue>{list.data?.items?.length || 0}</DataValue> 笔 · 全部时间为北京时间
                </p>
              </div>
              <div className="w-44">
                <Field
                  field={{
                    key: "reservation-status",
                    label: "预约状态",
                    type: "select",
                    options: [
                      ["pending", "待确认"],
                      ["confirmed", "已确认"],
                      ["rejected", "已拒绝"],
                      ["cancelled", "已取消"],
                      ["converted", "已转单"],
                      ["expired", "已过期"],
                    ],
                  }}
                  value={status}
                  onChange={(value) => {
                    setStatus(value)
                    setPage(1)
                  }}
                />
              </div>
            </div>
            <div className="admin-reservation-card-grid">
              {!list.data && <div className="col-span-full"><DataRegion label="预约列表" loading={list.loading} hasSnapshot={!!list.data} error={list.error} retry={list.reload}>{null}</DataRegion></div>}
              {(list.data?.items || []).map((item: Row) => (
                <article key={item.id} className="admin-reservation-card">
                  <div className="admin-reservation-card-top">
                    <div>
                      <Status value={item.status} label={item.status_display || reservationLabel(item.status)} />
                      <h3>
                        {item.service?.name ||
                          item.service_name ||
                          "未记录服务"}
                      </h3>
                      <p>{item.reservation_no}</p>
                    </div>
                    <span>
                      <CalendarClock size={16} />
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>开始</dt>
                      <dd>{beijing(item.start_time)}</dd>
                    </div>
                    <div>
                      <dt>结束</dt>
                      <dd>{beijing(item.end_time)}</dd>
                    </div>
                  </dl>
                  <div className="admin-reservation-parties">
                    <p>
                      <UserRound size={14} />
                      {item.customer?.name || item.customer?.nickname || "—"}
                    </p>
                    <p>
                      <Headphones size={14} />
                      {item.status === "CONVERTED" ? "原预约：" : ""}{item.provider?.name || item.provider?.nickname || "—"}
                    </p>
                    {item.status === "CONVERTED" && item.current_providers?.length > 0 && <p>当前履约：{item.current_providers.map((person: Row) => person.name).join("、")}</p>}
                  </div>
                  <div className="admin-reservation-card-bottom">
                    <div>
                      <p>创建时估价 · {item.game_rounds ?? "—"} 份</p>
                      <strong>
                        {amountToXaCoin(item.estimated_amount)} {coinUnit()}
                      </strong>
                    </div>
                    <div>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={list.loading || !!list.error}
                        onClick={() => setId(item.id)}
                      >
                        查看详情
                        <ChevronRight size={14} />
                      </Button>
                      {item.order && (
                        <Button asChild variant="ghost" size="sm">
                          <Link to={`/orders?detail=${item.order}`}>
                            查看订单
                          </Link>
                        </Button>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
            {!!list.data && !list.loading &&
              !list.error &&
              !(list.data?.items || []).length && (
                <div className="admin-reservation-empty">
                  <CalendarClock size={28} />
                  <h3>当前状态暂无预约</h3>
                  <p>切换到全部状态查看其他预约记录。</p>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setStatus("")
                      setPage(1)
                    }}
                  >
                    查看全部预约
                  </Button>
                </div>
              )}
            <Pagination snapshotAvailable={!!list.data}
              page={page}
              total={list.data?.total || 0}
              onChange={setPage}
              loading={list.loading}
            />
          </section>
          <Dialog
            open={!!id}
            onOpenChange={(value) => {
              if (!value) setId(null)
            }}
          >
            <DialogContent className="admin-reservation-detail-dialog max-h-[92dvh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>预约详情 {row?.reservation_no}</DialogTitle>
                <DialogDescription>
                  请核对预约状态后选择可用操作。
                </DialogDescription>
              </DialogHeader>
              <Feedback message={current.error} retry={current.reload} />
              <Feedback message={error} />
              {quoteLoading && <AdminPageSkeleton page="reservation-quote" />}
              {current.loading ? (
                <AdminPageSkeleton page="reservation-detail" />
              ) : (
                row && (
                  <div className="admin-reservation-detail-body space-y-4">
                    <section className="admin-reservation-detail-summary">
                      <div>
                        <div>
                          <h3>
                            {row.service?.name ||
                              row.service_name ||
                              "预约服务"}
                          </h3>
                          <p>{row.game_rounds} 份 · 北京时间</p>
                        </div>
                        <Status value={row.status} label={row.status_display || reservationLabel(row.status)} />
                      </div>
                      <div>
                        <p>
                          <Clock3 size={14} />
                          {beijing(row.start_time)} 开始
                        </p>
                        <p>
                          <Clock3 size={14} />
                          {beijing(row.end_time)} 结束
                        </p>
                      </div>
                    </section>
                    {row.status === "CONFIRMED" && (
                      <RecordNotice title="转单前重新计价">
                        创建时估价 {amountToXaCoin(row.estimated_amount)}{" "}
                        {coinUnit()}仅供对比，实际扣款以本次实时报价为准。
                      </RecordNotice>
                    )}
                    <Details
                      data={{
                        ...row,
                        customer_name: row.customer?.name,
                        provider_name: row.provider?.name,
                        current_provider_names: (row.current_providers || []).map((person: Row) => person.name).join("、") || "—",
                        service_name: row.service?.name,
                        start_label: beijing(row.start_time),
                        end_label: beijing(row.end_time),
                      }}
                      fields={[
                        ["customer_name", "老板"],
                        ["provider_name", row.status === "CONVERTED" ? "原预约陪玩" : "陪玩"],
                        ...(row.status === "CONVERTED" ? [["current_provider_names", "当前履约陪玩"] as [string, string]] : []),
                        ["service_name", "服务"],
                        ["game_rounds", "购买数量"],
                        ["start_label", "开始时间（北京时间）"],
                        ["end_label", "结束时间（北京时间）"],
                        ["game_region", "游戏区服"],
                        ["game_nickname", "游戏昵称"],
                        ["game_uid", "游戏 UID"],
                        ["remark", "备注"],
                        ["reason", "处理原因"],
                        ["order_no", "转单订单"],
                      ]}
                    />
                    <div className="flex gap-2">
                      {(row.allowed_actions || []).map((kind: string) => {
                        const permitted =
                          kind === "cancel"
                            ? auth.hasPerm("order:cancel")
                            : auth.hasPerm("order:dispatch")
                        if (
                          !permitted ||
                          !["confirm", "reject", "cancel", "convert"].includes(
                            kind
                          )
                        )
                          return null
                        return (
                          <Button
                            key={kind}
                            size="sm"
                            disabled={quoteLoading}
                            variant={kind === "convert" ? "default" : "outline"}
                            onClick={() => {
                              if (kind === "convert") void conversion()
                              else {
                                setAction(kind)
                                setReason("")
                                setUnknown(false)
                              }
                            }}
                          >
                            {quoteLoading && kind === "convert"
                              ? "正在读取报价…"
                              : (
                                  {
                                    confirm: "确认预约",
                                    reject: "拒绝预约",
                                    cancel: "取消预约",
                                    convert: "报价并转单",
                                  } as Row
                                )[kind]}
                          </Button>
                        )
                      })}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={current.reload}
                      >
                        重新读取
                      </Button>
                    </div>
                  </div>
                )
              )}
            </DialogContent>
          </Dialog>
          {action && row && (
            <MutationDialog
              title={
                action === "convert"
                  ? "确认预约转单"
                  : (
                      {
                        confirm: "确认预约",
                        reject: "拒绝预约",
                        cancel: "取消预约",
                      } as Row
                    )[action]
              }
              description={`${row.reservation_no} · ${row.customer?.name} · ${row.provider?.name} · ${beijing(row.start_time)}（北京时间）`}
              onClose={() => setAction(null)}
              disabled={
                unknown ||
                (action === "convert" &&
                  (!quote?.balance_sufficient || !reviewed || quoteLoading || fundingDirty ||
                    ((quote?.funding_selection_enabled || quote?.discount_selection_enabled) && bossConfirmation.trim().length < 2)))
              }
              submitText={action === "convert" ? "确认扣款并转单" : "确认处理"}
              submitVariant={
                action === "reject" || action === "cancel"
                  ? "destructive"
                  : "default"
              }
              onSubmit={async () => {
                const latest = (await api(`${id}/`)).data
                if (!latest.allowed_actions.includes(action))
                  throw new Error("预约状态已变化，请重新读取。")
                try {
                  if (action === "convert")
                    await reviewedFundRequest(
                      `/orders/reservations/${id}/convert/`,
                      {
                        expected_amount: quote!.amount,
                        expected_service: quote!.service,
                        ...(quote!.discount_selection_enabled ? {discount_selection_token: quote!.discount_selection_token} : {}),
                        ...(quote!.funding_selection_enabled ? {gift_balance_amount: giftBalance * 10,
                          compensation_balance_amount: compensationBalance * 10} : {}),
                        ...((quote!.funding_selection_enabled || quote!.discount_selection_enabled)
                          ? {boss_confirmation: bossConfirmation.trim()} : {}),
                      }
                    )
                  else
                    await api(`${id}/${action}/`, "POST", {
                      reason: reason.trim(),
                    })
                  toast.success(
                    action === "convert" ? "预约已转为订单" : "预约已更新"
                  )
                  void list.reload()
                  void current.reload()
                } catch (cause: any) {
                  if (!cause.status || cause.status >= 500) setUnknown(true)
                  throw cause
                }
              }}
            >
              {action === "convert" && quote ? (
                <div className="space-y-3 rounded-lg border bg-muted/40 p-4">
                  <p>
                    服务：{quote.service?.name} · 数量 {row.game_rounds}
                  </p>
                  <p className="font-semibold">
                    {quote.amount === 0
                      ? coinText("本单应付 0 浪花，无需扣款")
                      : `将扣老板余额 ${amountToXaCoin(quote.amount)} ${coinUnit()}`}
                  </p>
                  {quote.discount_selection_enabled && <label className="grid gap-2 text-sm">优惠组合
                    <select autoComplete="off" name="优惠组合" aria-label="优惠组合" className="min-h-10 rounded-md border bg-background px-3"
                      value={discountToken} disabled={quoteLoading} onChange={event => {setDiscountToken(event.target.value); setFundingDirty(true); setReviewed(false)}}>
                      {(quote.discount_options || []).map((item: Row, index: number) => <option key={item.key} value={item.selection_token}>
                        {item.label} · 实付 {amountToXaCoin(item.amount)} {coinUnit()}{index === 0 ? "（推荐）" : ""}
                      </option>)}
                    </select>
                  </label>}
                  {quote.funding_selection_enabled && <div className="grid grid-cols-2 gap-3">
                    <Field field={{key:"reservation-gift-balance", label:`使用赠送（${coinUnit()}）`, type:"number", min:0, step:1}}
                      value={giftBalance} onChange={value => {setGiftBalance(value); setFundingDirty(true); setReviewed(false)}} />
                    <Field field={{key:"reservation-compensation-balance", label:`使用赔付（${coinUnit()}）`, type:"number", min:0, step:1}}
                      value={compensationBalance} onChange={value => {setCompensationBalance(value); setFundingDirty(true); setReviewed(false)}} />
                    <p className="col-span-2 text-xs text-muted-foreground">本金余额 {amountToXaCoin(quote.prepaid_balance)} · 赠送余额 {amountToXaCoin(quote.gift_balance)} · 赔付余额 {amountToXaCoin(quote.compensation_balance)} {coinUnit()}</p>
                  </div>}
                  {(quote.funding_selection_enabled || quote.discount_selection_enabled) && <>
                    <Button type="button" variant="outline" disabled={quoteLoading || !fundingDirty} onClick={() => void refreshFundingQuote().catch(cause => setError(cause.message))}>重新核对报价</Button>
                    <Field field={{key:"reservation-boss-confirmation", label:"老板确认记录", type:"textarea", full:true, required:true, maxLength:500}}
                      value={bossConfirmation} onChange={value => {setBossConfirmation(value); setReviewed(false)}} />
                  </>}
                  {!quote.balance_sufficient && (
                    <Feedback message="老板余额不足或钱包不可用，请先核对。" />
                  )}
                  <label className="flex gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-blue-600"
                      checked={reviewed}
                      onChange={(event) => setReviewed(event.target.checked)}
                    />
                    已核对服务、数量、时间、人员与扣款金额
                  </label>
                </div>
              ) : (
                <Field
                  field={{
                    key: "reservation-reason",
                    label: "原因说明（选填）",
                    type: "textarea",
                  }}
                  value={reason}
                  onChange={setReason}
                />
              )}
              {unknown && (
                <Feedback message="处理结果未确认。请关闭弹窗并重新读取预约，核对状态、关联订单与钱包。" />
              )}
              {action === "convert" && <Feedback message={error} />}
            </MutationDialog>
          )}
          {creating && (
            <CreateReservation
              onClose={() => setCreating(false)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function CreateReservation({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    customer_account_id: null,
    provider_account_id: null,
    service_id: null,
    start_time: "",
    end_time: "",
    game_rounds: 1,
    game_region: "",
    game_nickname: "",
    game_uid: "",
    remark: "",
  })
  const [unknown, setUnknown] = useState(false)
  const accountFields = useLoad<Row | null>(async () => form.service_id
    ? (await api(`account-fields/${form.service_id}/`)).data : null, [form.service_id])
  const labels: Record<string, string> = {game_region: "游戏区服", game_nickname: "游戏昵称", game_uid: "游戏 UID"}
  const currentRule = Number(accountFields.data?.service_id) === Number(form.service_id)
    ? accountFields.data?.account_fields : null
  const requiredFields: string[] | null = currentRule && Array.isArray(currentRule.fields) &&
    currentRule.fields.every((field: unknown) => typeof field === "string" && Object.prototype.hasOwnProperty.call(labels, field)) &&
    new Set(currentRule.fields).size === currentRule.fields.length ? currentRule.fields : null
  return (
    <MutationDialog
      title="代老板预约"
      description="时间使用北京时间，须按 15 分钟间隔选择。创建后等待确认，尚不扣除老板余额。"
      onClose={onClose}
      disabled={unknown || (!!form.service_id && (accountFields.loading || !!accountFields.error || !requiredFields))}
      onSubmit={async () => {
        if (
          !form.customer_account_id ||
          !form.provider_account_id ||
          !form.service_id ||
          !form.start_time ||
          !form.end_time
        )
          throw new Error("请选择老板、陪玩、服务与起止时间。")
        if (
          ![form.start_time, form.end_time].every((value) =>
            /^\d{4}-\d{2}-\d{2}T\d{2}:(00|15|30|45)$/.test(value)
          )
        )
          throw new Error("预约时间须按 15 分钟选择。")
        if (form.end_time <= form.start_time)
          throw new Error("结束时间须晚于开始时间。")
        if (!requiredFields) throw new Error("未能读取游戏资料规则，请刷新后重试。")
        const missing = requiredFields.filter(field => !String(form[field] || "").trim())
        if (missing.length) throw new Error(`请填写${missing.map(field => labels[field]).join("、")}`)
        try {
          await api("", "POST", {
            ...form,
            start_time: new Date(`${form.start_time}:00+08:00`).toISOString(),
            end_time: new Date(`${form.end_time}:00+08:00`).toISOString(),
          })
          toast.success("预约已创建，等待确认")
          onSaved()
        } catch (cause: any) {
          if (!cause.status || cause.status >= 500) setUnknown(true)
          else void accountFields.reload()
          throw cause
        }
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        <OptionPicker
          resource="users"
          label="老板"
          value={form.customer_account_id}
          onChange={(value) =>
            setForm((old) => ({ ...old, customer_account_id: value }))
          }
        />
        <OptionPicker
          resource="escorts"
          keyField="user"
          label="陪玩"
          value={form.provider_account_id}
          onChange={(value) =>
            setForm((old) => ({ ...old, provider_account_id: value }))
          }
        />
        <div className="col-span-2">
          <OptionPicker
            resource="service-items"
            label="服务套餐"
            query={{ is_active: true }}
            value={form.service_id}
            onChange={(value) =>
              setForm((old) => ({ ...old, service_id: value }))
            }
          />
        </div>
        {[
          {
            key: "start_time",
            label: "开始时间（北京时间）",
            type: "datetime-local",
          },
          {
            key: "end_time",
            label: "结束时间（北京时间）",
            type: "datetime-local",
          },
          {
            key: "game_rounds",
            label: "购买数量",
            type: "number",
            min: 1,
            max: 999,
            step: 1,
          },
          { key: "game_region", label: "游戏区服" },
          { key: "game_nickname", label: "游戏昵称" },
          { key: "game_uid", label: "游戏 UID" },
          { key: "remark", label: "备注", type: "textarea" },
        ].map((field) => (
          <Field
            key={field.key}
            field={{...field, required: requiredFields?.includes(field.key) || false} as any}
            value={form[field.key]}
            onChange={(value) =>
              setForm((old) => ({ ...old, [field.key]: value }))
            }
          />
        ))}
      </div>
      {form.service_id && accountFields.loading && <p className="text-sm text-muted-foreground">正在读取游戏资料要求…</p>}
      {form.service_id && !accountFields.loading && (accountFields.error || !requiredFields) &&
        <Feedback message={accountFields.error || "游戏资料规则无效，请联系管理员核对。"} retry={accountFields.reload} />}
      {unknown && (
        <Feedback message="预约创建结果未确认。请关闭并核对预约列表，避免重复创建。" />
      )}
    </MutationDialog>
  )
}
