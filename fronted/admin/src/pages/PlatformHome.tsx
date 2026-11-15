import { PlatformReadStatus, PlatformStoreStatus } from "@/components/PlatformStoreStatus"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { clearPlatformSession, platformRequest, platformSession, type PlatformProfile } from "@/lib/platform-session"
import { PlatformOtpSetup } from "@/components/PlatformOtpSetup"
import { PlatformAccounts } from "@/components/PlatformAccounts"
import { PlatformPassRefundShortfalls } from "@/components/PlatformPassRefundShortfalls"
import { PlatformWithdrawals } from "@/components/PlatformWithdrawals"
import { PlatformStoreManagement } from "@/components/PlatformStoreManagement"

type Store = { slug: string; name: string; status: string; currency_name: string }
type Aggregate = { complete: boolean; generated_at: string; partial_totals: { orders: number; bosses: number; providers: number }; stores: { slug: string; name: string; status: string; read_at: string | null; counts: { orders: number; bosses: number; providers: number } | null }[] }

export default function PlatformHome() {
  const [profile, setProfile] = useState<PlatformProfile | null>(null)
  const [stores, setStores] = useState<Store[]>([])
  const [aggregate, setAggregate] = useState<Aggregate | null>(null)
  const [error, setError] = useState("")
  const [aggregateError, setAggregateError] = useState("")
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let current = true
    async function load() {
      setLoading(true); setError(""); setAggregateError("")
      try {
        const who = await platformRequest<PlatformProfile>("profile")
        const directory = await platformRequest<{ results: Store[] }>("stores")
        const isSuper = who.grants.some(grant => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*")
        let summary: Aggregate | null = null
        if (isSuper) {
          try { summary = await platformRequest<Aggregate>("dashboard") }
          catch { if (current) setAggregateError("门店汇总暂时无法读取，门店管理仍可使用。") }
        }
        if (current) { setProfile(who); setStores(directory.results); setAggregate(summary) }
      } catch (cause) {
        if (current) { setProfile(null); setStores([]); setAggregate(null); setError(cause instanceof Error ? cause.message : "平台数据读取失败") }
      } finally { if (current) setLoading(false) }
    }
    void load()
    return () => { current = false }
  }, [revision])
  function logout() { clearPlatformSession(); location.hash = "/login"; location.reload() }
  return <main id="main-content" tabIndex={-1} className="min-h-screen min-w-[1366px] bg-background p-8 text-foreground">
    <header className="flex items-center justify-between border-b pb-5"><div><h1 className="text-2xl font-semibold">平台管理</h1><p className="mt-1 text-sm text-muted-foreground">{profile?.nickname || profile?.username || platformSession()?.profile.username} · 独立平台身份</p></div><div className="flex gap-2"><Button variant="outline" onClick={() => setRevision(value => value + 1)} disabled={loading}>刷新</Button><Button variant="outline" onClick={logout}>退出登录</Button></div></header>
    {error && <p role="alert" className="mt-6 rounded-md border border-destructive/40 p-4 text-sm text-destructive">{error}</p>}
    {aggregateError && <p role="alert" className="mt-6 rounded-md border p-4 text-sm">{aggregateError}</p>}
    {loading && <p className="mt-6 text-sm text-muted-foreground">正在读取平台数据…</p>}
    {profile?.capabilities?.includes("STORE_MANAGEMENT_V1") && profile.grants.some(grant => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*") && <PlatformStoreManagement revision={revision} canSetup={profile.capabilities.includes("PLATFORM_SETUP_V1")} canEditClocks={profile.capabilities.includes("PLATFORM_CLOCKS_V1")} canEditDeposit={profile.capabilities.includes("PLATFORM_DEPOSIT_V1")} canEditWithdraw={profile.capabilities.includes("PLATFORM_WITHDRAW_V1")} onChanged={() => setRevision(value => value + 1)} />}
    {aggregate && <section className="mt-6 space-y-4"><h2 className="text-lg font-semibold">门店汇总</h2>{!aggregate.complete && <p role="alert" className="rounded-md border p-3 text-sm">部分门店数据未取得，以下仅为已读取门店的合计。</p>}<div className="grid grid-cols-3 gap-4">{[["订单", aggregate.partial_totals.orders], ["老板", aggregate.partial_totals.bosses], ["陪玩", aggregate.partial_totals.providers]].map(([name, value]) => <div key={name} className="rounded-lg border bg-card p-4"><p className="text-sm text-muted-foreground">{name}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}</div><div className="rounded-lg border"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-3">门店</th><th>状态</th><th>订单</th><th>老板</th><th>陪玩</th><th>读取时间</th></tr></thead><tbody>{aggregate.stores.map(row => <tr key={row.slug} className="border-b last:border-0"><td className="p-3">{row.name}</td><td><PlatformReadStatus value={row.status} /></td><td>{row.counts?.orders ?? "—"}</td><td>{row.counts?.bosses ?? "—"}</td><td>{row.counts?.providers ?? "—"}</td><td>{row.read_at ? new Date(row.read_at).toLocaleString() : "—"}</td></tr>)}</tbody></table></div><p className="text-xs text-muted-foreground">当前汇总仅包含数量；财务与经营金额功能仍在建设。</p></section>}
    {profile && !aggregate && <section className="mt-6"><h2 className="text-lg font-semibold">授权门店</h2><ul className="mt-3 divide-y rounded-lg border">{stores.map(store => <li className="p-4 text-sm" key={store.slug}>{store.name} · {store.currency_name} · <PlatformStoreStatus value={store.status} /></li>)}</ul></section>}
    {profile?.grants.some(grant => grant.role === "FINANCE" || (grant.role === "SUPER_ADMIN" && grant.scope_slug === "*")) && <PlatformPassRefundShortfalls revision={revision} />}
    {profile?.grants.some(grant => grant.role === "FINANCE" || (grant.role === "SUPER_ADMIN" && grant.scope_slug === "*")) && <PlatformWithdrawals revision={revision} financeScopes={profile.grants.filter(grant => grant.role === "FINANCE").map(grant => grant.scope_slug)} />}
    {profile?.grants.some(grant => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*") && <PlatformAccounts stores={stores} />}
    {profile && <PlatformOtpSetup />}
  </main>
}
