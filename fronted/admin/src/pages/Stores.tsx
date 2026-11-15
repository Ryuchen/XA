import { DataValue } from "@/components/page-data-state"
import { useState } from "react"
import { toast } from "sonner"
import { useCurrentStore, type StoreInfo } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PlatformStoreStatus } from "@/components/PlatformStoreStatus"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Copy, Plus } from "@/components/ui/icons"
import { StoreLogo } from "@/components/StoreLogo"
import {
  DataPageState,
  DataTable,
  Feedback,
  Field,
  MutationDialog,
  PageHeader,
  RefreshButton,
  useLoad,
  type Row,
} from "@/components/business"
import { adminClient } from "@/api/request"

type StoreRow = StoreInfo & { database_alias: string; initialized_at?: string | null }

export default function StoresPage() {
  const current = useCurrentStore()
  const [editing, setEditing] = useState<Partial<StoreRow> | null>(null)
  const [changing, setChanging] = useState<StoreRow | null>(null)
  const [resuming, setResuming] = useState<StoreRow | null>(null)
  const result = useLoad(() =>
    adminClient.get<{
      results: StoreRow[]
      available_database_aliases: string[]
      resume_initialization_capability?: string
    }>("/stores/manage/")
  )
  const rows = result.data?.results || []
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      toast.success("已复制门店入口")
    } catch {
      toast.error("复制失败，请选择入口地址后手动复制")
    }
  }
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="stores"
    >
      {
        <div className="admin-stores mx-auto max-w-[1500px] space-y-4">
          <PageHeader
            title="平台门店管理"
            tag="平台级权限"
            description="管理门店名称、独立入口和启停状态。店内订单、资金与经营数据分别管理。"
          >
            <RefreshButton loading={result.loading} reload={result.reload} />
            <Button
              disabled={result.loading || !!result.error}
              onClick={() =>
                setEditing({ slug: "", name: "", database_alias: "" })
              }
            >
              <Plus className="size-4" />新增门店
            </Button>
          </PageHeader>
          <Feedback message={result.error} retry={result.reload} />
          {<div className="grid grid-cols-3 gap-4">{[["已登记门店", rows.length, "每店一个稳定门店标识"], ["已配置业务库", new Set(rows.map(row => row.database_alias).filter(Boolean)).size, "各店业务数据独立管理"], ["营业中", rows.filter(row => row.status === "ACTIVE").length, "停用或初始化中的门店不接收新业务"]].map(([label,value,note]) => <Card key={String(label)}><CardHeader><CardDescription>{label}</CardDescription><CardTitle className="text-2xl tabular-nums"><DataValue>{value}</DataValue></CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">{note}</CardContent></Card>)}</div>}
          <DataTable snapshotAvailable={!!result.data}
            title="门店与独立入口"
            description="复制入口保留门店参数，进入后台仍需目标门店授权"
            minWidth={1080}
            tableClassName="table-fixed"
            columnWidths={{ name: 208, database_alias: 160, currency_name: 80, status: 96, entry: 288, actions: 208 }}
            error={result.error}
            retry={result.reload}
            loading={result.loading}
            rows={rows}
            columns={[
              {
                key: "name",
                label: "门店",
                render: (row: Row) => (
                  <div className="flex items-center gap-3"><StoreLogo store={row as StoreRow} className="size-9" /><div className="max-w-56 break-words">
                    <strong>{row.name}</strong>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {row.slug} · 陪玩简称 {row.provider_name_prefix === undefined ? "—" : row.provider_name_prefix || "待配置"}
                    </p>
                  </div></div>
                ),
              },
              {
                key: "database_alias",
                label: "独立业务库",
                render: (row: Row) => <code className="rounded bg-muted px-2 py-1 text-xs">{row.database_alias === undefined ? "—" : row.database_alias || "尚未配置"}</code>,
              },
              {
                key: "currency_name",
                label: "店币",
                render: (row: Row) => row.currency_name || "—",
              },
              {
                key: "status",
                label: "状态",
                render: (row: Row) => <PlatformStoreStatus value={row.status} />,
              },
              {
                key: "entry",
                label: "门店入口",
                render: (row: Row) => (
                  <div className="max-w-sm space-y-2"><div className="flex flex-wrap gap-2">{[["老板端",row.boss_url],["陪玩端",row.provider_url],["管理后台",row.admin_url]].map(([label,url]) => <Button key={label} size="sm" variant="ghost" disabled={!url} title={url === undefined ? "入口未读取" : url || "尚未配置入口"} onClick={() => void copy(url)}><Copy className="size-3.5" />{label}</Button>)}</div><details><summary className="cursor-pointer text-xs text-muted-foreground">查看完整入口</summary><div className="mt-2 space-y-2">
                    {[
                      ["老板端", row.boss_url],
                      ["陪玩端", row.provider_url],
                      ["管理后台", row.admin_url],
                    ].map(([label, url]) => (
                      <div className="flex items-center gap-2" key={label}>
                        <span className="w-16 shrink-0 text-xs">{label}</span>
                        {url ? (
                          <>
                            <a
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="min-w-0 break-all text-primary underline underline-offset-4"
                            >
                              {url}
                            </a>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => void copy(url)}
                            >
                              复制
                            </Button>
                          </>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            {url === undefined ? "入口未读取" : "待配置入口地址"}
                          </span>
                        )}
                      </div>
                    ))}
                  </div></details></div>
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row: Row) => (
                  <div className="flex flex-nowrap gap-2 whitespace-nowrap">
                    {result.data?.resume_initialization_capability === "OWNERSHIP_V1" && row.status === "PROVISIONING" && !row.initialized_at && (
                      <Button actionTone="edit" size="sm" variant="outline" onClick={() => setResuming(row as StoreRow)}>继续初始化</Button>
                    )}
                    <Button
                      actionTone="edit"
                      size="sm"
                      variant="outline"
                      onClick={() => setEditing(row)}
                    >
                      编辑
                    </Button>
                    <Button
                      actionTone={row.status === "ACTIVE" ? "adjust" : "approve"}
                      size="sm"
                      variant="outline"
                      disabled={row.slug === current.slug || ["PROVISIONING", "CONFIGURING"].includes(row.status)}
                      onClick={() => setChanging(row as StoreRow)}
                    >
                      {row.status === "ACTIVE" ? "停用" : "启用"}
                    </Button>
                  </div>
                ),
              },
            ]}
          />
          {rows.some((row) => row.status === "PROVISIONING") && (
            <p role="status" className="text-sm text-muted-foreground">
              待初始化门店尚不能登录或接单。初始化中断时可继续处理；数据库归属无法核实的情况需先由平台运维核对。
            </p>
          )}
          {editing && (
            <StoreEditor
              value={editing}
              locked={result.loading || !!result.error}
              aliases={result.data?.available_database_aliases || []}
              onClose={() => setEditing(null)}
              onSaved={result.reload}
            />
          )}
          {resuming && <StoreResume store={resuming} locked={result.loading || !!result.error} onClose={() => setResuming(null)} onSaved={result.reload} />}
          {changing && (
            <MutationDialog
              title={changing.status === "ACTIVE" ? "停用门店" : "启用门店"}
              submitVariant={
                changing.status === "ACTIVE" ? "destructive" : "default"
              }
              readOnly={result.loading || !!result.error}
              description={
                changing.status === "ACTIVE"
                  ? `停用“${changing.name}”后，老板端、陪玩端和该店后台将停止访问。订单和账目仍保留，请先确认在途业务已妥善处理。`
                  : `启用“${changing.name}”前，系统会检查业务库是否初始化完成。`
              }
              onClose={() => setChanging(null)}
              onSubmit={async () => {
                await adminClient.patch(`/stores/manage/${changing.id}/`, {
                  status: changing.status === "ACTIVE" ? "DISABLED" : "ACTIVE",
                })
                toast.success("门店状态已更新")
                await result.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function StoreResume({ store, locked, onClose, onSaved }: {
  store: StoreRow
  locked: boolean
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [existing, setExisting] = useState(false)
  const [failed, setFailed] = useState(false)
  const [form, setForm] = useState({ admin_username: "", admin_password: "", admin_phone: "", admin_nickname: "" })
  const incomplete = !form.admin_username.trim() || form.admin_password.length < 8 || !/^\d{5,20}$/.test(form.admin_phone)
  return <MutationDialog title={`继续初始化「${store.name}」`} description="系统会先核验独立业务库归属；已存在的管理员不会被覆盖。提交结果不明确时，请关闭并刷新列表后核对状态。" submitText="继续初始化" readOnly={locked} disabled={failed || (!existing && incomplete)} onClose={onClose} onSubmit={async () => {
    try {
      await adminClient.post(`/stores/manage/${store.id}/initialize/`, existing ? {} : { ...form, admin_username: form.admin_username.trim(), admin_phone: form.admin_phone.trim(), admin_nickname: form.admin_nickname.trim() })
      toast.success("门店初始化已完成")
      await onSaved()
    } catch (error) {
      setFailed(true)
      throw error
    }
  }}>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={existing} onChange={event => setExisting(event.target.checked)} />原管理员已经创建，直接核验并继续</label>
    {!existing && <div className="grid grid-cols-2 gap-4">
      <label className="space-y-2 text-sm"><span>原管理员账号</span><Input name="admin_username" required maxLength={150} autoComplete="off" value={form.admin_username} onChange={event => setForm({ ...form, admin_username: event.target.value })} /></label>
      <label className="space-y-2 text-sm"><span>管理员昵称（选填）</span><Input name="admin_nickname" maxLength={50} value={form.admin_nickname} onChange={event => setForm({ ...form, admin_nickname: event.target.value })} /></label>
      <label className="space-y-2 text-sm"><span>原登录密码</span><Input name="admin_password" required minLength={8} type="password" autoComplete="new-password" value={form.admin_password} onChange={event => setForm({ ...form, admin_password: event.target.value })} /></label>
      <label className="space-y-2 text-sm"><span>原手机号</span><Input name="admin_phone" required type="tel" inputMode="numeric" maxLength={20} value={form.admin_phone} onChange={event => setForm({ ...form, admin_phone: event.target.value.replace(/\D/g, "") })} /></label>
    </div>}
    {failed && <p className="text-sm text-muted-foreground">请关闭窗口并刷新门店列表，核对状态后再处理。</p>}
  </MutationDialog>
}

function StoreEditor({
  value,
  locked,
  aliases,
  onClose,
  onSaved,
}: {
  value: Partial<StoreRow>
  locked: boolean
  aliases: string[]
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [form, setForm] = useState({
    name: value.name || "",
    currency_name: value.currency_name || "浪花",
    slug: value.slug || "",
    entity_prefix: value.entity_prefix || "",
    provider_name_prefix: value.provider_name_prefix || "",
    database_alias: value.database_alias || "",
    boss_url: value.boss_url || "",
    provider_url: value.provider_url || "",
    admin_url: value.admin_url || "",
    logo_url: value.logo_url || "",
    admin_username: "",
    admin_password: "",
    admin_phone: "",
    admin_nickname: "",
  })
  const [logoUploading, setLogoUploading] = useState(false)
  const [logoError, setLogoError] = useState("")
  const fields: Array<[keyof typeof form, string]> = [
    ["name", "门店名称"],
    ["provider_name_prefix", "陪玩展示简称（英文）"],
    ["currency_name", "店币名称"],
    ["boss_url", "老板端基础地址"],
    ["provider_url", "陪玩端基础地址"],
    ["admin_url", "管理后台基础地址"],
  ]
  const adminIncomplete = !form.database_alias || !/^[A-Z0-9]{2,4}$/.test(form.entity_prefix) || !form.admin_username.trim() || form.admin_password.length < 8 || !/^\d{5,20}$/.test(form.admin_phone) || !form.admin_nickname.trim()
  const displayPrefixInvalid = !/^[A-Z]{2,8}$/.test(form.provider_name_prefix.trim().toUpperCase())
  async function uploadLogo(file: File) {
    setLogoUploading(true)
    setLogoError("")
    try {
      const payload = new FormData()
      payload.append("image", file)
      const result = await adminClient.upload<{ url: string }>("/stores/manage/logo-upload/", payload)
      setForm(current => ({ ...current, logo_url: result.url }))
      toast.success("门店 Logo 已上传")
    } catch (cause: any) {
      setLogoError(cause?.message || "Logo 上传失败，请重试")
    } finally {
      setLogoUploading(false)
    }
  }
  return (
    <MutationDialog
      title={value.id ? "编辑门店" : "新增门店"}
      readOnly={locked || logoUploading}
      description={
        value.id
          ? "修改门店名称与入口地址，历史业务数据保留。"
          : "创建独立业务库并初始化首位门店管理员，不复制其他门店的账号、余额与订单。"
      }
      submitText={value.id ? "保存" : "创建并初始化门店"}
      onClose={onClose}
      disabled={(!value.id && adminIncomplete) || displayPrefixInvalid || logoUploading}
      onSubmit={async () => {
        const { slug, entity_prefix, database_alias, admin_username, admin_password, admin_phone, admin_nickname, ...rawDetails } = form
        const details = { ...rawDetails, provider_name_prefix: rawDetails.provider_name_prefix.trim().toUpperCase() }
        if (value.id)
          await adminClient.patch(`/stores/manage/${value.id}/`, details)
        else
          await adminClient.post("/stores/manage/", {
            ...details,
            slug,
            entity_prefix: entity_prefix.trim().toUpperCase(),
            database_alias,
            admin_username: admin_username.trim(),
            admin_password,
            admin_phone: admin_phone.trim(),
            admin_nickname: admin_nickname.trim(),
          })
        toast.success(
          value.id ? "门店已更新" : "门店与首位管理员已初始化"
        )
        window.dispatchEvent(new Event("xa:store-updated"))
        await onSaved()
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        {fields.map(([key, label]) => (
          <label
            className={`space-y-2 text-sm ${key === "name" || key === "currency_name" || key === "provider_name_prefix" ? "col-span-2" : ""}`}
            key={key}
          >
            <span>{label}</span>
            <Input name={key === "name" ? "请输入门店名称" : key === "currency_name" ? "请输入店币名称" : key === "provider_name_prefix" ? "例如 SL 或 XA" : "https://…"}
              type={key.endsWith("_url") ? "url" : "text"}
              required={key === "name" || key === "currency_name" || key === "provider_name_prefix"}
              maxLength={key === "name" ? 80 : key === "currency_name" ? 16 : key === "provider_name_prefix" ? 8 : 500}
              value={form[key]}
              placeholder={key === "name" ? "请输入门店名称" : key === "currency_name" ? "请输入店币名称" : key === "provider_name_prefix" ? "例如 SL 或 XA" : "https://…"}
              onChange={(event) =>
                setForm({ ...form, [key]: event.target.value })
              }
            />
          </label>
        ))}
        {!value.id && (
          <>
            <label className="space-y-2 text-sm">
              <span>门店标识</span>
              <Input name="slug"
                required
                pattern="[a-z][a-z0-9-]{0,47}"
                maxLength={48}
                autoCapitalize="none"
                autoCorrect="off"
                value={form.slug}
                onChange={(event) =>
                  setForm({ ...form, slug: event.target.value })
                }
              />
              <p className="text-xs text-muted-foreground">
                小写字母开头，最多48位；创建后固定。
              </p>
            </label>
            <label className="space-y-2 text-sm">
              <span>实体编号前缀</span>
              <Input name="entity_prefix"
                required
                pattern="[A-Z0-9]{2,4}"
                maxLength={4}
                autoCapitalize="characters"
                autoCorrect="off"
                placeholder="例如 SL / XA…"
                value={form.entity_prefix}
                onChange={(event) =>
                  setForm({ ...form, entity_prefix: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "") })
                }
              />
              <p className="text-xs text-muted-foreground">
                2–4 位大写字母或数字，创建后固定，用于门店全部实体编号。
              </p>
            </label>
            <div className="space-y-2 text-sm">
              <Field field={{ key: "database_alias", label: "独立业务库", required: true, type: "select", options: [["", "请选择已配置的空业务库"], ...aliases.map(alias => [alias, alias] as [string,string])] }} value={form.database_alias} onChange={database_alias => setForm({ ...form, database_alias })} />
              {!aliases.length && (
                <p className="text-xs text-muted-foreground">
                  暂无可用业务库，请联系平台运维配置。
                </p>
              )}
            </div>
          </>
        )}
        <div className="col-span-2 flex flex-wrap items-center gap-4 rounded-lg border p-4">
          {form.logo_url ? <StoreLogo store={{ slug: value.slug || "new", name: form.name || "新门店", logo_url: form.logo_url }} className="size-16" /> : <div className="grid size-16 shrink-0 place-items-center rounded-xl border border-dashed text-xs text-muted-foreground">未上传</div>}
          <div className="min-w-56 flex-1 space-y-2">
            <label className="block text-sm font-medium" htmlFor="store-logo-upload">门店 Logo</label>
            <Input id="store-logo-upload" aria-label="上传门店 Logo" type="file" accept="image/png,image/jpeg,image/webp" disabled={locked || logoUploading} className="h-11 file:mr-3 file:rounded-md file:border-0 file:bg-primary/10 file:px-3 file:py-2 file:text-xs file:text-primary" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadLogo(file); event.target.value = "" }} />
            <p className="text-xs leading-5 text-muted-foreground">支持 PNG、JPEG、WebP，不超过 10MB。统一压缩为 PNG 并保留原透明通道，保存后门店 Logo 与 favicon 同步更新。</p>
            {logoUploading && <p role="status" className="text-xs text-muted-foreground">正在上传并压缩图片…</p>}
            {logoError && <p role="alert" className="text-xs text-destructive">{logoError}</p>}
          </div>
        </div>
        {!value.id && <fieldset className="col-span-2 grid grid-cols-2 gap-4 rounded-lg border p-4">
          <legend className="px-1 text-sm font-medium">首位门店管理员</legend>
          <p className="col-span-2 text-xs leading-5 text-muted-foreground">管理员获得本店后台权限。首次登录使用账号密码和短信验证码，随后完成微信绑定；密码仅用于本次初始化，不在页面回显。</p>
          <label className="space-y-2 text-sm"><span>管理员账号</span><Input name="admin_username" required maxLength={150} autoComplete="off" value={form.admin_username} placeholder="例如 store_admin…" onChange={event => setForm({ ...form, admin_username: event.target.value })} /></label>
          <label className="space-y-2 text-sm"><span>管理员昵称</span><Input name="admin_nickname" required maxLength={50} autoComplete="name" value={form.admin_nickname} placeholder="例如 门店店长…" onChange={event => setForm({ ...form, admin_nickname: event.target.value })} /></label>
          <label className="space-y-2 text-sm"><span>登录密码</span><Input name="admin_password" required minLength={8} type="password" autoComplete="new-password" value={form.admin_password} placeholder="至少 8 位字母与数字…" onChange={event => setForm({ ...form, admin_password: event.target.value })} /></label>
          <label className="space-y-2 text-sm"><span>手机号</span><Input name="admin_phone" required type="tel" inputMode="numeric" maxLength={20} autoComplete="tel" value={form.admin_phone} placeholder="例如 13800138000…" onChange={event => setForm({ ...form, admin_phone: event.target.value.replace(/\D/g, "") })} /></label>
        </fieldset>}
      </div>
    </MutationDialog>
  )
}
