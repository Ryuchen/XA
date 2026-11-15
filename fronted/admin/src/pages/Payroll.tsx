import {
  Banknote, BriefcaseBusiness, Calculator, CreditCard, FileText, GraduationCap,
  Headset, ReceiptText, Send, TrendingUp, UsersRound,
} from "@/components/ui/icons"
import { FinancialHeader as PageHeader, FinancialSection, FinancialStats } from "./Financials"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { fundRequest } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DataPageState,
  DataTable,
  Feedback,
  Field,
  FormSection,
  MutationDialog,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLoad,
  type Row,
} from "@/components/business"
import {
  PendingResult,
  PayrollInput,
  PayrollNotice,
  PayrollSelect,
  PayrollWarnings,
} from "@/components/PayrollFields"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"
import {
  activityKinds,
  allPayrollOptions,
  currentMonth,
  dictionaryOptions,
  kindLabel,
  percentInput,
  percentText,
  rmb,
  rmbInput,
  rmbText,
  withCurrentOption,
} from "@/lib/payroll"

function activitySourceLabel(row: Row): string {
  if (row.kind === "EXAM") return `已核实考试 #${row.id}`
  const sourceKey = String(row.source_key || "")
  if (row.kind === "RECHARGE") {
    const id = sourceKey.match(/^RECHARGE:(\d+)$/)?.[1]
    return id ? `充值 #${id}` : `充值业绩记录 #${row.id}`
  }
  if (row.kind === "AFTERSALE") {
    const id = sourceKey.match(/^AFTERSALE:(\d+):/)?.[1]
    return `${row.order_no || "关联订单未记录"} · ${id ? `售后 #${id}` : `售后业绩记录 #${row.id}`} · ${formatDateTime(row.performed_at)}`
  }
  const id = sourceKey.match(/^ORDER:(\d+)$/)?.[1]
  return row.order_no || (id ? `订单 #${id}` : `派单业绩记录 #${row.id}`)
}

export function SalaryPlansPage() {
  const { hasPerm } = useAdminAuth()
  const list = useList("salary-plans")
  const [editing, setEditing] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="salary-plans"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="人民币薪资"
            title="岗位薪资方案"
            description="按人配置底薪、绩效和提成。薪资岗位独立于权限角色，月度安排使用方案快照。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {hasPerm("salary:edit") && (
              <Button
                size="sm"
                onClick={() => setEditing({ name: "", is_active: true })}
              >
                新增方案
              </Button>
            )}
          </PageHeader>
          <PayrollNotice>
            方案金额均为人均人民币金额，按员工月度安排计入人力成本。
          </PayrollNotice>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable
            title="岗位薪资方案列表"
            titleIcon={BriefcaseBusiness}
            description="人民币金额 · 薪资方案独立于权限角色"
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "name",
                label: "方案",
                render: (row) => (
                  <>
                    <p className="font-medium">{row.name}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {row.position_name}
                    </p>
                    {row.remark && (
                      <p className="mt-2 max-w-64 text-xs whitespace-normal text-muted-foreground">
                        {row.remark}
                      </p>
                    )}
                  </>
                ),
              },
              {
                key: "base_fen",
                label: "人均底薪 / 月",
                render: (row) => rmb(row.base_fen),
              },
              {
                key: "performance_fen",
                label: "人均绩效 / 月",
                render: (row) => (
                  <>
                    {rmb(row.performance_fen)}
                    <p className="mt-1 text-xs text-muted-foreground">
                      目标 {rmb(row.target_fen)} ·{" "}
                      {row.target_scope === "STORE" ? "门店" : "个人"}
                    </p>
                  </>
                ),
              },
              {
                key: "dispatch_day_bps",
                label: "派单提成",
                render: (row) => (
                  <>
                    <p>日班 {percentText(row.dispatch_day_bps)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      夜班 {percentText(row.dispatch_night_bps)}
                    </p>
                  </>
                ),
              },
              {
                key: "recharge_bps",
                label: "充值提成",
                render: (row) => percentText(row.recharge_bps),
              },
              {
                key: "aftersale_fee_fen",
                label: "售后 / 次",
                render: (row) => rmb(row.aftersale_fee_fen),
              },
              {
                key: "exam_fee_fen",
                label: "考试 / 次",
                render: (row) => (
                  <>
                    {rmb(row.exam_fee_fen)}
                    <p className="mt-1 text-xs text-muted-foreground">
                      平台扣率 {percentText(row.exam_commission_bps)}
                    </p>
                  </>
                ),
              },
              {
                key: "is_active",
                label: "状态",
                render: (row) => (
                  <Status
                    value={row.is_active}
                    readState={typeof row.is_active === "boolean" ? undefined : "unread"}
                    label={row.is_active ? "启用" : "停用"}
                  />
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) =>
                  hasPerm("salary:edit") && (
                    <Button
                      actionTone="edit"
                      size="sm"
                      variant="outline"
                      onClick={() => setEditing(row)}
                    >
                      编辑方案
                    </Button>
                  ),
              },
            ]}
          />
          <Pagination
            page={list.page}
            total={list.total}
            pageSize={list.pageSize}
            onChange={list.setPage}
            onPageSizeChange={list.setPageSize}
            loading={list.loading}
          />
          {editing && (
            <PlanEditor
              row={editing}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

const moneyFields = [
  ["base_fen", "人均月度底薪（元）"],
  ["performance_fen", "人均月度绩效上限（元）"],
  ["target_fen", "月度绩效目标（元）"],
  ["aftersale_fee_fen", "完成售后单次提成（元）"],
  ["exam_fee_fen", "考试单次抽前金额（元）"],
]
const rateFields = [
  ["dispatch_day_bps", "日班派单提成（%）"],
  ["dispatch_night_bps", "夜班派单提成（%）"],
  ["recharge_bps", "预存充值提成（%）"],
  ["exam_commission_bps", "考试平台扣率（%）"],
]
function PlanEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>(() => ({
    ...row,
    position: row.position || "",
    target_scope: row.target_scope || "PERSON",
    remark: row.remark || "",
    ...Object.fromEntries(
      [...moneyFields, ...rateFields].map(([key]) => [key, rmbText(row[key])])
    ),
  }))
  const options = useLoad(() => dictionaryOptions("SALARY_POSITION"))
  const positions = withCurrentOption(
    options.data || [],
    row.position,
    row.position_name
  )
  const change = (key: string, value: any) =>
    setForm((old) => ({ ...old, [key]: value }))
  return (
    <MutationDialog
      title={row.id ? "编辑岗位薪资方案" : "新增岗位薪资方案"}
      description="底薪与绩效按个人整月配置；派单、充值和计次由真实归属及核实记录计算。"
      onClose={onClose}
      submitText="保存方案"
      contentClassName="sm:max-w-4xl"
      confirmDiscard
      disabled={options.loading || !!options.error || !form.position}
      onSubmit={async () => {
        const data: Row = {
          position: Number(form.position),
          name: String(form.name || "").trim(),
          target_scope: form.target_scope,
          remark: form.remark,
          is_active: !!form.is_active,
          ...(row.id ? { revision: row.revision } : {}),
        }
        if (!data.name) throw new Error("请填写方案名称。")
        moneyFields.forEach(([key, label]) => {
          data[key] = rmbInput(form[key], label)
        })
        rateFields.forEach(([key, label]) => {
          data[key] = percentInput(form[key], label)
        })
        if (data.performance_fen > 0 && !data.target_fen)
          throw new Error("有绩效时，请填写大于零的月度绩效目标。")
        await request({
          url: `/salary-plans/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data,
        })
        toast.success("薪资方案已保存")
        onSaved()
      }}
    >
      <Feedback message={options.error} retry={options.reload} />
      <fieldset
        disabled={options.loading || !!options.error}
        className="space-y-4 disabled:opacity-70"
      >
        <FormSection title="方案与岗位">
          <div className="grid grid-cols-2 gap-4">
            <PayrollInput
              label="方案名称"
              required
              value={form.name || ""}
              onChange={(value) => change("name", value)}
            />
            <PayrollSelect
              label="薪资岗位"
              required
              value={form.position}
              options={positions}
              onChange={(value) => change("position", value)}
            />
          </div>
        </FormSection>
        <FormSection title="月度底薪与绩效">
          <div className="grid grid-cols-2 gap-4">
            {moneyFields.slice(0, 3).map(([key, label]) => (
              <PayrollInput
                key={key}
                label={label}
                money
                required
                value={form[key]}
                onChange={(value) => change(key, value)}
              />
            ))}
            <PayrollSelect
              label="绩效目标范围"
              value={form.target_scope}
              options={[
                ["PERSON", "个人归属净消费"],
                ["STORE", "门店净消费"],
              ]}
              onChange={(value) => change("target_scope", value)}
            />
          </div>
        </FormSection>
        <FormSection title="提成与计次薪资">
          <div className="grid grid-cols-2 gap-4">
            {rateFields.slice(0, 3).map(([key, label]) => (
              <PayrollInput
                key={key}
                label={label}
                money
                required
                value={form[key]}
                onChange={(value) => change(key, value)}
              />
            ))}
            {moneyFields.slice(3).map(([key, label]) => (
              <PayrollInput
                key={key}
                label={label}
                money
                required
                value={form[key]}
                onChange={(value) => change(key, value)}
              />
            ))}
            <PayrollInput
              label="考试平台扣率（%）"
              money
              required
              value={form.exam_commission_bps}
              onChange={(value) => change("exam_commission_bps", value)}
            />
          </div>
        </FormSection>
        <FormSection title="启用与备注">
          <div className="space-y-4">
            <Field
              field={{
                key: "salary-plan-active",
                label: "启用方案",
                type: "boolean",
              }}
              value={form.is_active}
              onChange={(value) => change("is_active", value)}
            />
            <div className="col-span-2 space-y-2">
              <Label htmlFor="salary-plan-remark">薪资说明</Label>
              <Textarea
                id="salary-plan-remark"
                rows={3}
                value={form.remark}
                onChange={(event) => change("remark", event.target.value)}
              />
            </div>
          </div>
        </FormSection>
      </fieldset>
      <PayrollNotice>
        绩效按目标完成比例折算并封顶；夜班起止在“月度薪资核算”配置。考试薪资计入扣除平台比例后的实得，例：50
        元扣 30% 后为 35 元。已有月度安排按其方案快照计算。
      </PayrollNotice>
    </MutationDialog>
  )
}

export function SalaryAssignmentsPage() {
  const { hasPerm } = useAdminAuth()
  const [month, setMonth] = useState(currentMonth)
  const list = useList("salary-assignments", { month })
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="salary-assignments"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="月度人员安排"
            title="员工月度薪资安排"
            description="每位员工每月选择一份薪资方案，避免多权限角色重复计算底薪。"
            filterDescription="月份作用于本页员工安排列表；新建安排沿用当前月份。"
            filters={<div className="max-w-xs">
            <PayrollInput
              label="核算月份"
              type="month"
              value={month}
              onChange={setMonth}
            />
          </div>}
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {hasPerm("salary:edit") && (
              <Button
                size="sm"
                onClick={() => setEditing({ month, account: "", plan: "" })}
              >
                新增月度安排
              </Button>
            )}
          </PageHeader>

          <PayrollNotice>
            底薪按本次月度安排计入整月。人员入离职、兼岗等调整应选择对应方案并填写说明；权限角色变动不会自动改变薪资。
          </PayrollNotice>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable
            title="员工月度安排"
            titleIcon={UsersRound}
            description="按月份核对岗位方案与适用快照"
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              { key: "month", label: "月份" },
              { key: "account_name", label: "员工" },
              {
                key: "position_name",
                label: "薪资岗位",
                render: (row) =>
                  row.plan_snapshot?.position_name || row.position_name,
              },
              {
                key: "plan_name",
                label: "方案快照",
                render: (row) => row.plan_snapshot?.name || row.plan_name,
              },
              {
                key: "base_fen",
                label: "人均底薪",
                render: (row) => rmb(row.plan_snapshot?.base_fen),
              },
              {
                key: "performance_fen",
                label: "绩效上限",
                render: (row) => rmb(row.plan_snapshot?.performance_fen),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) =>
                  hasPerm("salary:edit") && (
                    <div className="flex flex-wrap gap-1">
                      <Button
                        actionTone="edit"
                        size="sm"
                        variant="outline"
                        onClick={() => setEditing(row)}
                      >
                        调整安排
                      </Button>
                      <Button
                        actionTone="reject"
                        size="sm"
                        variant="ghost"
                        onClick={() => setRemove(row)}
                      >
                        移除
                      </Button>
                    </div>
                  ),
              },
            ]}
          />
          <Pagination
            page={list.page}
            total={list.total}
            pageSize={list.pageSize}
            onChange={list.setPage}
            onPageSizeChange={list.setPageSize}
            loading={list.loading}
          />
          {editing && (
            <AssignmentEditor
              row={editing}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
          {remove && (
            <MutationDialog
              title="移除月度薪资安排"
              description={`移除 ${remove.month}「${remove.account_name}」的「${remove.plan_name}」安排。已确认月份不可修改。`}
              onClose={() => setRemove(null)}
              onSubmit={async () => {
                await request({
                  url: `/salary-assignments/${remove.id}/`,
                  method: "DELETE",
                  data: { revision: remove.revision },
                })
                toast.success("月度安排已移除")
                void list.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function AssignmentEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({ ...row })
  const [adoptCurrent, setAdoptCurrent] = useState(false)
  const options = useLoad(async () => {
    const [employees, plans, originalPlan] = await Promise.all([
      allPayrollOptions("/payroll/employees/"),
      allPayrollOptions("/salary-plans/", { is_active: true }),
      row.id && row.plan
        ? request<Row>({ url: `/salary-plans/${row.plan}/` }).then(
            (result) => result.data
          )
        : Promise.resolve(null),
    ])
    return { employees, plans, originalPlan }
  })
  const samePlan = !!row.id && Number(form.plan) === Number(row.plan)
  const selectedPlan =
    options.data?.plans.find((plan) => Number(plan.id) === Number(form.plan)) ||
    (samePlan ? options.data?.originalPlan : null)
  const snapshot = row.plan_snapshot
  const changedTemplate =
    samePlan &&
    selectedPlan &&
    Number(selectedPlan.revision) !== Number(snapshot?.revision)
  const adoptsTemplate = !samePlan || adoptCurrent
  return (
    <MutationDialog
      title={row.id ? "调整员工月度安排" : "新增员工月度安排"}
      description="已有安排默认保留原薪资快照。换方案或明确采纳新版才更新薪资条款，已确认月份不可调整。"
      onClose={onClose}
      submitText="保存月度安排"
      contentClassName="sm:max-w-3xl"
      confirmDiscard
      disabled={
        options.loading ||
        !!options.error ||
        !form.month ||
        !form.account ||
        !form.plan ||
        (adoptsTemplate && !selectedPlan)
      }
      onSubmit={async () => {
        await request({
          url: `/salary-assignments/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: {
            month: form.month,
            account: Number(form.account),
            ...(!samePlan ? { plan: Number(form.plan) } : {}),
            ...(samePlan && adoptCurrent ? { adopt_current_plan: true } : {}),
            ...(adoptsTemplate && selectedPlan
              ? { expected_plan_revision: selectedPlan.revision }
              : {}),
            ...(row.id ? { revision: row.revision } : {}),
          },
        })
        toast.success("月度薪资安排已保存")
        onSaved()
      }}
    >
      <Feedback message={options.error} retry={options.reload} />
      <FormSection title="员工与核算月份">
        <fieldset
          disabled={options.loading || !!options.error}
          className="grid grid-cols-2 gap-4 disabled:opacity-70"
        >
          <PayrollInput
            label="核算月份"
            type="month"
            required
            value={form.month}
            onChange={(value) => setForm({ ...form, month: value })}
          />
          <PayrollSelect
            label="员工"
            value={form.account}
            required
            options={withCurrentOption(
              options.data?.employees || [],
              row.account,
              row.account_name
            )}
            onChange={(value) => setForm({ ...form, account: value })}
          />
          <PayrollSelect
            label="薪资方案"
            value={form.plan}
            required
            options={withCurrentOption(
              options.data?.plans || [],
              row.plan,
              row.plan_name
            )}
            onChange={(value) => {
              setForm({ ...form, plan: value })
              setAdoptCurrent(false)
            }}
          />
        </fieldset>
      </FormSection>
      {row.id && snapshot && (
        <SalaryTerms title="现有月度薪资快照" plan={snapshot} />
      )}
      {selectedPlan && (!samePlan || changedTemplate) && (
        <SalaryTerms
          title={samePlan ? "当前方案模板（尚未采纳）" : "本次选择的方案模板"}
          plan={selectedPlan}
        />
      )}
      {changedTemplate && (
        <div className="space-y-3 border-t pt-4">
          <label className="flex items-start gap-3 text-sm leading-6">
            <Checkbox name="payroll_confirmation"
              className="mt-1 shrink-0"
              checked={adoptCurrent}
              onCheckedChange={(checked) => setAdoptCurrent(checked === true)}
              disabled={!selectedPlan?.is_active}
            />
            <span>
              我已核对，同意将本月薪资更新为当前方案第 {selectedPlan.revision}{" "}
              版
            </span>
          </label>
          <p className="text-xs leading-5 text-muted-foreground">
            {adoptCurrent
              ? "保存后将以以上当前模板重新冻结本月薪资条款。"
              : "未勾选时，保存仅调整人员或月份，原底薪、绩效、提成及方案版本均保留。"}
          </p>
        </div>
      )}
      {samePlan && !changedTemplate && snapshot && (
        <PayrollNotice>
          本次保存保留第 {snapshot.revision}{" "}
          版月度快照，不因方案再次读取而重新设置薪资。
        </PayrollNotice>
      )}
    </MutationDialog>
  )
}

function SalaryTerms({ title, plan }: { title: string; plan: Row }) {
  return (
    <section className="space-y-3 border-t pt-4">
      <h3 className="text-sm font-medium">
        {title} · 第 {plan.revision} 版
      </h3>
      <p className="text-sm">
        {plan.name} · {plan.position_name}
      </p>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">人均月度底薪</dt>
          <dd className="mt-1 tabular-nums">{rmb(plan.base_fen)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">绩效上限 / 目标</dt>
          <dd className="mt-1 tabular-nums">
            {rmb(plan.performance_fen)} / {rmb(plan.target_fen)} ·{" "}
            {plan.target_scope === "STORE" ? "门店" : "个人"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">派单日班 / 夜班提成</dt>
          <dd className="mt-1 tabular-nums">
            {percentText(plan.dispatch_day_bps)} /{" "}
            {percentText(plan.dispatch_night_bps)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">预存充值提成</dt>
          <dd className="mt-1 tabular-nums">
            {percentText(plan.recharge_bps)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">售后单次提成</dt>
          <dd className="mt-1 tabular-nums">{rmb(plan.aftersale_fee_fen)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">
            考试单次抽前 / 平台扣率
          </dt>
          <dd className="mt-1 tabular-nums">
            {rmb(plan.exam_fee_fen)} / {percentText(plan.exam_commission_bps)}
          </dd>
        </div>
      </dl>
    </section>
  )
}

export function SalaryActivitiesPage() {
  const { hasPerm } = useAdminAuth()
  const [search, setSearch] = useSearchParams()
  const kind = search.get("kind") || search.get("source_kind") || "DISPATCH"
  const sourceId = search.get("source_id") || ""
  const [month, setMonth] = useState(currentMonth)
  const list = useList("salary-activities", { month, kind })
  const [editing, setEditing] = useState<Row | null>(() =>
    sourceId ? { kind, source_id: sourceId } : null
  )
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="salary-activities"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="业绩核实"
            title="薪资业绩归属"
            description="单独核实销售与计次归属。充值收款人、派单操作人不自动等于获提成员工。"
            filterDescription="月份与业绩类型作用于本页归属记录，不改变原业务来源。"
            filters={<div className="flex flex-wrap items-end gap-4">
            <PayrollInput
              label="发生月份"
              type="month"
              value={month}
              onChange={setMonth}
            />
            <PayrollSelect
              label="业绩类型"
              value={kind}
              options={activityKinds}
              onChange={(value) => setSearch({ kind: value })}
            />
          </div>}
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {hasPerm("salary:edit") && (
              <Button size="sm" onClick={() => setEditing({ kind })}>
                登记归属
              </Button>
            )}
          </PageHeader>

          <PayrollNotice>
            派单和预存充值按真实业务时间计算，退款后重新核算净额；售后按实际完成周期去重。考试需登记一次已核实事实与说明，不把报名审核当成考试完成。
          </PayrollNotice>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable
            title="业绩归属记录"
            titleIcon={ReceiptText}
            description="按真实来源核对金额、时间与归属"
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "kind",
                label: "类型",
                render: (row) => kindLabel(row.kind),
              },
              { key: "account_name", label: "归属员工" },
              {
                key: "order_no",
                label: "业务来源",
                render: (row) => activitySourceLabel(row),
              },
              {
                key: "source_amount_fen",
                label: "来源净额（元）",
                render: (row) =>
                  row.source_amount_fen == null
                    ? "计次"
                    : rmb(row.source_amount_fen),
              },
              {
                key: "performed_at",
                label: "发生时间",
                render: (row) => formatDateTime(row.performed_at),
              },
              {
                key: "reason",
                label: "归属说明",
                className: "max-w-80 whitespace-normal",
              },
              {
                key: "actions",
                label: "操作",
                render: (row) =>
                  hasPerm("salary:edit") && (
                    <Button
                      actionTone="reject"
                      size="sm"
                      variant="ghost"
                      onClick={() => setRemove(row)}
                    >
                      撤销归属
                    </Button>
                  ),
              },
            ]}
          />
          <Pagination
            page={list.page}
            total={list.total}
            pageSize={list.pageSize}
            onChange={list.setPage}
            onPageSizeChange={list.setPageSize}
            loading={list.loading}
          />
          {editing && hasPerm("salary:edit") && (
            <ActivityEditor
              initial={editing}
              onClose={() => {
                setEditing(null)
                if (sourceId) setSearch({ kind })
              }}
              onSaved={list.reload}
            />
          )}
          {remove && (
            <MutationDialog
              title="撤销业绩归属"
              description={`撤销「${remove.account_name}」的${kindLabel(remove.kind)}：${activitySourceLabel(remove)}。说明：${remove.reason || "未填写"}。已确认月份不能撤销；其他记录可在核实后重新登记。`}
              onClose={() => setRemove(null)}
              onSubmit={async () => {
                await request({
                  url: `/salary-activities/${remove.id}/`,
                  method: "DELETE",
                })
                toast.success("业绩归属已撤销")
                void list.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function ActivityEditor({
  initial,
  onClose,
  onSaved,
}: {
  initial: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    kind: "DISPATCH",
    account: "",
    source_id: "",
    cycle_key: "",
    performed_at: "",
    reason: "",
    ...initial,
  })
  const [keyword, setKeyword] = useState(String(initial.source_id || ""))
  const [query, setQuery] = useState(keyword)
  const [unknown, setUnknown] = useState(false)
  const pendingPayload = useRef<Row | null>(null)
  const employees = useLoad(() => allPayrollOptions("/payroll/employees/"))
  const sources = useLoad(async () => {
    if (form.kind === "EXAM") return []
    const byId =
      initial.source_id &&
      query === String(initial.source_id) &&
      form.kind === initial.kind
    return allPayrollOptions("/payroll/sources/", {
      kind: form.kind,
      keyword: byId ? "" : query,
      ...(byId ? { source_id: initial.source_id } : {}),
    })
  }, [form.kind, query])
  const chosen = sources.data?.find(
    (item) =>
      String(item.id) === String(form.source_id) &&
      (form.kind !== "AFTERSALE" ||
        String(item.cycle_key || "") === String(form.cycle_key || ""))
  )
  useEffect(() => {
    if (form.kind !== "AFTERSALE" || form.cycle_key || !form.source_id) return
    const matching =
      sources.data?.filter(
        (item) => String(item.id) === String(form.source_id)
      ) || []
    if (matching.length === 1)
      setForm((old) => ({ ...old, cycle_key: matching[0].cycle_key || "" }))
  }, [sources.data, form.kind, form.source_id, form.cycle_key])
  return (
    <MutationDialog
      title="登记薪资业绩归属"
      description="将真实业务归属到员工，仅用于薪资核算，不改变订单负责人、钱包余额或权限。"
      onClose={onClose}
      submitText={unknown ? "核对并重试原归属" : "确认登记"}
      disabled={
        employees.loading ||
        !!employees.error ||
        !form.account ||
        !form.reason.trim() ||
        (form.kind !== "EXAM" &&
          (!chosen || chosen.attributed || sources.loading || !!sources.error))
      }
      onSubmit={async () => {
        let data = pendingPayload.current
        if (!data) {
          data = {
            kind: form.kind,
            account: Number(form.account),
            reason: form.reason.trim(),
          }
          if (form.kind === "EXAM") {
            if (!form.performed_at)
              throw new Error("请填写已核实考试的发生时间。")
            data.performed_at = new Date(
              `${form.performed_at}:00+08:00`
            ).toISOString()
          } else {
            if (!chosen) throw new Error("请选择已核实的业务来源。")
            data.source_id = Number(chosen.id)
            if (form.kind === "AFTERSALE") data.cycle_key = chosen.cycle_key
          }
        }
        try {
          await fundRequest("admin", "/admin/salary-activities/", data)
        } catch (cause: any) {
          if (!cause.status || cause.status >= 500) {
            pendingPayload.current = data
            setUnknown(true)
          }
          throw cause
        }
        toast.success("业绩归属已登记")
        onSaved()
      }}
    >
      <Feedback message={employees.error} retry={employees.reload} />
      {unknown && (
        <PendingResult>重试将核对原请求。返回后请先核对列表，避免重复登记考试。</PendingResult>
      )}
      <fieldset disabled={unknown} className="space-y-4 disabled:opacity-70">
        <div className="grid grid-cols-2 gap-4">
          <PayrollSelect
            label="业绩类型"
            value={form.kind}
            options={activityKinds}
            onChange={(value) => {
              setForm({ ...form, kind: value, source_id: "", cycle_key: "" })
              setKeyword("")
              setQuery("")
            }}
          />
          <PayrollSelect
            label="归属员工"
            required
            value={form.account}
            options={employees.data || []}
            onChange={(value) => setForm({ ...form, account: value })}
          />
        </div>
        {form.kind === "EXAM" ? (
          <PayrollInput
            label="考试完成时间（北京时间）"
            type="datetime-local"
            required
            value={form.performed_at}
            onChange={(value) => setForm({ ...form, performed_at: value })}
            hint="每条记录代表一次已核实考试，按员工该月薪资方案计算抽成及实得。"
          />
        ) : (
          <>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <PayrollInput
                  label="搜索业务编号或名称"
                  value={keyword}
                  onChange={setKeyword}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={sources.loading}
                onClick={() => setQuery(keyword.trim())}
              >
                查找来源
              </Button>
            </div>
            <Feedback message={sources.error} retry={sources.reload} />
            <PayrollSelect
              label="真实业务来源"
              value={
                form.source_id
                  ? `${form.source_id}|${form.cycle_key || ""}`
                  : ""
              }
              disabled={sources.loading || !!sources.error}
              options={(sources.data || []).map((item) => [
                `${item.id}|${item.cycle_key || ""}`,
                `${item.label} · ${formatDateTime(item.performed_at)}${item.attributed ? "（已有归属）" : ""}`,
              ])}
              onChange={(value) => {
                const [source_id, cycle_key] = value.split("|")
                setForm({ ...form, source_id, cycle_key: cycle_key || "" })
              }}
            />
            {chosen && (
              <PayrollNotice>
                {chosen.label} · {formatDateTime(chosen.performed_at)}
                {chosen.source_amount_fen != null
                  ? ` · ${rmb(chosen.source_amount_fen)}`
                  : ""}
                。
                {chosen.attributed
                  ? "该来源已登记归属，请先核对已有记录。"
                  : "班次将按系统配置与真实发生时间自动判定。"}
              </PayrollNotice>
            )}
          </>
        )}
        <div className="space-y-2">
          <Label htmlFor="salary-activity-reason">核实依据与归属说明</Label>
          <Textarea
            id="salary-activity-reason"
            required
            rows={3}
            value={form.reason}
            onChange={(event) =>
              setForm({ ...form, reason: event.target.value })
            }
          />
        </div>
      </fieldset>
    </MutationDialog>
  )
}

export function PayrollPage() {
  const { hasPerm } = useAdminAuth()
  const [month, setMonth] = useState(currentMonth)
  const preview = useLoad(
    async () =>
      (await request<Row>({ url: "/payroll/preview/", params: { month } }))
        .data,
    [month]
  )
  const config = useLoad(
    async () => (await request<Row>({ url: "/payroll/config/" })).data
  )
  const runs = useLoad(
    async () =>
      (await request<Row>({ url: "/payroll/runs/", params: { month } })).data,
    [month]
  )
  const [configuring, setConfiguring] = useState(false)
  const [confirming, setConfirming] = useState<Row | null>(null)
  const previewSnapshot = preview.data?.month === month ? preview.data : null
  const data: Row = previewSnapshot || {}
  const totals = data?.totals
  const effectiveNight = data?.config || config.data
  const nightSource = data.config ? preview : config
  const nightConfigured = !!effectiveNight?.night_start && !!effectiveNight?.night_end
  const nightUnset = effectiveNight?.night_start === null && effectiveNight?.night_end === null
  useEffect(() => {
    setConfirming(null)
  }, [month])
  return (
    <DataPageState
      loading={preview.loading}
      hasSnapshot={!!previewSnapshot}
      error={preview.error}
      retry={() => {
        void preview.reload()
        void config.reload()
      }}
      skeleton="payroll"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="成本快照"
            title="月度薪资核算"
            description="核对归属与薪资明细后确认成本快照。确认不会执行工资转账。"
            filterDescription="月份统一作用于员工明细、薪资统计与确认记录；夜班规则独立配置。"
            filters={<div className="flex flex-wrap items-end gap-6">
            <PayrollInput
              label="核算月份"
              type="month"
              value={month}
              onChange={setMonth}
            />
            <p className="pb-2 text-sm text-muted-foreground">
              本次核算夜班：
              {nightConfigured
                ? `${effectiveNight.night_start}–${effectiveNight.night_end}（北京时间）`
                : <Status label="未配置" tone="muted" readState={nightUnset ? undefined : nightSource.loading ? "loading" : nightSource.error ? "error" : "unread"} />}
            </p>
            <Status value={data.status}
              readState={data.status ? undefined : preview.loading ? "loading" : preview.error ? "error" : "unread"}
              label={data.status === "CONFIRMED" ? "已确认快照" : data.status === "PREVIEW" ? "待核对预览" : "状态待核对"}
              tone={data.status === "CONFIRMED" ? "success" : "warning"} />
          </div>}
          >
            <RefreshButton
              loading={preview.loading || config.loading || runs.loading}
              reload={() => {
                void preview.reload()
                void config.reload()
                void runs.reload()
              }}
            />
            {hasPerm("salary:edit") && (
              <Button
                size="sm"
                variant="outline"
                disabled={!config.data || config.loading || !!config.error}
                onClick={() => setConfiguring(true)}
              >
                配置夜班时段
              </Button>
            )}
            {hasPerm("salary:confirm") && (
              <Button
                size="sm"
                disabled={
                  preview.loading ||
                  !!preview.error ||
                  !previewSnapshot || !config.data || config.loading || !!config.error ||
                  !data?.can_confirm ||
                  data.status === "CONFIRMED"
                }
                onClick={() => setConfirming(data)}
              >
                确认本月核算
              </Button>
            )}
          </PageHeader>

          <Feedback message={config.error} retry={config.reload} />
          <Feedback message={preview.error} retry={preview.reload} />
          {(
            <>
              {!!data.warnings?.length && <FinancialSection title="核算校验" icon={FileText}><PayrollWarnings warnings={data.warnings} /></FinancialSection>}
              {preview.loading && previewSnapshot && (
                <PayrollNotice>
                  正在重新核算，以下为上次读取结果，暂不能确认。
                </PayrollNotice>
              )}
              <FinancialStats snapshotAvailable={!!previewSnapshot} items={[
                { label: "员工数", value: totals?.employee_count != null ? String(totals.employee_count) : "—", unit: "人", note: "当前核算快照", icon: UsersRound },
                ...([
                  ["底薪", "base_fen", Banknote],
                  ["绩效", "performance_fen", TrendingUp],
                  ["派单提成", "dispatch_commission_fen", Send],
                  ["预存提成", "recharge_commission_fen", CreditCard],
                  ["售后提成", "aftersale_commission_fen", Headset],
                  ["考试实得", "exam_commission_fen", GraduationCap],
                  ["薪资总成本", "total_fen", Calculator],
                ] as const).map(([label, key, icon]) => ({
                  label, value: rmb(totals?.[key]), note: "人民币 · 当前核算快照", icon,
                })),
              ]} />
              <DataTable
                title="员工核算明细"
                titleIcon={FileText}
                description="按员工核对底薪、绩效、提成与确认依据"
                rows={data.lines || []}
                loading={preview.loading} error={preview.error} retry={preview.reload} snapshotAvailable={!!previewSnapshot}
                rowKey="account"
                columns={[
                  {
                    key: "account_name",
                    label: "员工 / 岗位",
                    render: (row) => (
                      <>
                        <p className="font-medium">{row.account_name}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {row.position_name} · {row.plan_name}
                        </p>
                      </>
                    ),
                  },
                  {
                    key: "base_fen",
                    label: "底薪",
                    render: (row) => rmb(row.base_fen),
                  },
                  {
                    key: "performance_fen",
                    label: "绩效",
                    render: (row) => (
                      <>
                        {rmb(row.performance_fen)}
                        <p className="mt-1 text-xs text-muted-foreground">
                          业绩 {rmb(row.performance_basis_fen)}
                          <br />
                          目标 {rmb(row.plan_snapshot?.target_fen)}
                        </p>
                      </>
                    ),
                  },
                  {
                    key: "dispatch_commission_fen",
                    label: "派单提成",
                    render: (row) => (
                      <>
                        {rmb(row.dispatch_commission_fen)}
                        <details className="mt-1 text-xs text-muted-foreground">
                          <summary className="cursor-pointer">业绩明细</summary>
                          <p className="mt-1">
                            日班 {rmb(row.dispatch_day_amount_fen)}
                            <br />
                            夜班 {rmb(row.dispatch_night_amount_fen)}
                            {!!row.dispatch_unclassified_amount_fen && (
                              <>
                                <br />
                                待分类{" "}
                                {rmb(row.dispatch_unclassified_amount_fen)}
                              </>
                            )}
                          </p>
                        </details>
                      </>
                    ),
                  },
                  {
                    key: "recharge_commission_fen",
                    label: "预存提成",
                    render: (row) => (
                      <>
                        {rmb(row.recharge_commission_fen)}
                        <p className="mt-1 text-xs text-muted-foreground">
                          实充 {rmb(row.recharge_amount_fen)}
                        </p>
                      </>
                    ),
                  },
                  {
                    key: "aftersale_commission_fen",
                    label: "售后提成",
                    render: (row) => (
                      <>
                        {rmb(row.aftersale_commission_fen)}
                        <p className="mt-1 text-xs text-muted-foreground">
                          {row.aftersale_count} 次
                        </p>
                      </>
                    ),
                  },
                  {
                    key: "exam_commission_fen",
                    label: "考试实得",
                    render: (row) => (
                      <>
                        {rmb(row.exam_commission_fen)}
                        <p className="mt-1 text-xs text-muted-foreground">
                          {row.exam_count} 次 · 抽前 {rmb(row.exam_gross_fen)}
                          <br />
                          平台扣除 {rmb(row.exam_deduction_fen)}
                        </p>
                      </>
                    ),
                  },
                  {
                    key: "total_fen",
                    label: "薪资成本合计",
                    render: (row) => <strong>{rmb(row.total_fen)}</strong>,
                  },
                  {
                    key: "warnings",
                    label: "待核事项",
                    render: (row) =>
                      row.warnings?.length ? (
                        <ul className="max-w-64 list-disc space-y-1 pl-4 text-xs">
                          {row.warnings.map((warning: string) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            </>
          )}
          <PayrollNotice>
            净消费含可能的充值赠币，不能当成现金实收。确认后本月人员安排、归属及核算快照冻结；方案后续调整不会改写已确认成本。没有员工安排不代表无需发薪，请先核对人员是否齐全。
          </PayrollNotice>
          <Feedback message={runs.error} retry={runs.reload} />
          <DataTable
            title="确认记录"
            titleIcon={ReceiptText}
            loading={runs.loading} error={runs.error} retry={runs.reload} snapshotAvailable={!!runs.data}
            rows={runs.data?.list || []}
            columns={[
              { key: "month", label: "核算月份" },
              {
                key: "confirmed_at",
                label: "确认时间",
                render: (row) =>
                  formatDateTime(row.confirmed_at || row.created_at),
              },
              {
                key: "total_fen",
                label: "确认成本",
                render: (row) =>
                  rmb(row.total_fen ?? row.snapshot?.totals?.total_fen),
              },
            ]}
          />
          {configuring && config.data && (
            <NightConfigEditor
              row={config.data}
              onClose={() => setConfiguring(false)}
              onSaved={() => {
                void config.reload()
                void preview.reload()
              }}
            />
          )}
          {confirming && (
            <MutationDialog
              title="确认月度薪资成本"
              description={`确认 ${confirming.month} 共 ${confirming.totals.employee_count} 人，薪资成本 ${rmb(confirming.totals.total_fen)}。确认后冻结本月核算记录并计入 ROI，不执行转账。`}
              onClose={() => setConfirming(null)}
              submitText="确认核算快照"
              onSubmit={async () => {
                try {
                  await request({
                    url: "/payroll/confirm/",
                    method: "POST",
                    data: {
                      month: confirming.month,
                      fingerprint: confirming.fingerprint,
                    },
                  })
                } catch (cause) {
                  void preview.reload()
                  void runs.reload()
                  throw cause
                }
                toast.success("月度薪资成本已确认")
                void preview.reload()
                void runs.reload()
              }}
            >
              <PayrollWarnings warnings={confirming.warnings} />
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}

function NightConfigEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [start, setStart] = useState(row.night_start || "")
  const [end, setEnd] = useState(row.night_end || "")
  return (
    <MutationDialog
      title="配置夜班自动判定"
      description="使用真实派单时间与北京时间判断日夜班；支持跨午夜时段，起始时刻包含、结束时刻不包含。"
      onClose={onClose}
      submitText="保存夜班时段"
      contentClassName="sm:max-w-xl"
      confirmDiscard
      disabled={!start || !end || start === end}
      onSubmit={async () => {
        await request({
          url: "/payroll/config/",
          method: "PUT",
          data: { night_start: start, night_end: end, revision: row.revision },
        })
        toast.success("夜班时段已保存")
        onSaved()
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        <PayrollInput
          label="夜班开始（北京时间）"
          type="time"
          required
          value={start}
          onChange={setStart}
        />
        <PayrollInput
          label="夜班结束（北京时间）"
          type="time"
          required
          value={end}
          onChange={setEnd}
        />
      </div>
      <PayrollNotice>
        未配置时不会擅自假定夜班时间。更改后请重新核对未确认月份，已确认薪资快照保持不变。
      </PayrollNotice>
    </MutationDialog>
  )
}
