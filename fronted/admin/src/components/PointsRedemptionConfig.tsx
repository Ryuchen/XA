import { useEffect, useState } from "react"
import { coinUnit } from "@xa/shared"
import { toast } from "sonner"
import { request } from "@/api/request"
import { Feedback, MutationDialog, RefreshButton, useLoad } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAdminAuth } from "@/lib/auth"
import { DataRegion, ReadErrorNotice } from "@/components/page-data-state"

function hundredths(value: string): number | null {
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value)) return null
  const [whole, fraction = ""] = value.split(".")
  const result = Number(whole) * 100 + Number((fraction + "00").slice(0, 2))
  return Number.isSafeInteger(result) ? result : null
}

export default function PointsRedemptionConfig() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: "/config/points-redemption/" })).data)
  const [points, setPoints] = useState("")
  const [coins, setCoins] = useState("")
  const [ratio, setRatio] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  useEffect(() => {
    if (!result.data) return
    setPoints(result.data.group_point_hundredths == null ? "" : String(result.data.group_point_hundredths / 100))
    setCoins(result.data.group_coin_units == null ? "" : String(result.data.group_coin_units / 10))
    setRatio(result.data.max_ratio_basis_points == null ? "" : String(result.data.max_ratio_basis_points / 100))
    setBlocked("")
  }, [result.data])
  const pointUnits = hundredths(points)
  const cap = hundredths(ratio)
  const coinUnits = /^[1-9]\d*$/.test(coins) ? Number(coins) * 10 : NaN
  const valid = pointUnits !== null && pointUnits > 0 && pointUnits <= 2147483647 &&
    Number.isSafeInteger(coinUnits) && coinUnits <= 2147483647 &&
    cap !== null && cap >= 0 && cap < 10000
  const changed = result.data && (pointUnits !== result.data.group_point_hundredths ||
    coinUnits !== result.data.group_coin_units || cap !== result.data.max_ratio_basis_points)
  const locked = result.loading || !result.data || !!result.error || !!blocked || !auth.hasPerm("dictionary:edit")
  return <Card>
    <CardHeader><CardTitle>积分抵扣规则</CardTitle>
      <CardDescription>按完整兑换组使用，单笔上限须低于 100%。规则保存后，积分支付尚未开放。</CardDescription></CardHeader>
    <CardContent className="space-y-3">
      {result.data && <ReadErrorNotice error={result.error} retry={result.reload} hasSnapshot />}
      <Feedback message={blocked} retry={result.reload} />
      {!result.data && <DataRegion label="积分抵扣规则" loading={result.loading} hasSnapshot={false} error={result.error} retry={result.reload}>{null}</DataRegion>}
        <div className="space-y-2"><Label htmlFor="redemption-points">每组积分</Label><Input id="redemption-points" inputMode="decimal" value={points} onChange={event => setPoints(event.target.value)} disabled={locked} /></div>
        <div className="space-y-2"><Label htmlFor="redemption-coins">每组抵扣{coinUnit()}（整数）</Label><Input id="redemption-coins" inputMode="numeric" value={coins} onChange={event => setCoins(event.target.value)} disabled={locked} /></div>
        <div className="space-y-2"><Label htmlFor="redemption-cap">单笔最高抵扣比例（%）</Label><Input id="redemption-cap" inputMode="decimal" value={ratio} onChange={event => setRatio(event.target.value)} disabled={locked} /></div>
        <div className="flex gap-2"><RefreshButton loading={result.loading} reload={result.reload} />
          {auth.hasPerm("dictionary:edit") && <Button size="sm" disabled={locked || !valid || !changed} onClick={() => setConfirm(true)}>保存规则</Button>}
        </div>
      {confirm && result.data && <MutationDialog title="确认积分抵扣规则" description={`本店每 ${points} 积分抵 ${coins} ${coinUnit()}，单笔最高抵扣 ${ratio}%。保存不会开放积分支付。`} onClose={() => setConfirm(false)} onSubmit={async () => {
        try {
          const response = await request({ url: "/config/points-redemption/", method: "PUT", data: {
            group_point_hundredths: pointUnits, group_coin_units: coinUnits,
            max_ratio_basis_points: cap, version: result.data.version,
          } })
          result.setData(response.data)
          toast.success("积分抵扣规则已保存")
        } catch (cause: any) {
          setBlocked(cause.status === 409 ? "配置已变化，请重新读取后核对。" : "保存结果未确认，请先刷新并核对当前配置。")
          throw cause
        }
      }} />}
    </CardContent>
  </Card>
}
