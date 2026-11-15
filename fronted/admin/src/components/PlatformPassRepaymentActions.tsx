import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PlatformRequestError, platformRequest, platformSession } from "@/lib/platform-session"
import { amountToXaCoin } from "@/lib/format"
import type { Reconciliation } from "@/components/PlatformPassRepaymentRead"

type Pending = { version: 1; actor: string; slug: string; kind: "register" | "verify" | "allocate";
  claimId: string | null; body: Record<string, unknown>; summary: string }
type Props = { slug: string; data: Reconciliation; onChanged: (message: string) => void }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function parseUnits(text: string, decimals: 1 | 2): number {
  const value = text.trim()
  const pattern = decimals === 2 ? /^\d{1,10}(?:\.\d{1,2})?$/ : /^\d{1,10}(?:\.\d)?$/
  if (!pattern.test(value)) throw new Error(decimals === 2 ? "请输入有效人民币金额，最多两位小数。" : "请输入有效店币金额，最多一位小数。")
  const [whole, fraction = ""] = value.split(".")
  const units = BigInt(whole) * (decimals === 2 ? 100n : 10n) + BigInt(fraction.padEnd(decimals, "0"))
  if (units <= 0n || units > 2147483647n) throw new Error("金额超出允许范围。")
  return Number(units)
}

function readPending(key: string, actor: string, slug: string): { item: Pending | null; error: string } {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return { item: null, error: "" }
    const item: Pending = JSON.parse(raw)
    if (item.version !== 1 || item.actor !== actor || item.slug !== slug || !["register", "verify", "allocate"].includes(item.kind)
        || !item.body || typeof item.body !== "object" || typeof item.body.request_id !== "string" || !uuid.test(item.body.request_id)
        || typeof item.summary !== "string" || (item.kind !== "register" && (typeof item.claimId !== "string" || !uuid.test(item.claimId)))) {
      return { item: null, error: "本浏览器的待确认记录无法读取，请联系平台核对后再操作。" }
    }
    return { item, error: "" }
  } catch { return { item: null, error: "无法读取本浏览器的待确认记录，请检查浏览器存储后再操作。" } }
}

export function PlatformPassRepaymentActions({ slug, data, onChanged }: Props) {
  const actor = platformSession()?.profile.id || ""
  const storageKey = `xa:pass-repayment:pending:${actor}:${slug}`
  const [initial] = useState(() => readPending(storageKey, actor, slug))
  const [pending, setPending] = useState<Pending | null>(initial.item)
  const [blocked, setBlocked] = useState(initial.error)
  const [busy, setBusy] = useState(false)
  const working = useRef(false)
  const [notice, setNotice] = useState("")
  const [reference, setReference] = useState("")
  const [yuan, setYuan] = useState("")
  const [receivedAt, setReceivedAt] = useState("")
  const [evidence, setEvidence] = useState("")
  const [verifyId, setVerifyId] = useState("")
  const [allocateId, setAllocateId] = useState("")
  const [refundId, setRefundId] = useState("")
  const [coin, setCoin] = useState("")
  const locked = !!pending || !!blocked || busy || data.status !== "OK"
  const openClaims = data.claims.filter(row => row.status === "RECORDED" && row.receipt && !row.receipt.verified_at)
  const verifiedClaims = data.claims.filter(row => row.status === "RECORDED" && row.receipt?.verified_at && (row.receipt.unallocated_amount ?? 0) > 0)
  const openRefunds = data.refunds.filter(row => (row.remaining_amount ?? 0) > 0)

  const clearPending = () => {
    sessionStorage.removeItem(storageKey)
    setPending(null)
  }
  const send = async (item: Pending) => {
    if (working.current) return
    working.current = true; setBusy(true); setNotice("")
    try {
      const path = item.kind === "register" ? `pass-refund-repayments/${encodeURIComponent(slug)}/register` as const
        : `pass-refund-repayments/${encodeURIComponent(slug)}/${item.claimId}/${item.kind}` as const
      await platformRequest(path, { method: "POST", data: item.body })
      clearPending()
      onChanged(item.kind === "register" ? "已登记到账，请核实凭证后再分配。" : item.kind === "verify" ? "到账已核实。" : "已完成分配，请核对剩余金额。")
    } catch (cause) {
      const deterministic = cause instanceof PlatformRequestError && ["REPAYMENT_INVALID", "REPAYMENT_REJECTED", "RECEIPT_DUPLICATE", "REPAYMENT_BUSY", "REPAYMENT_VOIDED"].includes(cause.code || "")
      if (deterministic) {
        try { clearPending() } catch { setBlocked("无法更新本浏览器的待确认记录，请联系平台核对。") }
        onChanged(cause instanceof Error ? cause.message : "操作未通过校验。")
      } else {
        setNotice(`${cause instanceof Error ? cause.message : "操作结果尚未确认。"} 请使用原请求重试或联系平台核对，勿重复发起。`)
      }
    } finally { working.current = false; setBusy(false) }
  }
  const start = (kind: Pending["kind"], claimId: string | null, fields: Record<string, unknown>, summary: string) => {
    if (locked || working.current) return
    if (!actor || !globalThis.crypto?.randomUUID) { setNotice("当前环境无法生成安全请求编号，请更换浏览器。 "); return }
    if (!window.confirm(`${summary}\n请确认线下凭证与金额无误。`)) return
    const item: Pending = { version: 1, actor, slug, kind, claimId,
      body: { ...fields, request_id: globalThis.crypto.randomUUID() }, summary }
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(item))
      if (sessionStorage.getItem(storageKey) !== JSON.stringify(item)) throw new Error("storage")
    } catch { setBlocked("无法保存待确认请求，本次未提交。请检查浏览器存储。 "); return }
    setPending(item)
    void send(item)
  }
  const register = () => {
    try {
      const amount = parseUnits(yuan, 2)
      const timestamp = new Date(receivedAt)
      if (!reference.trim() || reference.trim().length > 128 || !evidence.trim() || evidence.trim().length > 4000 || !receivedAt || Number.isNaN(timestamp.getTime()))
        throw new Error("请填写流水号、到账时间和凭证说明。")
      start("register", null, { external_reference: reference.trim(), amount,
        received_at: timestamp.toISOString(), evidence: evidence.trim() }, `登记线下到账 ¥${(amount / 100).toFixed(2)}，流水号 ${reference.trim()}`)
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "请检查到账信息。") }
  }
  const verify = () => {
    const selected = openClaims.find(row => row.claim_id === verifyId)
    if (!selected?.receipt) { setNotice("请选择待核实的到账记录。"); return }
    start("verify", verifyId, {}, `核实到账流水 ${selected.receipt.external_reference}，金额 ¥${(selected.receipt.amount / 100).toFixed(2)}`)
  }
  const allocate = () => {
    try {
      const selected = verifiedClaims.find(row => row.claim_id === allocateId)
      const refund = openRefunds.find(row => String(row.refund_id) === refundId)
      const amount = parseUnits(coin, 1)
      if (!selected?.receipt || !refund) throw new Error("请选择已核实到账和退款记录。")
      if (amount > (selected.receipt.unallocated_amount ?? 0) || amount > (refund.remaining_amount ?? 0))
        throw new Error("分配金额不能超过到账未分配余额或退款剩余缺口。")
      start("allocate", allocateId, { refund_id: refund.refund_id, amount },
        `从流水 ${selected.receipt.external_reference} 向退款 #${refund.refund_id} 分配 ${amountToXaCoin(amount)} ${data.currency_name}`)
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "请检查分配信息。") }
  }

  return <div className="space-y-4 border-t pt-4">
    <h5 className="font-medium">补款操作</h5>
    {blocked && <p role="alert" className="text-sm text-destructive">{blocked}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {pending && <div role="alert" className="space-y-2 rounded-md border p-3 text-sm"><p>操作结果待确认：{pending.summary}</p><p className="break-all">请求编号 {String(pending.body.request_id)}。本页刷新后仍可使用原请求重试。</p><Button variant="outline" size="sm" disabled={busy} onClick={() => void send(pending)}>使用原请求重试</Button></div>}
    {data.status !== "OK" && <p className="text-sm">{data.status === "PENDING" || data.status === "REVIEW" ? "存在待确认或不一致记录，请先核对；新操作已暂停。" : "核对状态未读取，请刷新后再操作。"}</p>}
    <fieldset disabled={locked} className="space-y-2 rounded-md border p-3 disabled:opacity-60"><legend className="px-1 text-sm font-medium">登记线下到账</legend>
      <div className="grid gap-2 md:grid-cols-2"><label className="text-sm">外部流水号<Input aria-label="外部流水号" maxLength={128} value={reference} onChange={event => setReference(event.target.value)} /></label>
        <label className="text-sm">实收人民币（元）<Input aria-label="实收人民币（元）" inputMode="decimal" value={yuan} onChange={event => setYuan(event.target.value)} /></label>
        <label className="text-sm">到账时间（本机时区）<Input aria-label="到账时间（本机时区）" type="datetime-local" value={receivedAt} onChange={event => setReceivedAt(event.target.value)} /></label>
        <label className="text-sm">到账凭证说明<Input aria-label="到账凭证说明" maxLength={4000} value={evidence} onChange={event => setEvidence(event.target.value)} /></label></div>
      <Button actionTone="credit" type="button" size="sm" onClick={register}>登记到账</Button>
    </fieldset>
    <fieldset disabled={locked} className="space-y-2 rounded-md border p-3 disabled:opacity-60"><legend className="px-1 text-sm font-medium">核实到账</legend>
      <label className="block text-sm">待核实流水<select autoComplete="off" name="待核实流水" aria-label="待核实流水" className="mt-1 block w-full rounded-md border bg-background p-2" value={verifyId} onChange={event => setVerifyId(event.target.value)}><option value="">请选择</option>{openClaims.map(row => <option key={row.claim_id} value={row.claim_id}>{row.receipt?.external_reference} · {row.receipt?.amount == null ? "—" : `¥${(row.receipt.amount / 100).toFixed(2)}`}</option>)}</select></label>
      <Button actionTone="approve" type="button" size="sm" disabled={!openClaims.length} onClick={verify}>确认核实</Button>
    </fieldset>
    <fieldset disabled={locked} className="space-y-2 rounded-md border p-3 disabled:opacity-60"><legend className="px-1 text-sm font-medium">分配到账</legend>
      <div className="grid gap-2 md:grid-cols-2"><label className="text-sm">已核实流水<select autoComplete="off" name="已核实流水" aria-label="已核实流水" className="mt-1 block w-full rounded-md border bg-background p-2" value={allocateId} onChange={event => setAllocateId(event.target.value)}><option value="">请选择</option>{verifiedClaims.map(row => <option key={row.claim_id} value={row.claim_id}>{row.receipt?.external_reference} · 剩余 {row.receipt?.unallocated_amount == null ? "—" : amountToXaCoin(row.receipt.unallocated_amount)} {data.currency_name}</option>)}</select></label>
        <label className="text-sm">退款记录<select autoComplete="off" name="退款记录" aria-label="退款记录" className="mt-1 block w-full rounded-md border bg-background p-2" value={refundId} onChange={event => setRefundId(event.target.value)}><option value="">请选择</option>{openRefunds.map(row => <option key={row.refund_id} value={row.refund_id}>#{row.refund_id} · 剩余 {row.remaining_amount == null ? "—" : amountToXaCoin(row.remaining_amount)} {data.currency_name}</option>)}</select></label>
        <label className="text-sm">分配金额（{data.currency_name}）<Input aria-label={`分配金额（${data.currency_name}）`} inputMode="decimal" value={coin} onChange={event => setCoin(event.target.value)} /></label></div>
      <Button actionTone="adjust" type="button" size="sm" disabled={!verifiedClaims.length || !openRefunds.length} onClick={allocate}>确认分配</Button>
    </fieldset>
  </div>
}
