const EXPORT_PATH = "/api/admin/orders/export/"

export class OrderExportError extends Error {
  status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = "OrderExportError"
    this.status = status
  }
}

export function orderExportQuery(filters: Record<string, unknown>): string {
  const query = new URLSearchParams()
  Object.entries(filters).forEach(([name, value]) => {
    if (name === "page" || name === "page_size" || value === undefined || value === null || value === "") return
    if (Array.isArray(value)) value.forEach((item) => query.append(name, String(item)))
    else query.set(name, String(value))
  })
  const encoded = query.toString()
  return encoded ? `${EXPORT_PATH}?${encoded}` : EXPORT_PATH
}

export function orderExportFilename(disposition: string | null): string {
  const match = disposition?.match(/^attachment;\s*filename="([A-Za-z0-9._-]+\.csv)"$/i)
  if (!match) throw new OrderExportError("导出文件信息无效，请核对后重试", 502)
  return match[1]
}

async function exportError(response: Response): Promise<OrderExportError> {
  let message = `导出未完成（HTTP ${response.status}），请稍后重试`
  if (response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
    try {
      const body = await response.json()
      if (typeof body?.msg === "string" && body.msg.trim()) message = body.msg
      else if (typeof body?.detail === "string" && body.detail.trim()) message = body.detail
    } catch {
      // The HTTP status still makes this a failed export.
    }
  }
  return new OrderExportError(message, response.status)
}

export type OrderExportSession = {
  token: string
  store: string
  refresh: () => Promise<string>
  assertActive: () => void
}

export async function requestOrderExport(
  filters: Record<string, unknown>,
  session: OrderExportSession,
  options: { signal?: AbortSignal } = {},
): Promise<{ blob: Blob; filename: string }> {
  const target = orderExportQuery(filters)
  const send = (token: string) => fetch(target, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}`, "X-Store-Slug": session.store },
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  })

  let response: Response
  try {
    response = await send(session.token)
    session.assertActive()
    if (response.status === 401) {
      const token = await session.refresh()
      session.assertActive()
      response = await send(token)
      session.assertActive()
    }
  } catch (error) {
    session.assertActive()
    if (error instanceof OrderExportError || error instanceof Error && ("code" in error || error.name === "AbortError")) throw error
    throw new OrderExportError("网络连接失败，请检查网络后重试")
  }
  if (!response.ok) throw await exportError(response)
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/csv")) {
    throw new OrderExportError("导出文件格式无效，请核对后重试", 502)
  }
  const exportId = response.headers.get("X-Export-Id") || ""
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(exportId)) {
    throw new OrderExportError("导出凭证缺失，文件未下载", 502)
  }
  const filename = orderExportFilename(response.headers.get("Content-Disposition"))
  const buffer = await response.arrayBuffer()
  session.assertActive()
  const bytes = new Uint8Array(buffer)
  if (bytes.length < 3 || bytes[0] !== 0xef || bytes[1] !== 0xbb || bytes[2] !== 0xbf) {
    throw new OrderExportError("导出文件内容不完整，请核对后重试", 502)
  }
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(bytes)
  } catch {
    throw new OrderExportError("导出文件编码无效，请核对后重试", 502)
  }
  return { blob: new Blob([buffer], { type: "text/csv;charset=utf-8" }), filename }
}
