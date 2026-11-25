import { useMemo, useRef, useState } from "react"
import {
  coinUnit,
  createApiClient,
  formatXaCoin,
  getStoreSlug,
  useRealtime,
  useResource,
  useSession,
} from "@xa/shared"
import {
  addonRefundAmountsKnown,
  addonRefundProposalPath,
  loadAddonRefundProposal,
  loadAddonRefundProposalTargets,
  ownAddonRefundDecision,
  proposalScope,
  proposalScopeKey,
  readPendingAddonRefundDecision,
  recordAddonRefundDecision,
  scopeRowKey,
  scopeSelectionComplete,
  type AddonRefundDecision,
  type AddonRefundRole,
  type PendingAddonRefundDecision,
  type ProposalScopeRow,
} from "./addon-refund-scope"

type ProposalTarget = Pick<AddonRefundDecision, "paymentId" | "proposalId">
const storage = {
  getItem: (key: string) => sessionStorage.getItem(key),
  setItem: (key: string, value: string) => sessionStorage.setItem(key, value),
  removeItem: (key: string) => sessionStorage.removeItem(key),
}
const statuses: Record<string, string> = {
  PENDING: "等待确认",
  ACCEPTED: "所需人员已同意",
  REJECTED: "已有人员提出异议",
  SUPERSEDED: "已有更新方案",
}
const buttonClass = "min-h-11 rounded-md border px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
const amount = (value: number) => Number.isSafeInteger(value) ? `${formatXaCoin(value)} ${coinUnit()}` : "待核对"
const positiveId = (value: number | null | undefined): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0

/** User-side decisions are bound to one paid add-on and the exact fulfillment scope returned for it. */
export function AddonRefundProposals({ role, orderId, initialPaymentId, initialProposalId }: {
  role: AddonRefundRole
  orderId: number
  initialPaymentId?: number | null
  initialProposalId?: number | null
}) {
  const session = useSession(role)
  if (!session.user) return null
  const scope = `${getStoreSlug()}:${role}:${session.user.id}:${orderId}`
  return <ScopedAddonRefundProposals key={`${scope}:${session.revision}`} role={role} orderId={orderId}
    accountId={Number(session.user.id)} scope={scope} initialPaymentId={initialPaymentId} initialProposalId={initialProposalId} />
}

function ScopedAddonRefundProposals({ role, orderId, accountId, scope, initialPaymentId, initialProposalId }: {
  role: AddonRefundRole
  orderId: number
  accountId: number
  scope: string
  initialPaymentId?: number | null
  initialProposalId?: number | null
}) {
  const api = useMemo(() => createApiClient(role), [role])
  const key = `xa:addon-refund-decision:${scope}`
  const [pending, setPending] = useState<PendingAddonRefundDecision | "invalid" | null>(() => readPendingAddonRefundDecision(storage, key))
  const pendingDecision = pending && pending !== "invalid" ? pending : null
  const requested = positiveId(initialPaymentId) && positiveId(initialProposalId)
    ? { paymentId: initialPaymentId, proposalId: initialProposalId } : null
  const [target, setTarget] = useState<ProposalTarget | null>(() => pendingDecision || requested)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const load = useResource(() => loadAddonRefundProposalTargets(api, orderId), [api, orderId])

  function refresh() {
    setRefreshVersion(value => value + 1)
    void load.reload()
  }
  useRealtime(role, event => {
    if (!busy && (event.type === "reconnected" ||
        (event.type === "order_status_update" && Number(event.data?.id) === orderId))) refresh()
  })
  if (load.data && !load.data.length && !pending && !target && !load.error && !load.loading) return null
  return <section className="space-y-3 rounded-lg border p-4 text-sm" aria-label="加购退款方案">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">加购退款方案</h3>
      <button type="button" className={buttonClass} disabled={busy || load.loading} onClick={refresh}>刷新方案列表</button>
    </div>
    {load.loading && <p role="status" className="text-muted-foreground">正在读取加购退款方案列表…</p>}
    {load.error && <p role="alert" className="text-destructive">{load.error.message}</p>}
    {load.data?.map(row => {
      const selected = target?.paymentId === row.payment_id && target.proposalId === row.proposal_id
      return <div key={`${row.payment_id}:${row.proposal_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3">
        <div className="space-y-1">
          <p className="font-medium">付款 {row.payment_id} · 方案 {row.proposal_id}</p>
          <p>{statuses[row.status] || "方案状态待核对"} · {row.my_accepted === true ? "本人已同意" : row.my_accepted === false ? "本人已提出异议" : "本人尚未确认"}</p>
        </div>
        <button type="button" className={buttonClass} aria-expanded={selected} disabled={busy} onClick={() => setTarget({ paymentId: row.payment_id, proposalId: row.proposal_id })}>{selected ? "已选中方案" : "查看方案"}</button>
      </div>
    })}
    {pending === "invalid" ? <p role="alert" className="text-destructive">原确认凭证无法读取，请联系客服核对。</p> : pendingDecision && <div className="space-y-2 rounded-md border p-3">
      <p>原付款 {pendingDecision.paymentId}、方案 {pendingDecision.proposalId} 的{pendingDecision.accepted ? "同意" : "异议"}结果待核对。</p>
      <p className="text-muted-foreground">刷新或重试会继续核对原付款、原方案和已保存的履约段，不会改用其他目标。</p>
      <button type="button" className={buttonClass} disabled={busy} onClick={() => setTarget(pendingDecision)}>查看原决定</button>
    </div>}
    {target && <SelectedAddonRefundProposal key={`${target.paymentId}:${target.proposalId}`} api={api} role={role} orderId={orderId} accountId={accountId}
      target={target} storageKey={key} pending={pending} refreshVersion={refreshVersion} onBusy={setBusy}
      onPendingChange={setPending} onRecorded={() => void load.reload()} />}
  </section>
}

function SelectedAddonRefundProposal({ api, role, orderId, accountId, target, storageKey, pending, refreshVersion, onBusy, onPendingChange, onRecorded }: {
  api: ReturnType<typeof createApiClient>
  role: AddonRefundRole
  orderId: number
  accountId: number
  target: ProposalTarget
  storageKey: string
  pending: PendingAddonRefundDecision | "invalid" | null
  refreshVersion: number
  onBusy: (value: boolean) => void
  onPendingChange: (value: PendingAddonRefundDecision | "invalid" | null) => void
  onRecorded: () => void
}) {
  const [choice, setChoice] = useState<AddonRefundDecision | null>(null)
  const [selectedScope, setSelectedScope] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState(false)
  const [issue, setIssue] = useState("")
  const [notice, setNotice] = useState("")
  const submitting = useRef(false)
  const load = useResource(() => loadAddonRefundProposal(api, orderId, target.paymentId, target.proposalId, accountId, role),
    [api, orderId, target.paymentId, target.proposalId, refreshVersion])
  const proposal = load.data
  const error = issue || load.error?.message
  const pendingDecision = pending && pending !== "invalid" ? pending : null
  const original = pendingDecision && pendingDecision.paymentId === target.paymentId && pendingDecision.proposalId === target.proposalId ? pendingDecision : null
  const own = proposal ? ownAddonRefundDecision(proposal, accountId, role) : null
  const selected = proposal && choice?.paymentId === proposal.payment_id && choice.proposalId === proposal.id ? choice : null
  const amountsKnown = proposal ? addonRefundAmountsKnown(proposal, role) : false
  let scopeRows: ProposalScopeRow[] = []
  let scopeError = ""
  if (proposal) {
    try {
      scopeRows = proposalScope(proposal, role, accountId)
    } catch (cause) {
      scopeError = cause instanceof Error ? cause.message : "方案履约段暂无法核对。"
    }
  }
  const scopeReady = scopeSelectionComplete(scopeRows, selectedScope)
  const editable = !!proposal && proposal.status === "PENDING" && !proposal.refund_executed && own === null && amountsKnown && !scopeError
  const canChoose = editable && !pending
  const recoveringRecorded = !!original && typeof own === "boolean"

  async function submit(decision: AddonRefundDecision) {
    if (submitting.current || !proposal || (!recoveringRecorded && !scopeReady) || load.loading || load.error) return
    submitting.current = true
    setBusy(true)
    onBusy(true)
    setIssue("")
    setNotice("")
    try {
      const recorded = await recordAddonRefundDecision({ client: api, storage, key: storageKey, orderId, accountId, role, decision,
        scopeKey: recoveringRecorded ? original.scopeKey : proposalScopeKey(scopeRows) })
      setChoice(null)
      setNotice(recoveringRecorded
        ? `原决定已核对。${recorded.refund_executed ? "退款已由客服执行，实际到账请查看钱包流水。" : "退款尚未执行。"}`
        : recorded.refund_executed ? "原决定已核对。退款已由客服执行，实际到账请查看钱包流水。" : "确认已记录，退款尚未执行。")
    } catch (cause) {
      setIssue(cause instanceof Error ? cause.message : "确认结果尚待核对，请重试原决定。")
    } finally {
      onPendingChange(readPendingAddonRefundDecision(storage, storageKey))
      await load.reload()
      submitting.current = false
      setBusy(false)
      onBusy(false)
      onRecorded()
    }
  }

  return <section className="space-y-3 rounded-md border p-3" aria-label={`付款 ${target.paymentId} 的方案 ${target.proposalId}详情`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="font-medium">付款 {target.paymentId} · 方案 {target.proposalId}详情</h4>
      <button type="button" className={buttonClass} disabled={busy || load.loading} onClick={() => { setChoice(null); setIssue(""); void load.reload() }}>刷新所选方案</button>
    </div>
    {load.loading && <p role="status" className="text-muted-foreground">正在读取所选方案详情…</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {proposal && <article className="space-y-3 rounded-md border p-3">
      <div className="space-y-1">
        <p className="font-medium">{proposal.addon_name}</p>
        <p>{statuses[proposal.status] || "方案状态待核对"}{proposal.case_id ? ` · 售后工单 ${proposal.case_id}` : ""}</p>
        <p>方案退款金额 · {amount(proposal.amount_raw)}</p>
        {role === "provider" && <>
          <p>本人承担 · {amount(proposal.provider_burden_raw)}</p>
          <p>{proposal.refund_executed ? "本人加购收益" : "本人拟保留加购收益"} · {amount(proposal.provider_income_retained_raw)}</p>
        </>}
        <p className="whitespace-pre-wrap break-words">{proposal.explanation}</p>
      </div>
      <section className="space-y-2 rounded-md bg-muted p-3" aria-label="本方案受影响履约段">
        <div>
          <h5 className="font-medium">本方案受影响履约段</h5>
          <p className="text-muted-foreground">只能核对当前付款方案列出的履约段，不能混用其他付款或转出、转回段。</p>
        </div>
        {scopeError ? <p role="alert" className="text-destructive">{scopeError}</p> : scopeRows.map(row => {
          const rowKey = scopeRowKey(row)
          const details = <span className="space-y-1">
            <span className="block font-medium">陪玩 {row.accountId} · 履约段 {row.segmentId}</span>
            <span className="block text-muted-foreground">份额 {amount(row.basisRaw)} · 退款基数 {amount(row.refundBasisRaw)} · 承担 {amount(row.burdenRaw)}</span>
          </span>
          return editable ? <label key={rowKey} className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border p-3">
            <input type="checkbox" className="mt-1 size-4" checked={selectedScope.has(rowKey)} disabled={busy}
              onChange={event => setSelectedScope(previous => {
                const next = new Set(previous)
                if (event.target.checked) next.add(rowKey)
                else next.delete(rowKey)
                return next
              })} />
            {details}
          </label> : <div key={rowKey} className="rounded-md border p-3">{details}</div>
        })}
        {editable && <p role="status" className="text-muted-foreground">已核对 {selectedScope.size}/{scopeRows.length} 个履约段；须全部勾选后才能确认。</p>}
      </section>
      <p className="text-muted-foreground">{own === true ? "你的同意已记录。" : own === false ? "你的异议已记录。" : "请核对本方案后选择是否同意。"}{proposal.refund_executed ? "退款已由客服执行，实际到账请查看钱包流水。" : "退款尚未执行。"}</p>
      {proposal.refund_executed && <p className="text-muted-foreground">历史退款状态仅用于核对，老板和陪玩均无退款执行权限。</p>}
      {!amountsKnown && <p role="alert" className="text-destructive">方案金额暂无法核对，请联系客服。</p>}
      {canChoose && scopeReady && <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error} onClick={() => { setIssue(""); setChoice({ paymentId: proposal.payment_id, proposalId: proposal.id, accepted: true }) }}>同意方案</button>
        <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error} onClick={() => { setIssue(""); setChoice({ paymentId: proposal.payment_id, proposalId: proposal.id, accepted: false }) }}>提出异议</button>
      </div>}
      {selected && canChoose && <form action={`/api${addonRefundProposalPath(orderId, proposal.payment_id, proposal.id)}`} method="post"
        aria-label="确认本次加购退款决定" className="space-y-2 rounded-md bg-muted p-3"
        onSubmit={event => { event.preventDefault(); void submit(selected) }}
      >
        <p>确认{selected.accepted ? "同意" : "对"}付款 {proposal.payment_id} 的方案 {proposal.id}{selected.accepted ? "？" : "提出异议？"}决定记录后请联系客服修改方案。</p>
        <p className="text-muted-foreground">本次操作只记录决定，退款由客服另行执行。</p>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={buttonClass} disabled={busy || load.loading || !!load.error || !scopeReady} aria-busy={busy}>{busy ? "正在记录…" : "确认提交"}</button>
          <button type="button" className={buttonClass} disabled={busy} onClick={() => setChoice(null)}>返回修改</button>
        </div>
      </form>}
    </article>}
    {pendingDecision && <div className="space-y-2 rounded-md border p-3">
      {original ? <>
        <p>将先读取原方案的本人决定，再核对{original.accepted ? "同意" : "异议"}结果。</p>
        <p className="text-muted-foreground">{recoveringRecorded
          ? "服务端已记录本人决定，将只核对并清除原凭证，不重新提交或改用其他履约段。"
          : "请重新勾选当前方案列出的受影响履约段；提交仍使用原付款、原方案和原决定。"}</p>
        <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error || (!recoveringRecorded && (!!scopeError || !scopeReady))} onClick={() => void submit(original)}>{busy ? "正在核对…" : "核对原决定"}</button>
      </> : <p>当前查看的不是待核对的原付款/方案，请点击“查看原决定”。</p>}
    </div>}
    {notice && <p role="status" className="text-muted-foreground">{notice}</p>}
  </section>
}
