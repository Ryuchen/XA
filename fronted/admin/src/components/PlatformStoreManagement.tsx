import { PlatformStoreStatus } from "@/components/PlatformStoreStatus"
import { DataRegion } from "@/components/page-data-state"
import { useEffect, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { MutationDialog } from "@/components/business"
import { PlatformRequestError, platformRequest, type PlatformProfile } from "@/lib/platform-session"
import { PlatformStoreSetup } from "@/components/PlatformStoreSetup"
import PlatformGuaranteeClosure from "@/components/PlatformGuaranteeClosure"

type Store = { id: number; slug: string; name: string; provider_name_prefix: string; currency_name: string; database_alias: string; status: string; initialized_at: string | null; updated_at: string; boss_url: string; provider_url: string; admin_url: string }
type Directory = { results: Store[]; available_database_aliases: string[] }
type Draft = { slug: string; name: string; entity_prefix: string; provider_name_prefix: string; currency_name: string; database_alias: string;
  boss_url: string; provider_url: string; admin_url: string; admin_username: string; admin_password: string; admin_phone: string; admin_nickname: string }
const empty: Draft = { slug: "", name: "", entity_prefix: "", provider_name_prefix: "", currency_name: "", database_alias: "", boss_url: "", provider_url: "", admin_url: "", admin_username: "", admin_password: "", admin_phone: "", admin_nickname: "" }

export function PlatformStoreManagement({ revision, profile, canSetup, canEditClocks, canEditDeposit, canEditWithdraw, onChanged }: { revision: number; profile: PlatformProfile; canSetup: boolean; canEditClocks: boolean; canEditDeposit: boolean; canEditWithdraw: boolean; onChanged: () => void }) {
  const [data, setData] = useState<Directory | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [readVersion, setReadVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [editor, setEditor] = useState<"create" | "resume" | "edit" | null>(null)
  const [target, setTarget] = useState<Store | null>(null)
  const [changing, setChanging] = useState<Store | null>(null)
  const [setup, setSetup] = useState<Store | null>(null)
  const [guaranteeStore, setGuaranteeStore] = useState<Store | null>(null)
  const [statusUncertain, setStatusUncertain] = useState(false)
  const [existingAdmin, setExistingAdmin] = useState(false)
  const [form, setForm] = useState<Draft>(empty)
  useEffect(() => {
    let current = true
    setLoading(true); setError("")
    platformRequest<Directory>("store-management").then(result => { if (current) setData(result) })
      .catch(cause => { if (current) { setError(cause instanceof Error ? cause.message : "无法读取门店") } })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [revision, readVersion])
  function close() { setEditor(null); setTarget(null); setForm(empty); setExistingAdmin(false); setUncertain(false); setError(""); onChanged() }
  function start(kind: "create" | "resume" | "edit", store: Store | null = null) {
    setEditor(kind); setTarget(store); setForm(kind === "edit" && store ? { ...empty, name: store.name, provider_name_prefix: store.provider_name_prefix || "", currency_name: store.currency_name,
      boss_url: store.boss_url, provider_url: store.provider_url, admin_url: store.admin_url } : empty); setExistingAdmin(false); setUncertain(false); setError("")
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || uncertain || !editor) return
    if (editor !== "resume" && !/^[A-Z]{2,8}$/.test(form.provider_name_prefix.trim().toUpperCase())) {
      setError("陪玩展示简称须为 2–8 位英文字母")
      return
    }
    setBusy(true); setError("")
    try {
      if (editor === "create") {
        await platformRequest("store-management", { method: "POST", data: { ...form, slug: form.slug.trim(),
          name: form.name.trim(), entity_prefix: form.entity_prefix.trim().toUpperCase(), provider_name_prefix: form.provider_name_prefix.trim().toUpperCase(), currency_name: form.currency_name.trim(),
          admin_username: form.admin_username.trim(), admin_phone: form.admin_phone.trim(), admin_nickname: form.admin_nickname.trim() } })
      } else if (editor === "edit" && target) {
        const changes: Record<string, string> = { name: form.name.trim(), provider_name_prefix: form.provider_name_prefix.trim().toUpperCase(), currency_name: form.currency_name.trim(), expected_updated_at: target.updated_at }
        for (const key of ["boss_url", "provider_url", "admin_url"] as const) if (form[key] !== target[key]) changes[key] = form[key]
        await platformRequest(`store-management/${target.id}`, { method: "PATCH", data: changes })
      } else if (target) {
        const admin = existingAdmin ? {} : { admin_username: form.admin_username.trim(), admin_password: form.admin_password,
          admin_phone: form.admin_phone.trim(), admin_nickname: form.admin_nickname.trim() }
        await platformRequest(`store-management/${target.id}/initialize`, { method: "POST", data: admin })
      }
      close()
    } catch (cause) {
      setUncertain(!(cause instanceof PlatformRequestError && cause.status === 400))
      setError(cause instanceof Error ? cause.message : "操作结果未确认，请刷新后核对")
    } finally { setBusy(false) }
  }
  return <section className="mt-6 space-y-4 rounded-lg border p-4">
    <div className="flex items-center justify-between gap-4"><div><h2 className="text-lg font-semibold">门店管理</h2><p className="text-sm text-muted-foreground">每家门店使用独立业务库；新店初始化后进入待配置。</p></div><Button disabled={loading || !data || !!error || !!editor} onClick={() => start("create")}>新增门店</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {loading && <p className="text-sm text-muted-foreground">正在读取门店…</p>}
    {data && error && <p className="text-sm text-muted-foreground">刷新失败，保留上次读取的门店列表。<Button type="button" variant="outline" size="sm" onClick={() => setReadVersion(value => value + 1)}>重试读取</Button></p>}
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-2">门店</th><th>业务库</th><th>店币</th><th>状态</th><th scope="col" data-table-action className="text-center">操作</th></tr></thead><tbody>{(data?.results || []).map(store => <tr key={store.id} className="border-b last:border-0"><td className="p-2">{store.name} <span className="text-muted-foreground">{store.slug} · 陪玩简称 {store.provider_name_prefix || "待配置"}</span></td><td>{store.database_alias}</td><td>{store.currency_name}</td><td><PlatformStoreStatus value={store.status} /></td><td data-table-action><div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1"><Button actionTone="edit" size="sm" variant="outline" disabled={!!editor || loading || !!error} onClick={() => start("edit", store)}>编辑</Button><Button actionTone="read" size="sm" variant="outline" disabled={!!editor || loading || !!error} onClick={() => setGuaranteeStore(store)}>保证金与闭店</Button>{canSetup && !!store.initialized_at && ["CONFIGURING", "ACTIVE", "DISABLED"].includes(store.status) && <Button actionTone="read" size="sm" variant="outline" disabled={!!editor || loading || !!error} onClick={() => setSetup(store)}>配置核对</Button>}{store.status === "PROVISIONING" && !store.initialized_at && <Button actionTone="edit" size="sm" variant="outline" disabled={!!editor || loading || !!error} onClick={() => start("resume", store)}>继续初始化</Button>}{["ACTIVE", "DISABLED"].includes(store.status) && store.database_alias !== "default" && <Button actionTone={store.status === "ACTIVE" ? "reject" : "approve"} size="sm" variant="outline" disabled={!!editor || loading || !!error} onClick={() => { setStatusUncertain(false); setChanging(store) }}>{store.status === "ACTIVE" ? "停用" : "启用"}</Button>}</div></td></tr>)}{!data?.results.length && <tr><td colSpan={5} className="p-3"><DataRegion label="门店列表" loading={loading} hasSnapshot={!!data} error={error} retry={() => setReadVersion(value => value + 1)}><span>暂无门店</span></DataRegion></td></tr>}</tbody></table></div>
    {editor && <form onSubmit={submit} className="grid max-w-4xl grid-cols-2 gap-4 rounded-lg border p-4">
      <h3 className="col-span-2 font-medium">{editor === "create" ? "新增门店" : editor === "edit" ? `编辑门店：${target?.name}` : `继续初始化：${target?.name}`}</h3>
      {editor === "create" && <>
        {([["name", "门店名称"], ["slug", "门店标识（小写）"], ["entity_prefix", "实体编号前缀"], ["provider_name_prefix", "陪玩展示简称（英文）"], ["currency_name", "店币名称"]] as const).map(([key, title]) => <label className="space-y-1 text-sm" key={key}><span>{title}</span><Input name={key} required disabled={busy || uncertain} maxLength={key === "provider_name_prefix" ? 8 : undefined} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>)}
        <label className="space-y-1 text-sm"><span>已配置的独立业务库</span><select autoComplete="off" name="database_alias" required disabled={busy || uncertain} value={form.database_alias} onChange={event => setForm({ ...form, database_alias: event.target.value })} className="h-10 w-full rounded-md border bg-background px-3"><option value="">请选择</option>{data?.available_database_aliases.map(alias => <option key={alias} value={alias}>{alias}</option>)}</select></label>
      </>}
      {editor === "edit" && ([["name", "门店名称"], ["provider_name_prefix", "陪玩展示简称（英文）"], ["currency_name", "店币名称"]] as const).map(([key, title]) => <label className="space-y-1 text-sm" key={key}><span>{title}</span><Input name={key} required disabled={busy || uncertain} maxLength={key === "provider_name_prefix" ? 8 : undefined} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>)}
      {editor !== "resume" && ([["boss_url", "老板端入口（选填）"], ["provider_url", "陪玩端入口（选填）"], ["admin_url", "后台入口（选填）"]] as const).map(([key, title]) => <label className="space-y-1 text-sm" key={key}><span>{title}</span><Input name={key} type="url" disabled={busy || uncertain} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>)}
      {editor === "resume" && <label className="col-span-2 text-sm"><input type="checkbox" checked={existingAdmin} disabled={busy || uncertain} onChange={event => setExistingAdmin(event.target.checked)} /> 原管理员已经创建，直接核验并继续</label>}
      {editor !== "edit" && !existingAdmin && <fieldset className="col-span-2 grid grid-cols-2 gap-4 rounded border p-3"><legend className="px-1 text-sm">首位门店管理员</legend>
        {([["admin_username", "账号", "text"], ["admin_nickname", "昵称", "text"], ["admin_password", "密码", "password"], ["admin_phone", "手机号", "tel"]] as const).map(([key, title, type]) => <label className="space-y-1 text-sm" key={key}><span>{title}</span><Input name={key} required={key !== "admin_nickname" || editor === "create"} type={type} autoComplete={key === "admin_password" ? "new-password" : "off"} disabled={busy || uncertain} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>)}
      </fieldset>}
      <p className="col-span-2 text-xs text-muted-foreground">{editor === "create" ? "不复制其他门店的客户、订单和资金。" : editor === "resume" ? "系统只恢复归属可核实的业务库，不覆盖已有管理员。" : "修改名称和入口不会更改历史订单及资金。"}如结果未确认，请刷新列表核对。</p>
      <div className="col-span-2 flex gap-2"><Button type="submit" disabled={busy || uncertain}>{busy ? "处理中…" : editor === "create" ? "创建并初始化" : editor === "edit" ? "保存" : "继续初始化"}</Button><Button type="button" variant="outline" disabled={busy} onClick={close}>{uncertain ? "关闭并刷新" : "取消"}</Button></div>
    </form>}
    {changing && <MutationDialog title={changing.status === "ACTIVE" ? "停用门店" : "启用门店"} description={changing.status === "ACTIVE" ? `停用「${changing.name}」后本店业务入口将关闭，请先核对在途业务。` : `确认启用「${changing.name}」？`} disabled={statusUncertain} onClose={() => { setChanging(null); if (statusUncertain) onChanged() }} onSubmit={async () => {
      try { await platformRequest(`store-management/${changing.id}`, { method: "PATCH", data: { status: changing.status === "ACTIVE" ? "DISABLED" : "ACTIVE", expected_updated_at: changing.updated_at } }); onChanged() }
      catch (error) { setStatusUncertain(true); throw error }
    }}>{statusUncertain && <p className="text-sm text-muted-foreground">请关闭后刷新列表，核对门店状态。</p>}</MutationDialog>}
    {guaranteeStore && <PlatformGuaranteeClosure store={guaranteeStore} profile={profile} revision={revision} onClose={() => setGuaranteeStore(null)} />}
    {setup && <PlatformStoreSetup store={setup} canEditClocks={canEditClocks} canEditDeposit={canEditDeposit} canEditWithdraw={canEditWithdraw} onClose={() => setSetup(null)} />}
  </section>
}
