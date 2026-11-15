import { coinUnit } from "@xa/shared"
import { getStoreSlug, createPaginationController } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { Coins, Gift, Package, RefreshCw, TicketCheck } from "lucide-react"
import { useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  EmptyState,
  Status,
  FilterBar,
  DetailGrid,
  Confirm,
  Field,
} from "@/components/provider-ui"
import {
  api,
  useLoad,
  useRefresh,
  money,
  date,
  message,
  uncertain,
  requestId,
  useViewGuard,
  readStored,
  type Failure,
} from "@/lib/provider"

type Binding = {
  enabled: boolean
  connected: boolean
  bound: boolean
  nickname: string
  online_seconds: number
  online_points: number
  checked_in: boolean
  rules: string
}
type Reward = {
  id: number
  name: string
  kind: string
  point_cost: number
  description: string
  stock: number | null
  balance_amount: number
  point_value: number
  duration_days: number
  duration_months: number
  highlighted: boolean
  service: number | null
}
type Claim = {
  id: number
  name_snapshot: string
  kind_snapshot: string
  tag_reviewed: boolean | null
  tag_expired: boolean
  tag_revision_required: boolean
  tag_editable: boolean
  tag_revision_submitted: boolean
  expires_at: string | null
  tag_suspension_reason: string
  points_spent: number
  points_returned: boolean
  status: string
  status_label: string
  delivery_note: string
  details: Record<string, string>
}
type PointRow = {
  id: number
  amount: number
  balance_after: number
  remark: string
  created_at: string
}
type Pending = {
  reward_id: number
  name: string
  client_request_id: string
  details: Record<string, string>
  expected_point_cost: number
  expected_terms: ReturnType<typeof terms>
}
const terms = (row: Reward) => ({
  name: row.name,
  kind: row.kind,
  description: row.description || "",
  balance_amount: row.balance_amount || 0,
  point_value: row.point_value || 0,
  duration_days: row.duration_days || 0,
  duration_months: row.duration_months || 0,
  highlighted: !!row.highlighted,
  service_id: row.service || null,
})
const fields: Record<string, { key: string; label: string; max: number }[]> = {
  TAG: [{ key: "tag", label: "希望展示的TAG", max: 30 }],
  NAMING: [{ key: "provider_name", label: "希望冠名的陪玩", max: 50 }],
  PHYSICAL: [
    { key: "recipient", label: "收件人", max: 50 },
    { key: "phone", label: "联系电话", max: 20 },
    { key: "address", label: "完整收货地址", max: 255 },
  ],
  SERVICE: [{ key: "game_uid", label: "游戏UID", max: 64 }],
  VIRTUAL: [{ key: "game_uid", label: "游戏UID", max: 64 }],
}
const kinds: Record<string, string> = {
  PHYSICAL: "实物寄送",
  VIRTUAL: "游戏内交付",
  SERVICE: "服务权益",
  NAMING: "冠名权益",
  TAG: "自定义TAG",
  BALANCE: "余额兑换",
  POINTS: "积分",
}
function rewardSummary(row: Reward) {
  return [
    row.description,
    row.kind === "BALANCE" && `到账 ${money(row.balance_amount)} ${coinUnit()}`,
    row.kind === "POINTS" && `获得 ${row.point_value} 积分`,
    row.duration_months && `${row.duration_months} 个月`,
    row.duration_days && `${row.duration_days} 天`,
    row.kind === "TAG" && row.highlighted && "高亮TAG",
  ]
    .filter(Boolean)
    .join(" · ")
}
function RewardArtwork({ row }: { row: Reward }) {
  const Icon =
    row.kind === "BALANCE" || row.kind === "POINTS"
      ? Coins
      : row.kind === "PHYSICAL"
        ? Package
        : row.kind === "VIRTUAL" || row.kind === "SERVICE"
          ? TicketCheck
          : Gift
  return (
    <div className="relative aspect-[4/3] overflow-hidden bg-gradient-to-br from-blue-50 via-sky-100 to-blue-200">
      <span
        aria-hidden="true"
        className="absolute -right-8 -top-10 size-28 rounded-full border border-white/60 bg-white/20"
      />
      <span
        aria-hidden="true"
        className="absolute -bottom-12 -left-8 size-32 rounded-full bg-primary/10"
      />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
        <span className="grid size-12 place-items-center rounded-[12px] border border-white/70 bg-white/75 text-primary shadow-sm backdrop-blur-sm">
          <Icon className="size-6" aria-hidden="true" />
        </span>
        <span className="text-xs font-medium text-blue-950">
          {kinds[row.kind] || row.kind}
        </span>
      </div>
    </div>
  )
}
export function PointsPage() {
  const guardView = useViewGuard()
  const pointPages = useRef(createPaginationController<PointRow>()).current
  const claimPages = useRef(createPaginationController<Claim>()).current
  const session = useSession("provider"),
    [params, setParams] = useSearchParams(),
    focus = Number(params.get("claim_id")),
    tab = focus ? "claims" : params.get("tab") || "shop",
    key = `xa:store:${getStoreSlug()}:xa:provider:points-pending:${session.user?.id}`,
    [pending, setPending] = useState<Pending | null>(() => readStored(key)),
    [reward, setReward] = useState<Reward | null>(null),
    [details, setDetails] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [code, setCode] = useState(""),
    [pointBefore, setPointBefore] = useState<number | null>(null),
    [claimBefore, setClaimBefore] = useState<number | null>(null),
    [ledger, setLedger] = useState<PointRow[]>([]),
    [claims, setClaims] = useState<Claim[]>([]),
    [editing, setEditing] = useState<Claim | null>(null),
    [draft, setDraft] = useState<Record<string, string>>({}),
    [original, setOriginal] = useState<Record<string, string>>({}),
    [conflict, setConflict] = useState(false),
    [editNotice, setEditNotice] = useState("")
  const data = useLoad(async () => {
    const [points, binding, rewards, claimPage, focused, editingLatest] =
      await Promise.all([
        pointPages.readExpanded(
          "points",
          async (before) => {
            const value = await api<{
              points: number
              list: PointRow[]
              next: number | null
            }>(`/club/points/${before ? `?before=${before}` : ""}`)
            return { ...value, items: value.list }
          },
          pointBefore
        ),
        api<Binding>("/club/kook-binding/"),
        api<Reward[]>("/club/rewards/"),
        claimPages.readExpanded(
          "claims",
          (before) =>
            api<{ items: Claim[]; next: number | null }>(
              `/club/claims/?page_size=20${before ? `&before=${before}` : ""}`
            ),
          claimBefore
        ),
        focus ? api<Claim>(`/club/claims/${focus}/`) : Promise.resolve(null),
        editing
          ? api<Claim>(`/club/claims/${editing.id}/`)
          : Promise.resolve(null),
      ])
    points.list = points.items
    return { points, binding, rewards, claimPage, focused, editingLatest }
  }, [pointBefore, claimBefore, focus])
  useEffect(() => {
    if (!data.data) return
    setLedger(pointPages.merge("points", data.data.points.list, !!pointBefore))
    setClaims(
      claimPages.merge("claims", data.data.claimPage.items, !!claimBefore)
    )
    const latest = data.data.editingLatest
    if (editing && latest?.id === editing.id) {
      setEditing(latest)
      const same = (a: Record<string, string>, b: Record<string, string>) =>
        [...new Set([...Object.keys(a), ...Object.keys(b)])].every(
          (key) => a[key] === b[key]
        )
      if (latest.status !== "PENDING" && !latest.tag_editable) {
        setConflict(true)
        setEditNotice("权益状态已变化，草稿尚未保存，可复制后联系客服。")
      } else if (!same(original, latest.details)) {
        if (same(draft, latest.details)) {
          setOriginal({ ...latest.details })
          setConflict(false)
          setEditNotice("当前资料已保存。")
        } else if (same(draft, original)) {
          setDraft({ ...latest.details })
          setOriginal({ ...latest.details })
          setConflict(false)
        } else {
          setConflict(true)
          setEditNotice("资料已在其他位置更新，草稿已保留，请核对最新资料。")
        }
      }
    }
  }, [data.data])
  async function refreshAll() {
    if (pointBefore || claimBefore) {
      setPointBefore(null)
      setClaimBefore(null)
    } else await data.reload()
  }
  useRefresh(async () => {
    if (!busy) await refreshAll()
  }, [
    "site_message_update",
    "member_points_update",
    "reward_catalog_update",
    "provider_tags_update",
  ])
  async function bind() {
    setBusy(true)
    try {
      const value = await api<{ code: string }>("/club/kook-binding/", {
        method: "POST",
        data: {},
      })
      setCode(value.code)
      try {
        await navigator.clipboard.writeText(`绑定 ${value.code}`)
        setNotice("绑定码已复制，发给KOOK机器人完成绑定。")
      } catch {
        setNotice("请手动复制下方绑定码。")
      }
    } catch (cause) {
      setNotice(message(cause))
    } finally {
      setBusy(false)
    }
  }
  async function send(item: Pending) {
    const currentView = guardView()
    setBusy(true)
    try {
      const { name: _name, ...payload } = item
      const claim = await api<Claim>("/club/redeem/", {
        method: "POST",
        data: payload,
      })
      if (
        readStored<Pending>(key)?.client_request_id === item.client_request_id
      )
        localStorage.removeItem(key)
      if (!currentView()) return
      setPending(null)
      setParams({ claim_id: String(claim.id) })
      setNotice("兑换已提交")
      await data.reload()
    } catch (cause) {
      if (!currentView()) return
      const failure = cause as Failure
      if (
        !uncertain(cause) ||
        [
          "REWARD_PRICE_CHANGED",
          "REWARD_TERMS_CHANGED",
          "REWARD_UNAVAILABLE",
        ].includes(failure.errorCode || "")
      ) {
        localStorage.removeItem(key)
        setPending(null)
        setNotice(message(cause))
        await data.reload().catch(() => {})
      } else setNotice("兑换结果未确认，请重试原申请；不会创建新的兑换编号。")
    } finally {
      setBusy(false)
    }
  }
  function redeem() {
    if (!reward || busy || pending) return
    const item: Pending = {
      reward_id: reward.id,
      name: reward.name,
      client_request_id: requestId("redeem"),
      details: {
        ...details,
        game_uid: details.game_uid || "",
        note: details.note || "",
      },
      expected_point_cost: reward.point_cost,
      expected_terms: terms(reward),
    }
    try {
      localStorage.setItem(key, JSON.stringify(item))
      setPending(item)
      setReward(null)
      void send(item)
    } catch {
      setNotice("无法保存兑换请求，请检查设备存储后重试。")
    }
  }
  function edit(row: Claim) {
    setEditing(row)
    setDraft({ ...row.details })
    setOriginal({ ...row.details })
    setConflict(false)
    setEditNotice("")
  }
  async function readLatest() {
    if (!editing) return
    setBusy(true)
    try {
      const latest = await api<Claim>(`/club/claims/${editing.id}/`)
      setEditing(latest)
      if (latest.status !== "PENDING" && !latest.tag_editable) {
        setConflict(true)
        setEditNotice("权益状态已变化，草稿尚未保存，可复制后联系客服。")
      } else {
        setOriginal({ ...latest.details })
        setConflict(false)
        setEditNotice("已读取最新资料，当前草稿保留，请核对后保存。")
      }
    } catch {
      setEditNotice("读取失败，草稿仍保留，请重试。")
    } finally {
      setBusy(false)
    }
  }
  async function save() {
    if (!editing || busy || conflict) return
    setBusy(true)
    try {
      await api(`/club/claims/${editing.id}/`, {
        method: "PATCH",
        data: {
          details: Object.fromEntries(
            Object.entries(draft).map(([key, value]) => [key, value.trim()])
          ),
          expected_details: original,
        },
      })
      setEditing(null)
      setNotice("交付资料已保存")
      await data.reload()
    } catch (cause) {
      const failure = cause as Failure
      if (
        uncertain(cause) ||
        ["CLAIM_DETAILS_CHANGED", "CLAIM_NOT_EDITABLE"].includes(
          failure.errorCode || ""
        )
      ) {
        setConflict(true)
        setEditNotice(
          uncertain(cause)
            ? "保存结果未确认，请读取最新资料核对，草稿已保留。"
            : "资料或权益状态已变化，请读取最新内容后核对。"
        )
      } else setEditNotice(message(cause))
    } finally {
      setBusy(false)
    }
  }
  const shown = focus ? (data.data?.focused ? [data.data.focused] : []) : claims
  return (
    <div className="space-y-6">
      <PageHeader title="积分与兑换">
        {data.data && <Badge variant="outline" className="mt-1"><Coins className="size-3" />{data.data.points.points} 积分</Badge>}
        <Button
          variant="outline"
          size="icon"
          className="size-11"
          aria-label="刷新积分与兑换"
          disabled={data.loading || busy}
          onClick={() => void refreshAll().catch(() => {})}
        >
          <RefreshCw className={`size-4 ${data.loading ? "animate-spin" : ""}`} />
        </Button>
      </PageHeader>
      <Notice tone={["兑换已提交", "交付资料已保存", "绑定码已复制，发给KOOK机器人完成绑定。"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      {pending && (
        <Panel className="border-primary/40">
          <h2 className="text-sm font-semibold">待核对兑换：{pending.name}</h2>
          <p className="text-xs text-muted-foreground">
            请核对申请记录后重试，避免重复提交。
          </p>
          <Button disabled={busy} onClick={() => void send(pending)}>
            重试原申请
          </Button>
        </Panel>
      )}
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <section className="flex items-center justify-between gap-4 rounded-[12px] border bg-card px-4 py-4 sm:px-5">
          <p className="text-sm text-muted-foreground">当前可用积分</p>
          <p className="text-[28px] font-semibold leading-8">
            {data.data?.points.points ?? "—"}
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              积分
            </span>
          </p>
        </section>
        <FilterBar
          ariaLabel="积分分类"
          columns={3}
          items={[
            { value: "shop", label: "积分好礼" },
            { value: "claims", label: "权益交付" },
            { value: "ledger", label: "积分明细" },
          ]}
          value={tab}
          onChange={(value) => setParams({ tab: value })}
        />
        <LoadState
          hasData={data.data != null}
          loading={data.loading}
          error={data.error}
          reload={() => data.reload().catch(() => {})}
        >
          {tab === "shop" && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4">
              {!data.data?.rewards.length && !data.loading && (
                <div className="col-span-full">
                  <EmptyState
                    title="暂无可兑换好礼"
                    description="积分好礼上新后会在这里显示。"
                  />
                </div>
              )}
              {data.data?.rewards.map((row) => (
                <section
                  key={row.id}
                  className="flex min-w-0 flex-col overflow-hidden rounded-[12px] border bg-card"
                >
                  <RewardArtwork row={row} />
                  <div className="flex min-w-0 flex-1 flex-col p-3 sm:p-4">
                    <h2 className="text-sm font-semibold leading-5">{row.name}</h2>
                    <p className="mt-2 min-h-10 text-xs leading-5 text-muted-foreground">
                      {rewardSummary(row)}
                    </p>
                    <div className="mt-auto pt-4">
                      <div className="flex flex-wrap items-end justify-between gap-x-2 gap-y-1">
                        <p className="text-lg font-semibold leading-6">
                          {row.point_cost}
                          <span className="ml-1 text-xs font-normal text-muted-foreground">
                            积分
                          </span>
                        </p>
                        <p className="text-[11px] leading-5 text-muted-foreground">
                          库存：{row.stock == null ? "不限" : row.stock}
                        </p>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-3 w-full rounded-[6px]"
                        aria-busy={busy && pending?.reward_id === row.id}
                        disabled={
                          busy ||
                          !!pending ||
                          row.stock === 0 ||
                          (data.data?.points.points || 0) < row.point_cost
                        }
                        onClick={() => {
                          setReward(row)
                          setDetails({})
                        }}
                      >
                        {busy && pending?.reward_id === row.id ? "提交中…"
                          : row.stock === 0 ? "已兑完"
                          : (data.data?.points.points || 0) < row.point_cost ? "积分不足"
                          : pending ? "请先核对原申请"
                          : busy ? "请稍候"
                          : "核对并兑换"}
                      </Button>
                    </div>
                  </div>
                </section>
              ))}
            </div>
          )}
          {tab === "claims" && (
            <div className="space-y-3">
              {!shown.length && !data.loading && (
                <EmptyState
                  title={focus ? "未找到此权益" : "暂无权益交付记录"}
                  description={
                    focus
                      ? "请核对消息或联系客服。"
                      : "兑换后可在这里核对交付状态。"
                  }
                />
              )}
              {shown.map((row) => (
                <Panel key={row.id}>
                  <div className="flex justify-between gap-3">
                    <h2 className="font-semibold">{row.name_snapshot}</h2>
                    <Status value={row.status} label={row.status_label} />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    消耗 {row.points_spent} 积分
                    {row.points_returned ? " · 积分已退回" : ""} · 申请 #
                    {row.id}
                  </p>
                  {row.delivery_note && <Notice>{row.delivery_note}</Notice>}
                  {row.kind_snapshot === "TAG" && (
                    <Notice>
                      {row.tag_expired
                        ? "TAG已到期"
                        : row.tag_revision_required
                          ? "请按退回意见修改TAG"
                          : row.tag_revision_submitted
                            ? "修改已提交，等待审核"
                            : row.tag_reviewed === false
                              ? "TAG审核未通过"
                              : row.tag_reviewed
                                ? "TAG审核通过"
                                : "TAG待审核"}
                      {row.tag_suspension_reason &&
                        ` · ${row.tag_suspension_reason}`}
                    </Notice>
                  )}
                  {row.expires_at && (
                    <p className="text-xs text-muted-foreground">
                      有效至 {date(row.expires_at)}
                    </p>
                  )}
                  <DetailGrid
                    items={Object.entries(row.details)
                      .filter(([, value]) => value)
                      .map(([key, value]) => ({
                        label:
                          [
                            ...(fields[row.kind_snapshot] || []),
                            { key: "note", label: "备注" },
                          ].find((item) => item.key === key)?.label || key,
                        value,
                      }))}
                  />
                  {(row.status === "PENDING" || row.tag_editable) && (
                    <Button
                      variant="outline"
                      className="justify-self-start"
                      onClick={() => edit(row)}
                    >
                      填写 / 修改交付资料
                    </Button>
                  )}
                </Panel>
              ))}
              {!focus && data.data?.claimPage.next && (
                <Button
                  variant="outline"
                  disabled={data.loading}
                  onClick={() => setClaimBefore(data.data!.claimPage.next)}
                >
                  加载更多权益
                </Button>
              )}
            </div>
          )}
          {tab === "ledger" && (
            <section className="overflow-hidden rounded-[12px] border bg-card">
              {!ledger.length && (
                <EmptyState
                  title="暂无积分流水"
                  description="积分收入与兑换支出会在这里显示。"
                />
              )}
              {ledger.map((row) => (
                <div
                  className="flex items-center justify-between gap-3 border-b p-4 last:border-0"
                  key={row.id}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{row.remark || "积分变动"}</p>
                    <p className="text-xs text-muted-foreground">
                      {date(row.created_at)} · 余额 {row.balance_after}
                    </p>
                  </div>
                  <strong
                    className={`shrink-0 text-right text-sm font-semibold whitespace-nowrap tabular-nums ${row.amount > 0 ? "text-success" : ""}`}
                  >
                    {row.amount > 0 ? "+" : ""}
                    {row.amount}
                  </strong>
                </div>
              ))}
              {data.data?.points.next && (
                <Button
                  variant="outline"
                  disabled={data.loading}
                  onClick={() => setPointBefore(data.data!.points.next)}
                >
                  加载更多流水
                </Button>
              )}
            </section>
          )}
        </LoadState>
        {data.data?.binding && (
          <Panel>
            <h2 className="text-sm font-semibold">KOOK积分绑定</h2>
            <p className="text-xs text-muted-foreground">
              {data.data.binding.bound
                ? `已绑定 ${data.data.binding.nickname} · ${data.data.binding.connected ? "在线" : "当前离线"}`
                : data.data.binding.enabled
                  ? "绑定后按实际在线规则累计积分。"
                  : "当前未启用KOOK积分能力"}
            </p>
            <p className="text-xs text-muted-foreground">
              {data.data.binding.rules}
            </p>
            {data.data.binding.enabled && !data.data.binding.bound && (
              <Button
                className="justify-self-start"
                disabled={busy}
                variant="outline"
                onClick={() => void bind()}
              >
                获取绑定码
              </Button>
            )}
            {code && (
              <code className="rounded-md bg-muted p-2">绑定 {code}</code>
            )}
          </Panel>
        )}
      </SnapshotGate>
      <Confirm
        open={!!reward}
        title="确认兑换权益"
        description="请核对积分成本、权益内容和交付资料。"
        onClose={() => setReward(null)}
        onConfirm={redeem}
        busy={busy}
        disabled={
          !!pending ||
          (fields[reward?.kind || ""] || []).some(
            (field) => !details[field.key]?.trim()
          )
        }
        label="确认兑换"
      >
        {reward && (
          <>
            <DetailGrid
              items={[
                { label: "兑换权益", value: reward.name },
                { label: "积分成本", value: `${reward.point_cost} 积分` },
              ]}
            />
            <p className="text-sm text-muted-foreground">
              {rewardSummary(reward)}
            </p>
            {(fields[reward.kind] || []).map((field) => (
              <Field key={field.key} label={field.label}>
                <Input
                  value={details[field.key] || ""}
                  maxLength={field.max}
                  onChange={(e) =>
                    setDetails((old) => ({
                      ...old,
                      [field.key]: e.target.value,
                    }))
                  }
                />
              </Field>
            ))}
            <Field label="备注">
              <Textarea
                value={details.note || ""}
                onChange={(e) =>
                  setDetails((old) => ({ ...old, note: e.target.value }))
                }
                maxLength={500}
              />
            </Field>
          </>
        )}
      </Confirm>
      <Confirm
        open={!!editing}
        title="权益交付资料"
        description="仅修改本次权益资料，冲突时保留草稿并重新核对。"
        onClose={() => setEditing(null)}
        onConfirm={() => void save()}
        busy={busy}
        disabled={
          conflict || (editing?.status !== "PENDING" && !editing?.tag_editable)
        }
        label="保存资料"
      >
        <Notice error={conflict}>{editNotice}</Notice>
        {editing &&
          [
            ...(fields[editing.kind_snapshot] || []),
            { key: "note", label: "备注", max: 500 },
          ].map((field) => (
            <Field key={field.key} label={field.label}>
              <Input
                value={draft[field.key] || ""}
                maxLength={field.max}
                onChange={(e) =>
                  setDraft((old) => ({ ...old, [field.key]: e.target.value }))
                }
              />
            </Field>
          ))}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void readLatest()}
          >
            读取最新资料
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              void navigator.clipboard
                .writeText(
                  Object.entries(draft)
                    .map(([key, value]) => `${key}：${value}`)
                    .join("\n")
                )
                .catch(() => setEditNotice("复制失败，请手动复制。"))
            }
          >
            复制草稿
          </Button>
        </div>
      </Confirm>
    </div>
  )
}
