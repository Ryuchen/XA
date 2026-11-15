import { formatRmb, parseRmbFen } from "@xa/shared"
import { Link } from "react-router"
import {
  Search,
  Plus,
  BadgeCheck,
  Gamepad2,
  CheckCircle2,
  FileCheck2,
  Clock3,
  Banknote,
  UserCheck,
  RotateCcw,
  Medal,
  WalletCards,
  ShieldAlert,
} from "@/components/ui/icons"
import {
  RecordSection as FormSection,
  RecordHeading as PageHeader,
  RecordNotice,
} from "./OrderRecordVisual"
import { coinText, coinUnit } from "@xa/shared"
import { DataRegion, DataValue } from "@/components/page-data-state"
import { useEffect, useState } from "react"
import { rmbToRawAmount } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { AdminFilters } from "@/components/AdminFilters"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
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
  DraftPreview,
  ReadOnlyNotice,
  Details,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLiveReload,
  useLoad,
  type Row,
} from "@/components/business"
import QualificationFilters from "@/components/QualificationFilters"
import PrivateImage from "@/components/PrivateImage"
import { request, requestId, reviewedFundRequest } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from "@/lib/format"

const depositCoins = (value: number) => (Number(value) / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })

const qualificationText = (value: unknown) =>
  ({
    GUARD: "护航",
    COMPANION: "陪玩",
    GRIND: "代肝",
    RANK: "上分",
    PC: "端游",
    MOBILE: "手游",
    CONFIDENTIAL: "机密",
    TOP_SECRET: "绝密",
    ANY: "不限难度",
  })[String(value)] || String(value || "—")
function ProviderVerification({ value }: { value?: boolean | null }) {
  return <Status value={value == null ? undefined : value ? "VERIFIED" : "UNVERIFIED"}
    label={value == null ? undefined : value ? "已认证" : "未认证"} tone={value ? "success" : "warning"} />
}

const providerStatusText = (value: string) =>
  ({ AVAILABLE: "空闲", BUSY: "忙碌", OFFLINE: "离线" })[value] || value

export function UsersPage() {
  const auth = useAdminAuth()
  const [draft, setDraft] = useState<Row>({
    boss_no: "",
    nickname: "",
    phone: "",
    is_active: "",
  })
  const [query, setQuery] = useState(draft)
  const list = useList("users", query)
  const [operation, setOperation] = useState<{ kind: string; row: Row } | null>(
    null
  )
  useLiveReload(list.reload)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="users"
    >
      {
        <div className="space-y-4">
          <PageHeader
            title="老板资料"
            description="维护老板资料与推荐关系。账户停用和登录封禁通过专用流程记录原因。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </PageHeader>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable snapshotAvailable={!!list.data}
            toolbar={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  setQuery({ ...draft })
                }}
              >
                <AdminFilters activeCount={Number(!!draft.phone)} primary={<>
                {[
                  ["boss_no", "账户编号"],
                  ["nickname", "昵称"],
                ].map(([key, label]) => (
                  <Field
                    key={key}
                    field={{ key, label }}
                    value={draft[key]}
                    onChange={(value) => setDraft({ ...draft, [key]: value })}
                  />
                ))}
                <Field
                  field={{
                    key: "user-active",
                    label: "账户状态",
                    type: "select",
                    options: [
                      ["1", "正常"],
                      ["0", "停用"],
                    ],
                  }}
                  value={draft.is_active}
                  onChange={(value) => setDraft({ ...draft, is_active: value })}
                />
                </>} actions={<Button size="sm">查询</Button>}>
                  <Field field={{ key: "phone", label: "手机号" }} value={draft.phone} onChange={(value) => setDraft({ ...draft, phone: value })} />
                </AdminFilters>
              </form>
            }
            title="老板列表"
            description={`本页 ${list.rows.length} 人`}
            minWidth={1120}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "nickname",
                label: "老板",
                render: (row) => (
                  <>
                    <p className="font-medium">
                      {row.nickname || row.username}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {row.entity_no || row.boss_no} · {row.username}
                    </p>
                  </>
                ),
              },
              {
                key: "real_name",
                label: "姓名 / 手机",
                render: (row) => (
                  <>
                    <p>{row.real_name || "—"}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.phone || "未填写"}
                    </p>
                  </>
                ),
              },
              { key: "boss_type_name", label: "老板分级" },
              {
                key: "wallet_balance",
                label: "钱包余额",
                render: (row) => (
                  <DataValue known={row.wallet_balance != null}>{amountToXaCoin(row.wallet_balance)} {coinUnit()}</DataValue>
                ),
              },
              {
                key: "inviter_name",
                label: "推荐人 / 返佣",
                render: (row) => (
                  <>
                    {row.inviter_name || "无"}
                    <p className="text-xs text-muted-foreground">
                      {row.inviter_commission_rate == null ? "—" : `${row.inviter_commission_rate}%`}
                    </p>
                  </>
                ),
              },
              {
                key: "state",
                label: "账户 / 登录",
                render: (row) => (
                  <div className="space-y-1">
                    <Status
                      value={row.is_active == null ? undefined : row.is_active ? "ACTIVE" : "DISABLED"}
                      label={row.is_active == null ? undefined : row.is_active ? "账户正常" : "账户停用"}
                    />
                    <p className="text-xs text-muted-foreground">
                      <Status value={row.can_login == null ? undefined : row.can_login ? "ENABLED" : "BANNED"} label={row.can_login == null ? undefined : row.can_login ? "允许登录" : "登录封禁"} />
                    </p>
                  </div>
                ),
              },
              {
                key: "last_active_at",
                label: "最近活跃",
                render: (row) => formatDateTime(row.last_active_at),
              },
              {
                key: "actions",
                label: "操作",
                headerAlign: "center",
                render: (row) => (
                  <div className="admin-user-actions ml-auto flex w-full max-w-80 flex-wrap justify-end gap-1">
                    {auth.hasPerm("user:edit") && (
                      <Button
                        actionTone="edit"
                        size="xs"
                        variant="outline"
                        onClick={() => setOperation({ kind: "edit", row })}
                      >
                        编辑
                      </Button>
                    )}
                    <Button
                      actionTone="history"
                      size="xs"
                      variant="ghost"
                      onClick={() => setOperation({ kind: "ban-history", row })}
                    >
                      封禁记录
                    </Button>
                    {auth.hasPerm("user:ban") && (
                      <>
                        <Button
                          actionTone="adjust"
                          size="xs"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "ban", row })}
                        >
                          封禁
                        </Button>
                        <Button
                          actionTone="approve"
                          size="xs"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "unban", row })}
                        >
                          解封
                        </Button>
                      </>
                    )}
                    {auth.hasPerm("user:edit") && (
                      <Button
                        actionTone="reject"
                        size="xs"
                        variant="ghost"
                        onClick={() => setOperation({ kind: "retire", row })}
                      >
                        退店清空
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {operation?.kind === "edit" && (
            <UserEditor
              row={operation.row}
              onClose={() => setOperation(null)}
              onSaved={list.reload}
            />
          )}
          {operation?.kind === "deposit-topup" && (
            <CashDepositTopup row={operation.row!} onClose={() => setOperation(null)} onSaved={list.reload} />
          )}
          {operation && operation.kind !== "edit" && (
            <AccountAction
              resource="users"
              kind={operation.kind}
              row={operation.row}
              onClose={() => setOperation(null)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function UserEditor({
  row,
  onClose,
  onSaved,
}: {
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({ ...row })
  return (
    <MutationDialog
      title="编辑老板资料"
      description={`${row.nickname || row.username} · ${row.boss_no || row.id}。VIP 等级按已确认实销核定。`}
      onClose={onClose}
      onSubmit={async () => {
        if (
          !Number.isInteger(form.inviter_commission_rate) ||
          form.inviter_commission_rate < 0 ||
          form.inviter_commission_rate > 100
        )
          throw new Error("推荐返佣须为 0 到 100 的整数。")
        await request({
          url: `/users/${row.id}/`,
          method: "PATCH",
          data: {
            nickname: form.nickname,
            real_name: form.real_name,
            phone: form.phone,
            inviter: form.inviter || null,
            inviter_commission_rate: form.inviter_commission_rate,
            ...(form.boss_type !== row.boss_type
              ? { boss_type: form.boss_type || null }
              : {}),
          },
        })
        toast.success("资料已保存")
        onSaved()
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        {[
          ["nickname", "昵称"],
          ["real_name", "姓名"],
          ["phone", "手机号"],
        ].map(([key, label]) => (
          <Field
            key={key}
            field={{ key, label }}
            value={form[key]}
            onChange={(value) => setForm((old) => ({ ...old, [key]: value }))}
          />
        ))}
        <Field
          field={{
            key: "inviter_commission_rate",
            label: "推荐返佣（%）",
            type: "number",
            min: 0,
            max: 100,
            step: 1,
          }}
          value={form.inviter_commission_rate}
          onChange={(value) =>
            setForm((old) => ({ ...old, inviter_commission_rate: value }))
          }
        />
        <OptionPicker
          resource="users"
          endpoint="/users/"
          label="推荐人"
          value={form.inviter}
          onChange={(value) => setForm((old) => ({ ...old, inviter: value }))}
        />
        <OptionPicker
          resource="boss-types"
          label="老板分级"
          value={form.boss_type}
          onChange={(value) => setForm((old) => ({ ...old, boss_type: value }))}
        />
      </div>
    </MutationDialog>
  )
}
function AccountAction({
  resource,
  kind,
  row,
  onClose,
  onSaved,
}: {
  resource: "users" | "escorts"
  kind: string
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [reason, setReason] = useState("")
  const [expires, setExpires] = useState("")
  const [confirmed, setConfirmed] = useState(false)
  const [force, setForce] = useState(false)
  const auth = useAdminAuth()
  const history = useLoad(
    async () =>
      kind === "ban-history"
        ? (await request({ url: `/${resource}/${row.id}/ban-history/` })).data
        : null,
    [resource, kind, row.id]
  )
  if (kind === "ban-history")
    return (
      <Dialog
        open
        onOpenChange={(value) => {
          if (!value) onClose()
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>封禁记录</DialogTitle>
            <DialogDescription>
              {row.nickname || row.display_name || row.username}
            </DialogDescription>
          </DialogHeader>
          <Feedback message={history.error} retry={history.reload} />
          <DataTable snapshotAvailable={!!history.data} retry={history.reload}
            loading={history.loading}
            rows={
              Array.isArray(history.data)
                ? history.data
                : history.data?.list || []
            }
            columns={[
              { key: "reason", label: "原因" },
              { key: "scope", label: "范围" },
              { key: "operator_name", label: "操作人" },
              {
                key: "created_at",
                label: "封禁时间",
                render: (item) => formatDateTime(item.created_at),
              },
              {
                key: "expires_at",
                label: "到期时间",
                render: (item) =>
                  item.expires_at ? formatDateTime(item.expires_at) : "永久",
              },
              {
                key: "revoked_at",
                label: "解除时间",
                render: (item) => formatDateTime(item.revoked_at),
              },
            ]}
          />
        </DialogContent>
      </Dialog>
    )
  const title =
    kind === "ban"
      ? "封禁账号"
      : kind === "unban"
        ? "解除封禁"
        : "退店清空业务权益"
  return (
    <MutationDialog
      title={title}
      submitVariant={
        kind === "ban" || kind === "retire" ? "destructive" : "default"
      }
      description={`${row.nickname || row.display_name || row.username} · ${row.username} · #${row.id}`}
      onClose={onClose}
      disabled={!reason.trim() || !confirmed}
      submitText={`确认${title}`}
      onSubmit={async () => {
        const result = await request({
          url: `/${resource}/${row.id}/${kind}/`,
          method: "POST",
          data: {
            reason: reason.trim(),
            ...(kind === "ban"
              ? {
                  expires_at: expires ? new Date(expires).toISOString() : null,
                  ...(force ? { force: true } : {}),
                }
              : {}),
          },
        })
        if (result.data?.orphaned_order_count)
          toast.warning(
            `已封禁，该老板仍有 ${result.data.orphaned_order_count} 笔在途订单需跟进`
          )
        else toast.success("账号状态已更新")
        onSaved()
      }}
    >
      <p className="rounded-lg border bg-muted/40 p-4 text-sm leading-6">
        {kind === "retire"
          ? "将重置当前业务权益，历史订单和财务记录保留。请先核对余额与权益，并向账号持有人说明。"
          : kind === "ban"
            ? resource === "escorts"
              ? "有在途订单的陪玩不能直接封禁，请先跟进履约和转单。"
              : "封禁老板不会自动处理其在途订单，请同时安排客服跟进。"
            : "解除封禁会按封禁前的快照恢复登录状态。"}
      </p>
      <div className="space-y-2">
        <Label htmlFor="ban-reason">原因</Label>
        <Textarea
          id="ban-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={kind === "retire" ? 500 : 255}
          required
        />
      </div>
      {kind === "ban" && (
        <Field
          field={{
            key: "ban-expiry",
            label: "自动解封时间（选填，留空为永久）",
            type: "datetime-local",
          }}
          value={expires}
          onChange={setExpires}
        />
      )}
      {kind === "ban" &&
        resource === "escorts" &&
        auth.hasPerm("user:ban_force") && (
          <label className="flex items-start gap-2 rounded-lg border border-amber-500/40 p-3 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-blue-600"
              checked={force}
              onChange={(event) => {
                setForce(event.target.checked)
                setConfirmed(false)
              }}
            />
            已安排在途订单后续履约；使用强制封禁权限，由客服继续跟进受影响订单
          </label>
        )}
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-blue-600"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        已核对目标账号、操作原因及业务影响
      </label>
    </MutationDialog>
  )
}

export function EscortsPage() {
  const [draft, setDraft] = useState<Row>({
    keyword: "",
    status: "",
    is_verified: "",
    gender: "",
  })
  const [query, setQuery] = useState(draft)
  const list = useList("escorts", query)
  const auth = useAdminAuth()
  const [operation, setOperation] = useState<{
    kind: string
    row?: Row
  } | null>(null)
  useLiveReload(list.reload)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="providers"
    >
      {
        <div className="admin-providers-page space-y-4">
          <PageHeader
            title="陪玩档案"
            description="管理入驻陪玩的资质、服务范围、状态与业绩。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("escort:edit") && (
              <Button size="sm" onClick={() => setOperation({ kind: "edit" })}>
                <Plus size={16} />
                创建陪玩账号
              </Button>
            )}
          </PageHeader>
          <RecordNotice title="档案状态不等于可接单">
            账号、认证、押金、档期、具体服务资格与名额均需核验，请进入档案复核。
          </RecordNotice>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable snapshotAvailable={!!list.data}
            toolbar={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  setQuery({ ...draft })
                }}
              >
                <AdminFilters activeCount={["gender", "game_category", "provider_kind", "platform", "security_level", "grade", "pass_tier"].filter((key) => !!draft[key]).length} primary={<>
                <div className="space-y-2">
                <Label htmlFor="escort-keyword">搜索陪玩</Label>
                <div className="admin-record-search">
                  <Search size={16} />
                  <Input
                    id="escort-keyword"
                    aria-label="搜索陪玩"
                    placeholder="输入昵称、编号、账号或手机号…"
                    value={draft.keyword ?? ""}
                    onChange={(event) =>
                      setDraft({ ...draft, keyword: event.target.value })
                    }
                  />
                </div>
                </div>
                <Field
                  field={{
                    key: "escort-status",
                    label: "接单状态",
                    type: "select",
                    options: [
                      ["AVAILABLE", "空闲"],
                      ["BUSY", "忙碌"],
                      ["OFFLINE", "离线"],
                    ],
                  }}
                  value={draft.status}
                  onChange={(value) => setDraft({ ...draft, status: value })}
                />
                <Field
                  field={{
                    key: "escort-verified",
                    label: "认证状态",
                    type: "select",
                    options: [
                      ["true", "已认证"],
                      ["false", "未认证"],
                    ],
                  }}
                  value={draft.is_verified}
                  onChange={(value) => setDraft({ ...draft, is_verified: value })}
                />
                </>} actions={<>
                <Button className="h-11">查询</Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-11"
                  onClick={() => {
                    const empty = {
                      keyword: "",
                      status: "",
                      is_verified: "",
                      gender: "",
                    }
                    setDraft(empty)
                    setQuery(empty)
                  }}
                >
                  清除条件
                </Button>
                </>}>
                    <Field
                      field={{
                        key: "escort-gender",
                        label: "性别",
                        type: "select",
                        options: [
                          ["MALE", "男陪"],
                          ["FEMALE", "女陪"],
                          ["UNKNOWN", "未知"],
                        ],
                      }}
                      value={draft.gender}
                      onChange={(value) => setDraft({ ...draft, gender: value })}
                    />
                    <QualificationFilters embedded value={draft} onChange={setDraft} />
                </AdminFilters>
                {JSON.stringify(draft) !== JSON.stringify(query) && (
                  <p className="admin-record-draft-note">
                    筛选输入已更改，请点击查询更新列表。
                  </p>
                )}
              </form>
            }
            title="陪玩列表"
            description={`本页 ${list.rows.length} 人`}
            empty="没有匹配的陪玩"
            emptyDescription="调整关键词或清除筛选后再查看。"
            emptyAction={
              <Button
                variant="outline"
                onClick={() => {
                  const empty = {
                    keyword: "",
                    status: "",
                    is_verified: "",
                    gender: "",
                  }
                  setDraft(empty)
                  setQuery(empty)
                }}
              >
                清除全部条件
              </Button>
            }
            minWidth={1120}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "display_name",
                label: "陪玩",
                render: (row) => (
                  <div className="flex items-center gap-3">
                    {row.avatar_url ? (
                      <img
                        src={row.avatar_url}
                        alt=""
                        width={40}
                        height={40}
                        className="size-10 shrink-0 rounded-full object-cover"
                      />
                    ) : (
                      <span className="admin-provider-avatar-fallback">
                        {String(row.display_name || row.username || "陪").slice(
                          0,
                          1
                        )}
                      </span>
                    )}
                    <div>
                      <button
                        type="button"
                        className="text-sm font-medium text-primary hover:underline"
                        onClick={() => setOperation({ kind: "detail", row })}
                      >
                        {row.display_name || row.username}
                      </button>
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        {row.escort_no} · {row.username}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {[row.gender_display, row.level_name]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    </div>
                  </div>
                ),
              },
              {
                key: "gender_display",
                label: "服务范围",
                render: (row) => (
                  <>
                    <div className="flex max-w-60 flex-wrap gap-1">
                      {(row.game_category_names || []).map((name: string) => (
                        <Badge key={name} variant="secondary">
                          {name}
                        </Badge>
                      ))}
                      {!Array.isArray(row.game_category_names) ? <Status readState="unread" /> : row.game_category_names.length === 0 && (
                        <Status value="UNCONFIGURED" label="尚未配置游戏" />
                      )}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {row.service_item_names?.length ?? "—"} 项服务技能
                    </p>
                  </>
                ),
              },
              {
                key: "qualifications",
                label: "接单资格",
                render: (row) => {
                  if (!Array.isArray(row.qualifications) || row.qualifications.some((item: Row) => typeof item.is_active !== "boolean"))
                    return <Status readState="unread" />
                  const active = row.qualifications.filter(
                    (item: Row) => item.is_active
                  )
                  return active.length ? (
                    <div className="admin-provider-qualification-summary">
                      <strong>{active.length} 项启用资格</strong>
                      <p className="max-w-44 truncate text-muted-foreground">
                        {active
                          .map(
                            (item: Row) =>
                              `${item.game_category_name} · ${item.grade_name}`
                          )
                          .join(" / ")}
                      </p>
                    </div>
                  ) : (
                    <Status value={row.qualifications.length ? "DISABLED" : "UNCONFIGURED"} label={row.qualifications.length ? "资格均已停用" : "资格未配置"} />
                  )
                },
              },
              {
                key: "status",
                label: "状态 / 认证",
                render: (row) => (
                  <>
                    <Status
                      value={row.status}
                      label={providerStatusText(row.status)}
                      tone={
                        row.status === "AVAILABLE"
                          ? "success"
                          : row.status === "BUSY"
                            ? "warning"
                            : "muted"
                      }
                    />
                    <div className="mt-1"><ProviderVerification value={row.is_verified} /></div>
                  </>
                ),
              },
              {
                key: "deposit_paid",
                label: "已缴 / 应缴押金",
                render: (row) => (
                  <>
                    <p>
                      <DataValue known={row.deposit_paid != null}>{depositCoins(row.deposit_paid)}</DataValue> /{" "}
                      <DataValue known={(row.effective_deposit_required ?? row.deposit_required) != null}>{depositCoins(
                        row.effective_deposit_required ?? row.deposit_required
                      )}</DataValue>{" "}
                      {coinUnit()}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {row.deposit_mode === "EARNINGS"
                        ? "单抵模式"
                        : row.deposit_mode === "CASH" ? "现金模式" : "—"}
                    </p>
                  </>
                ),
              },
              {
                key: "completed_order_count",
                label: "业绩",
                align: "right",
                render: (row) => (
                  <>
                    <strong className="text-xs font-medium">
                      {row.rating_avg == null
                        ? "—"
                        : Number(row.rating_avg).toFixed(2)}{" "}
                      分
                    </strong>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {row.completed_order_count ?? "—"} 完成单
                    </p>
                  </>
                ),
              },
              {
                key: "pass_tier_display",
                label: "通行证",
                render: (row) => (
                  <>
                    <Status
                      value={row.active_pass_tier}
                      label={row.pass_tier_display || (row.active_pass_tier === null ? "无通行证" : undefined)}
                      tone="muted"
                    />
                    {row.recommendation_slot && (
                      <p className="mt-1 text-xs text-primary">
                        推荐位 {row.recommendation_slot}
                      </p>
                    )}
                  </>
                ),
              },
              {
                key: "actions",
                label: "操作",
                align: "right",
                render: (row) => (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setOperation({ kind: "detail", row })}
                  >
                    查看档案
                  </Button>
                ),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {operation?.kind === "detail" && (
            <EscortDetail
              id={operation.row!.id}
              onClose={() => setOperation(null)}
              onEdit={(row) => setOperation({ kind: "edit", row })}
              onOperation={(kind, row) => setOperation({ kind, row })}
            />
          )}
          {operation?.kind === "edit" && (
            <EscortEditor
              row={operation.row}
              onClose={() => setOperation(null)}
              onSaved={list.reload}
            />
          )}
          {operation &&
            ["dispose", "deposit-refund", "verify"].includes(
              operation.kind
            ) && (
              <EscortMutation
                kind={operation.kind}
                row={operation.row!}
                onClose={() => setOperation(null)}
                onSaved={list.reload}
              />
            )}
          {operation &&
            ["ban", "unban", "ban-history"].includes(operation.kind) && (
              <AccountAction
                resource="escorts"
                kind={operation.kind}
                row={operation.row!}
                onClose={() => setOperation(null)}
                onSaved={list.reload}
              />
            )}
        </div>
      }
    </DataPageState>
  )
}
function EscortDetail({
  id,
  onClose,
  onEdit,
  onOperation,
}: {
  id: number
  onClose: () => void
  onEdit: (row: Row) => void
  onOperation: (kind: string, row: Row) => void
}) {
  const auth = useAdminAuth()
  const result = useLoad(
    async () => (await request({ url: "/escorts/" + id + "/" })).data,
    [id]
  )
  const [mediaFailed, setMediaFailed] = useState(false)
  useLiveReload(result.reload)
  const row = result.data
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="admin-provider-detail-dialog max-h-[94dvh] overflow-y-auto sm:max-w-[1440px]">
        <DialogHeader>
          <DialogTitle>陪玩档案详情</DialogTitle>
          <DialogDescription>核对接单资格、资料与资金快照。</DialogDescription>
        </DialogHeader>
            <div className="space-y-4">
              <section className="admin-provider-detail-hero rounded-xl border border-[#10285b] bg-[#10285b] p-6 text-white">
                <DataRegion label="陪玩档案摘要" loading={result.loading} hasSnapshot={!!row} error={result.error} retry={result.reload}>
                {row && <div className="grid items-center gap-5 xl:grid-cols-[minmax(0,1fr)_auto]">
                <div className="flex min-w-0 items-center gap-4">
                  {row.avatar_url ? (
                    <img
                      src={row.avatar_url}
                      alt=""
                      width={80}
                      height={80}
                      className="size-20 shrink-0 rounded-2xl object-cover"
                    />
                  ) : (
                    <span className="grid size-20 shrink-0 place-items-center rounded-2xl border border-white/15 bg-white/10 text-2xl font-semibold">
                      {String(row.display_name || row.username).slice(0, 1)}
                    </span>
                  )}
                  <div className="min-w-0">
                    <h2 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
                      {row.display_name || row.username}
                      <Status
                        value={row.status}
                        label={providerStatusText(row.status)}
                      />
                      <ProviderVerification value={row.is_verified} />
                    </h2>
                    <p className="mt-2 text-sm text-blue-100">
                      {row.escort_no} · {row.username}
                    </p>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">
                      {row.bio || "未填写个人介绍"}
                    </p>
                  </div>
                </div>
                <dl className="grid grid-cols-3 gap-5">
                  {[
                    [row.rating_avg ?? "—", "综合评分"],
                    [row.completed_order_count ?? "—", "完成订单"],
                    [
                      row.price_per_hour == null ? "—" : amountToXaCoin(row.price_per_hour),
                      coinText("参考时价 · 浪花"),
                    ],
                  ].map(([value, label]) => (
                    <div key={label} className="border-l border-white/15 pl-4">
                      <dt className="text-xs text-blue-100">{label}</dt>
                      <dd className="mt-2 text-2xl font-semibold tabular-nums">
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
                </div>}
                </DataRegion>
              </section>
              <div className="flex flex-wrap gap-2">
                <RefreshButton
                  loading={result.loading}
                  reload={() => {
                    setMediaFailed(false)
                    void result.reload()
                  }}
                />
                {row && auth.hasPerm("escort:edit") && (
                  <Button
                    disabled={result.loading || !!result.error}
                    onClick={() => onEdit(row)}
                  >
                    编辑资料
                  </Button>
                )}
              </div>
              {row && !row.is_verified && (
                <div
                  role="status"
                  className="rounded-xl border border-warning/35 bg-warning/5 p-4 text-sm"
                >
                  该陪玩尚未认证，请核对资料与接单资格后处理。
                </div>
              )}
              <div className="admin-provider-detail-grid">
                <div className="space-y-4">
                  <FormSection
                    title="基础资料"
                    note="账号、展示资料与私密素材归档状态"
                  >
                    <DataRegion label="基础资料" loading={result.loading} hasSnapshot={!!row}>
                    <Details
                      data={row || {}}
                      fields={[
                        ["phone", "联系手机"],
                        ["gender_display", "性别"],
                        ["city", "城市"],
                        ["service_area", "服务区域"],
                        ["level_name", "陪玩等级"],
                        ["rank_tier", "游戏段位"],
                        ["win_rate", "胜率（%）"],
                        ["created_at", "入驻时间"],
                      ]}
                    />
                    </DataRegion>
                  </FormSection>
                  <FormSection
                    title="接单资格"
                    note={Array.isArray(row?.qualifications) && row.qualifications.every((item: Row) => typeof item.is_active === "boolean") ? `${row.qualifications.filter((item: Row) => item.is_active).length} 项启用 · 同范围可接低档，不同范围互不授权` : "同范围可接低档，不同范围互不授权"}
                  >
                    <DataRegion label="接单资格" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <div className="admin-provider-qualifications">
                      {(row.qualifications || []).map(
                        (item: Row, index: number) => (
                          <section key={item.id || index}>
                            <div>
                              <strong>
                                <Gamepad2 size={16} />
                                {item.game_category_name}
                              </strong>
                              <Status
                                value={item.is_active}
                                label={item.is_active == null ? undefined : item.is_active ? "已启用" : "已停用"}
                                tone={item.is_active ? "success" : "muted"}
                              />
                              <span>
                                {item.price_per_hour === undefined ? "—" : item.price_per_hour === null
                                  ? "未设参考时价"
                                  : `${amountToXaCoin(item.price_per_hour)} ${coinUnit()} / 小时`}
                              </span>
                            </div>
                            <dl>
                              {[
                                [
                                  "服务方向",
                                  qualificationText(item.provider_kind),
                                ],
                                ["平台", qualificationText(item.platform)],
                                [
                                  "难度",
                                  qualificationText(item.security_level),
                                ],
                                ["最高档位", item.grade_name || item.grade],
                              ].map(([label, value]) => (
                                <div key={label}>
                                  <dt>{label}</dt>
                                  <dd>{value}</dd>
                                </div>
                              ))}
                            </dl>
                          </section>
                        )
                      )}
                    </div>
                    {!Array.isArray(row.qualifications) ? <Status readState="unread" /> : !row.qualifications.length && (
                      <div className="admin-record-empty">
                        <Gamepad2 size={24} />
                        <h3>尚未配置接单资格</h3>
                        <p>请在编辑资料中选择游戏、服务范围和最高档位。</p>
                      </div>
                    )}
                    <div className="mt-4">
                      <RecordNotice title="参考时价用于展示和排序">
                        订单价格以服务商品为准，派单时仍会重新核验资格。
                      </RecordNotice>
                    </div>
                    </>}
                    </DataRegion>
                  </FormSection>
                  <FormSection
                    title="服务技能"
                    note={row ? `${row.game_category_names?.length ?? "—"} 款可接游戏 · ${row.service_item_names?.length ?? "—"} 项服务技能` : "核对可接游戏与服务技能"}
                  >
                    <DataRegion label="服务技能" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <div className="admin-provider-game-tags">
                      {(row.game_category_names || []).map((name: string) => (
                        <span key={name}>{name}</span>
                      ))}
                      {!Array.isArray(row.game_category_names) ? <Status readState="unread" /> : !row.game_category_names.length && <Status value="UNCONFIGURED" label="尚未配置可接游戏" />}
                    </div>
                    <div className="admin-provider-service-skills">
                      {!Array.isArray(row.service_item_names) && <Status readState="unread" />}
                      {(row.service_item_names || []).map((name: string) => (
                        <div key={name}>
                          <CheckCircle2 size={16} />
                          {name}
                        </div>
                      ))}
                    </div>
                    </>}
                    </DataRegion>
                  </FormSection>
                  <FormSection
                    title="操作记录"
                    note="资料、认证与资金操作分别留痕"
                  >
                    <p className="text-sm text-muted-foreground">
                      完整操作记录可在审计日志中核对。
                    </p>
                    {auth.hasPerm("audit:view") && (
                      <Button asChild variant="outline" className="mt-4">
                        <Link to="/audit-logs">查看审计日志</Link>
                      </Button>
                    )}
                  </FormSection>
                </div>
                <aside className="admin-provider-detail-aside space-y-4">
                  <FormSection title="档案操作" note="敏感操作均需复核">
                    <DataRegion label="档案操作" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <div className="admin-provider-detail-actions">
                      {auth.hasPerm("escort:edit") && (
                        <Button
                          className="h-11"
                          disabled={result.loading || !!result.error}
                          onClick={() => onEdit(row)}
                        >
                          编辑陪玩资料
                        </Button>
                      )}
                      {auth.hasPerm("escort:verify") && (
                        <Button
                          variant="outline"
                          className="h-11"
                          disabled={result.loading || !!result.error}
                          onClick={() => onOperation("verify", row)}
                        >
                          <BadgeCheck size={16} />
                          {row.is_verified ? "取消认证" : "认证陪玩"}
                        </Button>
                      )}
                      {auth.hasPerm("escort:dispose") && (
                        <Button
                          variant="outline"
                          className="h-11"
                          disabled={result.loading || !!result.error}
                          onClick={() => onOperation("dispose", row)}
                        >
                          <Medal size={16} />
                          奖励 / 罚款
                        </Button>
                      )}
                      {auth.hasPerm("escort:deposit_refund") && (
                        <Button
                          variant="outline"
                          className="h-11"
                          disabled={
                            result.loading ||
                            !!result.error ||
                            row.deposit_paid <= 0
                          }
                          onClick={() => onOperation("deposit-refund", row)}
                        >
                          <WalletCards size={16} />
                          {row.deposit_paid > 0 ? "退还押金" : "暂无可退押金"}
                        </Button>
                      )}
                      {auth.hasPerm("escort:deposit_topup") && row.deposit_mode === "CASH" && (
                        <Button variant="outline" className="h-11" disabled={result.loading || !!result.error}
                          onClick={() => onOperation("deposit-topup", row)}>
                          <Banknote size={16} />线下押金补差
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        className="h-11"
                        onClick={() => onOperation("ban-history", row)}
                      >
                        <ShieldAlert size={16} />
                        封禁记录
                      </Button>
                      {auth.hasPerm("user:ban") && (
                        <div className="grid grid-cols-2 gap-2">
                          <Button
                            variant="outline"
                            disabled={result.loading || !!result.error}
                            onClick={() => onOperation("ban", row)}
                          >
                            封禁账号
                          </Button>
                          <Button
                            variant="outline"
                            disabled={result.loading || !!result.error}
                            onClick={() => onOperation("unban", row)}
                          >
                            解除封禁
                          </Button>
                        </div>
                      )}
                    </div>
                    </>}
                    </DataRegion>
                  </FormSection>
                  <FormSection title="押金与通行证">
                    <DataRegion label="押金与通行证" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <dl className="space-y-4 text-sm">
                      {[
                        [
                          "已缴押金",
                          row.deposit_paid == null ? "—" : depositCoins(row.deposit_paid) + coinText(" 浪花"),
                        ],
                        [
                          "应缴押金",
                          (row.effective_deposit_required ?? row.deposit_required) == null ? "—" : depositCoins(
                            row.effective_deposit_required ??
                              row.deposit_required
                          ) + coinText(" 浪花"),
                        ],
                        [
                          "押金方式",
                          row.deposit_mode === "CASH" ? "现金押金" : row.deposit_mode === "EARNINGS" ? "单抵押金" : "—",
                        ],
                        ["通行证", row.pass_tier_display || (row.active_pass_tier === null ? "无通行证" : "—")],
                        ["通行证到期", formatDateTime(row.pass_expires_at)],
                      ].map(([label, value]) => (
                        <div key={label}>
                          <dt className="text-xs text-muted-foreground">
                            {label}
                          </dt>
                          <dd className="mt-1 font-medium tabular-nums">
                            {value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                    </>}
                    </DataRegion>
                  </FormSection>
                  <FormSection title="业绩与奖惩">
                    <DataRegion label="业绩与奖惩" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <div className="admin-provider-performance">
                      <div>
                        <strong>{row.rating_avg ?? "—"}</strong>
                        <p>综合评分</p>
                      </div>
                      <div>
                        <strong>{row.completed_order_count ?? "—"}</strong>
                        <p>完成订单</p>
                      </div>
                    </div>
                    <dl className="admin-record-definition">
                      <div>
                        <dt>累计奖励</dt>
                        <dd className="text-success">
                          <DataValue known={row.total_reward != null}>+{amountToXaCoin(row.total_reward)} {coinUnit()}</DataValue>
                        </dd>
                      </div>
                      <div>
                        <dt>累计罚款</dt>
                        <dd className="text-destructive">
                          <DataValue known={row.total_penalty != null}>−{amountToXaCoin(row.total_penalty)} {coinUnit()}</DataValue>
                        </dd>
                      </div>
                    </dl>
                    </>}
                    </DataRegion>
                  </FormSection>
                  <FormSection title="介绍与资质材料">
                    <DataRegion label="介绍与资质材料" loading={result.loading} hasSnapshot={!!row}>
                    {row && <>
                    <PrivateImage
                      label="防作弊凭证"
                      urls={[row.cheat_proof_url]}
                    />
                    {row.intro_video_url && (
                      <div className="mt-4">
                        {mediaFailed ? (
                          <p className="text-sm text-destructive">
                            视频暂不可读取，请刷新档案后重试。
                          </p>
                        ) : (
                          <video
                            controls
                            preload="metadata"
                            src={row.intro_video_url}
                            onError={() => setMediaFailed(true)}
                            className="aspect-video w-full rounded-xl bg-muted"
                          />
                        )}
                      </div>
                    )}
                    </>}
                    </DataRegion>
                  </FormSection>
                </aside>
              </div>
            </div>
      </DialogContent>
    </Dialog>
  )
}

function EscortEditor({
  row,
  onClose,
  onSaved,
}: {
  row?: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    username: "",
    password: "",
    display_name: "",
    gender: "UNKNOWN",
    phone: "",
    city: "",
    service_area: "",
    price_per_hour: 0,
    rank_tier: "",
    win_rate: 0,
    level: null,
    deposit_mode: "CASH",
    deposit_required: 0,
    status: "AVAILABLE",
    bio: "",
    game_categories: [],
    service_items: [],
    qualifications: [],
    ...row,
    price_per_hour_input: row ? Number(amountToXaCoin(row.price_per_hour)) : 0,
  })
  const [files, setFiles] = useState<Record<string, File>>({})
  function set(key: string, value: any) {
    setForm((old) => ({ ...old, [key]: value }))
  }
  return (
    <MutationDialog
      confirmDiscard
      contentClassName="admin-provider-editor-dialog sm:max-w-[1120px]"
      title={row ? "编辑陪玩档案" : "新增陪玩账号"}
      description="接单资格、可接游戏及服务共同决定可承接范围。已缴押金不能在资料中直接修改。"
      onClose={onClose}
      onSubmit={async () => {
        if (
          !form.display_name?.trim() ||
          (!row && (!form.username?.trim() || !form.password))
        )
          throw new Error("请填写结单昵称，新建时还须填写账号和密码。")
        if (!row && !/^1[3-9]\d{9}$/.test(String(form.phone || "").trim()))
          throw new Error("新建陪玩账号须填写可接收短信的手机号。")
        const payload: Row = row
          ? {
              display_name: form.display_name,
              gender: form.gender,
              city: form.city,
              service_area: form.service_area,
              price_per_hour: xaCoinToAmount(form.price_per_hour_input),
              rank_tier: form.rank_tier,
              win_rate: form.win_rate,
              level: form.level || null,
              status: form.status,
              bio: form.bio,
              recommendation_slot: Number(form.recommendation_slot) || null,
              qualifications: form.qualifications,
              game_categories: form.game_categories,
              service_items: form.service_items,
              ...(form.deposit_mode !== row.deposit_mode
                ? { deposit_mode: form.deposit_mode }
                : {}),
            }
          : {
              username: form.username.trim(),
              password: form.password,
              display_name: form.display_name.trim(),
              gender: form.gender,
              phone: form.phone,
              city: form.city,
              level: form.level || null,
              deposit_mode: form.deposit_mode,
              qualifications: form.qualifications,
              game_categories: form.game_categories,
              service_items: form.service_items,
            }
        let data: Row | FormData = payload
        if (Object.keys(files).length || !row) {
          const body = new FormData()
          for (const [key, value] of Object.entries(payload)) {
            if (value == null) continue
            if (key === "qualifications")
              body.append(key, JSON.stringify(value))
            else if (Array.isArray(value)) {
              if (!value.length) body.append(key, "[]")
              else value.forEach((item) => body.append(key, String(item)))
            } else body.append(key, String(value))
          }
          Object.entries(files).forEach(([key, file]) => body.append(key, file))
          data = body
        }
        await request({
          url: `/escorts/${row ? `${row.id}/` : ""}`,
          method: row ? "PATCH" : "POST",
          data,
        })
        toast.success("陪玩资料已保存")
        onSaved()
      }}
    >
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <FormSection title="档案、接单资格与资料">
          <div className="grid grid-cols-2 gap-4">
            {!row && (
              <>
                <Field
                  field={{ key: "username", label: "登录账号", required: true }}
                  value={form.username}
                  onChange={(value) => set("username", value)}
                />
                <Field
                  field={{
                    key: "password",
                    label: "初始密码",
                    type: "password",
                    required: true,
                  }}
                  value={form.password}
                  onChange={(value) => set("password", value)}
                />
              </>
            )}
            <Field
              field={{ key: "display_name", label: "结单昵称", required: true }}
              value={form.display_name}
              onChange={(value) => set("display_name", value)}
            />
            <Field
              field={{
                key: "gender",
                label: "性别",
                type: "select",
                options: [
                  ["UNKNOWN", "未知"],
                  ["MALE", "男陪"],
                  ["FEMALE", "女陪"],
                ],
              }}
              value={form.gender}
              onChange={(value) => set("gender", value)}
            />
            {!row && (
              <Field
                field={{ key: "phone", label: "手机号（短信验证）", required: true }}
                value={form.phone}
                onChange={(value) => set("phone", value)}
              />
            )}
            <Field
              field={{ key: "city", label: "城市" }}
              value={form.city}
              onChange={(value) => set("city", value)}
            />
            <OptionPicker
              resource="escort-levels"
              label="陪玩等级"
              value={form.level}
              onChange={(value) => set("level", value)}
            />
            <Field
              field={{
                key: "deposit_mode",
                label: "押金模式",
                type: "select",
                options: [
                  ["CASH", "现金押金"],
                  ["EARNINGS", "单抵押金"],
                ],
              }}
              value={form.deposit_mode}
              onChange={(value) => set("deposit_mode", value)}
            />
            {row ? (
              <>
                <Field
                  field={{
                    key: "recommendation-slot",
                    label: "下单页推荐位",
                    type: "select",
                    options: [
                      ["", "不推荐"],
                      [1, "推荐位 1"],
                      [2, "推荐位 2"],
                      [3, "推荐位 3"],
                      [4, "推荐位 4"],
                    ],
                    hint: "每个位置只能配置一位陪玩，替换前先清空原位置。",
                  }}
                  value={form.recommendation_slot}
                  onChange={(value) => set("recommendation_slot", value)}
                />
                <Field
                  field={{
                    key: "status",
                    label: "接单状态",
                    type: "select",
                    options: [
                      ["AVAILABLE", "空闲"],
                      ["BUSY", "忙碌"],
                      ["OFFLINE", "离线"],
                    ],
                  }}
                  value={form.status}
                  onChange={(value) => set("status", value)}
                />
                <Field
                  field={{
                    key: "price_per_hour_input",
                    label: coinText("参考时价（浪花）"),
                    type: "number",
                    min: 0,
                    step: 0.1,
                  }}
                  value={form.price_per_hour_input}
                  onChange={(value) => set("price_per_hour_input", value)}
                />
                <Field
                  field={{ key: "rank_tier", label: "游戏段位" }}
                  value={form.rank_tier}
                  onChange={(value) => set("rank_tier", value)}
                />
                <Field
                  field={{
                    key: "win_rate",
                    label: "胜率（%）",
                    type: "number",
                    min: 0,
                    max: 100,
                  }}
                  value={form.win_rate}
                  onChange={(value) => set("win_rate", value)}
                />
                <Field
                  field={{ key: "service_area", label: "服务区域" }}
                  value={form.service_area}
                  onChange={(value) => set("service_area", value)}
                />
              </>
            ) : null}
            <div className="col-span-2">
              <OptionPicker
                resource="game-categories"
                label="可接游戏"
                multiple
                value={form.game_categories}
                onChange={(value) => set("game_categories", value)}
              />
            </div>
            <div className="col-span-2">
              <OptionPicker
                resource="service-items"
                label="可接服务"
                multiple
                value={form.service_items}
                onChange={(value) => set("service_items", value)}
              />
            </div>
            <div className="col-span-2">
              <QualificationsEditor
                value={form.qualifications}
                onChange={(value) => set("qualifications", value)}
              />
            </div>
            {[
              ["avatar", "形象头像"],
              ["intro_video", "介绍视频"],
              ["cheat_proof", "防作弊凭证"],
            ].map(([key, label]) => (
              <div key={key} className="space-y-2">
                <Label htmlFor={`escort-file-${key}`}>{label}</Label>
                <Input
                  id={`escort-file-${key}`}
                  type="file"
                  accept={key === "intro_video" ? "video/*" : "image/*"}
                  onChange={(event) => {
                    if (event.target.files?.[0])
                      setFiles((old) => ({
                        ...old,
                        [key]: event.target.files![0],
                      }))
                  }}
                />
              </div>
            ))}
            {row && (
              <div className="col-span-2">
                <Field
                  field={{ key: "bio", label: "个人介绍", type: "textarea" }}
                  value={form.bio}
                  onChange={(value) => set("bio", value)}
                />
              </div>
            )}
          </div>
        </FormSection>
        <DraftPreview
          title={form.display_name}
          subtitle={form.bio}
          image={row?.avatar_url}
          fields={[
            ["登录账号", form.username || "—"],
            [
              "接单状态",
              { AVAILABLE: "空闲", BUSY: "忙碌", OFFLINE: "离线" }[
                form.status as "AVAILABLE"
              ] || "未设置",
            ],
            [
              "押金方式",
              form.deposit_mode === "CASH" ? "现金押金" : "单抵押金",
            ],
            [
              "参考时价",
              String(form.price_per_hour_input || 0) + coinText(" 浪花"),
            ],
          ]}
        />
      </div>
    </MutationDialog>
  )
}
function QualificationsEditor({
  value,
  onChange,
}: {
  value: Row[]
  onChange: (value: Row[]) => void
}) {
  const options = useLoad(
    async () => (await request({ url: "/qualification-options/" })).data
  )
  const config = options.data
  function change(index: number, patch: Row) {
    onChange(value.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <Label>按游戏和服务范围配置最高档位</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!config || value.length >= 80}
          onClick={() => {
            const scope = config?.scopes?.[0]
            if (scope)
              onChange([
                ...value,
                {
                  game_category: null,
                  provider_kind: scope.provider_kind,
                  platform: scope.platform,
                  security_level: scope.security_level,
                  grade: scope.grades?.[0]?.value || "",
                  is_active: true,
                  price_per_hour: null,
                },
              ])
          }}
        >
          新增资格
        </Button>
      </div>
      <Feedback message={options.error} retry={options.reload} />
      {value.map((row, index) => {
        const securities = [
          ...new Set<string>(
            (config?.scopes || [])
              .filter(
                (scope: Row) =>
                  scope.provider_kind === row.provider_kind &&
                  scope.platform === row.platform
              )
              .map((scope: Row) => scope.security_level)
          ),
        ]
        const grades =
          config?.scopes?.find(
            (scope: Row) =>
              scope.provider_kind === row.provider_kind &&
              scope.platform === row.platform &&
              scope.security_level === row.security_level
          )?.grades || []
        return (
          <div key={index} className="space-y-3 rounded-lg border p-3">
            <div className="flex items-center justify-between text-sm">
              <strong>资格 {index + 1}</strong>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                移除
              </Button>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <OptionPicker
                resource="game-categories"
                label="游戏"
                value={row.game_category}
                onChange={(selected) =>
                  change(index, { game_category: selected })
                }
              />
              <Field
                field={{
                  key: `qualification-kind-${index}`,
                  label: "服务方向",
                  type: "select",
                  options: (config?.kinds || []).map((item: Row) => [
                    item.value,
                    item.label,
                  ]),
                }}
                value={row.provider_kind}
                onChange={(selected) =>
                  change(index, {
                    provider_kind: selected,
                    security_level: "",
                    grade: "",
                  })
                }
              />
              <Field
                field={{
                  key: `qualification-platform-${index}`,
                  label: "平台",
                  type: "select",
                  options: (config?.platforms || []).map((item: Row) => [
                    item.value,
                    item.label,
                  ]),
                }}
                value={row.platform}
                onChange={(selected) =>
                  change(index, {
                    platform: selected,
                    security_level: "",
                    grade: "",
                  })
                }
              />
              <Field
                field={{
                  key: `qualification-security-${index}`,
                  label: "难度",
                  type: "select",
                  options: securities.map((item) => [
                    item,
                    (
                      {
                        ANY: "不限难度",
                        CONFIDENTIAL: "机密",
                        TOP_SECRET: "绝密",
                      } as Row
                    )[item] || item,
                  ]),
                }}
                value={row.security_level}
                onChange={(selected) =>
                  change(index, { security_level: selected, grade: "" })
                }
              />
              <Field
                field={{
                  key: `qualification-grade-${index}`,
                  label: "最高档位",
                  type: "select",
                  options: grades.map((item: Row) => [item.value, item.label]),
                }}
                value={row.grade}
                onChange={(selected) => change(index, { grade: selected })}
              />
              <Field
                field={{
                  key: `qualification-active-${index}`,
                  label: "启用",
                  type: "boolean",
                }}
                value={row.is_active}
                onChange={(selected) => change(index, { is_active: selected })}
              />
            </div>
          </div>
        )
      })}
      <p className="text-xs leading-5 text-muted-foreground">
        同一游戏、方向、平台和难度只配置一个最高档位。范围变更后，请重新选择有效难度与档位。
      </p>
    </section>
  )
}
function CashDepositTopup({ row, onClose, onSaved }: { row: Row; onClose: () => void; onSaved: () => void }) {
  const [amount, setAmount] = useState("")
  const [reason, setReason] = useState("")
  const [reference, setReference] = useState("")
  const [proof, setProof] = useState<File | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [intent] = useState(() => requestId("cash-deposit"))
  const records = useLoad(async () => (await request({ url: `/escorts/${row.id}/deposit-topups/` })).data)
  return <MutationDialog title="登记线下现金押金补差" onClose={onClose} disabled={!confirmed || unknown || !amount || !reason.trim() || (!reference.trim() && !proof)}
    description={`${row.display_name} · 当前已缴 ¥${formatRmb(row.deposit_paid)}，目标 ¥${formatRmb(row.effective_deposit_required ?? row.deposit_required)}。请先核验实际收款。`}
    onSubmit={async () => {
      const raw = parseRmbFen(amount)
      if (!raw) throw new Error("请输入大于零的人民币金额，最多两位小数。")
      const data = new FormData()
      data.append("amount", String(raw))
      data.append("reason", reason.trim())
      data.append("reference", reference.trim())
      data.append("client_request_id", intent)
      if (proof) data.append("proof_image", proof)
      try { await request({ url: `/escorts/${row.id}/deposit-topups/`, method: "POST", data }) }
      catch (cause: any) {
        if (!cause.status || cause.status >= 500 || cause.status === 408) setUnknown(true)
        throw cause
      }
      toast.success("押金补差已登记")
      onSaved()
    }}>
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2"><Label htmlFor="cash-deposit-amount">实收金额（人民币元）</Label><Input id="cash-deposit-amount" type="number" min="0.01" step="0.01" value={amount} onChange={e => { setAmount(e.target.value); setConfirmed(false) }} /></div>
      <div className="space-y-2"><Label htmlFor="cash-deposit-reference">收款凭据编号</Label><Input id="cash-deposit-reference" value={reference} onChange={e => { setReference(e.target.value); setConfirmed(false) }} /></div>
    </div>
    <div className="space-y-2"><Label htmlFor="cash-deposit-proof">凭证图片（与编号至少提供一项）</Label><Input id="cash-deposit-proof" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => { setProof(e.target.files?.[0] || null); setConfirmed(false) }} /></div>
    <div className="space-y-2"><Label htmlFor="cash-deposit-reason">登记原因</Label><Textarea id="cash-deposit-reason" value={reason} onChange={e => { setReason(e.target.value); setConfirmed(false) }} /></div>
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />已核验本店陪玩身份、线下收款金额及凭据。</label>
    {unknown && <Feedback message="登记结果未确认，请关闭后重新查看补差记录，避免重复登记。" />}
    <div className="space-y-2"><h3 className="font-semibold">最近现金押金登记</h3>
      <Feedback message={records.error} retry={records.reload} />
      {records.loading && <p className="text-sm">正在读取记录…</p>}
      {records.data?.map((item: Row) => <div key={item.id} className="rounded-md border p-3 text-sm">
        <p>{item.kind_label} · ¥{formatRmb(item.amount)} · {formatDateTime(item.created_at)} · {item.operator_name}</p>
        <p>{item.reason}{item.reference ? ` · 凭据 ${item.reference}` : ""}</p>
        {item.proof_image_url && <PrivateImage urls={[item.proof_image_url]} label="现金押金补差凭证" />}
        {item.registration_id && <p className="text-xs text-muted-foreground">入驻凭证保存在注册审核记录 #{item.registration_id}。</p>}
      </div>)}
      {records.data && !records.data.length && <p className="text-sm text-muted-foreground">暂无现金押金登记</p>}
    </div>
  </MutationDialog>
}

function EscortMutation({
  kind,
  row,
  onClose,
  onSaved,
}: {
  kind: string
  row: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    amount: "",
    reason: "",
    dispose_type: "REWARD",
  })
  const [confirmed, setConfirmed] = useState(false)
  const [intent] = useState(() => requestId(kind))
  const eligibility = useLoad<{ can_refund: boolean; reasons: string[] } | null>(
    async () => kind === "deposit-refund" ? (await request({ url: `/escorts/${row.id}/deposit-refund/` })).data : null,
    [kind, row.id]
  )
  const title =
    kind === "verify"
      ? row.is_verified
        ? "取消认证"
        : "认证陪玩"
      : kind === "dispose"
        ? "奖励与处罚"
        : "退还押金"
  return (
    <MutationDialog
      title={title}
      description={`${row.display_name} · ${row.escort_no} · ${row.username}`}
      onClose={onClose}
      disabled={
        kind !== "verify" && (!confirmed || !form.reason.trim() || !form.amount ||
          (kind === "deposit-refund" && (eligibility.loading || !!eligibility.error || !eligibility.data?.can_refund)))
      }
      onSubmit={async () => {
        if (kind === "verify")
          await request({
            url: `/escorts/${row.id}/verify/`,
            method: "POST",
            data: { is_verified: !row.is_verified },
          })
        else {
          const amount = xaCoinToAmount(form.amount)
          if (
            amount <= 0 ||
            (kind === "deposit-refund" && amount > row.deposit_paid)
          )
            throw new Error("请检查金额，退还不得超过已缴押金。")
          await reviewedFundRequest<Row>(
            `/admin/escorts/${row.id}/${kind}/`,
            {
              amount,
              reason: form.reason.trim(),
              client_request_id: intent,
              ...(kind === "dispose"
                ? { dispose_type: form.dispose_type }
                : {}),
            }
          )
        }
        toast.success("已完成处理")
        onSaved()
      }}
    >
      {kind !== "verify" && (
        <>
          {kind === "deposit-refund" && <div className="space-y-2 rounded-md border p-3 text-sm">
            {eligibility.loading && <p>正在核对订单、售后和收益…</p>}
            <Feedback message={eligibility.error} retry={eligibility.reload} />
            {eligibility.data && (eligibility.data.can_refund
              ? <p>当前没有发现阻止退押金的订单、售后或收益冻结。提交时将再次核对。</p>
              : <><p>暂不能退还押金：</p><ul className="list-disc pl-5">{eligibility.data.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul><Button type="button" variant="outline" size="sm" onClick={() => void eligibility.reload()}>重新核对</Button></>)}
          </div>}
          {kind === "dispose" && (
            <Field
              field={{
                key: "dispose_type",
                label: "类型",
                type: "select",
                options: [
                  ["REWARD", "奖励"],
                  ["PENALTY", "处罚"],
                ],
              }}
              value={form.dispose_type}
              onChange={(value) =>
                setForm((old) => ({ ...old, dispose_type: value }))
              }
            />
          )}
          <Field
            field={{
              key: "escort-amount",
              label: coinText("金额（浪花）"),
              type: "number",
              min: 0.1,
              step: 0.1,
            }}
            value={form.amount}
            onChange={(value) => {
              setForm((old) => ({ ...old, amount: value }))
              setConfirmed(false)
            }}
          />
          <Field
            field={{
              key: "escort-reason",
              label: "原因",
              type: "textarea",
              required: true,
            }}
            value={form.reason}
            onChange={(value) => {
              setForm((old) => ({ ...old, reason: value }))
              setConfirmed(false)
            }}
          />
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-blue-600"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            已核对陪玩身份、金额及处理原因。
            {kind === "deposit-refund"
              ? "押金退回陪玩钱包。"
              : "奖惩将改变陪玩钱包余额。"}
          </label>
        </>
      )}
    </MutationDialog>
  )
}

export function RegistrationsPage() {
  const [query, setQuery] = useState<Row>({ status: "PENDING", search: "" })
  const [searchDraft, setSearchDraft] = useState("")
  const list = useList("provider-registrations", query)
  const [id, setId] = useState<number | null>(null)
  const current = useLoad(
    async () =>
      id
        ? (await request({ url: `/provider-registrations/${id}/` })).data
        : null,
    [id]
  )
  const [action, setAction] = useState<"approve" | "reject" | null>(null)
  const [review, setReview] = useState<Row>({
    level_id: null,
    identity_confirmed: false,
    deposit_confirmed: false,
    deposit_yuan: 0,
    review_note: "",
  })
  useEffect(() => {
    if (current.data)
      setReview({
        level_id: current.data.level || null,
        identity_confirmed: false,
        deposit_confirmed: false,
        deposit_yuan: current.data.deposit_required / 100,
        review_note: current.data.review_note || "",
      })
  }, [current.data?.id])
  const auth = useAdminAuth()
  useLiveReload(list.reload)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="registrations"
    >
      {
        <div className="admin-registrations-page space-y-4">
          <PageHeader
            tone="feature"
            tag="注册审核"
            metrics={[
              {
                label: query.status === "PENDING" ? "等待审核" : "符合筛选",
                value: list.total,
              },
              {
                label: "本页现金待核验",
                value: list.rows.filter(
                  (row) =>
                    row.status === "PENDING" && row.deposit_mode === "CASH"
                ).length,
              },
              {
                label: "本页待补充资料",
                value: list.rows.filter((row) => row.status === "REJECTED")
                  .length,
              },
            ]}
            title="注册审核"
            description="核对实名材料与押金，审核通过后开通业务账号。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </PageHeader>
          <RecordNotice title="私密材料仅限授权审核">
            请核对证件正反面、姓名与号码；现金押金还须核实实际到账，不能仅凭图片通过。
          </RecordNotice>
          <Feedback message={list.error} retry={list.reload} />
          <DataTable snapshotAvailable={!!list.data}
            toolbar={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  setQuery((old) => ({ ...old, search: searchDraft.trim() }))
                }}
              >
                <AdminFilters primary={<>
                <Field
                  field={{
                    key: "registration-search",
                    label: "账号 / 姓名 / 手机号",
                  }}
                  value={searchDraft}
                  onChange={setSearchDraft}
                />
                <Field
                  field={{
                    key: "registration-status",
                    label: "审核状态",
                    type: "select",
                    options: [
                      ["PENDING", "待审核"],
                      ["APPROVED", "已通过"],
                      ["REJECTED", "已退回"],
                    ],
                  }}
                  value={query.status}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, status: value }))
                  }
                />
                </>} actions={<>
                <Button type="submit" className="h-11">
                  查询申请
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-11"
                  onClick={() => {
                    setSearchDraft("")
                    setQuery({ status: "PENDING", search: "" })
                  }}
                >
                  重置
                </Button>
                </>} />
              </form>
            }
            title="注册申请列表"
            description={`本页 ${list.rows.length} 人`}
            empty="没有匹配的申请"
            emptyDescription="调整关键词或切换审核状态后再查看。"
            emptyAction={
              <Button
                variant="outline"
                onClick={() => {
                  setSearchDraft("")
                  setQuery({ status: "PENDING", search: "" })
                }}
              >
                返回待审核
              </Button>
            }
            minWidth={1120}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "display_name",
                label: "申请 / 账号",
                render: (row) => (
                  <div className="admin-registration-identity">
                    <span>
                      {String(row.display_name || row.username).slice(0, 1)}
                    </span>
                    <div>
                      <button type="button" onClick={() => setId(row.id)}>
                        {row.display_name}
                      </button>
                      <p>
                        #{row.id} · {row.username} · v{row.revision}
                      </p>
                    </div>
                  </div>
                ),
              },
              {
                key: "real_name",
                label: "申报身份",
                render: (row) => (
                  <>
                    <p>{row.real_name}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {row.phone}
                    </p>
                    <p className="mt-1 font-mono text-xs text-muted-foreground">
                      {row.id_number_masked}
                    </p>
                  </>
                ),
              },
              {
                key: "deposit_mode",
                label: "押金方式",
                render: (row) => (
                  <>
                    <p className="flex items-center gap-2">
                      <Banknote size={16} />
                      {row.deposit_mode === "CASH" ? "现金押金" : "单抵押金"}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      应缴{" "}
                      {row.deposit_mode === "CASH"
                        ? `${formatRmb(row.deposit_required)} 元`
                        : `${depositCoins(row.deposit_required)} ${coinUnit()}`}
                    </p>
                    {row.status === "APPROVED" && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {row.deposit_mode === "CASH"
                          ? `登记现金 ${formatRmb(row.deposit_paid)} 元`
                          : "不登记现金押金"}
                      </p>
                    )}
                  </>
                ),
              },
              {
                key: "materials",
                label: "审核材料",
                render: (row) => {
                  const count = [
                    row.id_front_url,
                    row.id_back_url,
                    row.deposit_mode === "CASH" && row.deposit_proof_url,
                  ].filter(Boolean).length
                  const required = row.deposit_mode === "CASH" ? 3 : 2
                  return (
                    <div className="admin-registration-material-count">
                      <FileCheck2 size={18} />
                      <div>
                        <p>
                          {count} / {required} 份附件
                        </p>
                        <small>进入详情读取并人工核对</small>
                      </div>
                    </div>
                  )
                },
              },
              {
                key: "status",
                label: "状态 / 提交时间",
                render: (row) => (
                  <>
                    <Status value={row.status} label={row.status_display} />
                    <p className="mt-2 flex items-center gap-1 text-xs whitespace-nowrap text-muted-foreground">
                      <Clock3 size={12} />
                      {formatDateTime(row.submitted_at)}
                    </p>
                  </>
                ),
              },
              {
                key: "actions",
                label: "操作",
                align: "right",
                render: (row) => (
                  <Button
                    size="sm"
                    variant={row.status === "PENDING" ? "default" : "ghost"}
                    onClick={() => setId(row.id)}
                  >
                    {row.status === "PENDING" &&
                    auth.hasPerm("registration:audit")
                      ? "查看并审核"
                      : "查看详情"}
                  </Button>
                ),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          <Dialog
            open={!!id}
            onOpenChange={(value) => {
              if (!value) setId(null)
            }}
          >
            <DialogContent className="admin-registration-detail-dialog max-h-[92dvh] overflow-y-auto sm:max-w-[1440px]">
              <DialogHeader>
                <DialogTitle>注册申请 #{id}</DialogTitle>
                <DialogDescription>
                  实名资料仅供授权审核人员核验。
                </DialogDescription>
              </DialogHeader>
                  <div className="admin-registration-detail-body space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-xs text-muted-foreground">
                        申请 #{id} · <DataValue known={!!current.data} loading={current.loading}>v{current.data?.revision}</DataValue>
                      </span>
                      <RefreshButton
                        loading={current.loading}
                        reload={current.reload}
                      />
                    </div>
                    {current.data && <section className="admin-registration-detail-hero">
                      <div>
                        <span>
                          {String(
                            current.data.display_name || current.data.username
                          ).slice(0, 1)}
                        </span>
                        <div>
                          <h2>
                            {current.data.display_name || current.data.username}
                            <Status
                              value={current.data.status}
                              label={current.data.status_display}
                            />
                          </h2>
                          <p>
                            #{id} · {current.data.username} · v
                            {current.data.revision}
                          </p>
                          <p>
                            {current.data.real_name} · {current.data.phone}
                          </p>
                        </div>
                      </div>
                      <dl>
                        {[
                          [
                            "押金方式",
                            current.data.deposit_mode === "CASH"
                              ? "现金押金"
                              : "单抵押金",
                          ],
                          [
                            "当前应缴",
                            current.data.deposit_mode === "CASH"
                              ? `${formatRmb(current.data.deposit_required)} 元`
                              : `${depositCoins(current.data.deposit_required)} ${coinUnit()}`,
                          ],
                          [
                            "提交时间",
                            formatDateTime(current.data.submitted_at),
                          ],
                        ].map(([label, value]) => (
                          <div key={label}>
                            <dt>{label}</dt>
                            <dd>{value}</dd>
                          </div>
                        ))}
                      </dl>
                    </section>}
                    <ReadOnlyNotice
                      canEdit={auth.hasPerm("registration:audit")}
                      subject="注册申请"
                    />
                    <div className="admin-registration-detail-grid">
                      <div className="space-y-4">
                        <FormSection
                          title="申请信息"
                          note="陪玩账号、结单昵称与实名申报资料"
                        >
                          <DataRegion label="申请信息" loading={current.loading} hasSnapshot={!!current.data} error={current.error} retry={current.reload}>
                          <Details
                            data={current.data || {}}
                            fields={[
                              ["username", "陪玩账号"],
                              ["display_name", "结单昵称"],
                              ["real_name", "真实姓名"],
                              ["id_number", "身份证号"],
                              ["phone", "手机号"],
                              ["level_name", "开通等级"],
                              ["reviewer_name", "审核人"],
                              ["reviewed_at", "审核时间"],
                              ["review_note", "审核意见"],
                            ]}
                          />
                          </DataRegion>
                        </FormSection>
                        <FormSection
                          title="核对材料"
                          note="证件与凭证为私密附件，须逐项读取核对"
                        >
                          <DataRegion label="核对材料" loading={current.loading} hasSnapshot={!!current.data}>
                          {current.data && <div
                            className="admin-registration-materials"
                            data-deposit-mode={current.data.deposit_mode}
                          >
                            {[
                              {
                                label: "身份证正面（人像面）",
                                url: current.data.id_front_url,
                              },
                              {
                                label: "身份证反面（国徽面）",
                                url: current.data.id_back_url,
                              },
                              ...(current.data.deposit_mode === "CASH"
                                ? [
                                    {
                                      label: "押金交付凭证",
                                      url: current.data.deposit_proof_url,
                                    },
                                  ]
                                : []),
                            ].map((item) => (
                              <section key={item.label}>
                                <h3>{item.label}</h3>
                                <PrivateImage
                                  label={item.label}
                                  urls={[item.url]}
                                />
                              </section>
                            ))}
                          </div>}
                          </DataRegion>
                        </FormSection>
                        <FormSection
                          title="申请与审核记录"
                          note="每次重新提交生成新的资料版本"
                        >
                          <DataRegion label="申请与审核记录" loading={current.loading} hasSnapshot={!!current.data}>
                          {current.data && <ol className="admin-record-timeline">
                            {current.data.reviewed_at && (
                              <li>
                                <div>
                                  <strong>
                                    {current.data.status === "APPROVED"
                                      ? "审核通过并开通账号"
                                      : "退回补充资料"}
                                  </strong>
                                  <p>
                                    {current.data.reviewer_name} ·{" "}
                                    {current.data.review_note ||
                                      "未填写审核意见"}
                                  </p>
                                </div>
                                <time>
                                  {formatDateTime(current.data.reviewed_at)}
                                </time>
                              </li>
                            )}
                            <li>
                              <div>
                                <strong>
                                  {current.data.revision > 1
                                    ? "重新提交申请资料"
                                    : "提交入驻申请"}
                                </strong>
                                <p>
                                  资料版本 v{current.data.revision} ·{" "}
                                  {current.data.deposit_mode === "CASH"
                                    ? "现金押金"
                                    : "单抵押金"}
                                </p>
                              </div>
                              <time>
                                {formatDateTime(current.data.submitted_at)}
                              </time>
                            </li>
                          </ol>}
                          </DataRegion>
                        </FormSection>
                      </div>
                      <aside className="admin-registration-review-aside space-y-4">
                          <FormSection
                            title={!current.data || (current.data.status === "PENDING" && auth.hasPerm("registration:audit")) ? "审核处理" : current.data.status === "APPROVED" ? "账号已开通" : current.data.status === "REJECTED" ? "等待补充资料" : "仅查看申请"}
                            note={!current.data || (current.data.status === "PENDING" && auth.hasPerm("registration:audit")) ? "先核对材料，再选择审核结果" : undefined}
                            className={!current.data || (current.data.status === "PENDING" && auth.hasPerm("registration:audit")) ? "admin-registration-review-card" : undefined}
                          >
                            <DataRegion label="审核处理" loading={current.loading} hasSnapshot={!!current.data}>
                            {current.data && (current.data.status === "PENDING" && auth.hasPerm("registration:audit") ? (
                            <fieldset
                              className="space-y-4"
                              disabled={current.loading || !!current.error}
                            >
                              <OptionPicker
                                resource="escort-levels"
                                label="开通后的陪玩等级"
                                value={review.level_id}
                                query={{ is_active: true }}
                                onChange={(value) =>
                                  setReview((old) => ({
                                    ...old,
                                    level_id: value,
                                  }))
                                }
                              />
                              {current.data.deposit_mode === "CASH" && (
                                <Field
                                  field={{
                                    key: "deposit_yuan",
                                    label: "已确认到账的现金押金（元）",
                                    type: "number",
                                    min: current.data.deposit_required / 100,
                                    step: 0.01,
                                  }}
                                  value={review.deposit_yuan}
                                  onChange={(value) =>
                                    setReview((old) => ({
                                      ...old,
                                      deposit_yuan: value,
                                    }))
                                  }
                                />
                              )}
                              <label className="admin-registration-check">
                                <Checkbox name="已核对身份证正反面、姓名与号码"
                                  checked={review.identity_confirmed}
                                  disabled={current.loading || !!current.error}
                                  onCheckedChange={(checked) =>
                                    setReview((old) => ({
                                      ...old,
                                      identity_confirmed: checked === true,
                                    }))
                                  }
                                />
                                已核对身份证正反面、姓名与号码
                              </label>
                              {current.data.deposit_mode === "CASH" && (
                                <label className="admin-registration-check">
                                  <Checkbox name="已核实现金实际到账与交付凭证一致"
                                    checked={review.deposit_confirmed}
                                    disabled={current.loading || !!current.error}
                                    onCheckedChange={(checked) =>
                                      setReview((old) => ({
                                        ...old,
                                        deposit_confirmed: checked === true,
                                      }))
                                    }
                                  />
                                  已核实现金实际到账与交付凭证一致
                                </label>
                              )}
                              <Field
                                field={{
                                  key: "review_note",
                                  label: "审核意见",
                                  type: "textarea",
                                }}
                                value={review.review_note}
                                onChange={(value) =>
                                  setReview((old) => ({
                                    ...old,
                                    review_note: value,
                                  }))
                                }
                              />
                              <RecordNotice
                                title={
                                  current.data.deposit_mode === "CASH"
                                    ? "现金到账须人工核实"
                                    : "单抵不登记现金"
                                }
                              >
                                {current.data.deposit_mode === "CASH"
                                  ? "通过后按确认金额登记现金押金并开通账号，首次登录需绑定验证器。"
                                  : "从后续订单净收益抵扣，本次不登记现金到账。"}
                              </RecordNotice>
                              <div className="admin-registration-review-actions">
                                <Button
                                  className="h-11"
                                  disabled={
                                    !review.level_id ||
                                    !review.identity_confirmed ||
                                    (current.data.deposit_mode === "CASH" &&
                                      (!review.deposit_confirmed ||
                                        Number(review.deposit_yuan) <
                                          current.data.deposit_required / 100))
                                  }
                                  onClick={() => setAction("approve")}
                                >
                                  <UserCheck size={16} />
                                  通过并开通账号
                                </Button>
                                <Button
                                  variant="outline"
                                  className="h-11"
                                  disabled={!review.review_note.trim()}
                                  onClick={() => setAction("reject")}
                                >
                                  <RotateCcw size={16} />
                                  退回补充资料
                                </Button>
                              </div>
                            </fieldset>
                        ) : (
                          <>
                            {current.data.status === "APPROVED" ? (
                              <>
                                <p className="text-sm text-muted-foreground">
                                  本次审核已完成，不可重复处理。
                                </p>
                                <dl className="admin-record-definition">
                                  <div>
                                    <dt>开通等级</dt>
                                    <dd>
                                      {current.data.level_name || "未记录"}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>押金结果</dt>
                                    <dd>
                                      {current.data.deposit_mode === "CASH"
                                        ? `登记现金 ${formatRmb(current.data.deposit_paid)} 元`
                                        : "单抵，不登记现金"}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>后续步骤</dt>
                                    <dd>
                                      使用入驻绑定的登录方式，首次登录需绑定验证器。
                                    </dd>
                                  </div>
                                </dl>
                              </>
                            ) : current.data.status === "REJECTED" ? (
                              <div className="rounded-xl border border-warning/30 bg-warning/10 p-4">
                                <p className="text-xs text-warning-foreground dark:text-warning">
                                  需要申请人修改
                                </p>
                                <p className="mt-2 text-sm leading-6">
                                  {current.data.review_note ||
                                    "申请人重新提交后进入待审核队列。"}
                                </p>
                              </div>
                            ) : (
                              <RecordNotice title="审核操作已隐藏">
                                当前角色可核对资料，没有审核处理权限。需由具备注册审核权限的成员处理。
                              </RecordNotice>
                            )}
                          </>
                        ))}
                            </DataRegion>
                          </FormSection>
                        <FormSection
                          title="核对清单"
                          note="材料齐全不能代替人工判断"
                        >
                          <DataRegion label="核对清单" loading={current.loading} hasSnapshot={!!current.data}>
                          {current.data && <ol className="admin-registration-checklist">
                            {[
                              ["账号与结单昵称", "用于登录、接单与报单展示"],
                              ["实名证件", "正反面、姓名与号码一致"],
                              [
                                current.data.deposit_mode === "CASH"
                                  ? "现金押金"
                                  : "单抵方式",
                                current.data.deposit_mode === "CASH"
                                  ? "实际到账与凭证金额一致"
                                  : "本次不登记现金到账",
                              ],
                              ["开通等级", "从当前启用等级中选择"],
                            ].map(([title, note], index) => (
                              <li key={title}>
                                <span>{index + 1}</span>
                                <div>
                                  <strong>{title}</strong>
                                  <p>{note}</p>
                                </div>
                              </li>
                            ))}
                          </ol>}
                          </DataRegion>
                        </FormSection>
                      </aside>
                    </div>
                  </div>
            </DialogContent>
          </Dialog>
          {action && current.data && (
            <MutationDialog
              title={action === "approve" ? "确认开通陪玩账号" : "退回补充资料"}
              submitVariant={action === "reject" ? "destructive" : "default"}
              description={`${current.data.username} · ${current.data.display_name} · ${current.data.real_name}。${action === "approve" ? "首次登录需绑定验证器。" : "填写需补充资料与原因，申请人可以查看。"}`}
              onClose={() => setAction(null)}
              disabled={
                current.loading ||
                !!current.error ||
                (action === "approve"
                  ? !review.level_id ||
                    !review.identity_confirmed ||
                    (current.data.deposit_mode === "CASH" &&
                      !review.deposit_confirmed)
                  : !review.review_note.trim())
              }
              onSubmit={async () => {
                await request({
                  url: `/provider-registrations/${id}/${action}/`,
                  method: "POST",
                  data: {
                    revision: current.data.revision,
                    review_note: review.review_note.trim(),
                    ...(action === "approve"
                      ? {
                          level_id: review.level_id,
                          identity_confirmed: review.identity_confirmed,
                          deposit_confirmed:
                            current.data.deposit_mode === "CASH" &&
                            review.deposit_confirmed,
                          deposit_paid:
                            current.data.deposit_mode === "CASH"
                              ? rmbToRawAmount(review.deposit_yuan)
                              : 0,
                        }
                      : {}),
                  },
                })
                toast.success(
                  action === "approve" ? "账号已开通" : "已退回补充资料"
                )
                setId(null)
                void list.reload()
              }}
            >
              <div className="admin-registration-confirm-summary">
                <p>
                  <strong>{current.data.display_name}</strong> ·{" "}
                  {current.data.username}
                </p>
                <p>资料版本 v{current.data.revision}</p>
                {action === "approve" && (
                  <p>
                    {current.data.deposit_mode === "CASH"
                      ? `登记现金押金 ${review.deposit_yuan} 元`
                      : "单抵，本次不登记现金到账"}
                  </p>
                )}
                <p>{review.review_note || "未填写审核意见"}</p>
              </div>
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}
