import { coinText, coinUnit, useCurrentStore } from "@xa/shared"
import { getStoreSlug } from "@xa/shared"
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"
import { getSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Ticket, Gamepad2 } from "lucide-react"
import {
  api,
  Choice,
  Field,
  Explanation,
  Info,
  Image,
  money,
  Panel,
  Pending,
  queryUrl,
  readIntent,
  requestId,
  rows,
  State,
  storeIntent,
  submitIntent,
  BackLink,
  Title,
  useAction,
  useConfirm,
  useLoad,
  useQuery,
  type Intent,
  type Row,
} from "./ui"
import { providerIds, providerLimit } from "./commerce"
import { formatProviderName, ProviderName } from "./provider-name"
import { requiredAccountFields, validateAccountFields } from "./account-fields"

const serviceTerms = [
  "name",
  "unit_price",
  "price_unit",
  "service_type",
  "game_category",
  "gameplay",
  "platform",
  "security_level",
  "provider_kind",
  "provider_grade",
  "required_level",
  "service_category",
  "purchase_mode",
  "is_addon",
  "experience_kind",
  "allows_discount_coupons",
]
const fixedRounds = (service: Row) =>
  !!service.experience_kind ||
  service.gameplay_kind === "ROULETTE" ||
  (providerLimit(service) === 2 &&
    ["小时", "时", "hour"].includes(service.price_unit))
const validTimeout = (value: unknown) =>
  Number.isInteger(value) && Number(value) >= 1 && Number(value) <= 1440
function storedDraft(key: string) {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "{}")
  } catch {
    return {}
  }
}

export function CheckoutPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    navigate = useNavigate(),
    serviceId = query.get("service_id") || query.get("serviceId"),
    accountId = getSession("boss").user?.id,
    draftKey = `xa:store:${getStoreSlug()}:xa:boss:web:checkout:${accountId}:${serviceId}`,
    draft = useMemo(() => storedDraft(draftKey), [draftKey])
  const [form, setForm] = useState<Row>(() => ({
      rounds: 1,
      region: "",
      nickname: "",
      uid: "",
      remark: "",
      gender: "ANY",
      dispatch: "PUBLIC",
      mode: "BOSS_SELECT",
      coupon: Number(query.get("coupon_id")) || 0,
      contact: 0,
      giftBalance: "",
      compensationBalance: "",
      ...draft,
    })),
    [ids, setIds] = useState<number[]>(() =>
      providerIds(
        query.get("provider_ids") ||
          query.get("provider_id") ||
          String(draft.provider_ids || "")
      )
    ),
    [quote, setQuote] = useState<Row | null>(null),
    [fundingEnabled, setFundingEnabled] = useState(false),
    [discountState, setDiscountState] = useState<"loading" | "legacy" | "ready" | "error">("loading"),
    [discountOptions, setDiscountOptions] = useState<Row[]>([]),
    [discountKey, setDiscountKey] = useState(""),
    [discountError, setDiscountError] = useState(""),
    [quoteError, setQuoteError] = useState(""),
    [quoting, setQuoting] = useState(false),
    [intent, setIntent] = useState<Intent | null>(() => readIntent("order")),
    [storageError, setStorageError] = useState(""),
    initialized = useRef(false),
    quoteSeq = useRef(0),
    discountSeq = useRef(0),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const load = useLoad(async () => {
    if (!serviceId) throw new Error("请先在服务目录选择套餐")
    const [service, me, coupons, contacts, traffic, providers] =
      await Promise.all([
        api.get<any>(`/orders/services/${serviceId}/`),
        api.get<any>("/users/me/"),
        api.get<any>(`/coupons/mine/?usable=1&service_id=${serviceId}`),
        api.get<any>("/support/contact-cards/"),
        api.get<any>("/orders/traffic-status/"),
        Promise.all(
          ids.map((id) =>
            api.get<any>(
              queryUrl(`/users/escorts/${id}/`, {
                service_item: serviceId,
                gender: form.gender,
              })
            )
          )
        ),
      ])
    if (service.is_gift) throw new Error("请从赠礼页面购买礼物")
    return {
      service,
      me,
      coupons: rows(coupons),
      contacts: rows(contacts),
      traffic,
      providers,
    }
  }, [serviceId, ids.join(","), form.gender])
  const service = load.data?.service,
    limit = providerLimit(service),
    shortcutGender =
      service?.service_type === "COMPANION" ? form.gender : "ANY",
    payload = useMemo(() => {
      const result: Row = {
        dispatch_route: ids.length ? "PUBLIC" : form.dispatch,
        dispatch_mode:
          !ids.length && limit === 1 && form.dispatch === "PUBLIC"
            ? form.mode
            : "BOSS_SELECT",
        service_id: Number(serviceId),
        game_rounds: Number(form.rounds),
        game_region: String(form.region).trim(),
        game_nickname: String(form.nickname).trim(),
        game_uid: String(form.uid).trim(),
        remark: String(form.remark).trim(),
      }
      if (service?.service_type === "COMPANION")
        result.provider_gender = form.gender
      if (ids.length) {
        if (limit === 2) result.provider_ids = ids
        else result.provider_id = ids[0]
      }
      const selectedDiscount = discountOptions.find((option) => option.key === discountKey)
      if (discountState === "ready" && selectedDiscount) result.discount_selection_token = selectedDiscount.selection_token
      else if (discountState !== "ready" && Number(form.coupon)) result.user_coupon_id = Number(form.coupon)
      if (Number(form.contact)) result.support_contact_id = Number(form.contact)
      if (fundingEnabled) {
        result.gift_balance_amount = Number(form.giftBalance || 0) * 10
        result.compensation_balance_amount = Number(form.compensationBalance || 0) * 10
      }
      return result
    }, [form, ids, serviceId, service, limit, fundingEnabled, discountState, discountOptions, discountKey])
  const shortcuts = useLoad(
    () =>
      serviceId
        ? api.get<any>(
            queryUrl("/users/escorts/", {
              shortcuts: 1,
              service_item: serviceId,
              gender: shortcutGender,
            })
          )
        : Promise.resolve({ recent: [], recommended: [] }),
    [serviceId, shortcutGender]
  )
  function chooseShortcut(provider: Row) {
    if (!provider.can_order) {
      toast.error("该陪玩暂不可接此单，请重新选择")
      return
    }
    if (ids.includes(provider.id))
      setIds((current) => current.filter((id) => id !== provider.id))
    else if (limit === 1) setIds([provider.id])
    else if (ids.length < limit) setIds((current) => [...current, provider.id])
    else toast.error("已选齐两位陪玩，请先取消一位再选择")
  }
  useEffect(() => {
    try {
      sessionStorage.setItem(
        draftKey,
        JSON.stringify({ ...form, provider_ids: ids.join(",") })
      )
    } catch {
      /* A draft is optional; transaction credentials are persisted separately before writes. */
    }
  }, [form, ids, draftKey])
  useEffect(() => {
    if (!load.data) return
    const current = load.data
    if (current.service.service_type !== "COMPANION" && form.gender !== "ANY")
      setForm((value) => ({ ...value, gender: "ANY" }))
    if (!initialized.current) {
      const profile =
        rows(current.me.game_profiles).find(
          (item) => item.game_category === current.service.game_category
        ) || {}
      initialized.current = true
      setForm((value) => ({
        ...value,
        region: value.region || profile.region || "",
        nickname: value.nickname || profile.nickname || "",
        uid: value.uid || profile.uid || "",
        rounds: fixedRounds(current.service) ? 1 : value.rounds,
      }))
    } else if (fixedRounds(current.service))
      setForm((value) =>
        Number(value.rounds) === 1 ? value : { ...value, rounds: 1 }
      )
    if (
      Number(form.coupon) &&
      !current.coupons.some((item) => item.id === Number(form.coupon))
    ) {
      setForm((value) => ({ ...value, coupon: 0 }))
      setQuoteError("所选优惠券已不可用，请核对新报价")
    }
    if (
      ["NEW_VALUE", "DAILY_VALUE"].includes(current.service.experience_kind) &&
      !Number(form.coupon) &&
      current.coupons.length
    ) {
      setForm((value) => ({ ...value, coupon: current.coupons[0].id }))
    }
  }, [load.data])
  useEffect(() => {
    if (!service || !serviceId) return
    const seq = ++discountSeq.current
    setDiscountState("loading"); setDiscountError(""); setDiscountOptions([]); setDiscountKey("")
    if (service.experience_kind || service.service_type === "EXPERIENCE") {
      setDiscountState("legacy")
      return
    }
    api.post<any>("/orders/discount-options-preview/", {service_id:Number(serviceId), game_rounds:Number(form.rounds)})
      .then(result => {
        if (seq !== discountSeq.current) return
        if (!result.applicable || !result.payment_selection_enabled) { setDiscountState("legacy"); return }
        if (!Array.isArray(result.options) || !result.options.some((item:Row) => item.key === result.recommended_key && item.selection_token))
          throw new Error("优惠组合无法确认，请重新进入页面")
        setDiscountOptions(result.options); setDiscountKey(result.recommended_key); setDiscountState("ready")
      })
      .catch((error: any) => {
        if (seq !== discountSeq.current) return
        if (error?.status === 404 && (!error.errorCode || error.errorCode === "DISCOUNT_PREVIEW_DISABLED")) setDiscountState("legacy")
        else { setDiscountError(error.message || "优惠组合读取失败"); setDiscountState("error") }
      })
    return () => { ++discountSeq.current }
  }, [serviceId, form.rounds, service?.id, service?.experience_kind, service?.service_type])
  useEffect(() => {
    setQuote(null)
    if (!service || load.loading || load.error || discountState === "loading" || discountState === "error") return
    const seq = ++quoteSeq.current
    const timer = window.setTimeout(async () => {
      setQuoting(true)
      setQuoteError("")
      try {
        if (
          !Number.isInteger(payload.game_rounds) ||
          payload.game_rounds < 1 ||
          payload.game_rounds > 99
        )
          throw new Error("购买数量须为 1 至 99 的整数")
        if (ids.length && ids.length !== limit)
          throw new Error(`请选齐 ${limit} 位陪玩，或改为平台安排`)
        if (load.data?.providers.some((item) => !item.can_order))
          throw new Error("所选陪玩当前不可接单，请重新选择")
        if (fundingEnabled && [form.giftBalance, form.compensationBalance].some((value) =>
          !/^\d*$/.test(String(value)) || !Number.isSafeInteger(Number(value || 0) * 10) || Number(value || 0) * 10 > 2147483647))
          throw new Error("赠送和赔付余额须填写非负整数店币")
        const result = await api.post<any>("/orders/quote/", payload)
        if (seq === quoteSeq.current) setFundingEnabled(result.funding_selection_enabled === true)
        if (!ids.length && !validTimeout(result.pending_timeout_minutes))
          throw new Error("待接单时限无法确认，请刷新报价")
        if (seq === quoteSeq.current) setQuote(result)
      } catch (error: any) {
        if (seq === quoteSeq.current) setQuoteError(error.message)
      } finally {
        if (seq === quoteSeq.current) setQuoting(false)
      }
    }, 350)
    return () => {
      ++quoteSeq.current
      window.clearTimeout(timer)
    }
  }, [JSON.stringify(payload), load.data, load.loading, load.error, discountState])
  function update(key: string, value: any) {
    setQuote(null)
    setForm((current) => ({ ...current, [key]: value }))
  }
  function syncIntent() {
    try {
      setIntent(readIntent("order"))
    } catch {
      setStorageError(
        "无法读取上次下单凭证，请检查浏览器存储，或联系客服核对订单"
      )
    }
  }
  async function send(original: Intent) {
    try {
      const result = await submitIntent("order", original)
      if (!result?.id) throw new Error("已收到下单响应，请在订单列表核对结果")
      sessionStorage.removeItem(draftKey)
      navigate("/order-detail?id=" + result.id, { replace: true })
    } finally {
      syncIntent()
    }
  }
  async function resolve() {
    if (!intent) return
    const result = await api.post<any>("/orders/create/resolve/", {
      client_request_id: intent.id,
      payload: { ...intent.payload, client_request_id: intent.id },
    })
    if (
      result.status === "COMMITTED" &&
      result.fundResult?.code === 0 &&
      result.fundResult.data?.id
    ) {
      storeIntent("order", null)
      syncIntent()
      navigate("/order-detail?id=" + result.fundResult.data.id)
    } else if (result.status === "SEALED") {
      storeIntent("order", null)
      syncIntent()
      load.reload()
    } else throw new Error("结果仍待核对，请保留原凭证并联系客服")
  }
  async function prepare() {
    if (!service || !quote || !load.data) return
    validateAccountFields(service, { game_region: String(form.region), game_nickname: String(form.nickname), game_uid: String(form.uid) })
    if (!load.data.traffic.self_order_enabled)
      throw new Error("自主下单暂时关闭，请联系客服")
    if (readIntent("order")) {
      syncIntent()
      throw new Error("请先核对上次订单")
    }
    const [current, latest] = await Promise.all([
      api.get<any>(`/orders/services/${serviceId}/`),
      api.post<any>("/orders/quote/", payload),
    ])
    if (JSON.stringify(current.account_fields) !== JSON.stringify(service.account_fields))
      throw new Error("游戏资料要求已变化，请刷新页面重新核对")
    validateAccountFields(current, { game_region: String(form.region), game_nickname: String(form.nickname), game_uid: String(form.uid) })
    if (
      providerLimit(current) !== limit ||
      serviceTerms.some((key) => current[key] !== service[key])
    )
      throw new Error("套餐内容已变化，请刷新报价后重新确认")
    const fields = [
      "original_amount",
      "boss_discount",
      "promo_discount",
      "coupon_discount",
    ]
    if (
      latest.amount !== quote.amount ||
      latest.funding_selection_enabled !== quote.funding_selection_enabled ||
      fields.some((key) => latest[key] !== quote[key])
    )
      throw new Error("价格或优惠已变化，请刷新报价后重新确认")
    if (!latest.balance_sufficient) throw new Error("余额不足，请联系客服充值")
    if (
      !ids.length &&
      (!validTimeout(latest.pending_timeout_minutes) ||
        latest.pending_timeout_minutes !== quote.pending_timeout_minutes)
    )
      throw new Error("待接单时限已变化，请刷新报价后重新确认")
    const expectedService: Row = {
      name: current.name,
      price_unit: current.price_unit,
      service_type: current.service_type,
      game_category: current.game_category,
      billing_provider_count: providerLimit(current),
      unit_price: current.unit_price,
    }
    for (const key of serviceTerms)
      if (current[key] !== undefined) expectedService[key] = current[key]
    const finalPayload: Row = {
      ...payload,
      expected_service: expectedService,
      expected_quote: Object.fromEntries(
        fields.map((key) => [key, latest[key]])
      ),
      expected_amount: latest.amount,
      ...(!ids.length
        ? { expected_timeout_minutes: latest.pending_timeout_minutes }
        : {}),
    }
    const arrangement = ids.length
      ? `已指定 ${load.data.providers.map((item) => formatProviderName(item.nickname, store.provider_name_prefix)).join("、")}`
      : form.dispatch === "STAFF"
        ? `等待客服指定 ${limit} 位陪玩，不进入公开接单池`
        : limit === 2
          ? "符合条件的陪玩组队报名，由你在订单详情选人"
          : form.mode === "AUTO_GRAB"
            ? "符合条件的陪玩直接接单"
            : "陪玩报名后，由你在订单详情选人"
    confirm({
      title: latest.amount === 0 ? "确认下单" : "确认余额支付",
      description: (
        <>
          <p>
            {current.name} · {form.rounds} {current.price_unit || "份"}
          </p>
          <p>实付 {money(latest.amount)} {coinText(" 浪花。")}</p>
          {latest.funding_selection_enabled && <p>其中本金 {money(latest.amount - Number(payload.gift_balance_amount || 0) - Number(payload.compensation_balance_amount || 0))}、赠送 {money(payload.gift_balance_amount || 0)}、赔付 {money(payload.compensation_balance_amount || 0)} {coinUnit()}。</p>}
          <p>{arrangement}。</p>
          {!ids.length && (
            <p>
              待接单时限 {latest.pending_timeout_minutes}{" "}
              分钟。到期仍未安排将自动取消
              {latest.amount > 0 ? "并退回余额" : ""}。
            </p>
          )}
        </>
      ),
      confirm: latest.amount === 0 ? "确认下单" : "确认支付",
      action: async () => {
        if (readIntent("order")) throw new Error("请先核对上次订单")
        const original = {
          id: requestId("order"),
          path: "/orders/create/",
          payload: finalPayload,
        }
        storeIntent("order", original)
        setIntent(original)
        await send(original)
      },
    })
  }
  return (
    <>
      <BackLink to={queryUrl("/service", { id: serviceId, provider_id: ids[0] || "", coupon_id: query.get("coupon_id") || "" })}>服务详情</BackLink>
      <Title title="确认下单" description="核对服务、游戏资料与实际应付金额" />
      <Pending
        busy={busy}
        intent={intent}
        retry={() => {
          if (intent) void run(() => send(intent), "").catch(() => {})
        }}
        resolve={() =>
          confirm({
            title: "核对或停止上次下单",
            description:
              "如已下单，将打开原订单；如尚未下单，系统将停止原请求，随后可重新购买。",
            confirm: "确认核对",
            action: resolve,
          })
        }
      />
      {storageError && (
        <div role="alert" className="boss-feedback error">
          {storageError}
        </div>
      )}
      {load.data && !load.data.traffic.self_order_enabled && <div className="boss-feedback error" role="alert"><p>自主下单暂时关闭，请联系客服。</p><Button asChild variant="outline"><Link to="/support">联系客服</Link></Button></div>}
      <State {...load}>
        {service && (
          <div className="boss-columns boss-checkout-layout">
            <section className="boss-checkout-service-context"><div className="boss-section-heading"><div><h2>本单服务</h2><p>服务与规格来自上一步选择</p></div></div><div className="boss-checkout-service-card"><span><Gamepad2 size={24} /></span><div><h3>{service.name}</h3><p>{[service.catalog_variant, service.platform_name, service.security_level_name, service.provider_requirement?.label || service.required_level_name].filter(Boolean).join(" · ")}</p></div><strong>{money(service.price)}<small>{coinUnit()} / {service.price_unit || "份"}</small></strong></div></section>
            <div>
              <Panel title="服务与安排">
                <Info
                  entries={[
                    ["服务", service.name],
                    [
                      "规格",
                      [
                        service.platform_name,
                        service.security_level_name,
                        service.catalog_variant,
                      ]
                        .filter(Boolean)
                        .join(" · "),
                    ],
                    [
                      "接单资格",
                      service.provider_requirement?.label ||
                        service.required_level_name,
                    ],
                    ["套餐人数", limit + " 位"],
                  ]}
                />
                <div className="boss-form" style={{ marginTop: 20 }}>
                  <Field
                    label="购买数量"
                    type="number"
                    value={form.rounds}
                    onChange={(value) => update("rounds", value)}
                    min={1}
                    max={99}
                    disabled={fixedRounds(service) || busy}
                    hint={
                      fixedRounds(service)
                        ? "当前套餐按固定数量 1 购买"
                        : `单位：${service.price_unit || "份"}`
                    }
                  />
                  {service.service_type === "COMPANION" && (
                    <Choice
                      label="性别偏好"
                      value={form.gender}
                      onChange={(value) => {
                        update("gender", value)
                        setIds((current) =>
                          current.filter((id) =>
                            load.data?.providers.some(
                              (provider) =>
                                provider.id === id &&
                                (value === "ANY" || provider.gender === value)
                            )
                          )
                        )
                      }}
                      options={[
                        { value: "ANY", label: "不限" },
                        { value: "MALE", label: "男陪" },
                        { value: "FEMALE", label: "女陪" },
                      ]}
                    />
                  )}
                  <Info
                    entries={[
                      [
                        "所选陪玩",
                        load.data?.providers
                          .map((item) => formatProviderName(item.nickname, store.provider_name_prefix))
                          .join("、") || "由平台安排",
                      ],
                    ]}
                  />
                  <div className="boss-actions">
                    <Button asChild variant="outline">
                      <Link
                        to={queryUrl("/companion-picker", {
                          service_id: serviceId,
                          selected_provider_ids: ids.join(","),
                          provider_gender: form.gender,
                          coupon_id: form.coupon,
                        })}
                      >
                        挑选陪玩
                      </Link>
                    </Button>
                    {ids.length > 0 && (
                      <Button variant="ghost" onClick={() => setIds([])}>
                        取消指定陪玩
                      </Button>
                    )}
                  </div>
                  {!ids.length && (
                    <>
                      <Choice
                        label="派单方式"
                        value={form.dispatch}
                        onChange={(value) => update("dispatch", value)}
                        options={[
                          { value: "PUBLIC", label: "公开接单" },
                          { value: "STAFF", label: "客服派单" },
                        ]}
                      />
                      {form.dispatch === "PUBLIC" && limit === 1 && (
                        <Choice
                          label="公开接单方式"
                          value={form.mode}
                          onChange={(value) => update("mode", value)}
                          options={[
                            {
                              value: "BOSS_SELECT",
                              label: "陪玩报名，由我选人",
                            },
                            {
                              value: "AUTO_GRAB",
                              label: "符合条件的陪玩直接接单",
                            },
                          ]}
                        />
                      )}
                    </>
                  )}
                  {discountState === "ready" ? <Choice
                    label="优惠组合"
                    value={discountKey}
                    onChange={(value) => {setQuote(null); setDiscountKey(String(value))}}
                    options={discountOptions.map(item => ({value:item.key, label:`${item.label} · 实付 ${money(item.amount)} ${coinUnit()}${item.key === discountOptions[0]?.key ? "（推荐）" : ""}`}))}
                  /> : <Choice
                    label="优惠券"
                    value={form.coupon}
                    onChange={(value) => update("coupon", Number(value))}
                    options={[
                      ...(["NEW_VALUE", "DAILY_VALUE"].includes(
                        service.experience_kind
                      )
                        ? []
                        : [
                            {
                              value: 0,
                              label: service.experience_kind
                                ? "体验单专价，不参与活动"
                                : "不使用优惠券",
                            },
                          ]),
                      ...(load.data?.coupons || []).map((item) => ({
                        value: item.id,
                        label: `${item.name}${item.discount_type === "QUALIFICATION" ? " · 套餐资格券" : item.discount_type === "RATE" ? ` · ${item.discount_rate / 10}折` : ` · 减${money(item.amount)}${coinUnit()}`}`,
                      })),
                    ]}
                  />}
                  {discountState === "error" && <p role="alert">{discountError}</p>}
                  <Choice
                    label="订单客服"
                    value={form.contact}
                    onChange={(value) => update("contact", Number(value))}
                    options={[
                      { value: 0, label: "由平台安排客服" },
                      ...(load.data?.contacts || []).map((item) => ({
                        value: item.id,
                        label: item.name || item.nickname,
                      })),
                    ]}
                  />
                </div>
              </Panel>
              <Panel title="快捷挑选陪玩">
                <State compact {...shortcuts}>
                  {[
                    ["recent", "最近一起玩过"],
                    ["recommended", "平台推荐"],
                  ].map(([key, title]) => (
                    <div key={key}>
                      <h3>{title}</h3>
                      {rows(shortcuts.data?.[key])
                        .slice(0, 4)
                        .map((provider) => (
                          <div className="boss-list-row" key={provider.id}>
                            <Image
                              src={provider.avatar}
                              alt={provider.nickname}
                              className="boss-avatar"
                            />
                            <div>
                              <strong><ProviderName value={provider.nickname} /></strong>
                              <p className="boss-muted">
                                {provider.level_name || provider.rank}
                              </p>
                            </div>
                            <Button
                              className="right boss-choice-button"
                              aria-pressed={ids.includes(provider.id)}
                              variant={
                                ids.includes(provider.id)
                                  ? "secondary"
                                  : "outline"
                              }
                              disabled={
                                busy ||
                                shortcuts.loading ||
                                (!ids.includes(provider.id) &&
                                  !provider.can_order)
                              }
                              onClick={() => chooseShortcut(provider)}
                            >
                              {ids.includes(provider.id)
                                ? "已选，取消"
                                : "选择 TA"}
                            </Button>
                          </div>
                        ))}
                      {!rows(shortcuts.data?.[key]).length && (
                        <p className="boss-muted">
                          {key === "recent"
                            ? "暂无符合当前服务的最近陪玩"
                            : "暂无符合当前服务的推荐陪玩"}
                        </p>
                      )}
                    </div>
                  ))}
                </State>
              </Panel>
              <Panel title="游戏资料">
                <div className="boss-form-grid">
                  <Field
                    label="游戏大区"
                    value={form.region}
                    onChange={(value) => update("region", value)}
                    required={requiredAccountFields(service)?.includes("game_region") === true}
                    maxLength={50}
                  />
                  <Field
                    label="游戏昵称"
                    value={form.nickname}
                    onChange={(value) => update("nickname", value)}
                    required={requiredAccountFields(service)?.includes("game_nickname") === true}
                    maxLength={50}
                  />
                  <Field
                    label="游戏 UID"
                    value={form.uid}
                    onChange={(value) => update("uid", value)}
                    required={requiredAccountFields(service)?.includes("game_uid") === true}
                    maxLength={50}
                  />
                  <Field
                    label="订单备注"
                    value={form.remark}
                    onChange={(value) => update("remark", value)}
                    multiline
                    maxLength={255}
                  />
                </div>
              </Panel>
            </div>
            {fundingEnabled && <Panel title="选择余额来源">
              <p className="boss-muted">赠送和赔付默认不使用，剩余金额由本金支付。</p>
              <div className="boss-form-grid">
                <Field label={`使用赠送（${coinUnit()}）`} value={form.giftBalance} onChange={(value) => update("giftBalance", value)} inputMode="numeric" />
                <Field label={`使用赔付（${coinUnit()}）`} value={form.compensationBalance} onChange={(value) => update("compensationBalance", value)} inputMode="numeric" />
              </div>
            </Panel>}
            <Panel icon={Ticket} title="本次报价" className="boss-checkout-quote">
              <State compact loading={quoting} error={quoteError} reload={load.reload}>
                {quote && (
                  <>
                    <Info
                      entries={[
                        ["原价", money(quote.original_amount) + coinText(" 浪花")],
                        [
                          "会员优惠",
                          "−" + money(quote.boss_discount) + coinText(" 浪花"),
                        ],
                        [
                          "活动优惠",
                          "−" + money(quote.promo_discount) + coinText(" 浪花"),
                        ],
                        [
                          "优惠券",
                          "−" + money(quote.coupon_discount) + coinText(" 浪花"),
                        ],
                        ["可用余额", money(quote.wallet_balance) + coinText(" 浪花")],
                        ...(quote.funding_selection_enabled ? [
                          ["本金余额", money(quote.prepaid_balance) + " " + coinUnit()],
                          ["赠送余额", money(quote.gift_balance) + " " + coinUnit()],
                          ["赔付余额", money(quote.compensation_balance) + " " + coinUnit()],
                        ] as [string, string][] : []),
                        [
                          "待接单时限",
                          !ids.length
                            ? quote.pending_timeout_minutes + " 分钟"
                            : undefined,
                        ],
                      ]}
                    />
                    <div className="boss-quote-total"><span>实付</span><strong>{money(quote.amount)}<small>{coinUnit()}</small></strong></div>
                    {!quote.balance_sufficient && (
                      <p role="alert">所选余额不足，请调整赠送或赔付金额，或联系客服核对。</p>
                    )}
                  </>
                )}
              </State>
              <div className="boss-form boss-checkout-submit" style={{ marginTop: 20 }}>
                <div className="boss-checkout-submit-amount"><span>实际应付</span><strong>{quoting ? "核价中…" : quote ? money(quote.amount) : "待核价"}{quote && !quoting && <small>{coinUnit()}</small>}</strong></div>
                <Button
                  disabled={
                    busy ||
                    !quote ||
                    quoting ||
                    !!intent ||
                    !!storageError ||
                    load.loading ||
                    !!load.error ||
                    !load.data?.traffic.self_order_enabled ||
                    !quote.balance_sufficient
                  }
                  onClick={() =>
                    void run(prepare, "").catch((error) => {
                      setQuote(null)
                      setQuoteError(error.message)
                      syncIntent()
                    })
                  }
                >
                  {busy ? "正在核对…" : "核对并确认下单"}
                </Button>
              </div>
              <Button asChild variant={quote && !quote.balance_sufficient ? "default" : "ghost"} className="w-full"><Link to="/support?scene=recharge">联系客服充值</Link></Button>
              <Explanation title="余额与下单说明" returnLabel="返回确认下单">实际应付由当前服务、会员优惠、活动与优惠券共同核算，下单前请核对最新报价。余额不足时可联系客服充值。上次下单结果未确认时，请先核对原请求；不会自动重新下单。</Explanation>
            </Panel>
          </div>
        )}
      </State>
      {dialog}
    </>
  )
}
