import { useEffect, useState } from "react"
import { toast } from "sonner"
import { request } from "@/api/request"
import { Feedback, Field, MutationDialog, OptionPicker, RefreshButton, useLoad, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useAdminAuth } from "@/lib/auth"

const fields = [["game_region", "游戏大区"], ["game_nickname", "游戏昵称"], ["game_uid", "游戏 UID"]] as const

export default function OrderAccountFields({ onClose }: { onClose: () => void }) {
  const auth = useAdminAuth()
  const [scope, setScope] = useState("game")
  const [target, setTarget] = useState<number | null>(null)
  const [selected, setSelected] = useState<string[] | null>([])
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  const result = useLoad<Row | null>(async () => target
    ? (await request<Row>({ url: `/config/order-account-fields/${scope}/${target}/` })).data : null, [scope, target])
  const data = result.data?.scope === scope && Number(result.data?.target?.id) === Number(target) ? result.data : null
  useEffect(() => {
    if (!data) return
    setSelected(data.fields === null ? null : [...data.fields])
    setBlocked("")
  }, [data])
  const changed = !!data && (data.version === 0 || JSON.stringify(selected) !== JSON.stringify(data.fields))
  const locked = result.loading || !!result.error || !!blocked || !data || !auth.hasPerm("dictionary:edit")
  return <>
    <Dialog open onOpenChange={open => { if (!open && !confirm) onClose() }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle>下单游戏资料</DialogTitle><DialogDescription>按本店游戏配置必填项；服务可继承游戏规则，也可单独覆盖。未配置或未选中的资料均为选填。</DialogDescription></DialogHeader>
        <div className="space-y-4">
          <Field field={{key: "account-fields-scope", label: "配置范围", type: "select", options: [["game", "游戏"], ["service", "服务"]]}}
            value={scope} onChange={value => {setTarget(null); setScope(String(value)); setBlocked("")}} />
          <OptionPicker resource={scope === "game" ? "game-categories" : "service-items"}
            endpoint="/config/order-account-fields/targets/" query={{scope}} label={scope === "game" ? "选择游戏" : "选择服务"}
            value={target} onChange={value => {setTarget(value ? Number(value) : null); setBlocked("")}} />
          <Feedback message={result.error || blocked} retry={result.reload} />
          {data && <>
            {scope === "service" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected === null}
              disabled={locked} onChange={event => setSelected(event.target.checked ? null : [])} />继承所属游戏的规则</label>}
            {selected !== null && <fieldset disabled={locked} className="grid gap-3"><legend className="mb-2 text-sm font-medium">必填项目</legend>
              {fields.map(([field, label]) => <label key={field} className="flex items-center gap-2 text-sm"><input type="checkbox"
                checked={selected.includes(field)} onChange={event => setSelected(event.target.checked
                  ? fields.map(([key]) => key).filter(key => selected.includes(key) || key === field)
                  : selected.filter(key => key !== field))} />{label}</label>)}
            </fieldset>}
            <div className="flex gap-2"><RefreshButton loading={result.loading} reload={result.reload} />
              {auth.hasPerm("dictionary:edit") && <Button size="sm" disabled={locked || !changed} onClick={() => setConfirm(true)}>保存规则</Button>}</div>
          </>}
        </div>
      </DialogContent>
    </Dialog>
    {confirm && data && <MutationDialog title="确认游戏资料规则" description={`${data.target.name}：${selected === null ? "继承游戏规则" : selected.length ? `必填${selected.map(value => fields.find(([key]) => key === value)?.[1]).join("、")}` : "所有资料选填"}。新订单及新预约将按该规则校验。`}
      onClose={() => setConfirm(false)} onSubmit={async () => {
        try {
          const response = await request<Row>({url: `/config/order-account-fields/${scope}/${target}/`, method: "PUT",
            data: {version: data.version, fields: selected}})
          result.setData(response.data); toast.success("资料规则已保存")
        } catch (error: any) {
          setBlocked(error.status === 409 ? "规则已变化，请刷新后重新核对。" : "保存结果未确认，请刷新后核对。")
          throw error
        }
      }} />}
  </>
}
