import { Status } from "@/components/business"
import { DataRegion, DataValue } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { ReceiptText, CircleMinus } from "@/components/ui/icons"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { platformRequest, platformSession } from "@/lib/platform-session"
import { PlatformPassRepaymentActions } from "@/components/PlatformPassRepaymentActions"

type Receipt = { id: number; external_reference: string; amount: number; received_at: string; evidence: string;
  registered_by: string; verified_by: string | null; verified_at: string | null;
  allocated_amount: number | null; unallocated_amount: number | null }
export type Reconciliation = { status: "OK" | "PENDING" | "REVIEW"; currency_name: string; read_at: string;
  claims: { claim_id: string; request_id: string; status: "RECORDED" | "PENDING" | "REVIEW" | "VOIDED"; receipt: Receipt | null; void_reason?: string }[];
  refunds: { refund_id: number; shortfall_amount: number; allocated_amount: number | null; remaining_amount: number | null }[];
  operations: { operation_id: string; request_id: string; claim_id: string; kind: "VERIFY" | "ALLOCATE";
    status: "PENDING" | "COMPLETED" | "REJECTED" | "VOIDED"; created_at: string; actor_id: string;
    result: { refund_id?: number; amount?: number } | null; rejection_message: string | null }[] }

function money(amount: number | null | undefined, currency: string) {
  return amount == null || !Number.isFinite(amount) ? "待核对" : `${amountToXaCoin(amount)} ${currency}`
}

export function PlatformPassRepaymentRead({ slug }: { slug: string }) {
  const session = platformSession()
  const allowed = !!session?.profile.capabilities?.includes("PLATFORM_PASS_REPAYMENT_V1") &&
    session.profile.grants.some(grant => grant.role === "FINANCE" && (grant.scope_slug === "*" || grant.scope_slug === slug))
  const [revision, setRevision] = useState(0)
  const selection = `${session?.profile.id || ""}:${slug}`
  const [snapshot, setData] = useState<{ selection: string; value: Reconciliation } | null>(null)
  const data = snapshot?.selection === selection ? snapshot.value : null
  const [error, setError] = useState("")
  const [actionNotice, setActionNotice] = useState("")
  const [loading, setLoading] = useState(true)
  const [recovering, setRecovering] = useState(false)
  const recoveryLock = useRef(false)

  useEffect(() => {
    if (!allowed) return
    let current = true
    setData(previous => previous?.selection === selection ? previous : null); setError(""); setLoading(true)
    platformRequest<Reconciliation>(`pass-refund-repayments/${encodeURIComponent(slug)}/reconciliation`)
      .then(value => { if (current) setData({ selection, value }) })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : "补款核对失败") })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [allowed, slug, selection, revision])

  const clearMatchingPending = (kind: "claim" | "operation", id: string) => {
      try {
        const actor = session?.profile.id || ""
        const key = `xa:pass-repayment:pending:${actor}:${slug}`
        const saved = JSON.parse(sessionStorage.getItem(key) || "null")
        const claim = kind === "claim" ? data?.claims.find(row => row.claim_id === id) : null
        const operation = kind === "operation" ? data?.operations.find(row => row.operation_id === id) : null
        const matches = saved?.version === 1 && saved.actor === actor && saved.slug === slug &&
          ((claim && saved.kind === "register" && saved.body?.request_id === claim.request_id) ||
           (operation && saved.kind?.toUpperCase() === operation.kind && saved.claimId === operation.claim_id && saved.body?.request_id === operation.request_id))
        if (matches) sessionStorage.removeItem(key)
        return true
      } catch { return false }
  }
  const recover = async (kind: "claim" | "operation", id: string) => {
    if (recoveryLock.current || !window.confirm("系统将核对已存在的门店记录并恢复平台结果，不会新建到账或分配。是否继续？")) return
    recoveryLock.current = true; setRecovering(true); setActionNotice("")
    try {
      await platformRequest(`pass-refund-repayments/${encodeURIComponent(slug)}/recover/${kind}/${id}`, { method: "POST", data: {} })
      const storageError = !clearMatchingPending(kind, id)
      setActionNotice(storageError ? "核对完成；本机待确认记录未能清除，请使用原请求重试。" : "核对完成，平台结果已更新。")
      setRevision(value => value + 1)
    } catch (cause) {
      setActionNotice(cause instanceof Error ? cause.message : "核对结果暂不明确，请稍后重试。")
    } finally { recoveryLock.current = false; setRecovering(false) }
  }
  const voidPending = async (kind: "claim" | "operation", id: string) => {
    if (recoveryLock.current) return
    const reason = window.prompt("确认已核实门店没有对应提交记录，并填写作废原因（最多 200 字）：")
    if (reason === null) return
    if (!reason.trim() || reason.trim().length > 200) { setActionNotice("请填写不超过 200 字的核实及作废原因。"); return }
    if (!window.confirm("仅在门店没有对应记录时才能作废。原请求将不能再次执行；作废登记后可重新登记流水。是否继续？")) return
    recoveryLock.current = true; setRecovering(true); setActionNotice("")
    try {
      await platformRequest(`pass-refund-repayments/${encodeURIComponent(slug)}/void/${kind}/${id}`, { method: "POST", data: { reason: reason.trim() } })
      const storageError = !clearMatchingPending(kind, id)
      setActionNotice(storageError ? "操作已作废；本机待确认记录未能清除，请使用原请求核对。" : "待确认操作已作废，原因已留痕。")
      setRevision(value => value + 1)
    } catch (cause) {
      setActionNotice(cause instanceof Error ? cause.message : "作废结果暂不明确，请刷新后核对。")
    } finally { recoveryLock.current = false; setRecovering(false) }
  }

  if (!allowed) return null
  return <section className="space-y-3 rounded-lg border p-4" aria-labelledby="pass-repayment-title">
    <div className="flex items-center justify-between gap-3"><h4 id="pass-repayment-title" className="font-medium">线下补款核对</h4>
      <Button size="sm" variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}>刷新</Button></div>
    <p className="text-sm text-muted-foreground">核对平台到账与退款缺口；登记、核实和分配均须以实际线下到账凭据为依据。</p>
    {loading && <p role="status" className="text-sm">正在核对补款记录…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {actionNotice && <p role="status" className="text-sm">{actionNotice}</p>}
    {data && error && <p className="text-xs text-muted-foreground">刷新失败，保留上次核对结果；重新读取成功前暂不能提交处理。</p>}
      {data && data.status !== "OK" && <p role="alert" className="rounded-md border p-3 text-sm">{data.status === "PENDING" ? "存在结果待确认的操作，暂不显示剩余金额。" : data.status === "REVIEW" ? "记录存在不一致，暂不显示剩余金额，请联系平台核对。" : "未读取核对状态，请刷新核对。"}</p>}
      <p className="text-xs text-muted-foreground">核对时间：<DataValue known={!!data} loading={loading}>{formatDateTime(data?.read_at)}</DataValue></p>
      <div className="overflow-x-auto"><table className="w-full min-w-[920px] text-left text-sm">
        <caption className="caption-top border-b py-3 text-left"><h5 className="flex items-center gap-2 font-medium"><ReceiptText className="size-4 text-primary" />到账记录</h5></caption>
        <thead><tr className="border-b"><th className="py-2">流水号</th><th>到账金额</th><th>到账时间</th><th>核实状态</th><th>已分配</th><th>未分配</th><th>凭证</th><th scope="col" data-table-action className="text-center">操作</th></tr></thead><tbody>
        {(data?.claims || []).map(claim => <tr key={claim.claim_id} className="border-b last:border-0">
          <td className="break-all py-2">{claim.receipt?.external_reference || `请求编号 ${claim.request_id}`}</td>
          <td>{claim.receipt?.amount != null ? `¥${(claim.receipt.amount / 100).toFixed(2)}` : "—"}</td>
          <td>{claim.receipt ? formatDateTime(claim.receipt.received_at) : "—"}</td>
          <td><Status value={claim.status === "RECORDED" && claim.receipt?.verified_at ? "SUCCESS" : claim.status}
            label={claim.status === "PENDING" ? "登记待确认" : claim.status === "VOIDED" ? "已作废" : claim.status === "REVIEW" ? "待核对" : claim.status === "RECORDED" ? claim.receipt?.verified_at ? "已核实" : "待核实" : undefined}
            tone={claim.status === "VOIDED" ? "muted" : claim.status === "RECORDED" && claim.receipt?.verified_at ? "success" : "warning"}
            readState={claim.status === "RECORDED" && claim.receipt?.verified_at === undefined ? "unread" : undefined} />
            {claim.status === "VOIDED" && claim.void_reason && <p className="text-xs">{claim.void_reason}</p>}</td>
          <td>{claim.receipt ? money(claim.receipt.allocated_amount, data?.currency_name || "") : "—"}</td>
          <td>{claim.receipt?.verified_at ? money(claim.receipt.unallocated_amount, data?.currency_name || "") : "—"}</td>
          <td className="max-w-48 break-all">{claim.receipt?.evidence || "—"}</td>
          <td data-table-action>
            {claim.status === "PENDING" && <div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">
              <Button actionTone="adjust" size="sm" variant="outline" disabled={recovering || loading || !!error} onClick={() => void recover("claim", claim.claim_id)}>核对登记</Button>
              <Button actionTone="reject" size="sm" variant="outline" disabled={recovering || loading || !!error} onClick={() => void voidPending("claim", claim.claim_id)}>作废登记</Button>
            </div>}
          </td>
        </tr>)}
        {!data?.claims.length && <tr><td colSpan={8} className="py-3"><DataRegion label="到账记录" loading={loading} hasSnapshot={!!data} error={error} retry={() => setRevision(value => value + 1)}><p className="text-sm text-muted-foreground">暂无到账记录</p></DataRegion></td></tr>}
      </tbody></table></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm">
        <caption className="caption-top border-b py-3 text-left"><h5 className="flex items-center gap-2 font-medium"><CircleMinus className="size-4 text-primary" />退款剩余缺口</h5></caption>
        <thead><tr className="border-b"><th className="py-2">退款编号</th><th>原登记缺口</th><th>已核销</th><th>剩余缺口</th></tr></thead><tbody>
        {(data?.refunds || []).map(row => <tr key={row.refund_id} className="border-b last:border-0"><td className="py-2">{row.refund_id}</td><td>{money(row.shortfall_amount, data?.currency_name || "")}</td><td>{money(row.allocated_amount, data?.currency_name || "")}</td><td>{money(row.remaining_amount, data?.currency_name || "")}</td></tr>)}
        {!data?.refunds.length && <tr><td colSpan={4} className="py-3"><DataRegion label="退款剩余缺口" loading={loading} hasSnapshot={!!data} error={error} retry={() => setRevision(value => value + 1)}><p className="text-sm text-muted-foreground">暂无退款缺口记录</p></DataRegion></td></tr>}
      </tbody></table></div>
      {data?.operations.some(op => op.status === "PENDING") && <div className="space-y-1 text-sm"><p className="font-medium">待核对操作</p>{data.operations.filter(op => op.status === "PENDING").map(op => <p key={op.operation_id} className="break-all">{op.kind === "VERIFY" ? "核实" : "分配"} · 请求编号 {op.request_id} · {formatDateTime(op.created_at)} <Button actionTone="adjust" size="sm" variant="outline" disabled={recovering || loading || !!error} onClick={() => void recover("operation", op.operation_id)}>核对操作</Button> <Button actionTone="reject" size="sm" variant="outline" disabled={recovering || loading || !!error} onClick={() => void voidPending("operation", op.operation_id)}>作废操作</Button></p>)}</div>}
      {data?.operations.some(op => op.status === "VOIDED") && <div className="space-y-1 text-sm"><p className="font-medium">已作废操作</p>{data.operations.filter(op => op.status === "VOIDED").map(op => <p key={op.operation_id} className="break-all">请求编号 {op.request_id} · 原因：{op.rejection_message}</p>)}</div>}
      {data?.operations.some(op => op.kind === "ALLOCATE" && op.status === "COMPLETED" && op.result) && <div className="text-sm"><p className="font-medium">已完成分配</p>{data.operations.filter(op => op.kind === "ALLOCATE" && op.status === "COMPLETED" && op.result).map(op => <p key={op.operation_id} className="break-all">流水 {data.claims.find(claim => claim.claim_id === op.claim_id)?.receipt?.external_reference || "待核对"} · 退款 #{op.result?.refund_id} · {money(op.result?.amount ?? null, data.currency_name)} · 请求编号 {op.request_id} · {formatDateTime(op.created_at)}</p>)}</div>}
      {data && !loading && !error && <PlatformPassRepaymentActions key={`${slug}:${session?.profile.id || ""}`} slug={slug} data={data} onChanged={message => { setActionNotice(message); setRevision(value => value + 1) }} />}
  </section>
}
