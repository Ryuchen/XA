import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { formatRmb, parseRmbFen, randomUuid } from "@xa/shared"
import { Feedback, MutationDialog, ReadOnlyNotice, Status } from "@/components/business"
import { ReadErrorNotice } from "@/components/page-data-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { platformRequest, type PlatformProfile, type PlatformStore } from "@/lib/platform-session"
import {
  clearStoredClosureReturnIntent,
  closureReturnAllowedActions,
  closureReturnPoints,
  closureReturnStatusPresentation,
  closureReturnsAccess,
  closureReturnsViewMode,
  closureReturnStorageKey,
  closureReturnUnits,
  closureReturnWriteFailure,
  closureReturnWriteMessage,
  createClosureReturnPaymentIntent,
  createClosureReturnPrepareIntent,
  createClosureReturnReleaseIntent,
  parseClosureReturnDetail,
  parseClosureReturnList,
  parseClosureReturnPaymentResult,
  parseClosureReturnPrepareResult,
  parseClosureReturnReleaseResult,
  readStoredClosureReturnIntent,
  recoverClosureReturnWrite,
  sameClosureReturnScope,
  storeClosureReturnIntent,
  submitClosureReturnWrite,
  type ClosureReturnContext,
  type ClosureReturnDetail,
  type ClosureReturnIntent,
  type ClosureReturnList,
  type ClosureReturnRow,
  type ClosureReturnWriteResult,
} from "@/lib/platform-closure-returns.ts"

const PAGE_SIZE = 20
const operationLabels: Record<ClosureReturnIntent["operation"], string> = {
  prepare: "准备全额本金冻结",
  "record-payment": "登记全额线下实付",
  release: "解除未实付冻结",
}
type StoreTarget = Pick<PlatformStore, "id" | "slug" | "name"> & { status: string }
type Editor = { kind: "prepare"; row: ClosureReturnRow } | { kind: "record-payment"; row: ClosureReturnRow } | { kind: "release"; row: ClosureReturnRow }

function returnPath(storeId: number, suffix = "") {
  return `store-management/${storeId}/closure/returns${suffix}` as const
}
async function readList(storeId: number, page: number) {
  const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
  return parseClosureReturnList(await platformRequest<unknown>(`${returnPath(storeId)}?${query}`), { page, pageSize: PAGE_SIZE })
}
async function sendWrite(intent: ClosureReturnIntent): Promise<ClosureReturnWriteResult> {
  if (intent.operation === "prepare") {
    return parseClosureReturnPrepareResult(await platformRequest<unknown>(returnPath(intent.storeId, "/prepare"), {
      method: "POST", data: intent.payload,
    }), intent)
  }
  if (intent.operation === "record-payment") {
    return parseClosureReturnPaymentResult(await platformRequest<unknown>(returnPath(intent.storeId, `/${encodeURIComponent(intent.caseId!)}/record-payment`), {
      method: "POST", data: intent.payload,
    }), intent)
  }
  return parseClosureReturnReleaseResult(await platformRequest<unknown>(returnPath(intent.storeId, `/${encodeURIComponent(intent.caseId!)}/release`), {
    method: "POST", data: intent.payload,
  }), intent)
}
function money(value: number | null) {
  return value === null ? "待核" : `¥${formatRmb(value)}`
}
function editorCatch(cause: unknown, onClose: () => void) {
  if ((cause as { localCode?: string })?.localCode === "STORAGE_UNAVAILABLE") return
  const failure = closureReturnWriteFailure(cause)
  if (failure.kind !== "rejected") onClose()
}

export default function PlatformClosureReturns({ store, profile, sessionId, onAuthorizationDenied }: {
  store: StoreTarget
  profile: PlatformProfile
  sessionId: string
  onAuthorizationDenied: () => void
}) {
  const access = closureReturnsAccess(profile, store.slug)
  const context = useMemo<ClosureReturnContext>(() => ({
    storeId: store.id, storeSlug: store.slug, actorId: profile.id, sessionId,
    scopeSignature: access.scopeSignature,
  }), [store.id, store.slug, profile.id, sessionId, access.scopeSignature])
  const storageKey = useMemo(() => closureReturnStorageKey(context), [context])
  const restored = useMemo(() => {
    try {
      return {
        intent: typeof sessionStorage === "undefined" ? null : readStoredClosureReturnIntent(sessionStorage, storageKey, context),
        error: "",
      }
    } catch (cause) { return { intent: null, error: cause instanceof Error ? cause.message : "余额返还原请求无法读取" } }
  }, [storageKey, context])
  const [pending, setPending] = useState<ClosureReturnIntent | null>(restored.intent)
  const [recoveryError, setRecoveryError] = useState(restored.error)
  const [recoveryMessage, setRecoveryMessage] = useState("")
  const [recoveryNotFound, setRecoveryNotFound] = useState(false)
  const [data, setData] = useState<ClosureReturnList | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(access.view)
  const [error, setError] = useState("")
  const [actionError, setActionError] = useState("")
  const [denied, setDenied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [editor, setEditor] = useState<Editor | null>(null)
  const mounted = useRef(true)
  const readSequence = useRef(0)
  const writeSequence = useRef(0)
  const pendingRef = useRef(pending)
  const deniedCallback = useRef(onAuthorizationDenied)
  pendingRef.current = pending
  deniedCallback.current = onAuthorizationDenied

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      readSequence.current += 1
      writeSequence.current += 1
    }
  }, [])

  const clearIntent = useCallback((expected: ClosureReturnIntent) => {
    if (typeof sessionStorage === "undefined") return
    const cleared = clearStoredClosureReturnIntent(sessionStorage, storageKey, expected)
    if (!cleared || !mounted.current) return
    setPending(current => current && JSON.stringify(current) === JSON.stringify(expected) ? null : current)
    setRecoveryNotFound(false)
    setRecoveryMessage("")
  }, [storageKey])
  const saveIntent = useCallback((intent: ClosureReturnIntent) => {
    if (typeof sessionStorage === "undefined") throw Object.assign(new Error("浏览器无法保存原请求，本次未发送"), { localCode: "STORAGE_UNAVAILABLE" })
    storeClosureReturnIntent(sessionStorage, storageKey, intent)
    if (mounted.current) {
      setPending(intent)
      setRecoveryError("")
      setRecoveryNotFound(false)
      setRecoveryMessage("")
    }
  }, [storageKey])
  const deny = useCallback(() => {
    if (!mounted.current) return
    setDenied(true)
    setData(null)
    setEditor(null)
    setError("服务端已拒绝当前门店 scope；旧金额快照和写入能力已清除。")
    deniedCallback.current()
  }, [])

  const load = useCallback(async () => {
    if (!access.view || denied) return
    const sequence = ++readSequence.current
    setLoading(true)
    setError("")
    try {
      const result = await readList(store.id, page)
      if (!mounted.current || sequence !== readSequence.current) return
      setData(result)
    } catch (cause) {
      if (!mounted.current || sequence !== readSequence.current) return
      if ((cause as { status?: number })?.status === 403) deny()
      else setError(cause instanceof Error ? cause.message : "余额返还账户清单读取失败")
    } finally {
      if (mounted.current && sequence === readSequence.current) setLoading(false)
    }
  }, [access.view, denied, store.id, page, deny])
  useEffect(() => { void load() }, [load])

  const mode = closureReturnsViewMode({
    denied, hasSnapshot: !!data, loading, error, rowCount: data?.list.length || 0,
  })
  const incomplete = data?.summary.complete === false
  const writeBlocked = busy || !!pending || !!recoveryError || denied || mode !== "ready" || incomplete
  const canManage = access.manage && !denied
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  function openEditor(kind: Editor["kind"], row: ClosureReturnRow) {
    if (writeBlocked || !closureReturnAllowedActions(row, canManage, writeBlocked).includes(kind)) return
    setActionError("")
    setEditor({ kind, row } as Editor)
  }
  function live(sequence: number, intent: ClosureReturnIntent) {
    return mounted.current && sequence === writeSequence.current && sameClosureReturnScope(intent, context)
  }
  async function runIntent(intent: ClosureReturnIntent, recovering = false) {
    if (busy || denied || !canManage || (recovering ? !recoveryNotFound || !pending : writeBlocked)) return
    const sequence = ++writeSequence.current
    setBusy(true)
    setActionError("")
    try {
      const result = await submitClosureReturnWrite(intent, { save: saveIntent, send: sendWrite, clear: clearIntent })
      if (!live(sequence, intent)) return
      setEditor(null)
      if (intent.operation === "prepare") toast.success(`已建立全额本金冻结单 ${result.row.case_id || "待核"}`)
      else if (intent.operation === "record-payment") toast.success("已登记全额线下实付；channel_status=NOT_REQUESTED，不代表银行或支付渠道成功")
      else toast.success("已按原请求解除未实付冻结；历史事件未被覆盖")
      await load()
    } catch (cause) {
      if (!live(sequence, intent)) return
      setActionError((cause as { localCode?: string })?.localCode === "STORAGE_UNAVAILABLE"
        ? (cause as Error).message : closureReturnWriteMessage(cause, recovering))
      if ((cause as { status?: number })?.status === 403) deny()
      if ((cause as { status?: number })?.status === 409) await load()
      throw cause
    } finally {
      if (live(sequence, intent)) setBusy(false)
    }
  }
  async function findPrepareCase(intent: Extract<ClosureReturnIntent, { operation: "prepare" }>): Promise<ClosureReturnDetail | null> {
    let current = 1
    let found: ClosureReturnRow | null = null
    while (current <= 100_000) {
      const result = await readList(store.id, current)
      for (const row of result.list) {
        if (row.account_id !== intent.accountId || row.business_cycle !== intent.businessCycle) continue
        if (found) throw new Error("原账号周期在分页清单中出现多次，已停止恢复")
        found = row
      }
      if (!result.has_more) break
      current += 1
    }
    if (!found?.case_id) return null
    return parseClosureReturnDetail(await platformRequest<unknown>(returnPath(store.id, `/${encodeURIComponent(found.case_id)}`)), {
      caseId: found.case_id, accountId: intent.accountId, businessCycle: intent.businessCycle,
    })
  }
  async function recover() {
    const intent = pending
    if (!intent || busy || denied || !access.view) return
    const sequence = ++writeSequence.current
    setBusy(true)
    setActionError("")
    setRecoveryMessage("")
    setRecoveryNotFound(false)
    try {
      const result = await recoverClosureReturnWrite(intent, {
        findCase: findPrepareCase,
        readCase: async id => parseClosureReturnDetail(await platformRequest<unknown>(returnPath(store.id, `/${encodeURIComponent(id)}`)), {
          caseId: id, accountId: intent.accountId, businessCycle: intent.businessCycle,
        }),
      })
      if (!live(sequence, intent)) return
      setRecoveryMessage(result.message || "")
      if (result.status === "FOUND") {
        clearIntent(intent)
        toast.success("已按原请求号和完整内容恢复，未重复写入")
        await load()
      } else if (result.status === "ALREADY_EXISTS") {
        clearIntent(intent)
        await load()
      } else if (result.status === "NOT_FOUND") {
        setRecoveryNotFound(true)
        setRecoveryMessage("只读查询未找到原请求结果。仅在状态仍允许时，可使用原请求号和完整内容重试。")
      }
    } catch (cause) {
      if (!live(sequence, intent)) return
      if ((cause as { status?: number })?.status === 403) deny()
      else setActionError(`${cause instanceof Error ? cause.message : "恢复读取失败"}；原请求仍保留，不换号重试。`)
    } finally {
      if (live(sequence, intent)) setBusy(false)
    }
  }

  if (!access.view) return <Feedback message="当前平台账号没有本店 closure:view 权限；未读取余额返还数据。" />
  return <section className="space-y-4" aria-busy={loading || busy || undefined}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="font-semibold">逐户余额返还</h3><p className="text-xs text-muted-foreground">账号以 account_id + business_cycle 定位。单位字段保持原值；可退与已付人民币使用分。仅纯本金账户可建立全额冻结单。</p></div>
      <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading || busy || denied}>刷新清单</Button>
    </div>
    {!canManage && <ReadOnlyNotice canEdit={false} subject="逐户余额返还" />}
    {recoveryError && <Feedback message={recoveryError} />}
    {denied && <Feedback message="本店余额返还权限已失效，旧快照和写入能力已清除；请重新登录或等待授权刷新。" />}
    {pending && <section role="alert" className="space-y-3 rounded-xl border border-amber-500/50 bg-amber-500/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="font-semibold">{operationLabels[pending.operation]}结果待核</h4><p className="mt-1 break-all font-mono text-xs">{pending.client_request_id}</p><p className="mt-1 text-xs text-muted-foreground">账号 {pending.accountId} · 周期 {pending.businessCycle} · case {pending.caseId || "准备阶段尚未分配"}</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void recover()} disabled={busy || denied || !access.view}>只读恢复原 case</Button>{recoveryNotFound && <Button onClick={() => void runIntent(pending, true).catch(() => {})} disabled={busy || denied || !canManage || mode === "stale" || incomplete}>原号原内容重试</Button>}</div>
      </div>{recoveryMessage && <p className="text-sm text-muted-foreground">{recoveryMessage}</p>}<p className="text-xs text-muted-foreground">完整请求已写入会话存储；网络或 5xx 后不会换号。同号异内容、缺少内容证据或跨账号周期均停止重放。</p>
    </section>}
    <Feedback message={actionError} />
    <ReadErrorNotice error={denied ? "" : error} retry={() => void load()} hasSnapshot={!!data} />
    {mode === "stale" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">当前为旧清单快照，账号周期、状态或金额可能已变化，所有写入已暂停。</p>}
    {incomplete && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm"><strong>部分读取：</strong>summary.complete=false，相关汇总合计待核，缺失金额不按 0 补齐；写入已暂停。</p>}
    {mode === "loading" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">正在读取本店逐户余额、批次来源和返还状态…</p>
      : mode === "error" ? <p role="status" className="rounded-lg bg-muted/20 p-6 text-sm text-muted-foreground">余额返还清单尚未读取，全部金额保持待核，不显示为零。</p>
      : mode === "denied" ? null
      : data && <>
        <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[1420px] text-left text-sm"><caption className="sr-only">逐户余额返还清单，null 金额显示为待核</caption><thead><tr className="border-b text-xs text-muted-foreground"><th className="p-2">账户 / 状态</th><th>钱包</th><th>可核本金</th><th>赠送</th><th>赔付</th><th>积分</th><th>已冻结</th><th>可退人民币</th><th>已付人民币</th><th>待核原因</th><th className="text-right">操作</th></tr></thead><tbody>
          {data.list.map(row => {
            const actions = closureReturnAllowedActions(row, canManage, writeBlocked)
            const presentation = closureReturnStatusPresentation(row.status)
            return <tr key={`${row.account_id}:${row.business_cycle}`} className="border-b align-top last:border-0"><td className="p-2"><strong>{row.account_label}</strong><p className="mt-1 font-mono text-xs">ID {row.account_id} · 周期 {row.business_cycle}</p><p className="mt-1 break-all font-mono text-xs text-muted-foreground">case {row.case_id || "尚未建立"}</p><div className="mt-2"><Status value={row.status} label={presentation.label} tone={presentation.tone} /></div></td>
              <td>{closureReturnUnits(row.wallet_units)}</td><td>{closureReturnUnits(row.principal_units)}</td><td>{closureReturnUnits(row.gift_units)}</td><td>{closureReturnUnits(row.compensation_units)}</td><td>{closureReturnPoints(row.points_hundredths)}</td><td>{closureReturnUnits(row.frozen_units)}</td><td>{money(row.refund_fen)}</td><td>{money(row.paid_fen)}</td>
              <td className="max-w-64"><ul className="space-y-1 text-xs">{row.blocking_reasons.length ? row.blocking_reasons.map(reason => <li key={reason} className="break-words">{reason}</li>) : <li className="text-muted-foreground">无</li>}</ul></td>
              <td className="p-2 text-right">{presentation.readOnly ? <span className="text-xs text-muted-foreground">{presentation.readOnly}</span> : <div className="flex justify-end gap-1">{actions.includes("prepare") && <Button size="sm" variant="outline" disabled={writeBlocked} onClick={() => openEditor("prepare", row)}>准备冻结</Button>}{actions.includes("record-payment") && <Button size="sm" disabled={writeBlocked} onClick={() => openEditor("record-payment", row)}>登记全额实付</Button>}{actions.includes("release") && <Button size="sm" variant="outline" disabled={writeBlocked} onClick={() => openEditor("release", row)}>解除冻结</Button>}{!actions.length && row.status !== "REVIEW_REQUIRED" && <span className="text-xs text-muted-foreground">当前不可写</span>}</div>}</td></tr>
          })}
          {!data.list.length && <tr><td colSpan={11} className="p-8 text-center text-muted-foreground" role="status">本页没有可展示的老板账户；空清单不代表金额为零。</td></tr>}
        </tbody></table></div>
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">共 {data.total} 户 · 第 {data.page} / {totalPages} 页 · 每页 {data.page_size} 户</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={loading || busy || !!pending || page <= 1} onClick={() => { setEditor(null); setPage(value => Math.max(1, value - 1)) }}>上一页</Button><Button size="sm" variant="outline" disabled={loading || busy || !!pending || !data.has_more} onClick={() => { setEditor(null); setPage(value => value + 1) }}>下一页</Button></div></div>
        <p className="text-xs text-muted-foreground">本页不提供部分退、赠送或赔付折算、闭店手续费、最终停用及真实银行成功入口。实付登记只保存线下事实，channel_status 始终为 NOT_REQUESTED。</p>
      </>}
    {editor?.kind === "prepare" && <PrepareEditor context={context} row={editor.row} onClose={() => setEditor(null)} onSubmit={runIntent} />}
    {editor?.kind === "record-payment" && <PaymentEditor context={context} row={editor.row} onClose={() => setEditor(null)} onSubmit={runIntent} />}
    {editor?.kind === "release" && <ReleaseEditor context={context} row={editor.row} onClose={() => setEditor(null)} onSubmit={runIntent} />}
  </section>
}

type EditorProps = {
  context: ClosureReturnContext
  row: ClosureReturnRow
  onClose: () => void
  onSubmit: (intent: ClosureReturnIntent, recovering?: boolean) => Promise<void>
}
function PrepareEditor({ context, row, onClose, onSubmit }: EditorProps) {
  const [reviewed, setReviewed] = useState(false)
  return <MutationDialog title="准备全额本金冻结" description="服务端将在锁内重验闭店状态、账号周期、钱包和全部本金批次；预期值不一致会返回 409。本操作不登记实付。" onClose={onClose} submitText="确认准备全额冻结" confirmDiscard disabled={!reviewed} onSubmit={async () => {
    const intent = createClosureReturnPrepareIntent(context, row, randomUuid())
    try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause }
  }}><dl className="grid gap-3 rounded-lg bg-muted/20 p-4 text-sm md:grid-cols-2"><div><dt className="text-muted-foreground">账号 / 业务周期</dt><dd className="font-mono">{row.account_id} / {row.business_cycle}</dd></div><div><dt className="text-muted-foreground">账户</dt><dd>{row.account_label}</dd></div><div><dt className="text-muted-foreground">预期钱包（内部单位）</dt><dd>{closureReturnUnits(row.wallet_units)}</dd></div><div><dt className="text-muted-foreground">预期本金（内部单位）</dt><dd>{closureReturnUnits(row.principal_units)}</dd></div><div><dt className="text-muted-foreground">全额可退（人民币）</dt><dd>{money(row.refund_fen)}</dd></div><div><dt className="text-muted-foreground">赠送 / 赔付 / 已冻结</dt><dd>{closureReturnUnits(row.gift_units)} / {closureReturnUnits(row.compensation_units)} / {closureReturnUnits(row.frozen_units)}</dd></div></dl><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已明确核对 account_id={row.account_id}、business_cycle={row.business_cycle}、预期钱包 {row.wallet_units ?? "待核"} 和本金 {row.principal_units ?? "待核"}；仅建立全额本金冻结</label></MutationDialog>
}
function PaymentEditor({ context, row, onClose, onSubmit }: EditorProps) {
  const expected = row.refund_fen
  const [amount, setAmount] = useState(expected === null ? "" : (expected / 100).toFixed(2))
  const [reference, setReference] = useState("")
  const [digest, setDigest] = useState("")
  const [occurredAt, setOccurredAt] = useState("")
  const [reviewed, setReviewed] = useState(false)
  const amountFen = /^\d+(?:\.\d{1,2})?$/.test(amount) ? parseRmbFen(amount) : -1
  return <MutationDialog title="登记全额线下实付" description="仅登记已经核实的线下事实；金额必须等于原可退本金。channel_status=NOT_REQUESTED，不代表银行或支付渠道成功，登记后不可解除。" onClose={onClose} submitText="确认登记全额实付" confirmDiscard disabled={!reviewed || expected === null || amountFen !== expected || !reference.trim() || !/^[0-9a-fA-F]{64}$/.test(digest.trim()) || !occurredAt} onSubmit={async () => {
    const intent = createClosureReturnPaymentIntent(context, row, { amountFen, externalReference: reference, proofSha256: digest, occurredAt: new Date(occurredAt).toISOString() }, randomUuid())
    try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause }
  }}><div className="grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm"><span>实付金额（人民币元，必须全额 {expected === null ? "待核" : (expected / 100).toFixed(2)}）</span><Input value={amount} onChange={event => setAmount(event.target.value)} inputMode="decimal" /></label><label className="space-y-2 text-sm"><span>实际发生时间（浏览器本地时区）</span><Input type="datetime-local" value={occurredAt} onChange={event => setOccurredAt(event.target.value)} /></label><label className="space-y-2 text-sm md:col-span-2"><span>外部编号安全引用（服务端脱敏，不填完整银行账号或 URL）</span><Input value={reference} onChange={event => setReference(event.target.value)} maxLength={128} autoComplete="off" className="font-mono" placeholder="OFFLINE-RETURN-0001" /></label><label className="space-y-2 text-sm md:col-span-2"><span>proof SHA-256（只填摘要，不上传凭证正文）</span><Input value={digest} onChange={event => setDigest(event.target.value)} maxLength={64} className="font-mono" /></label></div><p className="rounded-lg bg-muted/20 p-3 text-xs text-muted-foreground">case {row.case_id} · 账号 {row.account_id} · 周期 {row.business_cycle}。部分金额会在本地校验中拒绝；不会提供银行成功按钮。</p><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已核对全额实付、脱敏外部编号、凭证摘要和实际发生时间；明确 NOT_REQUESTED 不代表银行成功，登记后不得解除</label></MutationDialog>
}
function ReleaseEditor({ context, row, onClose, onSubmit }: EditorProps) {
  const [reason, setReason] = useState("")
  const [reviewed, setReviewed] = useState(false)
  return <MutationDialog title="解除未实付冻结" description="仅当前未实付的 FROZEN 冻结单可解除；服务端按原请求精确恢复钱包与冻结凭证，不覆盖历史事件。实付与解除互斥。" onClose={onClose} submitText="确认解除冻结" confirmDiscard disabled={!reviewed || !reason.trim()} onSubmit={async () => {
    const intent = createClosureReturnReleaseIntent(context, row, reason, randomUuid())
    try { await onSubmit(intent) } catch (cause) { editorCatch(cause, onClose); throw cause }
  }}><p className="rounded-lg bg-muted/20 p-3 text-sm">case {row.case_id} · 账号 {row.account_id} · 周期 {row.business_cycle} · 冻结 {closureReturnUnits(row.frozen_units)} 内部单位</p><label className="space-y-2 text-sm"><span>解除原因（最多 500 字）</span><Textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} /></label><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} className="mt-1 size-4 accent-primary" />已核实该 case 尚未实付；解除只恢复本次冻结，不修改历史流水，也不代表银行或支付渠道结果</label></MutationDialog>
}
