import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { formatDateTime } from "@/lib/format"
import { platformGuaranteeClosureAccess } from "@/lib/platform-guarantee-closure"
import {
  beginClosureReadinessLoad,
  closureReadinessMode,
  closureReadinessReasonLabel,
  closureReadinessScopeKey,
  completeClosureReadinessLoad,
  emptyClosureReadinessState,
  failClosureReadinessLoad,
  formatClosureReadinessValue,
  requestClosureReadiness,
  type ClosureReadiness,
  type ClosureReadinessBlocker,
  type ClosureReadinessDomainStatus,
  type ClosureReadinessScope,
} from "@/lib/platform-closure-readiness"
import { platformRequest, type PlatformProfile, type PlatformStore } from "@/lib/platform-session"

type StoreTarget = Pick<PlatformStore, "id" | "slug" | "name">

const metricLabels: Record<string, string> = {
  ORDERS: "订单",
  RESERVATIONS: "预约",
  AFTERSALES: "售后",
  REPORTS: "报单审核",
  INCOME_HOLDS: "收益结算与冻结",
  WITHDRAWALS: "提现",
  DEPOSIT_DEFICITS: "押金缺口",
  RECOVERY_PERMITS: "补差资格",
  GIFT_APPROVALS: "赠送审批",
  REWARD_CLAIMS: "奖励领取",
  PAYROLL: "工资",
  OTHER_RIGHTS: "其他既有权益",
}

function domainStatusLabel(status: ClosureReadinessDomainStatus) {
  if (status === "CLEAR") return "CLEAR · 本分域未报告阻断"
  if (status === "BLOCKED") return "BLOCKED · 存在阻断"
  return "UNKNOWN · 读取不完整"
}

function Digest({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-all font-mono text-[11px]">{value}</dd></div>
}

function Blockers({ items }: { items: ClosureReadinessBlocker[] }) {
  if (!items.length) return <p className="mt-3 text-xs text-muted-foreground">本分域未返回阻断项；这不代表全店可以结清。</p>
  return <ul className="mt-3 space-y-2 text-xs">{items.map((item, index) => <li key={`${item.code}:${item.resource}:${item.reference}:${index}`} className="rounded-md bg-muted/20 p-2">
    <span className="font-medium">{item.code}</span><span className="text-muted-foreground"> · {item.resource}</span>
    <p className="mt-1 break-all font-mono text-[11px]">{item.reference}</p>
  </li>)}</ul>
}

function ReadinessView({ data }: { data: ClosureReadiness }) {
  const totals = data.funds.totals
  const amounts: Array<[string, number | null]> = [
    ["钱包余额（内部单位）", totals?.walletUnits ?? null],
    ["可核本金（内部单位）", totals?.principalUnits ?? null],
    ["赠送（内部单位）", totals?.giftUnits ?? null],
    ["赔付（内部单位）", totals?.compensationUnits ?? null],
    ["积分（百分位）", totals?.pointsHundredths ?? null],
    ["已冻结（内部单位）", totals?.frozenUnits ?? null],
    ["可退人民币（分）", totals?.refundFen ?? null],
    ["已付人民币（分）", totals?.paidFen ?? null],
  ]
  return <div className="space-y-4">
    <div className="rounded-lg bg-muted/20 p-4">
      <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{data.status}</Badge><Badge variant="outline">can_finalize=false</Badge><span className="text-xs text-muted-foreground">观测时间：{formatDateTime(data.observedAt)}</span></div>
      <dl className="mt-3 space-y-3 text-xs"><Digest label="全店预检快照 SHA-256" value={data.snapshotSha256} /></dl>
      <div className="mt-4"><h5 className="text-xs font-semibold">顶层阻断原因</h5><ul className="mt-2 grid gap-2 md:grid-cols-2">{data.blockingReasons.map(code => <li key={code} className="rounded-md border bg-background p-3 text-xs"><span>{closureReadinessReasonLabel(code)}</span><p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{code}</p></li>)}</ul></div>
    </div>

    <section className="rounded-lg border p-4" aria-labelledby="closure-readiness-funds">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 id="closure-readiness-funds" className="font-semibold">老板资金</h4><Badge variant="outline">{domainStatusLabel(data.funds.status)}</Badge></div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">{([
        ["老板账户数", data.funds.complete ? data.funds.accountCount : null],
        ["已完成返还账户数", data.funds.complete ? data.funds.completedCount : null],
        ["明确零余额账户数", data.funds.complete ? data.funds.zeroBalanceCount : null],
      ] as Array<[string, number | null]>).map(([label, value]) => <div key={label} className="rounded-lg bg-muted/20 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{formatClosureReadinessValue(value)}</p></div>)}</div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{amounts.map(([label, value]) => <div key={label} className="rounded-lg bg-muted/20 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{formatClosureReadinessValue(value)}</p></div>)}</div>
      {!totals && <p role="status" className="mt-3 text-xs text-muted-foreground">资金读取不完整，八项合计整体待核；“—”不表示 0。</p>}
      <Blockers items={data.funds.blockers} />
      <dl className="mt-3 text-xs"><Digest label="资金事实 SHA-256" value={data.funds.factsSha256} /></dl>
    </section>

    <section className="rounded-lg border p-4" aria-labelledby="closure-readiness-obligations">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 id="closure-readiness-obligations" className="font-semibold">履约、售后、提现及其他义务</h4><Badge variant="outline">{domainStatusLabel(data.obligations.status)}</Badge></div>
      {data.obligations.metrics.length ? <div className="mt-3 overflow-x-auto rounded-lg border"><table className="w-full min-w-[420px] text-left text-sm"><caption className="sr-only">全店业务义务预检指标</caption><thead><tr className="border-b text-xs text-muted-foreground"><th className="p-2">义务类别</th><th className="p-2">代码</th><th className="p-2 text-right">数量</th></tr></thead><tbody>{data.obligations.metrics.map(metric => <tr key={metric.code} className="border-b last:border-0"><td className="p-2">{metricLabels[metric.code] || "其他义务"}</td><td className="p-2 font-mono text-xs">{metric.code}</td><td className="p-2 text-right font-semibold">{formatClosureReadinessValue(metric.count)}</td></tr>)}</tbody></table></div> : <p role="status" className="mt-3 rounded-lg bg-muted/20 p-3 text-xs text-muted-foreground">未返回义务指标；空指标不代表零义务，仍须依据分域状态和阻断项复核。</p>}
      <Blockers items={data.obligations.blockers} />
      <dl className="mt-3 text-xs"><Digest label="义务事实 SHA-256" value={data.obligations.factsSha256} /></dl>
    </section>

    <section className="rounded-lg border p-4" aria-labelledby="closure-readiness-policy">
      <h4 id="closure-readiness-policy" className="font-semibold">费用规则与最终写入能力</h4>
      <div className="mt-3 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-muted/20 p-3"><p className="text-xs text-muted-foreground">费用规则</p><p className="mt-1 font-semibold">PENDING · 仍待确认</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-xs text-muted-foreground">最终写入屏障</p><p className="mt-1 font-semibold">UNAVAILABLE · 尚未接通</p></div></div>
      <Blockers items={data.policy.blockers} />
    </section>
  </div>
}

export default function PlatformClosureReadiness({ store, profile, sessionId, onAuthorizationDenied }: {
  store: StoreTarget
  profile: PlatformProfile
  sessionId: string
  onAuthorizationDenied: () => void
}) {
  const access = platformGuaranteeClosureAccess(profile, store.slug)
  const scope = useMemo<ClosureReadinessScope>(() => ({
    storeId: store.id,
    storeSlug: store.slug,
    actorId: profile.id,
    sessionId,
    scopeSignature: access.scopeSignature,
  }), [store.id, store.slug, profile.id, sessionId, access.scopeSignature])
  const scopeKey = useMemo(() => closureReadinessScopeKey(scope), [scope])
  const [state, setState] = useState(() => emptyClosureReadinessState(scopeKey))
  const mounted = useRef(false)
  const sequence = useRef(0)
  const activeController = useRef<AbortController | null>(null)
  const deniedCallback = useRef(onAuthorizationDenied)
  deniedCallback.current = onAuthorizationDenied

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      sequence.current += 1
      activeController.current?.abort()
    }
  }, [])

  const load = useCallback(async () => {
    if (!access.closureView) return
    activeController.current?.abort()
    const controller = new AbortController()
    const requestSequence = ++sequence.current
    activeController.current = controller
    setState(current => beginClosureReadinessLoad(current, scopeKey))
    try {
      const data = await requestClosureReadiness(store.id, {
        signal: controller.signal,
        request: path => platformRequest<unknown>(path),
      })
      if (!mounted.current || controller.signal.aborted || requestSequence !== sequence.current) return
      setState(current => current.scopeKey === scopeKey ? completeClosureReadinessLoad(current, scopeKey, data) : current)
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || requestSequence !== sequence.current) return
      if ((cause as { status?: number })?.status === 403) deniedCallback.current()
      setState(current => current.scopeKey === scopeKey ? failClosureReadinessLoad(current, scopeKey, cause) : current)
    } finally {
      if (activeController.current === controller) activeController.current = null
    }
  }, [access.closureView, scopeKey, store.id])

  useEffect(() => {
    void load()
    return () => {
      activeController.current?.abort()
      sequence.current += 1
    }
  }, [load])

  if (!access.closureView) return <p role="alert" className="rounded-lg bg-muted/20 p-4 text-sm">当前平台账号没有本店闭店预检读取权限。</p>
  const visibleState = state.scopeKey === scopeKey ? state : emptyClosureReadinessState(scopeKey)
  const mode = closureReadinessMode(visibleState)
  const data = visibleState.snapshot?.data || null

  return <section className="space-y-4 rounded-xl border p-4" aria-labelledby="closure-readiness-title" aria-busy={visibleState.loading || undefined}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 id="closure-readiness-title" className="font-semibold">结清预检</h3><p className="mt-1 text-xs text-muted-foreground">按门店、平台账号、会话和授权范围读取全店事实；不会从分页清单补算合计。</p></div><Button type="button" size="sm" variant="outline" onClick={() => void load()} disabled={visibleState.loading}>{visibleState.loading ? "正在读取…" : "刷新预检"}</Button></div>
    <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm"><strong>预检不是结清</strong>，快照摘要也不是结清授权。<strong>当前不可最终停用</strong>；本区域只提供事实读取与手动刷新。</p>
    {visibleState.error && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">{visibleState.error.message}</p>}
    {visibleState.stale && data && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">刷新未完成，以下为旧快照，事实可能已经变化；旧完整结果不能视为当前可结清。</p>}
    {mode === "loading" && <div role="status" className="space-y-3 rounded-lg bg-muted/20 p-4"><p className="text-sm text-muted-foreground">正在读取本店资金、业务义务和政策门禁…</p><div className="grid gap-3 sm:grid-cols-3" aria-hidden="true">{[0, 1, 2].map(item => <div key={item} className="h-20 animate-pulse rounded-lg bg-muted/40" />)}</div></div>}
    {mode === "error" && <p role="status" className="rounded-lg bg-muted/20 p-4 text-sm text-muted-foreground">尚未取得可核验的预检快照；金额和义务保持待核，不显示为零。</p>}
    {mode === "empty" && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">账户数为 0 且未返回义务指标；空指标不代表零义务，空账户也不代表满足结清条件。</p>}
    {data && <ReadinessView data={data} />}
  </section>
}
