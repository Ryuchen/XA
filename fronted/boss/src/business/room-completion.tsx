import {
  fundRequest,
  listCoordinatedPendingFundRequests,
  retryPendingFundRequest,
  type PendingFundRequest,
} from "@xa/shared"
import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { api, date, Panel, useConfirm, useLoad } from "./ui"

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
  RUNNING: "等待你确认", PAUSED: "确认已暂停", INVALIDATED: "原确认已失效",
  CONFIRMED: "已确认结单", AUTO: "已自动确认结单",
}

export function RoomCompletionPanel({ orderId, status, onSaved }: {
  orderId: number
  status: string
  onSaved: () => void
}) {
  const [page, setPage] = useState(1)
  const [notice, setNotice] = useState("")
  const { confirm, dialog } = useConfirm()
  const path = `/orders/orders/${orderId}/room/completion-requests/`
  const confirmPath = (id: number) => `${path}${id}/confirm/`
  const load = useLoad(async () => {
    const room = await api.get<Room>(`/orders/orders/${orderId}/room/`)
    if (room.role !== "BOSS") throw new Error("当前身份无权确认本单结单")
    const [requests, pending] = await Promise.all([
      api.get<CompletionPage>(`${path}?page=${page}`),
      listCoordinatedPendingFundRequests("boss", candidate => candidate.startsWith(path) && /^\d+\/confirm\/$/.test(candidate.slice(path.length))),
    ])
    return { room, requests, pending }
  }, [orderId, page])
  const current = useRef({ load, status })
  current.current = { load, status }
  const snapshot = load.data
  const pending = snapshot?.pending[0]

  function confirmCompletion(id: number, original?: PendingFundRequest) {
    confirm({
      title: original ? "核对原结单确认" : "确认本单服务已完成？",
      description: "确认后结束本单履约，收益仍按报单、审核及售后规则处理。",
      confirm: original ? "按原编号核对" : "确认结单",
      action: async () => {
        const latest = current.current
        const row = latest.load.data?.requests.list.find(item => item.id === id)
        if (latest.load.loading || latest.load.error || latest.load.data?.room.role !== "BOSS")
          throw new Error("请先刷新结单进度后重试")
        if (!original && (latest.status !== "IN_SERVICE" || latest.load.data.room.has_open_objection ||
            row?.status !== "VERIFIED" || row.confirmation?.status !== "RUNNING"))
          throw new Error("结单状态已变化，请重新核对")
        const accepts = (result: Completion) => result.order_id === orderId && result.id === id && result.confirmation?.status === "CONFIRMED"
        try {
          if (original) {
            await retryPendingFundRequest("boss", original, accepts, { coordinateAcrossTabs: true })
          } else {
            await fundRequest("boss", confirmPath(id), {}, { coordinateAcrossTabs: true, acceptsResult: accepts })
          }
          setNotice("已确认结单，收益仍按报单及售后规则处理。")
        } finally {
          load.reload()
          onSaved()
        }
      },
    })
  }

  return <Panel title="房间结单">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="boss-muted">查看陪玩申请、客服核实和确认进度。</p>
      <Button type="button" variant="outline" disabled={load.loading} onClick={load.reload}>刷新结单记录</Button>
    </div>
    {load.loading && <p role="status" className="boss-muted">正在读取结单进度…</p>}
    {load.error && <p role="alert" className="text-destructive">{load.error}。请重新读取后核对。</p>}
    {snapshot?.room.has_open_objection && <p className="boss-muted">房间异议尚未处理，请联系订单客服。</p>}
    {!load.loading && !load.error && snapshot?.requests.list.length === 0 && <p className="boss-muted">暂无结单申请。</p>}
    {snapshot?.requests.list.map(row => <article key={row.id} className="space-y-2 rounded-[8px] border p-3">
      <p className="font-medium">申请 {row.id} · {requestStatus[row.status] || row.status}</p>
      <p className="whitespace-pre-wrap break-words">{row.description}</p>
      <p className="boss-muted">提交于 {date(row.created_at)}</p>
      {row.review_note && <p>客服核实说明 · {row.review_note}</p>}
      {row.confirmation && <>
        <p>{confirmationStatus[row.confirmation.status] || row.confirmation.status}</p>
        {row.confirmation.deadline && <p className="boss-muted">确认截止 {date(row.confirmation.deadline)}</p>}
        {row.confirmation.review_reason && <p>{row.confirmation.review_reason}</p>}
      </>}
      {row.status === "VERIFIED" && row.confirmation?.status === "RUNNING" && status === "IN_SERVICE" && !snapshot.room.has_open_objection && !pending && <Button
        type="button" disabled={load.loading || !!load.error} onClick={() => confirmCompletion(row.id)}
      >确认本单已完成</Button>}
    </article>)}
    {snapshot && snapshot.requests.total > snapshot.requests.page_size && <nav aria-label="结单申请分页" className="flex items-center justify-between gap-3">
      <Button type="button" variant="outline" disabled={load.loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</Button>
      <span className="boss-muted">第 {snapshot.requests.page} 页 · 共 {snapshot.requests.total} 条</span>
      <Button type="button" variant="outline" disabled={load.loading || page * snapshot.requests.page_size >= snapshot.requests.total} onClick={() => setPage(value => value + 1)}>下一页</Button>
    </nav>}
    {pending && <div className="space-y-2 rounded-[8px] border p-3">
      <p>原结单确认结果待核对，请按原编号重试。</p>
      <Button type="button" variant="outline" disabled={load.loading || !!load.error} onClick={() => confirmCompletion(Number(pending.path.slice(path.length).split("/")[0]), pending)}>核对原结单确认</Button>
    </div>}
    {notice && <p role="status" className="boss-muted">{notice}</p>}
    {dialog}
  </Panel>
}
