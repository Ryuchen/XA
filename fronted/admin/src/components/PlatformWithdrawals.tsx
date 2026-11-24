import { Status } from "@/components/business"
import { DataRegion, DataValue, ReadErrorNotice } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { PlatformRequestError, platformRequest, platformSession } from "@/lib/platform-session"
import {
  findWithdrawalReceipt, forgetWithdrawalIntent, readWithdrawalIntents, rememberWithdrawalIntent, sameWithdrawalIntent,
  withdrawalActionLabels, withdrawalContentMismatch, withdrawalFingerprint, withdrawalIntentKey,
  type WithdrawalAction, type WithdrawalAuditEvent, type WithdrawalIntent,
} from "@/lib/withdrawal-intent"
import { formatRmb, parseRmbFen, randomUuid } from "@xa/shared"

type StoreRow = { slug: string; name: string; status: string; counts: Record<string, number> | null; legacy_pending: number | null }
type LegacyLedger = { transaction_present: boolean; transaction_no?: string; transaction_status_display?: string;
  transaction_type?: string; transaction_amount?: number; wallet_frozen_total?: number; owner_match?: boolean }
type Withdrawal = { id: number; amount: number; actual_amount: number; tax_amount: number; status: string;
  status_display: string; payee_method: string; payee_name: string; payee_account: string; created_at: string;
  paid_at: string | null; payout_recorded_at: string | null; payout_reference: string; audit_remark: string;
  legacy_ledger: LegacyLedger | null }
type Page = { readKey?: string; name: string; currency_name: string; page: number; page_size: number; total: number; results: Withdrawal[] }
const receiptStatuses: Record<string, string> = {
  PLATFORM_APPROVED: "平台终审通过，待打款", PAYMENT_UNKNOWN: "打款结果待核实",
  PAID: "已打款", REJECTED: "已驳回", CANCELLED: "已取消",
}

export function PlatformWithdrawals({ revision, financeScopes }: { revision: number; financeScopes: string[] }) {
  const [stores, setStores] = useState<StoreRow[]>([]), [store, setStore] = useState("")
  const [scope, setScope] = useState<"open" | "history" | "legacy">("open"), [page, setPage] = useState(1)
  const [snapshot, setData] = useState<Page | null>(null), [reload, setReload] = useState(0)
  const readKey = `${revision}:${reload}:${store}:${scope}:${page}`
  const data = snapshot?.readKey === readKey ? snapshot : null
  const [loading, setLoading] = useState(false)
  const [storesLoading, setStoresLoading] = useState(true), [storesKnown, setStoresKnown] = useState(false), [storesError, setStoresError] = useState("")
  const [error, setError] = useState(""), [busy, setBusy] = useState(false)
  const [action, setAction] = useState<(WithdrawalIntent & { submitted: boolean; resumed?: boolean }) | null>(null)
  const [actionError, setActionError] = useState("")
  const [retryable, setRetryable] = useState<WithdrawalIntent | null>(null)
  const [reason, setReason] = useState(""), [paidAt, setPaidAt] = useState(""), [confirmed, setConfirmed] = useState(false)
  const [paidAmount, setPaidAmount] = useState("")
  const [duplicateAck, setDuplicateAck] = useState(false), [notice, setNotice] = useState("")
  const session = platformSession()
  const intentKey = session ? withdrawalIntentKey(session.profile.id, session.sessionId) : ""
  const [pending, setPending] = useState<WithdrawalIntent[] | null>(() => {
    try { return intentKey ? readWithdrawalIntents(intentKey) : null }
    catch { return null }
  })
  const storesRequest = useRef<Promise<{ stores: StoreRow[] }> | null>(null)
  const resumeHeading = useRef<HTMLHeadingElement>(null)
  const canAct = financeScopes.includes("*") || financeScopes.includes(store)
  const actionRow = data?.results.find(row => row.id === action?.rowId)
  const actionVisible = canAct && !!data && action?.store === store && scope !== "legacy" && !!(actionRow || action.submitted)
    && (!action.resumed || (!!retryable && sameWithdrawalIntent(retryable, action)))
  const focusResume = actionVisible && action?.resumed === true
  const pendingForRow = (rowId: number) => pending?.some(item => item.store === store && item.rowId === rowId)
  const visibleIntents = storesKnown && !storesLoading ? pending?.filter(item =>
    (financeScopes.includes("*") || financeScopes.includes(item.store)) && stores.some(row => row.slug === item.store)) : []
  useEffect(() => {
    let alive = true
    setStoresLoading(true); setStoresError("")
    const request = platformRequest<{ stores: StoreRow[] }>("withdrawals")
    storesRequest.current = request
    request.then(result => { if (alive) { setStores(result.stores); setStoresKnown(true) } })
      .catch(cause => {
        if (!alive) return
        setStores([])
        setStoresKnown(false)
        setStoresError(cause instanceof Error ? cause.message : "读取失败")
      })
      .finally(() => { if (alive) setStoresLoading(false) })
    return () => { alive = false }
  }, [revision, reload])
  useEffect(() => {
    setData(null)
    const storeRead = storesRequest.current
    if (!store || !storeRead) return
    let alive = true
    setError(""); setLoading(true)
    const listRead = platformRequest<Page>(`withdrawals/${encodeURIComponent(store)}?page=${page}&scope=${scope}`)
    Promise.all([storeRead, listRead])
      .then(([directory, result]) => {
        if (!alive) return
        // A list read can finish after the directory has removed its store.
        const row = directory.stores.find(item => item.slug === store)
        if (!row) throw new Error("门店不存在或无权查看")
        if (row.status !== "OK") throw new Error("门店数据暂不可用")
        setData({ ...result, readKey })
      })
      .catch(cause => { if (alive) setError(cause instanceof Error ? cause.message : "读取失败") })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [store, page, scope, readKey])
  useEffect(() => {
    if (actionVisible || busy) return
    const refresh = () => { if (document.visibilityState === "visible") setReload(value => value + 1) }
    const timer = window.setInterval(refresh, 15000)
    window.addEventListener("focus", refresh)
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh) }
  }, [actionVisible, busy])
  useEffect(() => {
    if (focusResume) resumeHeading.current?.focus()
  }, [focusResume, action?.requestId])
  function clearActionFields() {
    setReason(""); setPaidAt(""); setPaidAmount(""); setConfirmed(false); setDuplicateAck(false); setActionError(""); setNotice("")
  }
  function start(kind: WithdrawalAction, row: Withdrawal) {
    if (!pending || pendingForRow(row.id) || busy) return
    clearActionFields()
    setAction({ store, kind, rowId: row.id, requestId: randomUuid(), submitted: false })
  }
  function resume(intent: WithdrawalIntent) {
    if (busy || !intent.fingerprint || !retryable || !sameWithdrawalIntent(retryable, intent)) return
    clearActionFields()
    setStore(intent.store); setPage(1); setScope("open")
    setAction({ ...intent, submitted: true, resumed: true })
  }
  function clearIntent(intent: WithdrawalIntent) {
    setRetryable(current => current && sameWithdrawalIntent(current, intent) ? null : current)
    try { setPending(forgetWithdrawalIntent(intentKey, intent)); return true }
    catch { return false }
  }
  async function checkReceipt(intent: WithdrawalIntent) {
    if (!session || busy) return
    setBusy(true); setNotice(""); setRetryable(null)
    setAction(current => current?.resumed ? null : current)
    try {
      const { events } = await platformRequest<{ events: WithdrawalAuditEvent[] }>(
        `withdrawals/${encodeURIComponent(intent.store)}/${intent.rowId}/audit-recover`,
      )
      if (!Array.isArray(events) || events.some(event => !event || typeof event.request_id !== "string"
        || typeof event.actor_id !== "string" || !Object.hasOwn(withdrawalActionLabels, event.action))) {
        setNotice("原操作记录需人工核对，请保留原编号。")
        return
      }
      const receipt = findWithdrawalReceipt(events, intent, session.profile.id)
      if (!receipt) {
        if (events.some(event => event.actor_id === session.profile.id && event.request_id === intent.requestId)) {
          setNotice("原操作记录需人工核对，请保留原编号。")
        } else if (intent.fingerprint) {
          setRetryable(intent)
          setNotice("暂未查到原操作，结果仍待核对。可核对并按原内容重发。")
        } else {
          setNotice("暂未查到原操作，结果仍待核对。请保留原编号并稍后重查。")
        }
        return
      }
      const status = receiptStatuses[receipt.to_status]
      if (!status || !["RECORDED", "MISSING"].includes(receipt.audit_status)) {
        setNotice("原操作记录需人工核对，请保留原编号。")
        return
      }
      const audit = receipt.audit_status === "MISSING" ? " 平台审计待补记。" : ""
      const saved = clearIntent(intent) ? "" : " 本地待核对记录暂未清除。"
      setNotice(`原操作已登记，结果为${status}。操作编号 ${intent.requestId}。${audit}${saved}`)
      setAction(current => current && sameWithdrawalIntent(current, intent) ? null : current)
      setReload(value => value + 1)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "原操作暂时无法核对，请稍后重试。")
    } finally { setBusy(false) }
  }
  async function submit() {
    if (!action || !actionVisible || !pending || !intentKey || busy || !confirmed || loading) return
    const { store, kind, rowId, requestId } = action
    const payload: Record<string, string | boolean | number> = { client_request_id: requestId }
    if (["reject", "cancel", "mark-unknown"].includes(kind)) payload.reason = reason.trim()
    if (["reject", "cancel"].includes(kind)) payload.confirmed_unpaid = true
    if (kind === "paid") {
      const timestamp = new Date(paidAt)
      const amount = parseRmbFen(paidAmount.trim())
      if (!paidAt || !reason.trim() || Number.isNaN(timestamp.valueOf())) { setActionError("请填写实际打款时间和凭据编号。"); return }
      if (amount <= 0 || amount > 2147483647) { setActionError("请填写有效的实际打款金额，单位为元，最多两位小数。"); return }
      payload.paid_at = timestamp.toISOString()
      payload.paid_amount_fen = amount
      payload.payout_reference = reason.trim()
      payload.acknowledge_duplicate_reference = duplicateAck
    }
    if (["reject", "cancel", "mark-unknown"].includes(kind) && !reason.trim()) { setActionError("请填写处理原因。"); return }
    const fingerprint = withdrawalFingerprint(intentKey, action, payload)
    if (action.submitted && action.fingerprint !== fingerprint) { setActionError(withdrawalContentMismatch); return }
    const intent = { store, kind, rowId, requestId, fingerprint }
    try { setPending(rememberWithdrawalIntent(intentKey, intent)) }
    catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "无法保存原操作编号，本次未提交。")
      return
    }
    setAction({ ...action, fingerprint, submitted: true }); setBusy(true); setActionError("")
    try {
      const result = await platformRequest<{ status: string }>(`withdrawals/${encodeURIComponent(store)}/${rowId}/${kind}`, { method: "POST", data: payload })
      const message = kind === "paid" && result.status === "PAYMENT_UNKNOWN" ? "打款金额与申请金额不一致，冻结资金保持不变，请继续核对。" : "提现状态已更新。"
      setNotice(clearIntent(intent) ? message : `${message} 本地待核对记录暂未清除。`)
      setAction(null); setReload(value => value + 1)
    } catch (cause) {
      if (!action.submitted && cause instanceof PlatformRequestError && cause.status >= 400 && cause.status < 500
          && ![408, 429].includes(cause.status) && cause.code !== "REQUEST_ID_CONFLICT") {
        if (clearIntent(intent)) setAction(current => current && sameWithdrawalIntent(current, intent)
          ? { store, kind, rowId, requestId, submitted: false } : current)
      }
      setActionError(cause instanceof Error ? cause.message : "处理失败，请核对状态")
    }
    finally { setBusy(false) }
  }
  return <section className="mt-6 space-y-3" aria-labelledby="platform-withdraw-title">
    <h2 id="platform-withdraw-title" className="text-lg font-semibold">平台提现审核</h2>
    <p className="text-sm text-muted-foreground">门店初审后由平台财务终审；实际线下打款后登记凭据，冻结资金才正式记账。</p>
    {import.meta.env.DEV && <p className="text-xs text-muted-foreground">开发环境中的记录包含隔离验收数据。</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {!pending && <p role="alert" className="text-sm text-destructive">本地原操作记录无法读取，请先核对提现处理记录。</p>}
    {!!visibleIntents?.length && <ul aria-label="待核对提现操作" className="space-y-2">
      {visibleIntents.map(intent => <li key={`${intent.store}:${intent.rowId}:${intent.kind}:${intent.requestId}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm">
        <div><p>{stores.find(row => row.slug === intent.store)?.name} · 申请 #{intent.rowId} · {withdrawalActionLabels[intent.kind]}待核对</p>
          <p className="break-all text-muted-foreground">原操作编号 {intent.requestId}</p></div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void checkReceipt(intent)}>查询原操作</Button>
          {retryable && sameWithdrawalIntent(retryable, intent) && <Button type="button" variant="outline" size="sm"
            disabled={busy || !stores.some(row => row.slug === intent.store && row.status === "OK")} onClick={() => resume(intent)}>按原内容重发</Button>}
        </div>
      </li>)}
    </ul>}
    {error && data && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <ReadErrorNotice error={storesError} hasSnapshot={storesKnown} retry={() => setReload(value => value + 1)} />
    <div className="flex flex-wrap gap-2">{stores.map(row => <Button key={row.slug} variant={store === row.slug ? "default" : "outline"} disabled={row.status !== "OK"}
      onClick={() => { setStore(row.slug); setPage(1); setScope("open"); setAction(null) }}>{row.name} · {row.status === "OK" ? `${row.counts ? Object.values(row.counts).reduce((sum, n) => sum + n, 0) : "—"} 待处理 · ${row.legacy_pending ?? "—"} 历史待核实` : "暂不可用"}</Button>)}</div>
    {!stores.length && <DataRegion label="授权门店提现队列" loading={storesLoading} hasSnapshot={storesKnown} error={storesError} retry={() => setReload(value => value + 1)}><span>暂无门店队列</span></DataRegion>}
    {store && <><div className="flex gap-2"><Button variant={scope === "open" ? "default" : "outline"} onClick={() => { setScope("open"); setPage(1) }}>待处理</Button><Button variant={scope === "history" ? "default" : "outline"} onClick={() => { setScope("history"); setPage(1) }}>处理记录</Button><Button variant={scope === "legacy" ? "default" : "outline"} onClick={() => { setScope("legacy"); setPage(1); setAction(null) }}>历史待核实</Button></div>
      {scope === "legacy" && <p className="text-sm text-muted-foreground">历史申请仅供核对，流水状态和钱包冻结总额不能证明是否已线下打款；请核查原渠道凭据。此处不修改状态或资金。</p>}
      <div className="overflow-x-auto rounded-lg border"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-3">申请</th><th>收款资料</th><th>申请金额</th><th>应打款</th><th>状态</th><th>凭据 / 时间</th><th scope="col" data-table-action={scope !== "legacy" || undefined} className={scope !== "legacy" ? "text-center" : undefined}>{scope === "legacy" ? "关联流水与冻结" : "操作"}</th></tr></thead><tbody>
      {(data?.results || []).map(row => <tr key={row.id} className="border-b last:border-0"><td className="p-3">#{row.id}<br />{formatDateTime(row.created_at)}</td><td>{row.payee_method} · {row.payee_name}<br />{row.payee_account}</td><td><DataValue known={row.amount != null}>{amountToXaCoin(row.amount)}</DataValue> {data?.currency_name}</td><td><DataValue known={row.actual_amount != null}>¥{formatRmb(row.actual_amount)}</DataValue></td><td><Status value={scope === "legacy" ? "REVIEW" : row.status} label={scope === "legacy" ? "历史申请待核实" : row.status_display} /></td><td>{row.payout_reference || row.audit_remark || "—"}<br />{row.paid_at ? formatDateTime(row.paid_at) : ""}</td><td data-table-action={scope !== "legacy" || undefined}>
        {scope === "legacy" && (row.legacy_ledger?.transaction_present ? <span>流水 {row.legacy_ledger.transaction_no || "无编号"} · <Status value={row.legacy_ledger.transaction_status_display} label={row.legacy_ledger.transaction_status_display} /><br />类型 {row.legacy_ledger.transaction_type} · <DataValue known={row.legacy_ledger.transaction_amount != null}>{amountToXaCoin(row.legacy_ledger.transaction_amount)}</DataValue> {data?.currency_name}<br />钱包冻结总额 <DataValue known={row.legacy_ledger.wallet_frozen_total != null}>{amountToXaCoin(row.legacy_ledger.wallet_frozen_total)}</DataValue> {data?.currency_name} · {row.legacy_ledger.owner_match ? "账户信息匹配" : "账户信息需核对"}</span> : row.legacy_ledger?.transaction_present === false ? <span>未关联流水，需人工核对</span> : <Status readState="unread" />)}
        <div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">{canAct && row.status === "STORE_APPROVED" && <Button actionTone="approve" size="sm" disabled={loading || !data || !pending || pendingForRow(row.id) || busy} onClick={() => start("approve", row)}>终审通过</Button>}
        {canAct && ["PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="approve" size="sm" disabled={loading || !data || !pending || pendingForRow(row.id) || busy} onClick={() => start("paid", row)}>登记打款</Button>}
        {canAct && row.status === "PLATFORM_APPROVED" && <Button actionTone="adjust" size="sm" variant="outline" disabled={loading || !data || !pending || pendingForRow(row.id) || busy} onClick={() => start("mark-unknown", row)}>结果待核实</Button>}
        {canAct && ["STORE_APPROVED", "PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="reject" size="sm" variant="outline" disabled={loading || !data || !pending || pendingForRow(row.id) || busy} onClick={() => start("reject", row)}>驳回</Button>}
        {canAct && ["STORE_APPROVED", "PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="reject" size="sm" variant="outline" disabled={loading || !data || !pending || pendingForRow(row.id) || busy} onClick={() => start("cancel", row)}>取消</Button>}</div>
      </td></tr>)}{!data?.results.length && <tr><td colSpan={7} className="p-3"><DataRegion label="提现申请" loading={loading} hasSnapshot={!!data} error={error} retry={() => setReload(value => value + 1)}><span>暂无记录</span></DataRegion></td></tr>}</tbody></table></div><div className="flex items-center gap-2 text-sm"><span>共 <DataValue known={!!data} loading={loading}>{data?.total}</DataValue> 条 · 第 {page} 页</span><Button size="sm" variant="outline" disabled={!data || loading || page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={!data || loading || page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>下一页</Button></div>
    </>}
    {action && actionVisible && <div role="dialog" aria-modal="true" aria-labelledby="withdraw-action-title" className="rounded-lg border bg-card p-4 space-y-3">
      <h3 id="withdraw-action-title" className="font-semibold" tabIndex={-1} ref={resumeHeading}>{withdrawalActionLabels[action.kind]} · #{action.rowId}</h3>
      {action.submitted && <p className="break-all text-sm">原操作编号 {action.requestId}。请核对原内容后确认。</p>}
      {actionRow ? <p className="text-sm">{actionRow.payee_method} · {actionRow.payee_name} · {actionRow.payee_account} · ¥{formatRmb(actionRow.actual_amount)}</p>
        : <p className="text-sm">本页未读到这笔申请。请核对处理记录，或使用原操作编号重试。</p>}
      {action.kind !== "approve" && <label className="block text-sm">{action.kind === "paid" ? "打款凭据编号" : "处理原因"}<input autoComplete="off" name="reason" className="mt-1 block w-full rounded border p-2" value={reason} maxLength={action.kind === "paid" ? 100 : 255} onChange={event => setReason(event.target.value)} /></label>}
      {action.kind === "paid" && <>
        <label className="block text-sm">实际打款金额（元）<input autoComplete="off" name="paid_amount" inputMode="decimal" className="mt-1 block rounded border p-2" value={paidAmount} onChange={event => setPaidAmount(event.target.value)} /></label>
        <label className="block text-sm">实际打款时间<input autoComplete="off" name="paid_at" type="datetime-local" step="1" className="mt-1 block rounded border p-2" value={paidAt} onChange={event => setPaidAt(event.target.value)} /></label>
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={duplicateAck} onChange={event => setDuplicateAck(event.target.checked)} />若凭据编号已用于其他明细，已核实属于同一批次</label>
      </>}
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{["reject", "cancel"].includes(action.kind) ? "已核实尚未打款" : action.kind === "paid" ? "已核对实际打款与凭据" : "已核对该申请"}</label>
      <p className="text-xs text-muted-foreground">系统仅登记人工操作，不会自动转账。结果不明时请先核对，使用原操作重试。</p>
      {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
      <div className="flex gap-2"><Button disabled={busy || !confirmed || loading || !data} onClick={() => void submit()}>确认</Button><Button variant="outline" disabled={busy} onClick={() => setAction(null)}>返回</Button></div>
    </div>}
  </section>
}
