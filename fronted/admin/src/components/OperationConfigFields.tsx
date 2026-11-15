import { DataValue } from "@/components/page-data-state"
import { CalendarClock, Coins, Crown } from "@/components/ui/icons"
import { coinUnit } from "@xa/shared"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Input } from "@/components/ui/input"
import { Field, type Row } from "@/components/business"

export default function OperationConfigFields({
  form,
  disabled,
  onChange,
  known = true,
}: {
  form: Row
  disabled: boolean
  onChange: (form: Row) => void
  known?: boolean
}) {
  const set = (key: string, value: unknown) =>
    onChange({ ...form, [key]: value })
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Coins className="size-4 text-primary" />
            积分与会员
          </CardTitle>
          <CardDescription>
            新配置用于后续业务，不回写历史账务。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-0 pt-0">
          <div className="flex items-start justify-between gap-6 py-4">
            <div>
              <label htmlFor="operation-points" className="text-sm font-medium">
                消费积分
              </label>
              <p className="mt-2 max-w-xl text-xs leading-5 text-muted-foreground">
                关闭后停止累积新的消费积分，已有余额与流水保留。
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-muted-foreground">
                <DataValue known={known}>{form.points_enabled ? "已开启" : "已关闭"}</DataValue>
              </span>
              <DataValue known={known}><Switch
                id="operation-points"
                checked={!!form.points_enabled}
                disabled={disabled}
                onCheckedChange={(value) => set("points_enabled", value)}
              /></DataValue>
            </div>
          </div>
          <div className="grid gap-4 border-y py-5 sm:grid-cols-[minmax(0,1fr)_180px] sm:items-center">
            <div>
              <label
                htmlFor="operation-birthday"
                className="text-sm font-medium"
              >
                生日消费倍率
              </label>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                老板生日当天新订单使用的整数倍率，范围 1–10 倍。
              </p>
            </div>
            <div className="relative">
              <Input
                id="operation-birthday"
                type="number"
                inputMode="numeric"
                min={1}
                max={10}
                step={1}
                value={form.birthday_multiplier ?? ""}
                disabled={disabled}
                onChange={(event) =>
                  set(
                    "birthday_multiplier",
                    event.target.value === "" ? "" : Number(event.target.value)
                  )
                }
                className="pr-10 text-right tabular-nums"
              />
              <span className="pointer-events-none absolute top-3 right-3 text-sm text-muted-foreground">
                倍
              </span>
            </div>
          </div>
          <div className="flex items-start justify-between gap-6 py-4">
            <div>
              <label
                htmlFor="operation-vip"
                className="flex items-center gap-2 text-sm font-medium"
              >
                <Crown className="size-3.5 text-primary" />
                VIP 自动升级
              </label>
              <p className="mt-2 max-w-xl text-xs leading-5 text-muted-foreground">
                {!known ? "读取配置后核对等级折扣、周期权益与券条款保护。" : form.vip_coupon_protection_enabled
                  ? "关闭后停止等级折扣和周期权益发放，已发且未过期的券保留。"
                  : "关闭后不执行自动升级与周期权益发放；券条款保护尚未启用。"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-muted-foreground">
                <DataValue known={known}>{form.vip_enabled ? "已开启" : "已关闭"}</DataValue>
              </span>
              <DataValue known={known}><Switch
                id="operation-vip"
                checked={!!form.vip_enabled}
                disabled={disabled}
                onCheckedChange={(value) => set("vip_enabled", value)}
              /></DataValue>
            </div>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CalendarClock className="size-4 text-primary" />
            当前赛季
          </CardTitle>
          <CardDescription>
            填写赛季编号时，须同时设置截止时间。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 pt-0 sm:grid-cols-2">
          <Field
            field={{
              key: "season_code",
              label: "赛季编号",
              maxLength: 40,
              hint: `${String(form.season_code || "").length} / 40 字符`,
            }}
            value={form.season_code}
            disabled={disabled}
            onChange={(value) => set("season_code", value)}
          />
          <Field
            field={{
              key: "season_ends_at",
              label: "赛季截止时间",
              type: "datetime-local",
            }}
            value={form.season_ends_at}
            disabled={disabled}
            onChange={(value) => set("season_ends_at", value)}
          />
        </CardContent>
      </Card>
      <details className="rounded-lg border bg-muted/20 p-4 text-xs">
        <summary className="cursor-pointer font-medium">固定业务规则</summary>
        <dl className="mt-4 space-y-3">
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">VIP 单次充值门槛</dt>
            <dd className="text-right tabular-nums">
              <DataValue known={known}>{form.vip_min_single_recharge ?? "—"}</DataValue> {coinUnit()}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">优惠券叠加</dt>
            <dd><DataValue known={known}>{form.coupon_stacking ? "允许叠加" : "不叠加"}</DataValue></dd>
          </div>
        </dl>
      </details>
    </div>
  )
}
