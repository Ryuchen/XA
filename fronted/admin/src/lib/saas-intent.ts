/** Pending markers contain no tokens or form contents. Only a verified receipt
 * can release an uncertain write; closing a dialog is never a failed receipt. */
export type SaasPending = { id: string; operation: string }
export type SaasReceipt = {
  client_request_id: string
  store_slug: string
  status: "APPLIED" | "REJECTED" | "PROCESSING" | "NOT_FOUND"
  message?: string
}
export function readSaasPending(key: string): SaasPending | null {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw)
    if (typeof value.id !== "string" || typeof value.operation !== "string")
      throw new Error("invalid")
    return value
  } catch {
    return { id: "unreadable", operation: "需联系平台核对" }
  }
}
export function rememberSaasPending(key: string, value: SaasPending) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    throw Object.assign(
      new Error("浏览器无法保存请求核对标记，本次未发送，请允许会话存储后重试"),
      { status: 400 }
    )
  }
}
export function clearSaasPending(key: string) {
  sessionStorage.removeItem(key)
}
export function verifySaasReceipt(
  receipt: SaasReceipt,
  store: string,
  id: string
): boolean {
  if (receipt.store_slug !== store || receipt.client_request_id !== id)
    throw new Error("操作回执与当前俱乐部或请求不一致，请联系平台核对")
  return receipt.status === "APPLIED" || receipt.status === "REJECTED"
}
