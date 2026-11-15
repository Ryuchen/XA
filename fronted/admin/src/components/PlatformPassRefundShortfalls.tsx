import { PlatformReadStatus, PlatformStoreStatus } from "@/components/PlatformStoreStatus"
import { DataRegion, DataValue } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { platformRequest } from "@/lib/platform-session"
import { PlatformPassRepaymentRead } from "@/components/PlatformPassRepaymentRead"

type ShortfallStore = {
  slug: string; name: string; currency_name: string; store_status: string
  status: "OK" | "UNAVAILABLE" | "NOT_INITIALIZED"; read_at: string | null
  data: { count: number; shortfall_amount: number } | null
}
type Summary = { complete: boolean; stores: ShortfallStore[] }
type Detail = {
  readKey?: string
  slug: string; name: string; currency_name: string; page: number; page_size: number; total: number
  results: { refund_id: number; purchase_id: number; account_no: string; tier: string; refund_amount: number;
    funded_amount: number; shortfall_amount: number; points_status?: "NOT_USED" | "VERIFIED" | "REVIEW";
    points_redeemed: number; points_returned: number; point_discount_amount: number; created_at: string }[]
}

export function PlatformPassRefundShortfalls({ revision }: { revision: number }) {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [selected, setSelected] = useState("")
  const [page, setPage] = useState(1)
  const [detailSnapshot, setDetail] = useState<Detail | null>(null)
  const detail = detailSnapshot?.readKey === `${selected}:${page}` ? detailSnapshot : null
  const [error, setError] = useState("")
  const [detailError, setDetailError] = useState("")
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [summaryReadVersion, setSummaryReadVersion] = useState(0)
  const [detailReadVersion, setDetailReadVersion] = useState(0)
  const detailSelection = useRef("")

  useEffect(() => {
    let current = true
    setLoading(true); setError("")
    platformRequest<Summary>("pass-refund-shortfalls")
      .then(data => { if (current) setSummary(data) })
      .catch(cause => { if (current) { setError(cause instanceof Error ? cause.message : "读取失败") } })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [revision, summaryReadVersion])
  useEffect(() => {
    if (!selected) { setDetail(null); setDetailError(""); detailSelection.current = ""; return }
    let current = true
    const selection = `${selected}:${page}`
    if (detailSelection.current !== selection) { setDetail(null); detailSelection.current = selection }
    setDetailError(""); setDetailLoading(true)
    platformRequest<Detail>(`pass-refund-shortfalls/${encodeURIComponent(selected)}?page=${page}`)
      .then(data => { if (current) setDetail({ ...data, readKey: `${selected}:${page}` }) })
      .catch(cause => { if (current) setDetailError(cause instanceof Error ? cause.message : "读取失败") })
      .finally(() => { if (current) setDetailLoading(false) })
    return () => { current = false }
  }, [selected, page, revision, detailReadVersion])

  return <section className="mt-6 space-y-3" aria-labelledby="pass-shortfalls-title">
    <h2 id="pass-shortfalls-title" className="text-lg font-semibold">通行证退款待补资金</h2>
    <p className="text-sm text-muted-foreground">按门店显示退款时登记的店币缺口；积分返还单独核对。登记缺口不会随补款核销改变。</p>
    {import.meta.env.DEV && <p className="text-xs text-muted-foreground">开发环境中的记录包含隔离验收数据。</p>}
    {loading && <p role="status" className="text-sm text-muted-foreground">正在读取门店数据…</p>}
    {error && <p role="alert" className="rounded-md border p-3 text-sm text-destructive">{error}</p>}
    {summary && error && <p className="text-xs text-muted-foreground">刷新失败，保留上次读取结果。<Button variant="outline" size="sm" onClick={() => setSummaryReadVersion(value => value + 1)}>重试读取</Button></p>}
    {summary && !summary.complete && <p role="alert" className="rounded-md border p-3 text-sm">部分门店数据暂不可用，无法读取的金额不按零计算。</p>}
    <div className="rounded-lg border"><table className="w-full text-left text-sm">
      <thead><tr className="border-b"><th className="p-3">门店</th><th>门店状态</th><th>资金缺口笔数</th><th>登记缺口</th><th>读取时间</th><th scope="col" data-table-action className="text-center">明细</th></tr></thead>
      <tbody>{(summary?.stores || []).map(row => <tr key={row.slug} className="border-b last:border-0">
        <td className="p-3">{row.name}</td><td><PlatformStoreStatus value={row.store_status}/></td>
        <td><DataValue known={row.status === "OK" && row.data?.count != null}>{row.data?.count}</DataValue></td><td><DataValue known={row.status === "OK" && row.data?.shortfall_amount != null}>{amountToXaCoin(row.data?.shortfall_amount)} {row.currency_name}</DataValue></td>
        <td>{row.read_at ? formatDateTime(row.read_at) : row.status === "OK" ? "—" : <PlatformReadStatus value={row.status} />}</td>
        <td data-table-action><div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">{row.status === "OK" && <Button actionTone="read" variant="outline" size="sm" onClick={() => { setSelected(row.slug); setPage(1) }}>查看明细</Button>}</div></td>
      </tr>)}{!summary?.stores.length && <tr><td colSpan={6} className="p-3"><DataRegion label="通行证退款待补资金" loading={loading} hasSnapshot={!!summary} error={error} retry={() => setSummaryReadVersion(value => value + 1)}><span>暂无门店记录</span></DataRegion></td></tr>}</tbody>
    </table></div>
    {selected && <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center justify-between"><h3 className="font-medium">{detail?.name || summary?.stores.find(row => row.slug === selected)?.name || selected} · 退款缺口明细</h3><Button variant="outline" size="sm" onClick={() => setSelected("")}>收起</Button></div>
      {detailLoading && <p role="status" className="text-sm">正在读取明细…</p>}
      {detailError && <p role="alert" className="text-sm text-destructive">{detailError}</p>}
      <div className="overflow-x-auto"><table className="w-full min-w-[1040px] text-left text-sm"><thead><tr className="border-b"><th className="py-2">退款编号</th><th>陪玩编号</th><th>通行证</th><th>退回店币</th><th>门店已出款</th><th>登记缺口</th><th>原积分抵扣</th><th>退回积分</th><th>退款时间</th></tr></thead><tbody>
        {(detail?.results || []).map(row => <tr key={row.refund_id} className="border-b last:border-0"><td className="py-2">{row.refund_id}</td><td>{row.account_no}</td><td>{row.tier}</td><td>{row.refund_amount == null ? "—" : amountToXaCoin(row.refund_amount)} {detail?.currency_name}</td><td>{row.funded_amount == null ? "—" : amountToXaCoin(row.funded_amount)} {detail?.currency_name}</td><td>{row.shortfall_amount == null ? "—" : amountToXaCoin(row.shortfall_amount)} {detail?.currency_name}</td><td>{row.points_status === "NOT_USED" ? "—" : row.points_status === "VERIFIED" ? `${row.points_redeemed == null ? "—" : row.points_redeemed / 100} 积分（抵 ${row.point_discount_amount == null ? "—" : amountToXaCoin(row.point_discount_amount)} ${detail?.currency_name}）` : "待核对"}</td><td>{row.points_status === "NOT_USED" ? "—" : row.points_status === "VERIFIED" ? `${row.points_returned == null ? "—" : row.points_returned / 100} 积分` : "待核对"}</td><td>{formatDateTime(row.created_at)}</td></tr>)}
        {!detail?.results.length && <tr><td colSpan={9} className="p-3"><DataRegion label="退款缺口明细" loading={detailLoading} hasSnapshot={!!detail} error={detailError} retry={() => setDetailReadVersion(value => value + 1)}><span>本页没有记录</span></DataRegion></td></tr>}
      </tbody></table></div><div className="flex items-center gap-3 text-sm"><span>共 <DataValue known={!!detail} loading={detailLoading}>{detail?.total}</DataValue> 条 · 第 {page} 页</span><Button variant="outline" size="sm" disabled={!detail || detailLoading || page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><Button variant="outline" size="sm" disabled={!detail || detailLoading || page * detail.page_size >= detail.total} onClick={() => setPage(page + 1)}>下一页</Button></div>
      <PlatformPassRepaymentRead key={selected} slug={selected} />
    </div>}
  </section>
}
