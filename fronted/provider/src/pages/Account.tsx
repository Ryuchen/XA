import { coinText, coinUnit, OtpSetupPanel } from "@xa/shared"
import {
  evaluationFocus,
  loadProviderEvaluations,
  submitProviderEvaluationReply,
  evaluationReplyFailure,
  evaluationLatestReply,
  providerAccountPresentation,
  updateProviderStatus,
  mutateAndReload,
} from "@xa/shared"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { Link, useSearchParams } from "react-router"
import {
  BadgeCheck,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
  Coins,
  FileCheck2,
  Gamepad2,
  Headphones,
  LogOut,
  LoaderCircle,
  Mic2,
  RefreshCw,
  Save,
  ShieldCheck,
  Star,
  TicketCheck,
  WalletCards,
  MessageSquareText,
  MessageCircle,
  type LucideIcon,
} from "lucide-react"
import { clearSession, isWechatBrowser, updateProfile, useH5Auth, useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { SearchField } from "@/components/SearchField"
import { Textarea } from "@/components/ui/textarea"
import { Checkbox } from "@/components/ui/checkbox"
import { Switch } from "@/components/ui/switch"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  EmptyState,
  Status,
  Stat,
  Confirm,
  Field,
  UploadField,
  PendingFeature,
  DetailGrid,
  Pager,
  selectClass,
  PrivateImage,
} from "@/components/provider-ui"
import {
  api,
  fund,
  useLoad,
  useRefresh,
  money,
  date,
  message,
  uncertain,
  useViewGuard,
} from "@/lib/provider"

type Profile = {
  display_name: string
  isWechatBound?: boolean
  isWechatH5Bound?: boolean
  wechatAvatarReady?: boolean
  wechatAvatarLegacyExempt?: boolean
  isPhoneVerified?: boolean
  gender: string
  bio: string
  city: string
  service_area: string
  avatar_url: string
  intro_video_url: string
  voice_card_url: string
  cheat_proof_url: string
  qualifications: { id: number; label: string; is_active: boolean }[]
  dispatch_notification_options: {
    key: string
    label: string
    eligible: boolean
    enabled: boolean
  }[]
  status: string
  is_verified: boolean
  rating_avg: number
  rating_count: number
  completed_order_count: number
  display_tags: {
    id: number
    title: string
    highlighted: boolean
    expires_at: string | null
  }[]
  game_category_ids?: number[]
  service_item_ids?: number[]
}
type Deposit = {
  deposit_mode?: string
  deposit_percent?: number
  hold_hours?: number
  deposit_required: number
  deposit_paid: number
  deposit_remaining: number
  balance: number
}
export function MinePage() {
  const session = useSession("provider"),
    data = useLoad(async () => {
      const results = await Promise.allSettled([
        api<Profile>("/users/escorts/me/"),
        api<Deposit>("/wallet/deposit/"),
        api<{ pending_total: number }>(
          "/orders/evaluations/mine/?page=1&page_size=1"
        ),
        api<{
          today_orders: number
          completion_rate: number
          rating_avg: number
          rating_count: number
        }>("/users/provider-stats/"),
      ])
      return {
        profile: results[0].status === "fulfilled" ? results[0].value : null,
        deposit: results[1].status === "fulfilled" ? results[1].value : null,
        pending:
          results[2].status === "fulfilled"
            ? results[2].value.pending_total
            : null,
        stats: results[3].status === "fulfilled" ? results[3].value : null,
        issues: results.flatMap((row) =>
          row.status === "rejected" ? [message(row.reason)] : []
        ),
      }
    }),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [depositOpen, setDepositOpen] = useState(false),
    [logoutOpen, setLogoutOpen] = useState(false),
    [issue, setIssue] = useState("")
  useRefresh(async () => {
    if (!busy) await data.reload()
  }, ["wallet_update", "order_status_update", "provider_tags_update"])
  const profile = data.data?.profile,
    deposit = data.data?.deposit,
    presentation = providerAccountPresentation(profile, deposit),
    totalFailure =
      data.data &&
      !profile &&
      !deposit &&
      data.data.pending == null &&
      !data.data.stats
        ? data.data.issues.join("；")
        : ""
  useEffect(() => {
    const expiries = (profile?.display_tags || [])
      .map((row) => (row.expires_at ? new Date(row.expires_at).getTime() : 0))
      .filter((time) => time > Date.now())
    if (!expiries.length) return
    const timer = setTimeout(
      () => {
        void data.reload().catch(() => {})
      },
      Math.min(
        2147483647,
        Math.max(0, Math.min(...expiries) - Date.now()) + 1000
      )
    )
    return () => clearTimeout(timer)
  }, [profile?.display_tags])
  async function status(value: string) {
    if (data.loading || data.error || busy) return
    setBusy(true)
    try {
      await updateProviderStatus(value)
      setNotice("接单状态已更新")
    } catch (cause) {
      setNotice(
        uncertain(cause) ? "状态结果未确认，请刷新核对。" : message(cause)
      )
    } finally {
      await data.reload().catch(() => {})
      setBusy(false)
    }
  }
  async function payDeposit() {
    if (!deposit || busy || data.loading || data.error) return
    setBusy(true)
    try {
      await fund("/wallet/deposit/", { amount: deposit.deposit_remaining })
      setDepositOpen(false)
      setNotice("押金缴纳成功")
    } catch (cause) {
      setIssue(
        uncertain(cause)
          ? "缴纳结果未确认，请刷新押金与钱包流水核对。"
          : message(cause)
      )
    } finally {
      await data.reload().catch(() => {})
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="个人中心">
        <Button
          variant="outline"
          size="icon"
          className="size-11 rounded-[8px]"
          aria-label="刷新个人中心"
          disabled={data.loading}
          onClick={() => void data.reload().catch(() => {})}
        >
          <RefreshCw
            className={`size-4 ${data.loading ? "animate-spin" : ""}`}
          />
        </Button>
      </PageHeader>
      <Notice tone={["接单状态已更新", "押金缴纳成功", "档期已保存", "资料已更新", "已恢复接单", "已暂停接单，已有订单不受影响", "技能已同步", "回复已发布"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <LoadState
        hasData={data.data != null && !totalFailure}
        loading={data.loading}
        error={data.error || totalFailure}
        reload={() => data.reload().catch(() => {})}
      >
        {data.data?.issues.map((issue, index) => (
          <Notice key={index} error>
            {issue}
          </Notice>
        ))}
        <div className="grid items-start gap-6 md:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <section className="overflow-hidden rounded-[12px] border bg-card">
              <Link
                to="/profile"
                className="flex min-h-24 w-full items-center gap-3 p-4 text-left hover:bg-accent/30"
              >
                {profile?.avatar_url ? (
                  <img
                    className="size-14 shrink-0 rounded-full object-cover"
                    width={56}
                    height={56}
                    src={profile.avatar_url}
                    alt="头像"
                  />
                ) : (
                  <span className="grid size-14 shrink-0 place-items-center rounded-full bg-primary/10 text-xl text-primary">
                    {(
                      profile?.display_name ||
                      session.user?.nickname ||
                      "陪"
                    ).slice(0, 1)}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-lg font-semibold">
                    {profile?.display_name ||
                      session.user?.nickname ||
                      session.user?.username}
                    {profile?.is_verified && (
                      <BadgeCheck
                        className="size-4 shrink-0 text-primary"
                        aria-label="已认证"
                      />
                    )}
                  </span>
                  <span className="mt-1 block text-xs break-all text-muted-foreground">
                    陪玩账号 · {session.user?.username}
                  </span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              </Link>
              <div className="grid grid-cols-3 border-t text-center">
                <ProfileStat
                  value={
                    profile?.rating_count || data.data?.stats?.rating_count
                      ? (profile?.rating_avg ??
                        data.data?.stats?.rating_avg ??
                        "—")
                      : "—"
                  }
                  label="综合评分"
                />
                <ProfileStat
                  value={data.data?.stats?.today_orders ?? "—"}
                  label="今日接单"
                />
                <ProfileStat
                  value={
                    data.data?.stats
                      ? `${data.data.stats.completion_rate}%`
                      : "—"
                  }
                  label="完成率"
                />
              </div>
              <div className="space-y-3 border-t p-4">
                {profile && (
                  <p className="text-xs text-muted-foreground">
                    已完成 {profile.completed_order_count} 单 ·{" "}
                    {profile.rating_count} 条评价
                  </p>
                )}
                {profile?.display_tags?.filter(
                  (row) =>
                    !row.expires_at ||
                    new Date(row.expires_at).getTime() > Date.now()
                ).length ? (
                  <div className="flex flex-wrap gap-2">
                    {profile.display_tags
                      .filter(
                        (row) =>
                          !row.expires_at ||
                          new Date(row.expires_at).getTime() > Date.now()
                      )
                      .map((row) => (
                        <span
                          className={`rounded-[4px] border px-2 py-1 text-xs ${row.highlighted ? "border-primary text-primary" : ""}`}
                          key={row.id}
                        >
                          {row.title}
                        </span>
                      ))}
                  </div>
                ) : null}
                {profile && (
                  <Field label="接单状态">
                    <select
                      className={selectClass}
                      value={presentation.status}
                      disabled={busy}
                      onChange={(e) => void status(e.target.value)}
                    >
                      {presentation.statusOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
              </div>
            </section>
            <Link
              to="/wallet"
              className="block w-full rounded-[12px] border border-slate-700 bg-slate-900 p-5 text-left text-white"
            >
              <span className="flex items-center justify-between text-sm text-slate-300">
                <span className="flex items-center gap-2">
                  <WalletCards className="size-4 text-sky-300" />
                  可用余额
                </span>
                <ChevronRight className="size-4" />
              </span>
              <span className="mt-4 block text-[32px] font-semibold">
                {deposit ? money(deposit.balance) : "—"}
                <span className="ml-2 text-sm font-normal text-slate-300">
                  {coinUnit()}</span>
              </span>
              <span className="mt-3 block text-xs text-slate-400">
                查看收益、提现与资金流水
              </span>
            </Link>
            {deposit && (
              <Panel>
                <div className="flex justify-between">
                  <h2 className="font-semibold">押金与收益规则</h2>
                  <Status value={presentation.deposit!.modeLabel} />
                </div>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground">目标</p>
                    <strong>
                      {(presentation.deposit!.required / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} {coinText(" 浪花")}</strong>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">已缴</p>
                    <strong>{(presentation.deposit!.paid / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} {coinText(" 浪花")}</strong>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">待缴</p>
                    <strong>
                      {(presentation.deposit!.remaining / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} {coinText(" 浪花")}</strong>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {presentation.deposit!.freezeNotice}
                  {presentation.deposit!.settlementNotice}
                </p>
                {deposit.deposit_remaining > 0 && (
                  <Button
                    disabled={busy || !presentation.deposit!.canPay}
                    onClick={() => {
                      setIssue("")
                      setDepositOpen(true)
                    }}
                  >
                    从钱包缴足押金
                  </Button>
                )}
                <Button variant="outline" asChild>
                  <Link to="/support">充值与押金咨询</Link>
                </Button>
              </Panel>
            )}
          </aside>
          <div className="space-y-4">
            <MenuGroup title="接单管理">
              <MenuRow
                icon={CircleUserRound}
                label="资料与档期"
                to="/profile"
              />
              <MenuRow
                icon={Gamepad2}
                label="我的技能"
                to="/skills"
                note={
                  profile?.service_item_ids
                    ? `已选 ${profile.service_item_ids.length} 项`
                    : undefined
                }
              />
              <MenuRow
                icon={Star}
                label="评价管理"
                to="/evaluations"
                note={
                  data.data?.pending == null
                    ? undefined
                    : `${data.data.pending} 条待回复`
                }
              />
              <MenuRow
                icon={CalendarClock}
                label="我的预约"
                to="/reservations"
              />
              <MenuRow icon={FileCheck2} label="我的报名" to="/applications" />
              <MenuRow
                icon={Mic2}
                label="试音报名"
                note="公开招募待开放"
                to="/auditions"
              />
            </MenuGroup>
            <MenuGroup title="账户服务">
              <MenuRow
                icon={WalletCards}
                label="我的钱包"
                to="/wallet"
                note={deposit ? `${money(deposit.balance)} ${coinUnit()}` : undefined}
              />
              <MenuRow icon={Coins} label="我的积分与兑换" to="/points" />
              <MenuRow icon={TicketCheck} label="接单通行证" to="/pass" />
              <MenuRow icon={FileCheck2} label="我的报单" to="/reports" />
              <MenuRow icon={Headphones} label="联系客服" to="/support-chat" />
            </MenuGroup>
            <Button
              variant="ghost"
              className="h-11 w-full text-muted-foreground"
              onClick={() => setLogoutOpen(true)}
            >
              <LogOut className="size-4" />
              退出登录
            </Button>
          </div>
        </div>
      </LoadState>
      <Confirm
        open={depositOpen}
        title="缴足押金"
        description="请核对本次从钱包扣除的金额。"
        busy={busy}
        disabled={!!issue}
        onClose={() => setDepositOpen(false)}
        onConfirm={() => void payDeposit()}
      >
        <p>本次缴纳 {((deposit?.deposit_remaining ?? 0) / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} {coinText(" 浪花")}</p>
        <Notice error>{issue}</Notice>
        {issue && (
          <Button
            variant="outline"
            onClick={() => {
              setDepositOpen(false)
              void data.reload().catch(() => {})
            }}
          >
            核对最新押金
          </Button>
        )}
      </Confirm>
      <Confirm
        open={logoutOpen}
        title="退出登录"
        description="退出后将关闭当前账号的实时连接。"
        onClose={() => setLogoutOpen(false)}
        onConfirm={() => clearSession("provider")}
        label="退出登录"
      />
    </div>
  )
}

function ProfileStat({
  value,
  label,
}: {
  value: string | number
  label: string
}) {
  return (
    <div className="border-r p-3 last:border-0">
      <p className="font-semibold">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  )
}
function MenuGroup({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="overflow-hidden rounded-[12px] border bg-card">
      <h2 className="border-b px-4 py-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}
function MenuRow({
  icon: Icon,
  label,
  note,
  to,
}: {
  icon: LucideIcon
  label: string
  note?: string
  to: string
}) {
  return (
    <Link
      to={to}
      className="group flex min-h-14 w-full items-center gap-3 border-b px-4 text-left last:border-0 hover:bg-accent/30"
    >
      <Icon className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
      <span className="flex-1 text-sm font-medium">{label}</span>
      {note && <span className="text-xs text-muted-foreground">{note}</span>}
      <ChevronRight className="size-4 text-muted-foreground" />
    </Link>
  )
}

type Slot = { weekday: number; start_minute: number; end_minute: number }
function WechatBindingCard({ profile, onBound, onAvatarSynced }: { profile: Profile; onBound: () => void; onAvatarSynced: () => void }) {
  const wechat = useH5Auth("provider", onBound)
  const avatar = useH5Auth("provider", onAvatarSynced)
  return <Panel className="gap-3">
    <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">微信登录</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">绑定后与账号密码、短信登录进入同一陪玩账号。</p></div><Badge variant="outline" className={profile.isWechatBound ? "border-success/25 text-success" : ""}>{profile.isWechatBound ? "已绑定" : "待绑定"}</Badge></div>
    {profile.isWechatH5Bound ? <p className="flex items-center gap-2 text-sm text-success"><ShieldCheck className="size-4" />可在微信内使用快捷登录</p> : isWechatBrowser() ? <Button type="button" variant="outline" disabled={wechat.busy} onClick={() => wechat.start({ purpose: "bind_wechat" })}><MessageCircle className="size-4" />{wechat.busy ? "正在打开微信授权…" : profile.isWechatBound ? "绑定当前微信用于网页授权" : "绑定当前微信"}</Button> : <p className="rounded-[8px] bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">请在微信内打开本页完成网页授权绑定；普通浏览器继续使用账号密码和短信验证码登录。</p>}
    {wechat.flow && <Button asChild variant="ghost" className="h-10"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权</a></Button>}
    <Notice error>{wechat.error}</Notice>
    {profile.isWechatBound && <div className="space-y-2 border-t pt-3"><p className="text-sm">{profile.wechatAvatarLegacyExempt ? "历史头像可继续使用。" : profile.wechatAvatarReady ? "已获取过微信头像，可重新获取或自行上传。" : "首次接单前需要从本人微信获取头像。"}</p>{!profile.isWechatH5Bound ? <p className="text-xs text-muted-foreground">获取头像前，请先绑定当前微信用于网页授权。</p> : isWechatBrowser() ? <Button type="button" variant="outline" disabled={avatar.busy} onClick={() => avatar.start({ purpose: "sync_avatar" })}>{avatar.busy ? "正在打开微信授权…" : "从微信获取头像"}</Button> : <p className="text-xs text-muted-foreground">请在微信内打开本页获取头像。</p>}{avatar.flow && <Button asChild variant="ghost" className="h-10"><a href={avatar.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信头像授权</a></Button>}<Notice error>{avatar.error}</Notice></div>}
  </Panel>
}

export function ProfilePage() {
  const guardView = useViewGuard()
  const hydrated = useRef(false)
  const data = useLoad(async () => ({
      profile: await api<Profile>("/users/escorts/me/"),
      slots: await api<Slot[]>("/users/escorts/schedule/"),
    })),
    [form, setForm] = useState({
      display_name: "",
      gender: "UNKNOWN",
      bio: "",
      city: "",
      service_area: "",
    }),
    [slots, setSlots] = useState<Slot[]>([]),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState(""),
    [noticeError, setNoticeError] = useState(false)
  useEffect(() => {
    if (data.data && !hydrated.current) {
      hydrated.current = true
      const p = data.data.profile
      setForm({
        display_name: p.display_name,
        gender: p.gender,
        bio: p.bio,
        city: p.city,
        service_area: p.service_area,
      })
      setSlots(data.data.slots)
    }
  }, [data.data])
  async function save(kind: string, payload: unknown, method = "PATCH") {
    if (busy || data.loading || data.error) return
    setNotice("")
    setNoticeError(false)
    setBusy(kind)
    try {
      if (kind === "schedule") {
        const value = await api<Slot[]>("/users/escorts/schedule/", {
          method: "PUT",
          data: payload,
        })
        setSlots(value)
      } else {
        const value = await api<Profile>("/users/escorts/me/", {
          method,
          data: payload,
        })
        data.setData((old) => (old ? { ...old, profile: value } : old))
      }
      setNotice(kind === "schedule" ? "档期已保存" : "资料已更新")
    } catch (cause) {
      setNoticeError(true)
      setNotice(message(cause))
    } finally {
      setBusy("")
    }
  }
  async function setAvailability(next: "AVAILABLE" | "OFFLINE") {
    if (busy || data.loading || data.error || !data.data?.profile) return
    const current = guardView()
    setBusy("status")
    setNotice("")
    setNoticeError(false)
    try {
      const result = await mutateAndReload({
        role: "provider",
        isCurrent: current,
        mutate: () => updateProviderStatus(next),
        reload: data.reload,
      })
      if (result.stale) return
      setNoticeError(!!result.error || !!result.reloadError)
      setNotice(
        result.error
          ? uncertain(result.error)
            ? "接单状态结果未确认，请重新核对。"
            : message(result.error)
          : result.reloadError
            ? "接单状态已保存，资料未完成刷新，请重试核对。"
            : next === "AVAILABLE"
              ? "已恢复接单"
              : "已暂停接单，已有订单不受影响"
      )
    } catch (cause) {
      if (current()) { setNoticeError(true); setNotice(message(cause)) }
    } finally {
      setBusy("")
    }
  }
  const profile = data.data?.profile,
    assets = [
      {
        key: "avatar",
        label: "头像",
        accept: "image/*",
        max: 5,
        url: profile?.avatar_url,
      },
      {
        key: "intro_video",
        label: "介绍视频",
        accept: "video/*",
        max: 50,
        url: profile?.intro_video_url,
      },
      {
        key: "voice_card",
        label: "语音卡",
        accept: ".mp3,.m4a,.wav,.aac,.ogg",
        max: 10,
        url: profile?.voice_card_url,
      },
      {
        key: "cheat_proof",
        label: "查外挂凭证",
        accept: "image/*",
        max: 10,
        url: profile?.cheat_proof_url,
      },
    ]
  return (
    <div className="space-y-6">
      <PageHeader title="资料与档期" />
      <Notice error={noticeError} tone={["档期已保存", "资料已更新", "已恢复接单", "已暂停接单，已有订单不受影响"].includes(notice) ? "success" : "info"}>
        {notice}
        {notice.includes("接单状态") && /未确认|未完成刷新/.test(notice) && (
          <Button
            variant="link"
            disabled={!!busy}
            onClick={() => void data.reload().catch(() => {})}
          >
            核对接单状态
          </Button>
        )}
      </Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
      >
        {profile && <WechatBindingCard profile={profile} onBound={() => { updateProfile("provider", { isWechatBound: true }); setNotice("微信绑定已完成"); setNoticeError(false); void data.reload().catch(() => {}) }} onAvatarSynced={() => { setNotice("微信头像已更新"); setNoticeError(false); void data.reload().catch(() => {}) }} />}
        {profile && <OtpSetupPanel role="provider" />}
        {profile && (
          <div className="grid w-full min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-2">
            <form
              className="w-full min-w-0"
              onSubmit={(e) => {
                e.preventDefault()
                void save("details", {
                  ...form,
                  display_name: form.display_name.trim(),
                  bio: form.bio.trim(),
                  city: form.city.trim(),
                  service_area: form.service_area.trim(),
                })
              }}
            >
              <Panel className="gap-0">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="font-semibold">基本资料</h2>
                  {profile.is_verified && (
                    <Badge
                      variant="outline"
                      className="border-success/25 text-success"
                    >
                      <ShieldCheck className="size-3" />
                      已认证
                    </Badge>
                  )}
                </div>
                <div className="mt-5 space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="结单昵称">
                      <Input
                        required
                        maxLength={50}
                        value={form.display_name}
                        onChange={(e) =>
                          setForm((old) => ({
                            ...old,
                            display_name: e.target.value,
                          }))
                        }
                      />
                    </Field>
                    <Field label="性别">
                      <select
                        className={selectClass}
                        value={form.gender}
                        onChange={(e) =>
                          setForm((old) => ({ ...old, gender: e.target.value }))
                        }
                      >
                        <option value="UNKNOWN">未设置</option>
                        <option value="MALE">男</option>
                        <option value="FEMALE">女</option>
                      </select>
                    </Field>
                    <Field label="城市">
                      <Input
                        maxLength={50}
                        value={form.city}
                        onChange={(e) =>
                          setForm((old) => ({ ...old, city: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="服务区服">
                      <Input
                        maxLength={100}
                        value={form.service_area}
                        onChange={(e) =>
                          setForm((old) => ({
                            ...old,
                            service_area: e.target.value,
                          }))
                        }
                      />
                    </Field>
                  </div>
                  <Field label="个人介绍">
                    <Textarea
                      rows={4}
                      maxLength={1000}
                      value={form.bio}
                      onChange={(e) =>
                        setForm((old) => ({ ...old, bio: e.target.value }))
                      }
                    />
                  </Field>
                  <DetailGrid
                    items={[
                      {
                        label: "擅长游戏",
                        value: (
                          <Link
                            className="inline-flex min-h-11 items-center text-primary underline-offset-4 hover:underline"
                            to="/skills"
                          >
                            {profile.game_category_ids
                              ? `已选 ${profile.game_category_ids.length} 款游戏 · 查看技能`
                              : "查看服务技能"}
                          </Link>
                        ),
                      },
                      {
                        label: "常用位置",
                        value: (
                          <PendingFeature
                            label="常用位置"
                            description="按游戏维护常用位置待开放，可先在个人介绍中说明。"
                          />
                        ),
                      },
                      {
                        label: "服务等级",
                        value: (
                          <PendingFeature
                            label="服务等级展示"
                            description="个人等级展示待接入，现有认证资格仍按后台配置显示。"
                          />
                        ),
                      },
                      {
                        label: "声音标签",
                        value: (
                          <PendingFeature
                            label="声音标签"
                            description="结构化声音标签待开放，现有语音卡上传不受影响。"
                          />
                        ),
                      },
                    ]}
                  />
                  <p className="text-xs text-muted-foreground">
                    认证资格：
                    {profile.qualifications
                      ?.filter((row) => row.is_active)
                      .map((row) => row.label)
                      .join("、") || "暂无有效资格"}
                  </p>
                  <div className="flex justify-end border-t pt-4">
                    <Button
                      type="submit"
                      disabled={!!busy || data.loading || !!data.error || !form.display_name.trim()}
                      aria-busy={busy === "details"}
                      className="h-11"
                    >
                      {busy === "details" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" />}
                      {busy === "details" ? "保存中…" : "保存资料"}
                    </Button>
                  </div>
                </div>
              </Panel>
            </form>
            <section className="min-w-0 rounded-[12px] border bg-card p-4 md:p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="font-semibold">本周接单</h2>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {busy === "status"
                      ? "接单状态更新中…"
                      : profile.status === "BUSY"
                        ? "当前忙碌 · 关闭后不影响已有订单"
                        : ["AVAILABLE", "OFFLINE"].includes(profile.status)
                          ? "关闭后不影响已有订单"
                          : "接单状态待核对"}
                  </p>
                </div>
                <Switch name="本周接单状态"
                  className="shrink-0"
                  checked={["AVAILABLE", "BUSY"].includes(profile.status)}
                  disabled={
                    !!busy ||
                    data.loading ||
                    !!data.error ||
                    !["AVAILABLE", "BUSY", "OFFLINE"].includes(profile.status)
                  }
                  onCheckedChange={(checked) =>
                    void setAvailability(checked ? "AVAILABLE" : "OFFLINE")
                  }
                  aria-label="本周接单状态"
                />
              </div>
              <div className="mt-4 flex items-center justify-between gap-3">
                <span className="text-sm font-medium">自由服务时段</span>
                <PendingFeature
                  label="自由服务时段"
                  description="自由服务日和起止时间待开放，现有固定档期继续生效。"
                />
              </div>
              <div className="mt-5">
                <span className="text-sm font-medium">常规服务日</span>
                <div className="mt-3 grid grid-cols-4 gap-2">
                  {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map(
                    (day) => (
                      <button
                        key={day}
                        type="button"
                        disabled
                        className="min-h-11 rounded-[8px] border text-xs text-muted-foreground opacity-50"
                      >
                        {day}
                      </button>
                    )
                  )}
                </div>
              </div>
              <div className="mt-5 grid grid-cols-2 gap-3">
                <Field label="开始时间">
                  <Input
                    type="time"
                    value=""
                    readOnly
                    disabled
                    aria-label="自由开始时间待开放"
                  />
                </Field>
                <Field label="结束时间">
                  <Input
                    type="time"
                    value=""
                    readOnly
                    disabled
                    aria-label="自由结束时间待开放"
                  />
                </Field>
              </div>
              <p className="mt-4 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
                自由服务日和起止时间尚未生效，现有服务时段仍按下方固定档期安排。
              </p>
            </section>
            <Panel className="gap-0">
              <div>
                <h2 className="font-semibold">每周可接单档期</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  北京时间；选中可提供服务的时间段。
                </p>
              </div>
              <div className="mt-5 space-y-4">
                <div
                  className="w-full min-w-0 overflow-x-auto"
                  role="region"
                  aria-label="每周可接单档期，可横向滚动"
                  tabIndex={0}
                >
                  <table className="w-full min-w-[520px] text-sm">
                    <thead>
                      <tr>
                        <th className="p-2 text-left">时段</th>
                        {["一", "二", "三", "四", "五", "六", "日"].map(
                          (day) => (
                            <th className="p-2" key={day}>
                              周{day}
                            </th>
                          )
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        [0, 360, "00–06"],
                        [360, 720, "06–12"],
                        [720, 1080, "12–18"],
                        [1080, 1440, "18–24"],
                      ].map(([start, end, label]) => (
                        <tr key={start}>
                          <th className="border-t p-2 text-left font-normal">
                            {label}
                          </th>
                          {Array.from({ length: 7 }, (_, day) => {
                            const value = {
                                weekday: day,
                                start_minute: Number(start),
                                end_minute: Number(end),
                              },
                              checked = slots.some(
                                (row) =>
                                  row.weekday === day &&
                                  row.start_minute === start &&
                                  row.end_minute === end
                              )
                            return (
                              <td
                                className="border-t p-2 text-center"
                                key={day}
                              >
                                <Checkbox name={`周${day + 1} ${label}`}
                                  className="mx-auto"
                                  aria-label={`周${day + 1} ${label}`}
                                  checked={checked}
                                  disabled={!!busy}
                                  onCheckedChange={() =>
                                    setSlots((old) =>
                                      checked
                                        ? old.filter(
                                            (row) =>
                                              !(
                                                row.weekday === day &&
                                                row.start_minute === start &&
                                                row.end_minute === end
                                              )
                                          )
                                        : [...old, value]
                                    )
                                  }
                                />
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex justify-end border-t pt-4">
                  <Button
                    className="h-11"
                    disabled={!!busy || data.loading || !!data.error}
                    aria-busy={busy === "schedule"}
                    onClick={() => void save("schedule", { slots })}
                  >
                    {busy === "schedule" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" />}
                    {busy === "schedule" ? "保存中…" : "保存档期"}
                  </Button>
                </div>
              </div>
            </Panel>

            <Panel>
              <h2 className="font-semibold">通知偏好</h2>
              {profile.dispatch_notification_options?.map((row) => (
                <div
                  className="flex items-center justify-between gap-3"
                  key={row.key}
                >
                  <label
                    htmlFor={`notification-${row.key}`}
                    className="text-sm"
                  >
                    {row.label}
                    {!row.eligible && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        暂不符合资格
                      </span>
                    )}
                  </label>
                  <Switch
                    id={`notification-${row.key}`}
                    checked={row.enabled}
                    disabled={!!busy || data.loading || !!data.error || !row.eligible}
                    onCheckedChange={(enabled) =>
                      void save("notification", {
                        dispatch_notification_preferences: {
                          [row.key]: enabled,
                        },
                      })
                    }
                  />
                </div>
              ))}
            </Panel>

            <Panel>
              <h2 className="font-semibold">展示附件</h2>
              <div className="grid gap-4 sm:grid-cols-2">
                {assets.map((asset) => (
                  <section className="space-y-3 border-t pt-4" key={asset.key}>
                    <h3 className="flex items-center justify-between gap-2 text-sm font-medium">
                      {asset.label}
                      {busy === asset.key && <span role="status" className="flex items-center gap-1.5 text-xs font-normal text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />上传中…</span>}
                    </h3>
                    {asset.url &&
                      (asset.key === "intro_video" ? (
                        <video
                          src={asset.url}
                          controls
                          className="max-h-48 w-full rounded-md"
                        />
                      ) : asset.key === "voice_card" ? (
                        <audio src={asset.url} controls className="w-full" />
                      ) : asset.key === "cheat_proof" ? (
                        <PrivateImage src={asset.url} alt={asset.label} />
                      ) : (
                        <img
                          src={asset.url}
                          alt={asset.label}
                          width={640}
                          height={360}
                          className="max-h-40 w-full rounded-md object-contain"
                        />
                      ))}
                    <UploadField
                      aria-label={`上传${asset.label}`}
                      accept={asset.accept}
                      disabled={!!busy || data.loading || !!data.error}
                      aria-busy={busy === asset.key}
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        e.target.value = ""
                        if (!file) return
                        if (file.size > asset.max * 1024 * 1024) {
                          setNoticeError(true)
                          setNotice(`${asset.label}不能超过${asset.max}MB`)
                          return
                        }
                        const payload = new FormData()
                        payload.append(asset.key, file)
                        void save(asset.key, payload, "POST")
                      }}
                    />
                    {asset.key === "voice_card" && asset.url && (
                      <Button
                        disabled={!!busy || data.loading || !!data.error}
                        variant="destructive"
                        onClick={() =>
                          void save("media", { remove_voice_card: true })
                        }
                      >
                        移除语音卡
                      </Button>
                    )}
                    <p className="text-xs text-muted-foreground">
                      最大 {asset.max}MB
                    </p>
                  </section>
                ))}
              </div>
            </Panel>
          </div>
        )}
      </LoadState>
    </div>
  )
}

type Skill = {
  id: number
  name: string
  price: number
  price_unit: string
  required_level_name: string
  platform: string
  platform_name: string
  security_level: string
  security_level_name: string
  qualified?: boolean
  provider_requirement?: { label: string }
}
type Group = {
  game_category_id: number
  game_category_name: string
  items: Skill[]
}
export function SkillsPage() {
  const data = useLoad(async () => ({
      groups: await api<Group[]>("/users/escorts/skill-options/"),
      profile: await api<{ service_item_ids: number[] }>("/users/escorts/me/"),
    })),
    [selected, setSelected] = useState<Set<number>>(new Set()),
    [saved, setSaved] = useState<Set<number>>(new Set()),
    [keyword, setKeyword] = useState(""),
    [game, setGame] = useState(""),
    [platform, setPlatform] = useState(""),
    [difficulty, setDifficulty] = useState(""),
    [selectedOnly, setSelectedOnly] = useState(false),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [confirm, setConfirm] = useState<"clear" | "reload" | null>(null),
    [limit, setLimit] = useState(24)
  useEffect(() => {
    if (data.data) {
      setSelected(new Set(data.data.profile.service_item_ids))
      setSaved(new Set(data.data.profile.service_item_ids))
    }
  }, [data.data])
  const dirty =
      selected.size !== saved.size ||
      [...selected].some((id) => !saved.has(id)),
    groups = data.data?.groups || [],
    offered = new Set(
      groups.flatMap((group) => group.items.map((row) => row.id))
    ),
    retired = [...selected].filter((id) => !offered.has(id)),
    shown = groups
      .filter((group) => !game || String(group.game_category_id) === game)
      .map((group) => ({
        ...group,
        items: group.items.filter(
          (row) =>
            `${group.game_category_name} ${row.name}`.includes(
              keyword.trim()
            ) &&
            (!platform || row.platform === platform) &&
            (!difficulty || row.security_level === difficulty) &&
            (!selectedOnly || selected.has(row.id))
        ),
      }))
      .filter((group) => group.items.length)
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault()
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [dirty])
  async function save() {
    setConfirm(null)
    setBusy(true)
    try {
      const result = await api<{ service_item_ids: number[] }>(
        "/users/escorts/me/",
        { method: "PATCH", data: { service_items: [...selected] } }
      )
      setSelected(new Set(result.service_item_ids))
      setSaved(new Set(result.service_item_ids))
      setNotice("技能已同步")
    } catch (cause) {
      setNotice(message(cause))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="我的技能"
        description="选择可以提供的服务，接单仍以实际资格和档期为准。"
      >
        <Button
          variant="outline"
          size="icon"
          className="size-11 rounded-[8px]"
          aria-label="刷新服务技能"
          disabled={busy}
          onClick={() =>
            dirty ? setConfirm("reload") : void data.reload().catch(() => {})
          }
        >
          <RefreshCw
            className={`size-4 ${data.loading ? "animate-spin" : ""}`}
          />
        </Button>
        <Button
          className="h-11"
          disabled={busy || !dirty || !!retired.length}
          onClick={() => (selected.size ? void save() : setConfirm("clear"))}
        >
          <Save className="size-4" />
          保存技能
        </Button>
      </PageHeader>
      <Notice tone={["接单状态已更新", "押金缴纳成功", "档期已保存", "资料已更新", "已恢复接单", "已暂停接单，已有订单不受影响", "技能已同步", "回复已发布"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
        empty={data.data != null && !groups.length && !retired.length}
        emptyTitle="暂无开放的技能"
        emptyDescription="运营开放服务目录后，可选择符合资格的项目。"
      >
        {data.data && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border bg-card p-4">
              <div>
                <h2 className="text-sm font-semibold">
                  已选 {selected.size - retired.length} 项技能 ·{" "}
                  {
                    groups.filter((group) =>
                      group.items.some((item) => selected.has(item.id))
                    ).length
                  }{" "}
                  款游戏
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {dirty ? "有未保存的修改" : "已与服务档案同步"}
                </p>
              </div>
              <Badge variant="outline">套餐价 ≠ 实得收益</Badge>
            </div>
            {!!retired.length && (
              <Notice error>
                已选的 {retired.length} 项服务已停用或调整。
                <Button
                  variant="link"
                  onClick={() =>
                    setSelected(
                      (old) => new Set([...old].filter((id) => offered.has(id)))
                    )
                  }
                >
                  移除失效项目
                </Button>
              </Notice>
            )}
            <details className="group rounded-[8px] border bg-card">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 text-sm font-medium [&::-webkit-details-marker]:hidden">
                <span>筛选服务{(keyword || game || platform || difficulty || selectedOnly) && <span className="ml-2 text-xs font-normal text-primary">已筛选</span>}</span>
                <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
              </summary>
              <div className="space-y-3 px-4 pb-4">
              <SearchField
                ariaLabel="搜索游戏或服务"
                placeholder="搜索游戏或服务"
                value={keyword}
                onValueChange={(value) => {
                  setKeyword(value)
                  setLimit(24)
                }}
              />
              <div className="grid gap-3 sm:grid-cols-3">
                <select autoComplete="off" name="游戏"
                  aria-label="游戏"
                  className={selectClass}
                  value={game}
                  onChange={(e) => setGame(e.target.value)}
                >
                  <option value="">全部游戏</option>
                  {groups.map((row) => (
                    <option
                      key={row.game_category_id}
                      value={row.game_category_id}
                    >
                      {row.game_category_name}
                    </option>
                  ))}
                </select>
                <select autoComplete="off" name="平台"
                  aria-label="平台"
                  className={selectClass}
                  value={platform}
                  onChange={(e) => setPlatform(e.target.value)}
                >
                  <option value="">全部平台</option>
                  <option value="MOBILE">手游</option>
                  <option value="PC">端游</option>
                </select>
                <select autoComplete="off" name="难度"
                  aria-label="难度"
                  className={selectClass}
                  value={difficulty}
                  onChange={(e) => setDifficulty(e.target.value)}
                >
                  <option value="">全部难度</option>
                  <option value="CONFIDENTIAL">机密</option>
                  <option value="TOP_SECRET">绝密</option>
                  <option value="ANY">不限难度</option>
                </select>
              </div>
              <Button
                className="justify-self-start"
                variant={selectedOnly ? "secondary" : "outline"}
                aria-pressed={selectedOnly}
                onClick={() => setSelectedOnly((old) => !old)}
              >
                只看已选
              </Button>
              </div>
            </details>
            {!shown.length && (
              <EmptyState
                title={groups.length ? "没有匹配的技能" : "暂无开放的技能"}
                description={
                  groups.length
                    ? "试试其他游戏、平台或搜索词。"
                    : "运营开放服务目录后，可选择符合资格的项目。"
                }
              />
            )}
            <div className="space-y-4">
            {shown.map((group) => (
              <section
                key={group.game_category_id}
                className="overflow-hidden rounded-[12px] border bg-card"
              >
                <div className="flex items-center justify-between border-b p-4">
                  <h2 className="font-semibold">{group.game_category_name}</h2>
                  <Badge variant="secondary">
                    {group.items.filter((row) => selected.has(row.id)).length} /{" "}
                    {group.items.length} 已选
                  </Badge>
                </div>
                {group.items.slice(0, limit).map((row) => (
                  <label
                    key={row.id}
                    className="flex min-h-24 cursor-pointer items-center gap-3 border-b p-4 last:border-0"
                  >
                    <Checkbox name={row.name}
                      checked={selected.has(row.id)}
                      disabled={
                        busy ||
                        (row.qualified === false && !selected.has(row.id))
                      }
                      onCheckedChange={() =>
                        setSelected((old) => {
                          const next = new Set(old)
                          next.has(row.id)
                            ? next.delete(row.id)
                            : next.add(row.id)
                          return next
                        })
                      }
                      aria-label={row.name}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">
                        {row.name}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {[row.platform_name, row.security_level_name]
                          .filter(Boolean)
                          .join(" · ")}{" "}
                        ·{" "}
                        {row.provider_requirement?.label ||
                          row.required_level_name ||
                          "历史服务规则"}
                      </span>
                      <span
                        className={`mt-1 block text-xs ${row.qualified === true ? "text-success" : "text-muted-foreground"}`}
                      >
                        {row.qualified === true
                          ? "资格已满足"
                          : row.qualified === false
                            ? "需运营认证"
                            : "资格以接单时核对为准"}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-xs font-medium">
                      {money(row.price)} {coinText(" 浪花/")}{row.price_unit || "份"}
                    </span>
                  </label>
                ))}
                {group.items.length > limit && (
                  <Button
                    variant="outline"
                    onClick={() => setLimit((old) => old + 24)}
                  >
                    继续查看
                  </Button>
                )}
              </section>
            ))}
            </div>
          </>
        )}
      </LoadState>
      <Confirm
        open={!!confirm}
        title={confirm === "clear" ? "清空服务技能" : "重新读取技能"}
        confirmVariant="destructive"
        label={confirm === "clear" ? "确认清空" : "放弃修改并刷新"}
        description={
          confirm === "clear"
            ? "清空后将不再出现在具体游戏服务的选人列表，也无法接取这些服务的新订单。"
            : "当前有未保存的选择，刷新将放弃这些修改。"
        }
        onClose={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm === "clear") void save()
          else {
            setConfirm(null)
            void data.reload().catch(() => {})
          }
        }}
        busy={busy}
      />
    </div>
  )
}

type Evaluation = {
  id: number
  order: number
  order_no: string
  score: number
  skill_score?: number
  attitude_score?: number
  communication_score?: number
  content: string
  customer_name: string
  service_name: string
  reply_content: string
  reply_author: string
  reply_version: number
  created_at: string
}
export function EvaluationsPage() {
  const guardView = useViewGuard()
  const [params] = useSearchParams(),
    focus = evaluationFocus(params),
    [page, setPage] = useState(1),
    data = useLoad(
      () => loadProviderEvaluations<Evaluation>({ page, orderId: focus }),
      [page, focus]
    ),
    [selected, setSelected] = useState<Evaluation | null>(null),
    [draft, setDraft] = useState(""),
    [drafts, setDrafts] = useState<Record<number, string>>({}),
    [busy, setBusy] = useState(false),
    [blocked, setBlocked] = useState(false),
    [notice, setNotice] = useState("")
  useRefresh(data.reload, ["order_status_update"])
  useEffect(() => {
    setPage(1)
    setSelected(null)
    setNotice("")
    setBusy(false)
    setBlocked(false)
  }, [focus])
  async function latest() {
    if (!selected) return
    const currentView = guardView()
    setBusy(true)
    try {
      const value = await loadProviderEvaluations<Evaluation>({
        page: 1,
        evaluationId: selected.id,
      })
      if (!currentView()) return
      const row = value.list.find((item) => item.id === selected.id)
      const state = evaluationLatestReply(row, draft)
      if (row) setSelected(row)
      setBlocked(state.blocked)
      setNotice(state.notice)
    } catch {
      if (currentView()) setNotice("最新回复暂时无法读取，草稿仍保留。")
    } finally {
      if (currentView()) setBusy(false)
    }
  }
  async function submit() {
    if (!selected || busy || blocked || !draft.trim()) return
    const currentView = guardView()
    setBusy(true)
    try {
      await submitProviderEvaluationReply(selected, draft)
      if (!currentView()) return
      setSelected(null)
      setNotice("回复已发布")
      await data.reload()
    } catch (cause) {
      if (!currentView()) return
      setDrafts((old) => ({ ...old, [selected.id]: draft }))
      const state = evaluationReplyFailure(cause)
      setBlocked(state.blocked)
      setNotice(state.notice)
    } finally {
      if (currentView()) setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="评价管理" />
      <Notice>{!selected && notice}</Notice>
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <div className="grid grid-cols-2 gap-3">
          <Stat label="全部评价" value={data.data?.total ?? "—"} />
          <Stat label="待回复" value={data.data?.pending_total ?? "—"} />
        </div>
        <LoadState
          hasData={data.data != null}
          loading={data.loading}
          error={data.error}
          reload={() => data.reload().catch(() => {})}
          empty={!data.data?.list.length}
          emptyTitle="暂无服务评价"
          emptyDescription="老板完成评价后，内容会在这里显示。"
        >
          <div className="grid items-start gap-4 md:grid-cols-2">
            {data.data?.list.map((row) => (
              <article
                key={row.id}
                className="rounded-[12px] border bg-card p-4 md:p-5"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold">
                      {row.customer_name}
                    </h2>
                    <p className="mt-1 text-xs break-words text-muted-foreground">
                      {row.service_name} · {row.order_no}
                    </p>
                  </div>
                  <span
                    className="flex shrink-0 gap-0.5 text-warning"
                    aria-label={`${row.score} 星评价`}
                  >
                    {Array.from({ length: 5 }, (_, index) => (
                      <Star
                        key={index}
                        className={`size-4 ${index < row.score ? "fill-current" : ""}`}
                      />
                    ))}
                  </span>
                </div>
                {(row.skill_score != null ||
                  row.attitude_score != null ||
                  row.communication_score != null) && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    技术 {row.skill_score ?? "—"} · 态度{" "}
                    {row.attitude_score ?? "—"} · 沟通{" "}
                    {row.communication_score ?? "—"}
                  </p>
                )}
                <p className="mt-4 text-sm leading-6 whitespace-pre-wrap">
                  {row.content || "老板未填写文字评价"}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {date(row.created_at)}
                </p>
                {row.reply_content && (
                  <div className="mt-4 rounded-[8px] bg-muted/40 p-3">
                    <p className="text-xs font-medium">
                      {row.reply_author === "STAFF"
                        ? "客服回复"
                        : row.reply_author === "PROVIDER"
                          ? "本人回复"
                          : "回复（来源未记录）"}
                    </p>
                    <p className="mt-1 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                      {row.reply_content}
                    </p>
                  </div>
                )}
                <Button
                  className="mt-4 h-11 w-full"
                  variant="outline"
                  onClick={() => {
                    setSelected(row)
                    setDraft(drafts[row.id] ?? row.reply_content ?? "")
                    setBlocked(false)
                    setNotice("")
                  }}
                >
                  <MessageSquareText className="size-4" />
                  {row.reply_content ? "查看 / 修改回复" : "回复评价"}
                </Button>
              </article>
            ))}
          </div>
        </LoadState>
        <Pager
          page={page}
          total={data.data?.filtered_total || 0}
          change={setPage}
        />
      </SnapshotGate>
      <Confirm
        open={!!selected}
        title="回复服务评价"
        description="回复将展示给老板，请核对内容；草稿会在当前页面保留。"
        busy={busy}
        disabled={
          blocked ||
          !draft.trim() ||
          draft.trim() === selected?.reply_content?.trim()
        }
        onClose={() => {
          if (selected) setDrafts((old) => ({ ...old, [selected.id]: draft }))
          setSelected(null)
        }}
        onConfirm={() => void submit()}
        label="发布回复"
      >
        <Notice error={blocked}>{notice}</Notice>
        {selected && (
          <p className="text-sm text-muted-foreground">
            {selected.order_no} · {selected.content}
          </p>
        )}
        {selected?.reply_content && (
          <div className="rounded-[8px] bg-muted/40 p-3 text-sm">
            <strong className="text-xs">
              当前
              {selected.reply_author === "STAFF"
                ? "客服"
                : selected.reply_author === "PROVIDER"
                  ? "本人"
                  : ""}
              回复
            </strong>
            <p className="mt-1 whitespace-pre-wrap">{selected.reply_content}</p>
          </div>
        )}
        {selected?.reply_content && selected.reply_author !== "PROVIDER" && (
          <Notice>保存后将替换当前回复，并显示为你的回复。</Notice>
        )}
        <Field label="回复内容">
          <Textarea
            aria-label="回复内容"
            placeholder="例如 服务很满意，希望继续改进…"
            rows={4}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={1000}
          />
        </Field>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void latest()}
          >
            读取最新回复
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              void navigator.clipboard
                .writeText(draft)
                .catch(() => setNotice("复制失败，请手动复制。"))
            }
          >
            复制草稿
          </Button>
        </div>
      </Confirm>
    </div>
  )
}
