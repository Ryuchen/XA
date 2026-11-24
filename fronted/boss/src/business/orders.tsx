import { coinText, coinUnit, useCurrentStore, PrivateAudio, NegotiationProposalPanel, OrderJourney, OrderSource, OrderSyncBar } from "@xa/shared"
import { formatProviderName, ProviderName } from "./provider-name"
import { RoomCompletionPanel } from "./room-completion"
import {
  bossOrderPresentation,
  orderBusinessDetails,
  bossOrderStatusText,
  afterSaleHistoryKey,
  schedulePendingOrderRefresh,
} from "@xa/shared"
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"
import { ChevronRight, Gift, Copy, UsersRound, Clock3, Gamepad2, Headphones, ShieldCheck, Star, MessageSquareText, WalletCards, Heart, ReceiptText } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import {
  api,
  Choice,
  date,
  Field,
  Info,
  label,
  money,
  Panel,
  queryUrl,
  readIntent,
  requestId,
  rows,
  State,
  StatusBadge,
  BackLink,
  storeIntent,
  Title,
  useAction,
  useConfirm,
  useLoad,
  useQuery,
  type Row,
} from "./ui"

const orderStates = [
  { value: "", label: "全部" },
  { value: "pending", label: "待接单" },
  { value: "grabbed", label: "已接单" },
  { value: "in_progress", label: "服务中" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
]
function isUnconfirmed(order: Row) {
  return bossOrderPresentation(order).isUnconfirmed
}
function statusText(order: Row) {
  return bossOrderStatusText(order, label(order.status))
}
function providerNames(order: Row, prefix: unknown) {
  if (isUnconfirmed(order)) return "付款状态待确认"
  return (
    rows(order.participants)
      .map((item) => formatProviderName(item.nickname, prefix))
      .join("、") ||
    formatProviderName(order.provider_name_snapshot || order.provider?.nickname, prefix) ||
    (order.status === "CANCELLED"
      ? "未安排陪玩"
      : order.dispatch_route === "STAFF"
        ? "等待客服指定陪玩"
        : "等待陪玩接单")
  )
}
export function OrdersPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    [status, setStatus] = useState(query.get("status") || ""),
    [giftsOnly, setGiftsOnly] = useState(query.get("type") === "gift"),
    load = useLoad(
      () =>
        api.get<any>(queryUrl("/orders/orders/", { status: status || "all" })),
      [status]
    )
  const visible = rows(load.data).filter(
    (item) =>
      !giftsOnly ||
      !!item.source_order ||
      (item.is_gift_snapshot ?? item.service?.is_gift)
  )
  const hasFilters = Boolean(status || giftsOnly)
  useEffect(
    () => schedulePendingOrderRefresh("boss", visible, load.reload),
    [load.data, giftsOnly, status, load.reload]
  )
  return (
    <>
      <Title title="我的订单" description="查看接单、履约、评价与退款进度。" support />
      <OrderSyncBar loading={load.loading} error={load.error} snapshot={load.data} reload={load.reload} />
      <div className="boss-tabs boss-order-filters" role="group" aria-label="订单状态筛选">{orderStates.map(item => <Button key={item.value} variant={item.value === status ? "secondary" : "ghost"} aria-pressed={item.value === status} onClick={() => setStatus(item.value)}>{item.label}</Button>)}</div>
      <div className="boss-order-filter-summary"><Button variant={giftsOnly ? "secondary" : "outline"} className="boss-choice-button" aria-pressed={giftsOnly} onClick={() => setGiftsOnly(!giftsOnly)}><Gift size={16} />只看礼物单{giftsOnly && " ✓"}</Button>{!load.loading && !load.error && <span>{visible.length} 笔订单</span>}</div>
      <State
        {...load}
        empty={!visible.length}
        emptyTitle={hasFilters ? "没有符合当前条件的订单" : "还没有订单"}
        emptyDescription={hasFilters ? "清除筛选可查看全部订单。" : "选择服务并下单后，可以在这里跟进接单和履约进度。"}
        emptyAction={hasFilters && <Button variant="outline" onClick={() => {
          setStatus("")
          setGiftsOnly(false)
        }}>清除筛选</Button>}
      >
        <div className="boss-orders-list">{visible.map(order => <article className="boss-order-card" key={order.id}>
          <div className="boss-order-card-number"><span>{(order.entity_no || order.order_no)}</span><StatusBadge status={isUnconfirmed(order) ? "UNPAID" : order.status}>{statusText(order)}</StatusBadge></div>
          <div className="boss-order-card-service"><div><p>{order.game_category_name || order.service?.game_category_name}</p><h2><Link className="inline-flex min-h-11 items-center" to={queryUrl("/order-detail", { id: order.id, status, type: giftsOnly ? "gift" : "" })}>{order.service_name_snapshot || order.service?.name || "服务订单"}</Link></h2></div><div><p>{isUnconfirmed(order) ? "待核实金额" : "实付"}</p><strong>{money(order.amount)}<small>{coinUnit()}</small></strong><p>{label(order.payment_status)}</p></div></div>
          <OrderSource order={order} />
          <div className="boss-order-card-meta"><div><span>陪玩安排</span><strong>{providerNames(order, store.provider_name_prefix)}</strong></div><div><span>下单时间</span><strong>{date(order.created_at)}</strong></div></div>
          {order.aftersale_status === "OPEN" && !isUnconfirmed(order) && <p className="boss-order-card-note">售后处理中</p>}
          {order.refund_summary && !isUnconfirmed(order) && <p className="boss-order-card-refund">已退 {money(bossOrderPresentation(order).refundAmount)} {coinText(" 浪花")}</p>}
          <div className="boss-order-card-footer"><p>{!isUnconfirmed(order) ? order.fulfillment : label(order.payment_status)}</p><div className="boss-actions"><Link to={queryUrl("/order-detail", { id: order.id, status, type: giftsOnly ? "gift" : "" })}>查看详情<ChevronRight size={16} /></Link>{(order.allowed_actions || []).includes("SELECT_PROVIDER") && !isUnconfirmed(order) && <Button asChild><Link to={queryUrl("/order-detail", { id: order.id, status, type: giftsOnly ? "gift" : "" })}>查看报名</Link></Button>}</div></div>
        </article>)}</div>
      </State>
    </>
  )
}

export function OrderPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    id = query.get("id"),
    [reason, setReason] = useState(""),
    [aftersaleProvider, setAftersaleProvider] = useState("ALL"),
    [cancelReason, setCancelReason] = useState(""),
    [orderDialog, setOrderDialog] = useState<"cancel" | "aftersale" | null>(null),
    [score, setScore] = useState(""),
    [dimensions, setDimensions] = useState<Row>({
      skill_score: "",
      attitude_score: "",
      communication_score: "",
    }),
    [content, setContent] = useState(""),
    [anonymous, setAnonymous] = useState(false),
    [target, setTarget] = useState(""),
    [drafts, setDrafts] = useState<Record<string, Row>>({}),
    { busy, run, error: actionError, clearError } = useAction(),
    { confirm, dialog } = useConfirm()
  const load = useLoad(async () => {
    if (!id) throw new Error("缺少订单编号，请从订单列表重新进入")
    const order = await api.get<any>(`/orders/orders/${id}/`)
    let aftersale = null,
      aftersaleError = ""
    if (order.aftersale_status)
      try {
        aftersale = await api.get<any>(`/orders/orders/${id}/aftersale/`)
      } catch (error: any) {
        aftersaleError = error.message
      }
    return { order, aftersale, aftersaleError }
  }, [id])
  const order = load.data?.order,
    details = orderBusinessDetails(order || {}, "boss"),
    giftOrder = !!(order?.is_gift_snapshot ?? order?.service?.is_gift),
    can = (action: string) =>
      !!order &&
      !isUnconfirmed(order) &&
      (order.allowed_actions || []).includes(action),
    targets = rows(order?.evaluation_targets),
    confirmedTerminationIds = Array.isArray(load.data?.aftersale?.confirmed_termination_provider_ids)
      ? load.data.aftersale.confirmed_termination_provider_ids.map(Number) : [],
    aftersaleTargets = targets.filter(item => item.provider_account_id &&
      !confirmedTerminationIds.includes(Number(item.provider_account_id))),
    needsAftersaleScopeReview = order?.status === "COMPLETED" && !!order?.aftersale_status &&
      (!Array.isArray(load.data?.aftersale?.confirmed_termination_provider_ids) || !!load.data?.aftersaleError),
    targetKey = (item: Row) =>
      item.provider_account_id
        ? "a:" + item.provider_account_id
        : "u:" + item.provider_legacy_id
  useEffect(
    () =>
      schedulePendingOrderRefresh("boss", order ? [order] : [], load.reload),
    [order, id, load.reload]
  )
  useEffect(() => {
    if (
      targets.length &&
      !targets.some((item) => targetKey(item) === target && !item.reviewed)
    )
      setTarget(
        targets.find((item) => !item.reviewed)
          ? targetKey(targets.find((item) => !item.reviewed)!)
          : ""
      )
  }, [order])
  useEffect(() => {
    const draft = drafts[target]
    setScore(String(draft?.score || ""))
    setContent(draft?.content || "")
    setAnonymous(!!draft?.anonymous)
    setDimensions({
      skill_score: draft?.skill_score || "",
      attitude_score: draft?.attitude_score || "",
      communication_score: draft?.communication_score || "",
    })
  }, [target])
  const editEvaluation = (patch: Row) => {
    const draft = { score, content, anonymous, ...dimensions, ...patch }
    if (target) setDrafts((value) => ({ ...value, [target]: draft }))
    if (patch.score != null) setScore(patch.score)
    if (patch.content != null) setContent(patch.content)
    if (patch.anonymous != null) setAnonymous(patch.anonymous)
    if (
      ["skill_score", "attitude_score", "communication_score"].some(
        (key) => key in patch
      )
    )
      setDimensions((value) => ({ ...value, ...patch }))
  }
  async function mutation(path: string, payload: Row = {}) {
    try {
      await api.post(path, payload)
    } catch (error) {
      load.reload()
      throw error
    }
    load.reload()
  }
  function openOrderDialog(kind: "cancel" | "aftersale") {
    clearError()
    if (kind === "aftersale" && confirmedTerminationIds.length && aftersaleTargets.length)
      setAftersaleProvider(String(aftersaleTargets[0].provider_account_id))
    setOrderDialog(kind)
  }
  function cancel() {
    if (!order || !can("CANCEL") || busy || load.loading || load.error) return
    void run(async () => {
      await mutation(`/orders/orders/${id}/cancel/`, { reason: cancelReason.trim() })
      setOrderDialog(null)
    }, "订单已取消").catch(() => {})
  }
  function applyAftersale() {
    if (!order) return
    const pending = readIntent("aftersale-" + id)
    if (pending?.blocked) {
      toast.error("原售后申请凭证无法读取，请联系客服核对。")
      return
    }
    if (!pending && (needsAftersaleScopeReview || (confirmedTerminationIds.length > 0 &&
        !aftersaleTargets.some(item => String(item.provider_account_id) === aftersaleProvider)))) {
      toast.error("售后范围暂无法确认，请刷新订单或联系客服。")
      return
    }
    const original = pending || {
      id: requestId("aftersale"),
      path: `/orders/orders/${id}/aftersale/`,
      payload: { reason: reason.trim(),
        affected_provider_account_ids: order.aftersale_scope_selection_available === true && aftersaleProvider !== "ALL"
          ? [Number(aftersaleProvider)] : null },
    }
    if (typeof original.payload.reason !== "string" || Array.from(original.payload.reason).length < 2) {
      toast.error("请填写至少 2 字问题说明")
      return
    }
    if (busy || load.loading || load.error) return
    void run(async () => {
      storeIntent("aftersale-" + id, original)
      try {
        await api.post(original.path, {
          ...original.payload,
          client_request_id: original.id,
        })
        storeIntent("aftersale-" + id, null)
        setReason("")
        setAftersaleProvider("ALL")
        setOrderDialog(null)
      } catch (error: any) {
        if (["AFTERSALE_ALREADY_OPEN", "AFTERSALE_REQUEST_CONFLICT", "AFTERSALE_REQUEST_PREVIOUS"].includes(error.errorCode))
          storeIntent("aftersale-" + id, null)
        throw error
      } finally {
        load.reload()
      }
    }, "售后问题已提交").catch(() => {})
  }
  async function evaluate() {
    const selected = targets.find(
      (item) => targetKey(item) === target && !item.reviewed
    )
    if (targets.length && !selected) throw new Error("请选择尚未评价的陪玩")
    if (!score || ["skill_score", "attitude_score", "communication_score"].some(key => !dimensions[key]))
      throw new Error("请选择综合、技术、态度和沟通四项评分")
    const identity = selected
      ? selected.provider_account_id
        ? { provider_account_id: selected.provider_account_id }
        : { provider_legacy_id: selected.provider_legacy_id }
      : {}
    await mutation("/orders/evaluate/", {
      order_id: Number(id),
      ...identity,
      score: Number(score),
      ...Object.fromEntries(
        Object.entries(dimensions)
          .filter(([, value]) => value !== "")
          .map(([key, value]) => [key, Number(value)])
      ),
      content: content.trim(),
      is_anonymous: anonymous,
    })
    setContent("")
    setScore("")
    setAnonymous(false)
    setDimensions({
      skill_score: "",
      attitude_score: "",
      communication_score: "",
    })
    setDrafts((value) => {
      const next = { ...value }
      delete next[target]
      return next
    })
  }
  const aftersaleIntent = readIntent("aftersale-" + id)
  const canAftersale =
    order &&
    !isUnconfirmed(order) &&
    (!!aftersaleIntent || (order.can_aftersale === true &&
      ["IN_SERVICE", "COMPLETED"].includes(order.status) &&
      ["PAID", "PARTIALLY_REFUNDED"].includes(order.payment_status) &&
      order.aftersale_status !== "REFUNDED"))
  return (
    <>
      <BackLink to={queryUrl("/orders", { status: query.get("status"), type: query.get("type") })}>我的订单</BackLink>
      <OrderSyncBar loading={load.loading} error={load.error} snapshot={order} reload={load.reload} />
      <Title
        title="订单详情"
        description="核对服务进度、费用明细与可执行操作。"
      />
      <State {...load}>
        {order && (
          <div className="boss-order-detail">
            <Panel className="boss-order-summary-panel">
              <section className={"boss-order-status-hero" + (isUnconfirmed(order) ? " payment-unknown" : order.status !== "CANCELLED" ? " dark-summary" : "")} aria-label="订单金额与状态摘要">
                <div className="boss-order-status-main"><div className="boss-order-status-label"><span>{isUnconfirmed(order) ? "待核对金额" : "本单实付"}</span><Badge variant="outline">{statusText(order)}</Badge></div><p className="boss-order-status-amount">{money(order.amount)}<small>{coinUnit()}</small></p><p className="boss-order-status-progress">{isUnconfirmed(order) ? "付款情况待确认，请核对钱包流水或联系客服。" : order.fulfillment}</p>{order.pending_deadline_expired && !isUnconfirmed(order) && <p className="boss-order-status-deadline">已到接单期限，等待平台处理。长时间未更新请联系客服。</p>}</div>
                <div className="boss-order-status-meta"><div><div><p>订单编号</p><strong>{(order.entity_no || order.order_no)}</strong></div><Button variant="ghost" onClick={() => void navigator.clipboard.writeText((order.entity_no || order.order_no)).then(() => toast.success("订单编号已复制")).catch(() => toast.error("复制失败，请长按订单编号复制"))}><Copy size={16} />复制</Button></div><p><span>下单时间</span><time>{date(order.created_at)}</time></p></div>
              </section>
            </Panel>
            <OrderJourney order={order} audience="boss" />
            <div className="boss-order-detail-columns">
              <div className="boss-order-detail-main">
                {order.room_required_snapshot === true && <RoomCompletionPanel key={order.id} orderId={order.id} status={order.status} onSaved={load.reload} />}
            {can("SELECT_PROVIDER") && !order.pending_deadline_expired && (
              <Panel icon={UsersRound} title="挑选接单陪玩" className="boss-order-provider-selection">
                <p className="boss-muted">
                  按本单已付总金额服务，组队报名需等待队友确认。
                </p>
                {rows(order.applications).map((item) => (
                  <div className="boss-list-row boss-order-applicant" key={item.id}>
                    <div className="boss-order-applicant-identity"><div className="boss-order-applicant-avatars" aria-hidden="true">{rows(item.members).map((member, index) => <span key={member.id || member.provider_id || index}>{String(member.nickname || "陪玩").slice(0, 1)}</span>)}</div><div><div className="boss-order-applicant-heading"><strong>{rows(item.members).map(member => formatProviderName(member.nickname, store.provider_name_prefix)).filter(Boolean).join(" + ")}</strong><Badge variant="outline" className={item.ready ? "ready" : "not-ready"}>{item.ready ? "可选择" : "暂不可选择"}</Badge></div><p className="boss-muted">{rows(item.members).map(member => member.level).filter(Boolean).join(" / ")}{item.team_size != null && ` · ${item.team_size}人服务`}</p></div></div>
                    {rows(item.members).map(member => member.recording?.audio_url && <div key={member.id} className="boss-order-audition-audio"><span>{formatProviderName(member.nickname, store.provider_name_prefix)} · {member.recording.title}</span><PrivateAudio role="boss" src={member.recording.audio_url} label={`试听${formatProviderName(member.nickname, store.provider_name_prefix)}的报名录音`} /></div>)}
                    {!item.ready && <p className="boss-order-applicant-reason">{item.not_ready_reason || "等待队友确认"}</p>}
                    <Button
                      variant="outline"
                      className="right"
                      disabled={
                        !item.ready || busy || load.loading || !!load.error
                      }
                      onClick={() =>
                        confirm({
                          title: `选择 ${item.team_size} 名陪玩`,
                          description:
                            rows(item.members)
                              .map((member) => formatProviderName(member.nickname, store.provider_name_prefix))
                              .join("、") +
                            `，按本单实付 ${money(order.amount)} ${coinUnit()}提供服务。`,
                          confirm: "确认选择",
                          action: () =>
                            mutation(
                              `/orders/orders/${id}/applications/${item.id}/select/`
                            ),
                        })
                      }
                    >
                      选择本组
                    </Button>
                  </div>
                ))}
                {!rows(order.applications).length && (
                  <p>正在等待陪玩报名，可稍后刷新。</p>
                )}
              </Panel>
            )}
                <Panel icon={Clock3} title={giftOrder ? "赠礼记录" : "服务进度"}>
                  <ol className="boss-timeline">
                    {(isUnconfirmed(order) ? [["订单已创建", order.created_at]] : giftOrder ? [
                      [order.status === "COMPLETED" ? "礼物已送达" : "礼物订单已创建", order.completed_at || order.created_at],
                      ["订单已取消", order.cancelled_at],
                      ["退款入账", order.refunded_at],
                    ] : [
                      ["下单成功", order.created_at],
                      ...(!isUnconfirmed(order)
                        ? [
                            ["陪玩已接单", order.grabbed_at],
                            ["服务已开始", order.in_service_at],
                            ["服务已完成", order.completed_at],
                            ["订单已取消", order.cancelled_at],
                            ["退款入账", order.refunded_at],
                          ]
                        : []),
                    ])
                      .filter(([, value]) => value)
                      .map(([name, value]) => (
                        <li key={name}>
                          <span>{name}</span>
                          <time>{date(value)}</time>
                        </li>
                      ))}
                  </ol>
                </Panel>
                {bossOrderPresentation(order).teamProgressNeedsReview && (
                  <Panel icon={Headphones} title="双陪履约进度">
                    <p>
                      历史双陪订单尚无逐人凭证进度，请联系客服核对后继续履约。
                    </p>
                  </Panel>
                )}
                {bossOrderPresentation(order).zeroPaymentCancelled && (
                  <p className="boss-muted">实付为 0，无需退回余额。</p>
                )}
                {order.team_progress && !isUnconfirmed(order) && (
                  <Panel icon={Headphones} title="双陪履约进度">
                    <p>
                      {order.team_progress.stage === "ENTRY" ? "入队" : "结单"}
                      截图：{order.team_progress.submitted_count} /{" "}
                      {order.team_progress.required_count}
                    </p>
                    {rows(order.team_progress.members).map((item) => (
                      <div
                        className="boss-list-row"
                        key={item.id || item.provider_id}
                      >
                        <div>
                          <strong><ProviderName value={item.nickname || item.provider_name} /></strong>
                          <p className="boss-muted">
                            {item.submitted ? "已提交凭证" : "等待提交"}
                          </p>
                        </div>
                      </div>
                    ))}
                  </Panel>
                )}
                {can("EVALUATE") && (
                  <Panel icon={Star} title="评价本次服务">
                    <form
                      className="boss-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void run(evaluate, "评价已提交").catch(() =>
                          load.reload()
                        )
                      }}
                    >
                      {targets.length > 0 && <div><p className="boss-order-evaluation-label">选择评价对象</p><div className="boss-order-evaluation-targets">{targets.map(item => <Button type="button" key={targetKey(item)} variant={targetKey(item) === target && !item.reviewed ? "default" : "outline"} disabled={item.reviewed || busy} aria-pressed={targetKey(item) === target && !item.reviewed} onClick={() => setTarget(targetKey(item))}>{item.name || item.provider_name || item.nickname ? <ProviderName value={item.name || item.provider_name || item.nickname} /> : "本次陪玩"}{item.reviewed && " · 已评价"}</Button>)}</div></div>}
                      <Choice label="综合评分" value={score} disabled={busy}
                        onChange={value => editEvaluation({ score: value })}
                        options={[{ value: "", label: "请选择" }, ...[5, 4, 3, 2, 1].map(value => ({ value, label: value + " 分" }))]} />
                      <Field
                        label="评价内容"
                        value={content}
                        onChange={(value) => editEvaluation({ content: value })}
                        multiline
                        maxLength={500}
                        disabled={busy}
                        placeholder="分享你的真实体验（选填）"
                      />
                      <div className="boss-form-grid">
                        {[
                          ["skill_score", "技术表现"],
                          ["attitude_score", "服务态度"],
                          ["communication_score", "沟通体验"],
                        ].map(([key, title]) => (
                          <Choice
                            key={key}
                            label={title}
                            value={dimensions[key]}
                            disabled={busy}
                            onChange={(value) =>
                              editEvaluation({ [key]: value })
                            }
                            options={[
                              { value: "", label: "请选择" },
                              ...[5, 4, 3, 2, 1].map((value) => ({
                                value,
                                label: value + " 分",
                              })),
                            ]}
                          />
                        ))}
                      </div>
                      <label className="boss-order-evaluation-anonymous" htmlFor="boss-order-anonymous">匿名评价<Switch id="boss-order-anonymous" checked={anonymous} disabled={busy} onCheckedChange={value => editEvaluation({ anonymous: value })} /></label>
                      <Button className="boss-order-evaluation-submit" aria-busy={busy} disabled={busy || load.loading || !!load.error}>
                        {busy ? "正在提交…" : "提交评价"}
                      </Button>
                    </form>
                  </Panel>
                )}
                {rows(order.evaluations).length > 0 && (
                  <Panel icon={MessageSquareText} title="我的评价">
                    {rows(order.evaluations).map((item) => (
                      <article key={item.id} className="boss-order-review-entry">
                          <header><p>{item.provider_name ? <ProviderName value={item.provider_name} /> : "本次陪玩"}</p><Badge variant="secondary">{item.score} 分</Badge></header>
                          <p className="boss-order-review-content">{item.content || "未填写文字评价"}</p>
                          <p className="boss-order-review-dimensions">
                            {[
                              ["skill_score", "技术"],
                              ["attitude_score", "态度"],
                              ["communication_score", "沟通"],
                            ]
                              .filter(([key]) => item[key] != null)
                              .map(([key, title]) => `${title} ${item[key]} 分`)
                              .join(" · ")}
                          </p>
                          {item.reply_content && (
                            <p className="boss-order-review-reply">
                              <span>{item.reply_author === "PROVIDER" ? "陪玩回复：" : item.reply_author ? "客服回复：" : "回复："}</span>{item.reply_content}
                            </p>
                          )}
                      </article>
                    ))}
                  </Panel>
                )}
              </div>
              <aside className="boss-order-detail-aside" aria-label="订单资料与售后">
                <Panel icon={ReceiptText} title="服务与费用">
                  <h3 className="boss-order-service-name">{order.service_name_snapshot || order.service?.name || "服务订单"}</h3>
                  {(order.game_category_name || order.service?.game_category_name) && <p className="boss-order-service-category">{order.game_category_name || order.service?.game_category_name}</p>}
                  <Info entries={[
                    ["购买数量", order.game_rounds == null ? undefined : order.game_rounds + " " + (order.quantity_unit || "份")],
                    ["原价", money(order.original_amount) + " " + coinUnit()],
                    ["会员优惠", giftOrder && Number(order.boss_discount) === 0 ? undefined : money(order.boss_discount) + " " + coinUnit()],
                    ["活动优惠", giftOrder && Number(order.promo_discount) === 0 ? undefined : money(order.promo_discount) + " " + coinUnit()],
                    ["优惠券抵扣", giftOrder && Number(order.coupon_discount) === 0 ? undefined : money(order.coupon_discount) + " " + coinUnit()],
                    [isUnconfirmed(order) ? "待核对金额" : "本单实付", money(order.amount) + " " + coinUnit()],
                    ["支付状态", label(order.payment_status)],
                    ["接单截止", !isUnconfirmed(order) && order.status === "PENDING" ? date(order.auto_cancel_at) : undefined],
                    [details.cancelReasonLabel, details.cancelReason],
                  ]} />
                  {giftOrder && order.remark && <div className="boss-order-detail-note"><p>赠礼备注</p><div>{order.remark}</div></div>}
                  {order.roulette_result?.name && <div className="boss-order-detail-note"><strong>本次抽签结果：{order.roulette_result.name}</strong><p>{order.roulette_result.description}</p></div>}
                  {order.source_order && <Button asChild variant="outline" className="boss-order-section-action"><Link to={"/order-detail?id=" + order.source_order}>查看关联服务订单</Link></Button>}
                  {order.reservation_origin && <div className="boss-order-detail-note"><p>原预约时段：{date(details.reservation?.startTime)} 至 {date(details.reservation?.endTime)}</p><Button asChild variant="outline" className="boss-order-section-action"><Link to={details.reservation!.destination}>查看原预约</Link></Button></div>}
                </Panel>
                <Panel icon={Headphones} title={giftOrder ? "赠礼对象" : "陪玩安排"}>
                  <Info entries={[[giftOrder ? "赠礼对象" : "陪玩", providerNames(order, store.provider_name_prefix)]]} />
                </Panel>
                {!giftOrder && <Panel icon={Gamepad2} title="游戏信息">
                  <Info entries={[["游戏大区", details.gameRegion], ["游戏昵称", details.gameNickname], ["游戏 UID", details.gameUid]]} />
                  {order.remark && <div className="boss-order-detail-note"><p>服务备注</p><div>{order.remark}</div></div>}
                </Panel>}
                {!isUnconfirmed(order) && (
                  <Panel icon={ShieldCheck} title="售后与实销进度">
                    <Info
                      entries={[
                        [
                          "售后状态",
                          order.aftersale_status
                            ? label(order.aftersale_status)
                            : "无进行中售后",
                        ],
                        [
                          "实销确认",
                          order.consumption_confirmed_at
                            ? date(order.consumption_confirmed_at)
                            : "尚未确认",
                        ],
                        [
                          "计时节点",
                          order.consumption_ready_at
                            ? date(order.consumption_ready_at)
                            : undefined,
                        ],
                        ["售后范围", load.data?.aftersale ? (load.data.aftersale.affected_provider_account_ids?.length
                          ? (load.data.aftersale.affected_provider_names || []).map((name: string) => formatProviderName(name, store.provider_name_prefix)).join("、") || "陪玩信息待核对" : "整单") : undefined],
                        ["售后申请", load.data?.aftersale?.reason],
                        ["处理说明", load.data?.aftersale?.resolution],
                      ]}
                    />
                    {order.aftersale_status === "OPEN" && (
                      <p className="boss-muted">
                        售后处理中，请等待平台处理结果。
                      </p>
                    )}
                    {order.aftersale_status === "OPEN" && ["IN_SERVICE", "COMPLETED"].includes(order.status) &&
                      <NegotiationProposalPanel role="boss" orderId={order.id}
                        names={Object.fromEntries((load.data?.aftersale?.affected_provider_account_ids || []).map((id: number, index: number) =>
                          [Number(id), <ProviderName value={load.data?.aftersale?.affected_provider_names?.[index] || "陪玩"} />]))} />}
                    {rows(load.data?.aftersale?.history).map((item, index) => (
                      <div
                        className="boss-list-row"
                        key={afterSaleHistoryKey(item, index)}
                      >
                        <div>
                          <strong>
                            {label(item.status)} ·{" "}
                            {date(item.resolved_at || item.created_at)}
                          </strong>
                          <p>{item.reason}</p>
                          <p className="boss-muted">{item.resolution}</p>
                        </div>
                      </div>
                    ))}
                    {load.data?.aftersaleError && (
                      <div className="boss-feedback error">
                        <p>{load.data.aftersaleError}</p>
                        <Button variant="outline" onClick={load.reload}>
                          重新读取售后详情
                        </Button>
                      </div>
                    )}
                    {canAftersale && <Button variant="outline" className="boss-order-section-action" onClick={() => openOrderDialog("aftersale")} disabled={busy || load.loading || !!load.error}>提交或核对售后申请</Button>}
                  </Panel>
                )}
                {!isUnconfirmed(order) && order.refund_summary && (
                  <Panel icon={WalletCards} title="退款记录">
                    <Info
                      entries={[
                        [
                          "累计退款",
                          money(order.refund_summary.amount) + coinText(" 浪花"),
                        ],
                        [
                          "退款后保留金额",
                          money(bossOrderPresentation(order).refundRemaining) +
                            coinText(" 浪花"),
                        ],
                      ]}
                    />
                    {bossOrderPresentation(order).refundRecords.map((item) => (
                      <div key={item.id} className="boss-list-row">
                        <div>
                          <strong>{money(item.amount)} {coinText(" 浪花")}</strong>
                          <p>{item.reason}</p>
                          <small className="boss-muted">
                            {date(item.created_at)}
                          </small>
                          {rows(item.coupon_results).map((coupon) => (
                            <p key={coupon.id || coupon.name}>
                              {coupon.name || "优惠券"}：{coupon.label}
                            </p>
                          ))}
                          {rows(item.gift_claim_results).map((claim) => (
                            <p key={claim.id}>
                              <Link to={"/reward-claim?id=" + claim.id}>
                                {claim.name || "关联权益"}：
                                {claim.outcome === "CANCELLED"
                                  ? "未交付，已撤销"
                                  : "已交付，保留记录"}
                              </Link>
                            </p>
                          ))}
                          {item.gift_rights_preserved && (
                            <p className="boss-muted">如已获得赠礼权益，将继续保留；未交付权益继续交付</p>
                          )}
                          {item.safety_box_replenishment && (
                            <p className="boss-muted">
                              {item.safety_box_replenishment ===
                              "WAITING_SETTLEMENT"
                                ? "报酬结算后，按会员权益发放下一张券"
                                : item.safety_box_replenishment === "SETTLED"
                                  ? "会员权益请在我的券查看"
                                  : ""}
                            </p>
                          )}
                          {item.gift_naming_result && (
                            <p className="boss-muted">
                              本次处理后冠名截止：
                              {date(item.gift_naming_result.expires_at)}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                    <Button asChild variant="outline">
                      <Link to="/wallet?tx_type=REFUND">查看钱包流水</Link>
                    </Button>
                  </Panel>
                )}
                {can("TIP") && (
                  <Panel icon={Heart} title="把欣赏送给 TA">
                    <p className="boss-muted">
                      选择本次订单的陪玩，送出一份心意。
                    </p>
                    <Button asChild variant="outline" className="boss-order-section-action">
                      <Link to={"/gifts?order_id=" + id}><Gift size={16} />挑选礼物</Link>
                    </Button>
                  </Panel>
                )}
              </aside>
            </div>
            <div className="boss-order-detail-actions">
              <div><p>当前状态</p><strong>{statusText(order)}</strong></div>
              <div className="boss-order-detail-action-buttons">
                {can("CANCEL") && <Button type="button" variant="outline" className="boss-order-cancel" disabled={busy || load.loading || !!load.error} onClick={() => openOrderDialog("cancel")}>取消订单</Button>}
                {!isUnconfirmed(order) && order.refund_summary && <Button asChild variant="outline"><Link to="/wallet?tx_type=REFUND">退款流水</Link></Button>}
                <Button asChild><Link to={"/support?order_id=" + id}><MessageSquareText size={16} />联系订单客服</Link></Button>
              </div>
            </div>
          </div>
        )}
      </State>
      <Dialog open={!!orderDialog} onOpenChange={open => { if (!busy && !open) setOrderDialog(null) }}>
        <DialogContent className={"boss-order-form-dialog " + (orderDialog === "cancel" ? "boss-order-cancel-dialog" : "boss-order-aftersale-dialog")} closeDisabled={busy}>
          <DialogHeader>
            <DialogTitle>{orderDialog === "cancel" ? "确认取消订单？" : aftersaleIntent ? "核对原售后申请" : "提交订单售后"}</DialogTitle>
            <DialogDescription>{orderDialog === "cancel" ? <>本单实付 {money(order?.amount)} {coinUnit()}。{order?.points_payment?.state === "CONFIRMED" ? `另使用 ${order.points_payment.points_hundredths / 100} 积分抵扣 ${money(order.points_payment.discount_units)} ${coinUnit()}。符合取消条件时，店币和积分将分别退回。` : order?.points_payment?.state === "REVIEW" ? "积分付款信息待核对，请联系订单客服。" : bossOrderPresentation(order || {}).zeroPayment ? "实付为 0，无需退回余额。" : "符合取消条件时，已付金额将退回余额。"}{order?.cancel_coupon_nonrefundable ? "本单已核销的券取消后不返还。" : ""}</> : "请说明遇到的问题，提交后可在订单中查看处理进度。"}</DialogDescription>
          </DialogHeader>
          {orderDialog === "cancel" ? <Field label="取消原因（选填）" value={cancelReason} onChange={setCancelReason} multiline maxLength={255} disabled={busy} placeholder="可填写取消原因" /> : <>
            <p className="boss-muted">{order?.entity_no || order?.order_no}</p>
            {confirmedTerminationIds.length > 0 && <p className="boss-muted">{aftersaleTargets.length
              ? "已确认终止的陪玩不能再次申请售后，请选择其他陪玩。"
              : "当前没有可选择的陪玩，请联系客服核对。"}</p>}
            {needsAftersaleScopeReview && !aftersaleIntent && <p role="alert" className="text-destructive">售后范围暂无法核对，请刷新订单或联系客服。</p>}
            {order?.aftersale_scope_selection_available === true && (confirmedTerminationIds.length > 0 || targets.filter(item => item.provider_account_id).length > 1) && aftersaleTargets.length > 0 && <Choice
              label="涉及的陪玩"
              value={aftersaleIntent ? String(aftersaleIntent.payload.affected_provider_account_ids?.[0] ?? "ALL") : aftersaleProvider}
              onChange={setAftersaleProvider}
              options={[...(confirmedTerminationIds.length ? [] : [{ value: "ALL", label: "整单或无法确定" }]), ...aftersaleTargets
                .map(item => ({ value: String(item.provider_account_id), label: formatProviderName(item.name, store.provider_name_prefix) || "陪玩" }))]}
              disabled={busy || !!aftersaleIntent}
            />}
            <Field label="问题说明" value={aftersaleIntent?.payload.reason ?? reason} onChange={setReason} multiline maxLength={1000} disabled={busy || !!aftersaleIntent} placeholder="请描述至少 2 字的问题，便于客服核对" />
            {aftersaleIntent && <p role={aftersaleIntent.blocked ? "alert" : undefined} className={aftersaleIntent.blocked ? "text-destructive" : "boss-muted"}>{aftersaleIntent.blocked ? "原售后申请凭证无法读取，请联系客服核对。" : "原申请结果尚待核对，将使用原请求再次查询，避免重复提交。"}</p>}
          </>}
          {actionError && <p role="alert" className="text-destructive">{actionError}</p>}
          {(load.loading || load.error) && <p role="status" className="boss-muted">{load.error ? "订单状态读取失败，请关闭弹窗后刷新状态。" : "正在核对最新订单状态…"}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={() => setOrderDialog(null)}>{orderDialog === "cancel" ? "保留订单" : "取消"}</Button>
            <Button type="button" variant={orderDialog === "cancel" ? "destructive" : "default"} disabled={busy || load.loading || !!load.error || (orderDialog === "cancel" ? !can("CANCEL") || order?.points_payment?.state === "REVIEW" : !canAftersale || !!aftersaleIntent?.blocked || (!aftersaleIntent && (needsAftersaleScopeReview || (confirmedTerminationIds.length > 0 && aftersaleTargets.length === 0))))} aria-busy={busy} onClick={orderDialog === "cancel" ? cancel : applyAftersale}>{busy ? "正在提交…" : orderDialog === "cancel" ? "确认取消" : aftersaleIntent ? "核对原申请" : "提交问题"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {dialog}
    </>
  )
}
