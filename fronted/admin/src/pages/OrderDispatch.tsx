import { RecordSection as FormSection } from "./OrderRecordVisual"
import { coinText, coinUnit } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { getSession } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  DataTable,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  type Row,
} from "@/components/business"
import { request, requestId } from "@/api/request"
import { amountToXaCoin } from "@/lib/format"

export default function DispatchDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    customer_id: null,
    service_id: null,
    game_rounds: 1,
    provider_gender: "ANY",
    escort_mode: "SINGLE",
    provider_ids: [],
    remark: "",
    game_region: "",
    game_nickname: "",
    game_uid: "",
    giftBalance: 0,
    compensationBalance: 0,
    bossConfirmation: "",
  })
  const [service, setService] = useState<Row | null>(null)
  const [quote, setQuote] = useState<Row | null>(null)
  const [fundingEnabled, setFundingEnabled] = useState(false)
  const [discountOptions, setDiscountOptions] = useState<Row[]>([])
  const [discountToken, setDiscountToken] = useState("")
  const [rates, setRates] = useState<Record<number, Row>>({})
  const [error, setError] = useState("")
  const [quoting, setQuoting] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [intent] = useState(() => requestId("dispatch"))
  const sequence = useRef(0)
  const alive = useRef(true)
  const owner = useRef(getSession("admin").revision)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      ++sequence.current
    }
  }, [])
  const current = (id: number) =>
    alive.current &&
    sequence.current === id &&
    owner.current === getSession("admin").revision
  const fixed = quote?.commission_type === "FIXED"
  const count = Number(service?.billing_provider_count || 1)
  const rateValid = (value: any) =>
    Number.isInteger(value) && value >= 0 && value <= 100
  function change(patch: Row) {
    ++sequence.current
    if (["customer_id", "service_id", "game_rounds"].some(key => Object.prototype.hasOwnProperty.call(patch, key))) {
      setDiscountOptions([]); setDiscountToken("")
    }
    setForm((old) => ({ ...old, bossConfirmation: "", ...patch }))
    setQuote(null)
    setReviewed(false)
    setQuoting(false)
  }
  async function getQuote() {
    const id = ++sequence.current
    setQuoting(true)
    setError("")
    setQuote(null)
    setReviewed(false)
    try {
      if (
        !form.customer_id ||
        !form.service_id ||
        !Number.isInteger(form.game_rounds) ||
        form.game_rounds < 1
      )
        throw new Error("请选择老板、套餐和有效整数数量。")
      if (fundingEnabled && [form.giftBalance, form.compensationBalance].some(value =>
        !Number.isSafeInteger(value) || value < 0 || value * 10 > 2147483647))
        throw new Error("赠送与赔付须填写非负整数店币。")
      const data = (
        await request({
          url: "/orders/dispatch-quote/",
          params: {
            customer_id: form.customer_id,
            service_id: form.service_id,
            game_rounds: form.game_rounds,
            ...(discountToken ? {discount_selection_token: discountToken} : {}),
            ...(fundingEnabled ? {gift_balance_amount: form.giftBalance * 10,
              compensation_balance_amount: form.compensationBalance * 10} : {}),
          },
        })
      ).data
      if (!current(id)) return
      const next: Record<number, Row> = {}
      for (const provider_id of form.provider_ids) {
        if (data.commission_locked || data.commission_type === "FIXED")
          next[provider_id] = {
            commission_rate: data.commission_rate,
            source: data.commission_source_label,
          }
        else {
          try {
            const suggestion = (
              await request({
                url: "/orders/suggest-commission/",
                params: {
                  service_id: form.service_id,
                  provider_id,
                  amount: data.amount,
                },
              })
            ).data
            if (!current(id)) return
            next[provider_id] = {
              commission_rate: suggestion.commission_rate,
              source:
                suggestion.commission_source_label || suggestion.source_label,
            }
          } catch {
            if (!current(id)) return
            next[provider_id] = {
              commission_rate: "",
              source: "抽成读取失败，请重新试算。",
            }
          }
        }
      }
      if (!current(id)) return
      setRates(next)
      setFundingEnabled(data.funding_selection_enabled === true)
      setDiscountOptions(data.discount_options || [])
      setDiscountToken(data.discount_selection_token || "")
      setQuote(data)
    } catch (cause: any) {
      if (current(id)) { setDiscountOptions([]); setDiscountToken(""); setError(cause.message || "试算未完成，请重试。") }
    } finally {
      if (current(id)) setQuoting(false)
    }
  }
  const invalidProviders =
    count === 2 ? form.provider_ids.length !== 2 : form.provider_ids.length > 1
  const invalidRates =
    !fixed &&
    form.provider_ids.some(
      (id: number) => !rateValid(rates[id]?.commission_rate)
    )
  const accountLabels: Record<string, string> = {game_region: "游戏大区", game_nickname: "游戏昵称", game_uid: "游戏 UID"}
  const requiredFields: string[] | null = quote?.account_fields && Array.isArray(quote.account_fields.fields) &&
    quote.account_fields.fields.every((field: string) => Object.prototype.hasOwnProperty.call(accountLabels, field))
    ? quote.account_fields.fields : null
  const invalidAccountFields = !requiredFields || requiredFields.some(field => !String(form[field] || "").trim())
  return (
    <MutationDialog
      contentClassName="admin-order-dispatch-dialog sm:max-w-5xl"
      title="快捷派单"
      description="代老板创建订单并扣除钱包余额。先读取报价，再核对老板、陪玩、抽成与扣款金额。"
      onClose={onClose}
      submitText="确认扣款并派单"
      disabled={
        !quote ||
        !reviewed ||
        unknown ||
        quoting ||
        invalidProviders ||
        invalidRates ||
        invalidAccountFields ||
        (fundingEnabled && !quote?.balance_sufficient) ||
        ((fundingEnabled || quote?.discount_selection_enabled) && String(form.bossConfirmation).trim().length < 2)
      }
      onSubmit={async () => {
        if (!quote || !reviewed || invalidProviders || invalidRates || invalidAccountFields) return
        const providers = form.provider_ids.map((provider_id: number) => ({ provider_id }))
        try {
          await request({
            url: "/orders/dispatch/",
            method: "POST",
            data: {
              client_request_id: intent,
              customer_id: form.customer_id,
              service_id: form.service_id,
              game_rounds: form.game_rounds,
              expected_amount: quote.amount,
              ...(quote.discount_selection_enabled ? {discount_selection_token: quote.discount_selection_token} : {}),
              escort_mode: count === 2 ? "DOUBLE" : "SINGLE",
              provider_gender:
                service?.service_type === "COMPANION"
                  ? form.provider_gender
                  : "ANY",
              providers,
              game_region: String(form.game_region).trim(),
              game_nickname: String(form.game_nickname).trim(),
              game_uid: String(form.game_uid).trim(),
              remark: form.remark,
              ...(fundingEnabled ? {gift_balance_amount: form.giftBalance * 10,
                compensation_balance_amount: form.compensationBalance * 10} : {}),
              ...((fundingEnabled || quote.discount_selection_enabled)
                ? {boss_confirmation: String(form.bossConfirmation).trim()} : {}),
            },
          })
          toast.success("已创建订单并派单")
          onSaved()
        } catch (cause: any) {
          if (
            !cause.status ||
            cause.status >= 500 ||
            [408, 409].includes(cause.status)
          )
            setUnknown(true)
          else {
            setQuote(null)
            setReviewed(false)
          }
          throw cause
        }
      }}
    >
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(300px,1fr)]">
        <FormSection title="老板、服务与接单安排">
          <fieldset
            disabled={unknown || quoting}
            className="grid grid-cols-2 gap-4"
          >
            <OptionPicker
              resource="users"
              label="下单老板"
              value={form.customer_id}
              onChange={(value) => change({ customer_id: value, giftBalance: 0, compensationBalance: 0 })}
            />
            <OptionPicker
              resource="service-items"
              label="服务套餐"
              value={form.service_id}
              query={{ is_active: 1 }}
              onChange={(value, row) => {
                setService(row || null)
                setRates({})
                change({
                  service_id: value,
                  provider_ids: [],
                  provider_gender: "ANY",
                })
              }}
            />
            <Field
              field={{
                key: "dispatch-rounds",
                label: "购买数量",
                type: "number",
                min: 1,
                max: 999,
                step: 1,
              }}
              value={form.game_rounds}
              onChange={(value) => change({ game_rounds: value })}
            />
            {service?.service_type === "COMPANION" && (
              <Field
                field={{
                  key: "dispatch-gender",
                  label: "陪玩性别",
                  type: "select",
                  options: [
                    ["ANY", "不限"],
                    ["MALE", "男陪"],
                    ["FEMALE", "女陪"],
                  ],
                }}
                value={form.provider_gender}
                onChange={(value) => {
                  setRates({})
                  change({ provider_gender: value, provider_ids: [] })
                }}
              />
            )}
            <div className="col-span-2">
              <OptionPicker
                resource="escorts"
                keyField="user"
                label={`指派陪玩（${count === 2 ? "须选择两名" : "选填，留空进入大厅"}）`}
                multiple
                value={form.provider_ids}
                query={{
                  service_id: form.service_id,
                  provider_gender:
                    service?.service_type === "COMPANION"
                      ? form.provider_gender
                      : "ANY",
                }}
                onChange={(value) => {
                  setRates({})
                  change({ provider_ids: value })
                }}
              />
              {invalidProviders && (
                <Feedback
                  message={
                    count === 2
                      ? "双陪套餐须指定两名不同陪玩。"
                      : "单陪套餐最多选择一名陪玩。"
                  }
                />
              )}
            </div>
            <p className="col-span-2 text-sm text-muted-foreground">
              本单抽成按门店适用规则计算，不可在派单时修改。
            </p>
            {quote && requiredFields && Object.entries(accountLabels).map(([key, label]) => <Field key={key}
              field={{key: `dispatch-${key}`, label, required: requiredFields.includes(key), maxLength: 50}}
              value={form[key]} onChange={value => {setForm(old => ({...old, [key]: value})); setReviewed(false)}} />)}
            <Field
              field={{
                key: "dispatch-remark",
                label: "订单备注",
                type: "textarea",
                full: true,
              }}
              value={form.remark}
              onChange={(value) => change({ remark: value })}
            />
            {fundingEnabled && <>
              <Field field={{key:"dispatch-gift-balance", label:`使用赠送（${coinUnit()}）`, type:"number", min:0, step:1}}
                value={form.giftBalance} onChange={value => change({giftBalance:value})} />
              <Field field={{key:"dispatch-compensation-balance", label:`使用赔付（${coinUnit()}）`, type:"number", min:0, step:1}}
                value={form.compensationBalance} onChange={value => change({compensationBalance:value})} />
            </>}
            {discountOptions.length > 0 && <label className="col-span-2 grid gap-2 text-sm">优惠组合
              <select aria-label="优惠组合" className="min-h-10 rounded-md border bg-background px-3" value={discountToken}
                onChange={event => {setDiscountToken(event.target.value); change({})}}>
                {discountOptions.map((item, index) => <option key={item.key} value={item.selection_token}>
                  {item.label} · 实付 {amountToXaCoin(item.amount)} {coinUnit()}{index === 0 ? "（推荐）" : ""}
                </option>)}
              </select>
            </label>}
            {(fundingEnabled || discountOptions.length > 0) && <Field field={{key:"dispatch-boss-confirmation", label:"老板确认记录", type:"textarea", full:true, required:true, maxLength:500,
              hint:"记录老板明确告知的付款选择；未填写时不能提交。"}} value={form.bossConfirmation}
              onChange={value => {setForm(old => ({...old, bossConfirmation:value})); setReviewed(false)}} />}
          </fieldset>
          <Button
            type="button"
            variant="outline"
            disabled={
              !form.customer_id || !form.service_id || quoting || unknown
            }
            onClick={() => void getQuote()}
          >
            {quoting ? "正在试算" : "读取最新报价与抽成"}
          </Button>
          <Feedback message={error} />
          {quote && !requiredFields && <Feedback message="游戏资料要求无法确认，请重新读取报价。" />}
        </FormSection>
        <FormSection title="本次报价与分账" className="bg-muted/15">
          {!quote && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {quoting ? "正在读取报价…" : "选择老板和服务后，读取最新报价。"}
            </p>
          )}
          {quote && (
            <div className="space-y-4 rounded-lg border bg-muted/40 p-4">
              <p className="font-semibold">
                本次扣款 {amountToXaCoin(quote.amount)} {coinText(" 浪花")}
              </p>
              <p className="text-sm">游戏资料：{Object.entries(accountLabels).map(([key, label]) => `${label} ${String(form[key] || "").trim() || "未填"}`).join(" · ")}</p>
              {quote.funding_selection_enabled && <>
                <p className="text-sm">本金 {amountToXaCoin(quote.amount - form.giftBalance * 10 - form.compensationBalance * 10)} · 赠送 {form.giftBalance} · 赔付 {form.compensationBalance} {coinUnit()}</p>
                <p className="text-xs text-muted-foreground">余额：本金 {amountToXaCoin(quote.prepaid_balance)} · 赠送 {amountToXaCoin(quote.gift_balance)} · 赔付 {amountToXaCoin(quote.compensation_balance)} {coinUnit()}</p>
                {!quote.balance_sufficient && <Feedback message="所选资金来源余额不足，请调整后重新试算。" />}
              </>}
              <p className="text-xs text-muted-foreground">
                原价 {amountToXaCoin(quote.original_amount)} · 老板优惠{" "}
                {amountToXaCoin(quote.boss_discount)} · 活动优惠{" "}
                {amountToXaCoin(quote.promo_discount)}
              </p>
              <p className="text-sm">
                {quote.commission_source_label || "适用规则"} ·{" "}
                {fixed
                  ? `整单固定抽成 ${amountToXaCoin(quote.commission_fixed_total)} ${coinUnit()}`
                  : `参考抽成 ${quote.commission_rate ?? "—"}%`}
                {quote.commission_locked ? "（该规则锁定抽成）" : ""}
              </p>
              {form.provider_ids.length > 0 && (
                <DataTable
                  rows={form.provider_ids.map((id: number, index: number) => ({
                    id,
                    index,
                    ...rates[id],
                  }))}
                  columns={[
                    { key: "id", label: "陪玩账号" },
                    { key: "source", label: "抽成来源" },
                    {
                      key: "commission_rate",
                      label: fixed
                        ? coinText("预计实得（浪花）")
                        : "平台抽成（%）",
                      render: (row) =>
                        fixed ? (
                          amountToXaCoin(
                            quote.provider_shares?.[row.index]?.provider_income
                          )
                        ) : `${row.commission_rate ?? "—"}%`,
                    },
                  ]}
                />
              )}
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 size-4 accent-blue-600"
                  checked={reviewed}
                  disabled={unknown || invalidProviders || invalidRates}
                  onChange={(event) => setReviewed(event.target.checked)}
                />
                已核对老板、服务、数量、陪玩、抽成和本次扣款
              </label>
            </div>
          )}
        </FormSection>
      </div>
      {unknown && (
        <Feedback message="提交结果未确认。请关闭后核对订单和老板扣款流水；本次请求可能已完成，避免重复派单。" />
      )}
    </MutationDialog>
  )
}
