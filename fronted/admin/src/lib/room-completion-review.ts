export type ReviewDecision = "VERIFIED" | "REJECTED"
export interface ReviewIntent {
  id: number
  order_id: number
  payload: { decision: ReviewDecision; note: string; client_request_id: string }
}
export interface ReviewReceipt {
  id: number
  order_id: number
  status: string
  review_note: string
  reviewed_at?: string
  confirmation?: { status: string; started_at?: string; deadline?: string } | null
}

export function parseReviewIntent(raw: string | null): ReviewIntent | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw)
    const payload = value?.payload
    if (Number.isSafeInteger(value?.id) && value.id > 0 && Number.isSafeInteger(value?.order_id) && value.order_id > 0 &&
      payload && Object.keys(payload).sort().join(",") === "client_request_id,decision,note" &&
      ["VERIFIED", "REJECTED"].includes(payload.decision) && typeof payload.note === "string" &&
      payload.note.trim() && payload.note.length <= 1000 && typeof payload.client_request_id === "string" &&
      payload.client_request_id.trim() && payload.client_request_id.length <= 64) return value
  } catch { /* A damaged record must not silently create a new operation. */ }
  throw new Error("房间审核恢复记录无法核对，请保留当前浏览器记录并联系管理员。")
}

/** Persist before sending; only a matching server receipt resolves the original intent. */
export async function submitRoomCompletionReview(intent: ReviewIntent, io: {
  save: (value: ReviewIntent) => void
  send: (value: ReviewIntent) => Promise<ReviewReceipt>
  clear: () => void
}): Promise<ReviewReceipt> {
  io.save(intent)
  let receipt: ReviewReceipt
  try {
    receipt = await io.send(intent)
  } catch (cause) {
    const failure = cause as { status?: number; body?: { code?: number; msg?: string } } | null
    // Authentication, throttling and transport errors cannot resolve an earlier unknown write.
    if (failure?.status && [400, 404, 409].includes(failure.status) &&
      failure.body?.code === failure.status && typeof failure.body.msg === "string") {
      try { io.clear() } catch { /* Keeping the original request is safer when storage is unavailable. */ }
    }
    throw cause
  }
  if (receipt?.id !== intent.id || receipt.order_id !== intent.order_id ||
    receipt.status !== intent.payload.decision || receipt.review_note !== intent.payload.note) {
    throw new Error("审核回执暂无法核对，请保留原请求重试。")
  }
  io.clear()
  return receipt
}
