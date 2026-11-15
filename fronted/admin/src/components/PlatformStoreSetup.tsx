import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { MutationDialog, Status } from "@/components/business"
import { platformRequest } from "@/lib/platform-session"
import { canCheckSetupItem, nextSetupStep, setupStatusLabels, setupStatusSummary, type SetupItem } from "@/lib/setup-checklist"
import PlatformPointsSetup, { type PointsTerms, type PointsUsage } from "@/components/PlatformPointsSetup"

type Evidence = SetupItem & { message: string; snapshot?: { fingerprint: string; fields: { label: string; value: string }[] } }
type Setup = { capability: string; store: { currency_name: string }; evidence: Evidence[]; capacity: { max_concurrent_orders: number; version: number } | null; grab_window: { window_seconds: number | null; version: number } | null; points_terms: PointsTerms | null; points_usage: PointsUsage | null; points_usage_enforcement_available: boolean; clocks?: { aftersale_hours: number; income_freeze_hours: number; version: number } | null; deposit?: { targets: { cash_target: number; earnings_target: number; version: number; deposit_percent: number }; percent_fingerprint: string; percent_state: "VALID" | "MISSING" | "INVALID" } | null; withdrawal?: { rules: { min_amount: number; balance_threshold: number | null; tax_rate: number }; fingerprint: string }; monthly_cutover?: { configured: boolean; activated_at: string | null; available: boolean } }

export function PlatformStoreSetup({ store, canEditClocks, canEditDeposit, canEditWithdraw, onClose }: { store: { id: number; name: string }; canEditClocks: boolean; canEditDeposit: boolean; canEditWithdraw: boolean; onClose: () => void }) {
  const [data, setData] = useState<Setup | null>(null)
  const settings = useRef<HTMLDivElement>(null)
  const [limit, setLimit] = useState("")
  const [grabSeconds, setGrabSeconds] = useState("")
  const [aftersale, setAftersale] = useState("")
  const [freeze, setFreeze] = useState("")
  const [cash, setCash] = useState("")
  const [earnings, setEarnings] = useState("")
  const [percent, setPercent] = useState("")
  const [withdrawMin, setWithdrawMin] = useState("")
  const [withdrawThreshold, setWithdrawThreshold] = useState("")
  const [withdrawRate, setWithdrawRate] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [blocked, setBlocked] = useState(false)
  const [confirmingCapacity, setConfirmingCapacity] = useState(false)
  const [confirmingGrab, setConfirmingGrab] = useState(false)
  const [confirmingClocks, setConfirmingClocks] = useState(false)
  const [confirmingDeposit, setConfirmingDeposit] = useState<"targets" | "percent" | null>(null)
  const [confirmingWithdraw, setConfirmingWithdraw] = useState(false)
  const [confirmingMonthly, setConfirmingMonthly] = useState(false)
  const [confirming, setConfirming] = useState<Evidence | null>(null)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true); setError("")
    platformRequest<Setup>(`store-management/${store.id}/setup`).then(result => {
      if (active) { setData(result); setLimit(result.capacity ? String(result.capacity.max_concurrent_orders) : ""); setGrabSeconds(result.grab_window?.window_seconds == null ? "" : String(result.grab_window.window_seconds)); setAftersale(result.clocks ? String(result.clocks.aftersale_hours) : ""); setFreeze(result.clocks ? String(result.clocks.income_freeze_hours) : ""); setCash(result.deposit ? String(result.deposit.targets.cash_target / 10) : ""); setEarnings(result.deposit ? String(result.deposit.targets.earnings_target / 10) : ""); setPercent(result.deposit ? String(result.deposit.targets.deposit_percent) : ""); setWithdrawMin(result.withdrawal ? String(result.withdrawal.rules.min_amount / 10) : ""); setWithdrawThreshold(result.withdrawal?.rules.balance_threshold == null ? "" : String(result.withdrawal.rules.balance_threshold / 10)); setWithdrawRate(result.withdrawal ? String(result.withdrawal.rules.tax_rate) : ""); setBlocked(false) }
    }).catch(cause => { if (active) { setData(null); setError(cause instanceof Error ? cause.message : "门店配置读取失败") } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [store.id, revision])
  const validLimit = /^[1-9]\d*$/.test(limit) && Number(limit) <= 2147483647
  const capacity = data?.capacity
  const grabWindow = data?.grab_window
  const clocks = data?.clocks
  const deposit = data?.deposit
  const withdrawal = data?.withdrawal
  const editableField: Record<string, string | undefined> = {
    capacity: capacity ? "limit" : undefined,
    grab_window: grabWindow ? "grab_seconds" : undefined,
    settlement_clocks: canEditClocks && clocks ? "aftersale" : undefined,
    deposit_targets: canEditDeposit && deposit ? "cash" : undefined,
    deposit_percent: canEditDeposit && deposit ? "percent" : undefined,
    withdraw: canEditWithdraw && withdrawal ? "withdraw_min" : undefined,
  }
  const mayConfirm = (item: Evidence) => canCheckSetupItem(item) && !!item.snapshot
  const next = data && nextSetupStep(data.evidence, item => !!editableField[item.key], mayConfirm)
  const focusConfig = (key: string) => {
    const name = editableField[key]
    if (name) settings.current?.querySelector<HTMLInputElement>(`input[name="${name}"]`)?.focus()
  }
  const validHours = (value: string) => /^\d+$/.test(value) && Number(value) <= 8760
  const validAmount = (value: string) => /^\d+(?:\.\d)?$/.test(value) && Number(value) > 0 && Math.round(Number(value) * 10) <= 2147483647
  const validPercent = /^\d+$/.test(percent) && Number(percent) <= 100
  const validWithdrawAmount = (value: string) => /^\d+(?:\.\d)?$/.test(value) && Math.round(Number(value) * 10) <= 2147483647
  const validWithdrawRate = /^\d+$/.test(withdrawRate) && Number(withdrawRate) <= 100
  return <Dialog open onOpenChange={open => { if (!open && !loading) onClose() }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
    <DialogHeader><DialogTitle>配置核对：{store.name}</DialogTitle><DialogDescription>逐项核对本店规则的当前值。正式开业另行核验。</DialogDescription></DialogHeader>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {blocked && <p role="alert" className="text-sm text-destructive">操作结果或配置版本需要核对，请重新读取后再操作。</p>}
    <div><Button size="sm" variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}>重新读取</Button></div>
    {loading && <p role="status" className="text-sm text-muted-foreground">正在读取本店配置…</p>}
    <section aria-label="门店设置清单" className="overflow-hidden rounded-lg border">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
        <div>
          <h3 className="font-medium">门店设置清单</h3>
          {data && <p className="mt-1 text-xs text-muted-foreground">{setupStatusSummary(data.evidence)}</p>}
        </div>
        {next && <Button size="sm" disabled={loading || blocked} onClick={() => next.kind === "confirm" ? setConfirming(next.item) : focusConfig(next.item.key)}>
          {next.kind === "confirm" ? "下一步：核对" : "下一步：配置"}{next.item.name}
        </Button>}
      </header>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <thead><tr className="border-b"><th className="p-2">配置</th><th>状态</th><th>当前值</th><th scope="col" data-table-action className="text-center">操作</th></tr></thead>
        <tbody>
          {data?.evidence.map(row => <tr className="border-b last:border-0" key={row.key}>
            <td className="p-2">{row.name}</td><td><Status value={row.status} label={setupStatusLabels[row.status]} tone={row.status === "INVALID" ? "destructive" : row.status === "MISSING" || row.status === "NEEDS_CONFIRMATION" ? "warning" : row.status === "CONFIRMED" ? "success" : row.status === "SAVED" ? "primary" : "muted"} /></td>
            <td>{row.snapshot ? row.snapshot.fields.map(field => `${field.label}：${field.value}`).join("；") : row.message}</td>
            <td data-table-action><div className="admin-table-actions ml-auto flex flex-wrap items-center justify-end gap-1">{mayConfirm(row) ? <Button actionTone="approve" size="sm" variant="outline" disabled={loading || blocked} onClick={() => setConfirming(row)}>核对并确认</Button>
              : editableField[row.key] && ["MISSING", "INVALID"].includes(row.status) ? <Button actionTone="edit" size="sm" variant="outline" disabled={loading || blocked} onClick={() => focusConfig(row.key)}>填写配置</Button> : null}</div></td>
          </tr>)}
          {!data?.evidence.length && <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">
            {data ? "暂无配置项" : loading ? "正在读取配置清单…" : "配置清单尚未读取"}
          </td></tr>}
        </tbody>
      </table></div>
    </section>
    {data && <div ref={settings} className="space-y-5">
      {data.monthly_cutover && <div className="rounded-lg border p-3 text-sm"><p className="font-medium">会员月券新规则</p>
        <p><Status value={data.monthly_cutover.configured} label={data.monthly_cutover.configured == null ? undefined : data.monthly_cutover.configured ? "已切换" : "尚未切换"} /> {data.monthly_cutover.configured && data.monthly_cutover.activated_at && `${new Date(data.monthly_cutover.activated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）`}</p>
        {data.monthly_cutover.configured && data.monthly_cutover.available === false && <p role="alert" className="text-destructive">本店已切换，但当前服务未启用新规则，请联系平台核对。</p>}
        {!data.monthly_cutover.configured && data.monthly_cutover.available && <Button size="sm" className="mt-2" disabled={loading || blocked} onClick={() => setConfirmingMonthly(true)}>核对切换规则</Button>}
      </div>}
      <div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>每位陪玩同时接单上限</span><Input name="limit" type="number" min={1} max={2147483647} step={1} value={limit} disabled={loading || blocked || !capacity} onChange={event => setLimit(event.target.value)} /></label>
        <Button disabled={loading || blocked || !capacity || !validLimit || (capacity.version > 0 && Number(limit) === capacity.max_concurrent_orders)} onClick={() => setConfirmingCapacity(true)}>保存名额</Button></div>
      <div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>抢单窗口（秒）</span><Input name="grab_seconds" type="number" min={1} max={86400} step={1} value={grabSeconds} disabled={loading || blocked || !grabWindow} onChange={event => setGrabSeconds(event.target.value)} /></label><Button disabled={loading || blocked || !grabWindow || !/^[1-9]\d*$/.test(grabSeconds) || Number(grabSeconds) > 86400 || Number(grabSeconds) === grabWindow.window_seconds} onClick={() => setConfirmingGrab(true)}>保存窗口</Button></div>
      <p className="text-xs text-muted-foreground">自动抢单尚未启用，当前订单仍按现有方式分配。</p>
      <PlatformPointsSetup store={{ ...store, currency_name: data.store.currency_name }} terms={data.points_terms} usage={data.points_usage} enforcement={data.points_usage_enforcement_available} disabled={loading || blocked} onSaved={() => setRevision(value => value + 1)} onUncertain={() => setBlocked(true)} />
      {canEditClocks && <div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>售后等待（小时）</span><Input name="aftersale" type="number" min={0} max={8760} step={1} value={aftersale} disabled={loading || blocked || !clocks} onChange={event => setAftersale(event.target.value)} /></label><label className="space-y-1 text-sm"><span>收益冻结（小时）</span><Input name="freeze" type="number" min={0} max={8760} step={1} value={freeze} disabled={loading || blocked || !clocks} onChange={event => setFreeze(event.target.value)} /></label><Button disabled={loading || blocked || !clocks || !validHours(aftersale) || !validHours(freeze) || (clocks.version > 0 && Number(aftersale) === clocks.aftersale_hours && Number(freeze) === clocks.income_freeze_hours)} onClick={() => setConfirmingClocks(true)}>保存时限</Button></div>}
      {canEditDeposit && <><div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>现金押金目标（{data.store.currency_name}）</span><Input name="cash" type="number" min={0.1} step={0.1} value={cash} disabled={loading || blocked || !deposit} onChange={event => setCash(event.target.value)} /></label><label className="space-y-1 text-sm"><span>单抵押金目标（{data.store.currency_name}）</span><Input name="earnings" type="number" min={0.1} step={0.1} value={earnings} disabled={loading || blocked || !deposit} onChange={event => setEarnings(event.target.value)} /></label><Button disabled={loading || blocked || !deposit || !validAmount(cash) || !validAmount(earnings) || (deposit.targets.version > 0 && Math.round(Number(cash)*10) === deposit.targets.cash_target && Math.round(Number(earnings)*10) === deposit.targets.earnings_target)} onClick={() => setConfirmingDeposit("targets")}>保存押金目标</Button></div><div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>单抵比例（%）</span><Input name="percent" type="number" min={0} max={100} step={1} value={percent} disabled={loading || blocked || !deposit} onChange={event => setPercent(event.target.value)} /></label><Button disabled={loading || blocked || !deposit || !validPercent} onClick={() => setConfirmingDeposit("percent")}>保存单抵比例</Button></div></>}
      {canEditWithdraw && <div className="flex items-end gap-3"><label className="space-y-1 text-sm"><span>提现单笔最低额（{data.store.currency_name}）</span><Input name="withdraw_min" type="number" min={0} step={0.1} value={withdrawMin} disabled={loading || blocked || !withdrawal} onChange={event => setWithdrawMin(event.target.value)} /></label><label className="space-y-1 text-sm"><span>提现余额门槛（{data.store.currency_name}）</span><Input name="withdraw_threshold" type="number" min={0} step={0.1} value={withdrawThreshold} disabled={loading || blocked || !withdrawal} onChange={event => setWithdrawThreshold(event.target.value)} /></label><label className="space-y-1 text-sm"><span>提现费率（%）</span><Input name="withdraw_rate" type="number" min={0} max={100} step={1} value={withdrawRate} disabled={loading || blocked || !withdrawal} onChange={event => setWithdrawRate(event.target.value)} /></label><Button disabled={loading || blocked || !withdrawal || !validWithdrawAmount(withdrawMin) || !validWithdrawAmount(withdrawThreshold) || !validWithdrawRate} onClick={() => setConfirmingWithdraw(true)}>保存提现规则</Button></div>}
    </div>}
    {confirmingCapacity && capacity && <MutationDialog title="确认接单名额" description={`「${store.name}」每位陪玩的上限由 ${capacity.max_concurrent_orders} 单调整为 ${limit} 单。`} disabled={blocked || loading} onClose={() => setConfirmingCapacity(false)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/capacity`, { method: "PUT", data: { max_concurrent_orders: Number(limit), version: capacity.version } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirmingGrab && grabWindow && <MutationDialog title="确认抢单窗口" description={`「${store.name}」抢单窗口由 ${grabWindow.window_seconds === undefined ? "未读取" : grabWindow.window_seconds === null ? "未配置" : `${grabWindow.window_seconds} 秒`}调整为 ${grabSeconds} 秒。`} disabled={blocked || loading} onClose={() => setConfirmingGrab(false)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/grab-window`, { method: "PUT", data: { window_seconds: Number(grabSeconds), version: grabWindow.version } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirmingClocks && clocks && <MutationDialog title="确认售后和收益时限" description={`「${store.name}」售后等待 ${clocks.aftersale_hours} → ${aftersale} 小时，收益冻结 ${clocks.income_freeze_hours} → ${freeze} 小时。已付款订单保留原有快照。`} disabled={blocked || loading} onClose={() => setConfirmingClocks(false)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/clocks`, { method: "PUT", data: { aftersale_hours: Number(aftersale), income_freeze_hours: Number(freeze), expected_version: clocks.version } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirmingDeposit && deposit && <MutationDialog title={confirmingDeposit === "targets" ? "确认押金目标" : "确认单抵比例"} description={confirmingDeposit === "targets" ? `现金押金 ${deposit.targets.cash_target / 10} → ${cash} ${data?.store.currency_name}；单抵押金 ${deposit.targets.earnings_target / 10} → ${earnings} ${data?.store.currency_name}。已缴押金保留，接单资格按新目标核对。` : `单抵比例${deposit.percent_state === "VALID" ? ` ${deposit.targets.deposit_percent}%` : `配置${deposit.percent_state === "MISSING" ? "尚未保存" : "无效"}，当前业务使用默认 ${deposit.targets.deposit_percent}%`} → ${percent}%。已付款订单按原比例快照执行。`} disabled={blocked || loading} onClose={() => setConfirmingDeposit(null)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/deposit/${confirmingDeposit}`, { method: "PUT", data: confirmingDeposit === "targets" ? { cash_target: Math.round(Number(cash) * 10), earnings_target: Math.round(Number(earnings) * 10), expected_version: deposit.targets.version } : { deposit_percent: Number(percent), fingerprint: deposit.percent_fingerprint } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirmingWithdraw && withdrawal && <MutationDialog title="确认提现规则" description={`单笔最低 ${withdrawMin} ${data?.store.currency_name}，可用余额须严格大于 ${withdrawThreshold} ${data?.store.currency_name}，费率 ${withdrawRate}%。`} disabled={blocked || loading} onClose={() => setConfirmingWithdraw(false)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/withdraw`, { method: "PUT", data: { min_amount: Math.round(Number(withdrawMin) * 10), balance_threshold: Math.round(Number(withdrawThreshold) * 10), tax_rate: Number(withdrawRate), fingerprint: withdrawal.fingerprint } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirmingMonthly && <MutationDialog title="确认切换会员月券" description={`「${store.name}」从确认时刻开始使用新规则。切换当月历史老板若无发券记录且无法核实，按已发 0 张处理；已有券保留，下月按配置发放。切换时间不可修改。`} disabled={blocked || loading} onClose={() => setConfirmingMonthly(false)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/monthly-cutover`, { method: "POST", data: { confirm: true } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }} />}
    {confirming?.snapshot && <MutationDialog title={`确认${confirming.name}`} description="请核对本店当前值。确认不修改配置，之后变化须重新确认。" disabled={blocked || loading} onClose={() => setConfirming(null)} onSubmit={async () => {
      try { await platformRequest(`store-management/${store.id}/setup/confirm/${confirming.key}`, { method: "POST", data: { fingerprint: confirming.snapshot?.fingerprint } }); setRevision(value => value + 1) }
      catch (cause) { setBlocked(true); throw cause }
    }}><dl className="space-y-2">{confirming.snapshot.fields.map(field => <div className="flex justify-between gap-3 text-sm" key={field.label}><dt>{field.label}</dt><dd className="font-medium">{field.value}</dd></div>)}</dl></MutationDialog>}
  </DialogContent></Dialog>
}
