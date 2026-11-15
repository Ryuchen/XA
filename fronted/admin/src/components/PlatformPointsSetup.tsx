import { useEffect, useState } from "react"
import { MutationDialog, Status } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { platformRequest } from "@/lib/platform-session"

export type PointsTerms = { group_point_hundredths: number | null; group_coin_units: number | null; max_ratio_basis_points: number | null; version: number }
export type PointsUsage = { issuance_enabled: boolean; redemption_enabled: boolean; shop_enabled: boolean; lottery_enabled: boolean; version: number; usage_enabled: boolean }
const switchFields = [["issuance_enabled", "积分发放"], ["redemption_enabled", "积分抵扣"], ["shop_enabled", "积分商城"], ["lottery_enabled", "积分抽奖"]] as const
type Draft = Pick<PointsUsage, typeof switchFields[number][0]>

function hundredths(value: string): number | null {
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value)) return null
  const [whole, fraction = ""] = value.split(".")
  const result = Number(whole) * 100 + Number((fraction + "00").slice(0, 2))
  return Number.isSafeInteger(result) ? result : null
}

export default function PlatformPointsSetup({ store, terms, usage, enforcement, disabled, onSaved, onUncertain }: {
  store: { id: number; name: string; currency_name: string }; terms: PointsTerms | null; usage: PointsUsage | null;
  enforcement: boolean; disabled: boolean; onSaved: () => void; onUncertain: () => void
}) {
  const [points, setPoints] = useState("")
  const [coins, setCoins] = useState("")
  const [ratio, setRatio] = useState("")
  const [draft, setDraft] = useState<Draft | null>(null)
  const [confirm, setConfirm] = useState<"terms" | "usage" | null>(null)
  useEffect(() => {
    setPoints(terms?.group_point_hundredths == null ? "" : String(terms.group_point_hundredths / 100))
    setCoins(terms?.group_coin_units == null ? "" : String(terms.group_coin_units / 10))
    setRatio(terms?.max_ratio_basis_points == null ? "" : String(terms.max_ratio_basis_points / 100))
    setDraft(usage ? { issuance_enabled: usage.issuance_enabled, redemption_enabled: usage.redemption_enabled,
      shop_enabled: usage.shop_enabled, lottery_enabled: usage.lottery_enabled } : null)
  }, [terms, usage])
  const pointUnits = hundredths(points), cap = hundredths(ratio)
  const coinUnits = /^[1-9]\d*$/.test(coins) ? Number(coins) * 10 : NaN
  const valid = pointUnits !== null && pointUnits > 0 && pointUnits <= 2147483647 &&
    Number.isSafeInteger(coinUnits) && coinUnits <= 2147483647 && cap !== null && cap >= 0 && cap < 10000
  const changedTerms = terms && (terms.group_point_hundredths !== pointUnits || terms.group_coin_units !== coinUnits || terms.max_ratio_basis_points !== cap)
  const changedUsage = draft && usage && switchFields.some(([key]) => draft[key] !== usage[key])
  return <section className="space-y-3 rounded-lg border p-3" aria-label="门店积分配置">
    <p className="font-medium">积分抵扣规则</p>
    <p className="text-sm text-muted-foreground">按完整兑换组抵扣，单笔上限须低于 100%；积分付款尚未开放。</p>
    {!terms && <p role="alert" className="text-sm text-destructive">本店积分兑换规则无法读取，请先核对。</p>}
    {terms && <div className="flex flex-wrap items-end gap-3">
      <label className="space-y-1 text-sm"><span>每组积分</span><Input name="points" inputMode="decimal" value={points} disabled={disabled} onChange={e => setPoints(e.target.value)} /></label>
      <label className="space-y-1 text-sm"><span>每组抵扣{store.currency_name}（整数）</span><Input name="coins" inputMode="numeric" value={coins} disabled={disabled} onChange={e => setCoins(e.target.value)} /></label>
      <label className="space-y-1 text-sm"><span>单笔最高抵扣比例（%）</span><Input name="ratio" inputMode="decimal" value={ratio} disabled={disabled} onChange={e => setRatio(e.target.value)} /></label>
      <Button disabled={disabled || !valid || !changedTerms} onClick={() => setConfirm("terms")}>保存积分规则</Button>
    </div>}
    {enforcement && !usage && <p role="alert" className="text-sm text-destructive">本店积分使用开关无法读取，请先核对。</p>}
    {enforcement && usage && draft && <div className="space-y-2"><p className="font-medium">积分使用开关</p>
      <p className="text-sm">当前积分使用：<Status value={usage.usage_enabled} label={usage.usage_enabled ? "可用" : "暂停"} readState={usage.usage_enabled == null ? "unread" : undefined} />；任一项停用时全部暂停使用。</p>
      <div className="flex flex-wrap gap-4">{switchFields.map(([key, label]) => <label className="flex items-center gap-2 text-sm" key={key}>
        <Switch name={label} aria-label={label} checked={draft[key]} disabled={disabled} onCheckedChange={value => setDraft(old => old ? { ...old, [key]: value } : old)} />{label}
      </label>)}</div>
      <Button size="sm" disabled={disabled || !changedUsage} onClick={() => setConfirm("usage")}>保存积分开关</Button>
    </div>}
    {confirm === "terms" && terms && <MutationDialog title="确认积分抵扣规则" description={`「${store.name}」每 ${points} 积分抵 ${coins} ${store.currency_name}，单笔最高抵扣 ${ratio}%。保存不会开放积分付款。`}
      disabled={disabled} onClose={() => setConfirm(null)} onSubmit={async () => {
        try { await platformRequest(`store-management/${store.id}/setup/points-terms`, { method: "PUT", data: {
          group_point_hundredths: pointUnits, group_coin_units: coinUnits, max_ratio_basis_points: cap, version: terms.version } }); onSaved() }
        catch (error) { onUncertain(); throw error }
      }} />}
    {confirm === "usage" && usage && draft && <MutationDialog title="确认积分使用开关" description={`「${store.name}」${switchFields.map(([key, label]) => `${label}${draft[key] ? "启用" : "停用"}`).join("，")}。任一项停用时全部暂停使用。`}
      disabled={disabled} onClose={() => setConfirm(null)} onSubmit={async () => {
        try { await platformRequest(`store-management/${store.id}/setup/points-usage`, { method: "PUT", data: {
          ...draft, version: usage.version, expected_issuance_enabled: usage.issuance_enabled } }); onSaved() }
        catch (error) { onUncertain(); throw error }
      }} />}
  </section>
}
