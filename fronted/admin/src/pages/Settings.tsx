import { rmb as settingsRmb } from "@/lib/payroll"
import { UsersRound, KeyRound, History, ShieldCheck, Store, FileClock } from "@/components/ui/icons"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { FinancialHeader as PageHeader, FinancialSection, FinancialStats } from "./Financials"
import { coinText, coinUnit } from "@xa/shared"
import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import { useEffect, useRef, useState } from "react"
import { parseRmbFen } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  CreditCard,
  Clock3,
  ReceiptText,
  LockKeyhole,
  Calculator,
} from "@/components/ui/icons"
import { Input } from "@/components/ui/input"
import { AdminFilters } from "@/components/AdminFilters"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataPageState,
  DataTable,
  FormSection,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  Pagination,
  RefreshButton,
  ReadOnlyNotice,
  Status,
  useList,
  useLoad,
  useLiveReload,
  type FormField,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { DataRegion, DataValue, PageDataContext, ReadErrorNotice } from "@/components/page-data-state"
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from "@/lib/format"

export function MembersPage() {
  const auth = useAdminAuth()
  const [keyword, setKeyword] = useState("")
  const list = useList("admins", { keyword })
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="members"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            title="后台成员"
            description="管理成员资料、多职位分配和在职状态。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("admin:edit") && (
              <Button
                size="sm"
                onClick={() =>
                  setEditing({
                    username: "",
                    password: "",
                    nickname: "",
                    phone: "",
                    remark: "",
                    roles: [],
                    is_active: true,
                  })
                }
              >
                新增成员
              </Button>
            )}
          </PageHeader>
          
          <Feedback message={list.error} retry={list.reload} />
          <div className="fin-records-group"><DataTable
            toolbar={<AdminFilters primary={<div className="min-w-64 max-w-sm space-y-2">
            <Label htmlFor="member-keyword">搜索后台成员</Label>
            <Input
            id="member-keyword"
            placeholder="搜索账号、昵称或手机号…"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          /></div>} />}
            title="后台成员列表"
            titleIcon={UsersRound}
            description="核对后台账号、职位与在职状态"
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "nickname",
                label: "成员",
                render: (row) => (
                  <>
                    <p>{row.nickname || row.username}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.username} · {row.entity_no || `#${row.id}`}
                    </p>
                  </>
                ),
              },
              { key: "role_names", label: "职位" },
              { key: "phone", label: "手机号" },
              {
                key: "today_dispatch_amount",
                label: "今日派单额",
                render: (row) => (
                  <DataValue known={row.today_dispatch_amount != null}>{amountToXaCoin(row.today_dispatch_amount)} {coinUnit()}</DataValue>
                ),
              },
              {
                key: "is_superuser",
                label: "超级管理员",
                render: (row) => (row.is_superuser ? "是" : "否"),
              },
              {
                key: "is_active",
                label: "在职状态",
                render: (row) => (
                  <Status
                    value={row.is_active}
                    label={row.is_active ? "在职" : "离职"}
                    readState={typeof row.is_active === "boolean" ? undefined : "unread"}
                  />
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) =>
                  auth.hasPerm("admin:edit") && (
                    <div className="flex gap-1">
                      <Button
                        actionTone="edit"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setEditing({
                            ...row,
                            roles: row.role_ids || [],
                            password: "",
                          })
                        }
                      >
                        编辑
                      </Button>
                      <Button
                        actionTone="reject"
                        variant="ghost"
                        size="sm"
                        disabled={row.is_superuser}
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
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {editing && (
            <MemberEditor
              row={editing}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
          {remove && (
            <MutationDialog
              title="删除后台成员"
              submitVariant="destructive"
              description={`确认删除「${remove.nickname || remove.username}」的后台成员资格？`}
              onClose={() => setRemove(null)}
              onSubmit={async () => {
                await request({
                  url: `/admins/${remove.id}/`,
                  method: "DELETE",
                })
                toast.success("已删除成员")
                void list.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function MemberEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState(row)
  return (
    <MutationDialog
      title={row.id ? "编辑后台成员" : "新增后台成员"}
      description="选择成员可使用的职位。新建账号请使用独立初始密码。"
      onClose={onClose}
      onSubmit={async () => {
        if (!form.roles?.length) throw new Error("请选择至少一个职位。")
        if (!row.id && (!form.username.trim() || !form.password))
          throw new Error("请填写账号和密码。")
        if (!row.id && !/^1[3-9]\d{9}$/.test(String(form.phone || "").trim()))
          throw new Error("请填写可接收短信的手机号，首次登录需要短信验证。")
        await request({
          url: `/admins/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: {
            nickname: form.nickname,
            phone: form.phone,
            remark: form.remark,
            roles: form.roles,
            ...(row.id
              ? { is_active: form.is_active }
              : { username: form.username.trim(), password: form.password }),
          },
        })
        toast.success("成员资料已保存")
        onSaved()
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        {[
          {
            key: "username",
            label: "账号",
            disabled: !!row.id,
            required: true,
          },
          ...(!row.id
            ? [{ key: "password", label: "初始密码", type: "password" }]
            : []),
          { key: "nickname", label: "昵称" },
          { key: "phone", label: "手机号（短信验证）", required: true },
          { key: "remark", label: "备注", type: "textarea", full: true },
          ...(row.id
            ? [{ key: "is_active", label: "在职", type: "boolean" }]
            : []),
        ].map((field) => (
          <Field
            key={field.key}
            field={field as FormField}
            value={form[field.key]}
            onChange={(value) =>
              setForm((old) => ({ ...old, [field.key]: value }))
            }
          />
        ))}
        <div className="col-span-2">
          <OptionPicker
            resource="roles"
            label="职位"
            multiple
            value={form.roles}
            onChange={(value) => setForm((old) => ({ ...old, roles: value }))}
          />
        </div>
      </div>
    </MutationDialog>
  )
}
export function RolesPage() {
  const auth = useAdminAuth()
  const list = useList("roles")
  const [editing, setEditing] = useState<Row | null>(null)
  const [remove, setRemove] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="roles"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            title="角色权限"
            description="按职责分配功能权限。内置超级管理员角色和有关联成员的角色受到保护。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("role:edit") && (
              <Button
                size="sm"
                onClick={() =>
                  setEditing({
                    name: "",
                    code: "",
                    description: "",
                    permissions: [],
                    sort_order: 0,
                    is_active: true,
                  })
                }
              >
                新增角色
              </Button>
            )}
          </PageHeader>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable
            title="职位与权限"
            titleIcon={KeyRound}
            description="权限职位与薪资岗位分别管理"
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              { key: "name", label: "角色名称" },
              { key: "code", label: "角色标识" },
              { key: "description", label: "描述" },
              {
                key: "permissions",
                label: "权限数",
                render: (row) => row.permissions?.length || 0,
              },
              { key: "member_count", label: "成员数" },
              {
                key: "is_active",
                label: "状态",
                render: (row) => (
                  <Status
                    value={row.is_active}
                    label={row.is_active ? "已启用" : "已停用"}
                    readState={typeof row.is_active === "boolean" ? undefined : "unread"}
                  />
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) => (
                  <div className="flex gap-1">
                    {auth.hasPerm("role:edit") && (
                      <Button
                        actionTone="edit"
                        variant="outline"
                        size="sm"
                        onClick={() => setEditing(row)}
                      >
                        编辑权限
                      </Button>
                    )}
                    {auth.hasPerm("role:delete") && (
                      <Button
                        actionTone="reject"
                        variant="ghost"
                        size="sm"
                        disabled={
                          row.code === "super_admin" || row.member_count > 0
                        }
                        onClick={() => setRemove(row)}
                      >
                        删除
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <Pagination
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {editing && (
            <RoleEditor
              row={editing}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
          {remove && (
            <MutationDialog
              title="删除角色"
              submitVariant="destructive"
              description={`确定删除「${remove.name}」？`}
              onClose={() => setRemove(null)}
              onSubmit={async () => {
                await request({ url: `/roles/${remove.id}/`, method: "DELETE" })
                toast.success("已删除角色")
                void list.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function RoleEditor({
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
    permissions: [...row.permissions],
  })
  const [search, setSearch] = useState("")
  const permissions = useLoad(
    async () => (await request({ url: "/permissions" })).data
  )
  const visiblePermissions = (permissions.data || []).filter((group: Row) => `${group.label} ${group.group}`.toLowerCase().includes(search.toLowerCase()))
  function toggle(codes: string[], selected: boolean) {
    setForm((old) => ({
      ...old,
      permissions: selected
        ? [...new Set([...old.permissions, ...codes])]
        : old.permissions.filter((code: string) => !codes.includes(code)),
    }))
  }
  return (
    <MutationDialog
      confirmDiscard
      contentClassName="sm:max-w-5xl"
      title={row.id ? "编辑角色权限" : "新增角色"}
      description="配置角色资料和功能权限。"
      onClose={onClose}
      disabled={!permissions.data || permissions.loading || !!permissions.error}
      onSubmit={async () => {
        if (!form.name.trim() || !form.code.trim())
          throw new Error("请填写角色名称与标识。")
        await request({
          url: `/roles/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data: {
            name: form.name,
            code: form.code,
            description: form.description,
            sort_order: form.sort_order,
            is_active: form.is_active,
            permissions: form.permissions,
          },
        })
        toast.success("角色权限已保存")
        onSaved()
      }}
    >
      <FormSection title="角色资料">
        <div className="grid grid-cols-2 gap-4">
          {[
            { key: "name", label: "角色名称", required: true },
            {
              key: "code",
              label: "英文标识",
              disabled: !!row.id,
              required: true,
            },
            { key: "description", label: "描述", type: "textarea", full: true },
            { key: "sort_order", label: "排序", type: "number", min: 0 },
            {
              key: "is_active",
              label: "启用",
              type: "boolean",
              disabled: row.code === "super_admin",
            },
          ].map((field) => (
            <Field
              key={field.key}
              field={field as FormField}
              value={form[field.key]}
              onChange={(value) =>
                setForm((old) => ({ ...old, [field.key]: value }))
              }
            />
          ))}
        </div>
      </FormSection>
      <div className="flex items-end justify-between gap-4">
        <strong className="text-sm">
          功能权限 · 已选 {form.permissions.length} 项
        </strong>
        <div className="w-52 space-y-2">
        <Label htmlFor="role-permission-search">搜索权限分组</Label>
        <Input
          id="role-permission-search"
          placeholder="搜索权限分组…"
          value={search}
          disabled={permissions.loading}
          onChange={(event) => setSearch(event.target.value)}
        />
        </div>
      </div>
      <div className="max-h-[440px] space-y-3 overflow-y-auto rounded-xl border bg-muted/15 p-4" aria-busy={permissions.loading || undefined}>
        <DataRegion label="功能权限" loading={permissions.loading} hasSnapshot={!!permissions.data} error={permissions.error} retry={permissions.reload}>
        {!visiblePermissions.length && <div className="flex min-h-40 flex-col items-center justify-center text-center"><KeyRound className="size-7 text-muted-foreground" /><p className="mt-4 text-sm font-semibold">{search ? "没有匹配的权限分组" : "暂无可配置权限"}</p>{search && <Button type="button" variant="outline" className="mt-4" onClick={() => setSearch("")}>清除搜索</Button>}</div>}
        <fieldset disabled={permissions.loading || !!permissions.error} className="space-y-3">{visiblePermissions.map((group: Row) => (
            <section
              key={group.group}
              className="space-y-3 border-b pb-4 last:border-b-0"
            >
              <label className="flex items-center gap-2 text-sm font-semibold">
                <Checkbox
                  name={`permission-group-${group.group}`}
                  checked={group.permissions.every(([code]: string[]) => form.permissions.includes(code)) ? true : group.permissions.some(([code]: string[]) => form.permissions.includes(code)) ? "indeterminate" : false}
                  onCheckedChange={(checked) =>
                    toggle(
                      group.permissions.map(([code]: string[]) => code),
                      checked === true
                    )
                  }
                />
                {group.label}
              </label>
              <div className="grid grid-cols-2 gap-3">
                {group.permissions.map(([code, label]: string[]) => (
                  <label key={code} className="flex items-start gap-2 text-xs">
                    <Checkbox
                      name={`permission-${code}`}
                      checked={form.permissions.includes(code)}
                      onCheckedChange={(checked) => toggle([code], checked === true)}
                    />
                    <span>
                      {label}
                      <span className="mt-1 block text-muted-foreground">
                        {code}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </section>
          ))}</fieldset>
        </DataRegion>
      </div>
    </MutationDialog>
  )
}
export function AuditPage() {
  const [query, setQuery] = useState<Row>({ keyword: "" })
  const list = useList("audit-logs", query)
  const [detail, setDetail] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="audit"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            title="操作审计日志"
            description="追溯后台操作人、请求资源、响应状态与时间。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </PageHeader>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable
            toolbar={
              <AdminFilters title="审计日志筛选" activeCount={[query.operator, query.method].filter(Boolean).length} primary={<>
              <div className="min-w-64 max-w-sm space-y-2">
              <Label htmlFor="audit-keyword">搜索审计日志</Label>
              <Input
                id="audit-keyword"
                placeholder="搜索操作人或资源…"
                value={query.keyword ?? ""}
                onChange={(event) =>
                  setQuery((old) => ({ ...old, keyword: event.target.value }))
            }
          />
          </div>
          {[
            { key: "start_date", label: "开始日期", type: "date" },
            { key: "end_date", label: "结束日期", type: "date" },
          ].map((field) => <Field key={field.key} field={field as FormField} value={query[field.key]} onChange={(value) => setQuery((old) => ({ ...old, [field.key]: value }))} />)}
          </>}>
            {[
              { key: "operator", label: "操作人 ID" },
              {
                key: "method",
                label: "请求方法",
                type: "select",
                options: [
                  ["POST", "POST"],
                  ["PUT", "PUT"],
                  ["PATCH", "PATCH"],
                  ["DELETE", "DELETE"],
                  ["GET", "GET"],
                ],
              },
            ].map((field) => (
              <Field
                key={field.key}
                field={field as FormField}
                value={query[field.key]}
                onChange={(value) =>
                  setQuery((old) => ({ ...old, [field.key]: value }))
                }
              />
            ))}
          </AdminFilters>
            }
            title="操作审计记录"
            titleIcon={History}
            description="按操作人、对象和时间追溯真实变更"
            loading={list.loading}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            columns={[
              { key: "id", label: "记录" },
              { key: "operator_name", label: "操作人" },
              { key: "method", label: "方法" },
              { key: "path", label: "路径" },
              { key: "resource", label: "资源" },
              { align: "center", key: "status_code", label: "响应状态" },
              { key: "ip", label: "来源 IP" },
              {
                key: "created_at",
                label: "时间",
                render: (row) => formatDateTime(row.created_at),
              },
              {
                align: "right",
                key: "details",
                label: "操作",
                render: (row) => (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setDetail(row)}
                  >
                    请求详情
                  </Button>
                ),
              },
            ]}
          />
          <Pagination
            page={list.page}
            total={list.total}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
            loading={list.loading}
          />
          <Dialog
            open={!!detail}
            onOpenChange={(value) => {
              if (!value) setDetail(null)
            }}
          >
            <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-3xl">
              <DialogHeader>
                <DialogTitle>审计记录 #{detail?.id}</DialogTitle>
                <DialogDescription>
                  {detail?.operator_name} · {detail?.method} {detail?.path} ·{" "}
                  {detail?.status_code}
                </DialogDescription>
              </DialogHeader>
              <p className="text-xs text-muted-foreground">请求内容已脱敏。</p>
              <pre className="max-h-[60vh] overflow-auto rounded-lg bg-muted p-4 text-sm whitespace-pre-wrap">
                {detail ? JSON.stringify(detail.request_body, null, 2) : ""}
              </pre>
            </DialogContent>
          </Dialog>
        </div>
      }
    </DataPageState>
  )
}

export function DepositRulesPage() {
  const showTarget = (amount: number) => (amount / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })
  const auth = useAdminAuth()
  const result = useLoad(
    async () => (await request({ url: "/config/deposit-rules" })).data
  )
  const [percent, setPercent] = useState(0)
  const [cashTarget, setCashTarget] = useState("")
  const [earningsTarget, setEarningsTarget] = useState("")
  const [targetsConfirming, setTargetsConfirming] = useState(false)
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const locked =
    result.loading ||
    !!result.error ||
    needsRefresh ||
    !result.data ||
    !auth.hasPerm("deposit_rule:edit")
  const [confirming, setConfirming] = useState(false)
  useEffect(() => {
    if (result.data) {
      setPercent(result.data.deposit_percent)
      setCashTarget(String(result.data.cash_target / 10))
      setEarningsTarget(String(result.data.earnings_target / 10))
      setNeedsRefresh(false)
    }
  }, [result.data])
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="deposit-rules"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            title="押金与结款规则"
            description="单抵净收益在押金与可结款之间分配，修改规则不直接改变钱包余额。"
          >
            <RefreshButton loading={result.loading} reload={result.reload} />
          </PageHeader>
          <Feedback message={result.error} retry={result.reload} />
          <ReadOnlyNotice
            canEdit={auth.hasPerm("deposit_rule:edit")}
            subject="押金与结款规则"
          />
          {needsRefresh && (
            <Feedback
              message="保存结果待核对，请重新读取当前分配规则后再操作。"
              retry={result.reload}
            />
          )}
            <section className="space-y-5">
              <FinancialStats columns={3} items={[
                { label: "现金押金目标", value: <DataValue known={!!result.data} loading={result.loading}>{result.data ? showTarget(result.data.cash_target) : null}</DataValue>, unit: coinUnit(), note: "已保存的押金规则", icon: LockKeyhole },
                { label: "单抵押金目标", value: <DataValue known={!!result.data} loading={result.loading}>{result.data ? showTarget(result.data.earnings_target) : null}</DataValue>, unit: coinUnit(), note: "与可结款金额分开核对", icon: ReceiptText },
                { label: "收益保护时长", value: <DataValue known={!!result.data} loading={result.loading}>{result.data?.hold_hours}</DataValue>, unit: "小时", note: "已保存的结算规则", icon: Clock3 },
              ]} />
              <FinancialSection title="门店押金目标" description="修改后按新目标核对本店陪玩，已缴押金保留。" icon={LockKeyhole}>
                {!result.data && <DataRegion label="押金目标" loading={result.loading} hasSnapshot={false} error={result.error} retry={result.reload}>{null}</DataRegion>}
                <div className="grid max-w-3xl grid-cols-2 gap-6">
                  <Field field={{ key: "cash-target", label: coinText("现金押金目标（浪花）"), type: "number", min: 0.1, step: 0.1, disabled: locked }}
                    value={cashTarget} onChange={(value) => setCashTarget(String(value))} />
                  <Field field={{ key: "earnings-target", label: coinText("单抵押金目标（浪花）"), type: "number", min: 0.1, step: 0.1, disabled: locked }}
                    value={earningsTarget} onChange={(value) => setEarningsTarget(String(value))} />
                </div>
                {auth.hasPerm("deposit_rule:edit") && <Button disabled={locked || !cashTarget || !earningsTarget}
                  onClick={() => setTargetsConfirming(true)}>保存押金目标</Button>}
              </FinancialSection>
              <FinancialSection title="单抵净收益分配" description="修改规则不会直接改变钱包余额" icon={LockKeyhole}>
                <div className="grid max-w-3xl grid-cols-2 gap-6">
                  <Field
                    field={{
                      key: "deposit-percent",
                      label: "单抵押金分配比例（%）",
                      type: "number",
                      min: 0,
                      max: 100,
                      step: 1,
                      disabled: locked,
                    }}
                    value={result.data ? percent : ""}
                    onChange={setPercent}
                  />
                  <div className="text-sm leading-6">
                    <p>可结款比例：<DataValue known={!!result.data} loading={result.loading}>{100 - percent}%</DataValue></p>
                    <p className="text-muted-foreground"><DataValue known={!!result.data} loading={result.loading}>
                      {coinText("每 100 浪花净收益中，")}
                      {percent} {coinText(" 浪花用于抵押金，")}
                      {100 - percent} {coinText(" 浪花按结款规则处理。")}
                    </DataValue></p>
                  </div>
                </div>
                {auth.hasPerm("deposit_rule:edit") && (
                  <Button
                    disabled={
                      locked ||
                      !Number.isInteger(percent) ||
                      percent < 0 ||
                      percent > 100
                    }
                    onClick={() => setConfirming(true)}
                  >
                    保存分配规则
                  </Button>
                )}
              </FinancialSection>
            </section>
          <GiftSettlementClocks />
          {targetsConfirming && result.data && (
            <MutationDialog title="确认押金目标" readOnly={locked}
              description={coinText(`现金押金 ${showTarget(result.data.cash_target)} → ${cashTarget} 浪花；单抵押金 ${showTarget(result.data.earnings_target)} → ${earningsTarget} 浪花。`)}
              onClose={() => setTargetsConfirming(false)} onSubmit={async () => {
                const cash = xaCoinToAmount(cashTarget), earnings = xaCoinToAmount(earningsTarget)
                if (!/^\d+(?:\.\d)?$/.test(cashTarget) || !/^\d+(?:\.\d)?$/.test(earningsTarget) ||
                    !Number.isSafeInteger(cash) || !Number.isSafeInteger(earnings) || cash <= 0 || earnings <= 0)
                  throw new Error("请填写大于零且精度不超过 0.1 店币的押金目标。")
                try {
                  await request({ url: "/config/deposit-targets", method: "PUT", data: {
                    cash_target: cash, earnings_target: earnings, expected_version: result.data.version,
                  } })
                } catch (cause: any) {
                  if (!cause.status || cause.status >= 500 || [408, 409, 429].includes(cause.status)) setNeedsRefresh(true)
                  throw cause
                }
                toast.success("押金目标已保存")
                void result.reload()
              }} />
          )}
          {confirming && result.data && (
            <MutationDialog
              title="确认单抵分配规则"
              readOnly={locked}
              description={`将押金比例由 ${result.data.deposit_percent}% 调整为 ${percent}%，可结款比例为 ${100 - percent}%。`}
              onClose={() => setConfirming(false)}
              onSubmit={async () => {
                try {
                  await request({
                    url: "/config/deposit-rules",
                    method: "PUT",
                    data: {
                      deposit_percent: percent,
                      expected_deposit_percent: result.data.deposit_percent,
                    },
                  })
                } catch (cause: any) {
                  if (
                    !cause.status ||
                    cause.status >= 500 ||
                    [408, 409, 429].includes(cause.status)
                  )
                    setNeedsRefresh(true)
                  throw cause
                }
                toast.success("分配规则已保存")
                void result.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function GiftSettlementClocks() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: "/config/settlement-clocks" })).data)
  const [aftersale, setAftersale] = useState(""), [freeze, setFreeze] = useState("")
  const [confirming, setConfirming] = useState(false), [needsRefresh, setNeedsRefresh] = useState(false)
  useEffect(() => {
    if (result.data) { setAftersale(String(result.data.aftersale_hours)); setFreeze(String(result.data.income_freeze_hours)); setNeedsRefresh(false) }
  }, [result.data])
  const valid = /^\d+$/.test(aftersale) && /^\d+$/.test(freeze) && Number(aftersale) <= 8760 && Number(freeze) <= 8760
  const locked = result.loading || !!result.error || !result.data || needsRefresh || !auth.hasPerm("deposit_rule:edit")
  return <PageDataContext.Provider value={{ loading: result.loading, hasSnapshot: !!result.data, error: result.error, retry: result.reload, noticeTarget: "region" }}><FinancialSection title="售后与收益冻结时限" description="新礼物及服务订单付款时保存时限；礼物的两段时间同时计算，普通服务的收益冻结改造仍在进行。已成交订单按各自原规则处理。" icon={Clock3}>
    {result.data && <ReadErrorNotice error={result.error} retry={result.reload} hasSnapshot />}
    <Feedback message={needsRefresh ? "保存结果待核对，请重新读取时限。" : ""} retry={result.reload} />
    {!result.data && <DataRegion label="售后与收益冻结时限" loading={result.loading} hasSnapshot={false} error={result.error} retry={result.reload}>{null}</DataRegion>}
    <div className="grid max-w-3xl grid-cols-2 gap-6">
      <Field field={{ key: "gift-aftersale-hours", label: "售后时限（小时）", type: "number", min: 0, max: 8760, step: 1, disabled: locked }} value={aftersale} onChange={value => setAftersale(String(value))} />
      <Field field={{ key: "gift-freeze-hours", label: "收益冻结时限（小时）", type: "number", min: 0, max: 8760, step: 1, disabled: locked }} value={freeze} onChange={value => setFreeze(String(value))} />
    </div>{auth.hasPerm("deposit_rule:edit") && <Button disabled={locked || !valid || (!!result.data && result.data.version > 0 && Number(aftersale) === result.data.aftersale_hours && Number(freeze) === result.data.income_freeze_hours)} onClick={() => setConfirming(true)}>保存时限</Button>}
    {confirming && result.data && <MutationDialog title="确认售后与收益冻结时限" readOnly={locked}
      description={`售后 ${result.data.aftersale_hours} → ${aftersale} 小时；冻结 ${result.data.income_freeze_hours} → ${freeze} 小时。`}
      onClose={() => setConfirming(false)} onSubmit={async () => {
        try { await request({ url: "/config/settlement-clocks", method: "PUT", data: { aftersale_hours:Number(aftersale), income_freeze_hours:Number(freeze), expected_version:result.data.version } }) }
        catch (cause: any) { if (!cause.status || cause.status >= 500 || [408, 409, 429].includes(cause.status)) setNeedsRefresh(true); throw cause }
        toast.success("时限已保存"); void result.reload()
      }} />}
  </FinancialSection></PageDataContext.Provider>
}
export function PassesPage() {
  const auth = useAdminAuth()
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const config = useLoad(
    async () => (await request({ url: "/config/provider-pass" })).data
  )
  const [draft, setDraft] = useState<Row | null>(null)
  const [query, setQuery] = useState<Row>({ keyword: "", tier: "" })
  const list = useList("pass-purchases", query)
  // Purchases change the ledger, while the editable rule draft stays intact.
  useLiveReload(list.reload)
  useEffect(() => {
    if (config.data) {
      setNeedsRefresh(false)
      setSaveError("")
      setDraft({
        ...config.data,
        products: config.data.products.map((item: Row) => ({
          ...item,
          price_input: Number(amountToXaCoin(item.daily_price)),
        })),
      })
    }
  }, [config.data])
  return (
    <div className="fin-page space-y-4">
      <PageHeader
        tone="feature"
        tag="接单资格"
        metrics={[
          { label: "已配置档位", value: config.data ? config.data.products.length : "—" },
          { label: "最快可见", value: config.data?.products.length ? `${Math.min(...config.data.products.map((item: Row) => Number(item.delay_seconds)))} 秒` : "—" },
          { label: "符合条件购买记录", value: list.data ? list.total : "—" },
        ]}
        title="通行证"
        description="陪玩购买后立即生效 24 小时，生效期间不能购买新卡。"
      >
        <RefreshButton loading={config.loading} reload={config.reload} />
      </PageHeader>
      <Feedback
        message={
          config.error
            ? `通行证规则读取失败，购买记录仍可查询。${config.error}`
            : ""
        }
        retry={config.reload}
      />
      {needsRefresh && <Feedback message="保存结果未确认，请重新读取并核对通行证规则后再修改。" retry={config.reload} />}
        <FinancialSection title="价格与接单优先级" description="按购买时的规则核对实付与有效期；可见延迟按当前规则生效" icon={ShieldCheck} action={<Badge variant="outline">{auth.hasPerm("escort:edit") ? "可配置" : "只读权限"}</Badge>}>
        <DataRegion label="通行证价格与接单优先级" loading={config.loading} hasSnapshot={!!config.data && !!draft} error={config.error} retry={config.reload}>
        {draft && <form
          className="space-y-5"
          onSubmit={async (event) => {
            event.preventDefault()
            if (saving) return
            setSaving(true)
            setSaveError("")
            try {
              if (
                draft.products.some(
                  (item: Row) =>
                    !Number.isInteger(item.delay_seconds) ||
                    item.delay_seconds < 0 ||
                    item.delay_seconds > 86400 ||
                    item.price_input < 0.1
                )
              )
                throw new Error("请核对通行证价格与 0 至 86400 秒的整数延迟。")
              await request({
                url: "/config/provider-pass",
                method: "PUT",
                data: {
                  products: draft.products.map((item: Row) => ({
                    tier: item.tier,
                    daily_price: xaCoinToAmount(item.price_input),
                    delay_seconds: item.delay_seconds,
                  })),
                  no_pass_delay_seconds: draft.no_pass_delay_seconds,
                },
              })
              toast.success("通行证规则已保存")
              void config.reload()
            } catch (cause: any) {
              if (!cause.status || cause.status >= 500) setNeedsRefresh(true)
              setSaveError(cause.message)
              toast.error(cause.message)
            } finally {
              setSaving(false)
            }
          }}
        >
          <fieldset
            disabled={saving || config.loading || !!config.error || needsRefresh}
            className="space-y-5"
          >
            <p className="text-xs leading-5 text-muted-foreground">
              价格调整仅适用于后续购买，已购有效期保留；可见延迟立即影响公共订单池。
            </p>
            {!draft.products.length && (
              <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                暂无通行证档位
              </p>
            )}
            <div className="fin-pass-products grid grid-cols-4 gap-3">
              {draft.products.map((item: Row, index: number) => (
                <section
                  key={item.tier}
                  className="min-w-0 space-y-4 rounded-xl border bg-muted/20 p-4"
                >
                  <h2 className="flex items-center gap-2 text-sm font-medium">
                    <CreditCard className="size-4 text-primary" />
                    {item.name}
                  </h2>
                  {auth.hasPerm("escort:edit") ? <><Field
                    field={{
                      key: `pass-price-${item.tier}`,
                      label: coinText("每天价格（浪花）"),
                      type: "number",
                      min: 0.1,
                      step: 0.1,
                      disabled: !auth.hasPerm("escort:edit"),
                    }}
                    value={item.price_input}
                    onChange={(value) =>
                      setDraft(
                        (old) =>
                          old && {
                            ...old,
                            products: old.products.map((p: Row, i: number) =>
                              i === index ? { ...p, price_input: value } : p
                            ),
                          }
                      )
                    }
                  /><Field
                    field={{
                      key: `pass-delay-${item.tier}`,
                      label: "订单可见延迟（秒）",
                      type: "number",
                      min: 0,
                      max: 86400,
                      step: 1,
                      disabled: !auth.hasPerm("escort:edit"),
                    }}
                    value={item.delay_seconds}
                    onChange={(value) =>
                      setDraft(
                        (old) =>
                          old && {
                            ...old,
                            products: old.products.map((p: Row, i: number) =>
                              i === index ? { ...p, delay_seconds: value } : p
                            ),
                          }
                      )
                    }
                  /></> : <dl className="fin-pass-readonly"><div><dt>每日价格</dt><dd>{amountToXaCoin(item.daily_price)}<span>{coinUnit()}</span></dd></div><div><dt>可见延迟</dt><dd>{item.delay_seconds}<span>秒</span></dd></div></dl>}
                  <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <Clock3 className="size-3.5" />
                    {Number(item.delay_seconds) === 0
                      ? "新单发布后立即可见"
                      : `新单发布 ${item.delay_seconds} 秒后可见`}
                  </p>
                </section>
              ))}
            </div>
            <div className="flex items-end justify-between">
              {auth.hasPerm("escort:edit") ? <Field
                field={{
                  key: "no-pass-delay",
                  label: "无通行证可见延迟（秒）",
                  type: "number",
                  min: 0,
                  max: 86400,
                  step: 1,
                  disabled: !auth.hasPerm("escort:edit"),
                }}
                value={draft.no_pass_delay_seconds}
                onChange={(value) =>
                  setDraft(
                    (old) => old && { ...old, no_pass_delay_seconds: value }
                  )
                }
              /> : <div><p className="text-xs text-muted-foreground">无通行证可见延迟</p><p className="mt-2 text-sm"><strong>{draft.no_pass_delay_seconds}</strong> 秒</p></div>}
              {auth.hasPerm("escort:edit") && (
                <Button
                  type="submit"
                  disabled={saving || config.loading || !!config.error || needsRefresh}
                >
                  {saving ? "保存中…" : "保存规则"}
                </Button>
              )}
            </div>
            <Feedback message={saveError} />
          </fieldset>
        </form>}
        </DataRegion></FinancialSection>
      
      <Feedback message={list.error} retry={list.reload} />
      <div className="fin-records-group"><DataTable
            toolbar={<AdminFilters primary={<>
        <div className="space-y-2">
          <label className="text-sm" htmlFor="pass-record-search">
            搜索购买记录
          </label>
          <Input
            id="pass-record-search"
            aria-label="搜索购买记录"
            className="max-w-sm"
            placeholder="输入陪玩编号、昵称、账号或流水号…"
            value={query.keyword ?? ""}
            onChange={(event) =>
              setQuery((old) => ({ ...old, keyword: event.target.value }))
            }
          />
        </div>
        <Field
          field={{
            key: "pass-record-tier",
            label: "通行证档位",
            type: "select",
            options: [
              ["BLACK", "黑卡通行证"],
              ["GOLD", "金卡通行证"],
              ["SILVER", "银卡通行证"],
              ["BRONZE", "铜卡通行证"],
            ],
          }}
          value={query.tier}
          onChange={(tier) => setQuery((old) => ({ ...old, tier }))}
        />
        </>} actions={<Button
          variant="outline"
          onClick={() => setQuery({ keyword: "", tier: "" })}
        >
          清除筛选
        </Button>} />}
        title="购买记录"
        description="核对陪玩购买、续费、实付快照与对应扣款流水"
        titleIcon={ReceiptText}
        minWidth={1080}
        rows={list.rows}
        snapshotAvailable={!!list.data}
        error={list.error || ""}
        retry={list.reload}
        loading={list.loading}
        columns={[
          { key: "account_no", label: "陪玩编号" },
          { key: "nickname", label: "陪玩" },
          { key: "tier_display", label: "通行证" },
          {
            key: "paid_amount",
            label: "实付",
            render: (row) => `${amountToXaCoin(row.paid_amount)} ${coinUnit()}`,
          },
          {
            key: "points_hundredths",
            label: "积分抵扣",
            render: (row) => row.points_hundredths > 0
              ? `${Number(row.points_hundredths) / 100} 积分，抵 ${amountToXaCoin(row.point_discount_amount)} ${coinUnit()}` : "—",
          },
          {
            key: "starts_at",
            label: "生效时间",
            render: (row) => formatDateTime(row.starts_at),
          },
          {
            key: "expires_at",
            label: "到期时间",
            render: (row) => formatDateTime(row.expires_at),
          },
          { key: "tx_no", label: "扣款流水" },
          {
            key: "refund_amount",
            label: "退款 / 本店待补",
            render: (row) => row.refunded_at
              ? `已退 ${amountToXaCoin(row.refund_amount)}，待补 ${amountToXaCoin(row.refund_shortfall)} ${coinUnit()}${row.points_returned ? `；退回 ${Number(row.points_returned) / 100} 积分` : ""}`
              : "—",
          },
          {
            key: "created_at",
            label: "购买时间",
            render: (row) => formatDateTime(row.created_at),
          },
        ]}
      /></div>
      <Pagination
        page={list.page}
        total={list.total}
        snapshotAvailable={!!list.data}
        loading={list.loading}
        onChange={list.setPage}
        pageSize={list.pageSize}
        onPageSizeChange={list.setPageSize}
      />
    </div>
  )
}

export function PaymentFeePage() {
  const auth = useAdminAuth()
  const config = useLoad(
    async () => (await request({ url: "/config/payment-fee/" })).data
  )
  const [form, setForm] = useState<Row>({
    merchant_id: "",
    appid: "",
    rate_percent: "",
    fee_basis: "",
    rounding: "",
  })
  const [historyPage, setHistoryPage] = useState(1)
  const history = useLoad(
    async () =>
      (
        await request({
          url: "/config/payment-fee/history/",
          params: { page: historyPage, page_size: 10 },
        })
      ).data,
    [historyPage, config.data?.revision]
  )
  const [amount, setAmount] = useState("")
  const [preview, setPreview] = useState<Row | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const configLocked = config.loading || !config.data || !!config.error || needsRefresh
  const configured = config.data?.configured
  const configReadState = typeof configured === "boolean" ? undefined : !config.data && config.loading ? "loading" : !config.data && config.error ? "error" : "unread"
  const previewGeneration = useRef(0)
  useEffect(() => {
    ++previewGeneration.current
    setPreview(null)
    setPreviewBusy(false)
    return () => {
      ++previewGeneration.current
    }
  }, [config.data, config.loading, config.error, needsRefresh])
  const [error, setError] = useState("")
  const [confirming, setConfirming] = useState(false)
  useEffect(() => {
    if (config.data) {
      setForm({
        merchant_id: config.data.merchant_id || "",
        appid: config.data.appid || "",
        rate_percent: config.data.rate_percent || "",
        fee_basis: config.data.fee_basis || "",
        rounding: config.data.rounding || "",
      })
      setPreview(null)
      setNeedsRefresh(false)
    }
  }, [config.data])
  const fields: FormField[] = [
    { key: "merchant_id", label: "微信商户号", required: true, maxLength: 32, hint: "仅保存商户标识，不保存支付密钥" },
    { key: "appid", label: "应用 AppID", required: true, maxLength: 32 },
    { key: "rate_percent", label: "商户费率（%）", required: true, maxLength: 8, hint: "填写 0–100 的签约费率百分数，最多四位小数，例如 0.6 表示 0.6%" },
    {
      key: "fee_basis",
      label: "手续费计算基数",
      type: "select",
      hint: "用于向老板报价，不代表渠道实际计费口径",
      options: [
        ["SERVICE_AMOUNT", "服务成交金额"],
        ["PAYMENT_TOTAL", "支付总额"],
      ],
    },
    {
      key: "rounding",
      label: "手续费取整方式",
      type: "select",
      full: true,
      options: [
        ["DOWN", "不足一分舍去"],
        ["UP", "不足一分进一分"],
        ["HALF_UP", "四舍五入到分"],
      ],
    },
  ]
  return (
    <DataPageState
      loading={config.loading}
      hasSnapshot={!!config.data}
      error={config.error}
      retry={config.reload}
      skeleton="payment-fee-config"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="支付配置"
            metrics={[
              { label: "当前版本", value: configured === false ? "未配置" : configured === true && config.data?.revision != null ? `v${config.data.revision}` : "—" },
              { label: "商户费率", value: configured === false ? "未配置" : configured === true && config.data?.rate_percent != null ? `${config.data.rate_percent}%` : "—" },
              { label: "取整方式", value: <span className="fin-fee-rounding">{config.data?.configured ? ({ DOWN: "舍去不足一分", UP: "进一分", HALF_UP: "四舍五入" } as Row)[config.data.rounding] || config.data.rounding : "—"}</span> },
            ]}
            title="微信支付费率"
            description="配置手续费计算口径并保留版本历史，试算不发起真实支付。"
          >
            <RefreshButton loading={config.loading} reload={config.reload} />
          </PageHeader>
          <Feedback message={config.error} retry={config.reload} />
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
              <FinancialSection title="商户与费率" icon={Store} description={<Status readState={configReadState} value={configured ? "ACTIVE" : "UNCONFIGURED"} label={configured ? "已配置" : "未配置"} />} action={<Badge variant="outline">{auth.hasPerm("payment_fee:edit") ? "可编辑" : "只读权限"}</Badge>} className="fin-payment-config">
                {!config.data && <DataRegion label="商户与费率" loading={config.loading} hasSnapshot={false} error={config.error} retry={config.reload}>{null}</DataRegion>}
                <div className="grid grid-cols-2 gap-4">
                  {fields.map((field) => (
                    <Field
                      key={field.key}
                      field={field}
                      value={form[field.key]}
                      disabled={
                        !auth.hasPerm("payment_fee:edit") || configLocked
                      }
                      onChange={(value) => {
                        setForm((old) => ({ ...old, [field.key]: value }))
                        setPreview(null)
                      }}
                    />
                  ))}
                </div>
                <div className="fin-payment-savebar"><p>保存会生成新版本，历史版本不会被覆盖。</p><div><RefreshButton loading={config.loading} reload={config.reload} />{auth.hasPerm("payment_fee:edit") && <Button disabled={!config.data || configLocked} onClick={() => setConfirming(true)}>核对并保存费率</Button>}</div></div>
              </FinancialSection>
              <FinancialSection title="手续费试算" icon={Calculator} description={<DataValue known={!configReadState} loading={config.loading}>{configured ? "仅使用已保存的规则，金额以人民币元计" : "保存首个费率版本后即可试算"}</DataValue>} className="fin-payment-preview">
                <DataRegion label="手续费试算配置" loading={config.loading} hasSnapshot={!!config.data} error={config.error} retry={config.reload}>
                {configReadState ? <Status readState={configReadState} /> : !configured ? (
                  <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed p-6 text-center">
                    <Calculator className="size-7 text-muted-foreground" />
                    <h2 className="mt-4 text-sm font-semibold">
                      暂无可试算配置
                    </h2>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      完整填写商户、费率和计算规则并保存，再进行金额试算。
                    </p>
                  </div>
                ) : (
                  <>
                    <Feedback message={error} />
                    <div className="flex items-end gap-3">
                      <Field
                        field={{
                          key: "fee-preview-amount",
                          label: "服务成交金额（元）",
                          type: "number",
                          min: 0.01,
                          step: 0.01,
                        }}
                        value={amount}
                        disabled={configLocked || previewBusy}
                        onChange={(value) => {
                          setAmount(String(value))
                          setPreview(null)
                        }}
                      />
                      <Button
                        variant="outline"
                        disabled={
                          configLocked ||
                          previewBusy ||
                          !amount ||
                          !config.data?.configured
                        }
                        onClick={async () => {
                          if (
                            configLocked ||
                            previewBusy ||
                            !config.data?.configured
                          )
                            return
                          const generation = ++previewGeneration.current
                          setError("")
                          setPreview(null)
                          setPreviewBusy(true)
                          try {
                            const fen = parseRmbFen(amount)
                            if (!(fen > 0))
                              throw new Error("请输入有效的服务成交金额。")
                            const result = (
                              await request({
                                url: "/config/payment-fee/preview/",
                                method: "POST",
                                data: { service_fen: fen },
                              })
                            ).data
                            if (generation === previewGeneration.current)
                              setPreview(result)
                          } catch (cause: any) {
                            if (generation === previewGeneration.current)
                              setError(cause.message)
                          } finally {
                            if (generation === previewGeneration.current)
                              setPreviewBusy(false)
                          }
                        }}
                      >
                        {previewBusy ? "正在试算…" : error ? "重试试算" : "开始试算"}
                      </Button>
                    </div>
                    <section className="fin-fee-result" role="status" aria-busy={previewBusy || undefined}>
                      <div><h3>预计支付明细</h3>{preview && !configLocked && !previewBusy && <Badge variant="outline">版本 {preview.revision}</Badge>}</div>
                      <dl>
                        <div><dt>服务金额</dt><dd><DataValue known={!!preview && !configLocked && !previewBusy} loading={previewBusy}>{settingsRmb(preview?.service_fen)}</DataValue></dd></div>
                        <div><dt>预计手续费</dt><dd className="text-primary"><DataValue known={!!preview && !configLocked && !previewBusy} loading={previewBusy}>+ {settingsRmb(preview?.fee_fen)}</DataValue></dd></div>
                        <div><dt>预计合计</dt><dd><DataValue known={!!preview && !configLocked && !previewBusy} loading={previewBusy}>{settingsRmb(preview?.total_fen)}</DataValue></dd></div>
                      </dl>
                      <p>{configLocked ? "旧试算结果已隐藏，刷新核对当前配置后可重新试算。" : previewBusy ? "正在按已保存规则试算，请稍候。" : preview ? "仅为当前已保存规则的报价试算，不会发起真实支付。" : "输入服务成交金额并开始试算，结果仅供核对，不会发起真实支付。"}</p>
                    </section>
                  </>
                )}
                </DataRegion>
              </FinancialSection>
            </div>
          <Feedback message={history.error} retry={history.reload} />
          <DataTable
            title="费率变更记录"
            titleIcon={FileClock}
            minWidth={1040}
            loadingSkeleton={<AdminPageSkeleton page="payment-fee-history" />}
            description="按版本倒序核对商户、费率与操作人"
            error={history.error || ""}
            retry={history.reload}
            loading={history.loading}
            snapshotAvailable={!!history.data}
            rows={history.data?.list || []}
            columns={[
              { key: "revision", label: "版本与时间", render: row => <div><div className="flex items-center gap-2"><strong>版本 {row.revision}</strong>{row.revision === config.data?.revision && <Badge variant="outline">当前</Badge>}</div><p className="mt-1 text-xs text-muted-foreground">{formatDateTime(row.created_at)}</p></div> },
              { key: "merchant", label: "商户号 / AppID", render: row => <div><p>{row.merchant_id}</p><p className="mt-1 text-xs text-muted-foreground">{row.appid}</p></div> },
              { key: "rate_percent", label: "费率 / 计算基数", align: "right", render: row => <div><strong>{row.rate_percent}%</strong><p className="mt-1 text-xs text-muted-foreground">{({ SERVICE_AMOUNT: "服务成交金额", PAYMENT_TOTAL: "服务金额及手续费总额" } as Row)[row.fee_basis] || row.fee_basis}</p></div> },
              { key: "rounding", label: "取整方式", render: row => ({ DOWN: "不足一分舍去", UP: "不足一分进一分", HALF_UP: "四舍五入到分" } as Row)[row.rounding] || row.rounding },
              { key: "operator_name", label: "操作人" },
            ]}
          />
          <Pagination
            page={historyPage}
            total={history.data?.total || 0}
            snapshotAvailable={!!history.data}
            onChange={setHistoryPage}
            loading={history.loading}
          />
          {confirming && config.data && (
            <MutationDialog
              title="确认费率配置"
              description={`商户 ${form.merchant_id} · AppID ${form.appid} · 费率 ${form.rate_percent}% · 版本 ${config.data.revision}。`}
              onClose={() => setConfirming(false)}
              readOnly={configLocked}
              onSubmit={async () => {
                if (
                  !/^[0-9]{8,32}$/.test(form.merchant_id) ||
                  !/^[A-Za-z0-9_-]{1,32}$/.test(form.appid) ||
                  !/^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$/.test(
                    form.rate_percent
                  ) ||
                  Number(form.rate_percent) > 100 ||
                  (form.fee_basis === "PAYMENT_TOTAL" &&
                    Number(form.rate_percent) === 100)
                )
                  throw new Error("请核对商户号、AppID 与费率范围。")
                try {
                  await request({
                    url: "/config/payment-fee/",
                    method: "PUT",
                    data: { revision: config.data.revision, ...form },
                  })
                } catch (cause: any) {
                  if (
                    !cause.status ||
                    cause.status >= 500 ||
                    [408, 409, 429].includes(cause.status)
                  )
                    setNeedsRefresh(true)
                  throw cause
                }
                toast.success("费率配置已保存")
                void config.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
