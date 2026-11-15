import { DataRegion, DataValue, usePageDataState } from "@/components/page-data-state"
import type { StatusReadState } from "@/components/Status"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ArrowUp,
  ArrowDown,
  Trash2,
  Megaphone,
  Sparkles,
  Trophy,
  ShieldCheck,
  Crown,
  Settings2,
} from "@/components/ui/icons"
import { coinText, coinUnit } from "@xa/shared"
import {
  CheckCircle2,
  Clock3,
  Gift,
  Headphones,
  Link2,
  PackageCheck,
  ListFilter,
  UserCheck,
} from "@/components/ui/icons"
import { useEffect, useState } from "react"
import { Navigate, useSearchParams } from "react-router"
import { operationPages, resolveOperationLocation, type OperationSection } from "@/lib/operation-navigation"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { AdminFilters } from "@/components/AdminFilters"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataPageState,
  FormSection,
  ReadOnlyNotice,
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
  type FormField,
  type Row,
} from "@/components/business"
import {
  SourceDirectoryCard,
  SourcePageHeader,
  useSourceReviewState,
  ResourcePage,
  type ResourceSpec,
} from "@/pages/Catalog"
import { errorMessage, request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { enumLabel } from "@/lib/labels"
import OperationConfigFields from "@/components/OperationConfigFields"
import PointsRedemptionConfig from "@/components/PointsRedemptionConfig"
import PointsUsageConfig from "@/components/PointsUsageConfig"

const enabled: FormField = { key: "is_active", label: "启用", type: "boolean" }
const sort: FormField = {
  key: "sort_order",
  label: "排序",
  type: "number",
  min: 0,
}
const rewardKinds: Array<[string, string]> = [
  ["PHYSICAL", "实物寄送"],
  ["VIRTUAL", "游戏内交付"],
  ["SERVICE", "服务权益"],
  ["NAMING", "冠名权益"],
  ["TAG", "自定义 TAG"],
  ["BALANCE", "余额奖品（仅抽奖）"],
  ["POINTS", "积分"],
]
const operationsSpecs: Record<string, ResourceSpec> = {
  rewards: {
    resource: "club-rewards",
    title: "积分礼品与权益",
    description:
      "配置兑换积分、库存和交付规格。余额奖品仅用于抽奖，不可上架积分商城。库存保存核对当前兑换数量，已有抽奖引用的奖品无法删除。",
    permission: "engagement",
    deletePermission: "engagement:edit",
    defaults: {
      name: "",
      code: "",
      audience: "BOSS",
      kind: "PHYSICAL",
      image_url: "",
      description: "",
      point_cost: 0,
      point_value: 0,
      balance_amount: 0,
      duration_days: 0,
      duration_months: 0,
      highlighted: false,
      service: null,
      stock: null,
      redeemed_count: 0,
      is_active: false,
      in_shop: true,
      sort_order: 0,
      vip_quota_kind: "",
    },
    fields: [
      { key: "name", label: "名称", required: true },
      { key: "code", label: "英文标识", required: true },
      {
        key: "audience",
        label: "适用会员",
        type: "select",
        options: [
          ["BOSS", "老板"],
          ["PROVIDER", "陪玩"],
          ["ALL", "全部会员"],
        ],
      },
      { key: "kind", label: "交付类型", type: "select", options: rewardKinds },
      { key: "point_cost", label: "兑换积分", type: "number", min: 0 },
      { key: "stock", label: "库存（留空不限）", type: "number", min: 0 },
      {
        key: "balance_amount",
        label: coinText("到账余额（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      { key: "point_value", label: "奖励积分", type: "number", min: 0 },
      { key: "duration_days", label: "有效天数", type: "number", min: 0 },
      { key: "duration_months", label: "有效月数", type: "number", min: 0 },
      {
        key: "service",
        label: "绑定服务",
        resource: "service-items",
        full: true,
      },
      { key: "image_url", label: "展示图片", upload: true, full: true },
      { key: "description", label: "交付说明", type: "textarea", full: true },
      { key: "in_shop", label: "在积分商城上架", type: "boolean" },
      { key: "highlighted", label: "重点权益", type: "boolean" },
      {
        key: "vip_quota_kind",
        label: "VIP 配额类别",
        type: "select",
        options: [
          ["", "按权益项目补差"],
          ["FRIEND", "好友位"],
          ["BIRTHDAY_GIFT", "生日礼物"],
        ],
      },
      sort,
      enabled,
    ],
    money: ["balance_amount"],
    columns: [
      { key: "name", label: "权益" },
      { key: "kind_label", label: "类型" },
      { key: "audience", label: "适用会员" },
      { key: "point_cost", label: "兑换积分" },
      {
        key: "stock",
        label: "库存",
        render: (row) => (row.stock == null ? "不限" : row.stock),
      },
      { key: "redeemed_count", label: "已兑换" },
      {
        key: "is_active",
        label: "启用",
        render: (row) => (row.is_active ? "启用" : "停用"),
      },
    ],
    transform: (data, source) => ({
      ...data,
      service: data.service || null,
      stock: data.stock === "" || data.stock == null ? null : data.stock,
      redeemed_count: source.redeemed_count,
    }),
  },
  content: {
    resource: "club-content",
    title: "运营内容",
    description: "维护活动海报、老板权益、下单须知、售后标准与积分规则。",
    permission: "engagement",
    deletePermission: "engagement:edit",
    defaults: {
      code: "",
      kind: "HOT",
      title: "",
      image_url: "",
      content: "",
      is_active: true,
      starts_at: "",
      ends_at: "",
      sort_order: 0,
    },
    fields: [
      { key: "title", label: "标题", required: true },
      { key: "code", label: "英文标识", required: true },
      {
        key: "kind",
        label: "内容类型",
        type: "select",
        options: [
          ["HOT", "俱乐部活动"],
          ["RIGHTS", "老板权益"],
          ["ORDER_RULES", "下单须知"],
          ["PROVIDER_RULES", "打手保障"],
          ["AFTERSALE_RULES", "售后标准"],
          ["POINT_RULES", "积分规则"],
        ],
      },
      sort,
      { key: "image_url", label: "展示图片", upload: true, full: true },
      {
        key: "content",
        label: "图文内容",
        richText: true,
        type: "textarea",
        full: true,
      },
      { key: "starts_at", label: "展示开始时间", type: "datetime-local" },
      { key: "ends_at", label: "展示结束时间", type: "datetime-local" },
      enabled,
    ],
    columns: [
      { key: "title", label: "标题" },
      { key: "kind", label: "类型" },
      { key: "code", label: "标识" },
      {
        key: "starts_at",
        label: "开始",
        render: (row) => formatDateTime(row.starts_at),
      },
      {
        key: "ends_at",
        label: "结束",
        render: (row) => formatDateTime(row.ends_at),
      },
      {
        key: "is_active",
        label: "状态",
        render: (row) => (row.is_active ? "启用" : "停用"),
      },
    ],
    transform: (data) => ({
      ...data,
      starts_at: data.starts_at || null,
      ends_at: data.ends_at || null,
    }),
  },
  packages: {
    resource: "recharge-packages",
    title: "充值档位",
    description:
      "只在消费此充值批次余额时计算积分，充值本身不加积分；修改仅影响新充值。",
    permission: "engagement",
    deletePermission: "engagement:edit",
    defaults: { amount: 0, gift_amount: 0, points_rate: 100, active: true },
    money: ["amount", "gift_amount"],
    fields: [
      {
        key: "amount",
        label: coinText("实充金额（浪花）"),
        type: "number",
        min: 0.1,
        step: 0.1,
      },
      {
        key: "gift_amount",
        label: coinText("赠送金额（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "points_rate",
        label: "消费积分倍率（100 表示 1 倍）",
        type: "number",
        min: 100,
        max: 1000,
      },
      { key: "active", label: "启用", type: "boolean" },
    ],
    columns: [
      {
        key: "amount",
        label: "实充",
        render: (row) => `${amountToXaCoin(row.amount)} ${coinUnit()}`,
      },
      {
        key: "gift_amount",
        label: "赠送",
        render: (row) => `${amountToXaCoin(row.gift_amount)} ${coinUnit()}`,
      },
      {
        key: "points_rate",
        label: "积分倍率",
        render: (row) => `${row.points_rate / 100} 倍`,
      },
      {
        key: "active",
        label: "状态",
        render: (row) => (row.active ? "启用" : "停用"),
      },
    ],
  },
  rules: {
    canCreate: false,
    canDelete: false,
    resource: "broadcast-rules",
    title: "播报规则",
    description: "配置事件模板、展示时长和昵称隐私。",
    permission: "engagement",
    deletePermission: "engagement:edit",
    defaults: {
      kind: "RECHARGE",
      template: "",
      enabled: false,
      visible_hours: 72,
      sort_order: 0,
      mask_nickname: false,
    },
    fields: [
      {
        key: "kind",
        label: "事件类型",
        type: "select",
        options: [
          ["NAMING", "冠名"],
          ["RECHARGE", "充值"],
          ["UPGRADE", "升级"],
          ["REVIEW", "好评"],
          ["BIRTHDAY", "生日"],
          ["GIFT", "礼物单"],
          ["LOTTERY", "转盘中奖"],
          ["LOTTERY_SERVICE", "抽中体验单"],
          ["LOTTERY_NAMING", "抽中冠名"],
          ["LOTTERY_TAG", "抽中 TAG"],
        ],
      },
      {
        key: "visible_hours",
        label: "可见时长（小时）",
        type: "number",
        min: 1,
        max: 8760,
      },
      {
        key: "template",
        label: "事件播报模板",
        type: "textarea",
        full: true,
        hint: "变量以现有播报规则为准，保存时会校验未知变量。",
      },
      { key: "mask_nickname", label: "昵称脱敏", type: "boolean" },
      { key: "enabled", label: "启用", type: "boolean" },
      sort,
    ],
    columns: [
      { key: "kind", label: "事件" },
      {
        key: "template",
        label: "模板",
        render: (row) => (
          <p className="max-w-[390px] text-xs leading-5 break-words whitespace-pre-wrap text-muted-foreground">
            {row.template}
          </p>
        ),
      },
      {
        key: "visible_hours",
        label: "展示时长",
        render: (row) => (
          <span className="inline-flex items-center gap-2 text-xs whitespace-nowrap tabular-nums">
            <Clock3 className="size-3.5 text-muted-foreground" />
            {row.visible_hours} 小时
          </span>
        ),
      },
      { key: "sort_order", label: "排序" },
      {
        align: "center",
        key: "mask_nickname",
        label: "昵称保护",
        render: (row) => (
          <Status
            value={row.mask_nickname ? "AVAILABLE" : "PENDING"}
            label={row.mask_nickname ? "脱敏" : "完整昵称"}
            tone={row.mask_nickname ? "primary" : "muted"}
            readState={typeof row.mask_nickname === "boolean" ? undefined : "unread"}
          />
        ),
      },
      {
        key: "enabled",
        label: "启用",
        render: (row) => <Status value={row.enabled} />,
      },
    ],
  },
  giftBenefits: {
    resource: "gift-benefits",
    title: "赠礼附送权益",
    description: "按礼物金额门槛配置服务、冠名或 TAG 附送权益。",
    permission: "engagement",
    deletePermission: "engagement:edit",
    defaults: {
      name: "",
      reward: null,
      category: null,
      threshold: 0,
      inclusive: false,
      cumulative: false,
      once_per_account: false,
      enabled: false,
      starts_at: "",
      ends_at: "",
    },
    money: ["threshold"],
    fields: [
      { key: "name", label: "规则名称", required: true },
      {
        key: "threshold",
        label: coinText("门槛（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      {
        key: "reward",
        label: "附送权益",
        resource: "club-rewards",
        optionPath: "/club-rewards/",
        required: true,
      },
      { key: "category", label: "礼物分类", resource: "service-categories" },
      { key: "inclusive", label: "包含等于门槛", type: "boolean" },
      { key: "cumulative", label: "按活动期累计", type: "boolean" },
      { key: "once_per_account", label: "每账号限一次", type: "boolean" },
      { key: "enabled", label: "启用", type: "boolean" },
      { key: "starts_at", label: "活动开始", type: "datetime-local" },
      { key: "ends_at", label: "活动结束", type: "datetime-local" },
    ],
    columns: [
      { key: "name", label: "规则" },
      {
        align: "right",
        key: "threshold",
        label: "门槛",
        render: (row) => `${amountToXaCoin(row.threshold)} ${coinUnit()}`,
      },
      { key: "reward", label: "附送权益编号" },
      {
        key: "cumulative",
        label: "累计活动",
        render: (row) => (row.cumulative ? "是" : "否"),
      },
      {
        key: "enabled",
        label: "状态",
        render: (row) => <Status value={row.enabled} />,
      },
    ],
    transform: (data) => ({
      ...data,
      category: data.category || null,
      starts_at: data.starts_at || null,
      ends_at: data.ends_at || null,
    }),
  },
}
export default function ClubOperationsPage() {
  const [params] = useSearchParams()
  const auth = useAdminAuth()
  const target = resolveOperationLocation(params.toString(), auth.hasPerm)
  return <Navigate to={target || "/403"} replace />
}

export function OperationPage({ section }: { section: OperationSection }) {
  const auth = useAdminAuth()
  const page = operationPages.find(page => page.section === section)!
  if (!auth.hasPerm(page.permission)) return <Navigate to="/403" replace />
  return (
    <div className="admin-operations-page space-y-4">
      {operationsSpecs[section] ? (
        <ResourcePage key={section} spec={operationsSpecs[section]} />
      ) : section === "claims" ? (
        <ClaimsPage />
      ) : section === "tiers" ? (
        <TiersPage />
      ) : section === "lotteries" ? (
        <LotteriesPage />
      ) : section === "feed" || section === "namings" ? (
        <VisibilityRecords kind={section} />
      ) : section === "external" ? (
        <ExternalPoints />
      ) : section === "kook" ? (
        <KookStatus />
      ) : (
        <><OperationConfig /><PointsRedemptionConfigIfPermitted /></>
      )}
    </div>
  )
}
function PointsRedemptionConfigIfPermitted() {
  const auth = useAdminAuth()
  return auth.hasPerm("dictionary:view") || auth.hasPerm("dictionary:edit") ? <><PointsRedemptionConfig /><PointsUsageConfig /></> : null
}
function ClaimsPage() {
  const auth = useAdminAuth()
  const [params] = useSearchParams()
  const [query, setQuery] = useState<Row>({
    status: params.get("status") || "",
    kind: "",
    search: "",
    needs_tag_review: "",
  })
  const list = useList("club-claims", query)
  const [id, setId] = useState<number | null>(null)
  const current = useLoad(
    async () =>
      id ? (await request({ url: `/club-claims/${id}/` })).data : null,
    [id]
  )
  const [action, setAction] = useState<string | null>(null)
  const [note, setNote] = useState("")
  const [reviewed, setReviewed] = useState(false)
  const [blocked, setBlocked] = useState(false)
  useLiveReload(list.reload)
  const review = useSourceReviewState("/admin/club-claims/")
  const operationsLocked = current.loading || !!current.error || review.locked
  const claim = current.data
  const clearClaimsFilters = () => setQuery({ status: "", kind: "", search: "", needs_tag_review: "", source: "", account_type: "", tag_ready_for_review: "" })
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="claims"
    >
      {
        <div
          className="admin-source-page space-y-4"
          data-resource="club-claims"
        >
          <SourcePageHeader
            tag="权益与履约"
            title="权益交付"
            description="核对领取人、权益规格和最新交付资料，再更新状态或审核标签。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </SourcePageHeader>
          <section className="grid grid-cols-4 gap-3" aria-label="交付队列摘要">
            {[
              {
                label: "本页待处理",
                count: list.rows.filter((row) => row.status === "PENDING")
                  .length,
                status: "PENDING",
                icon: Clock3,
              },
              {
                label: "本页交付中",
                count: list.rows.filter((row) => row.status === "FULFILLING")
                  .length,
                status: "FULFILLING",
                icon: PackageCheck,
              },
              {
                label: "本页已完成",
                count: list.rows.filter((row) => row.status === "COMPLETED")
                  .length,
                status: "COMPLETED",
                icon: CheckCircle2,
              },
              {
                label: "符合条件",
                count: list.total,
                status: "",
                icon: ListFilter,
              },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-xl border bg-card p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">
                      {item.label}
                    </p>
                    <p className="mt-2 text-2xl font-semibold tabular-nums">
                      <DataValue>{item.count}</DataValue>
                    </p>
                  </div>
                  <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                    <item.icon className="size-4" />
                  </span>
                </div>
              </div>
            ))}
          </section>
          <ReadOnlyNotice
            canEdit={auth.hasPerm("engagement:fulfill")}
            subject="权益交付"
          />
          <Feedback message={list.error} retry={list.reload} />
          <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
            title="交付队列"
            icon={PackageCheck}
            minWidth={1060}
            description={`本页 ${list.rows.length} 条`}
            loading={list.loading}
            rows={list.rows}
            error={list.error}
            filters={
              <AdminFilters activeCount={[query.source, query.account_type, query.needs_tag_review, query.tag_ready_for_review].filter(Boolean).length} primary={<>
                <Field
                  field={{ key: "claim-search", label: "权益 / 账号 / 标签" }}
                  value={query.search}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, search: value }))
                  }
                />
                <Field
                  field={{
                    key: "claim-status",
                    label: "状态",
                    type: "select",
                    options: [
                      ["PENDING", "待处理"],
                      ["FULFILLING", "交付中"],
                      ["COMPLETED", "已完成"],
                      ["CANCELLED", "已取消"],
                    ],
                  }}
                  value={query.status}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, status: value }))
                  }
                />
                <Field
                  field={{
                    key: "claim-kind",
                    label: "权益类型",
                    type: "select",
                    options: rewardKinds,
                  }}
                  value={query.kind}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, kind: value }))
                  }
                />
              </>} actions={<Button type="button" variant="ghost" onClick={clearClaimsFilters}>清除筛选</Button>}>
                <label className="flex h-9 items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={query.needs_tag_review === "1"}
                    onChange={(event) =>
                      setQuery((old) => ({
                        ...old,
                        needs_tag_review: event.target.checked ? "1" : "",
                      }))
                    }
                  />
                  待审核标签
                </label>
                <Field
                  field={{
                    key: "claim-source",
                    label: "权益来源",
                    type: "select",
                    options: [
                      ["GIFT", "赠礼权益"],
                      ["VIP", "会员权益"],
                      ["REDEMPTION", "积分兑换"],
                      ["LOTTERY", "转盘奖品"],
                    ],
                  }}
                  value={query.source}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, source: value }))
                  }
                />
                <Field
                  field={{
                    key: "claim-account-type",
                    label: "会员类型",
                    type: "select",
                    options: [
                      ["BOSS", "老板"],
                      ["PROVIDER", "陪玩"],
                    ],
                  }}
                  value={query.account_type}
                  onChange={(value) =>
                    setQuery((old) => ({ ...old, account_type: value }))
                  }
                />
                <label className="flex min-h-9 items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={query.tag_ready_for_review === "1"}
                    onChange={(event) =>
                      setQuery((old) => ({
                        ...old,
                        tag_ready_for_review: event.target.checked ? "1" : "",
                      }))
                    }
                  />
                  TAG 资料已提交待审核
                </label>
              </AdminFilters>
            }
            empty="没有匹配的交付记录"
            clearFilters={clearClaimsFilters}
            columns={[
              {
                key: "name_snapshot",
                label: "权益 / 记录",
                render: (row) => (
                  <div className="flex items-start gap-3">
                    <span className="grid size-10 shrink-0 place-items-center rounded-xl border bg-primary/5 text-primary">
                      <Gift className="size-5" />
                    </span>
                    <div>
                      <p className="text-sm font-semibold">
                        {row.name_snapshot}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        交付 #{row.id}
                        {row.source_order ? ` · 订单 #${row.source_order}` : ""}
                      </p>
                    </div>
                  </div>
                ),
              },
              {
                key: "account_name",
                label: "申请人",
                render: (row) => (
                  <div>
                    <p className="text-sm font-medium">
                      {row.account_name || `账号 #${row.account}`}
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {enumLabel(row.account_type)} · #{row.account}
                    </p>
                  </div>
                ),
              },
              {
                key: "source",
                label: "来源 / 类型",
                render: (row) => (
                  <div className="space-y-1">
                    <Badge variant="outline">{enumLabel(row.source)}</Badge>
                    <p className="text-[11px] text-muted-foreground">
                      {enumLabel(row.kind_snapshot)}
                    </p>
                  </div>
                ),
              },
              {
                key: "status",
                label: "交付状态",
                align: "center",
                render: (row) => (
                  <div className="space-y-1">
                    <Status value={row.status} label={row.status_label} />
                    {row.tag_revision_required && (
                      <p className="text-[11px] text-warning">TAG 待修改</p>
                    )}
                    {row.tag_revision_submitted && (
                      <p className="text-[11px] text-primary">新资料待审核</p>
                    )}
                    {row.points_returned && (
                      <p className="text-[11px] text-success">积分已退回</p>
                    )}
                  </div>
                ),
              },
              {
                key: "details",
                label: "资料摘要（已脱敏）",
                render: (row) => (
                  <div className="max-w-52 space-y-1 text-xs text-muted-foreground">
                    {row.details?.tag ? (
                      <p className="break-words">TAG · {row.details.tag}</p>
                    ) : row.details?.game_uid ? (
                      <p>已填写游戏 UID</p>
                    ) : row.details?.recipient ||
                      row.details?.phone ||
                      row.details?.address ? (
                      <p>已填写收件资料</p>
                    ) : (
                      <p>尚未提交交付资料</p>
                    )}
                    {row.delivery_note && <p>有后台交付备注</p>}
                  </div>
                ),
              },
              {
                key: "created_at",
                label: "创建时间",
                className: "whitespace-nowrap",
                render: (row) => formatDateTime(row.created_at),
              },
              {
                key: "actions",
                label: "操作",
                align: "right",
                render: (row) => (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={list.loading || !!list.error}
                    onClick={() => setId(row.id)}
                  >
                    核对处理
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
            <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-[1120px]">
              <DialogHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <DialogTitle>权益交付 #{id}</DialogTitle>
                  {claim && (
                    <Status value={claim.status} label={claim.status_label} />
                  )}
                </div>
                <DialogDescription>
                  {claim ? `${claim.name_snapshot} · ${claim.account_name}` : "核对历史权益、私密交付资料与处理记录。"}
                </DialogDescription>
              </DialogHeader>
                  <div className="admin-source-editor grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
                    <div className="space-y-4">
                      <FormSection title="历史权益快照" className="bg-muted/15">
                        <DataRegion label="历史权益快照" loading={current.loading} hasSnapshot={!!claim} error={current.error} retry={current.reload}>
                        {claim && <dl className="grid gap-x-5 gap-y-4 sm:grid-cols-2">
                          {[
                            ["权益名称", claim.name_snapshot],
                            [
                              "申请人",
                              `${enumLabel(claim.account_type)} · ${claim.account_name} #${claim.account}`,
                            ],
                            [
                              "来源",
                              `${enumLabel(claim.source)}${claim.source_order ? ` · 订单 #${claim.source_order}` : ""}`,
                            ],
                            ["创建时间", formatDateTime(claim.created_at)],
                            [
                              "权益时长",
                              claim.duration_months
                                ? `${claim.duration_months} 个月`
                                : claim.duration_days
                                  ? `${claim.duration_days} 天`
                                  : "以权益规格为准",
                            ],
                            [
                              "展示类型",
                              claim.kind_snapshot === "TAG"
                                ? claim.highlighted
                                  ? "高亮 TAG"
                                  : "普通 TAG"
                                : enumLabel(claim.kind_snapshot),
                            ],
                            ...(claim.points_spent > 0
                              ? [
                                  [
                                    "兑换积分",
                                    `${claim.points_spent} 积分${claim.points_returned ? " · 已退回" : ""}`,
                                  ],
                                ]
                              : []),
                            ...(claim.balance_amount > 0
                              ? [
                                  [
                                    "到账余额",
                                    `${amountToXaCoin(claim.balance_amount)} ${coinUnit()}`,
                                  ],
                                ]
                              : []),
                            ...(claim.expires_at
                              ? [["有效期至", formatDateTime(claim.expires_at)]]
                              : []),
                            ...(claim.completed_at
                              ? [
                                  [
                                    "完成时间",
                                    formatDateTime(claim.completed_at),
                                  ],
                                ]
                              : []),
                            ...(claim.delivery_note
                              ? [["交付说明", claim.delivery_note]]
                              : []),
                            ...(claim.tag_suspension_reason
                              ? [["标签暂停原因", claim.tag_suspension_reason]]
                              : []),
                          ].map(([label, value]) => (
                            <div key={label}>
                              <dt className="text-[11px] text-muted-foreground">
                                {label}
                              </dt>
                              <dd className="mt-1 text-sm leading-5 break-words">
                                {value}
                              </dd>
                            </div>
                          ))}
                        </dl>}
                        </DataRegion>
                      </FormSection>
                      <section className="space-y-3 rounded-xl border p-4">
                        <h3 className="text-sm font-semibold">私密交付资料</h3>
                        <DataRegion label="私密交付资料" loading={current.loading} hasSnapshot={!!claim}>
                        {claim && <>
                        <dl className="grid grid-cols-2 gap-3">
                          {Object.entries(claim.details || {}).map(
                            ([key, value]) => (
                              <div
                                key={key}
                                className={`rounded-lg bg-muted/30 px-3 py-2 ${["address", "note"].includes(key) ? "col-span-2" : ""}`}
                              >
                                <dt className="text-xs text-muted-foreground">
                                  {(
                                    {
                                      name: "收件人",
                                      recipient: "收件人",
                                      phone: "联系手机",
                                      address: "收货地址",
                                      game_name: "游戏昵称",
                                      game_id: "游戏账号",
                                      game_uid: "游戏账号",
                                      provider_name: "指定陪玩",
                                      tag: "TAG 内容",
                                      naming: "冠名内容",
                                      note: "备注",
                                    } as Row
                                  )[key] || key}
                                </dt>
                                <dd className="mt-1 text-sm break-words">
                                  {typeof value === "object"
                                    ? "请核对关联资料"
                                    : String(value)}
                                </dd>
                              </div>
                            )
                          )}
                        </dl>
                        {!Object.keys(claim.details || {}).length && (
                          <p className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                            无需补充交付资料
                          </p>
                        )}
                        </>}
                        </DataRegion>
                      </section>
                      <div className="rounded-xl border p-4">
                        <Button variant="outline" disabled className="w-full">
                          处理轨迹 · 待开放
                        </Button>
                      </div>
                    </div>
                    <aside className="min-w-0 space-y-4 xl:sticky xl:top-0">
                      {claim && ["COMPLETED", "CANCELLED"].includes(claim.status) && (
                        <section className="rounded-xl border bg-muted/15 p-4">
                          <p className="text-sm font-semibold">
                            交付记录已归档
                          </p>
                          <p className="mt-2 text-xs leading-5 text-muted-foreground">
                            {claim.status === "CANCELLED"
                              ? "已取消，不能恢复为待处理或交付中。"
                              : "历史快照和处理说明保持只读。"}
                          </p>
                        </section>
                      )}
                      <fieldset disabled={operationsLocked}>
                        <FormSection
                          title={
                            claim?.status === "COMPLETED"
                              ? "TAG 展示与记录管理"
                              : "处理本次交付"
                          }
                        >
                          <DataRegion label="交付处理" loading={current.loading} hasSnapshot={!!claim}>
                          {claim && <div className="flex flex-wrap gap-2">
                            {auth.hasPerm("engagement:fulfill") &&
                              ["PENDING", "FULFILLING"].includes(
                                claim.status
                              ) &&
                              [
                                ["PENDING", "退回补充资料"],
                                ["FULFILLING", "开始交付"],
                                ["COMPLETED", "完成交付"],
                                ["CANCELLED", "取消交付"],
                              ]
                                .filter(([status]) =>
                                  status === "PENDING"
                                    ? claim.status === "FULFILLING" ||
                                      (claim.kind_snapshot === "TAG" &&
                                        claim.status === "PENDING")
                                    : status !== claim.status &&
                                      (status !== "CANCELLED" ||
                                        claim.source === "REDEMPTION")
                                )
                                .map(([status, label]) => (
                                  <Button
                                    key={status}
                                    variant={
                                      status === "COMPLETED"
                                        ? "default"
                                        : "outline"
                                    }
                                    size="sm"
                                    disabled={
                                      claim.kind_snapshot === "TAG" &&
                                      claim.tag_revision_required &&
                                      ["FULFILLING", "COMPLETED"].includes(
                                        status
                                      )
                                    }
                                    onClick={() => {
                                      setAction(status)
                                      setNote("")
                                      setReviewed(false)
                                      setBlocked(false)
                                    }}
                                  >
                                    {label}
                                  </Button>
                                ))}
                            {auth.hasPerm("engagement:fulfill") &&
                              claim.kind_snapshot === "TAG" &&
                              claim.status === "COMPLETED" &&
                              !claim.tag_expired && (
                                <>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={
                                      !!claim.tag_reviewed ||
                                      (claim.tag_editable &&
                                        !claim.tag_revision_submitted)
                                    }
                                    onClick={() => {
                                      setAction("review-tag")
                                      setNote("")
                                      setReviewed(false)
                                      setBlocked(false)
                                    }}
                                  >
                                    审核 TAG
                                  </Button>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={!claim.tag_reviewed}
                                    onClick={() => {
                                      setAction("suspend-tag")
                                      setNote("")
                                      setReviewed(false)
                                      setBlocked(false)
                                    }}
                                  >
                                    暂停 TAG 展示
                                  </Button>
                                </>
                              )}
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={current.reload}
                            >
                              重新读取
                            </Button>
                          </div>}
                          </DataRegion>
                        </FormSection>
                      </fieldset>
                    </aside>
                  </div>
            </DialogContent>
          </Dialog>
          {action && claim && (
            <MutationDialog
              confirmDiscard
              dirty={Boolean(note || reviewed)}
              title={
                action === "review-tag"
                  ? "审核 TAG 内容"
                  : action === "suspend-tag"
                    ? "暂停 TAG 展示"
                    : "更新权益交付"
              }
              description={`权益 #${claim.id} · ${claim.account_name} · ${claim.name_snapshot}。${action === "CANCELLED" && claim.source === "REDEMPTION" ? `取消兑换将退回 ${claim.points_spent} 积分并恢复库存。` : "请核对最新资料与权益规格。"}`}
              readOnly={operationsLocked}
              submitVariant={
                action === "CANCELLED" || action === "suspend-tag"
                  ? "destructive"
                  : "default"
              }
              onClose={() => setAction(null)}
              disabled={
                blocked ||
                !reviewed ||
                (action !== "review-tag" && !note.trim())
              }
              onSubmit={async () => {
                try {
                  if (action === "review-tag" || action === "suspend-tag")
                    await request({
                      url: `/club-claims/${claim.id}/${action}/`,
                      method: "POST",
                      data: {
                        expected_tag: claim.details?.tag,
                        ...(action === "review-tag"
                          ? { tag_reviewed: true }
                          : { reason: note.trim() }),
                      },
                    })
                  else
                    await request({
                      url: `/club-claims/${claim.id}/process/`,
                      method: "POST",
                      data: {
                        status: action,
                        note: note.trim(),
                        tag_reviewed: claim.kind_snapshot === "TAG" && reviewed,
                        expected_details: claim.details || {},
                        expected_status: claim.status,
                        expected_terms: {
                          duration_days: claim.duration_days,
                          duration_months: claim.duration_months,
                          highlighted: claim.highlighted,
                        },
                      },
                    })
                  toast.success("权益记录已更新")
                  void list.reload()
                  void current.reload()
                } catch (cause: any) {
                  setBlocked(true)
                  void current.reload()
                  throw cause
                }
              }}
            >
              {action !== "review-tag" && (
                <Field
                  field={{
                    key: "claim-note",
                    label: action === "suspend-tag" ? "暂停原因" : "处理说明",
                    type: "textarea",
                    required: true,
                  }}
                  value={note}
                  onChange={setNote}
                />
              )}
              <label className="flex items-start gap-2 text-sm">
                <input
                  className="mt-1 size-[18px] shrink-0 accent-primary"
                  type="checkbox"
                  checked={reviewed}
                  onChange={(event) => setReviewed(event.target.checked)}
                />
                已核对账号、最新交付资料、权益规格
                {claim.kind_snapshot === "TAG" ? "及 TAG 内容符合要求" : ""}
              </label>
              {blocked && (
                <Feedback message="交付资料、状态或处理结果已变化。请返回详情重新核对后继续。" />
              )}
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}
function TiersPage() {
  const list = useList("vip-tiers")
  const pointsConfig = useLoad(async () => (await request({ url: "/club-config/" })).data)
  const canConfigurePoints = typeof pointsConfig.data?.base_points_rate === "number"
  const auth = useAdminAuth()
  const review = useSourceReviewState("/admin/vip-tiers/")
  const locked = list.loading || !!list.error || review.locked
  const [editing, setEditing] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="vip-tiers"
    >
      {
        <div className="space-y-4">
          <SourcePageHeader
            title="VIP 等级"
            description="累计确认消费达到门槛后自动升级，按权益类别补差，不以充值额代替实销。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("engagement:edit") && (
              <Button
                variant="secondary"
                disabled={locked}
                onClick={() =>
                  setEditing({
                    boss_type: null,
                    level: 1,
                    consumption_threshold: 0,
                    benefits: {},
                    upgrade_rewards: [],
                    companion_service_ids: [],
                    safe_box_service_ids: [],
                    monthly_discount: 90,
                    monthly_coupon_count: 0,
                    season_discount: 80,
                    ...(canConfigurePoints ? { points_rate: 100 } : {}),
                    active: true,
                  })
                }
              >
                新增等级
              </Button>
            )}
          </SourcePageHeader>
          <ReadOnlyNotice
            canEdit={auth.hasPerm("engagement:edit")}
            subject="VIP 权益"
          />
          <Feedback message={list.error} retry={list.reload} />
          <Feedback message={pointsConfig.error ? `积分倍率配置未读取：${pointsConfig.error}` : ""} retry={pointsConfig.reload} />
          <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
            title="VIP 等级权益"
            minWidth={1040}
            rows={list.rows}
            error={list.error}
            loading={list.loading}
            columns={[
              { key: "level", label: "VIP 等级" },
              { key: "name", label: "老板分级" },
              {
                align: "right",
                key: "consumption_threshold",
                label: "累计确认消费门槛",
                render: (row) =>
                  `${amountToXaCoin(row.consumption_threshold ?? row.recharge_threshold)} ${coinUnit()}`,
              },
              {
                key: "monthly_discount",
                label: "月券折扣",
                render: (row) => `${row.monthly_discount}%`,
              },
              { key: "monthly_coupon_count", label: "月券张数" },
              {
                key: "points_rate",
                label: "积分倍率",
                render: (row: Row) => {
                  const rate = row.points_rate
                  const rateKnown = (typeof rate === "number" || (typeof rate === "string" && rate.trim() !== "")) && Number.isFinite(Number(rate))
                  return <DataValue known={!!pointsConfig.data} loading={pointsConfig.loading}>
                    {canConfigurePoints ? <DataValue known={rateKnown}>{rateKnown ? `${Number(rate) / 100} 倍` : null}</DataValue> : "未开放"}
                  </DataValue>
                },
              },
              {
                key: "season_discount",
                label: "赛季折扣",
                render: (row) => `${row.season_discount}%`,
              },
              {
                key: "active",
                label: "状态",
                align: "center",
                render: (row) => (row.active ? "启用" : "停用"),
              },
              {
                key: "actions",
                label: "操作",
                align: "right",
                render: (row) =>
                  auth.hasPerm("engagement:edit") && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={locked}
                      onClick={() => setEditing(row)}
                    >
                      编辑权益
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
          {pointsConfig.data?.vip_threshold_recalculation_enabled === true && <VipUpgradeReviewPanel />}
          {editing && (
            <TierEditor
              row={editing}
              canConfigurePoints={canConfigurePoints}
              canConfigureCouponTerms={pointsConfig.data?.vip_coupon_terms_config_enabled === true}
              couponTermsReadState={typeof pointsConfig.data?.vip_coupon_terms_config_enabled === "boolean" ? undefined : !pointsConfig.data && pointsConfig.loading ? "loading" : !pointsConfig.data && pointsConfig.error ? "error" : "unread"}
              canRetireSafely={pointsConfig.data?.vip_coupon_protection_enabled === true}
              canRecalculateThreshold={pointsConfig.data?.vip_threshold_recalculation_enabled === true}
              vipEnabled={pointsConfig.data?.vip_enabled !== false}
              locked={locked}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function VipUpgradeReviewPanel() {
  const auth = useAdminAuth()
  const list = useList("vip-upgrade-reviews", { status: "PENDING" })
  const [editing, setEditing] = useState<Row | null>(null)
  return <div className="space-y-3">
    <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
      title="历史升级权益待核对"
      description="停用前规则无法核实时，系统先重算等级，不自动补发权益。"
      rows={list.rows} loading={list.loading} error={list.error} minWidth={900}
      columns={[
        { key: "account_no", label: "老板编号" },
        { key: "account_name", label: "老板" },
        { key: "tier_name", label: "待核对等级" },
        { key: "created_at", label: "产生时间", render: (row) => formatDateTime(row.created_at) },
        { key: "actions", label: "操作", align: "right", render: (row) => auth.hasPerm("engagement:edit") &&
          <Button size="sm" variant="outline" disabled={list.loading || !!list.error} onClick={() => setEditing(row)}>核对</Button> },
      ]}
    />
    <Feedback message={list.error} retry={list.reload} />
    <Pagination snapshotAvailable={!!list.data} page={list.page} total={list.total} loading={list.loading} onChange={list.setPage}
      pageSize={list.pageSize} onPageSizeChange={list.setPageSize} />
    {editing && <VipUpgradeReviewDialog row={editing} onClose={() => setEditing(null)} onSaved={list.reload} />}
  </div>
}
function VipUpgradeReviewDialog({ row, onClose, onSaved }: { row: Row; onClose: () => void; onSaved: () => void }) {
  const [decision, setDecision] = useState("")
  const [note, setNote] = useState("")
  return <MutationDialog title="核对历史升级权益" description={`老板 ${row.account_name || row.account_no} · ${row.tier_name}。请先核实历史发放情况。`}
    onClose={onClose} disabled={!decision || note.trim().length < 2} onSubmit={async () => {
      await request({ url: `/vip-upgrade-reviews/${row.id}/resolve/`, method: "POST", data: { decision, note: note.trim() } })
      toast.success(decision === "ISSUE" ? "已核对并补发" : "已记录历史发放结果")
      onSaved()
    }}>
    <p className="text-sm">重算时净消费：<DataValue known={row.evidence?.net_consumption != null}>{amountToXaCoin(row.evidence?.net_consumption)}</DataValue> {coinUnit()}。如确认补发，将按当前等级配置补足，已有记录不会重复发放。</p>
    <p className="text-xs text-muted-foreground">确认历史已发时，不会伪造发放记录；若安全箱券历史记录缺失，系统也不会自动创建新券，请保留核对依据。</p>
    {decision === "ISSUE" && <div className="rounded border p-3 text-sm space-y-1">
      <p>当前陪玩赠券：{row.companion_quantity} 张</p>
      {(row.current_rewards || []).map((item: Row) => <p key={item.id}>{item.name}：最多补足 {item.quantity} 项</p>)}
      {row.tier_active === false && <p>该等级已停用，暂不能补发。</p>}
    </div>}
    <Field field={{ key: "vip-review-decision", label: "核对结果", type: "select", options: [["", "请选择"], ["ALREADY_ISSUED", "确认历史已发"], ["ISSUE", "确认历史未发并补发"]] }} value={decision} onChange={setDecision} />
    <Field field={{ key: "vip-review-note", label: "核对依据", type: "textarea", maxLength: 500 }} value={note} onChange={setNote} />
  </MutationDialog>
}
function TierEditor({
  row,
  canConfigurePoints,
  canConfigureCouponTerms,
  couponTermsReadState,
  canRetireSafely,
  canRecalculateThreshold,
  vipEnabled,
  locked,
  onClose,
  onSaved,
}: {
  row: Row
  canConfigurePoints: boolean
  canConfigureCouponTerms: boolean
  couponTermsReadState?: StatusReadState
  canRetireSafely: boolean
  canRecalculateThreshold: boolean
  vipEnabled: boolean
  locked: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    ...row,
    threshold: Number(
      amountToXaCoin(row.consumption_threshold ?? row.recharge_threshold ?? 0)
    ),
    upgrade_rewards: [...(row.upgrade_rewards || [])],
  })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [optionReady, setOptionReady] = useState<Record<string, boolean>>({})
  const [retirement, setRetirement] = useState<Row | null>(null)
  const [retirementError, setRetirementError] = useState("")
  const [recalculation, setRecalculation] = useState<Row | null>(null)
  const [recalculationError, setRecalculationError] = useState("")
  const retiring = Boolean(row.id && row.active && !form.active)
  const reactivating = Boolean(row.id && !row.active && form.active)
  const creatingActive = Boolean(!row.id && form.active)
  const thresholdValue = Math.round(Number(form.threshold) * 10)
  const thresholdChanged = Boolean(row.id && thresholdValue !== Number(row.consumption_threshold ?? row.recharge_threshold))
  const needsRecalculation = thresholdChanged || reactivating || creatingActive
  const couponSettingsValid = !canConfigureCouponTerms || [
    [form.monthly_coupon_count, 0, 32767], [form.monthly_discount, 1, 100], [form.season_discount, 0, 100],
  ].every(([value, min, max]) => value !== "" && Number.isInteger(Number(value)) && Number(value) >= Number(min) && Number(value) <= Number(max))
  const tierData = {
    boss_type: form.boss_type,
    level: form.level,
    consumption_threshold: thresholdValue,
    benefits: form.benefits || {},
    upgrade_rewards: form.upgrade_rewards,
    companion_service_ids: form.companion_service_ids || [],
    safe_box_service_ids: form.safe_box_service_ids || [],
    active: form.active,
    ...(canConfigureCouponTerms ? { monthly_discount: Number(form.monthly_discount), monthly_coupon_count: Number(form.monthly_coupon_count), season_discount: Number(form.season_discount) } : {}),
    ...(canConfigurePoints ? { points_rate: Number(form.points_rate) } : {}),
  }
  const creationPayload = JSON.stringify(tierData)
  useEffect(() => {
    let current = true
    setRetirement(null)
    setRetirementError("")
    if (retiring && canRetireSafely) {
      request({ url: `/vip-tiers/${row.id}/retirement-preview/` })
        .then((result) => { if (current) setRetirement(result.data) })
        .catch(() => { if (current) setRetirementError("无法核对停用影响，请关闭后重试") })
    }
    return () => { current = false }
  }, [retiring, canRetireSafely, row.id])
  useEffect(() => {
    let current = true
    setRecalculation(null)
    setRecalculationError("")
    if (needsRecalculation && canRecalculateThreshold && !retiring && !(reactivating && thresholdChanged) && Number.isInteger(thresholdValue) && thresholdValue > 0 && (!creatingActive || !!form.boss_type)) {
      const data = creatingActive ? JSON.parse(creationPayload) : reactivating ? { active: true } : { consumption_threshold: thresholdValue }
      const url = creatingActive ? "/vip-tiers/creation-preview/" : `/vip-tiers/${row.id}/recalculation-preview/`
      request({ url, method: "POST", data })
        .then((result) => { if (current) setRecalculation(result.data) })
        .catch((error) => { if (current) setRecalculationError(errorMessage(error)) })
    }
    return () => { current = false }
  }, [needsRecalculation, thresholdChanged, reactivating, creatingActive, creationPayload, canRecalculateThreshold, retiring, row.id, thresholdValue, form.boss_type])
  const availability = (key: string) => (ready: boolean) =>
    setOptionReady((old) =>
      old[key] === ready ? old : { ...old, [key]: ready }
    )
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      title="配置 VIP 升级权益"
      contentClassName="sm:max-w-4xl"
      confirmDiscard
      readOnly={locked}
      disabled={!couponSettingsValid || Object.values(optionReady).some((ready) => !ready) || (retiring && (!canRetireSafely || !retirement)) ||
        (needsRecalculation && (!canRecalculateThreshold || !recalculation?.executable || !recalculation?.token || (reactivating && thresholdChanged)))}
      description="按本店配置等级权益，服务绑定必须符合权益类型。"
      onClose={onClose}
      onSubmit={async () => {
        const data = {
          ...tierData,
          ...(retiring ? { retirement_token: retirement?.token } : {}),
          ...(needsRecalculation ? { recalculation_token: recalculation?.token } : {}),
        }
        await request({
          url: `/vip-tiers/${row.id ? `${row.id}/` : ""}`,
          method: row.id ? "PATCH" : "POST",
          data,
        })
        toast.success("VIP 权益已保存")
        onSaved()
      }}
    >
      {retiring && <p className="rounded border p-3 text-sm" role="status">
        {!canRetireSafely ? "本店会员权益保护尚未就绪，暂不能停用等级。" : retirementError || (retirement
          ? vipEnabled
            ? `将重新计算 ${retirement.account_count} 位老板的等级，保留 ${retirement.coupon_count} 条已发券记录和原适用条款；其他修改请先单独保存。`
            : `VIP 停用期间先保存等级状态；${retirement.account_count} 位老板将在重新启用时统一核对和重算。`
          : "正在核对受影响的老板和已发券…")}
      </p>}
      {needsRecalculation && !retiring && <p className="rounded border p-3 text-sm" role="status">
        {creatingActive && (!form.boss_type || thresholdValue <= 0) ? "请选择老板分级并填写消费门槛后核对影响。" : reactivating && thresholdChanged ? "启用等级前请先单独保存门槛修改。" : !canRecalculateThreshold ? "等级变更后的会员重算尚未就绪，暂不能保存此修改。" : recalculationError || (recalculation
          ? recalculation.executable
            ? recalculation.deferred
              ? `${recalculation.notice}本次修改预计影响 ${recalculation.affected_count} 位老板，最终以重新启用时的核对为准。`
              : `将重算 ${recalculation.affected_count} 位老板：升级 ${recalculation.upgrade_count} 位、降级 ${recalculation.downgrade_count} 位；新达等级的权益将按配置发放，其他修改请先单独保存。`
            : recalculation.notice
          : "正在核对门槛变更影响…")}
      </p>}
      <div className="grid grid-cols-2 gap-4">
        <OptionPicker
          resource="boss-types"
          onAvailabilityChange={availability("boss-type")}
          label="对应老板分级"
          value={form.boss_type}
          onChange={(value) => setForm((old) => ({ ...old, boss_type: value }))}
        />
        <Field
          field={{
            key: "vip-level",
            label: "VIP 等级",
            type: "number",
            min: 1,
            max: 6,
            step: 1,
          }}
          value={form.level}
          onChange={(value) => setForm((old) => ({ ...old, level: value }))}
        />
        <Field
          field={{
            key: "vip-threshold",
            label: coinText("累计确认消费门槛（浪花）"),
            type: "number",
            min: 0.1,
            step: 0.1,
          }}
          value={form.threshold}
          onChange={(value) => setForm((old) => ({ ...old, threshold: value }))}
        />
        <Field
          field={{ key: "vip-active", label: "启用", type: "boolean" }}
          value={form.active}
          onChange={(value) => setForm((old) => ({ ...old, active: value }))}
        />
        {canConfigurePoints && <Field field={{ key: "vip-points-rate", label: "会员积分倍率（%，100 表示 1 倍）", type: "number", min: 0, max: 1000, step: 1 }} value={form.points_rate ?? 100} onChange={(value) => setForm((old) => ({ ...old, points_rate: value }))} />}
      </div>
      {canConfigurePoints && <p className="text-xs text-muted-foreground">礼物消费按付款时的会员倍率计算积分；服务订单的新版倍率尚待接入。</p>}
      {canConfigureCouponTerms ? <section className="space-y-3">
        <h3 className="font-semibold">月券和安全箱券</h3>
        <div className="grid grid-cols-3 gap-4">
          <Field field={{ key: "vip-monthly-count", label: "每月券数量", type: "number", min: 0, max: 32767, step: 1 }} value={form.monthly_coupon_count} onChange={(value) => setForm((old) => ({ ...old, monthly_coupon_count: value }))} />
          <Field field={{ key: "vip-monthly-discount", label: "月券支付比例（%）", type: "number", min: 1, max: 100, step: 1 }} value={form.monthly_discount} onChange={(value) => setForm((old) => ({ ...old, monthly_discount: value }))} />
          <Field field={{ key: "vip-season-discount", label: "安全箱券支付比例（%）", type: "number", min: 0, max: 100, step: 1 }} value={form.season_discount} onChange={(value) => setForm((old) => ({ ...old, season_discount: value }))} />
        </div>
        <p className="text-xs text-muted-foreground">数量从下次发券生效，本月已发券保留；符合条件的未使用券按新折扣使用，已使用或因降级等原因保留原条款的券不变。安全箱 0% 表示免费。</p>
        {!couponSettingsValid && <p className="text-sm text-destructive" role="alert">请填写有效的整数数量和支付比例。</p>}
      </section> : couponTermsReadState ? <div className="flex items-center gap-2 text-xs text-muted-foreground">月券规则<Status readState={couponTermsReadState} /></div>
        : <p className="text-xs text-muted-foreground">本店月券新规则尚未启用，暂不能在这里配置月券和安全箱折扣。</p>}
      <OptionPicker
        resource="vip-companion"
        onAvailabilityChange={availability("companion")}
        endpoint="/vip-tiers/service-options/"
        query={{ benefit_kind: "COMPANION_HOUR" }}
        label="陪玩小时权益可用套餐"
        multiple
        value={form.companion_service_ids}
        onChange={(value) =>
          setForm((old) => ({ ...old, companion_service_ids: value }))
        }
      />
      <OptionPicker
        resource="vip-safe-box"
        onAvailabilityChange={availability("safe-box")}
        endpoint="/vip-tiers/service-options/"
        query={{ benefit_kind: "SAFEBOX_33" }}
        label="安全箱权益可用套餐"
        multiple
        value={form.safe_box_service_ids}
        onChange={(value) =>
          setForm((old) => ({ ...old, safe_box_service_ids: value }))
        }
      />
      <section className="space-y-3">
        <h3 className="font-semibold">升级权益总额</h3>
        {form.upgrade_rewards.map((item: Row, index: number) => (
          <div
            key={index}
            className="grid grid-cols-[1fr_100px_auto] items-end gap-3"
          >
            <OptionPicker
              resource="rewards"
              onAvailabilityChange={availability(`reward-${index}`)}
              endpoint="/club-rewards/"
              label={`升级权益 ${index + 1}`}
              value={item.reward}
              onChange={(value) =>
                setForm((old) => ({
                  ...old,
                  upgrade_rewards: old.upgrade_rewards.map(
                    (entry: Row, i: number) =>
                      i === index ? { ...entry, reward: value } : entry
                  ),
                }))
              }
            />
            <Field
              field={{
                key: `vip-quantity-${index}`,
                label: "份数",
                type: "number",
                min: 1,
                max: 100,
                step: 1,
              }}
              value={item.quantity}
              onChange={(value) =>
                setForm((old) => ({
                  ...old,
                  upgrade_rewards: old.upgrade_rewards.map(
                    (entry: Row, i: number) =>
                      i === index ? { ...entry, quantity: value } : entry
                  ),
                }))
              }
            />
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                setForm((old) => ({
                  ...old,
                  upgrade_rewards: old.upgrade_rewards.filter(
                    (_: any, i: number) => i !== index
                  ),
                }))
              }
            >
              移除
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            setForm((old) => ({
              ...old,
              upgrade_rewards: [
                ...old.upgrade_rewards,
                { reward: null, quantity: 1 },
              ],
            }))
          }
        >
          添加权益
        </Button>
      </section>
      {Object.keys(form.benefits || {}).map((key) => (
        <Field
          key={key}
          field={{ key: `benefit-${key}`, label: key }}
          value={form.benefits[key]}
          onChange={(value) =>
            setForm((old) => ({
              ...old,
              benefits: { ...old.benefits, [key]: value },
            }))
          }
        />
      ))}
    </MutationDialog>
  )
}
function LotteriesPage() {
  const list = useList("lotteries")
  const auth = useAdminAuth()
  const metadata = useLoad(
    async () =>
      (await request({ url: "/club-rewards/", params: { page_size: 1 } })).data
  )
  const review = useSourceReviewState("/admin/lotteries/")
  const locked =
    list.loading ||
    !!list.error ||
    metadata.loading ||
    !!metadata.error ||
    review.locked
  const [editing, setEditing] = useState<Row | null>(null)
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="lotteries"
    >
      {
        <div className="admin-source-page space-y-4" data-resource="lotteries">
          <SourcePageHeader
            tag="积分活动"
            title="幸运转盘"
            description="先创建暂停的活动，配置完整奖池与概率后启用。修改已有活动必须核对版本。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("engagement:edit") && (
              <Button
                size="sm"
                disabled={locked}
                onClick={() =>
                  setEditing({
                    name: "",
                    point_cost: 0,
                    enabled: false,
                    daily_limit: 0,
                    rules: "",
                    starts_at: "",
                    ends_at: "",
                    prizes: [],
                  })
                }
              >
                创建活动
              </Button>
            )}
          </SourcePageHeader>
          <ReadOnlyNotice
            canEdit={auth.hasPerm("engagement:edit")}
            subject="抽奖活动"
          />
          <Feedback message={list.error} retry={list.reload} />
          {metadata.loading && (
            <p
              role="status"
              className="rounded-lg border bg-muted/20 p-3 text-sm text-muted-foreground"
            >
              正在读取奖品字典…
            </p>
          )}
          <Feedback message={metadata.error} retry={metadata.reload} />
          {list.rows[0] && <CampaignRuleCards campaign={list.rows[0]} />}
          <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
            title="抽奖活动"
            icon={Gift}
            description={`本页 ${list.rows.length} 项 · 按活动编号核对完整奖池`}
            minWidth={1040}
            rows={list.rows}
            error={list.error}
            loading={list.loading}
            filters={
              <AdminFilters primary={<>
                <Field
                  field={{
                    key: "lottery-keyword",
                    label: "活动名称（待开放）",
                    disabled: true,
                  }}
                  value=""
                  onChange={() => {}}
                />
                <Field
                  field={{
                    key: "lottery-status",
                    label: "开放状态（待开放）",
                    type: "select",
                    disabled: true,
                    options: [["", "全部状态"]],
                  }}
                  value=""
                  onChange={() => {}}
                />
              </>} actions={<Button disabled>查询</Button>} />
            }
            columns={[
              {
                key: "name",
                label: "活动",
                render: (row) => (
                  <div className="flex items-start gap-3">
                    <span className="grid size-10 shrink-0 place-items-center rounded-xl border bg-primary/5 text-primary">
                      <Sparkles className="size-5" />
                    </span>
                    <div>
                      <p className="text-sm font-semibold">{row.name}</p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        活动 #{row.id}
                      </p>
                    </div>
                  </div>
                ),
              },
              {
                key: "enabled",
                label: "开放状态 / 时间",
                align: "center",
                render: (row) => {
                  const upcoming =
                    row.starts_at && Date.parse(row.starts_at) > Date.now()
                  const expired =
                    row.ends_at && Date.parse(row.ends_at) <= Date.now()
                  return (
                    <div>
                      <Status
                        readState={typeof row.enabled === "boolean" ? undefined : "unread"}
                        tone={!row.enabled || expired ? "muted" : upcoming ? "warning" : "success"}
                        value={
                          !row.enabled
                            ? "DISABLED"
                            : upcoming
                              ? "WAITING"
                              : expired
                                ? "CLOSED"
                                : "AVAILABLE"
                        }
                        label={
                          !row.enabled
                            ? "已暂停"
                            : upcoming
                              ? "未开始"
                              : expired
                                ? "已结束"
                                : "开放中"
                        }
                      />
                      <p className="mt-1 text-[11px] whitespace-nowrap text-muted-foreground">
                        {row.starts_at
                          ? formatDateTime(row.starts_at)
                          : "立即开始"}
                        <br />
                        {row.ends_at ? formatDateTime(row.ends_at) : "长期开放"}
                      </p>
                    </div>
                  )
                },
              },
              {
                key: "point_cost",
                label: "抽奖消耗",
                align: "right",
                render: (row) => (
                  <div>
                    <p className="font-medium tabular-nums">
                      {row.point_cost} 积分 / 次
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      每日 {row.daily_limit || "不限"}
                      {row.daily_limit ? " 次" : ""}
                    </p>
                  </div>
                ),
              },
              {
                key: "prizes",
                label: "奖项 / 概率",
                align: "right",
                render: (row) => {
                  const total = (row.prizes || []).reduce(
                    (sum: number, prize: Row) =>
                      sum + Number(prize.probability || 0),
                    0
                  )
                  return (
                    <div>
                      <p className="font-medium tabular-nums">
                        {row.prizes?.length || 0} 项
                      </p>
                      <p
                        className={`mt-1 text-[11px] tabular-nums ${total === 10000 ? "text-success" : "text-warning"}`}
                      >
                        合计 {(total / 100).toFixed(2)}%
                      </p>
                    </div>
                  )
                },
              },
              {
                key: "ten_guarantee",
                label: "十连与保底",
                render: (row) => (
                  <div className="space-y-1 text-xs">
                    <p
                      className={
                        row.ten_guarantee?.available
                          ? "text-success"
                          : "text-warning"
                      }
                    >
                      {row.ten_guarantee?.available
                        ? "十连保障可用"
                        : "十连保障待核对"}
                    </p>
                    <p className="text-muted-foreground">
                      {row.pity?.enabled
                        ? `${row.pity.limit} 抽保底`
                        : "未启用保底"}
                    </p>
                  </div>
                ),
              },
              {
                key: "version",
                label: "版本",
                align: "center",
                render: (row) => (
                  <Badge variant="outline">v{row.version}</Badge>
                ),
              },
              {
                key: "actions",
                label: "操作",
                align: "right",
                render: (row) =>
                  auth.hasPerm("engagement:edit") && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={locked}
                      onClick={() => setEditing(row)}
                    >
                      配置奖池
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
          {editing && (
            <LotteryEditor
              row={editing}
              locked={locked}
              onClose={() => setEditing(null)}
              onSaved={list.reload}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function CampaignRuleCards({ campaign }: { campaign: Row }) {
  const prizes: Row[] = campaign.prizes || []
  const total = prizes.reduce(
    (sum, prize) => sum + Number(prize.probability || 0),
    0
  )
  const pity: Row = campaign.pity || {}
  const guarantee: Row = campaign.ten_guarantee || {}
  return (
    <section className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,1fr)]">
      <article className="overflow-hidden rounded-xl border border-primary/20 bg-card shadow-sm">
        <header className="flex items-start justify-between gap-3 p-6 pb-4">
          <div>
            <h2 className="text-base font-semibold">
              活动快照 · {campaign.name}
            </h2>
            <p className="mt-2 text-xs text-muted-foreground">
              #{campaign.id} · 配置版本 v{campaign.version} ·{" "}
              {formatDateTime(campaign.starts_at)} 至{" "}
              {formatDateTime(campaign.ends_at)}
            </p>
          </div>
          <Gift className="size-5 shrink-0 text-primary" />
        </header>
        <div className="grid gap-4 border-t p-6 pt-4 sm:grid-cols-[minmax(0,1fr)_220px]">
          <div>
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="text-[11px] text-muted-foreground">
                  基础概率合计
                </p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">
                  {(total / 100).toFixed(2)}%
                </p>
              </div>
              <p className="text-xs text-muted-foreground">
                单抽{" "}
                <strong className="text-foreground">
                  {campaign.point_cost}
                </strong>{" "}
                积分 · 十连{" "}
                <strong className="text-foreground">
                  {campaign.ten_draw_cost ?? "—"}
                </strong>{" "}
                积分
              </p>
            </div>
            <div
              className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-muted"
              aria-label="基础奖池概率分布"
            >
              {prizes.map((prize, index) => (
                <span
                  key={prize.id}
                  style={{ width: `${Number(prize.probability || 0) / 100}%` }}
                  className={`h-full border-r border-background/60 last:border-0 ${["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5"][index % 5]}`}
                />
              ))}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {prizes.map((prize) => (
                <div
                  key={prize.id}
                  className="flex items-start justify-between gap-2 text-[11px]"
                >
                  <span className="min-w-0 break-words text-muted-foreground">
                    {prize.name}
                  </span>
                  <span className="shrink-0 font-medium tabular-nums">
                    {(Number(prize.probability || 0) / 100).toFixed(2)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-lg border bg-muted/20 p-4">
            <p className="flex items-center gap-2 text-xs font-semibold">
              <Gift className="size-4 text-warning" />
              体验单保底规则
            </p>
            {pity.enabled ? (
              <>
                <p className="mt-3 text-2xl font-semibold tabular-nums">
                  {pity.limit}
                  <span className="ml-1 text-xs font-normal text-muted-foreground">
                    抽
                  </span>
                </p>
                <p className="mt-2 text-xs leading-6 text-muted-foreground">
                  {pity.target_name || pity.target_reward_code}
                </p>
              </>
            ) : (
              <p className="mt-3 text-xs text-muted-foreground">
                保底奖品未配置
              </p>
            )}
          </div>
        </div>
      </article>
      <article className="rounded-xl border bg-card p-6 shadow-sm">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <CheckCircle2 className="size-4 text-primary" />
          十连保障核对
        </h2>
        <dl className="mt-4 grid grid-cols-2 gap-4 text-xs">
          <div>
            <dt className="text-muted-foreground">保障范围</dt>
            <dd className="mt-1 font-medium">
              {guarantee.count ?? "—"} 抽 · 至少{" "}
              {guarantee.min_non_points ?? "—"} 项非积分奖品
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">当前可用</dt>
            <dd
              className={`mt-1 font-medium ${guarantee.available ? "text-success" : "text-warning"}`}
            >
              {guarantee.available ? "可用" : "暂不可用"}
            </dd>
          </div>
        </dl>
        {guarantee.unavailable_reason && (
          <p className="mt-4 rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs leading-6 text-warning">
            {guarantee.unavailable_reason}
          </p>
        )}
      </article>
    </section>
  )
}
function LotteryEditor({
  row,
  locked,
  onClose,
  onSaved,
}: {
  row: Row
  locked: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({
    ...row,
    prizes: (row.prizes || []).map((prize: Row) => ({ ...prize })),
  })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [optionReady, setOptionReady] = useState<Record<number, boolean>>({})
  const total = form.prizes.reduce(
    (sum: number, prize: Row) => sum + Number(prize.probability || 0),
    0
  )
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      confirmDiscard
      readOnly={locked}
      disabled={Object.values(optionReady).some((ready) => !ready)}
      contentClassName="sm:max-w-[1240px]"
      title={row.id ? "配置转盘奖池" : "创建转盘活动"}
      description={
        row.id
          ? `当前版本 ${row.version}。已有中奖记录的奖项不能删除，可将概率设为 0。`
          : "新活动先以暂停状态保存，再配置奖池。"
      }
      onClose={onClose}
      onSubmit={async () => {
        if (!form.name.trim()) throw new Error("请填写活动名称。")
        if (row.id && (form.prizes.length < 2 || form.prizes.length > 20))
          throw new Error("奖项须为 2 至 20 个。")
        const data = {
          name: form.name,
          point_cost: form.point_cost,
          enabled: row.id ? form.enabled : false,
          starts_at: form.starts_at || null,
          ends_at: form.ends_at || null,
          daily_limit: form.daily_limit,
          rules: form.rules,
          ...(row.id
            ? {
                version: row.version,
                prizes: form.prizes.map((prize: Row, index: number) => ({
                  reward: prize.reward,
                  probability: prize.probability,
                  sort_order: index,
                })),
              }
            : {}),
        }
        await request({
          url: row.id ? `/lotteries/${row.id}/configure/` : "/lotteries/",
          method: "POST",
          data,
        })
        toast.success("转盘配置已保存")
        onSaved()
      }}
    >
      <div className="admin-source-editor grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_318px]">
        <div className="space-y-5">
          <FormSection title="活动规则">
            <div className="grid grid-cols-2 gap-4">
              {[
                { key: "name", label: "活动名称", required: true },
                {
                  key: "point_cost",
                  label: "单次消耗积分",
                  type: "number",
                  min: 0,
                },
                {
                  key: "daily_limit",
                  label: "每人每天限次（0 为不限）",
                  type: "number",
                  min: 0,
                },
                {
                  key: "enabled",
                  label: "启用活动",
                  type: "boolean",
                  disabled: !row.id,
                },
                { key: "starts_at", label: "开始时间", type: "datetime-local" },
                { key: "ends_at", label: "结束时间", type: "datetime-local" },
                {
                  key: "rules",
                  label: "活动规则",
                  type: "textarea",
                  full: true,
                },
              ].map((field) =>
                field.type === "boolean" ? (
                  <label
                    key={field.key}
                    className="flex min-h-14 items-center justify-between gap-3 rounded-lg border px-4 py-3"
                  >
                    <span className="text-sm font-medium">{field.label}</span>
                    <Switch name={field.label}
                      aria-label={field.label}
                      checked={Boolean(form[field.key])}
                      disabled={field.disabled}
                      onCheckedChange={(value) =>
                        setForm((old) => ({ ...old, [field.key]: value }))
                      }
                    />
                  </label>
                ) : (
                  <Field
                    key={field.key}
                    field={field as any}
                    value={form[field.key]}
                    onChange={(value) =>
                      setForm((old) => ({ ...old, [field.key]: value }))
                    }
                  />
                )
              )}
            </div>
          </FormSection>
          {row.id && (
            <section className="space-y-4 rounded-xl border p-4">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-base font-medium">奖项配置</h3>
                <Badge
                  variant="outline"
                  className={total === 10000 ? "text-success" : "text-warning"}
                >
                  概率合计 {(total / 100).toFixed(2)}%
                </Badge>
              </div>
              <div className="admin-lottery-prize-head grid grid-cols-[34px_minmax(180px,1fr)_116px_94px_104px] gap-3 border-y bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
                <span>#</span>
                <span>好礼</span>
                <span className="text-right">概率（万分比）</span>
                <span className="text-right">概率</span>
                <span className="text-right">操作</span>
              </div>
              {form.prizes.map((prize: Row, index: number) => (
                <div
                  key={index}
                  className="admin-lottery-prize-row grid grid-cols-[34px_minmax(180px,1fr)_116px_94px_104px] items-end gap-3 rounded-lg border px-3 py-3"
                >
                  <span className="pb-2 text-xs font-medium text-muted-foreground">
                    {index + 1}
                  </span>
                  <OptionPicker
                    onAvailabilityChange={(ready) =>
                      setOptionReady((old) =>
                        old[index] === ready ? old : { ...old, [index]: ready }
                      )
                    }
                    resource="lottery-rewards"
                    endpoint="/club-rewards/"
                    label={`奖项 ${index + 1}`}
                    value={prize.reward}
                    onChange={(value) =>
                      setForm((old) => ({
                        ...old,
                        prizes: old.prizes.map((entry: Row, i: number) =>
                          i === index ? { ...entry, reward: value } : entry
                        ),
                      }))
                    }
                  />
                  <Field
                    field={{
                      key: `probability-${index}`,
                      label: "概率（万分比）",
                      type: "number",
                      min: 0,
                      max: 10000,
                      step: 1,
                    }}
                    value={prize.probability}
                    onChange={(value) =>
                      setForm((old) => ({
                        ...old,
                        prizes: old.prizes.map((entry: Row, i: number) =>
                          i === index ? { ...entry, probability: value } : entry
                        ),
                      }))
                    }
                  />
                  <p className="pb-2 text-right text-sm font-medium tabular-nums">
                    {(Number(prize.probability || 0) / 100).toFixed(2)}%
                  </p>
                  <div className="flex justify-end gap-1">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8"
                      aria-label={`上移奖项 ${index + 1}`}
                      disabled={index === 0}
                      onClick={() =>
                        setForm((old) => {
                          const prizes = [...old.prizes]
                          ;[prizes[index - 1], prizes[index]] = [
                            prizes[index],
                            prizes[index - 1],
                          ]
                          return { ...old, prizes }
                        })
                      }
                    >
                      <ArrowUp className="size-3.5" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8"
                      aria-label={`下移奖项 ${index + 1}`}
                      disabled={index === form.prizes.length - 1}
                      onClick={() =>
                        setForm((old) => {
                          const prizes = [...old.prizes]
                          ;[prizes[index + 1], prizes[index]] = [
                            prizes[index],
                            prizes[index + 1],
                          ]
                          return { ...old, prizes }
                        })
                      }
                    >
                      <ArrowDown className="size-3.5" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8 text-muted-foreground hover:text-destructive"
                      aria-label={`移除奖项 ${index + 1}`}
                      onClick={() => {
                        setOptionReady({})
                        setForm((old) => ({
                          ...old,
                          prizes: old.prizes.filter(
                            (_: unknown, i: number) => i !== index
                          ),
                        }))
                      }}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
              {total !== 10000 && (
                <p
                  className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs leading-5 text-warning"
                  role="status"
                >
                  当前奖池概率合计为 {(total / 100).toFixed(2)}%，启用前须核对为
                  100%。
                </p>
              )}
              <Button
                type="button"
                variant="outline"
                className="w-full border-dashed"
                disabled={form.prizes.length >= 20}
                onClick={() =>
                  setForm((old) => ({
                    ...old,
                    prizes: [...old.prizes, { reward: null, probability: 0 }],
                  }))
                }
              >
                添加奖项
              </Button>
            </section>
          )}
          {row.id && (
            <div className="grid gap-3 md:grid-cols-2">
              <section className="rounded-xl border p-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <Trophy className="size-4 text-warning" />
                  {row.pity?.limit ? `${row.pity.limit} 抽保底` : "体验单保底"}
                </h3>
                <p className="mt-3 text-xs font-medium">
                  {row.pity?.enabled
                    ? row.pity.target_name || row.pity.target_reward_code
                    : "保底目标未配置"}
                </p>
              </section>
              <section className="rounded-xl border p-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <ShieldCheck className="size-4 text-primary" />
                  十连保障
                </h3>
                <p className="mt-3 text-xs font-medium">
                  {row.ten_guarantee?.count ?? "—"} 抽 · 至少{" "}
                  {row.ten_guarantee?.min_non_points ?? "—"} 项非积分奖品
                </p>
                {row.ten_guarantee?.unavailable_reason && (
                  <p className="mt-2 text-[11px] leading-5 text-warning">
                    {row.ten_guarantee.unavailable_reason}
                  </p>
                )}
              </section>
            </div>
          )}
        </div>
        <LotteryDraftPreview form={form} source={row} />
      </div>
    </MutationDialog>
  )
}
function LotteryDraftPreview({ form, source }: { form: Row; source: Row }) {
  const prizes: Row[] = form.prizes || []
  const name = (prize: Row, index: number) =>
    prize.reward === source.prizes?.[index]?.reward && prize.name
      ? prize.name
      : prize.reward
        ? `好礼 #${prize.reward}`
        : "待选奖品"
  const upcoming = form.starts_at && Date.parse(form.starts_at) > Date.now()
  const expired = form.ends_at && Date.parse(form.ends_at) <= Date.now()
  const state = !form.enabled
    ? "活动已暂停"
    : upcoming
      ? "尚未开始"
      : expired
        ? "活动已结束"
        : "活动开放中"
  return (
    <aside
      className="min-w-0 space-y-3 xl:sticky xl:top-0"
      aria-label="老板端幸运转盘预览"
    >
      <p className="text-xs font-medium text-muted-foreground">
        老板端幸运转盘预览
      </p>
      <div className="overflow-hidden rounded-xl border bg-[#f7f8fc] text-[#172033] shadow-sm dark:bg-muted/30 dark:text-foreground">
        <header className="bg-[#10285b] p-4 text-white">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-blue-100/75">积分好礼</span>
            <Badge className="border-white/20 bg-white/10 text-white">
              配置预览
            </Badge>
          </div>
          <h2 className="mt-3 truncate text-xl font-semibold tracking-tight">
            {form.name || "活动名称"}
          </h2>
          <div className="mt-2 flex items-center justify-between text-[11px] text-blue-100/75">
            <span>
              单抽{" "}
              <strong className="ml-1 text-base text-white">
                {form.point_cost || 0}
              </strong>{" "}
              积分
            </span>
            <span>{state}</span>
          </div>
        </header>
        <div className="p-3">
          <div className="rounded-xl border bg-white p-3 text-center dark:bg-card">
            <p className="text-left text-xs font-semibold">每一抽，都有好礼</p>
            <div className="relative mx-auto my-4 size-[184px]">
              <div className="absolute top-[-3px] left-1/2 z-20 size-0 -translate-x-1/2 border-x-[8px] border-t-[18px] border-x-transparent border-t-sky-300" />
              <div
                className="absolute inset-0 rounded-full border-4 border-[#2563eb]/50 shadow-[0_0_36px_-12px_rgba(56,189,248,0.4)]"
                style={{
                  background:
                    "conic-gradient(from -30deg,#172554 0deg 60deg,#1e3a8a 60deg 120deg,#0f172a 120deg 180deg,#1e40af 180deg 240deg,#172554 240deg 300deg,#334155 300deg 360deg)",
                }}
              >
                {prizes.slice(0, 6).map((prize, index) => (
                  <span
                    key={index}
                    className="absolute top-1/2 left-1/2 line-clamp-2 w-[70px] origin-center text-[9px] leading-3 font-medium break-words text-sky-50/85"
                    style={{
                      transform: `translate(-50%,-50%) rotate(${index * 60}deg) translateY(-60px) rotate(${-index * 60}deg)`,
                    }}
                  >
                    {name(prize, index)}
                  </span>
                ))}
              </div>
              <div className="absolute top-1/2 left-1/2 z-10 grid size-[68px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-4 border-white bg-[linear-gradient(145deg,#38bdf8,#2563eb)] text-white shadow-lg dark:border-card">
                <div>
                  <Trophy className="mx-auto size-4" />
                  <p className="mt-1 text-[9px] font-semibold">积分好礼</p>
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled
                className="h-12 flex-col"
              >
                <span>抽 1 次</span>
                <span className="text-[9px] font-normal">
                  {form.point_cost || 0} 积分
                </span>
              </Button>
              <Button
                type="button"
                size="sm"
                disabled
                className="h-12 flex-col bg-[linear-gradient(135deg,#2563eb,#38bdf8)]"
              >
                <span>抽 10 次</span>
                <span className="text-[9px] font-normal text-white/75">
                  {Number(form.point_cost || 0) * 10} 积分
                </span>
              </Button>
            </div>
          </div>
          {source.pity?.enabled && (
            <div className="mt-3 rounded-xl border bg-white p-3 dark:bg-card">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold">体验单保底</span>
                <span className="font-semibold text-warning tabular-nums">
                  {source.pity.limit} 抽
                </span>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {source.pity.target_name || source.pity.target_reward_code}
              </p>
            </div>
          )}
          <div className="mt-3 rounded-xl border bg-white p-3 dark:bg-card">
            <p className="text-xs font-semibold">本期奖池 · 基础概率</p>
            <div className="mt-1 max-h-44 divide-y overflow-y-auto">
              {prizes.map((prize, index) => (
                <div
                  key={index}
                  className="flex min-h-9 items-center justify-between gap-2 py-2 text-[10px]"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Badge
                      variant="secondary"
                      className="w-8 shrink-0 justify-center px-1"
                    >
                      {index + 1}
                    </Badge>
                    <span className="truncate">{name(prize, index)}</span>
                  </span>
                  <span className="shrink-0 text-muted-foreground tabular-nums">
                    {(Number(prize.probability || 0) / 100).toFixed(2)}%
                  </span>
                </div>
              ))}
              {!prizes.length && (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  尚未配置奖项
                </p>
              )}
            </div>
          </div>
          <div className="mt-3 rounded-lg bg-[#10285b]/[0.05] p-3 dark:bg-primary/10">
            <div className="flex gap-2">
              <ShieldCheck className="mt-1 size-3.5 shrink-0 text-primary" />
              <p className="text-[9px] leading-4 text-muted-foreground">
                {form.rules || "尚未填写参与说明"}
              </p>
            </div>
          </div>
        </div>
      </div>
    </aside>
  )
}

function broadcastPublicView(row: Row) {
  if (typeof row.is_visible !== "boolean" || (row.is_visible && row.published_at === undefined))
    return { readState: "unread" as const }
  if (!row.is_visible) return { label: "已隐藏", tone: "muted" as const }
  if (!row.published_at) return { label: "未发布", tone: "primary" as const }
  if (row.starts_at && Date.parse(row.starts_at) > Date.now())
    return { label: "待展示", tone: "primary" as const }
  if (row.expires_at && Date.parse(row.expires_at) <= Date.now())
    return { label: "已到期", tone: "warning" as const }
  return { label: "公开中", tone: "success" as const }
}

function VisibilityRecords({ kind }: { kind: string }) {
  const resource = kind === "feed" ? "broadcasts" : "provider-namings"
  const list = useList(resource)
  const auth = useAdminAuth()
  const review = useSourceReviewState(`/admin/${resource}/`)
  const locked = list.loading || !!list.error || review.locked
  const [editing, setEditing] = useState<Row | null>(null)
  const [form, setForm] = useState<Row>({})
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton={kind === "feed" ? "broadcast-records" : "provider-namings"}
    >
      {
        <div className="admin-source-page space-y-4" data-resource={resource}>
          <SourcePageHeader
            title={kind === "feed" ? "播报记录" : "陪玩冠名"}
            description="管理运营记录的展示状态。"
          >
            <RefreshButton loading={list.loading} reload={list.reload} />
          </SourcePageHeader>
          <ReadOnlyNotice
            canEdit={auth.hasPerm("engagement:edit")}
            subject={kind === "feed" ? "播报记录" : "陪玩冠名"}
          />
          <Feedback message={list.error} retry={list.reload} />
          <SourceDirectoryCard snapshotAvailable={!!list.data} retry={list.reload}
            title={kind === "feed" ? "播报记录" : "冠名记录"}
            minWidth={1040}
            filters={
              kind === "feed" ? (
                <AdminFilters primary={<>
                  <Field
                    field={{
                      key: "broadcast-keyword",
                      label: "正文或编号（待开放）",
                      disabled: true,
                    }}
                    value=""
                    onChange={() => {}}
                  />
                  <Field
                    field={{
                      key: "broadcast-kind",
                      label: "事件（待开放）",
                      type: "select",
                      disabled: true,
                      options: [["", "全部事件"]],
                    }}
                    value=""
                    onChange={() => {}}
                  />
                  <Field
                    field={{
                      key: "broadcast-status",
                      label: "公开状态（待开放）",
                      type: "select",
                      disabled: true,
                      options: [["", "全部状态"]],
                    }}
                    value=""
                    onChange={() => {}}
                  />
                </>} actions={<Button disabled>查询</Button>} />
              ) : undefined
            }
            rows={list.rows}
            error={list.error}
            loading={list.loading}
            columns={
              kind === "feed"
                ? [
                    {
                      key: "content",
                      label: "动态正文",
                      render: (row) => (
                        <div className="flex items-start gap-3">
                          <span className="grid size-10 shrink-0 place-items-center rounded-xl border bg-primary/5 text-primary">
                            <Megaphone className="size-5" />
                          </span>
                          <div>
                            <p className="max-w-sm text-sm leading-6 break-words whitespace-pre-wrap">
                              {row.content}
                            </p>
                            <p className="mt-1 text-[11px] text-muted-foreground">
                              动态 #{row.id} · 排序 {row.sort_order}
                            </p>
                          </div>
                        </div>
                      ),
                    },
                    {
                      key: "kind",
                      label: "播报事件",
                      render: (row) => (
                        <Badge variant="outline">{enumLabel(row.kind)}</Badge>
                      ),
                    },
                    {
                      key: "created_at",
                      label: "创建 / 发布",
                      className: "whitespace-nowrap",
                      render: (row) => (
                        <div className="text-xs leading-5">
                          <p>{formatDateTime(row.created_at)}</p>
                          <p className="text-muted-foreground">
                            发布 {formatDateTime(row.published_at)}
                          </p>
                        </div>
                      ),
                    },
                    {
                      key: "expires_at",
                      label: "展示窗口",
                      className: "whitespace-nowrap",
                      render: (row) => (
                        <div className="text-xs leading-5">
                          <p>
                            {row.starts_at
                              ? formatDateTime(row.starts_at)
                              : "立即展示"}
                          </p>
                          <p className="text-muted-foreground">
                            至{" "}
                            {row.expires_at
                              ? formatDateTime(row.expires_at)
                              : "长期展示"}
                          </p>
                        </div>
                      ),
                    },
                    {
                      key: "public_status",
                      label: "实际公开状态",
                      align: "center",
                      render: (row) => {
                        const view = broadcastPublicView(row)
                        return <Status {...view} />
                      },
                    },
                    {
                      key: "is_visible",
                      label: "前台显示",
                      align: "center",
                      render: (row) => (
                        <div className="flex items-center justify-center gap-2">
                          <Switch name={`动态 #${row.id}前台显示`}
                            aria-label={`动态 #${row.id}前台显示`}
                            checked={row.is_visible}
                            disabled={
                              !auth.hasPerm("engagement:edit") || locked
                            }
                            onCheckedChange={(value) => {
                              setEditing(row)
                              setForm({
                                is_visible: value,
                                sort_order: row.sort_order,
                                expires_at: row.expires_at
                                  ? new Date(
                                      new Date(row.expires_at).getTime() -
                                        new Date(
                                          row.expires_at
                                        ).getTimezoneOffset() *
                                          60000
                                    )
                                      .toISOString()
                                      .slice(0, 16)
                                  : "",
                              })
                            }}
                          />
                          <span className="text-xs text-muted-foreground">
                            {row.is_visible ? "开启" : "关闭"}
                          </span>
                        </div>
                      ),
                    },
                    {
                      key: "actions",
                      label: "操作",
                      align: "right",
                      render: (row) =>
                        auth.hasPerm("engagement:edit") && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={locked}
                            onClick={() => {
                              setEditing(row)
                              setForm({
                                is_visible: row.is_visible,
                                sort_order: row.sort_order,
                                expires_at: row.expires_at
                                  ? new Date(
                                      new Date(row.expires_at).getTime() -
                                        new Date(
                                          row.expires_at
                                        ).getTimezoneOffset() *
                                          60000
                                    )
                                      .toISOString()
                                      .slice(0, 16)
                                  : "",
                              })
                            }}
                          >
                            编辑展示
                          </Button>
                        ),
                    },
                  ]
                : [
                    { key: "provider_name", label: "陪玩" },
                    { key: "sponsor_name", label: "冠名老板" },
                    { key: "title", label: "冠名内容" },
                    {
                      key: "expires_at",
                      label: "到期",
                      render: (row) => formatDateTime(row.expires_at),
                    },
                    {
                      key: "is_visible",
                      label: "展示",
                      render: (row) => (row.is_visible ? "可见" : "隐藏"),
                    },
                    {
                      key: "actions",
                      label: "操作",
                      render: (row) =>
                        auth.hasPerm("engagement:edit") && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setEditing(row)
                              setForm({
                                title: row.title,
                                is_visible: row.is_visible,
                              })
                            }}
                          >
                            编辑展示
                          </Button>
                        ),
                    },
                  ]
            }
          />
          <Pagination snapshotAvailable={!!list.data}
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {editing && (
            <MutationDialog
              confirmDiscard
              dirty={
                kind === "feed"
                  ? form.is_visible !== editing.is_visible ||
                    String(form.sort_order) !== String(editing.sort_order) ||
                    (form.expires_at || "") !==
                      (editing.expires_at
                        ? new Date(
                            new Date(editing.expires_at).getTime() -
                              new Date(editing.expires_at).getTimezoneOffset() *
                                60000
                          )
                            .toISOString()
                            .slice(0, 16)
                        : "")
                  : form.title !== editing.title ||
                    form.is_visible !== editing.is_visible
              }
              contentClassName="sm:max-w-5xl"
              readOnly={locked}
              title="编辑展示记录"
              description={editing.content || editing.title}
              onClose={() => setEditing(null)}
              onSubmit={async () => {
                await request({
                  url: `/${resource}/${editing.id}/`,
                  method: "PATCH",
                  data: {
                    ...form,
                    ...(kind === "feed"
                      ? {
                          expires_at: form.expires_at
                            ? new Date(form.expires_at).toISOString()
                            : null,
                        }
                      : {}),
                  },
                })
                toast.success("展示记录已更新")
                void list.reload()
              }}
            >
              <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
                <div className="space-y-5">
                  {kind === "feed" && (
                    <FormSection title="业务事件快照" className="bg-muted/25">
                      <Status value={editing.kind} />
                      <p className="mt-3 text-sm leading-6 font-medium break-words whitespace-pre-wrap">
                        {editing.content}
                      </p>
                      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                        <div>
                          <dt className="text-muted-foreground">发布时间</dt>
                          <dd className="mt-2">
                            {formatDateTime(editing.published_at)}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">开始展示</dt>
                          <dd className="mt-2">
                            {formatDateTime(editing.starts_at)}
                          </dd>
                        </div>
                      </dl>
                    </FormSection>
                  )}
                  <FormSection title="公开展示设置">
                    {kind === "namings" ? (
                      <Field
                        field={{ key: "naming-title", label: "冠名内容" }}
                        value={form.title}
                        onChange={(value) =>
                          setForm((old) => ({ ...old, title: value }))
                        }
                      />
                    ) : (
                      <>
                        <Field
                          field={{
                            key: "broadcast-order",
                            label: "排序",
                            type: "number",
                          }}
                          value={form.sort_order}
                          onChange={(value) =>
                            setForm((old) => ({ ...old, sort_order: value }))
                          }
                        />
                        <Field
                          field={{
                            key: "broadcast-expires",
                            label: "展示截止",
                            type: "datetime-local",
                          }}
                          value={form.expires_at}
                          onChange={(value) =>
                            setForm((old) => ({ ...old, expires_at: value }))
                          }
                        />
                      </>
                    )}
                    <label className="flex min-h-14 items-center justify-between gap-3 rounded-lg border p-3">
                      <span className="text-sm font-medium">前台显示</span>
                      <Switch name="前台显示"
                        aria-label="前台显示"
                        checked={form.is_visible}
                        onCheckedChange={(value) =>
                          setForm((old) => ({ ...old, is_visible: value }))
                        }
                      />
                    </label>
                  </FormSection>
                </div>
                <aside className="space-y-3">
                  <p className="text-xs font-medium text-muted-foreground">
                    俱乐部动态预览
                  </p>
                  <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
                    <header className="border-b bg-[#10285b] p-5 text-white">
                      <p className="text-[11px] text-blue-100/80">俱乐部动态</p>
                      <h3 className="mt-4 text-xl font-semibold">
                        {editing.title || enumLabel(editing.kind) || "陪玩冠名"}
                      </h3>
                    </header>
                    <div className="space-y-4 p-4">
                      <p className="text-sm leading-6 break-words whitespace-pre-wrap">
                        {editing.content || form.title}
                      </p>
                      {kind === "feed" ? (
                        <Status
                          {...broadcastPublicView({ ...editing, ...form })}
                        />
                      ) : (
                        <Status
                          tone={form.is_visible ? "success" : "muted"}
                          label={form.is_visible ? "显示已开启" : "显示已关闭"}
                          readState={typeof form.is_visible === "boolean" ? undefined : "unread"}
                        />
                      )}
                      <p className="border-t pt-3 text-[11px] text-muted-foreground">
                        截止{" "}
                        {formatDateTime(form.expires_at || editing.expires_at)}
                      </p>
                    </div>
                  </div>
                </aside>
              </div>
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}
function ExternalPoints() {
  const [form, setForm] = useState<Row>({
    account_id: null,
    kind: "KOOK_ONLINE",
    day: new Date().toLocaleDateString("sv-SE"),
    online_seconds: 1,
    source_id: "",
  })
  const [reviewed, setReviewed] = useState(false)
  const [accountReady, setAccountReady] = useState(false)
  const fields: FormField[] = [
    {
      key: "kind",
      label: "来源类型",
      type: "select",
      options: [
        ["KOOK_ONLINE", "KOOK 在线"],
        ["KOOK_CHECKIN", "KOOK 签到"],
      ],
    },
    { key: "day", label: "业务日期", type: "date" },
    {
      key: "online_seconds",
      label: "在线秒数",
      type: "number",
      min: 1,
      max: 86400,
    },
    { key: "source_id", label: "唯一来源编号", required: true },
  ]
  return (
    <div className="space-y-4">
      <SourcePageHeader
        title="外部积分登记"
        description="登记真实来源的 KOOK 在线或签到积分，同一来源编号用于避免重复入账。"
      />
      <MutationInline
        title="登记外部积分"
        reviewPrefix="/admin/external-points/"
        disabled={!accountReady}
        submitDisabled={!reviewed || !form.account_id || !form.source_id.trim()}
        fields={fields}
        renderFields={(locked) => (
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <OptionPicker
                resource="users"
                onAvailabilityChange={setAccountReady}
                endpoint="/users/"
                label="接收积分的老板账号"
                allLabel="请选择老板账号"
                value={form.account_id}
                disabled={locked}
                onChange={(value) => {
                  setForm((old) => ({ ...old, account_id: value }))
                  setReviewed(false)
                }}
              />
            </div>
            {fields.map((field) => (
              <Field key={field.key} field={field} disabled={locked} value={form[field.key]} onChange={(value) => {
                setForm({ ...form, [field.key]: value })
                setReviewed(false)
              }} />
            ))}
          </div>
        )}
        form={form}
        onChange={(value) => {
          setForm(value)
          setReviewed(false)
        }}
        onSubmit={async () => {
          if (!reviewed || !form.account_id || !form.source_id.trim())
            throw new Error("请核对接收账号、来源记录与唯一编号。")
          const result = await request({
            url: "/external-points/",
            method: "POST",
            data: form,
          })
          toast.success(`已登记 ${result.data.points} 积分`)
        }}
      >
        <label className="flex gap-2 text-sm">
          <input
            type="checkbox"
            className="size-[18px] shrink-0 accent-primary"
            checked={reviewed}
            onChange={(event) => setReviewed(event.target.checked)}
          />
          已核对外部来源、账号、日期和记录编号
        </label>
      </MutationInline>
    </div>
  )
}
function MutationInline({
  title,
  fields,
  form,
  onChange,
  onSubmit,
  children,
  disabled,
  sections,
  renderFields,
  unframed = false,
  submitDisabled = false,
  onReset,
  validate,
  confirmation,
  reviewPrefix,
}: {
  title: string
  fields: FormField[]
  form: Row
  onChange: (form: Row) => void
  onSubmit: () => Promise<void>
  children?: React.ReactNode
  disabled?: boolean
  sections?: Array<{ title: string; keys: string[] }>
  renderFields?: (locked: boolean) => React.ReactNode
  unframed?: boolean
  submitDisabled?: boolean
  onReset?: () => void
  validate?: () => string
  confirmation?: React.ReactNode
  reviewPrefix?: string
}) {
  const pageState = usePageDataState()
  const review = useSourceReviewState(
    reviewPrefix || "/admin/no-inline-mutation/"
  )
  disabled = disabled || review.locked
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [confirming, setConfirming] = useState(false)
  const [resetting, setResetting] = useState(false)
  return (
    <section
      className={
        unframed
          ? "space-y-4"
          : "space-y-5 rounded-xl border bg-card p-6 text-sm leading-5 shadow-sm"
      }
    >
      <fieldset
        disabled={busy || disabled}
        className="admin-source-editor min-w-0 space-y-5"
      >
        {renderFields
          ? renderFields(!!busy || !!disabled)
          : (
              sections || [
                { title: "配置内容", keys: fields.map((field) => field.key) },
              ]
            ).map((group) => (
              <FormSection key={group.title} title={group.title}>
                <div className="grid grid-cols-2 gap-4">
                  {fields
                    .filter((field) => group.keys.includes(field.key))
                    .map((field) => (
                      <Field
                        key={field.key}
                        field={field}
                        disabled={busy || disabled}
                        value={form[field.key]}
                        onChange={(value) =>
                          onChange({ ...form, [field.key]: value })
                        }
                      />
                    ))}
                </div>
              </FormSection>
            ))}
        {children}
      </fieldset>
      <Feedback message={error} />
      <div
        className={
          unframed
            ? "flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4 shadow-sm"
            : "flex items-center justify-end gap-2"
        }
      >
        {unframed && (
          <p className="text-sm font-medium">
            {pageState && !pageState.hasSnapshot ? (pageState.loading ? "配置读取中" : "配置未读取") : busy
              ? "正在保存配置"
              : submitDisabled
                ? "配置已与读取快照一致"
                : "有未保存的修改"}
          </p>
        )}
        <div className="flex gap-2">
          {onReset && (
            <Button
              variant="outline"
              disabled={busy || disabled || submitDisabled}
              onClick={() => setResetting(true)}
            >
              重置
            </Button>
          )}
          <Button
            disabled={busy || disabled || submitDisabled}
            onClick={() => {
              const message = validate?.() || ""
              setError(message)
              if (!message) setConfirming(true)
            }}
          >
            {title}
          </Button>
        </div>
      </div>
      {resetting && onReset && (
        <MutationDialog
          title="重置未保存的修改？"
          description="当前字段将恢复为最近一次成功读取的配置快照，不会修改已保存配置。"
          submitText="确认重置"
          submitVariant="destructive"
          onClose={() => setResetting(false)}
          onSubmit={async () => onReset()}
        />
      )}
      {confirming && (
        <MutationDialog
          title={title}
          description="核对当前修改后确认保存。"
          onClose={() => setConfirming(false)}
          readOnly={disabled}
          onSubmit={async () => {
            setBusy(true)
            setError("")
            try {
              await onSubmit()
            } catch (cause: any) {
              setError(cause.message)
              throw cause
            } finally {
              setBusy(false)
            }
          }}
        >
          {confirmation}
        </MutationDialog>
      )}
    </section>
  )
}
function OperationConfig() {
  const auth = useAdminAuth()
  const result = useLoad(
    async () => (await request({ url: "/club-config/" })).data
  )
  const [form, setForm] = useState<Row>({})
  const [saveBlocked, setSaveBlocked] = useState(false)
  const [resumePreview, setResumePreview] = useState<Row | null>(null)
  const [resumeError, setResumeError] = useState("")
  useEffect(() => {
    if (result.data) {
      setForm({
        ...result.data,
        vip_min_single_recharge: Number(
          amountToXaCoin(result.data.vip_min_single_recharge)
        ),
      })
      setSaveBlocked(false)
    }
  }, [result.data])
  const resuming = result.data?.vip_enabled === false && form.vip_enabled === true &&
    result.data?.vip_threshold_recalculation_enabled === true
  useEffect(() => {
    let current = true
    setResumePreview(null)
    setResumeError("")
    if (resuming) {
      request({ url: "/club-config/vip-resume-preview/" })
        .then((response) => { if (current) setResumePreview(response.data) })
        .catch((error) => { if (current) setResumeError(errorMessage(error)) })
    }
    return () => { current = false }
  }, [resuming])
  const dirty =
    !!result.data &&
    [
      "points_enabled",
      "vip_enabled",
      "birthday_multiplier",
      "base_points_rate",
      "season_code",
      "season_ends_at",
    ].some((key) => String(form[key] ?? "") !== String(result.data[key] ?? ""))
  const validate = () => {
    if (
      !Number.isInteger(Number(form.birthday_multiplier)) ||
      Number(form.birthday_multiplier) < 1 ||
      Number(form.birthday_multiplier) > 10
    )
      return "生日消费倍率须为 1–10 的整数。"
    if (typeof result.data?.base_points_rate === "number" && (!Number.isInteger(Number(form.base_points_rate)) || Number(form.base_points_rate) < 0 || Number(form.base_points_rate) > 1000))
      return "普通会员积分倍率须为 0–1000 的整数百分比。"
    if (String(form.season_code || "").trim().length > 40)
      return "赛季编号最多 40 个字符。"
    if (String(form.season_code || "").trim() && !form.season_ends_at)
      return "填写赛季编号时，请同时设置截止时间。"
    return ""
  }
  const changedFields = [
    ["points_enabled", "消费积分"],
    ["birthday_multiplier", "生日消费倍率"],
    ...(typeof result.data?.base_points_rate === "number" ? [["base_points_rate", "普通会员积分倍率（%）"]] : []),
    ["vip_enabled", "VIP 自动升级"],
    ["season_code", "当前赛季编号"],
    ["season_ends_at", "赛季截止时间"],
  ].filter(
    ([key]) => String(form[key] ?? "") !== String(result.data?.[key] ?? "")
  )
  const displayChange = (key: string, value: any) =>
    key === "season_ends_at"
      ? formatDateTime(value)
      : key.endsWith("enabled")
        ? value
          ? "开启"
          : "关闭"
        : key === "birthday_multiplier"
          ? `${value || "—"} 倍`
          : String(value || "未设置")
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="operation-config"
    >
      {
        <div className="space-y-4">
          <SourcePageHeader tag="会员与积分" title="会员与积分设置">
            <RefreshButton loading={result.loading} reload={result.reload} />
          </SourcePageHeader>
          <Feedback message={result.error} retry={result.reload} />
          <ReadOnlyNotice
            canEdit={auth.hasPerm("engagement:edit")}
            subject="运营设置"
          />
          {saveBlocked && (
            <Feedback
              message="保存结果待核对，请重新读取当前配置后再操作。"
              retry={result.reload}
            />
          )}
          {resuming && <p className="rounded border p-3 text-sm" role="status">
            {resumeError || (resumePreview
              ? `启用后重算 ${resumePreview.affected_count} 位老板；${resumePreview.baseline_known ? `新达等级权益 ${resumePreview.candidate_upgrade_rewards} 项将按规则发放${resumePreview.manual_review_count ? `，另有 ${resumePreview.manual_review_count} 项待核对` : ""}` : `${resumePreview.manual_review_count} 项历史权益需要人工核对，不会自动补发`}。`
              : "正在核对 VIP 重新启用的影响…")}
          </p>}
          {<>
              <MutationInline
                unframed
                reviewPrefix="/admin/club-config/"
                validate={validate}
                confirmation={
                  <div className="overflow-hidden rounded-lg border">
                    {result.data?.vip_enabled && !form.vip_enabled && result.data?.vip_coupon_protection_enabled &&
                      <p className="border-b p-3 text-xs">停用时固化本店未用券的当前适用条款；已发且未过期的券继续有效。</p>}
                    {resuming && resumePreview && <p className="border-b p-3 text-xs">
                      {resumePreview.baseline_known ? `将重算 ${resumePreview.affected_count} 位老板并按新达等级发放权益。` : `将重算 ${resumePreview.affected_count} 位老板，${resumePreview.manual_review_count} 项历史权益列入人工核对。`}
                    </p>}
                    {changedFields.map(([key, label], index) => (
                      <div
                        key={key}
                        className={`grid grid-cols-[112px_minmax(0,1fr)] gap-x-4 gap-y-1 p-3 text-xs ${index ? "border-t" : ""}`}
                      >
                        <span className="row-span-2 font-medium">{label}</span>
                        <span className="text-muted-foreground line-through decoration-muted-foreground/40">
                          {displayChange(key, result.data?.[key])}
                        </span>
                        <span className="font-medium text-foreground">
                          {displayChange(key, form[key])}
                        </span>
                      </div>
                    ))}
                  </div>
                }
                submitDisabled={!dirty || (resuming && !resumePreview?.token)}
                renderFields={(locked) => (
                  <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                    <OperationConfigFields
                      form={form}
                      known={!!result.data}
                      disabled={locked}
                      onChange={setForm}
                    />
                    <aside className="flex min-w-0 flex-col gap-4">
                      <Card className="overflow-hidden">
                        <div className="bg-[#10285b] p-5 text-white">
                          <div className="flex items-center justify-between gap-3">
                            <span className="text-xs text-blue-100/75">
                              新订单积分试算
                            </span>
                            <Badge className="border-white/20 bg-white/10 text-white">
                              待开放
                            </Badge>
                          </div>
                          <div className="mt-5 flex items-end gap-2">
                            <strong className="text-4xl font-semibold tracking-[-0.04em]">
                              —
                            </strong>
                            <span className="pb-1 text-sm text-blue-100/75">
                              积分
                            </span>
                          </div>
                        </div>
                        <CardContent>
                          <Button variant="outline" disabled className="w-full">
                            积分试算 · 待开放
                          </Button>
                        </CardContent>
                      </Card>
                      <Card>
                        <CardHeader>
                          <CardTitle className="flex items-center gap-2 text-base">
                            <Settings2 className="size-4 text-primary" />
                            保存前核对
                          </CardTitle>
                          <CardDescription>
                            当前配置对老板端的业务含义
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4 pt-0 text-xs leading-5">
                          <div className="flex gap-3">
                            <Sparkles className="mt-1 size-4 shrink-0 text-primary" />
                            <p>
                              消费积分<DataValue>{form.points_enabled ? "开启" : "关闭"}</DataValue>
                              ，生日消费倍率 <DataValue>{form.birthday_multiplier ||
                                "—"}</DataValue>{" "}
                              倍。
                            </p>
                          </div>
                          <div className="flex gap-3">
                            <Crown className="mt-1 size-4 shrink-0 text-primary" />
                            <p>
                              VIP 自动升级
                              <DataValue>{form.vip_enabled
                                ? "开启，达到启用等级门槛时执行"
                                : "关闭，不自动升级或发放周期权益"}</DataValue>
                              。
                            </p>
                          </div>
                          <div className="flex gap-3">
                            <Settings2 className="mt-1 size-4 shrink-0 text-primary" />
                            <p>
                              <DataValue>{form.season_code
                                ? `当前赛季 ${form.season_code}，截止 ${form.season_ends_at ? formatDateTime(form.season_ends_at) : "未设置"}。`
                                : "未设置当前赛季编号。"}</DataValue>
                            </p>
                          </div>
                        </CardContent>
                      </Card>
                    </aside>
                  </div>
                )}
                onReset={() =>
                  result.data &&
                  setForm({
                    ...result.data,
                    vip_min_single_recharge: Number(
                      amountToXaCoin(result.data.vip_min_single_recharge)
                    ),
                  })
                }
                sections={[
                  {
                    title: "积分与会员",
                    keys: [
                      "points_enabled",
                      "vip_enabled",
                      "birthday_multiplier",
                      ...(typeof result.data?.base_points_rate === "number" ? ["base_points_rate"] : []),
                      "vip_min_single_recharge",
                      "coupon_stacking",
                    ],
                  },
                  {
                    title: "当前赛季",
                    keys: ["season_code", "season_ends_at"],
                  },
                ]}
                title="保存运营设置"
                fields={[
                  { key: "points_enabled", label: "启用积分", type: "boolean" },
                  { key: "vip_enabled", label: "启用 VIP", type: "boolean" },
                  {
                    key: "birthday_multiplier",
                    label: "生日积分倍率",
                    type: "number",
                    min: 1,
                    max: 10,
                  },
                  ...(typeof result.data?.base_points_rate === "number" ? [{ key: "base_points_rate", label: "普通会员积分倍率（%，100 表示 1 倍）", type: "number" as const, min: 0, max: 1000 }] : []),
                  {
                    key: "vip_min_single_recharge",
                    label: coinText("VIP 单次充值门槛（浪花，按既定规则）"),
                    type: "number",
                    min: 0,
                    disabled: true,
                  },
                  {
                    key: "coupon_stacking",
                    label: "优惠券叠加（按既定规则）",
                    type: "boolean",
                    disabled: true,
                  },
                  { key: "season_code", label: "赛季标识" },
                  {
                    key: "season_ends_at",
                    label: "赛季结束时间",
                    type: "datetime-local",
                  },
                ]}
                form={form}
                onChange={setForm}
                disabled={
                  !auth.hasPerm("engagement:edit") ||
                  !result.data ||
                  result.loading ||
                  !!result.error ||
                  saveBlocked
                }
                onSubmit={async () => {
                  const { updated_at, vip_coupon_protection_enabled, vip_threshold_recalculation_enabled, vip_coupon_terms_config_enabled, vip_deferred, ...data } = form
                  void updated_at; void vip_coupon_protection_enabled; void vip_threshold_recalculation_enabled; void vip_coupon_terms_config_enabled; void vip_deferred
                  if (
                    !Number.isInteger(Number(data.birthday_multiplier)) ||
                    Number(data.birthday_multiplier) < 1 ||
                    Number(data.birthday_multiplier) > 10
                  )
                    throw new Error("生日消费倍率须为 1–10 的整数。")
                  if (
                    String(data.season_code || "").trim() &&
                    !data.season_ends_at
                  )
                    throw new Error("填写赛季编号时，请同时设置截止时间。")
                  let pendingReviews = 0
                  try {
                    const saved = await request({
                      url: "/club-config/",
                      method: "PUT",
                      data: {
                        ...data,
                        ...(resuming ? { vip_resume_token: resumePreview?.token } : {}),
                        vip_min_single_recharge: Math.round(
                          data.vip_min_single_recharge * 10
                        ),
                        season_ends_at: data.season_ends_at || null,
                      },
                    })
                    pendingReviews = Number(saved.data?.vip_resume?.manual_review_count || 0)
                  } catch (cause: any) {
                    if (
                      !cause.status ||
                      cause.status >= 500 ||
                      [408, 409, 429].includes(cause.status)
                    )
                      setSaveBlocked(true)
                    throw cause
                  }
                  toast.success(pendingReviews ? `运营设置已保存，${pendingReviews} 项权益请在 VIP 等级页面核对` : "运营设置已保存")
                  void result.reload()
                }}
              />
            </>}
        </div>
      }
    </DataPageState>
  )
}
function KookStatus() {
  const result = useLoad(
    async () => (await request({ url: "/kook-points-status/" })).data
  )
  const data = result.data
  const enabled = typeof data?.enabled === "boolean" ? data.enabled : null
  const connected = typeof data?.connected === "boolean" ? data.connected : null
  const missing = !data && result.loading ? "loading" : !data && result.error ? "error" : "unread"
  const states = [
    { label: "启用状态", icon: Settings2, value: <Status value={enabled} readState={enabled === null ? missing : undefined} />, note: "本店 KOOK 积分接入开关" },
    { label: "连接状态", icon: Link2, value: <Status value={connected ? "CONNECTED" : "DISCONNECTED"} tone={connected ? "success" : enabled ? "warning" : "muted"} readState={connected === null ? missing : undefined} />, note: "以服务端最近心跳校验为准" },
    { label: "最近运行记录", icon: Clock3, value: data?.status === undefined ? <Status readState={missing} /> : <span>{data.status || "暂无运行记录"}</span>, note: "最近上报内容，当前连接见左侧" },
  ]
  const metrics = [
    { label: "语音频道", value: data?.channel_count ?? "—", unit: "个", icon: Headphones },
    { label: "绑定会员", value: data?.binding_count ?? "—", unit: "人", icon: UserCheck },
    { label: "绑定陪玩", value: data?.provider_count ?? "—", unit: "人", icon: Headphones },
    { label: "最近心跳", value: data?.heartbeat_at ? formatDateTime(data.heartbeat_at) : data?.heartbeat_at === null ? "暂无心跳" : "—", unit: "", icon: Clock3 },
  ]
  return (
    <DataPageState loading={result.loading} hasSnapshot={!!data} error={result.error} retry={result.reload} skeleton="kook-status">
      <div className="space-y-4">
        <SourcePageHeader title="KOOK 积分状态">
          <RefreshButton loading={result.loading} reload={result.reload} />
        </SourcePageHeader>
        <section className="admin-kook-overview" aria-labelledby="kook-overview-title" aria-busy={result.loading}>
          <header className="admin-kook-heading">
            <h2 id="kook-overview-title"><Link2 className="size-4 text-primary" aria-hidden="true" />接入运行概况</h2>
            <span>本店接入与采集快照</span>
          </header>
          <dl className="admin-kook-states">
            {states.map(item => <div key={item.label}>
              <dt><item.icon className="size-4" aria-hidden="true" />{item.label}</dt>
              <dd>{item.value}</dd>
              <p>{item.note}</p>
            </div>)}
          </dl>
          <dl className="admin-kook-metrics">
            {metrics.map(item => <div key={item.label}>
              <dt><item.icon className="size-4" aria-hidden="true" />{item.label}</dt>
              <dd><DataValue>{item.value}</DataValue>{item.unit && <small>{item.unit}</small>}</dd>
            </div>)}
          </dl>
        </section>
        <section className="admin-kook-rules" aria-labelledby="kook-rules-title">
          <h2 id="kook-rules-title"><ListFilter className="size-4 text-primary" aria-hidden="true" />计分规则</h2>
          <p><DataValue>{data?.rules || "—"}</DataValue></p>
        </section>
      </div>
    </DataPageState>
  )
}

export function CheckinPage() {
  const auth = useAdminAuth()
  const [tab, setTab] = useState("rules")
  const config = useLoad(
    async () => (await request({ url: "/config/checkin" })).data
  )
  const [form, setForm] = useState<Row>({})
  const [month, setMonth] = useState<Row>({
    year: new Date().getFullYear(),
    month: new Date().getMonth() + 1,
  })
  const progress = useList("checkin-progress", month)
  useLiveReload(progress.reload)
  useEffect(() => {
    if (config.data)
      setForm({
        ...config.data,
        daily_coin: config.data.daily_spend_required == null
          ? ""
          : Number(config.data.daily_spend_required) / 10,
        makeup_coin: config.data.makeup_card_spend_required == null
          ? ""
          : Number(config.data.makeup_card_spend_required) / 10,
      })
  }, [config.data])
  const giftSpec: ResourceSpec = {
    resource: "checkin-gifts",
    title: "签到礼物",
    description: "按月签到天数配置礼物与钱包奖励。",
    permission: "checkin",
    deletePermission: "checkin:edit",
    defaults: {
      checkin_day: 1,
      name: "",
      description: "",
      icon: "",
      reward_amount: 0,
      is_active: true,
    },
    money: ["reward_amount"],
    fields: [
      {
        key: "checkin_day",
        label: "签到天数",
        type: "number",
        min: 1,
        max: 31,
      },
      { key: "name", label: "礼物名称", required: true },
      { key: "description", label: "礼物说明", type: "textarea", full: true },
      { key: "icon", label: "图标" },
      {
        key: "reward_amount",
        label: coinText("奖励（浪花）"),
        type: "number",
        min: 0,
        step: 0.1,
      },
      enabled,
    ],
    columns: [
      { key: "checkin_day", label: "签到天数" },
      { key: "name", label: "礼物" },
      { key: "description", label: "说明" },
      {
        key: "reward_amount",
        label: "奖励",
        render: (row) => `${amountToXaCoin(row.reward_amount)} ${coinUnit()}`,
      },
      {
        key: "is_active",
        label: "启用",
        render: (row) => (row.is_active ? "是" : "否"),
      },
    ],
  }
  return (
    <DataPageState
      loading={
        tab === "rules"
          ? config.loading
          : tab === "progress"
            ? progress.loading
            : false
      }
      hasSnapshot={
        tab === "gifts" || !!(tab === "rules" ? config.data : progress.data)
      }
      error={
        tab === "rules"
          ? config.error
          : tab === "progress"
            ? progress.error
            : ""
      }
      retry={() => {
        if (tab === "rules") void config.reload()
        else void progress.reload()
      }}
      skeleton={tab === "rules" ? "checkin-rules" : "checkin-progress"}
    >
      {
        <div className="space-y-4">
          <div className="flex gap-2 border-b pb-4">
            {[
              ["rules", "签到规则"],
              ["gifts", "签到礼物"],
              ["progress", "月度进度"],
            ].map(([key, label]) => (
              <Button
                key={key}
                size="sm"
                variant={tab === key ? "secondary" : "ghost"}
                onClick={() => setTab(key)}
              >
                {label}
              </Button>
            ))}
          </div>
          {tab === "gifts" ? (
            <ResourcePage spec={giftSpec} />
          ) : tab === "progress" ? (
            <>
              <SourcePageHeader title="月度签到进度">
                <RefreshButton
                  loading={progress.loading}
                  reload={progress.reload}
                />
              </SourcePageHeader>
              <Feedback message={progress.error} retry={progress.reload} />
              <SourceDirectoryCard snapshotAvailable={!!progress.data} retry={progress.reload}
                filters={
                  <AdminFilters primary={<>
                    <Field
                      field={{
                        key: "checkin-year",
                        label: "年份",
                        type: "number",
                        min: 2024,
                        max: 2100,
                      }}
                      value={month.year}
                      onChange={(value) =>
                        setMonth((old) => ({ ...old, year: value }))
                      }
                    />
                    <Field
                      field={{
                        key: "checkin-month",
                        label: "月份",
                        type: "number",
                        min: 1,
                        max: 12,
                      }}
                      value={month.month}
                      onChange={(value) =>
                        setMonth((old) => ({ ...old, month: value }))
                      }
                    />
                  </>} />
                }
                title="月度签到进度"
                icon={ListFilter}
                rows={progress.rows}
                loading={progress.loading}
                columns={[
                  { key: "nickname", label: "会员" },
                  { key: "username", label: "账号" },
                  { key: "checked_count", label: "已签到天数" },
                  { key: "makeup_cards", label: "补签卡" },
                  {
                    key: "full_attendance_awarded",
                    label: "全勤已发放",
                    render: (row) =>
                      row.full_attendance_awarded ? "是" : "否",
                  },
                  { key: "full_attendance_reward_name", label: "全勤权益" },
                  {
                    key: "updated_at",
                    label: "更新时间",
                    render: (row) => formatDateTime(row.updated_at),
                  },
                ]}
              />
              <Pagination snapshotAvailable={!!progress.data}
                page={progress.page}
                total={progress.total}
                loading={progress.loading}
                onChange={progress.setPage}
                pageSize={progress.pageSize}
                onPageSizeChange={progress.setPageSize}
              />
            </>
          ) : (
            <>
              <SourcePageHeader
                title="签到规则"
                description="签到与补签门槛按实际消费核算。"
              >
                <RefreshButton
                  loading={config.loading}
                  reload={config.reload}
                />
              </SourcePageHeader>
              <Feedback message={config.error} retry={config.reload} />
              {<MutationInline
                  title="保存签到规则"
                  reviewPrefix="/admin/config/checkin"
                  fields={[
                    {
                      key: "daily_coin",
                      label: coinText("每日签到消费门槛（浪花）"),
                      type: "number",
                      min: 1,
                      step: 0.1,
                    },
                    {
                      key: "makeup_coin",
                      label: coinText("补签卡消费门槛（浪花）"),
                      type: "number",
                      min: 1,
                      step: 0.1,
                    },
                    {
                      key: "max_makeup_cards",
                      label: "补签卡上限",
                      type: "number",
                      min: 1,
                      max: 31,
                    },
                    {
                      key: "full_attendance_reward_name",
                      label: "全勤权益名称",
                    },
                    {
                      key: "full_attendance_reward_desc",
                      label: "全勤权益说明",
                      type: "textarea",
                    },
                    { key: "full_attendance_tag_code", label: "全勤 TAG 标识" },
                  ]}
                  form={form}
                  onChange={setForm}
                  disabled={
                    !auth.hasPerm("checkin:edit") ||
                    !config.data ||
                    config.loading ||
                    !!config.error
                  }
                  onSubmit={async () => {
                    if (form.makeup_coin < form.daily_coin)
                      throw new Error("补签门槛不能低于签到门槛。")
                    const data: Row = {
                      ...form,
                      daily_spend_required: Math.round(form.daily_coin * 10),
                      makeup_card_spend_required: Math.round(
                        form.makeup_coin * 10
                      ),
                    }
                    delete data.daily_coin
                    delete data.makeup_coin
                    await request({
                      url: "/config/checkin",
                      method: "PUT",
                      data,
                    })
                    toast.success("签到规则已保存")
                    void config.reload()
                  }}
                />}
            </>
          )}
        </div>
      }
    </DataPageState>
  )
}
