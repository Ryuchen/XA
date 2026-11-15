import { Link } from "react-router"
import { Calculator, Percent, ShieldCheck, LockKeyhole, FileClock, RefreshCw } from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Status } from "@/components/Status"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAdminAuth } from "@/lib/auth"
import { coinText } from "@xa/shared"
import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import {
  Feedback,
  Field,
  MutationDialog,
  useLoad,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"
import { amountToXaCoin, xaCoinToAmount } from "@/lib/format"

export default function DefaultCommission({
  onClose,
}: {
  onClose: () => void
}) {
  const result = useLoad(
    async () => (await request({ url: "/config/commission" })).data
  )
  const [form, setForm] = useState<Row>({
    commission_type: "PERCENT",
    rate: 0,
    fixed: 0,
  })
  const [blocked, setBlocked] = useState(false)
  useEffect(() => {
    if (result.data) {
      setForm({
        commission_type: result.data.commission_type,
        rate: result.data.rate,
        fixed: Number(amountToXaCoin(result.data.commission_fixed)),
      })
      setBlocked(false)
    }
  }, [result.data])
  const auth = useAdminAuth(), locked = !result.data || result.loading || !!result.error || blocked || !auth.hasPerm("report:audit")
  const dirty = !!result.data && (form.commission_type !== result.data.commission_type || Number(form.rate) !== Number(result.data.rate) || (form.commission_type === "FIXED" && Number(form.fixed) !== Number(amountToXaCoin(result.data.commission_fixed))))
  return (
    <MutationDialog
      contentClassName="fin-commission-dialog sm:max-w-5xl"
      title="订单默认抽成"
      description={coinText("按比例或固定浪花设置默认抽成，后续新订单按适用规则生成快照，已有订单收益不变。")}
      onClose={onClose}
      disabled={locked || !dirty}
      readOnly={!auth.hasPerm("report:audit")}
      confirmDiscard
      submitText="保存默认配置"
      onSubmit={async () => {
        if (
          form.commission_type === "PERCENT" &&
          (!Number.isInteger(form.rate) || form.rate < 0 || form.rate > 100)
        )
          throw new Error("抽成比例须为 0–100 的整数。")
        const fixed = xaCoinToAmount(form.fixed)
        if (
          form.commission_type === "FIXED" &&
          (!Number.isSafeInteger(fixed) ||
            fixed < 0 ||
            Math.abs(Number(amountToXaCoin(fixed)) - Number(form.fixed)) > 1e-7)
        )
          throw new Error(coinText("固定抽成须为非负浪花，最多一位小数。"))
        try {
          await request({
            url: "/config/commission",
            method: "PUT",
            data: {
              commission_type: form.commission_type,
              commission_fixed: form.commission_type === "FIXED" ? fixed : 0,
              rate: form.rate,
            },
          })
          toast.success("默认抽成已保存")
        } catch (cause: any) {
          if (!cause.status || cause.status >= 500) setBlocked(true)
          throw cause
        }
      }}
    >
      <Feedback
        variant={result.data ? "inline" : "panel"}
        message={result.error}
        retry={result.reload}
      />
      {blocked && (
        <Feedback
          message="保存结果未确认，请重新读取并核对当前配置。"
          retry={result.reload}
        />
      )}
      {result.loading && !result.data ? (
        <AdminPageSkeleton page="commission-config" />
      ) : (
        result.data && (
          <div className="space-y-4 fin-commission-body">
            <section className="fin-policy"><ShieldCheck size={16} /><div><h2>默认值不是全平台统一抽成</h2><p>商品、活动及业务专属规则优先；礼物使用独立规则。保存只影响后续适用订单，已有订单仍按自身分账快照结算。</p></div></section>
            <dl className="fin-commission-snapshot"><div><dt>已保存默认</dt><dd>{result.data.commission_type === "FIXED" ? `${amountToXaCoin(result.data.commission_fixed)} ${coinText("浪花")}` : `${result.data.rate}%`}</dd></div><div><dt>旧手工报单保留比例</dt><dd>{result.data.rate}%</dd></div><div><dt>历史订单口径</dt><dd>分账快照</dd></div></dl>
            <div className="fin-form-split">
              <Card className="fin-section"><CardHeader><div><CardTitle><Percent size={16} />全局默认配置</CardTitle><CardDescription>当前读取的配置；多人修改时请重新核对后保存</CardDescription></div><Status label={!auth.hasPerm("report:audit") ? "只读权限" : dirty ? "有未保存修改" : "配置已读取"} tone={dirty ? "warning" : "muted"} readState={result.error ? "error" : result.loading ? "loading" : undefined} /></CardHeader><CardContent><fieldset disabled={locked} className="space-y-5"><div className="space-y-2"><Label>抽成方式</Label><div className="fin-commission-types" role="group" aria-label="订单默认抽成方式">{[["PERCENT", "百分比"], ["FIXED", "整单固定金额"]].map(([value, title]) => <Button key={value} type="button" variant={form.commission_type === value ? "secondary" : "ghost"} aria-pressed={form.commission_type === value} onClick={() => setForm(old => ({ ...old, commission_type: value }))}>{title}</Button>)}</div></div>
                {form.commission_type === "FIXED" ? <Field field={{ key: "default-fixed", label: coinText("整单固定抽成（浪花）"), type: "number", min: 0, step: 0.1 }} value={form.fixed} onChange={value => setForm(old => ({ ...old, fixed: value }))} /> : <Field field={{ key: "default-rate", label: "平台抽成（%）", type: "number", min: 0, max: 100, step: 1 }} value={form.rate} onChange={value => setForm(old => ({ ...old, rate: value }))} />}
                <p className="text-xs leading-5 text-muted-foreground">{form.commission_type === "FIXED" ? "整笔订单仅扣一次，实际分摊以订单报价和分账快照为准。" : "比例范围为 0–100 的整数，实际派单仍核对业务适用规则。"}</p>
                <dl className="grid grid-cols-2 gap-3 rounded-xl border bg-muted/20 p-4 text-xs"><div><dt className="text-muted-foreground">旧手工报单默认比例</dt><dd className="mt-2 font-medium">{form.rate}% · {form.commission_type === "FIXED" ? "保留比例" : "与默认比例一致"}</dd></div><div><dt className="text-muted-foreground">生效范围</dt><dd className="mt-2 font-medium">后续适用订单 · 不回写历史</dd></div></dl>
              </fieldset></CardContent></Card>
              <Card className="fin-section"><CardHeader><div><CardTitle><Calculator size={16} />普通订单分账试算</CardTitle><CardDescription>实际派单报价会返回适用规则和真实分账</CardDescription></div><Status label="通用试算待开放" tone="muted" /></CardHeader><CardContent className="space-y-4"><div className="grid grid-cols-[minmax(0,1fr)_132px] gap-3"><div className="space-y-2"><Label htmlFor="commission-preview-pending">{coinText("订单实付（浪花）")}</Label><Input id="commission-preview-pending" disabled placeholder="待开放" /></div><div className="space-y-2"><Label>陪玩人数</Label><Input disabled value="单陪 / 双陪" readOnly /></div></div><Button variant="outline" className="w-full" disabled><Calculator size={16} />按已保存配置试算</Button><div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed p-6 text-center"><LockKeyhole size={24} className="text-muted-foreground" /><p className="mt-3 text-sm font-medium">通用金额试算待开放</p><p className="mt-2 text-xs leading-5 text-muted-foreground">真实报价需选择老板、服务与陪玩；请从订单页发起派单报价核对。</p>{auth.hasPerm("order:view") && <Button asChild variant="ghost" className="mt-3"><Link to="/orders">前往订单报价</Link></Button>}</div></CardContent></Card>
            </div>
            <Card className="fin-section"><CardHeader><div><CardTitle><FileClock size={16} />规则与快照</CardTitle><CardDescription>优先核对实际订单返回的命中规则</CardDescription></div><Button variant="outline" type="button" onClick={result.reload} disabled={result.loading}><RefreshCw size={16} />重新读取配置</Button></CardHeader><CardContent><div className="grid grid-cols-3 gap-3">{[["历史订单", "保留订单已有分账快照，不读取新默认值回写。"], ["礼物与专属业务", "使用各自适用规则，不按本页默认值推测收益。"], ["全局默认", "未命中优先规则时使用；以真实派单报价为准。"]].map(([title, text]) => <section className="rounded-xl border p-4" key={title}><h3 className="text-sm font-semibold">{title}</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{text}</p></section>)}</div></CardContent></Card>
          </div>
        )
      )}
    </MutationDialog>
  )
}
