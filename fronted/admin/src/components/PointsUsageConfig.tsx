import { useEffect, useState } from "react"
import { toast } from "sonner"
import { request } from "@/api/request"
import { Feedback, MutationDialog, RefreshButton, useLoad } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { useAdminAuth } from "@/lib/auth"
import { DataRegion, ReadErrorNotice } from "@/components/page-data-state"

const fields = [["issuance_enabled", "积分发放"], ["redemption_enabled", "积分抵扣"], ["shop_enabled", "积分商城"], ["lottery_enabled", "积分抽奖"]] as const
type Field = typeof fields[number][0]
type Draft = Record<Field, boolean>

export default function PointsUsageConfig() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: "/config/points-usage/" })).data)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [blocked, setBlocked] = useState("")
  const [confirm, setConfirm] = useState(false)
  useEffect(() => {
    if (!result.data?.enforcement_available) return
    setDraft({ issuance_enabled: result.data.issuance_enabled, redemption_enabled: result.data.redemption_enabled, shop_enabled: result.data.shop_enabled,
      lottery_enabled: result.data.lottery_enabled })
    setBlocked("")
  }, [result.data])
  if (result.data && !result.data.enforcement_available && !result.error) return null
  const locked = result.loading || !result.data || !draft || !!result.error || !!blocked || !auth.hasPerm("dictionary:edit")
  const changed = draft && fields.some(([field]) => draft[field] !== result.data?.[field])
  return <Card><CardHeader><CardTitle>积分使用开关</CardTitle>
    <CardDescription>发放、抵扣、商城或抽奖任一项停用时，所有积分使用暂停；全部启用后恢复。已获得的积分和权益保留。</CardDescription></CardHeader>
    <CardContent className="space-y-3">
      {result.data && <ReadErrorNotice error={result.error} retry={result.reload} hasSnapshot />}
      <Feedback message={blocked} retry={result.reload} />
      {!result.data && <DataRegion label="积分使用开关" loading={result.loading} hasSnapshot={false} error={result.error} retry={result.reload}>{null}</DataRegion>}
        {result.data && !result.data.issuance_configured && <p className="text-sm">积分发放使用初始默认值，保存后记录本店配置。</p>}
        {!auth.hasPerm("engagement:edit") && <p className="text-sm">修改积分发放需会员运营编辑权限。</p>}
        <p className="text-sm" role="status">当前积分使用：{result.data ? (result.data.usage_enabled ? "可用" : "暂停") : "未读取"}；积分抵扣付款尚未开放。</p>
        {fields.map(([field, label]) => <div className="flex items-center justify-between" key={field}>
          <span id={`points-switch-${field}`}>{label}</span>
          {draft && result.data ? <Switch aria-labelledby={`points-switch-${field}`} checked={draft[field]} disabled={locked || (field === "issuance_enabled" && !auth.hasPerm("engagement:edit"))}
            onCheckedChange={value => setDraft(current => current ? { ...current, [field]: value } : current)} /> : <span className="text-sm text-muted-foreground" aria-label={`${label}未读取`}>—</span>}
        </div>)}
        <div className="flex gap-2"><RefreshButton loading={result.loading} reload={result.reload} />
          {auth.hasPerm("dictionary:edit") && <Button size="sm" disabled={locked || !changed} onClick={() => setConfirm(true)}>保存开关</Button>}
        </div>
      {confirm && draft && result.data && <MutationDialog title="确认积分使用开关"
        description={fields.map(([field, label]) => `${label}${draft[field] ? "启用" : "停用"}`).join("，") + "。任一项停用时所有积分使用暂停。"}
        onClose={() => setConfirm(false)} onSubmit={async () => {
          try {
            const response = await request({ url: "/config/points-usage/", method: "PUT", data: { ...draft, version: result.data.version, expected_issuance_enabled: result.data.issuance_enabled } })
            result.setData(response.data); toast.success("积分使用开关已保存")
          } catch (cause: any) {
            setBlocked(cause.status === 409 ? "配置已变化，请重新读取后核对。" : "保存结果未确认，请先刷新并核对当前配置。")
            throw cause
          }
        }} />}
    </CardContent></Card>
}
