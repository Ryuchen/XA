import { useEffect, useId, useRef, useState } from "react"
import { Link } from "react-router"
import { coinUnit, getStoreSlug } from "@xa/shared"
import { errorMessage, request, requestId, type PageResult } from "@/api/request"
import { Feedback, Status, useLoad, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin } from "@/lib/format"
import { checkedAddonCase, parseAddonIntent, returnedAddonScope, submitAddonCase, type AddonCaseIntent } from "@/lib/consultation-addon"

export default function ConsultationAddonCase({ sessionId, messageId, orderId }: { sessionId: number; messageId: number; orderId: number }) {
  const auth = useAdminAuth()
  const key = `xa:store:${getStoreSlug()}:admin:addon-case:${auth.profile.id}:${sessionId}:${messageId}`
  const [restored] = useState(() => {
    try { return { intent: parseAddonIntent(sessionStorage.getItem(key), sessionId, messageId, orderId), error: "" } }
    catch (cause) { return { intent: null, error: errorMessage(cause) } }
  })
  const [pending, setPending] = useState<AddonCaseIntent | null>(restored.intent)
  const [open, setOpen] = useState(false)
  const [payments, setPayments] = useState<Row[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [paymentId, setPaymentId] = useState("")
  const [selectedScope, setSelectedScope] = useState(false)
  const [reason, setReason] = useState("")
  const [error, setError] = useState(restored.error)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const generation = useRef(0)
  const [receipt, setReceipt] = useState<Row | null>(null)
  const formId = useId()
  const payment = payments.find(item => String(item.payment_id) === paymentId)
  const handovers = useLoad<Row[] | null>(async () => paymentId && open && !pending
    ? (await request<Row[]>({ url: `/orders/${orderId}/addons/payments/${paymentId}/handovers/` })).data : null, [paymentId, open, !!pending])
  let scope: ReturnType<typeof returnedAddonScope> | null = null
  let scopeError = ""
  if (payment && handovers.data && !handovers.loading && !handovers.error) {
    try { scope = returnedAddonScope(payment, handovers.data) }
    catch (cause) { scopeError = errorMessage(cause) }
  }
  useEffect(() => () => { ++generation.current }, [])

  async function readPayments(cursor?: number) {
    if (loading) return
    const run = ++generation.current
    setLoading(true)
    setError("")
    try {
      const data = (await request<{ items: Row[]; next_before_id: number | null }>({ url: `/orders/${orderId}/addons/intents/`, params: cursor ? { before_id: cursor } : {} })).data
      if (run !== generation.current) return
      if (!Array.isArray(data?.items) || (data.next_before_id !== null && (!Number.isSafeInteger(data.next_before_id) || data.next_before_id <= 0 || (cursor && data.next_before_id >= cursor)))) throw new Error("加购付款分页回执待核对。")
      const paid = data.items.filter(item => item.order_id === orderId && item.paid === true && Number.isSafeInteger(item.payment_id) && item.payment_id > 0)
      setPayments(old => cursor ? [...old, ...paid.filter(item => !old.some(existing => existing.payment_id === item.payment_id))] : paid)
      setBefore(data.next_before_id)
      setLoaded(true)
    } catch (cause) { if (run === generation.current) setError(errorMessage(cause)) }
    finally { if (run === generation.current) setLoading(false) }
  }

  function clearPending() { sessionStorage.removeItem(key); setPending(null) }
  function accept(value: Row, source: AddonCaseIntent) { setReceipt({ ...value, original: source.payload }); setError("") }
  async function submit() {
    if (inFlight.current || restored.error) return
    if (!pending && (!payment || !scope || !selectedScope || handovers.loading || handovers.error || reason.trim().length < 2)) {
      setError("请选择原付款和已确认转回段，并填写至少 2 字的咨询说明。")
      return
    }
    const intent: AddonCaseIntent = pending || { session_id: sessionId, payload: { order_id: orderId, chat_message_id: messageId,
      addon_payment_id: payment!.payment_id, affected_provider_account_ids: [scope!.provider], affected_segment_ids: [scope!.segment], reason: reason.trim(), client_request_id: requestId("addon-case") } }
    inFlight.current = true
    setBusy(true)
    setError("")
    try {
      const value = await submitAddonCase(intent, {
        recovering: !!pending,
        save: value => { try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { throw new Error("无法保存原申请，请检查浏览器存储后重试。") }; setPending(value) },
        clear: clearPending,
        send: async value => (await request<Row>({ url: "/aftersales/open-negotiation/", method: "POST", data: value.payload })).data,
      })
      accept(value, intent)
    } catch (cause) { setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause)) }
    finally { inFlight.current = false; setBusy(false) }
  }

  async function checkOriginal() {
    if (!pending || inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError("")
    try {
      let page = 1
      let seen = 0
      while (true) {
        const data = (await request<PageResult>({ url: "/aftersales/", params: { order: orderId, page, page_size: 100 } })).data
        if (!Array.isArray(data?.list) || !Number.isSafeInteger(data.total) || data.total < 0) throw new Error("工单列表回执待核对。")
        const found = data.list.find(row => Array.isArray(row.history) && row.history.some((entry: Row) => entry.client_request_id === pending.payload.client_request_id))
        if (found) { checkedAddonCase(pending, found); clearPending(); accept(found, pending); return }
        seen += data.list.length
        if (seen >= data.total || !data.list.length) break
        ++page
      }
      setError("当前未查到与原号、咨询及付款范围一致的工单，原申请仍保留，请稍后核对或按原请求重试。")
    } catch (cause) { setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause)) }
    finally { inFlight.current = false; setBusy(false) }
  }

  return <>
    <Button type="button" size="sm" variant="outline" onClick={() => { setOpen(true); if (!loaded && !pending && !restored.error) void readPayments() }}>{pending ? "继续核对加购售后" : "登记指定加购售后"}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-[720px]">
        <DialogHeader><DialogTitle id={`${formId}-title`}>从咨询登记指定加购售后</DialogTitle><DialogDescription>订单 #{orderId} · 咨询消息 #{messageId}。本次只登记工单，后续方案、双方确认和退款需另行处理。</DialogDescription></DialogHeader>
        <Feedback message={error} />
        {receipt ? <div role="status" className="space-y-3 text-sm">
          <p>已核对原申请记录，工单 #{receipt.id}，当前状态：<Status value={receipt.status} /></p>
          <p>原付款 #{receipt.original.addon_payment_id} · 陪玩账号 #{receipt.original.affected_provider_account_ids[0]} · 履约段 #{receipt.original.affected_segment_ids[0]}</p>
          <Button variant="outline" asChild><Link to={`/orders?detail=${orderId}`}>查看订单与原工单</Link></Button>
        </div> : <form aria-labelledby={`${formId}-title`} className="space-y-4" onSubmit={event => { event.preventDefault(); void submit() }}>
          {pending ? <div className="space-y-2 rounded-lg border p-3 text-sm">
            <p>原申请结果待核对，决定范围与请求号已保留。</p>
            <p>付款 #{pending.payload.addon_payment_id} · 陪玩账号 #{pending.payload.affected_provider_account_ids[0]} · 履约段 #{pending.payload.affected_segment_ids[0]}</p>
            <p className="whitespace-pre-wrap break-words">说明：{pending.payload.reason}</p>
            <p className="break-all text-xs text-muted-foreground">原请求号：{pending.payload.client_request_id}</p>
            <Button type="button" variant="outline" disabled={busy} onClick={() => void checkOriginal()}>核对原工单</Button>
          </div> : <>
            <div className="space-y-2">
              <Label htmlFor={`${formId}-payment`}>原加购付款（必选）</Label>
              <select id={`${formId}-payment`} name="addon_payment" required disabled={busy || loading || !!restored.error} className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={paymentId} onChange={event => { setPaymentId(event.target.value); setSelectedScope(false); setError("") }}>
                <option value="">请选择已付加购</option>
                {payments.map(item => <option key={item.payment_id} value={String(item.payment_id)}>{item.addon_name || "加购"} · 付款 #{item.payment_id} · 原履约段 #{item.segment_id} · {Number.isSafeInteger(item.amount_raw) ? `${amountToXaCoin(item.amount_raw)} ${coinUnit()}` : "金额待核对"}</option>)}
              </select>
              {loading && <p className="text-xs text-muted-foreground">正在读取付款…</p>}
              {loaded && !payments.length && <p className="text-sm text-muted-foreground">当前已读取记录没有已付加购。{before ? "可继续读取更早记录。" : "请返回订单核对。"}</p>}
              <div className="flex gap-2"><Button type="button" variant="ghost" size="sm" disabled={loading || busy} onClick={() => { setPaymentId(""); setSelectedScope(false); void readPayments() }}>重新读取付款</Button>{before && <Button type="button" variant="outline" size="sm" disabled={loading || busy} onClick={() => void readPayments(before)}>读取更早加购</Button>}</div>
            </div>
            {payment && <div className="space-y-2">
              {handovers.loading && <p className="text-sm">正在核对该付款的接替确认记录…</p>}
              <Feedback message={handovers.error} retry={handovers.reload} />
              <Feedback message={scopeError} />
              {scope && <label className="flex min-h-10 items-start gap-2 rounded-md border p-3 text-sm"><input type="checkbox" required name="affected_segment" className="mt-1 size-4" checked={selectedScope} disabled={busy} onChange={event => setSelectedScope(event.target.checked)} /><span>选择已确认转回段 #{scope.segment} · 陪玩账号 #{scope.provider} · 第 {scope.version} 版接替<span className="mt-1 block text-xs text-muted-foreground">这是接替确认快照，当前能否开案仍由服务端复核。</span></span></label>}
            </div>}
            <div className="space-y-2"><Label htmlFor={`${formId}-reason`}>咨询与登记说明（必填）</Label><p id={`${formId}-hint`} className="text-xs text-muted-foreground">说明本次指定付款的售后诉求，2–1000 字。</p><Textarea id={`${formId}-reason`} name="reason" required minLength={2} maxLength={1000} value={reason} disabled={busy || !!restored.error} aria-describedby={`${formId}-hint`} onChange={event => setReason(event.target.value)} /></div>
          </>}
          <DialogFooter><Button type="button" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>关闭{pending ? "并保留原请求" : ""}</Button><Button type="submit" disabled={busy || !!restored.error}>{busy ? "正在核对…" : pending ? "按原请求重试登记" : "登记售后申请"}</Button></DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>
  </>
}
