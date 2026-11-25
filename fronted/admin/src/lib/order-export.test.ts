/// <reference types="node" />
import { afterEach, test } from "node:test"
import assert from "node:assert/strict"
import { orderExportFilename, orderExportQuery, requestOrderExport, type OrderExportSession } from "./order-export-request.ts"

const originalFetch = globalThis.fetch
const exportId = "123e4567-e89b-42d3-a456-426614174000"
const csv = (extraHeaders: Record<string, string> = {}) => new Response(new TextEncoder().encode("\ufeff文件,XA 订单明细导出\r\n"), {
  status: 200,
  headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": `attachment; filename="XA-orders-20261125-120000-${exportId.slice(0, 8)}.csv"`, "X-Export-Id": exportId, ...extraHeaders },
})
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } })
function session(overrides: Partial<OrderExportSession> = {}): OrderExportSession {
  return { token: "access", store: "shenglang", refresh: async () => "renewed", assertActive: () => {}, ...overrides }
}
afterEach(() => { globalThis.fetch = originalFetch })

test("导出只携带已提交筛选，数组可重复并排除分页", () => {
  assert.equal(orderExportQuery({ keyword: "A B", status: "PAID", page: 2, page_size: 20 }), "/api/admin/orders/export/?keyword=A+B&status=PAID")
  assert.equal(orderExportQuery({ tag: ["A", "B"], empty: "", missing: undefined }), "/api/admin/orders/export/?tag=A&tag=B")
  assert.equal(orderExportQuery({}), "/api/admin/orders/export/")
})

test("附件名只接受安全的 ASCII CSV 文件名", () => {
  assert.equal(orderExportFilename('attachment; filename="XA-orders-1.csv"'), "XA-orders-1.csv")
  for (const value of [null, "inline", 'attachment; filename="../../x.csv"', 'attachment; filename="订单.csv"', 'attachment; filename="x.csv"; filename="y.csv"']) {
    assert.throws(() => orderExportFilename(value), (error: any) => error.status === 502)
  }
})

test("下载携带本店凭证并校验 CSV、BOM、附件名和导出凭证", async () => {
  let sent: { url: string; init?: RequestInit } | undefined
  globalThis.fetch = (async (url, init) => { sent = { url: String(url), init }; return csv() }) as typeof fetch
  const result = await requestOrderExport({ status: "PAID", page: 3 }, session())
  assert.equal(sent!.url, "/api/admin/orders/export/?status=PAID")
  const headers = sent!.init!.headers as Record<string, string>
  assert.equal(headers["X-Store-Slug"], "shenglang")
  assert.equal(headers.Authorization, "Bearer access")
  assert.equal(sent!.init!.credentials, "same-origin")
  assert.equal(sent!.init!.cache, "no-store")
  assert.match(result.filename, /^XA-orders-.*\.csv$/)
  assert.equal(result.blob.type, "text/csv;charset=utf-8")
  assert.match(new TextDecoder("utf-8", { ignoreBOM: true }).decode(await result.blob.arrayBuffer()), /^\ufeff文件/)
})

test("401 只刷新一次本店会话并重试原下载地址", async () => {
  const calls: string[] = [], authorizations: string[] = []
  let refreshes = 0
  globalThis.fetch = (async (url, init) => {
    calls.push(String(url))
    authorizations.push((init!.headers as Record<string, string>).Authorization)
    return calls.length === 1 ? json({ code: 401, msg: "expired" }, 401) : csv()
  }) as typeof fetch
  const result = await requestOrderExport({ order_no_exact: "XA-1" }, session({ refresh: async () => { refreshes++; return "renewed" } }))
  assert.equal(result.filename.endsWith(".csv"), true)
  assert.deepEqual(calls, ["/api/admin/orders/export/?order_no_exact=XA-1", "/api/admin/orders/export/?order_no_exact=XA-1"])
  assert.equal(refreshes, 1)
  assert.deepEqual(authorizations, ["Bearer access", "Bearer renewed"])
})

test("403、413、500 保留服务端信息且不生成文件", async () => {
  for (const [status, message] of [[403, "无订单导出权限"], [413, "超过 5000 条"], [500, "审计写入失败"]] as const) {
    globalThis.fetch = (async () => json({ code: status, msg: message }, status)) as typeof fetch
    await assert.rejects(requestOrderExport({}, session()), (error: any) => error instanceof Error && error.message === message && (error as any).status === status)
  }
})

test("成功状态缺少附件、凭证、正确类型或 BOM 均拒绝下载", async () => {
  const invalid = [
    csv({ "Content-Disposition": "inline" }),
    csv({ "X-Export-Id": "" }),
    csv({ "Content-Type": "application/json" }),
    new Response(new TextEncoder().encode("文件,XA\r\n"), { status: 200, headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="x.csv"', "X-Export-Id": exportId } }),
    new Response(new Uint8Array([0xef, 0xbb, 0xbf, 0xff]), { status: 200, headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="x.csv"', "X-Export-Id": exportId } }),
  ]
  for (const response of invalid) {
    globalThis.fetch = (async () => response.clone()) as typeof fetch
    await assert.rejects(requestOrderExport({}, session()), (error: any) => error.status === 502)
  }
})

test("下载期间门店或登录变化时丢弃响应", async () => {
  globalThis.fetch = (async () => csv()) as typeof fetch
  await assert.rejects(requestOrderExport({}, session({ assertActive: () => { throw Object.assign(new Error("changed"), { code: "SESSION_CHANGED" }) } })), (error: any) => error.code === "SESSION_CHANGED")
})

test("网络失败明确提示且不把 GET 说成结果未知", async () => {
  globalThis.fetch = (async () => { throw new TypeError("offline") }) as typeof fetch
  await assert.rejects(requestOrderExport({}, session()), (error: any) => error.status === 0 && error.message.includes("网络连接失败"))
})
