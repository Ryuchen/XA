import { Status } from "@/components/business"
import { DataRegion, DataValue, ReadErrorNotice } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { platformRequest } from "@/lib/platform-session"
import { formatRmb, randomUuid } from "@xa/shared"

type StoreRow = { slug: string; name: string; status: string; counts: Record<string, number> | null; legacy_pending: number | null }
type LegacyLedger = { transaction_present: boolean; transaction_no?: string; transaction_status_display?: string;
  transaction_type?: string; transaction_amount?: number; wallet_frozen_total?: number; owner_match?: boolean }
type Withdrawal = { id: number; amount: number; actual_amount: number; tax_amount: number; status: string;
  status_display: string; payee_method: string; payee_name: string; payee_account: string; created_at: string;
  paid_at: string | null; payout_recorded_at: string | null; payout_reference: string; audit_remark: string;
  legacy_ledger: LegacyLedger | null }
type Page = { readKey?: string; name: string; currency_name: string; page: number; page_size: number; total: number; results: Withdrawal[] }
type Action = "approve" | "paid" | "mark-unknown" | "reject" | "cancel"

export function PlatformWithdrawals({ revision, financeScopes }: { revision: number; financeScopes: string[] }) {
  const [stores, setStores] = useState<StoreRow[]>([]), [store, setStore] = useState("")
  const [scope, setScope] = useState<"open" | "history" | "legacy">("open"), [page, setPage] = useState(1)
  const [snapshot, setData] = useState<Page | null>(null), [reload, setReload] = useState(0)
  const data = snapshot?.readKey === `${store}:${scope}:${page}` ? snapshot : null
  const [loading, setLoading] = useState(false), [readFailed, setReadFailed] = useState(false)
  const [storesLoading, setStoresLoading] = useState(true), [storesKnown, setStoresKnown] = useState(false), [storesError, setStoresError] = useState("")
  const [error, setError] = useState(""), [busy, setBusy] = useState(false)
  const [action, setAction] = useState<{ kind: Action; row: Withdrawal } | null>(null)
  const [reason, setReason] = useState(""), [paidAt, setPaidAt] = useState(""), [confirmed, setConfirmed] = useState(false)
  const [duplicateAck, setDuplicateAck] = useState(false), [notice, setNotice] = useState("")
  const key = useRef(""), lastSelection = useRef("")
  const canAct = financeScopes.includes("*") || financeScopes.includes(store)
  useEffect(() => {
    let alive = true
    setStoresLoading(true); setStoresError("")
    platformRequest<{ stores: StoreRow[] }>("withdrawals").then(result => { if (alive) { setStores(result.stores); setStoresKnown(true) } })
      .catch(cause => { if (alive) setStoresError(cause instanceof Error ? cause.message : "读取失败") })
      .finally(() => { if (alive) setStoresLoading(false) })
    return () => { alive = false }
  }, [revision, reload])
  useEffect(() => {
    if (!store) { setData(null); lastSelection.current = ""; return }
    let alive = true
    const selection = `${store}:${scope}:${page}`
    if (lastSelection.current !== selection) { setData(null); lastSelection.current = selection }
    setError(""); setLoading(true); setReadFailed(false)
    platformRequest<Page>(`withdrawals/${encodeURIComponent(store)}?page=${page}&scope=${scope}`)
      .then(result => { if (alive) setData({ ...result, readKey: selection }) })
      .catch(cause => { if (alive) { setReadFailed(true); setError(cause instanceof Error ? cause.message : "读取失败") } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [store, page, scope, revision, reload])
  useEffect(() => {
    if (action || busy) return
    const refresh = () => { if (document.visibilityState === "visible") setReload(value => value + 1) }
    const timer = window.setInterval(refresh, 15000)
    window.addEventListener("focus", refresh)
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh) }
  }, [action, busy])
  function start(kind: Action, row: Withdrawal) {
    setReason(""); setPaidAt(""); setConfirmed(false); setDuplicateAck(false); setError(""); setNotice("")
    key.current = randomUuid()
    setAction({ kind, row })
  }
  async function submit() {
    if (!action || busy || !confirmed) return
    const { kind, row } = action
    const payload: Record<string, unknown> = { client_request_id: key.current }
    if (["reject", "cancel", "mark-unknown"].includes(kind)) payload.reason = reason.trim()
    if (["reject", "cancel"].includes(kind)) payload.confirmed_unpaid = true
    if (kind === "paid") {
      if (!paidAt || !reason.trim() || Number.isNaN(new Date(paidAt).valueOf())) { setError("请填写实际打款时间和凭据编号。 "); return }
      payload.paid_at = new Date(paidAt).toISOString()
      payload.payout_reference = reason.trim()
      payload.acknowledge_duplicate_reference = duplicateAck
    }
    if (["reject", "cancel", "mark-unknown"].includes(kind) && !reason.trim()) { setError("请填写处理原因。"); return }
    setBusy(true); setError("")
    try {
      await platformRequest(`withdrawals/${encodeURIComponent(store)}/${row.id}/${kind}`, { method: "POST", data: payload })
      setAction(null); setNotice("提现状态已更新。 "); setReload(value => value + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : "处理失败，请核对状态") }
    finally { setBusy(false) }
  }
  return <section className="mt-6 space-y-3" aria-labelledby="platform-withdraw-title">
    <h2 id="platform-withdraw-title" className="text-lg font-semibold">平台提现审核</h2>
    <p className="text-sm text-muted-foreground">门店初审后由平台财务终审；实际线下打款后登记凭据，冻结资金才正式记账。</p>
    {import.meta.env.DEV && <p className="text-xs text-muted-foreground">开发环境中的记录包含隔离验收数据。</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <ReadErrorNotice error={storesError} hasSnapshot={storesKnown} retry={() => setReload(value => value + 1)} />
    <div className="flex flex-wrap gap-2">{stores.map(row => <Button key={row.slug} variant={store === row.slug ? "default" : "outline"} disabled={row.status !== "OK"}
      onClick={() => { setStore(row.slug); setPage(1); setScope("open"); setAction(null) }}>{row.name} · {row.status === "OK" ? `${row.counts ? Object.values(row.counts).reduce((sum, n) => sum + n, 0) : "—"} 待处理 · ${row.legacy_pending ?? "—"} 历史待核实` : "暂不可用"}</Button>)}</div>
    {!stores.length && <DataRegion label="授权门店提现队列" loading={storesLoading} hasSnapshot={storesKnown} error={storesError} retry={() => setReload(value => value + 1)}><span>暂无门店队列</span></DataRegion>}
    {store && <><div className="flex gap-2"><Button variant={scope === "open" ? "default" : "outline"} onClick={() => { setScope("open"); setPage(1) }}>待处理</Button><Button variant={scope === "history" ? "default" : "outline"} onClick={() => { setScope("history"); setPage(1) }}>处理记录</Button><Button variant={scope === "legacy" ? "default" : "outline"} onClick={() => { setScope("legacy"); setPage(1); setAction(null) }}>历史待核实</Button></div>
      {scope === "legacy" && <p className="text-sm text-muted-foreground">历史申请仅供核对，流水状态和钱包冻结总额不能证明是否已线下打款；请核查原渠道凭据。此处不修改状态或资金。</p>}
      <div className="overflow-x-auto rounded-lg border"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-3">申请</th><th>收款资料</th><th>申请金额</th><th>应打款</th><th>状态</th><th>凭据 / 时间</th><th scope="col" data-table-action={scope !== "legacy" || undefined} className={scope !== "legacy" ? "text-center" : undefined}>{scope === "legacy" ? "关联流水与冻结" : "操作"}</th></tr></thead><tbody>
      {(data?.results || []).map(row => <tr key={row.id} className="border-b last:border-0"><td className="p-3">#{row.id}<br />{formatDateTime(row.created_at)}</td><td>{row.payee_method} · {row.payee_name}<br />{row.payee_account}</td><td><DataValue known={row.amount != null}>{amountToXaCoin(row.amount)}</DataValue> {data?.currency_name}</td><td><DataValue known={row.actual_amount != null}>¥{formatRmb(row.actual_amount)}</DataValue></td><td><Status value={scope === "legacy" ? "REVIEW" : row.status} label={scope === "legacy" ? "历史申请待核实" : row.status_display} /></td><td>{row.payout_reference || row.audit_remark || "—"}<br />{row.paid_at ? formatDateTime(row.paid_at) : ""}</td><td data-table-action={scope !== "legacy" || undefined}>
        {scope === "legacy" && (row.legacy_ledger?.transaction_present ? <span>流水 {row.legacy_ledger.transaction_no || "无编号"} · <Status value={row.legacy_ledger.transaction_status_display} label={row.legacy_ledger.transaction_status_display} /><br />类型 {row.legacy_ledger.transaction_type} · <DataValue known={row.legacy_ledger.transaction_amount != null}>{amountToXaCoin(row.legacy_ledger.transaction_amount)}</DataValue> {data?.currency_name}<br />钱包冻结总额 <DataValue known={row.legacy_ledger.wallet_frozen_total != null}>{amountToXaCoin(row.legacy_ledger.wallet_frozen_total)}</DataValue> {data?.currency_name} · {row.legacy_ledger.owner_match ? "账户信息匹配" : "账户信息需核对"}</span> : row.legacy_ledger?.transaction_present === false ? <span>未关联流水，需人工核对</span> : <Status readState="unread" />)}
        <div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">{canAct && row.status === "STORE_APPROVED" && <Button actionTone="approve" size="sm" disabled={loading || readFailed || !data} onClick={() => start("approve", row)}>终审通过</Button>}
        {canAct && ["PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="approve" size="sm" disabled={loading || readFailed || !data} onClick={() => start("paid", row)}>登记打款</Button>}
        {canAct && row.status === "PLATFORM_APPROVED" && <Button actionTone="adjust" size="sm" variant="outline" disabled={loading || readFailed || !data} onClick={() => start("mark-unknown", row)}>结果待核实</Button>}
        {canAct && ["STORE_APPROVED", "PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="reject" size="sm" variant="outline" disabled={loading || readFailed || !data} onClick={() => start("reject", row)}>驳回</Button>}
        {canAct && ["STORE_APPROVED", "PLATFORM_APPROVED", "PAYMENT_UNKNOWN"].includes(row.status) && <Button actionTone="reject" size="sm" variant="outline" disabled={loading || readFailed || !data} onClick={() => start("cancel", row)}>取消</Button>}</div>
      </td></tr>)}{!data?.results.length && <tr><td colSpan={7} className="p-3"><DataRegion label="提现申请" loading={loading} hasSnapshot={!!data} error={error} retry={() => setReload(value => value + 1)}><span>暂无记录</span></DataRegion></td></tr>}</tbody></table></div><div className="flex items-center gap-2 text-sm"><span>共 <DataValue known={!!data} loading={loading}>{data?.total}</DataValue> 条 · 第 {page} 页</span><Button size="sm" variant="outline" disabled={!data || loading || page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={!data || loading || page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>下一页</Button></div>
      {readFailed && data && <ReadErrorNotice error={error} hasSnapshot retry={() => setReload(value => value + 1)} />}
    </>}
    {action && <div role="dialog" aria-modal="true" aria-labelledby="withdraw-action-title" className="rounded-lg border bg-card p-4 space-y-3">
      <h3 id="withdraw-action-title" className="font-semibold">{({approve:"平台终审",paid:"登记实际打款","mark-unknown":"标记付款待核实",reject:"驳回提现",cancel:"取消提现"})[action.kind]} · #{action.row.id}</h3>
      <p className="text-sm">{action.row.payee_method} · {action.row.payee_name} · {action.row.payee_account} · ¥{formatRmb(action.row.actual_amount)}</p>
      {action.kind !== "approve" && <label className="block text-sm">{action.kind === "paid" ? "打款凭据编号" : "处理原因"}<input autoComplete="off" name="reason" className="mt-1 block w-full rounded border p-2" value={reason} maxLength={255} onChange={event => setReason(event.target.value)} /></label>}
      {action.kind === "paid" && <><label className="block text-sm">实际打款时间<input autoComplete="off" name="paid_at" type="datetime-local" step="1" className="mt-1 block rounded border p-2" value={paidAt} onChange={event => setPaidAt(event.target.value)} /></label><label className="flex gap-2 text-sm"><input name="若凭据编号已用于其他明细，已核实属于同一批次" type="checkbox" checked={duplicateAck} onChange={event => setDuplicateAck(event.target.checked)} />若凭据编号已用于其他明细，已核实属于同一批次</label></>}
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{["reject", "cancel"].includes(action.kind) ? "已核实尚未打款" : action.kind === "paid" ? "已核对实际打款与凭据" : "已核对该申请"}</label>
      <p className="text-xs text-muted-foreground">系统仅登记人工操作，不会自动转账。结果不明时请先核对，使用原操作重试。</p>
      <div className="flex gap-2"><Button disabled={busy || !confirmed || loading || readFailed || !data} onClick={() => void submit()}>确认</Button><Button variant="outline" disabled={busy} onClick={() => setAction(null)}>返回</Button></div>
    </div>}
  </section>
}
