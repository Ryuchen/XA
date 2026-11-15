import { CalendarDays, ReceiptText, Banknote, Layers3 } from "@/components/ui/icons"
import { FinancialHeader as PageHeader, FinancialSection, FinancialStats } from "./Financials"
import { useState } from "react"
import { fundRequest } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import {
  DataPageState,
  DataTable,
  Feedback,
  FormSection,
  MutationDialog,
  Pagination,
  RefreshButton,
  useList,
  useLoad,
  type Row,
} from "@/components/business"
import {
  PendingResult,
  PayrollInput,
  PayrollNotice,
  PayrollSelect,
} from "@/components/PayrollFields"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import {
  beijingDay,
  dictionaryOptions,
  hasMoneyAmount,
  rmb,
  rmbInput,
  rmbText,
  withCurrentOption,
} from "@/lib/payroll"
import { formatDateTime } from "@/lib/format"

export default function OperatingCostsPage() {
  const { hasPerm } = useAdminAuth()
  const [filter, setFilter] = useState<Row>({ start_date: "", end_date: "" })
  const [query, setQuery] = useState<Row>({})
  const list = useList("operating-costs", query)
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="operating-costs"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="人民币成本"
            title="每日投流与运营成本"
            description="按实际发生日期登记人民币成本。投流费需选择渠道，其他成本按字典类别归集。"

          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {hasPerm("cost:edit") && (
              <Button
                size="sm"
                onClick={() =>
                  setEditing({ day: beijingDay(), amount_fen: 0, note: "" })
                }
              >
                登记成本
              </Button>
            )}
          </PageHeader>
          <FinancialStats items={[
            { label: "符合条件记录", value: list.data ? list.total : "—", unit: "条", note: "服务端筛选结果总数", icon: ReceiptText },
            { label: "本页登记成本", value: list.data && list.rows.every(row => hasMoneyAmount(row.amount_fen)) ? rmb(list.rows.reduce((sum, row) => sum + Number(row.amount_fen), 0)) : "—", note: "人民币 · 仅本页已录成本", icon: Banknote },
            { label: "本页发生日期", value: list.data && list.rows.every(row => !!row.day) ? new Set(list.rows.map(row => row.day)).size : "—", unit: "天", note: "不代表期间成本已完整核对", icon: CalendarDays },
            { label: "本页成本类别", value: list.data && list.rows.every(row => !!row.category_name) ? new Set(list.rows.map(row => row.category_name)).size : "—", unit: "类", note: "按真实登记类别归集", icon: Layers3 },
          ]} />
          
          <FinancialSection title="登记口径" icon={Layers3}><PayrollNotice>
            这里只登记已发生的成本，不执行付款。请勿重复登记已自动归集的工资、陪玩分账、推荐分佣或平台补贴。当日未投放时请明确登记
            0 元投流成本，未登记与已核对零支出分别处理。
          </PayrollNotice></FinancialSection>
          <Feedback message={list.error} retry={list.reload} />
          <div className="fin-records-group"><DataTable
            toolbar={<form
            className="admin-filter-form flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...filter })
            }}
          >
            <PayrollInput
              label="开始日期"
              type="date"
              value={filter.start_date}
              onChange={(value) => setFilter({ ...filter, start_date: value })}
            />
            <PayrollInput
              label="结束日期"
              type="date"
              value={filter.end_date}
              onChange={(value) => setFilter({ ...filter, end_date: value })}
            />
            <Button type="submit" variant="outline" disabled={list.loading}>
              查询
            </Button>
          </form>}

            title="每日成本明细"
            titleIcon={ReceiptText}
            description={`本页 ${list.data ? list.rows.length : "—"} 条 · 人民币成本与门店虚拟币分开核对`}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              { key: "day", label: "发生日期" },
              { key: "category_name", label: "成本类别" },
              { key: "channel_name", label: "投流渠道" },
              {
                key: "amount_fen",
                label: "金额（人民币）",
                render: (row) => rmb(row.amount_fen),
              },
              {
                key: "note",
                label: "用途与备注",
                className: "max-w-72 whitespace-normal",
              },
              {
                key: "updated_at",
                label: "更新时间",
                render: (row) => formatDateTime(row.updated_at),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) =>
                  hasPerm("cost:edit") && (
                    <div className="flex flex-wrap gap-1">
                      <Button
                        actionTone="edit"
                        size="sm"
                        variant="outline"
                        onClick={() => setEditing(row)}
                      >
                        编辑
                      </Button>
                      <Button
                        actionTone="reject"
                        size="sm"
                        variant="ghost"
                        onClick={() => setRemove(row)}
                      >
                        删除
                      </Button>
                    </div>
                  ),
              },
            ]}
          /></div>
          <Pagination
            page={list.page}
            total={list.total}
            pageSize={list.pageSize}
            onChange={list.setPage}
            onPageSizeChange={list.setPageSize}
            loading={list.loading}
          />
          {editing && (
            <CostEditor
              row={editing}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
          {remove && (
            <MutationDialog
              title="删除成本记录"
              submitVariant="destructive"
              description={`删除 ${remove.day}「${remove.category_name}」${rmb(remove.amount_fen)}，${remove.channel_name || "无投流渠道"}。删除后会重新计算对应日期 ROI。`}
              submitText="确认删除"
              onClose={() => setRemove(null)}
              onSubmit={async () => {
                await request({
                  url: `/operating-costs/${remove.id}/`,
                  method: "DELETE",
                  data: { revision: remove.revision },
                })
                toast.success("成本记录已删除")
                void list.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function CostEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    ...row,
    amount: rmbText(row.amount_fen),
    channel: row.channel || "",
    category: row.category || "",
  })
  const [unknown, setUnknown] = useState(false)
  const options = useLoad(async () => {
    const [categories, channels] = await Promise.all([
      dictionaryOptions("COST_CATEGORY"),
      dictionaryOptions("AD_CHANNEL"),
    ])
    return { categories, channels }
  })
  const categories = withCurrentOption(
    options.data?.categories || [],
    row.category,
    row.category_name
  )
  const channels = withCurrentOption(
    options.data?.channels || [],
    row.channel,
    row.channel_name
  )
  const selectedCategory = categories.find(
    (item) => Number(item.id) === Number(form.category)
  )
  const isAd = selectedCategory?.code === "AD_SPEND"
  const change = (key: string, value: string) =>
    setForm((old) => ({ ...old, [key]: value }))
  return (
    <MutationDialog
      title={row.id ? "编辑成本记录" : "登记每日成本"}
      description="金额单位为人民币元，以核实后的实际支出为准。"
      onClose={onClose}
      contentClassName="sm:max-w-3xl"
      confirmDiscard
      submitText={unknown ? "按原内容重试并核对" : "保存成本"}
      disabled={
        options.loading ||
        !!options.error ||
        !form.day ||
        !form.category ||
        (isAd && !form.channel)
      }
      onSubmit={async () => {
        const amount = rmbInput(form.amount, "成本金额")
        const data = {
          day: form.day,
          category: Number(form.category),
          channel: form.channel ? Number(form.channel) : null,
          amount_fen: amount,
          note: String(form.note || "").trim(),
          ...(row.id ? { revision: row.revision } : {}),
        }
        try {
          if (row.id)
            await request({
              url: `/operating-costs/${row.id}/`,
              method: "PATCH",
              data,
            })
          else await fundRequest("admin", "/admin/operating-costs/", data)
        } catch (cause: any) {
          if (!cause.status || cause.status >= 500) setUnknown(true)
          throw cause
        }
        toast.success("成本已保存，请核对登记记录")
        onSaved()
      }}
    >
      <Feedback message={options.error} retry={options.reload} />
      {unknown && (
        <PendingResult>当前内容已保留。重试会核对原请求，也可返回列表核对已登记成本。</PendingResult>
      )}
      <fieldset
        disabled={unknown || options.loading || !!options.error}
        className="space-y-4 disabled:opacity-70"
      >
        <FormSection title="支出信息">
          <div className="grid grid-cols-2 gap-4">
            <PayrollInput
              label="发生日期（北京时间）"
              type="date"
              required
              value={form.day}
              onChange={(value) => change("day", value)}
            />
            <PayrollSelect
              label="成本类别"
              required
              value={form.category}
              options={categories}
              onChange={(value) => {
                change("category", value)
                if (
                  categories.find((item) => Number(item.id) === Number(value))
                    ?.code !== "AD_SPEND"
                )
                  change("channel", "")
              }}
            />
            <PayrollSelect
              label={isAd ? "投流渠道（必填）" : "关联渠道（可选）"}
              required={isAd}
              value={form.channel}
              options={channels}
              onChange={(value) => change("channel", value)}
            />
            <PayrollInput
              label="成本金额（人民币元）"
              required
              money
              value={form.amount}
              onChange={(value) => change("amount", value)}
            />
          </div>
        </FormSection>
        <FormSection title="用途与备注">
          <div className="space-y-2">
            <Label htmlFor="cost-note">用途与备注</Label>
            <Textarea
              id="cost-note"
              rows={3}
              value={form.note || ""}
              onChange={(event) => change("note", event.target.value)}
            />
          </div>
        </FormSection>
      </fieldset>
      {options.data && !options.loading && !options.error && !categories.length && (
        <PayrollNotice>
          暂无启用的成本类别，请先在“字典配置”中配置成本类别。
        </PayrollNotice>
      )}
      {isAd && options.data && !options.loading && !options.error && !channels.length && (
        <PayrollNotice>
          暂无启用的投流渠道，请先在“字典配置”中配置渠道。
        </PayrollNotice>
      )}
    </MutationDialog>
  )
}
