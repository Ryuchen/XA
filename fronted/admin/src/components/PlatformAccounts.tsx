import { Status } from "@/components/business"
import { DataRegion } from "@/components/page-data-state"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { platformRequest } from "@/lib/platform-session"

type Grant = { role: string; scope_slug: string }
type Account = { id: string; username: string; nickname: string; phone: string; is_phone_verified: boolean; is_active: boolean; updated_at: string; grants: Grant[] }
type Store = { slug: string; name: string }
type Audit = { action: string; actor: string; created_at: string; detail: { changed_fields?: string[]; grants_before?: string[][]; grants_after?: string[][]; grants?: Grant[]; is_active?: boolean } }
type Draft = { username: string; nickname: string; phone: string; password: string; is_active: boolean; superAdmin: boolean; finance: boolean; allStores: boolean; scopes: string[] }
const empty: Draft = { username: "", nickname: "", phone: "", password: "", is_active: true, superAdmin: false, finance: true, allStores: true, scopes: [] }
const fieldNames: Record<string, string> = { nickname: "姓名", phone: "手机号", password: "密码", is_active: "账号状态", grants: "职能授权" }

export function PlatformAccounts({ stores }: { stores: Store[] }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [hasSnapshot, setHasSnapshot] = useState(false)
  const [editing, setEditing] = useState<Account | null | undefined>(undefined)
  const [draft, setDraft] = useState<Draft>(empty)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [version, setVersion] = useState(0)
  const [history, setHistory] = useState<{ username: string; rows: Audit[] } | null>(null)
  const historyRevision = useRef(0)
  const [historyTarget, setHistoryTarget] = useState<Account | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState("")
  useEffect(() => {
    let active = true
    setLoading(true); setError("")
    platformRequest<{ results: Account[] }>("accounts").then(data => { if (active) { setAccounts(data.results); setHasSnapshot(true) } })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "无法读取平台人员") })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [version])
  function begin(account: Account | null) {
    setError(""); setNotice(""); setEditing(account)
    if (!account) { setDraft({ ...empty, scopes: [] }); return }
    const finance = account.grants.filter(grant => grant.role === "FINANCE")
    setDraft({ username: account.username, nickname: account.nickname, phone: account.phone, password: "", is_active: account.is_active,
      superAdmin: account.grants.some(grant => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*"), finance: finance.length > 0,
      allStores: finance.some(grant => grant.scope_slug === "*"), scopes: finance.filter(grant => grant.scope_slug !== "*").map(grant => grant.scope_slug) })
  }
  async function showHistory(account: Account) {
    const revision = ++historyRevision.current
    setError(""); setHistory(null); setHistoryTarget(account); setHistoryLoading(true); setHistoryError("")
    try {
      const data = await platformRequest<{ results: Audit[] }>(`accounts/${encodeURIComponent(account.id)}/audit`)
      if (historyRevision.current === revision) setHistory({ username: account.username, rows: data.results })
    } catch (cause) { if (historyRevision.current === revision) setHistoryError(cause instanceof Error ? cause.message : "无法读取操作记录") }
    finally { if (historyRevision.current === revision) setHistoryLoading(false) }
  }
  function showGrants(grants: string[][] | undefined) {
    return grants?.map(([role, scope]) => `${role === "SUPER_ADMIN" ? "超级管理员" : "财务"}：${scope === "*" ? "全部门店" : stores.find(store => store.slug === scope)?.name || scope}`).join("；") || "无"
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || editing === undefined) return
    setBusy(true); setError(""); setNotice("")
    try {
      if (editing?.grants.some(grant => grant.role === "SUPER_ADMIN" && grant.scope_slug !== "*")) throw new Error("该账号存在旧版限定门店的超级管理员授权，请先核对后再调整。")
      const grants: Grant[] = []
      if (draft.superAdmin) grants.push({ role: "SUPER_ADMIN", scope_slug: "*" })
      if (draft.finance) {
        const scopes = draft.allStores ? ["*"] : draft.scopes
        grants.push(...scopes.map(scope_slug => ({ role: "FINANCE", scope_slug })))
      }
      if (!grants.length || (draft.finance && !draft.allStores && !draft.scopes.length)) throw new Error("请选择至少一项职能和相应门店。")
      if (editing) {
        const data: Record<string, unknown> = { expected_updated_at: editing.updated_at, nickname: draft.nickname, phone: draft.phone, is_active: draft.is_active, grants }
        if (draft.password) data.password = draft.password
        await platformRequest(`accounts/${encodeURIComponent(editing.id)}`, { method: "PATCH", data })
      } else {
        await platformRequest("accounts", { method: "POST", data: { username: draft.username, nickname: draft.nickname, phone: draft.phone, password: draft.password, grants } })
      }
      setEditing(undefined); setDraft({ ...empty, scopes: [] }); setNotice("平台账号已保存。"); setVersion(value => value + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : "账号保存失败，请核对后重试") }
    finally { setBusy(false) }
  }
  return <section className="mt-6 space-y-3 rounded-lg border p-4" aria-label="平台人员管理">
    <div className="flex items-center justify-between"><h2 className="font-semibold">平台人员</h2><Button type="button" variant="outline" disabled={busy || loading || !hasSnapshot || !!error} onClick={() => begin(null)}>新增平台账号</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-2">账号</th><th>姓名</th><th>手机号</th><th>职能与范围</th><th>状态</th><th scope="col" data-table-action className="text-center">操作</th></tr></thead><tbody>{accounts.map(account => <tr key={account.id} className="border-b last:border-0"><td className="p-2">{account.username}</td><td>{account.nickname || "—"}</td><td>{account.phone}{account.is_phone_verified === false && "（未验证）"}</td><td>{account.grants.map(grant => `${grant.role === "SUPER_ADMIN" ? "超级管理员" : "财务"}：${grant.scope_slug === "*" ? "全部门店" : stores.find(store => store.slug === grant.scope_slug)?.name || grant.scope_slug}`).join("；") || "无授权"}</td><td><Status value={account.is_active == null ? undefined : account.is_active ? "ENABLED" : "DISABLED"} label={account.is_active == null ? undefined : account.is_active ? "启用" : "停用"} /></td><td data-table-action><div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1"><Button actionTone="edit" type="button" variant="ghost" disabled={busy || loading || !!error} onClick={() => begin(account)}>编辑</Button><Button actionTone="history" type="button" variant="ghost" disabled={busy} onClick={() => void showHistory(account)}>记录</Button></div></td></tr>)}{!accounts.length && <tr><td colSpan={6} className="p-3"><DataRegion label="平台人员" loading={loading} hasSnapshot={hasSnapshot} error={error} retry={() => setVersion(value => value + 1)}><span>暂无平台人员</span></DataRegion></td></tr>}</tbody></table></div>
    {error && hasSnapshot && <p className="text-sm text-muted-foreground">保留上次读取的平台人员列表。<Button type="button" variant="outline" size="sm" onClick={() => setVersion(value => value + 1)}>重试读取</Button></p>}
    {historyTarget && <section className="rounded-md border p-3 text-sm"><div className="flex justify-between"><h3 className="font-medium">{history?.username || historyTarget.username} 的操作记录（最近 100 条）</h3><Button type="button" variant="ghost" onClick={() => { historyRevision.current += 1; setHistory(null); setHistoryTarget(null); setHistoryLoading(false) }}>关闭</Button></div><ul className="divide-y">{(history?.rows || []).map((row, index) => <li className="space-y-1 py-2" key={`${row.created_at}-${index}`}><p>{new Date(row.created_at).toLocaleString()} · {row.actor} · {row.action === "platform.account_created" ? "创建账号" : row.action === "platform.account_updated" ? `修改账号（${(row.detail.changed_fields || []).map(field => fieldNames[field] || field).join("、")}）` : row.action === "auth.otp_enrolled" ? "绑定验证器" : row.action === "platform.bootstrap" ? "初始化账号" : row.action}</p>{row.detail.changed_fields?.includes("grants") && <p className="text-muted-foreground">授权：{showGrants(row.detail.grants_before)} → {showGrants(row.detail.grants_after)}</p>}{row.detail.changed_fields?.includes("is_active") && <p className="text-muted-foreground">账号状态：<Status value={row.detail.is_active == null ? undefined : row.detail.is_active ? "ENABLED" : "DISABLED"} label={row.detail.is_active == null ? undefined : row.detail.is_active ? "启用" : "停用"} /></p>}</li>)}</ul>{!history?.rows.length && <DataRegion label="平台账号操作记录" loading={historyLoading} hasSnapshot={!!history} error={historyError} retry={() => void showHistory(historyTarget)}><p className="py-2 text-muted-foreground">暂无记录</p></DataRegion>}</section>}
    {editing !== undefined && <form onSubmit={save} className="grid max-w-3xl grid-cols-2 gap-4 rounded-lg border bg-card p-4">
      <h3 className="col-span-2 font-medium">{editing ? "编辑平台账号" : "新增平台账号"}</h3>
      <label className="text-sm">账号<input autoComplete="off" name="username" required disabled={!!editing || busy} value={draft.username} onChange={event => setDraft({ ...draft, username: event.target.value })} className="mt-1 block w-full rounded-md border bg-background px-3 py-2" /></label>
      <label className="text-sm">姓名<input autoComplete="off" name="nickname" value={draft.nickname} disabled={busy} onChange={event => setDraft({ ...draft, nickname: event.target.value })} className="mt-1 block w-full rounded-md border bg-background px-3 py-2" /></label>
      <label className="text-sm">手机号<input autoComplete="off" name="phone" required value={draft.phone} disabled={busy} onChange={event => setDraft({ ...draft, phone: event.target.value })} className="mt-1 block w-full rounded-md border bg-background px-3 py-2" /></label>
      <label className="text-sm">{editing ? "重设密码（不修改请留空）" : "初始密码"}<input name="password" type="password" autoComplete="new-password" required={!editing} value={draft.password} disabled={busy} onChange={event => setDraft({ ...draft, password: event.target.value })} className="mt-1 block w-full rounded-md border bg-background px-3 py-2" /></label>
      <fieldset className="col-span-2 space-y-2 text-sm"><legend className="font-medium">平台职能</legend><label className="mr-4"><input type="checkbox" checked={draft.superAdmin} disabled={busy} onChange={event => setDraft({ ...draft, superAdmin: event.target.checked })} /> 平台超级管理员</label><label><input type="checkbox" checked={draft.finance} disabled={busy} onChange={event => setDraft({ ...draft, finance: event.target.checked })} /> 平台财务</label></fieldset>
      {draft.finance && <fieldset className="col-span-2 space-y-2 text-sm"><legend className="font-medium">财务授权门店</legend><label className="mr-4"><input type="checkbox" checked={draft.allStores} disabled={busy} onChange={event => setDraft({ ...draft, allStores: event.target.checked })} /> 全部门店（含以后新增的门店）</label>{!draft.allStores && <div className="flex flex-wrap gap-4">{stores.map(store => <label key={store.slug}><input type="checkbox" checked={draft.scopes.includes(store.slug)} disabled={busy} onChange={event => setDraft({ ...draft, scopes: event.target.checked ? [...draft.scopes, store.slug] : draft.scopes.filter(item => item !== store.slug) })} /> {store.name}</label>)}</div>}</fieldset>}
      {editing && <label className="col-span-2 text-sm"><input type="checkbox" checked={draft.is_active} disabled={busy} onChange={event => setDraft({ ...draft, is_active: event.target.checked })} /> 启用该账号</label>}
      {editing && <p className="col-span-2 text-xs text-muted-foreground">手机号或密码变更后，原登录状态会失效。</p>}
      <div className="col-span-2 flex gap-2"><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存"}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => { setEditing(undefined); setDraft({ ...empty, scopes: [] }); setError("") }}>取消</Button></div>
    </form>}
  </section>
}
