import {
  fundRequest,
  listCoordinatedPendingFundRequests,
  retryPendingFundRequest,
  type PendingFundRequest,
} from "@xa/shared"
import { useId, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Notice, Pager } from "@/components/provider-ui"
import { api, date, message, useLoad, useViewGuard } from "@/lib/provider"

type Completion = {
  id: number
  order_id: number
  status: string
  description: string
  created_at: string
  review_note: string
  confirmation: {
    status: string
    deadline: string | null
    review_reason: string
  } | null
}
type CompletionPage = { list: Completion[]; total: number; page: number; page_size: number }
type Room = { role: string; has_open_objection?: boolean }

const requestStatus: Record<string, string> = {
  PENDING: "等待客服核实", VERIFIED: "客服已核实", REJECTED: "客服已驳回",
}
const confirmationStatus: Record<string, string> = {
  RUNNING: "等待老板确认", PAUSED: "确认已暂停", INVALIDATED: "原确认已失效",
  CONFIRMED: "老板已确认结单", AUTO: "已自动确认结单",
}

export function RoomCompletionPanel({ orderId, status, onSaved }: {
  orderId: number
  status: string
  onSaved: () => Promise<unknown>
}) {
  const [page, setPage] = useState(1)
  const [description, setDescription] = useState("")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [issue, setIssue] = useState("")
  const submitting = useRef(false)
  const descriptionId = useId()
  const headingId = useId()
  const guardView = useViewGuard()
  const path = `/orders/orders/${orderId}/room/completion-requests/`
  const load = useLoad(async () => {
    const room = await api<Room>(`/orders/orders/${orderId}/room/`)
    if (room.role !== "PROVIDER") return { room, requests: null, pending: [] }
    const [requests, pending] = await Promise.all([
      api<CompletionPage>(`${path}?page=${page}`),
      listCoordinatedPendingFundRequests("provider", candidate => candidate === path),
    ])
    return { room, requests, pending }
  }, [orderId, page])
  const snapshot = load.data
  const pending = snapshot?.pending[0]
  const blocked = snapshot?.requests?.list.some(row => row.status === "PENDING" ||
    (row.status === "VERIFIED" && row.confirmation?.status !== "INVALIDATED"))
  const canSubmit = snapshot?.room.role === "PROVIDER" && status === "IN_SERVICE" &&
    !snapshot.room.has_open_objection && !blocked && page === 1

  async function submit(original?: PendingFundRequest) {
    if (submitting.current || load.loading || load.error || (!original && !canSubmit)) return
    const text = description.trim()
    if (!original && !text) { setIssue("请填写结单说明"); return }
    const current = guardView()
    submitting.current = true
    setBusy(true)
    setIssue("")
    setNotice("")
    const accepts = (result: Completion) => result.order_id === orderId && Number.isSafeInteger(result.id) && result.id > 0
    try {
      if (original) {
        await retryPendingFundRequest("provider", original, accepts, { coordinateAcrossTabs: true })
      } else {
        await fundRequest("provider", path, { description: text }, { coordinateAcrossTabs: true, acceptsResult: accepts })
      }
      if (!current()) return
      setDescription("")
      setNotice("结单申请已保存，请查看客服核实和老板确认进度。收益仍按报单及售后规则处理。")
      await Promise.allSettled([load.reload(), onSaved()])
    } catch (cause) {
      if (!current()) return
      setIssue(message(cause))
      await load.reload().catch(() => {})
    } finally {
      submitting.current = false
      if (current()) setBusy(false)
    }
  }

  return <section className="space-y-3" aria-labelledby={headingId}>
    <div className="flex items-center justify-between gap-3">
      <h3 id={headingId} className="text-sm font-semibold">房间结单</h3>
      <Button type="button" variant="outline" size="sm" disabled={busy || load.loading} onClick={() => void load.reload().catch(() => {})}>刷新结单记录</Button>
    </div>
    {load.loading && <p role="status" className="text-xs text-muted-foreground">正在读取结单进度…</p>}
    {load.error && <Notice error>{load.error}。请重新读取后核对。</Notice>}
    {snapshot?.room.role === "FORMER_PROVIDER" && <p className="text-xs text-muted-foreground">你已转出本单，房间结单由当前陪玩处理。</p>}
    {snapshot?.room.has_open_objection && <Notice>房间异议尚未处理，请联系订单客服。</Notice>}
    {snapshot?.requests && <>
      {snapshot.requests.list.length === 0 && <p className="text-xs text-muted-foreground">暂无结单申请。</p>}
      {snapshot.requests.list.map(row => <article key={row.id} className="space-y-2 rounded-[8px] border p-3 text-xs">
        <p className="font-medium">申请 {row.id} · {requestStatus[row.status] || row.status}</p>
        <p className="whitespace-pre-wrap break-words">{row.description}</p>
        <p className="text-muted-foreground">提交于 {date(row.created_at)}</p>
        {row.review_note && <p>客服核实说明 · {row.review_note}</p>}
        {row.confirmation && <>
          <p>{confirmationStatus[row.confirmation.status] || row.confirmation.status}</p>
          {row.confirmation.deadline && <p>确认截止 {date(row.confirmation.deadline)}</p>}
          {row.confirmation.review_reason && <p>{row.confirmation.review_reason}</p>}
        </>}
      </article>)}
      <Pager page={page} total={snapshot.requests.total} size={snapshot.requests.page_size} change={setPage} disabled={busy || load.loading} />
    </>}
    {pending && <div className="space-y-2 rounded-[8px] border p-3 text-xs">
      <p>原申请结果待核对，请按原说明和编号重试。</p>
      <p className="whitespace-pre-wrap break-words">{String(pending.payload.description || "")}</p>
      <Button type="button" variant="outline" disabled={busy || load.loading || !!load.error} onClick={() => void submit(pending)}>{busy ? "正在核对…" : "核对原结单申请"}</Button>
    </div>}
    {canSubmit && !pending && <form action={`/api${path}`} method="post" aria-labelledby={headingId} className="space-y-2" onSubmit={event => { event.preventDefault(); void submit() }}>
      <label htmlFor={descriptionId} className="text-xs font-medium">结单说明（必填）</label>
      <Textarea id={descriptionId} name="description" value={description} maxLength={1000} required disabled={busy} className="text-base" onChange={event => { setDescription(event.target.value); setIssue("") }} />
      <p className="text-xs text-muted-foreground">请先提交本单所需结单凭证，再申请客服核实。</p>
      <Button type="submit" size="sm" disabled={busy || load.loading || !!load.error} aria-busy={busy}>{busy ? "正在提交…" : "申请房间结单"}</Button>
    </form>}
    {notice && <p role="status" className="text-xs text-muted-foreground">{notice}</p>}
    {issue && <p role="alert" className="text-xs text-destructive">{issue}</p>}
  </section>
}
