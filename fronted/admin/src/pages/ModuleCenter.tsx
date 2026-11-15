import { DataValue, ReadErrorNotice } from "@/components/page-data-state"
import { useEffect, useRef, useState } from "react"
import {
  SAAS_MODULES,
  SAAS_CAPABILITIES,
  DEFAULT_H5_BRANDING,
  H5_ENTITLEMENT_LABELS,
  h5BrandingPayload,
  h5Foreground,
  useCurrentStore,
  useResource,
  formatDateTime,
  randomUuid,
  getSession,
  type H5Branding,
  type ModuleContract,
} from "@xa/shared"
import { Check, Eye, Package, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { PageHeader, Status } from "@/components/business"
import { SaasField as Field } from "@/components/SaasField"
import { adminClient } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import {
  platformRequest,
  platformSession,
  type PlatformProfile,
} from "@/lib/platform-session"
import {
  readSaasPending,
  rememberSaasPending,
  clearSaasPending,
  verifySaasReceipt,
  type SaasReceipt,
} from "@/lib/saas-intent"
import "./saas.css"

type Club = { slug: string; name: string }
export default function StoreModulesPage() {
  const store = useCurrentStore(),
    auth = useAdminAuth()
  return (
    <ModuleCenter
      store={store}
      supported={
        store.capabilities?.includes(SAAS_CAPABILITIES.modules) === true
      }
      canEdit={auth.hasPerm("module:manage")}
    />
  )
}
export function PlatformModulesPage({
  profile,
  stores,
}: {
  profile: PlatformProfile
  stores: Club[]
}) {
  const [slug, setSlug] = useState("")
  const target = stores.find((store) => store.slug === slug)
  const superAdmin = profile.grants.some(
    (grant) => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*"
  )
  return (
    <div className="space-y-6">
      <PageHeader title="模块与购买管理" />
      <label className="flex items-center gap-3 text-sm">
        目标俱乐部
        <select
          className="saas-select w-72"
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
        >
          <option value="">选择俱乐部查看授权</option>
          {stores.map((store) => (
            <option key={store.slug} value={store.slug}>
              {store.name}
            </option>
          ))}
        </select>
      </label>
      {target ? (
        <ModuleCenter
          key={slug}
          store={target}
          platform
          supported={
            profile.capabilities?.includes(SAAS_CAPABILITIES.modules) === true
          }
          canEdit={superAdmin}
        />
      ) : (
        <ModuleBoundaries />
      )}
    </div>
  )
}
function ModuleBoundaries() {
  return (
    <section className="saas-section">
      <h2>模块交付边界</h2>
      <div className="overflow-x-auto">
        <table className="saas-table">
          <caption className="sr-only">模块、售卖和私人定制范围</caption>
          <thead>
            <tr>
              <th scope="col">产品模块</th>
              <th scope="col">购买方式</th>
              <th scope="col">适用场景</th>
              <th scope="col">私人定制</th>
            </tr>
          </thead>
          <tbody>
            {SAAS_MODULES.map((module) => (
              <tr key={module.code}>
                <th scope="row">{module.name}</th>
                <td>
                  <Badge variant="outline">{module.sale}</Badge>
                </td>
                <td>
                  {module.code === "ADMIN" ? "俱乐部由客服代下单、派单，需要统一跟进订单和售后。"
                    : module.code === "PROVIDER" ? "陪玩人员需要自行接单、提交履约凭证及查看收益。"
                    : "希望客户自行选服务、下单和查单，或需要自有品牌客户入口的俱乐部。"}
                </td>
                <td>
                  {module.customizable
                    ? "品牌、欢迎语、客户侧布局及展示"
                    : "平台统一规范"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="saas-note">
        老板端 H5
        定制沿用同一订单、报价、支付、权限与售后规则。后台与陪玩工作台的功能、角色、资金和状态流转由平台统一维护。
      </p>
    </section>
  )
}
function ModuleCenter({
  store,
  platform = false,
  supported,
  canEdit,
}: {
  store: Club
  platform?: boolean
  supported: boolean
  canEdit: boolean
}) {
  const pendingKey = `xa:saas:modules:${platform ? platformSession()?.sessionId : getSession("admin").sessionId}:${store.slug}`
  const saved = useRef(readSaasPending(pendingKey))
  const load = useResource<ModuleContract | null>(async () => {
    if (!supported) return null
    const result = platform
      ? await platformRequest<ModuleContract>(`saas/modules/${store.slug}`)
      : await adminClient.get<ModuleContract>("/saas/modules/")
    if (
      result.store_slug !== store.slug ||
      !result.version ||
      !result.h5 ||
      !Array.isArray(result.allowed_actions) ||
      !Array.isArray(result.requests)
    )
      throw new Error("模块授权信息不完整，请刷新后核对")
    h5BrandingPayload(result.branding)
    return result
  }, [store.slug, platform, supported])
  const [branding, setBranding] = useState<H5Branding>({
    ...DEFAULT_H5_BRANDING,
    display_name: store.name,
  })
  const [kind, setKind] = useState<"PURCHASE" | "CUSTOMIZATION">("PURCHASE")
  const [note, setNote] = useState("")
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(!!saved.current)
  const [review, setReview] = useState(false)
  const [dirty, setDirty] = useState(false)
  const locked = useRef(false)
  const intent = useRef(saved.current?.id || randomUuid())
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    if (load.data && !dirty) setBranding(load.data.branding)
  }, [load.data, dirty])
  const contract = load.data
  const h5 = contract?.h5
  const contractReadState = contract ? undefined : load.loading ? "loading" : load.error ? "error" : "unread"
  const action =
    kind === "PURCHASE" ? "REQUEST_PURCHASE" : "REQUEST_CUSTOMIZATION"
  const canSubmit =
    canEdit &&
    !!contract?.allowed_actions.includes(action) &&
    !load.loading &&
    !load.error
  const canSave =
    canEdit &&
    contract?.h5?.can_customize === true &&
    contract.allowed_actions.includes("SAVE_BRANDING") &&
    !load.loading &&
    !load.error
  const change = <K extends keyof H5Branding>(key: K, value: H5Branding[K]) => {
    setBranding((old) => ({ ...old, [key]: value }))
    setDirty(true)
    setReview(false)
    setSuccess("")
  }
  async function send(operation: "request" | "branding") {
    if (
      locked.current ||
      uncertain ||
      !contract ||
      (operation === "request" ? !canSubmit : !canSave)
    )
      return
    let data: Record<string, unknown>
    try {
      const clean = h5BrandingPayload(branding)
      if (operation === "request" && !note.trim())
        throw new Error("请填写购买或定制需求，方便平台核对")
      data = {
        client_request_id: intent.current,
        expected_version: contract.version,
        ...(operation === "branding"
          ? { branding: clean }
          : {
              kind,
              note: note.trim(),
              ...(kind === "CUSTOMIZATION" ? { branding: clean } : {}),
            }),
      }
    } catch (cause) {
      setError((cause as Error).message)
      return
    }
    locked.current = true
    setBusy(true)
    setError("")
    setSuccess("")
    try {
      rememberSaasPending(pendingKey, { id: intent.current, operation })
      const result = platform
        ? await platformRequest<ModuleContract>(
            `saas/modules/${store.slug}/${operation === "request" ? "requests" : "branding"}`,
            { method: "POST", data }
          )
        : await adminClient.post<ModuleContract>(
            `/saas/modules/${operation === "request" ? "requests" : "branding"}/`,
            data
          )
      if (!alive.current) return
      if (result.store_slug !== store.slug || !result.version)
        throw new Error("提交结果需核对，请刷新记录")
      clearSaasPending(pendingKey)
      intent.current = randomUuid()
      setReview(false)
      setDirty(false)
      setSuccess(
        operation === "branding"
          ? "品牌配置已保存，以发布结果为准。"
          : "需求已提交，开通与生效结果请查看处理记录。"
      )
      await load.reload()
    } catch (cause) {
      if (!alive.current) return
      const status = (cause as { status?: number }).status
      if (status && status < 500 && status !== 409) clearSaasPending(pendingKey)
      setUncertain(!status || status >= 500 || status === 409)
      setError((cause as Error).message || "提交结果未确认，请刷新处理记录核对")
    } finally {
      locked.current = false
      if (alive.current) setBusy(false)
    }
  }
  async function verify() {
    await load.reload()
    if (!uncertain || !supported) return
    try {
      const receipt = platform
        ? await platformRequest<SaasReceipt>(
            `saas/modules/${store.slug}/receipts/${intent.current}`
          )
        : await adminClient.get<SaasReceipt>(
            `/saas/modules/receipts/${intent.current}/`
          )
      if (!verifySaasReceipt(receipt, store.slug, intent.current)) {
        setError("本次提交仍在核对中，请稍后再查或联系平台")
        return
      }
      clearSaasPending(pendingKey)
      setUncertain(false)
      intent.current = randomUuid()
      if (receipt.status === "APPLIED") {
        setSuccess("已核对到本次操作成功回执。")
        setDirty(false)
        setError("")
      } else
        setError(receipt.message || "已确认本次操作未执行，可调整后重新提交")
    } catch (cause) {
      setError((cause as Error).message)
    }
  }
  return (
    <div className="saas-module-center space-y-6">
      {uncertain && (
        <p role="alert" className="rounded-md border p-4 text-sm">
          有一笔操作结果待核对。请点击核对授权，取得最终回执后继续。
        </p>
      )}
      <PageHeader title={platform ? `${store.name} · 模块授权` : "模块与品牌"}
        headingLevel={platform ? 2 : 1} tone={platform ? "plain" : "feature"}
        description={platform ? "核对本俱乐部老板端 H5 的授权与品牌草稿，发布范围仅限客户侧。" : undefined}>
        <Button
          variant="outline"
          disabled={load.loading}
          onClick={() => void verify()}
        >
          <RefreshCw size={16} />
          核对授权
        </Button>
      </PageHeader>
      <ModuleBoundaries />
      <ReadErrorNotice error={load.error?.message} retry={load.reload} hasSnapshot={!!contract} />
      <section className="saas-section">
        <div className="flex items-center justify-between gap-4">
          <h2>
            <Package className="inline size-5 align-text-bottom" /> 老板端 H5
            授权
          </h2>
          <Status value={h5?.status} label={h5?.status ? H5_ENTITLEMENT_LABELS[h5.status] || "授权待核对" : undefined}
            tone={h5?.status === "SCHEDULED" ? "warning" : h5?.status === "SUSPENDED" || h5?.status === "NOT_PURCHASED" ? "muted" : undefined}
            readState={contractReadState} />
        </div>
        <dl className="saas-definition">
            <div>
              <dt>开通时间</dt>
              <dd><DataValue known={!!contract} loading={load.loading}>{formatDateTime(h5?.starts_at)}</DataValue></dd>
            </div>
            <div>
              <dt>结束时间</dt>
              <dd><DataValue known={!!contract} loading={load.loading}>{formatDateTime(h5?.ends_at)}</DataValue></dd>
            </div>
            <div>
              <dt>自主下单</dt>
              <dd><Status value={h5?.can_create_order == null ? undefined : h5.can_create_order ? "ENABLED" : "DISABLED"} label={h5?.can_create_order == null ? undefined : h5.can_create_order ? "允许" : "暂未开放"} readState={contractReadState} /></dd>
            </div>
            <div>
              <dt>品牌定制</dt>
              <dd><Status value={h5?.can_customize == null ? undefined : h5.can_customize ? "ENABLED" : "DISABLED"} label={h5?.can_customize == null ? undefined : h5.can_customize ? "允许" : "暂未开放"} readState={contractReadState} /></dd>
            </div>
          </dl>
        {!contract && <p className="saas-note">
            {supported
              ? "暂未取得有效授权信息，请核对后继续。"
              : "模块购买与开通服务尚未开放，请联系平台确认授权。"}{" "}
            客服派单、陪玩履约和既有订单继续按当前业务授权处理。
          </p>}
        <p className="saas-note">
          H5 模块与私人定制分别按需报价。SaaS License 年费和账号配额单独管理，实际费用与交付时间由平台确认。
        </p>
      </section>
      <div className="saas-customization-grid">
        <section className="saas-section">
          <h2>老板端 H5 品牌定制</h2>
          <p className="saas-note">
            {!contract ? "已保存品牌尚未读取，以下仅为本地草稿，不代表当前线上配置。" : "预览仅展示当前草稿，保存并发布后才影响老板端。"}
          </p>
          <fieldset
            disabled={busy || uncertain || !canEdit}
            className="mt-4 grid gap-4"
          >
            <Field label="客户侧品牌名称">
              <Input
                value={branding.display_name}
                maxLength={30}
                onChange={(event) => change("display_name", event.target.value)}
              />
            </Field>
            <Field label="品牌主色">
              <div className="flex items-center gap-3">
                <input
                  aria-label="选择品牌主色"
                  className="h-10 w-14 cursor-pointer rounded-md border bg-background"
                  type="color"
                  value={branding.primary_color}
                  onChange={(event) =>
                    change("primary_color", event.target.value)
                  }
                />
                <Input
                  aria-label="品牌颜色值"
                  value={branding.primary_color}
                  maxLength={7}
                  onChange={(event) =>
                    change("primary_color", event.target.value)
                  }
                />
              </div>
            </Field>
            <Field label="首页欢迎语">
              <Textarea
                value={branding.welcome}
                maxLength={60}
                onChange={(event) => change("welcome", event.target.value)}
              />
            </Field>
            <Field label="客服入口名称">
              <Input
                value={branding.support_label}
                maxLength={12}
                onChange={(event) =>
                  change("support_label", event.target.value)
                }
              />
            </Field>
            <Field label="客户侧展示密度">
              <select
                className="saas-select"
                value={branding.density}
                onChange={(event) =>
                  change("density", event.target.value as H5Branding["density"])
                }
              >
                <option value="comfortable">舒适</option>
                <option value="compact">紧凑</option>
              </select>
            </Field>
          </fieldset>
          <p className="saas-note">
            后台与陪玩端保持平台统一的字体、组件、状态色及交互。定制不包含派单算法、资金规则、权限或自定义脚本。
          </p>
          <Button
            className="mt-4"
            variant="outline"
            disabled={busy || !canSave || uncertain}
            onClick={() => {
              try {
                h5BrandingPayload(branding)
                setError("")
                setReview(true)
              } catch (cause) {
                setError((cause as Error).message)
              }
            }}
          >
            <Eye size={16} />
            核对品牌配置
          </Button>
          {review && (
            <div className="mt-4 space-y-3 rounded-md border p-4">
              <p className="text-sm">
                将为「{store.name}」保存老板端 H5
                品牌配置，后台与陪玩工作台保持平台规范。
              </p>
              <Button
                disabled={busy || uncertain || !canSave}
                onClick={() => void send("branding")}
              >
                {busy ? "正在保存…" : "确认保存品牌配置"}
              </Button>
            </div>
          )}
        </section>
        <aside
          className="saas-h5-preview"
          aria-label="老板端 H5 品牌草稿预览"
          style={
            {
              "--h5-preview-primary": /^#[0-9a-f]{6}$/i.test(
                branding.primary_color
              )
                ? branding.primary_color
                : "#2563EB",
              "--h5-preview-foreground": h5Foreground(
                /^#[0-9a-f]{6}$/i.test(branding.primary_color)
                  ? branding.primary_color
                  : "#2563EB"
              ),
            } as React.CSSProperties
          }
        >
          <p className="mb-3 text-xs text-muted-foreground">
            品牌草稿预览 · 未发布
          </p>
          <div className="saas-preview-screen" data-density={branding.density}>
            <header>
              {branding.display_name || store.name}
              <span>服务中心</span>
            </header>
            <div className="saas-preview-welcome">
              <h3>{branding.welcome || "欢迎来到俱乐部"}</h3>
              <p>找陪玩 · 选服务 · 跟进订单</p>
            </div>
            <div className="saas-preview-content">
              <h4>为你安排每一局</h4>
              <p>服务与价格由本店维护</p>
              <span className="saas-preview-action">
                {branding.support_label || "联系客服"}
              </span>
            </div>
            <footer>首页　　陪玩　　订单　　消息　　我的</footer>
          </div>
        </aside>
      </div>
      <section className="saas-section">
        <h2>购买与私人定制需求</h2>
        <form
          className="mt-4 grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void send("request")
          }}
        >
          <fieldset
            disabled={busy || uncertain || !canEdit}
            className="grid gap-4"
          >
            <Field label="需求类型">
              <select
                className="saas-select"
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value as typeof kind)
                  setSuccess("")
                }}
              >
                <option value="PURCHASE">单独购买老板端 H5</option>
                <option value="CUSTOMIZATION">老板端 H5 私人定制</option>
              </select>
            </Field>
            <Field label="具体需求">
              <Textarea
                required
                value={note}
                maxLength={2000}
                placeholder="填写使用场景、客户侧品牌或页面需求"
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>
          </fieldset>
          <p className="saas-note">
            {kind === "PURCHASE"
              ? "适合准备开放客户自助下单的俱乐部。说明需要开放的服务和期望启用时间，供平台报价。"
              : "适合需要自有品牌客户入口的俱乐部。说明品牌、首页展示或内容需求，供平台评估定制费用。"}
            提交后可在处理记录中核对进展。
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={busy || uncertain || !canSubmit}>
              {busy ? "提交中…" : "提交需求"}
            </Button>
            {!canSubmit && (
              <span className="saas-note">
                {canEdit
                  ? "需求受理暂未开放，请联系平台。"
                  : "当前身份仅可查看，请由俱乐部管理员处理。"}
              </span>
            )}
          </div>
        </form>
      </section>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
          {uncertain && "。操作结果待核对，请使用上方核对授权，不要重复提交。"}
        </p>
      )}
      {success && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <Check size={16} />
          {success}
        </p>
      )}
      <section className="saas-section">
        <h2>处理记录</h2>
          <table className="saas-table">
            <thead>
              <tr>
                <th scope="col">类型</th>
                <th scope="col">状态</th>
                <th scope="col">提交时间</th>
                <th scope="col">需求</th>
              </tr>
            </thead>
            <tbody>
              {(contract?.requests || []).map((request) => (
                <tr key={request.id}>
                  <td>{request.kind === "PURCHASE" ? "购买 H5" : request.kind === "CUSTOMIZATION" ? "H5 定制" : "—"}</td>
                  <td>
                    <Status value={request.status} label={(
                      {
                        SUBMITTED: "待受理",
                        REVIEWING: "受理中",
                        APPROVED: "已确认",
                        REJECTED: "已退回",
                        DELIVERED: "已交付",
                      } as Record<string, string>
                    )[request.status]} tone={request.status === "DELIVERED" ? "success" : request.status === "SUBMITTED" ? "warning" : undefined} />
                  </td>
                  <td>{formatDateTime(request.created_at)}</td>
                  <td className="max-w-lg break-words whitespace-pre-wrap">
                    {request.note}
                  </td>
                </tr>
              ))}
              {!contract?.requests.length && <tr><td colSpan={4}>{contract ? "暂无购买或定制需求。" : <Status readState={contractReadState} />}</td></tr>}
            </tbody>
          </table>
      </section>
    </div>
  )
}
