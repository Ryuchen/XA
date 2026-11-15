import { coinText, coinUnit, useCurrentStore } from "@xa/shared"
import { formatProviderName, ProviderName } from "./provider-name"
import { reservationRebookTarget } from "@xa/shared"
import { requiredAccountFields, validateAccountFields } from "./account-fields"
import { getStoreSlug } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"
import { CalendarClock, ChevronRight, Gamepad2, WalletCards, ShieldCheck, Check, Copy, CalendarCheck2 } from "lucide-react"
import { getSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ArrowRight, Clock3, CheckCircle2, XCircle } from "lucide-react"
import {
  api,
  Choice,
  date,
  Field,
  Info,
  Image,
  LinkButton,
  label,
  money,
  Panel,
  Pending,
  queryUrl,
  readIntent,
  requestId,
  rows,
  State,
  StatusBadge,
  BackLink,
  storeIntent,
  submitIntent,
  Title,
  uncertain,
  useAction,
  useConfirm,
  useLoad,
  useQuery,
  type Intent,
  type Row,
} from "./ui"

const reservationStatus = (value: string) =>
  value === "PENDING" ? "待陪玩确认" : label(value)

export function ReservationsPage() {
  const store = useCurrentStore()
  const [status, setStatus] = useState(""),
    [before, setBefore] = useState<Row | null>(null),
    load = useLoad(
      async () => {
        const [list, pending, confirmed] = await Promise.all([
          api.get<any>(
          queryUrl("/orders/reservations/", {
            cursor: 1,
            page_size: 20,
            status,
            before_time: before?.start_time,
            before_id: before?.id,
          })),
          api.get<any>(queryUrl("/orders/reservations/", { status: "PENDING", sort_start: "asc", page_size: 1 })),
          api.get<any>(queryUrl("/orders/reservations/", { status: "CONFIRMED", sort_start: "asc", page_size: 1 })),
        ])
        const upcoming = [...rows(pending), ...rows(confirmed)].sort((a, b) => Date.parse(a.start_time) - Date.parse(b.start_time))
        return { ...list, nextReservation: upcoming[0] || null, activeCount: Number(pending.total) + Number(confirmed.total) }
      },
      [status, JSON.stringify(before)],
    )
  const data = load.data,
    items = rows(data)
  const upcoming = data?.nextReservation
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title title="我的预约" description="查看服务时段与确认进度，核对报价后再决定是否成单。" />
      <State {...load} errorAction={before ? <Button variant="outline" onClick={() => setBefore(null)}>返回最新预约</Button> : undefined}>
        {data && <div className="boss-reservations-content">
        {upcoming && <section className="boss-next-reservation" data-status={upcoming.status} aria-labelledby="next-reservation-title"><div className="boss-next-reservation-main"><span><CalendarClock size={20} /></span><div><div className="boss-actions"><h2 id="next-reservation-title">下一次预约</h2><StatusBadge status={upcoming.status}>{reservationStatus(upcoming.status)}</StatusBadge></div><strong>{date(upcoming.start_time)}</strong><p title={[upcoming.service?.name || upcoming.service_name, formatProviderName(upcoming.provider?.nickname || upcoming.provider?.name || upcoming.provider?.display_name, store.provider_name_prefix)].filter(Boolean).join(" · ")}>{upcoming.service?.name || upcoming.service_name} · {formatProviderName(upcoming.provider?.nickname || upcoming.provider?.name || upcoming.provider?.display_name, store.provider_name_prefix)}</p></div></div><div className="boss-next-reservation-side"><div><strong>{data.activeCount}</strong><p>进行中预约</p></div><Button asChild variant="outline"><Link to={"/reservation-detail?id=" + upcoming.id}>查看安排<ArrowRight size={16} /></Link></Button></div></section>}
        <div className="boss-reservations-records-heading"><div><h2>预约记录</h2><p>共 {data.total} 条{status ? "当前状态" : ""}记录</p></div><span>预约不扣款</span></div>
        <div className="boss-tabs boss-reservations-status-tabs" role="group" aria-label="预约状态筛选">{["", "PENDING", "CONFIRMED", "CONVERTED", "REJECTED", "CANCELLED", "EXPIRED"].map(value => <Button key={value} aria-label={value ? reservationStatus(value) : "全部预约"} aria-pressed={status === value} variant={status === value ? "secondary" : "ghost"} onClick={() => { setStatus(value); setBefore(null) }}>{value === "PENDING" ? "待确认" : value ? reservationStatus(value) : "全部"}</Button>)}</div>
        <State compact empty={!items.length} emptyAction={before ? <Button variant="outline" onClick={() => setBefore(null)}>返回最新预约</Button> : undefined}>
        <div className="boss-reservations-list">{items.map(item => <Link to={"/reservation-detail?id=" + item.id} className="boss-reservation-card" data-status={item.status} key={item.id}>
          <div className="boss-reservation-service"><div className="boss-reservation-number-row"><span className="boss-reservation-number" title={item.reservation_no}>{item.reservation_no}</span><span className="boss-reservation-mobile-status"><StatusBadge status={item.status}>{reservationStatus(item.status)}</StatusBadge></span></div><div className="boss-actions"><h2>{item.service?.name || item.service_name}</h2><span className="boss-reservation-desktop-status"><StatusBadge status={item.status}>{item.status === "CONVERTED" ? <CheckCircle2 size={12} /> : ["REJECTED", "CANCELLED", "EXPIRED"].includes(item.status) ? <XCircle size={12} /> : <Clock3 size={12} />}{reservationStatus(item.status)}</StatusBadge></span></div><p className="boss-reservation-provider"><Avatar><AvatarFallback>{(item.provider?.name || item.provider?.nickname || item.provider?.display_name || "陪玩").slice(0, 1)}</AvatarFallback></Avatar><span>{item.status === "CONVERTED" ? "原预约：" : ""}<ProviderName value={item.provider?.name || item.provider?.nickname || item.provider?.display_name} /></span>{item.game_rounds != null && <span>· {item.game_rounds} 份服务</span>}</p>{item.status === "CONVERTED" && item.current_providers?.length > 0 && <p>当前履约：{item.current_providers.map((person: Row) => formatProviderName(person.name, store.provider_name_prefix)).join("、")}</p>}</div>
          <div className="boss-reservation-time"><span>预约时间 · 北京时间</span><strong>{date(item.start_time)}</strong><small>至 {date(item.end_time)}</small><small className="boss-reservation-mobile-hint">{item.status === "CONFIRMED" ? "核对实时报价后可成单" : item.status === "PENDING" ? "等待陪玩确认，暂不会扣款" : item.status === "CONVERTED" ? "已生成订单，可查看服务进度" : item.status === "EXPIRED" ? "可沿用原服务重新预约" : "查看处理说明"}</small></div>
          <div className="boss-reservation-amount"><div><span>预约时预估</span><strong>{money(item.estimated_amount)} <small>{coinUnit()}</small></strong></div><span className="boss-reservation-mobile-more">查看详情<ChevronRight size={16} /></span></div><ChevronRight className="boss-reservation-arrow" size={20} />
        </Link>)}</div>
        <div className="boss-reservations-pagination">{before && <Button variant="outline" onClick={() => setBefore(null)} disabled={load.loading}>返回最新</Button>}{data.next_cursor ? <Button variant="outline" onClick={() => setBefore(data.next_cursor)} disabled={load.loading}>查看更多预约</Button> : <p>{before ? "已显示至最早一页" : "已显示当前筛选下的全部预约"}</p>}</div>
        </State>
        </div>}
      </State>
    </>
  )
}

export function ReservationPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    id = query.get("id"),
    navigate = useNavigate(),
    load = useLoad(() => {
      if (!id) throw new Error("缺少预约编号，请返回预约列表")
      return api.get<any>(`/orders/reservations/${id}/`)
    }, [id]),
    [quote, setQuote] = useState<Row | null>(null),
    [quoteLoading, setQuoteLoading] = useState(false),
    [fundingEnabled, setFundingEnabled] = useState(false),
    [discountOptions, setDiscountOptions] = useState<Row[]>([]),
    [discountSelection, setDiscountSelection] = useState(""),
    [giftBalance, setGiftBalance] = useState(""),
    [compensationBalance, setCompensationBalance] = useState(""),
    quoteScope = useRef(id),
    [reason, setReason] = useState(""),
    [intent, setIntent] = useState<Intent | null>(() =>
      readIntent("reservation-convert-" + id),
    ),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const reservation = load.data,
    allowed = reservation?.allowed_actions || []
  quoteScope.current = id
  useEffect(() => { setQuote(null); setQuoteLoading(false); setFundingEnabled(false); setDiscountOptions([]); setDiscountSelection(""); setGiftBalance(""); setCompensationBalance("") }, [id])
  function fundingSelection() {
    const values = [giftBalance, compensationBalance]
    if (values.some(value => !/^\d*$/.test(value) || !Number.isSafeInteger(Number(value || 0) * 10) || Number(value || 0) * 10 > 2147483647))
      throw new Error("赠送和赔付余额须填写非负整数店币")
    return { gift_balance_amount: Number(giftBalance || 0) * 10,
      compensation_balance_amount: Number(compensationBalance || 0) * 10 }
  }
  function quoteUrl() {
    return queryUrl(`/orders/reservations/${id}/quote/`, {
      ...(fundingEnabled ? fundingSelection() : {}),
      ...(discountSelection ? {discount_selection_token: discountSelection} : {}),
    })
  }
  async function readQuote() {
    setQuote(null)
    setQuoteLoading(true)
    try {
    const selected = fundingEnabled ? fundingSelection() : {gift_balance_amount: 0, compensation_balance_amount: 0}
    const latest = await api.get<any>(quoteUrl()),
      service = await api.get<any>(
        `/orders/services/${reservation.service.id}/`,
      )
    if (
      Object.entries(latest.service || {}).some(
        ([key, value]) => service[key] !== value,
      )
    )
      throw new Error("商品信息已变化，请重新获取报价")
    if (quoteScope.current === id) {
      setFundingEnabled(latest.funding_selection_enabled === true)
      setDiscountOptions(latest.discount_options || [])
      setDiscountSelection(latest.discount_selection_token || "")
      setQuote({ ...latest, serviceDisplay: service, selected })
    }
    } catch (error) {
      if (quoteScope.current === id) { setDiscountOptions([]); setDiscountSelection("") }
      throw error
    } finally { if (quoteScope.current === id) setQuoteLoading(false) }
  }
  async function convert(original: Intent) {
    try {
      const result = await submitIntent("reservation-convert-" + id, original)
      if (result.order?.id) navigate("/order-detail?id=" + result.order.id)
      else load.reload()
    } finally {
      setIntent(readIntent("reservation-convert-" + id))
      setQuote(null)
      load.reload()
    }
  }
  function prepareConvert() {
    if (!quote || !reservation) return
    const approved = quote
    confirm({
      title: "确认预约成单",
      description: (
        <>
          <p>{approved.serviceDisplay.name}</p>
          <p>
            将支付 {money(approved.amount)} {coinText(" 浪花，并立即指派给")}{" "}
            <ProviderName value={reservation.provider?.nickname ||
              reservation.provider?.display_name || reservation.provider?.name} />
            。
          </p>
          {approved.funding_selection_enabled && <p>其中本金 {money(approved.amount - approved.selected.gift_balance_amount - approved.selected.compensation_balance_amount)}、赠送 {money(approved.selected.gift_balance_amount)}、赔付 {money(approved.selected.compensation_balance_amount)} {coinUnit()}。</p>}
        </>
      ),
      confirm: "确认成单",
      action: async () => {
        const current = await api.get<any>(quoteUrl())
        if (
          current.amount !== approved.amount ||
          current.funding_selection_enabled !== approved.funding_selection_enabled ||
          current.discount_option_key !== approved.discount_option_key ||
          !current.balance_sufficient ||
          JSON.stringify(fundingEnabled ? fundingSelection() : {gift_balance_amount: 0, compensation_balance_amount: 0}) !== JSON.stringify(approved.selected) ||
          JSON.stringify(current.service) !== JSON.stringify(approved.service)
        ) {
          setQuote(null)
          throw new Error("预约报价或套餐内容已变化，请重新获取报价")
        }
        const original = {
          id: requestId("reservation"),
          path: `/orders/reservations/${id}/convert/`,
          payload: {
            expected_amount: approved.amount,
            expected_service: approved.service,
            ...(approved.discount_selection_enabled ? {discount_selection_token: approved.discount_selection_token} : {}),
            ...(approved.funding_selection_enabled ? approved.selected : {}),
          },
        }
        storeIntent("reservation-convert-" + id, original)
        setIntent(original)
        await convert(original)
      },
    })
  }
  return (
    <>
      <BackLink to="/reservations">我的预约</BackLink>
      <Title
        title="预约详情"
        description={reservation?.reservation_no}
      />
      <Pending
        busy={busy}
        intent={intent}
        retry={() => {
          if (intent) void run(() => convert(intent), "").catch(() => {})
        }}
      />
      <State {...load}>
        {reservation && (
          <div className="boss-reservation-detail-layout">
            <div>
              <section className="boss-reservation-status"><div className="boss-section-heading"><StatusBadge status={reservation.status}>{reservationStatus(reservation.status)}</StatusBadge><span>预约不扣款</span></div><h2>{reservation.service?.name}</h2><div className="boss-reservation-provider"><Image src={reservation.provider?.avatar} alt={reservation.provider?.nickname || "预约陪玩"} className="boss-avatar" /><span>{reservation.status === "CONVERTED" ? "原预约：" : ""}<ProviderName value={reservation.provider?.nickname || reservation.provider?.display_name || reservation.provider?.name} /></span><small>· {reservation.game_rounds} 份服务</small></div>{reservation.status === "CONVERTED" && reservation.current_providers?.length > 0 && <p>当前履约：{reservation.current_providers.map((person: Row) => formatProviderName(person.name, store.provider_name_prefix)).join("、")}</p>}<p>{reservation.status === "CONFIRMED" ? "陪玩已确认时段，核对实时报价后即可成单。" : reservation.status === "PENDING" ? "正在等待陪玩确认，当前不会扣款。" : reservation.status === "CONVERTED" ? "预约已转为订单，后续服务进度在订单详情查看。" : reservation.status === "EXPIRED" ? "这个时段已结束，请重新安排服务。" : "查看预约处理记录"}</p>{reservation.reservation_no && <div className="boss-reservation-copy-row"><div><p>预约编号</p><strong>{reservation.reservation_no}</strong></div><Button variant="ghost" onClick={() => void navigator.clipboard.writeText(reservation.reservation_no).then(() => toast.success("预约编号已复制")).catch(() => toast.error("复制失败，请手动复制预约编号"))}><Copy size={16} />复制</Button></div>}</section>
              <Panel icon={CalendarClock} title="预约安排"><div className="boss-reservation-time-grid"><div><p>开始时间</p><strong>{date(reservation.start_time)}</strong></div><div><p>预计结束</p><strong>{date(reservation.end_time)}</strong></div></div><div className="boss-reservation-estimate"><span>预约时预估</span><strong>{money(reservation.estimated_amount)}<small>{coinUnit()}</small></strong></div><p className="boss-reservation-estimate-note">预估仅供参考，陪玩确认后以实时报价成单。</p></Panel>
              <Panel icon={Gamepad2} title="游戏与备注"><Info entries={[["游戏大区", reservation.game_region], ["游戏昵称", reservation.game_nickname], ["游戏 UID", reservation.game_uid], ["备注", reservation.remark], ["拒绝原因", reservation.reject_reason], ["取消原因", reservation.cancel_reason]]} /></Panel>
              <Panel title="预约操作">
              <div className="boss-actions" style={{ marginTop: 20 }}>
                {reservation.reservation_no && (
                  <Button
                    variant="outline"
                    onClick={() =>
                      void navigator.clipboard
                        .writeText(reservation.reservation_no)
                        .then(() => toast.success("预约编号已复制"))
                        .catch(() =>
                          toast.error("复制失败，请手动复制预约编号"),
                        )
                    }
                  >
                    复制预约编号
                  </Button>
                )}
                {reservation.order && (
                  <Button asChild>
                    <Link to={"/order-detail?id=" + reservation.order}>
                      查看关联订单
                    </Link>
                  </Button>
                )}
                {allowed.includes("cancel") && (
                  <Button
                    variant="destructive"
                    disabled={busy || load.loading || !!load.error}
                    onClick={() =>
                      confirm({
                        title: "取消预约",
                        description:
                          "取消后本次时段将不再保留。" +
                          (reason.trim() ? `取消原因：${reason.trim()}` : ""),
                        confirm: "确认取消预约",
                        variant: "destructive",
                        action: async () => {
                          try {
                            await api.post(
                              `/orders/reservations/${id}/cancel/`,
                              { reason: reason.trim() },
                            )
                          } finally {
                            setQuote(null)
                            load.reload()
                          }
                        },
                      })
                    }
                  >
                    取消预约
                  </Button>
                )}
                {reservation.status === "EXPIRED" && (
                  <Button asChild variant="outline">
                    <Link to={reservationRebookTarget(reservation)}>
                      重新预约
                    </Link>
                  </Button>
                )}
                <Button asChild variant="outline">
                  <Link to="/support">联系客服</Link>
                </Button>
              </div>
              {allowed.includes("cancel") && (
                <div style={{ marginTop: 16 }}>
                  <Field
                    label="取消原因（选填）"
                    value={reason}
                    onChange={setReason}
                    multiline
                    maxLength={255}
                  />
                </div>
              )}
              </Panel>
            </div>
            <aside>
            {allowed.includes("convert") && (
              <Panel icon={WalletCards} title="实时报价" className="boss-reservation-quote">
                {discountOptions.length > 0 && <label className="boss-field">优惠组合
                  <select autoComplete="off" name="优惠组合" aria-label="优惠组合" value={discountSelection} disabled={quoteLoading}
                    onChange={event => {setDiscountSelection(event.target.value); setQuote(null)}}>
                    {discountOptions.map((item, index) => <option key={item.key} value={item.selection_token}>
                      {item.label} · 实付 {money(item.amount)} {coinUnit()}{index === 0 ? "（推荐）" : ""}
                    </option>)}
                  </select>
                </label>}
                {fundingEnabled && <div className="boss-form-grid">
                  <Field label={`使用赠送（${coinUnit()}）`} value={giftBalance} onChange={value => {setGiftBalance(value); setQuote(null)}} inputMode="numeric" />
                  <Field label={`使用赔付（${coinUnit()}）`} value={compensationBalance} onChange={value => {setCompensationBalance(value); setQuote(null)}} inputMode="numeric" />
                  <p className="boss-muted">默认不使用赠送和赔付，剩余金额由本金支付；调整后请重新报价。</p>
                </div>}
                {!quote && !quoteLoading && <div className="boss-reservation-quote-intro"><ShieldCheck size={24} /><p>获取实时价格后再决定是否成单，此步骤不会扣款。</p></div>}
                {quoteLoading && <State compact loading><span className="sr-only">正在核对服务与钱包余额</span></State>}
                <Button
                  variant={quote ? "ghost" : "default"}
                  style={{ marginTop: 16 }}
                  disabled={busy || load.loading || !!load.error || !!intent}
                  onClick={() => void run(readQuote, "").catch(() => {})}
                >
                  获取最新报价
                </Button>
                {quote && (
                  <div className="boss-form" style={{ marginTop: 16 }}>
                    <Info
                      entries={[
                        ["当前套餐", quote.serviceDisplay.name],
                        [
                          "套餐规格",
                          [
                            quote.serviceDisplay.platform_name,
                            quote.serviceDisplay.security_level_name,
                            quote.serviceDisplay.provider_requirement?.label ||
                              quote.serviceDisplay.required_level_name,
                          ]
                            .filter(Boolean)
                            .join(" · "),
                        ],
                        ["可用余额", money(quote.wallet_balance) + coinText(" 浪花")],
                        ...(quote.funding_selection_enabled ? [
                          ["本金余额", money(quote.prepaid_balance) + " " + coinUnit()],
                          ["赠送余额", money(quote.gift_balance) + " " + coinUnit()],
                          ["赔付余额", money(quote.compensation_balance) + " " + coinUnit()],
                        ] as [string, string][] : []),
                      ]}
                    />
                    <div className="boss-quote-total boss-reservation-quote-total"><span>当前应付</span><strong>{money(quote.amount)}<small>{coinUnit()}</small></strong></div>
                    {quote.amount !== reservation.estimated_amount && (
                      <p className="boss-reservation-quote-change" role="status">
                        当前报价与预约预估金额不同，请核对后确认。
                      </p>
                    )}
                    <Button
                      disabled={busy || !quote.balance_sufficient || !!intent}
                      onClick={prepareConvert}
                    >
                      确认成单
                    </Button>
                    {!quote.balance_sufficient && <p role="alert">所选余额不足，请调整金额或联系客服核对。</p>}
                  </div>
                )}
              </Panel>
            )}
            </aside>
          </div>
        )}
      </State>
      {dialog}
    </>
  )
}

export function ReservationCreatePage() {
  const store = useCurrentStore()
  const query = useQuery(),
    navigate = useNavigate(),
    providerId = query.get("provider_id"),
    serviceId = query.get("service_id"),
    owner = getSession("boss").user?.id,
    pendingKey = `xa:store:${getStoreSlug()}:xa:boss:web:reservation-pending:${owner}`,
    [pending, setPending] = useState(
      () => sessionStorage.getItem(pendingKey) || "",
    ),
    [start, setStart] = useState(""),
    [duration, setDuration] = useState("60"),
    [form, setForm] = useState<Row>({
      rounds: "1",
      region: "",
      nickname: "",
      uid: "",
      remark: "",
    }),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const load = useLoad(
    async () => {
      if (!serviceId || !providerId)
        throw new Error("请先在服务详情选择要预约的陪玩")
      const [service, provider, profile] = await Promise.all([
        api.get<any>(`/orders/services/${serviceId}/`),
        api.get<any>(`/users/escorts/${providerId}/?service_item=${serviceId}`),
        api.get<any>("/users/me/"),
      ])
      return { service, provider, profile }
    },
    [serviceId, providerId],
    false,
  )
  async function submit() {
    if (!load.data) return
    validateAccountFields(load.data.service, { game_region: String(form.region), game_nickname: String(form.nickname), game_uid: String(form.uid) })
    const dateValue = new Date(start + ":00+08:00"),
      roundsValue = Number(form.rounds)
    if (
      !Number.isFinite(dateValue.getTime()) ||
      dateValue.getTime() <= Date.now()
    )
      throw new Error("请选择未来的预约时间（北京时间）")
    if (Number(start.slice(-2)) % 15)
      throw new Error("开始时间请按 15 分钟选择")
    if (!Number.isInteger(roundsValue) || roundsValue < 1)
      throw new Error("请填写有效的服务数量")
    const payload = {
      provider_account_id: Number(providerId),
      service_id: Number(serviceId),
      start_time: dateValue.toISOString(),
      end_time: new Date(
        dateValue.getTime() + Number(duration) * 60000,
      ).toISOString(),
      game_rounds: roundsValue,
      game_region: form.region.trim(),
      game_nickname: form.nickname.trim(),
      game_uid: form.uid.trim(),
      remark: form.remark.trim(),
    }
    const summary = `${start.replace("T", " ")}（北京时间），${duration} 分钟`
    confirm({
      title: "确认提交预约",
      description: (
        <>
          <p>
            {load.data.service.name} · <ProviderName value={load.data.provider.nickname} />
          </p>
          <p>{summary}</p>
          <p>提交后等待陪玩确认，成单时再核对报价。</p>
        </>
      ),
      action: async () => {
        sessionStorage.setItem(pendingKey, summary)
        setPending(summary)
        try {
          const result = await api.post<any>("/orders/reservations/", payload)
          sessionStorage.removeItem(pendingKey)
          setPending("")
          navigate("/reservation-detail?id=" + result.id, { replace: true })
        } catch (error) {
          if (!uncertain(error)) {
            sessionStorage.removeItem(pendingKey)
            setPending("")
          }
          throw error
        }
      },
    })
  }
  const previewStart = new Date(start + ":00+08:00"),
    previewEnd = Number.isFinite(previewStart.getTime()) ? new Date(previewStart.getTime() + Number(duration) * 60000) : null,
    scheduleText = previewEnd ? `${start.replace("T", " ")} — ${date(previewEnd.toISOString())}（北京时间）` : "请选择有效时间"
  return (
    <>
      <BackLink to="/companions">陪玩列表</BackLink>
      <Title title="预约陪玩" description="先锁定时间，陪玩确认后再核对价格。" /><nav className="boss-reservation-steps" aria-label="预约流程：选择时间、等待确认、转成订单">{["选择时间", "等待确认", "转成订单"].map((text, index) => <div key={text} aria-current={index === 0 ? "step" : undefined}><span>{index === 0 ? <Check size={14} /> : index + 1}</span>{text}</div>)}</nav>
      {pending && (
        <div className="boss-feedback" role="alert">
          <strong>上次预约结果待确认</strong>
          <p>{pending}。请先查看预约列表，避免重复提交。</p>
          <div className="boss-actions">
            <Button asChild>
              <Link to="/reservations">核对预约列表</Link>
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                confirm({
                  title: "确认已核对上次预约",
                  description:
                    "请确认已查看预约列表并与客服核对，上次预约不存在后再提交新的预约。",
                  confirm: "已核对，继续填写",
                  action: async () => {
                    sessionStorage.removeItem(pendingKey)
                    setPending("")
                  },
                })
              }
            >
              我已完成核对
            </Button>
          </div>
        </div>
      )}
      <State {...load}>
        {load.data && (
            <form
              id="boss-reservation-create-form"
              className="boss-reservation-create-layout"
              style={{ marginTop: 20 }}
              onSubmit={(event) => {
                event.preventDefault()
                void run(submit, "").catch(() => {})
              }}
            >
              <section className="boss-reservation-context"><Image src={load.data.provider.avatar} alt={load.data.provider.nickname} className="boss-avatar" /><div><p>本次预约</p><h2>{load.data.service.name}</h2><p><ProviderName value={load.data.provider.nickname} /> · {money(load.data.service.price)} {coinUnit()} / {load.data.service.price_unit || "份"}</p></div><StatusBadge status="PENDING">待提交</StatusBadge></section>
              <div><Panel icon={CalendarClock} number="01" title="选择时间" note="按你的方便时间安排">
              <div className="boss-form-grid">
                <Field disabled={busy || !!pending}
                  label="开始时间（北京时间）"
                  type="datetime-local"
                  value={start}
                  onChange={setStart}
                  step={900}
                  required
                  hint="按 15 分钟选择，可预约未来 30 天内的时段"
                />
                <Choice disabled={busy || !!pending}
                  label="预约时长"
                  value={duration}
                  onChange={setDuration}
                  options={Array.from(
                    { length: 47 },
                    (_, index) => (index + 2) * 15,
                  ).map((value) => ({
                    value,
                    label: value + " 分钟",
                  }))}
                />

              </div><div className="boss-reservation-time-preview"><p>预计时段 · 北京时间</p><strong>{scheduleText}</strong><small>共 {duration} 分钟，{form.rounds || "0"} 份服务</small></div></Panel><Panel icon={Gamepad2} number="02" title="游戏资料" note="帮助陪玩提前做好准备">                <Field disabled={busy || !!pending}
                  label="服务数量"
                  type="number"
                  value={form.rounds}
                  onChange={(value) =>
                    setForm((current) => ({ ...current, rounds: value }))
                  }
                  min={1}
                  required
                /><div className="boss-form-grid">
                <Field disabled={busy || !!pending}
                  label="游戏大区"
                  required={requiredAccountFields(load.data.service)?.includes("game_region") === true}
                  value={form.region}
                  onChange={(value) =>
                    setForm((current) => ({ ...current, region: value }))
                  }
                  maxLength={50}
                />
                <Field disabled={busy || !!pending}
                  label="游戏昵称"
                  required={requiredAccountFields(load.data.service)?.includes("game_nickname") === true}
                  value={form.nickname}
                  onChange={(value) =>
                    setForm((current) => ({ ...current, nickname: value }))
                  }
                  maxLength={50}
                />
                <Field disabled={busy || !!pending}
                  label="游戏 UID"
                  required={requiredAccountFields(load.data.service)?.includes("game_uid") === true}
                  value={form.uid}
                  onChange={(value) =>
                    setForm((current) => ({ ...current, uid: value }))
                  }
                  maxLength={50}
                />
              </div>
              <Field disabled={busy || !!pending}
                label="预约备注"
                value={form.remark}
                onChange={(value) =>
                  setForm((current) => ({ ...current, remark: value }))
                }
                multiline
                maxLength={255}
              />
              </Panel></div><aside><Panel title="提交前确认" icon={ShieldCheck}><Info entries={[["服务", load.data.service.name], ["陪玩", formatProviderName(load.data.provider.nickname, store.provider_name_prefix)], ["时长", duration + " 分钟"], ["数量", form.rounds + " 份服务"]]} /><div className="boss-reservation-time-preview"><p>预约时段</p><strong>{scheduleText}</strong></div><p className="boss-muted">提交预约不会扣款。陪玩确认后，可查看实时报价并决定是否成单。</p><div className="boss-reservation-submit">              <Button
                disabled={busy || load.loading || !!load.error || !!pending}
              >
                <CalendarCheck2 size={16} />{busy ? "正在提交…" : "核对并提交预约"}
              </Button>
<Button asChild variant="outline"><Link to="/reservations">查看我的预约</Link></Button></div></Panel></aside>
            </form>
        )}
      </State>
      {load.data && !load.loading && !load.error && !pending && <div className="boss-reservation-mobile-submit"><LinkButton variant="outline" disabled={busy} to="/reservations">我的预约</LinkButton><Button type="submit" form="boss-reservation-create-form" disabled={busy || !!pending}><CalendarCheck2 size={16} />{busy ? "正在提交…" : "提交预约"}</Button></div>}
      {dialog}
    </>
  )
}
