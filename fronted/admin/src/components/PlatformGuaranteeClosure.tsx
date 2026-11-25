import { useMemo, useState } from "react"
import { toast } from "sonner"
import { formatRmb, parseRmbFen, randomUuid, useResource } from "@xa/shared"
import { formatDateTime } from "@/lib/format"
import { platformRequest, platformSession, type PlatformProfile, type PlatformStore } from "@/lib/platform-session"
import {
  CLOSURE_REASON_CATEGORIES,
  createClosureExpiryIntent,
  createClosureStartIntent,
  createContractIntent,
  createGuaranteeEventIntent,
  parseClosureExpiryResult,
  parseClosureStartResult,
  parseClosureSummary,
  parseContractResult,
  parseGuaranteeEventResult,
  parseGuaranteeSummary,
  parsePlatformWriteIntent,
  platformGuaranteeClosureAccess,
  platformSnapshotMode,
  platformWriteFailure,
  platformWriteMessage,
  recoverPlatformWrite,
  submitPlatformWrite,
  type ClosureExpiryResult,
  type ClosureStartResult,
  type ClosureSummary,
  type GuaranteeContract,
  type GuaranteeEvent,
  type GuaranteeSummary,
  type PlatformWriteContext,
  type PlatformWriteIntent,
  type PlatformWriteResult,
} from "@/lib/platform-guarantee-closure"
import { Feedback, MutationDialog, ReadOnlyNotice, Status } from "@/components/business"
import PlatformClosureReturns from "@/components/PlatformClosureReturns"
import PlatformClosureReadiness from "@/components/PlatformClosureReadiness"
import { ReadErrorNotice } from "@/components/page-data-state"
import { PlatformStoreStatus as StoreStatusBadge } from "@/components/PlatformStoreStatus"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

const reasonLabels: Record<(typeof CLOSURE_REASON_CATEGORIES)[number], string> = {
  OPERATOR_REQUEST: "经营主体申请",
  LICENSE_TERMINATION: "授权终止",
  PLATFORM_DIRECTED: "平台要求",
  OTHER: "其他",
}
const operationLabels: Record<PlatformWriteIntent["operation"], string> = {
  contract: "配置保证金合同",
  "guarantee-event": "登记线下收退凭证",
  "closure-start": "发起闭店公示",
  "closure-expiry": "确认公示到期",
}
type StoreTarget = Pick<PlatformStore, "id" | "slug" | "name"> & { status: string }

async function readGuarantee(store: StoreTarget) {
  return parseGuaranteeSummary(await platformRequest<unknown>(`store-management/${store.id}/guarantee`), { storeSlug: store.slug })
}
async function readClosure(store: StoreTarget) {
  return parseClosureSummary(await platformRequest<unknown>(`store-management/${store.id}/closure`), { storeSlug: store.slug })
}
async function sendWrite(intent: PlatformWriteIntent): Promise<PlatformWriteResult> {
  if (intent.operation === "contract") {
    return parseContractResult(await platformRequest<unknown>(`store-management/${intent.storeId}/guarantee`, { method: "PUT", data: intent.payload }), intent)
  }
  if (intent.operation === "guarantee-event") {
    return parseGuaranteeEventResult(await platformRequest<unknown>(`store-management/${intent.storeId}/guarantee/events`, { method: "POST", data: intent.payload }), intent)
  }
  if (intent.operation === "closure-start") {
    return parseClosureStartResult(await platformRequest<unknown>(`store-management/${intent.storeId}/closure`, { method: "POST", data: intent.payload }))
  }
  return parseClosureExpiryResult(await platformRequest<unknown>(`store-management/${intent.storeId}/closure/confirm-expiry`, { method: "POST", data: intent.payload }), intent)
}

export function PlatformGuaranteeClosureDirectory({ stores, profile, revision }: { stores: PlatformStore[]; profile: PlatformProfile; revision: number }) {
  const [selected, setSelected] = useState<PlatformStore | null>(null)
  const allowed = stores.filter(store => {
    const access = platformGuaranteeClosureAccess(profile, store.slug)
    return access.guaranteeView || access.closureView
  })
  return <section className="mt-6 space-y-4 rounded-lg border p-4" key={`${profile.id}:${revision}`}>
    <div><h2 className="text-lg font-semibold">门店保证金与闭店</h2><p className="text-sm text-muted-foreground">按平台账号的门店 scope 核对保证金和闭店；财务角色不因此获得合同配置或闭店管理权限。</p></div>
    {allowed.length ? <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b"><th className="p-2">门店</th><th>状态</th><th>店币</th><th className="text-right">操作</th></tr></thead><tbody>{allowed.map(store => <tr key={store.id} className="border-b last:border-0"><td className="p-2">{store.name}<p className="text-xs text-muted-foreground">{store.slug} · ID {store.id}</p></td><td><StoreStatusBadge value={store.status} /></td><td>{store.currency_name}</td><td className="text-right"><Button size="sm" variant="outline" onClick={() => setSelected(store)}>保证金与闭店</Button></td></tr>)}</tbody></table></div> : <p role="status" className="rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">当前平台授权范围内没有可查看的门店。</p>}
    {selected && <PlatformGuaranteeClosure key={`${selected.id}:${revision}`} store={selected} profile={profile} revision={revision} onClose={() => setSelected(null)} />}
  </section>
}

export default function PlatformGuaranteeClosure({ store, profile, revision, onClose }: { store: StoreTarget; profile: PlatformProfile; revision: number; onClose: () => void }) {
  const session = platformSession()
  const access = platformGuaranteeClosureAccess(profile, store.slug)
  const scopeKey = `${store.id}:${store.slug}:${profile.id}:${session?.sessionId}:${access.scopeSignature}:${revision}`
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}><DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-6xl">
    <DialogHeader><DialogTitle>保证金与闭店 · {store.name}</DialogTitle><DialogDescription>{store.slug} · 门店 ID {store.id}。所有金额均为人民币分；线下凭证登记不代表银行或支付渠道成功。</DialogDescription></DialogHeader>
    {session ? <PlatformGuaranteeClosureContent key={scopeKey} store={store} profile={profile} sessionId={session.sessionId} onClose={onClose} /> : <Feedback message="平台登录已失效，请重新登录" />}
  </DialogContent></Dialog>
}

function PlatformGuaranteeClosureContent({ store, profile, sessionId, onClose }: { store: StoreTarget; profile: PlatformProfile; sessionId: string; onClose: () => void }) {
  const access = platformGuaranteeClosureAccess(profile, store.slug)
  const context = useMemo<PlatformWriteContext>(() => ({ storeId: store.id, storeSlug: store.slug, actorId: profile.id, sessionId }), [store.id, store.slug, profile.id, sessionId])
  const storageKey = `xa:fronted:platform:guarantee-closure:${profile.id}:${sessionId}:${store.id}`
  const restored = useMemo(() => {
    try { return { intent: typeof sessionStorage === "undefined" ? null : parsePlatformWriteIntent(sessionStorage.getItem(storageKey), context), error: "" } }
    catch (cause) { return { intent: null, error: cause instanceof Error ? cause.message : "待核对请求无法读取" } }
  }, [storageKey, context])
  const [pending, setPending] = useState<PlatformWriteIntent | null>(restored.intent)
  const [recoveryError, setRecoveryError] = useState(restored.error)
  const [recoveryNotFound, setRecoveryNotFound] = useState(false)
  const [recoveryMessage, setRecoveryMessage] = useState("")
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const [writeDenied, setWriteDenied] = useState(false)
  const [guaranteeDenied, setGuaranteeDenied] = useState(false)
  const [closureDenied, setClosureDenied] = useState(false)
  const [tab, setTab] = useState<"guarantee" | "closure" | "returns">(access.guaranteeView ? "guarantee" : "closure")
  const [editor, setEditor] = useState<"contract" | "event" | "closure-start" | "closure-expiry" | null>(null)
  const [compensates, setCompensates] = useState<GuaranteeEvent | null>(null)

  const guarantee = useResource<GuaranteeSummary | null>(
    () => access.guaranteeView && !guaranteeDenied ? readGuarantee(store).catch(cause => { if ((cause as { status?: number })?.status === 403) { setGuaranteeDenied(true); setWriteDenied(true) }; throw cause }) : Promise.resolve(null),
    [store.id, store.slug, access.scopeSignature, guaranteeDenied],
  )
  const closure = useResource<ClosureSummary | null>(
    () => access.closureView && !closureDenied ? readClosure(store).catch(cause => { if ((cause as { status?: number })?.status === 403) { setClosureDenied(true); setWriteDenied(true) }; throw cause }) : Promise.resolve(null),
    [store.id, store.slug, access.scopeSignature, closureDenied],
  )
  const canConfigure = access.guaranteeConfigure && !writeDenied && !guaranteeDenied
  const canRecord = access.guaranteeRecord && !writeDenied && !guaranteeDenied
  const canManageClosure = access.closureManage && !writeDenied && !closureDenied
  const guaranteeMode = platformSnapshotMode({ hasSnapshot: !!guarantee.data, loading: guarantee.loading, error: guarantee.error?.message || "" })
  const closureMode = platformSnapshotMode({ hasSnapshot: !!closure.data, loading: closure.loading, error: closure.error?.message || "" })
  const contract = guarantee.data?.contract || null
  const closureData = closure.data
  const writeBlocked = !!pending || !!recoveryError || busy
  const pendingWriteAllowed = !pending || (pending.operation === "contract" ? canConfigure : pending.operation === "guarantee-event" ? canRecord : canManageClosure)
  const pendingReadAllowed = !pending || (pending.operation === "contract" || pending.operation === "guarantee-event" ? access.guaranteeView && !guaranteeDenied : access.closureView && !closureDenied)

  function saveIntent(intent: PlatformWriteIntent) {
    try { sessionStorage.setItem(storageKey, JSON.stringify(intent)) }
    catch { throw Object.assign(new Error("浏览器无法保存原请求，本次未发送；请允许会话存储后重试"), { localCode: "STORAGE_UNAVAILABLE" }) }
    setPending(intent); setRecoveryError(""); setRecoveryNotFound(false); setRecoveryMessage("")
  }
  function clearIntent() {
    sessionStorage.removeItem(storageKey); setPending(null); setRecoveryNotFound(false); setRecoveryMessage("")
  }
  async function reloadFor(operation: PlatformWriteIntent["operation"]) {
    if (operation === "closure-start" || operation === "closure-expiry") await closure.reload()
    else await guarantee.reload()
  }
  async function runIntent(intent: PlatformWriteIntent, recovering = false) {
    if (busy || writeBlocked && !recovering || (recovering && !pendingWriteAllowed)) return
    setBusy(true); setActionError("")
    try {
      const result = await submitPlatformWrite(intent, { save: saveIntent, send: sendWrite, clear: clearIntent }, { recovering })
      setEditor(null); setCompensates(null)
      if (intent.operation === "contract") toast.success(`保证金合同已登记为 v${(result as GuaranteeContract).version}`)
      else if (intent.operation === "guarantee-event") toast.success("线下凭证已登记；channel_status=NOT_REQUESTED，未发起或确认支付渠道结果")
      else if (intent.operation === "closure-start") {
        const receipt = result as ClosureStartResult
        toast.success(receipt.outcome === "EXISTING" ? "已有闭店公示，本次未创建或重置倒计时" : "闭店 30 天公示已登记，原倒计时不可撤回或重置")
      } else {
        const receipt = result as ClosureExpiryResult
        toast.success(receipt.outcome === "EXISTING" ? "原公示已有到期确认，本次未新增事件" : "已确认进入待结清；门店未因此自动停用，未执行余额返还")
      }
      await reloadFor(intent.operation)
    } catch (cause) {
      const localCode = (cause as { localCode?: string })?.localCode
      setActionError(localCode === "STORAGE_UNAVAILABLE" ? (cause as Error).message : platformWriteMessage(cause, recovering))
      if ((cause as { status?: number })?.status === 403) setWriteDenied(true)
      if ((cause as { status?: number })?.status === 409) await reloadFor(intent.operation)
      throw cause
    } finally { setBusy(false) }
  }
  async function recover() {
    if (!pending || busy || !pendingReadAllowed) return
    setBusy(true); setActionError(""); setRecoveryMessage(""); setRecoveryNotFound(false)
    try {
      const result = await recoverPlatformWrite(pending, { guarantee: () => readGuarantee(store), closure: () => readClosure(store) })
      setRecoveryMessage(result.message || "")
      if (result.status === "FOUND") { clearIntent(); toast.success("已按原请求号找到匹配记录，未重复登记"); await reloadFor(pending.operation) }
      else if (result.status === "ALREADY_EXISTS") { clearIntent(); await reloadFor(pending.operation) }
      else if (result.status === "NOT_FOUND") { setRecoveryNotFound(true); setRecoveryMessage("只读查询未找到原请求结果。如仍需继续，只能使用原请求号和完整内容重试。") }
    } catch (cause) {
      setActionError(`${cause instanceof Error ? cause.message : "恢复读取失败"}；原请求仍保留，不换号重试。`)
      if ((cause as { status?: number })?.status === 403) setWriteDenied(true)
    } finally { setBusy(false) }
  }
  function openEditor(kind: "contract" | "event" | "closure-start" | "closure-expiry", original: GuaranteeEvent | null = null) {
    if (writeBlocked || writeDenied) return
    setActionError(""); setCompensates(original); setEditor(kind)
  }

  return <div className="space-y-4 p-6">
    {recoveryError && <Feedback message={recoveryError} />}
    {writeDenied && <Feedback message="服务端已拒绝当前平台权限，旧页面中的写入能力已清除。请等待授权刷新或重新进入。" />}
    {pending && <section role="alert" className="space-y-3 rounded-xl border border-amber-500/50 bg-amber-500/5 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">{operationLabels[pending.operation]}结果待核</h3><p className="mt-1 break-all font-mono text-xs">{pending.client_request_id}</p><p className="mt-1 text-xs text-muted-foreground">{pending.storeSlug} · 门店 ID {pending.storeId}</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void recover()} disabled={busy || !pendingReadAllowed}>只读恢复原请求</Button>{recoveryNotFound && <Button onClick={() => void runIntent(pending, true)} disabled={busy || !pendingWriteAllowed || (pending.operation === "closure-expiry" && closureData?.status !== "SETTLEMENT_PENDING")}>原号原内容重试</Button>}</div></div>{recoveryMessage && <p className="text-sm text-muted-foreground">{recoveryMessage}</p>}{pending.operation === "contract" && <p className="text-xs text-muted-foreground">合同 PUT 不接收请求编号；本地编号仅用于保存和恢复流程，版本匹配也必须复核平台审计。</p>}</section>}
    <Feedback message={actionError} />
    <Tabs value={tab} onValueChange={value => setTab(value as "guarantee" | "closure" | "returns")}><TabsList>{access.guaranteeView && <TabsTrigger value="guarantee">保证金合同与收退</TabsTrigger>}{access.closureView && <TabsTrigger value="closure">闭店 30 天公示</TabsTrigger>}{access.closureView && <TabsTrigger value="returns">余额返还</TabsTrigger>}</TabsList></Tabs>
    {tab === "guarantee" && access.guaranteeView && <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">保证金合同额度与线下凭证</h3><p className="text-xs text-muted-foreground">已入账事件不可修改；差错使用方向相反、金额相同的补偿事件。</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void guarantee.reload()} disabled={guarantee.loading || busy}>刷新保证金</Button>{canConfigure && <Button size="sm" variant="outline" onClick={() => openEditor("contract")} disabled={writeBlocked || guaranteeMode !== "ready"}>{contract ? "调整合同额度" : "配置合同额度"}</Button>}{canRecord && <Button size="sm" onClick={() => openEditor("event")} disabled={writeBlocked || !contract || guaranteeMode !== "ready"}>登记线下缴纳／退还</Button>}</div></div>
      {!access.guaranteeConfigure && !access.guaranteeRecord && <ReadOnlyNotice canEdit={false} subject="保证金合同与收退事件" />}
      <ReadErrorNotice error={guarantee.error?.message} retry={() => void guarantee.reload()} hasSnapshot={!!guarantee.data} />
      {guaranteeMode === "stale" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">当前保证金为旧快照，合同版本、净额和凭证状态可能已经过期，写入已暂停。</p>}
      {guaranteeMode === "loading" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">正在读取本店保证金合同与事件…</p> : guaranteeMode === "error" ? <p className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">保证金尚未读取，金额保持待核，不显示为零。</p> : guarantee.data ? <GuaranteeView summary={guarantee.data} canRecord={canRecord && guaranteeMode === "ready" && !writeBlocked} onCompensate={event => openEditor("event", event)} /> : null}
    </section>}
    {tab === "closure" && access.closureView && <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">闭店公示与到期证据</h3><p className="text-xs text-muted-foreground">闭店公示不可撤回；待结清不等于已停用、已退款或最终结清。</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void closure.reload()} disabled={closure.loading || busy}>刷新闭店状态</Button>{canManageClosure && closureData?.status === "NOT_STARTED" && <Button size="sm" variant="destructive" onClick={() => openEditor("closure-start")} disabled={writeBlocked || closureData.store_status !== "ACTIVE" || closureMode !== "ready"}>发起 30 天闭店公示</Button>}{canManageClosure && closureData?.notice && !closureData.expiry_event && <Button size="sm" onClick={() => openEditor("closure-expiry")} disabled={writeBlocked || closureData.status !== "SETTLEMENT_PENDING" || closureMode !== "ready"}>{closureData.status === "NOTICE_ACTIVE" ? "公示期满后确认" : "确认公示已到期"}</Button>}</div></div>
      {!access.closureManage && <ReadOnlyNotice canEdit={false} subject="闭店公示和到期证据" />}
      <ReadErrorNotice error={closure.error?.message} retry={() => void closure.reload()} hasSnapshot={!!closure.data} />
      {closureMode === "stale" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">当前闭店状态为旧快照，到期资格可能已经变化，操作已暂停。</p>}
      {closureMode === "loading" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">正在读取闭店公示与控制库时钟证据…</p> : closureMode === "error" ? <p className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">闭店状态尚未读取，不推定未发起或已到期。</p> : closureData ? <ClosureView data={closureData} /> : null}
      <PlatformClosureReadiness store={store} profile={profile} sessionId={sessionId} onAuthorizationDenied={() => { setClosureDenied(true); setWriteDenied(true) }} />
    </section>}
    {tab === "returns" && access.closureView && <PlatformClosureReturns store={store} profile={profile} sessionId={sessionId} onAuthorizationDenied={() => { setClosureDenied(true); setWriteDenied(true) }} />}
    <div className="flex justify-end border-t pt-4"><Button variant="outline" onClick={onClose} disabled={busy}>关闭</Button></div>
    {editor === "contract" && <ContractEditor store={store} context={context} contract={contract} onClose={() => setEditor(null)} onSubmit={runIntent} />}
    {editor === "event" && contract && <EventEditor context={context} contract={contract} original={compensates} onClose={() => { setEditor(null); setCompensates(null) }} onSubmit={runIntent} />}
    {editor === "closure-start" && <ClosureStartEditor context={context} onClose={() => setEditor(null)} onSubmit={runIntent} />}
    {editor === "closure-expiry" && closureData?.notice && <ClosureExpiryEditor context={context} notice={closureData.notice} onClose={() => setEditor(null)} onSubmit={runIntent} />}
  </div>
}

function money(value: number | null) {
  return value === null ? "待核" : `¥${formatRmb(value)}`
}
function GuaranteeView({ summary, canRecord, onCompensate }: { summary: GuaranteeSummary; canRecord: boolean; onCompensate: (event: GuaranteeEvent) => void }) {
  const contract = summary.contract, publicStatus = summary.public_status
  return <><div className="grid gap-3 md:grid-cols-5">{[["合同应收", contract?.amount_due_fen ?? null], ["累计缴纳", contract?.amount_received_fen ?? null], ["累计退还", contract?.amount_refunded_fen ?? null], ["已收净额", contract?.net_received_fen ?? null], ["剩余应缴", contract?.amount_remaining_fen ?? null]].map(([label, value]) => <div key={label as string} className="rounded-lg bg-muted/20 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{money(value as number | null)}</p></div>)}</div>
    {contract ? <div className="rounded-lg border p-4 text-sm"><div className="flex flex-wrap items-center gap-2"><strong>合同 v{contract.version}</strong><Badge variant="outline">{contract.status}</Badge><span className="text-xs text-muted-foreground">{contract.store_slug}</span></div><dl className="mt-3 grid gap-2 text-xs md:grid-cols-2"><div><dt className="text-muted-foreground">合同 ID</dt><dd className="break-all font-mono">{contract.id}</dd></div><div><dt className="text-muted-foreground">来源 License 合同</dt><dd className="break-all font-mono">{contract.source_license_contract_id || "未关联"}</dd></div><div><dt className="text-muted-foreground">最后更新</dt><dd>{formatDateTime(contract.updated_at)} · {contract.updated_by.nickname || contract.updated_by.username}</dd></div></dl></div> : <p role="status" className="rounded-lg bg-muted/20 p-4 text-sm text-muted-foreground">尚未配置保证金合同应收；金额为待核，而不是零。</p>}
    <div className="rounded-lg border p-3 text-xs"><span className="font-medium">公开投影：{publicStatus.label}</span><span className="ml-2 text-muted-foreground">{publicStatus.status} · 应收 {money(publicStatus.amount_due_fen)} · 净额 {money(publicStatus.net_received_fen)} · 剩余 {money(publicStatus.amount_remaining_fen)}</span></div>
    <div className="overflow-x-auto"><table className="w-full min-w-[1080px] text-left text-sm"><caption className="sr-only">线下收退事件，channel_status 均为 NOT_REQUESTED</caption><thead><tr className="border-b text-xs text-muted-foreground"><th className="p-2">业务键 / 方向</th><th>金额</th><th>发生时间</th><th>凭证摘要与安全引用</th><th>合同版本 / 登记人</th><th>渠道状态</th><th className="text-right">补偿</th></tr></thead><tbody>{summary.events.map(event => { const compensated = summary.events.some(row => row.compensates_event_id === event.id); return <tr key={event.id} className="border-b last:border-0"><td className="p-2"><span className="font-mono text-xs">{event.business_key}</span><p>{event.direction === "RECEIPT" ? "线下缴纳" : "线下退还"}{event.compensates_event_id ? " · 补偿事件" : ""}</p></td><td>{money(event.amount_fen)}</td><td>{formatDateTime(event.occurred_at)}</td><td className="max-w-64 break-all text-xs"><span title={event.evidence_sha256}>{event.evidence_sha256.slice(0, 12)}…</span><p>{event.evidence_ref}</p></td><td>v{event.contract_version}<p className="text-xs">{event.created_by.nickname || event.created_by.username}</p></td><td><Badge variant="outline">NOT_REQUESTED</Badge></td><td className="text-right">{canRecord && !compensated ? <Button size="sm" variant="outline" onClick={() => onCompensate(event)}>登记补偿</Button> : compensated ? <span className="text-xs text-muted-foreground">已有补偿</span> : null}</td></tr> })}{!summary.events.length && <tr><td colSpan={7} className="p-6 text-center text-muted-foreground">暂无收退事件。</td></tr>}</tbody></table></div></>
}
function ClosureView({ data }: { data: ClosureSummary }) {
  const notice = data.notice, expiry = data.expiry_event
  return <><div className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/20 p-4"><Status value={data.status} label={data.status === "NOT_STARTED" ? "未发起" : data.status === "NOTICE_ACTIVE" ? "30 天公示中" : data.status === "SETTLEMENT_PENDING" ? "待结清" : "证据／时钟待核"} tone={data.status === "SETTLEMENT_PENDING" ? "warning" : data.status === "NOTICE_ACTIVE" ? "primary" : "muted"} /><StoreStatusBadge value={data.store_status} /><span className="text-xs text-muted-foreground">待结清不覆盖门店实际状态，不代表已停用或已退款。</span></div>{notice ? <div className="space-y-4 rounded-lg border p-4"><div><strong>{reasonLabels[notice.reason_category]}</strong><p className="mt-2 whitespace-pre-wrap break-words text-sm">{notice.reason}</p></div><dl className="grid gap-3 text-xs md:grid-cols-2"><div><dt className="text-muted-foreground">公示开始</dt><dd>{formatDateTime(notice.started_at)}</dd></div><div><dt className="text-muted-foreground">最早结清时点</dt><dd>{formatDateTime(notice.earliest_settlement_at)}</dd></div><div><dt className="text-muted-foreground">政策与天数</dt><dd>{notice.notice_policy_version} · {notice.notice_days} 天</dd></div><div><dt className="text-muted-foreground">申请人</dt><dd>{notice.applicant.nickname || notice.applicant.username}</dd></div><div><dt className="text-muted-foreground">请求编号</dt><dd className="break-all font-mono">{notice.client_request_id}</dd></div><div><dt className="text-muted-foreground">控制库时钟</dt><dd className="break-all">{notice.clock_source} · {notice.clock_identity}</dd></div></dl></div> : <p role="status" className="rounded-lg bg-muted/20 p-6 text-center text-sm text-muted-foreground">尚未发起闭店公示。</p>}{expiry && <div className="rounded-lg border p-4 text-sm"><strong>公示到期确认事件</strong><p className="mt-2 whitespace-pre-wrap">{expiry.reason}</p><dl className="mt-3 grid gap-2 text-xs md:grid-cols-2"><div><dt className="text-muted-foreground">确认时点</dt><dd>{formatDateTime(expiry.occurred_at)}</dd></div><div><dt className="text-muted-foreground">确认人</dt><dd>{expiry.actor.nickname || expiry.actor.username}</dd></div><div><dt className="text-muted-foreground">请求编号</dt><dd className="break-all font-mono">{expiry.client_request_id}</dd></div><div><dt className="text-muted-foreground">时钟证据</dt><dd className="break-all">{expiry.clock_source} · {expiry.clock_identity}</dd></div></dl></div>}<p className="text-xs text-muted-foreground">本页不提供闭店撤回或最终停用入口；逐户余额返还在独立页签办理。新增业务入口的闭店守卫覆盖范围仍需统一联调。</p></>
}

type EditorProps = { context: PlatformWriteContext; onClose: () => void; onSubmit: (intent: PlatformWriteIntent, recovering?: boolean) => Promise<void> }
function editorCatch(cause: unknown, onClose: () => void) {
  const failure = platformWriteFailure(cause)
  if ((cause as { localCode?: string })?.localCode !== "STORAGE_UNAVAILABLE" && (failure.kind === "unknown" || failure.kind === "denied" || failure.kind === "conflict")) onClose()
}
function ContractEditor({ context, contract, onClose, onSubmit }: EditorProps & { store: StoreTarget; contract: GuaranteeContract | null }) {
  const [amount, setAmount] = useState(contract ? (contract.amount_due_fen / 100).toFixed(2) : ""), [reason, setReason] = useState(""), [source, setSource] = useState(contract?.source_license_contract_id || ""), [reviewed, setReviewed] = useState(false)
  return <MutationDialog title={contract ? `调整合同额度（当前 v${contract.version}）` : "配置保证金合同应收"} description="合同使用 expected_version 并发控制。后端不接收 client_request_id；前端仍在写前保存本地请求编号与完整请求，用于未知结果恢复。" onClose={onClose} submitText="确认登记合同应收" confirmDiscard disabled={!reviewed || !/^\d+(?:\.\d{1,2})?$/.test(amount) || !reason.trim()} onSubmit={async () => {
    const intent = createContractIntent(context, { amountDueFen: parseRmbFen(amount), expectedVersion: contract?.version || 0, reason, sourceLicenseContractId: source.trim() || null }, randomUuid())
    try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause }
  }}><div className="grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm"><span>合同应收（人民币元，内部按分保存）</span><Input value={amount} onChange={event => setAmount(event.target.value)} inputMode="decimal" placeholder="0.00" /></label><label className="space-y-2 text-sm"><span>来源 License 合同 UUID（选填）</span><Input value={source} onChange={event => setSource(event.target.value)} className="font-mono" /></label><label className="space-y-2 text-sm md:col-span-2"><span>变更原因（最多 200 字）</span><Textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={200} /></label></div><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已核对门店、合同额度和来源；应收不得低于已收净额</label></MutationDialog>
}
function EventEditor({ context, original, onClose, onSubmit }: EditorProps & { contract: GuaranteeContract; original: GuaranteeEvent | null }) {
  const [businessKey, setBusinessKey] = useState(""), [direction, setDirection] = useState<"RECEIPT" | "REFUND">(original ? original.direction === "RECEIPT" ? "REFUND" : "RECEIPT" : "RECEIPT"), [amount, setAmount] = useState(original ? (original.amount_fen / 100).toFixed(2) : ""), [occurredAt, setOccurredAt] = useState(""), [digest, setDigest] = useState(""), [reference, setReference] = useState(""), [reviewed, setReviewed] = useState(false)
  return <MutationDialog title={original ? "登记补偿事件" : "登记线下缴纳／退还凭证"} description="只登记线下事实和证据摘要，channel_status 固定为 NOT_REQUESTED，不发起或确认银行结果。事件提交后不可修改。" onClose={onClose} submitText="确认登记线下凭证" confirmDiscard disabled={!reviewed || !businessKey.trim() || !/^\d+(?:\.\d{1,2})?$/.test(amount) || parseRmbFen(amount) <= 0 || !occurredAt || !/^[0-9a-fA-F]{64}$/.test(digest.trim()) || !reference.trim()} onSubmit={async () => {
    const intent = createGuaranteeEventIntent(context, { businessKey: businessKey.trim(), direction, amountFen: parseRmbFen(amount), occurredAt: new Date(occurredAt).toISOString(), evidenceSha256: digest, evidenceRef: reference, compensatesEventId: original?.id || null }, randomUuid())
    try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause }
  }}><div className="grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm"><span>本店业务键（唯一）</span><Input value={businessKey} onChange={event => setBusinessKey(event.target.value)} maxLength={80} className="font-mono" /></label><label className="space-y-2 text-sm"><span>收退方向</span><select className="h-10 w-full rounded-md border bg-background px-3" value={direction} disabled={!!original} onChange={event => setDirection(event.target.value as "RECEIPT" | "REFUND")}><option value="RECEIPT">线下缴纳</option><option value="REFUND">线下退还</option></select></label><label className="space-y-2 text-sm"><span>金额（人民币元）</span><Input value={amount} readOnly={!!original} onChange={event => setAmount(event.target.value)} inputMode="decimal" /></label><label className="space-y-2 text-sm"><span>实际发生时间（浏览器本地时区）</span><Input type="datetime-local" value={occurredAt} onChange={event => setOccurredAt(event.target.value)} /></label><label className="space-y-2 text-sm md:col-span-2"><span>凭证 SHA-256</span><Input value={digest} onChange={event => setDigest(event.target.value)} maxLength={64} className="font-mono" /></label><label className="space-y-2 text-sm md:col-span-2"><span>安全引用（不得包含银行账号或任意外部 URL）</span><Input value={reference} onChange={event => setReference(event.target.value)} maxLength={200} className="font-mono" placeholder="offline:2026/11/receipt-0001" /></label></div>{original && <p className="rounded-lg bg-muted/20 p-3 text-xs text-muted-foreground">补偿原事件 {original.business_key}：方向必须相反、金额必须相同；原事件不可修改。</p>}<label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已核对原始线下凭证、金额、发生时间和安全引用；登记不代表银行成功</label></MutationDialog>
}
function ClosureStartEditor({ context, onClose, onSubmit }: EditorProps) {
  const [category, setCategory] = useState<(typeof CLOSURE_REASON_CATEGORIES)[number]>("OPERATOR_REQUEST"), [reason, setReason] = useState(""), [reviewed, setReviewed] = useState(false)
  return <MutationDialog title="发起不可撤回的 30 天闭店公示" description="公示一旦登记不可撤回或用重复申请重置倒计时。待结清不等于门店已停用或余额已返还。" onClose={onClose} submitText="确认发起闭店公示" submitVariant="destructive" confirmDiscard disabled={!reviewed || !reason.trim()} onSubmit={async () => { const intent = createClosureStartIntent(context, { reasonCategory: category, reason }, randomUuid()); try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause } }}><label className="space-y-2 text-sm"><span>原因类别</span><select className="h-10 w-full rounded-md border bg-background px-3" value={category} onChange={event => setCategory(event.target.value as (typeof CLOSURE_REASON_CATEGORIES)[number])}>{CLOSURE_REASON_CATEGORIES.map(value => <option key={value} value={value}>{reasonLabels[value]}</option>)}</select></label><label className="space-y-2 text-sm"><span>内部闭店原因（最多 500 字）</span><Textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} /></label><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />明确确认发起不可撤回的 30 天公示；本操作不执行最终结清或逐户退款</label></MutationDialog>
}
function ClosureExpiryEditor({ context, notice, onClose, onSubmit }: EditorProps & { notice: NonNullable<ClosureSummary["notice"]> }) {
  const [reason, setReason] = useState(""), [reviewed, setReviewed] = useState(false)
  return <MutationDialog title="确认 30 天公示已到期" description={`最早结清时点：${formatDateTime(notice.earliest_settlement_at)}。确认只追加到期证据并进入待结清，不自动停用门店或退款。`} onClose={onClose} submitText="确认进入待结清" confirmDiscard disabled={!reviewed || !reason.trim()} onSubmit={async () => { const intent = createClosureExpiryIntent(context, reason, randomUuid()); try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause } }}><label className="space-y-2 text-sm"><span>到期确认原因（最多 500 字）</span><Textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} /></label><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已核对控制库时钟和公示到期时点；待结清不是已停用、已退款或最终结清</label></MutationDialog>
}
