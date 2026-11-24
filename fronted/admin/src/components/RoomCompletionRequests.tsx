import { useEffect, useId, useRef, useState } from "react"
import { Link } from "react-router"
import { getStoreSlug } from "@xa/shared"
import { errorMessage, request, requestId } from "@/api/request"
import { DataPageState, DataTable, Details, Feedback, Pagination, RefreshButton, Status, useList, useLiveReload, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"
import { parseReviewIntent, submitRoomCompletionReview, type ReviewDecision, type ReviewIntent, type ReviewReceipt } from "@/lib/room-completion-review"

/** Mounted only for order:view + support:edit; the server rechecks store and STAFF authority. */
export default function RoomCompletionRequests({ onReviewed }: { onReviewed: () => void }) {
  const auth = useAdminAuth()
  const storageKey = `xa:store:${getStoreSlug()}:admin:room-review:${auth.profile.id}`
  const [restored] = useState(() => {
    try { return { intent: parseReviewIntent(sessionStorage.getItem(storageKey)), error: "" } }
    catch (cause) { return { intent: null, error: errorMessage(cause) } }
  })
  const [pending, setPending] = useState<ReviewIntent | null>(restored.intent)
  const [selected, setSelected] = useState<Row | null>(restored.intent)
  const [decision, setDecision] = useState<ReviewDecision | "">(restored.intent?.payload.decision || "")
  const [note, setNote] = useState(restored.intent?.payload.note || "")
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [error, setError] = useState(restored.error)
  const [receipt, setReceipt] = useState<ReviewReceipt | null>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const formId = useId()
  const list = useList("room-completion-requests")
  useLiveReload(list.reload)
  useEffect(() => { if (selected) titleRef.current?.focus() }, [selected?.id])

  function open(row: Row) {
    if (pending || busy || restored.error) return
    setSelected(row)
    setDecision("")
    setNote("")
    setError("")
    setReceipt(null)
  }

  async function submit() {
    if (!selected || inFlight.current || restored.error) return
    if (!pending && (!decision || !note.trim())) {
      setError(!decision ? "请选择核实或驳回。" : "请填写核实说明。")
      if (decision) noteRef.current?.focus()
      return
    }
    const intent: ReviewIntent = pending || {
      id: selected.id, order_id: selected.order_id,
      payload: { decision: decision as ReviewDecision, note: note.trim(), client_request_id: requestId("room-review") },
    }
    inFlight.current = true
    setBusy(true)
    setError("")
    try {
      const value = await submitRoomCompletionReview(intent, {
        save: value => {
          try { sessionStorage.setItem(storageKey, JSON.stringify(value)) }
          catch { throw new Error("无法保存审核请求，请检查浏览器存储后重试。") }
          setPending(value)
          setDecision(value.payload.decision)
          setNote(value.payload.note)
        },
        send: async value => (await request<ReviewReceipt>({
          url: `/room-completion-requests/${value.id}/review/`, method: "POST", data: value.payload,
        })).data,
        clear: () => { sessionStorage.removeItem(storageKey); setPending(null) },
      })
      setPending(null)
      setSelected(null)
      setReceipt(value)
      await list.reload()
      onReviewed()
    } catch (cause) {
      const failure = cause as { body?: { msg?: string } } | null
      setError(failure?.body?.msg || errorMessage(cause))
      await list.reload()
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return <DataPageState loading={list.loading} hasSnapshot={!!list.data} error={list.error} retry={list.reload} skeleton="orders">
    <section className="space-y-3" data-testid="room-completion-review">
      <DataTable
        title="待核实房间结单"
        description="核对申请与订单凭证。核实只开始老板确认计时，收益仍按后续规则处理。"
        toolbar={<RefreshButton loading={list.loading} reload={list.reload} />}
        rows={list.rows} loading={list.loading} error={list.error} retry={list.reload} snapshotAvailable={!!list.data}
        minWidth={1000} empty="暂无待核实房间申请" emptyDescription="陪玩提交结单申请后，会在这里等待客服核实。"
        columns={[
          { key: "id", label: "申请 / 状态", render: row => <div>#{row.id} <Status value={row.status} label={row.status === "PENDING" ? "待核实" : undefined} /></div> },
          { key: "order_id", label: "关联订单", render: row => <Button type="button" variant="link" size="sm" asChild><Link to={`/orders?detail=${row.order_id}`}>订单 #{row.order_id}</Link></Button> },
          { key: "submitted_by", label: "提交人账号 ID", render: row => row.submitted_by == null ? "—" : `#${row.submitted_by}` },
          { key: "participants", label: "申请时陪玩账号 ID", render: row => Array.isArray(row.participants) ? row.participants.map((id: number) => `#${id}`).join("、") || "—" : "—" },
          { key: "description", label: "申请说明", render: row => <p className="max-w-80 whitespace-pre-wrap break-words">{row.description || "—"}</p> },
          { key: "created_at", label: "申请时间", render: row => formatDateTime(row.created_at) },
          { key: "actions", label: "操作", render: row => <Button type="button" size="sm" variant="outline" actionTone="read" aria-controls={`${formId}-form`} disabled={busy || !!pending || !!restored.error || !!list.error || list.loading} onClick={() => open(row)}>核实申请 #{row.id}</Button> },
        ]}
      />
      <Pagination page={list.page} total={list.total} loading={list.loading} onChange={list.setPage} pageSize={list.pageSize} onPageSizeChange={list.setPageSize} snapshotAvailable={!!list.data} />
      <Feedback message={list.error} retry={list.reload} />
      <Feedback message={error} />
      {receipt && <div role="status" className="space-y-1 rounded-lg border p-4 text-sm">
        <p>申请 #{receipt.id}：{receipt.status === "VERIFIED" ? "服务端已核实。" : "服务端已驳回。"}</p>
        <p className="whitespace-pre-wrap break-words">核实说明：{receipt.review_note}</p>
        {receipt.status === "VERIFIED" && <p>{receipt.confirmation?.status === "RUNNING" ? `等待老板确认，截止时间 ${formatDateTime(receipt.confirmation.deadline)}。` : "老板确认进度请以订单最新记录为准。"}</p>}
      </div>}
      {selected && <form id={`${formId}-form`} aria-labelledby={`${formId}-title`} className="space-y-4 rounded-lg border p-4" onSubmit={event => { event.preventDefault(); void submit() }}>
        <h3 ref={titleRef} tabIndex={-1} id={`${formId}-title`} className="font-medium">核实房间申请 #{selected.id} · 订单 #{selected.order_id}</h3>
        <Details data={selected} fields={[["submitted_by", "提交人账号 ID"], ["description", "申请说明"], ["created_at", "申请时间"]]} />
        <p className="text-sm text-muted-foreground">请先从关联订单核对结单凭证、当前陪玩和售后。核实后开始等待老板确认；驳回后由陪玩核对说明。</p>
        {pending && <p role="status" className="text-sm">原审核结果尚待核对，已保留原决定、说明和请求号。请按原请求重试，不要另开一次审核。</p>}
        <fieldset disabled={busy || !!pending} className="space-y-2">
          <legend className="mb-2 text-sm font-medium">核实结果（必选）</legend>
          <div className="flex flex-wrap gap-4">{([["VERIFIED", "核实，开始老板确认"], ["REJECTED", "驳回申请"]] as const).map(([value, label]) => <div key={value} className="flex min-h-10 items-center gap-2">
            <input id={`${formId}-${value}`} className="size-4 accent-blue-600" type="radio" name={`${formId}-decision`} value={value} checked={decision === value} required onChange={() => { setDecision(value); setError("") }} />
            <Label htmlFor={`${formId}-${value}`}>{label}</Label>
          </div>)}</div>
        </fieldset>
        <div className="space-y-2">
          <Label htmlFor={`${formId}-note`}>核实说明（必填）</Label>
          <p id={`${formId}-hint`} className="text-xs text-muted-foreground">写明核对依据或驳回原因，最多 1000 字。</p>
          <Textarea ref={noteRef} id={`${formId}-note`} name="note" value={note} required maxLength={1000} readOnly={!!pending} disabled={busy} aria-describedby={`${formId}-hint`} onChange={event => { setNote(event.target.value); setError("") }} />
        </div>
        {pending && <p className="break-all text-xs text-muted-foreground">原请求号：{pending.payload.client_request_id}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" disabled={busy} onClick={() => setSelected(null)}>{pending ? "收起，保留原请求" : "取消"}</Button>
          <Button type="submit" disabled={busy}>{busy ? "正在提交…" : pending ? "按原请求重试" : "提交核实结果"}</Button>
        </div>
      </form>}
      {pending && !selected && <Button type="button" variant="outline" onClick={() => { setSelected(pending); setDecision(pending.payload.decision); setNote(pending.payload.note) }}>继续核对原审核请求 #{pending.id}</Button>}
    </section>
  </DataPageState>
}
