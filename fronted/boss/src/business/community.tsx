import { useCurrentStore as useCheckinStore } from "@xa/shared"
import { ProviderName } from "./provider-name"
import { AlertCircle as CheckinAlert, LoaderCircle as CheckinLoader, RefreshCw as CheckinRefresh, LockKeyhole as CheckinLock, Gift as CheckinGift } from "lucide-react"
import * as CheckinDialog from "@/components/ui/dialog"
import { notifyChange as notifyCheckinChange, uncertain as checkinUncertain } from "./ui"
import { coinText, coinUnit, storeBrandLogo } from "@xa/shared"
import { useCallback, useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"
import { Award, Trophy, ChevronRight, Crown, Heart, Waves, WalletCards, CalendarCheck, Check, ChevronDown, ChevronUp, Clock3, ReceiptText, Sparkles } from "lucide-react"
import { formatRmb, couponPresentation } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import {
  api,
  Choice,
  date,
  Field,
  Image,
  Info,
  LinkButton,
  label,
  money,
  Panel,
  Pending,
  queryUrl,
  readIntent,
  requestId,
  rows,
  State,
  storeIntent,
  submitIntent,
  Title,
  BackLink,
  useAction,
  useConfirm,
  useExpiryReload,
  useLoad,
  useQuery,
  type Intent,
  type Row,
} from "./ui"
import "./lottery.css"
import { ArrowRight as PointsArrowRight, CircleDollarSign as PointsBalance, Copy as PointsCopy, Gift as PointsGift, Headphones as PointsHeadphones, History as PointsHistory, Link2 as PointsLink, ShieldCheck as PointsShield, Ticket as PointsTicket } from "lucide-react"
import { EmptySurface } from "./visual-states"

export function MembershipPage() {
  const store = useCheckinStore()
  const load = useLoad(() => api.get<any>("/club/me/")),
    [selected, setSelected] = useState<string[]>([]),
    [dirty, setDirty] = useState(false),
    [birthday, setBirthday] = useState(""),
    [birthdayDirty, setBirthdayDirty] = useState(false),
    [tier, setTier] = useState(""),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  useEffect(() => {
    if (!load.data) return
    if (!dirty) setSelected(load.data.selected_tags || [])
    if (!birthdayDirty) setBirthday(load.data.birthday || "")
    setTier(
      (value) =>
        value || String(load.data.vip?.id || load.data.tiers?.[0]?.id || "")
    )
  }, [load.data])
  const member = load.data,
    shownTier = rows(member?.tiers).find((item) => String(item.id) === tier),
    vipLevel = Math.min(6, Math.max(1, Number(member?.vip?.level) || 1)),
    vipMaterial = ["冰蓝亚克力", "雾银亚克力", "淡金亚克力", "晶铂亚克力", "极光亚克力", "曜黑亚克力"][vipLevel - 1]
  async function update(payload: Row) {
    await api.patch("/club/me/", payload)
    if ("selected_tags" in payload) setDirty(false)
    if ("birthday" in payload) setBirthdayDirty(false)
    load.reload()
  }
  function toggle(key: string) {
    if (selected.includes(key))
      setSelected((values) => values.filter((item) => item !== key))
    else if (selected.length < 5) setSelected((values) => [...values, key])
    else {
      toast.error("最多展示 5 个标签")
      return
    }
    setDirty(true)
  }
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title
        title="我的会员"
        description="已确认的实销用于会员成长，具体权益以当前等级为准"
      />
      <State {...load}>
        {member && (
          <>
            <Panel className={`boss-member-summary vip-level-${vipLevel}`}>
              <span className="boss-member-orbit" aria-hidden="true" /><Waves className="boss-member-waves" size={128} strokeWidth={1} aria-hidden="true" />
              <span className="boss-member-card-chip" aria-hidden="true" />
              <span className="boss-member-card-material">{vipMaterial}</span>
              <img className="boss-member-brand-mark" src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" width={32} height={32} />
              <div className="boss-member-level"><div><p><Crown size={14} />当前身份</p><h2>{member.vip?.name || member.boss_type?.name || "会员成长"}</h2></div><Badge variant="outline">{!rows(member.tiers).length ? "等级待配置" : member.next_vip ? "成长中" : member.vip ? "当前最高等级" : "等级待确认"}</Badge></div>
              <div className="boss-member-consumption"><div><p>累计确认实销</p><strong><small>¥</small>{formatRmb(member.consumption_amount)}</strong></div><div><p>待确认实销</p><strong>¥ {formatRmb(member.pending_consumption_amount)}</strong></div></div>
              <p className="boss-member-growth-note">已确认实销用于会员成长，待确认金额尚未计入。</p>
              <div className="boss-member-growth"><div><div><p>{member.next_vip ? "下一等级" : "等级进度"}</p><h3>{member.next_vip?.name || (member.vip ? "已达到当前最高等级" : "暂无可升级等级")}</h3></div>{member.next_vip && <div><p>升级还差</p><strong>¥ {formatRmb(Math.max(0, member.next_vip.consumption_threshold - member.consumption_amount))}</strong></div>}</div>{rows(member.tiers).length > 0 && <Progress className="boss-member-progress" aria-label="会员成长进度" value={member.next_vip ? Math.min(100, Math.max(0, member.consumption_amount / Math.max(1, member.next_vip.consumption_threshold) * 100)) : member.vip ? 100 : 0} />}<div className="boss-member-growth-range"><span>当前 ¥ {formatRmb(member.consumption_amount)}</span><span>{member.next_vip ? "目标 ¥ " + formatRmb(member.next_vip.consumption_threshold) : rows(member.tiers).length ? "继续保持，享受会员权益" : "会员等级暂未配置"}</span></div></div>
              <div className="boss-member-benefit"><div><p>当前权益</p><strong>下单 {Number(member.vip?.discount_rate || member.boss_type?.discount_rate || 100) / 10} 折</strong></div><Button asChild variant="outline"><Link to="/club-content?kind=RIGHTS&from=membership">预存福利<ChevronRight size={14} /></Link></Button></div>
              <div className="boss-member-related-links"><Button asChild variant="ghost"><Link to="/coupons?tab=mine">我的优惠券<ChevronRight size={14} /></Link></Button><Button asChild variant="ghost"><Link to="/points?tab=claims">会员权益记录<ChevronRight size={14} /></Link></Button></div>
            </Panel>
            <Panel title="会员权益">
              <div className="boss-form" style={{ marginTop: 20 }}>
                {rows(member.tiers).length ? (
                  <div className="boss-member-tier">
                    <Choice
                      label="查看等级权益"
                      value={tier}
                      onChange={setTier}
                      options={rows(member.tiers).map((item) => ({
                        value: item.id,
                        label: item.name,
                      }))}
                    />
                  </div>
                ) : (
                  <p className="boss-muted">会员等级暂未配置</p>
                )}
                {shownTier && (
                  <>
                    <p className="boss-muted">
                      {shownTier.id === member.vip?.id
                        ? "当前等级已生效"
                        : "正在预览此等级，不影响当前权益"}
                    </p>
                    <Info
                      entries={[
                        [
                          "预览等级折扣",
                          Number(shownTier.discount_rate) / 10 + " 折",
                        ],
                        ...Object.entries(shownTier.benefits || {}).map(
                          ([name, value]) =>
                            [
                              name,
                              Array.isArray(value)
                                ? value.join("、")
                                : String(value),
                            ] as [string, string]
                        ),
                      ]}
                    />
                  </>
                )}
              </div>
            </Panel>
            <Panel title="展示标签">
              <p className="boss-muted">
                最多选择 5 个标签。标签是否可用及有效时间由平台核定。
              </p>
              <div className="boss-member-tags">
                {rows(member.tags).map((item) => (
                  <Button
                    variant={
                      selected.includes(item.key) ? "secondary" : "outline"
                    }
                    key={item.key}
                    className="boss-choice-button"
                    aria-pressed={selected.includes(item.key)}
                    disabled={busy}
                    onClick={() => toggle(item.key)}
                  >
                    {selected.includes(item.key) && <Check size={14} />}
                    {item.title}
                    {item.expires_at ? " · " + date(item.expires_at) : ""}
                  </Button>
                ))}
              </div>
              {!rows(member.tags).length && (
                <p className="boss-muted">尚无可展示标签，可查看成就和权益。</p>
              )}
              <div className="boss-actions">
                <Button
                  disabled={busy || !dirty || load.loading || !!load.error}
                  onClick={() =>
                    void run(
                      () => update({ selected_tags: selected }),
                      "展示标签已保存"
                    ).catch(() => {})
                  }
                >
                  保存展示标签
                </Button>
                {dirty && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setSelected(member.selected_tags || [])
                      setDirty(false)
                    }}
                  >
                    恢复已保存标签
                  </Button>
                )}
                <Button asChild variant="outline">
                  <Link to="/achievements">查看成就</Link>
                </Button>
              </div>
            </Panel>
            <Panel title="生日设置">
              <div className="boss-form">
                <Field
                  label="生日"
                  type="date"
                  value={birthday}
                  onChange={(value) => {
                    setBirthday(value)
                    setBirthdayDirty(true)
                  }}
                  disabled={busy || !!member.birthday}
                  hint={
                    member.birthday
                      ? "生日登记后修改需联系客服核对"
                      : "生日用于生日祝福和当日消费积分规则"
                  }
                />
                {!member.birthday && (
                  <Button
                    disabled={busy || !birthday || load.loading || !!load.error}
                    onClick={() =>
                      confirm({
                        title: "确认登记生日",
                        description: `登记生日为 ${birthday}。登记后修改需要客服核对。`,
                        confirm: "确认登记",
                        action: () => update({ birthday }),
                      })
                    }
                  >
                    登记生日
                  </Button>
                )}
                <label className="boss-checkbox">
                  <input
                    name="birthday_public"
                    autoComplete="off"
                    type="checkbox"
                    checked={!!member.birthday_public}
                    disabled={busy || load.loading}
                    onChange={(event) =>
                      void run(
                        () => update({ birthday_public: event.target.checked }),
                        "生日展示设置已保存"
                      ).catch(() => {})
                    }
                  />
                  允许生日祝福公开展示
                </label>
              </div>
            </Panel>
          </>
        )}
      </State>
      {dialog}
    </>
  )
}

export function CheckinPage() {
  const store = useCheckinStore()
  const load = useLoad(() => api.get<any>("/users/checkin/")), calendar = load.data
  const [busy, setBusy] = useState(false), [refreshing, setRefreshing] = useState(false), [makeupDay, setMakeupDay] = useState<number | null>(null), [issue, setIssue] = useState(""), [feedback, setFeedback] = useState(""), [pendingDate, setPendingDate] = useState<{ year: number; month: number; day: number } | null>(null)
  const busyRef = useRef(false)
  const checked: number[] = calendar?.checked_days || [], available: number[] = calendar?.makeup_available_days || [], blanks = calendar ? new Date(calendar.year, calendar.month - 1, 1).getDay() : 0
  const locked = busy || refreshing || load.loading || !!load.error || !!issue
  async function submit(day?: number) {
    if (busyRef.current || locked || !calendar) return
    if (day ? !available.includes(day) || !calendar.makeup_cards : calendar.today_checked || !calendar.today_eligible) return
    busyRef.current = true
    setBusy(true)
    setFeedback("")
    setPendingDate({ year: calendar.year, month: calendar.month, day: day || calendar.today })
    try {
      const result = await api.post<any>("/users/checkin/", day ? { day } : {})
      setFeedback(`${day ? "补签" : "签到"}成功${result.reward_amount != null ? `，获得 ${money(result.reward_amount)} ${coinUnit()}` : ""}。`)
      setPendingDate(null)
      if (result.full_attendance_awarded) toast.success(calendar.full_attendance_reward?.name || "已达成本月全勤奖励")
      notifyCheckinChange()
    } catch (error: any) {
      setIssue(checkinUncertain(error) ? `${day ? "补签" : "签到"}结果暂未确认，请刷新日历核对，避免重复操作。` : error?.message || "签到未完成，请刷新日历核对。")
    } finally {
      busyRef.current = false
      setBusy(false)
      setMakeupDay(null)
      load.reload()
    }
  }
  async function refreshStatus() {
    if (busyRef.current) return
    busyRef.current = true
    setRefreshing(true)
    setFeedback("")
    try {
      const latest = await api.get<any>("/users/checkin/")
      load.setData(latest)
      setFeedback(pendingDate && latest.year === pendingDate.year && latest.month === pendingDate.month && latest.checked_days.includes(pendingDate.day) ? `${latest.month} 月 ${pendingDate.day} 日已在服务器日历中记录，请在钱包核对奖励流水。` : "签到日历已刷新，请以当前日历与钱包记录为准。")
      setIssue("")
      setPendingDate(null)
      load.reload()
    } catch (error: any) {
      setIssue(`签到状态暂时无法核对：${error?.message || "请检查网络后重试"}。请勿重复提交。`)
    } finally {
      busyRef.current = false
      setRefreshing(false)
    }
  }
  function openMakeup(day: number) {
    if (locked || !available.includes(day) || !calendar) return
    setFeedback("")
    if (!calendar.makeup_cards) {
      setFeedback(`暂无补签卡，当日消费满 ${money(calendar.makeup_card_spend_required)} ${coinUnit()}可获得补签卡，最多持有 ${calendar.max_makeup_cards} 张。`)
      return
    }
    setMakeupDay(day)
  }
  return <>
    <div className="boss-checkin-topline"><BackLink to="/mine">我的</BackLink><Badge variant="outline">每日签到</Badge></div>
    <header className="boss-checkin-header"><p>{store.name} · 每日回响</p><h1>每一天，都有回响</h1><p>完成当日消费签到，领取本月专属奖励</p></header>
    <State {...load}>
      {calendar && <div className="boss-checkin-content">
        <Panel className="boss-checkin-summary"><div className="boss-stats"><div><strong>{calendar.checked_count}<small>天</small></strong><span>本月已签</span></div><div><strong>{calendar.continuous_days}<small>天</small></strong><span>连续签到</span></div><div><strong>{calendar.makeup_cards}<small>张</small></strong><span>可用补签卡</span></div></div></Panel>
        <Panel className="boss-checkin-spend"><div className="boss-section-heading"><div><WalletCards size={16} /><h2>今日消费</h2></div><Badge variant={calendar.today_eligible ? "outline" : "secondary"} className={calendar.today_eligible ? "boss-available" : undefined}>{calendar.today_eligible ? "已达标" : "未达标"}</Badge></div><strong>{money(calendar.today_spend)}<small>/ {money(calendar.daily_spend_required)} {coinUnit()}</small></strong><Progress value={Math.min(100, Number(calendar.today_spend) / Math.max(1, Number(calendar.daily_spend_required)) * 100)} aria-label="今日签到消费进度" /><p>当日消费满 {money(calendar.makeup_card_spend_required)} {coinUnit()}可获得补签卡，最多持有 {calendar.max_makeup_cards} 张。</p></Panel>
        {issue && <section role="alert" className="boss-checkin-issue"><div><CheckinAlert size={16} /><p>{issue}</p></div><Button variant="outline" disabled={busy || refreshing} onClick={() => void refreshStatus()}>{refreshing ? <CheckinLoader size={16} className="animate-spin" /> : <CheckinRefresh size={16} />}{refreshing ? "正在核对签到状态" : "刷新签到状态"}</Button></section>}
        {feedback && <div role="status" className="boss-checkin-feedback"><Check size={16} /><p>{feedback}</p></div>}
        <Panel className="boss-checkin-calendar"><div className="boss-checkin-calendar-heading"><h2>{calendar.year} 年 {calendar.month} 月</h2><span>标有「补签」的日期可点击</span></div><div className="boss-calendar">
          {["日", "一", "二", "三", "四", "五", "六"].map(day => <strong key={day}>{day}</strong>)}
          {Array.from({ length: blanks }, (_, index) => <span key={"blank-" + index} />)}
          {Array.from({ length: calendar.days_in_month }, (_, offset) => offset + 1).map(day => <button type="button" key={day} className={(checked.includes(day) ? "checked " : "") + (available.includes(day) ? "makeup " : "") + (calendar.today === day ? "today" : "")} disabled={locked || !available.includes(day)} aria-label={`${calendar.month} 月 ${day} 日，${checked.includes(day) ? "已签到" : available.includes(day) ? "可补签" : calendar.today === day ? "今天" : "不可补签"}`} onClick={() => openMakeup(day)}><span>{day}</span><small>{checked.includes(day) ? "已签" : available.includes(day) ? "补签" : calendar.today === day ? "今天" : ""}</small></button>)}
        </div><Button disabled={locked || calendar.today_checked || !calendar.today_eligible} onClick={() => void submit()}>{busy ? <CheckinLoader size={16} className="animate-spin" /> : calendar.today_checked ? <Check size={16} /> : <CalendarCheck size={16} />}{busy ? "正在提交签到" : calendar.today_checked ? "今日已签到" : calendar.today_eligible ? "立即签到" : `再消费 ${money(Math.max(0, calendar.daily_spend_required - calendar.today_spend))} ${coinUnit()}可签到`}</Button></Panel>
        <Panel className="boss-checkin-rewards"><div className="boss-checkin-rewards-heading"><h2>本月签到礼物</h2><span>按累计签到天数达成</span></div>{!rows(calendar.rewards).length ? <div className="boss-checkin-rewards-empty"><CheckinGift size={24} /><p>本期奖励待公布</p><small>签到记录会继续保留</small></div> : <div className="boss-checkin-reward-scroll" tabIndex={0} role="region" aria-label="本月签到礼物，可左右滑动">{rows(calendar.rewards).map(item => <article key={item.seq} className={calendar.checked_count >= item.seq ? "reached" : ""}><div><span>第 {item.seq} 天</span>{calendar.checked_count >= item.seq && <Check size={14} />}</div><h3>{item.name || "签到奖励"}</h3>{item.amount != null && <strong>{money(item.amount)}<small>{coinUnit()}</small></strong>}<p>{calendar.checked_count >= item.seq ? "已达成" : item.description || "按累计签到天数达成"}</p></article>)}</div>}</Panel>
        {calendar.full_attendance_reward && <section className="boss-checkin-full"><span><Trophy size={20} /></span><div><div><h2>{calendar.full_attendance_reward.name || "本月全勤奖励"}</h2><Badge variant="outline">{calendar.full_attendance ? "已获得" : "全勤奖励"}</Badge></div><p>{calendar.full_attendance_reward.description || "完成本月全部签到后，可获得全勤奖励。"}</p></div></section>}
        <Button asChild variant="ghost" className="boss-checkin-wallet"><Link to="/wallet"><Sparkles size={14} />签到奖励可在钱包流水中查看<ChevronRight size={14} /></Link></Button>
        <div className="boss-checkin-server-note"><CheckinLock size={16} /><p>签到与补签以服务器日历为准。结果未知时先刷新核对，不重复消耗补签卡。</p></div>
      </div>}
    </State>
    <CheckinDialog.Dialog open={makeupDay !== null} onOpenChange={open => { if (!open && !busy) setMakeupDay(null) }}><CheckinDialog.DialogContent className="boss-checkin-makeup-dialog" showCloseButton={false} closeDisabled={busy}><CheckinDialog.DialogHeader><span className="boss-checkin-makeup-icon"><CalendarCheck size={20} /></span><CheckinDialog.DialogTitle>补签 {calendar?.month} 月 {makeupDay} 日</CheckinDialog.DialogTitle><CheckinDialog.DialogDescription>将消耗 1 张补签卡，当前共有 {calendar?.makeup_cards} 张。补签结果以服务器日历为准。</CheckinDialog.DialogDescription></CheckinDialog.DialogHeader><p className="boss-checkin-makeup-note">结果未知时请先刷新签到日历，核对日期和补签卡后再操作。</p><CheckinDialog.DialogFooter><Button variant="outline" disabled={busy} onClick={() => setMakeupDay(null)}>再想想</Button><Button disabled={locked || !calendar?.makeup_cards || makeupDay === null || !available.includes(makeupDay)} onClick={() => makeupDay != null && void submit(makeupDay)}>{busy && <CheckinLoader size={16} className="animate-spin" />}确认补签</Button></CheckinDialog.DialogFooter></CheckinDialog.DialogContent></CheckinDialog.Dialog>
  </>
}

export function AchievementsPage() {
  const load = useLoad(() => api.get<any>("/users/achievements/"))
  const items = rows(load.data), active = items.filter(item => !item.unlocked).sort((a, b) => Number(b.current || 0) / Math.max(1, Number(b.target || 1)) - Number(a.current || 0) / Math.max(1, Number(a.target || 1))), unlocked = items.filter(item => item.unlocked)
  return <><BackLink to="/mine">个人中心</BackLink><Title title="我的成就" description="每一次并肩，都值得被记住" /><State {...load} empty={!items.length}>
    {items.length > 0 && <section className="boss-achievement-summary"><div><p>成就收集进度</p><strong>{unlocked.length}<small> / {items.length}</small></strong><span>已点亮 {unlocked.length} 枚俱乐部印记</span></div><Trophy size={28} /><Progress value={unlocked.length / items.length * 100} aria-label="成就收集进度" /><div className="boss-achievement-summary-stats"><div><strong>{active.length}</strong><span>进行中</span></div><div><strong>{unlocked.length}</strong><span>已达成</span></div></div></section>}
    {active.length > 0 && <section className="boss-achievement-group"><div className="boss-section-heading"><h2>继续点亮</h2><Badge variant="secondary">{active.length} 项进行中</Badge></div><div className="boss-achievement-active">{active.map((item, index) => <article className={index === 0 ? "boss-achievement-card nearest" : "boss-achievement-card"} key={item.code || item.id}><div className="boss-achievement-card-title"><Award size={22} /><div><h3>{item.title || item.name}</h3><p>{item.desc || item.description}</p></div><Badge variant={index === 0 ? "default" : "secondary"}>{index === 0 ? "最近目标" : "进行中"}</Badge></div><div className="boss-achievement-progress-label"><span>{item.metric === "amount" ? "累计净消费" : "完成订单"}</span><strong>{item.metric === "amount" ? money(item.current) : item.current}<small> / {item.metric === "amount" ? money(item.target) : item.target}{item.metric === "amount" ? coinText(" 浪花") : " 单"}</small></strong></div><Progress value={Math.min(100, Number(item.current || 0) / Math.max(1, Number(item.target || 1)) * 100)} aria-label={(item.title || item.name) + "完成进度"} /></article>)}</div></section>}
    {unlocked.length > 0 && <section className="boss-achievement-group"><h2>已达成</h2><div className="boss-achievement-unlocked">{unlocked.map(item => <article key={item.code || item.id}><Award size={20} /><h3>{item.title || item.name}</h3><span>已达成</span><p>{item.metric === "amount" ? money(item.current) + coinText(" 浪花") : item.current + " 单"}</p></article>)}</div></section>}
    <section className="boss-achievement-links"><Link to="/orders?status=completed"><ReceiptText size={18} /><span><strong>查看完成订单</strong><small>订单状态与净消费以实际记录为准</small></span><ChevronRight size={16} /></Link><Link to="/membership"><Sparkles size={18} /><span><strong>设置会员名片标签</strong><small>已达成成就可以展示在个人名片</small></span><ChevronRight size={16} /></Link></section>
  </State></>
}

export function RankingsPage() {
  const [period, setPeriod] = useState("month"),
    load = useLoad(
      () => api.get<any>("/orders/rankings/?period=" + period),
      [period]
    )
  return <><BackLink to="/mine">个人中心</BackLink><Title title="一起见证高光时刻" description="门店消费与陪玩接单榜单" /><div className="boss-tabs" role="group" aria-label="榜单周期">{[["day", "今日"], ["week", "本周"], ["month", "本月"]].map(([value, title]) => <Button key={value} variant={period === value ? "secondary" : "ghost"} aria-pressed={period === value} onClick={() => setPeriod(value)}>{title}</Button>)}</div><State {...load}>
    <section className="boss-ranking-overview"><Crown size={28} /><div><h2>每份支持，都有回响</h2><p>{period === "day" ? "今日" : period === "week" ? "本周" : "本月"}门店榜单</p></div></section>
    <div className="boss-ranking-groups">{[["consume_rank", "消费榜"], ["order_rank", "陪玩接单榜"]].map(([key, title]) => <section className="boss-ranking-group" key={key}><header><Trophy size={18} /><h2>{title}</h2><Badge variant="secondary">{rows(load.data?.[key]).length} 位</Badge></header>{rows(load.data?.[key]).length ? rows(load.data?.[key]).map((item, index) => <div className="boss-ranking-row" key={item.user_id || index}><span className={index < 3 ? "top" : undefined}>{index + 1}</span><Image src={item.avatar} alt={item.nickname || item.name} className="boss-ranking-avatar" /><div>{key === "order_rank" && Number(item.provider_account_id) > 0 ? <Link className="inline-flex min-h-11 items-center" to={"/companion-detail?id=" + item.provider_account_id}><ProviderName value={item.nickname || item.name} /></Link> : <strong>{key === "order_rank" ? <ProviderName value={item.nickname || item.name} /> : item.nickname || item.name}</strong>}</div><p>{money(item.total_amount)}<small>{coinUnit()}</small></p></div>) : <div className="boss-ranking-empty"><Trophy size={24} /><p>本时段暂无榜单记录</p></div>}</section>)}</div>
  </State></>
}

export function CouponsPage() {
  const query = useQuery(),
    navigate = useNavigate(),
    [tab, setTab] = useState(
      query.get("tab") === "mine" ? "mine" : "available"
    ),
    [status, setStatus] = useState("UNUSED"),
    load = useLoad(
      () => api.get<any>(tab === "mine" ? "/coupons/mine/" : "/coupons/"),
      [tab]
    ),
    [claimIssue, setClaimIssue] = useState(""),
    [expanded, setExpanded] = useState<number | null>(null),
    { busy, run } = useAction()
  const visible = rows(load.data).filter(
    (item) =>
      tab !== "mine" || (item.availability_status || item.status) === status
  )
  async function claim(item: Row) {
    setClaimIssue("")
    try { await api.post(`/coupons/${item.id}/claim/`) }
    catch (error: any) { if (!error?.status || error.status >= 500) setClaimIssue("领取结果待确认，请先查看我的券，避免重复领取。"); throw error }
    finally { load.reload() }
  }
  async function useCoupon(item: Row) {
    const current = rows(await api.get("/coupons/mine/?usable=1")).find(
      (row) => row.id === item.id
    )
    if (!current) {
      load.reload()
      throw new Error("优惠券状态已变化，请重新查看")
    }
    if (Array.isArray(current.service_ids) && !current.service_ids.length)
      throw new Error("适用套餐待配置，请联系客服")
    navigate("/catalog?coupon_id=" + current.id)
  }
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title title="优惠券" description="领取适用优惠，结算时核对最终报价" />
      <div className="boss-tabs">
        <Button
          variant={tab === "available" ? "secondary" : "ghost"} aria-pressed={tab === "available"}
          onClick={() => setTab("available")}
        >
          领券中心
        </Button>
        <Button
          variant={tab === "mine" ? "secondary" : "ghost"} aria-pressed={tab === "mine"}
          onClick={() => setTab("mine")}
        >
          我的券
        </Button>
      </div>
      {tab === "mine" && (
        <div className="boss-tabs">
          {[
            ["UNUSED", "待使用"],
            ["USED", "已使用"],
            ["EXPIRED", "已过期"],
            ["DISABLED", "已停用"],
          ].map(([value, title]) => (
            <Button
              key={value}
              variant={status === value ? "secondary" : "ghost"} aria-pressed={status === value}
              onClick={() => setStatus(value)}
            >
              {title}
            </Button>
          ))}
        </div>
      )}
      {claimIssue && <div className="boss-feedback boss-pending-operation" role="alert"><p>{claimIssue}</p><Button variant="outline" onClick={() => { setTab("mine"); setStatus("UNUSED"); setClaimIssue("") }}>查看我的券</Button></div>}
      <State {...load} empty={!visible.length}>
        <div className="boss-grid">
          {visible.map((item) => (
            <article className={"boss-coupon-card" + (tab === "mine" && ["USED", "EXPIRED", "DISABLED"].includes(item.status) ? " inactive" : "")} key={item.id}><div className="boss-coupon-benefit">{couponPresentation(item).benefitText}</div><div className="boss-coupon-body"><div className="boss-coupon-title"><h2>{item.name}</h2>{tab === "mine" && status !== "UNUSED" && <Badge variant="secondary">{label(item.status || status)}</Badge>}</div><p className="boss-coupon-threshold">{couponPresentation(item).thresholdText}</p><p className="boss-muted boss-coupon-validity"><Clock3 size={14} />{item.valid_to ? date(item.valid_to) : item.experience_kind === "DAILY_VALUE" ? "领取当日有效" : "永久有效"}</p><div className="boss-coupon-actions"><button type="button" className="boss-coupon-toggle" aria-expanded={expanded === item.id} aria-controls={"boss-coupon-details-" + item.id} onClick={() => setExpanded(value => value === item.id ? null : item.id)}>{expanded === item.id ? "收起说明" : "使用说明"}{expanded === item.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</button>
              {tab === "mine" ? status === "UNUSED" ? (
                <Button variant="outline"
                  disabled={
                    busy || status !== "UNUSED" || load.loading || !!load.error
                  }
                  onClick={() =>
                    void run(() => useCoupon(item), "").catch(() => {})
                  }
                >
                  查看可用服务
                </Button>
              ) : null : (
                <Button variant="outline"
                  disabled={
                    busy ||
                    !couponPresentation(item).claimable ||
                    load.loading ||
                    !!load.error
                  }
                  onClick={() =>
                    void run(
                      () => claim(item),
                      "领取成功，可在我的券查看"
                    ).catch(() => {})
                  }
                >
                  {couponPresentation(item).claimLabel}
                </Button>
              )}
            </div></div>{expanded === item.id && <div className="boss-coupon-expanded" id={"boss-coupon-details-" + item.id}>
              <Info
                entries={[
                  ["使用门槛", couponPresentation(item).thresholdText],
                  [
                    "适用套餐",
                    Array.isArray(item.service_ids)
                      ? item.service_ids.length + " 款指定套餐"
                      : item.scope_variant || item.scope,
                  ],
                  ["可用份数", item.limit_units || undefined],
                  [
                    "有效期",
                    item.valid_to
                      ? date(item.valid_to)
                      : item.experience_kind === "DAILY_VALUE"
                        ? "领取当日有效"
                        : "永久有效",
                  ],
                  [
                    "最高优惠",
                    item.max_discount
                      ? "¥" + formatRmb(item.max_discount)
                      : undefined,
                  ],
                  [
                    "平台",
                    item.platform === "PC"
                      ? "端游"
                      : item.platform === "MOBILE"
                        ? "手游"
                        : undefined,
                  ],
                ]}
              />
              <p className="boss-muted" style={{ marginBlock: 12 }}>
                {item.description}
                {couponPresentation(item).rateNotice}
              </p>
              </div>}</article>
          ))}
        </div>
      </State>
    </>
  )
}

export function FavoritesPage() {
  const load = useLoad(() => api.get<any>("/orders/favorites/")),
    [undoItem, setUndoItem] = useState<Row | null>(null),
    { busy, run } = useAction()
  async function undo() {
    if (!undoItem) return
    const original = undoItem
    setUndoItem(null)
    try {
      const latest = rows(await api.get("/orders/favorites/"))
      if (!latest.some((item) => item.id === original.id)) {
        const result = await api.post<any>("/orders/favorites/toggle/", {
          service_id: original.id,
        })
        if (!result.favorited) throw new Error("收藏状态已变化，请刷新列表核对")
      }
    } finally {
      load.reload()
    }
  }
  return <><BackLink to="/mine">个人中心</BackLink><Title title="我的收藏" description="进入服务详情后可选择陪玩并下单" action={<Heart size={22} className="text-primary" />} />{undoItem && <div className="boss-favorite-undo" role="status"><Heart size={18} /><div><h2>已取消收藏</h2><p>{undoItem.name}</p>{undoItem.available === false && <small>服务暂不可购买，无法恢复</small>}</div>{undoItem.available !== false && <Button variant="outline" disabled={busy} onClick={() => void run(undo, "已恢复收藏").catch(() => {})}>{busy ? "恢复中…" : "撤销"}</Button>}</div>}
    <State {...load} empty={!rows(load.data).length}><div className="boss-favorites-count"><p><strong>{rows(load.data).length}</strong> 个收藏服务</p><span>价格以详情页实时报价为准</span></div><div className="boss-favorites-list">{rows(load.data).map(item => <article className="boss-favorite-card" key={item.id}><LinkButton variant="ghost" to={"/service?id=" + item.id} disabled={item.available === false}><Image src={item.cover_url || item.gameplay_cover_url} alt={item.name} className="boss-favorite-thumb" /><span><span className="boss-favorite-name">{item.name}{item.available === false && <Badge variant="secondary">暂不可购买</Badge>}</span><span className="boss-favorite-description">{item.description}</span><span className="boss-price">{money(item.price)} <small>{coinText("浪花 / ")}{item.price_unit || "份"}</small></span></span><ChevronRight size={16} /></LinkButton><footer><span><Heart size={12} />{item.available === false ? "服务状态已变化" : "已收藏 · 可查看详情"}</span><Button variant="ghost" disabled={busy} onClick={() => void run(async () => { try { await api.delete(`/orders/favorites/${item.id}/`); setUndoItem(item) } finally { load.reload() } }, "已取消收藏").catch(() => {})}>{busy ? "处理中…" : "取消收藏"}</Button></footer></article>)}</div><Button asChild variant="outline"><Link to="/catalog">探索更多服务<ChevronRight size={16} /></Link></Button></State>
  </>
}

const rewardTerms = (row: Row) => ({
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
export function PointsPage() {
  const store = useCheckinStore(),
    query = useQuery(),
    navigate = useNavigate(),
    tab = query.get("tab") || "shop",
    [kind, setKind] = useState(""),
    [claimBefore, setClaimBefore] = useState(""),
    [ledgerBefore, setLedgerBefore] = useState(""),
    [intent, setIntent] = useState<Intent | null>(() =>
      readIntent("redemption")
    ),
    [binding, setBinding] = useState<Row | null>(null),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const load = useLoad(async () => {
    const [points, rewards, claims, kook] = await Promise.all([
      api.get<any>(queryUrl("/club/points/", { before: ledgerBefore })),
      api.get<any>("/club/rewards/"),
      api.get<any>(
        queryUrl("/club/claims/", { page_size: 20, before: claimBefore })
      ),
      api.get<any>("/club/kook-binding/"),
    ])
    return { points, rewards: rows(rewards), claims, kook }
  }, [claimBefore, ledgerBefore])
  async function redeem(original: Intent) {
    try {
      const claim = await submitIntent("redemption", original)
      navigate("/reward-claim?id=" + claim.id)
    } finally {
      setIntent(readIntent("redemption"))
      load.reload()
    }
  }
  function prepare(reward: Row) {
    const description = [
      reward.description,
      reward.kind === "BALANCE"
        ? `到账 ${money(reward.balance_amount)} ${coinUnit()}`
        : "",
      reward.kind === "POINTS" ? `获得 ${reward.point_value} 积分` : "",
      reward.duration_months ? `${reward.duration_months} 个月` : "",
      reward.duration_days ? `${reward.duration_days} 天` : "",
    ]
      .filter(Boolean)
      .join("；")
    confirm({
      title: "确认积分兑换",
      description: (
        <>
          <p>
            使用 {reward.point_cost} 积分兑换「{reward.name}」。
          </p>
          <p>{description}</p>
          <p>兑换后可在权益记录中查看结果及补充交付资料。</p>
        </>
      ),
      confirm: "确认兑换",
      action: async () => {
        if (readIntent("redemption")) throw new Error("请先核对上次兑换")
        const original = {
          id: requestId("redeem"),
          path: "/club/redeem/",
          payload: {
            reward_id: reward.id,
            expected_point_cost: reward.point_cost,
            expected_terms: rewardTerms(reward),
          },
        }
        storeIntent("redemption", original)
        setIntent(original)
        await redeem(original)
      },
    })
  }
  const lastSnapshot = useRef<any>(null)
  if (load.data) lastSnapshot.current = load.data
  const data =
      load.data || (claimBefore || ledgerBefore ? lastSnapshot.current : null),
    visibleRewards = rows(data?.rewards).filter(
      (item) => !kind || item.kind === kind
    )
  useExpiryReload(
    rows(data?.claims.items)
      .filter(
        (claim) =>
          claim.kind_snapshot === "TAG" &&
          claim.status === "COMPLETED" &&
          !claim.tag_expired
      )
      .map((claim) => claim.expires_at),
    load.reload
  )
  const rewardKinds = [
    ["", "全部"],
    ["PHYSICAL", "实物礼物"],
    ["VIRTUAL", "虚拟物品"],
    ["SERVICE", "服务权益"],
    ["TAG", "专属标签"],
    ["NAMING", "专属冠名"],
    ["BALANCE", coinText("浪花兑换")],
    ["POINTS", "积分奖励"],
  ]
  const sourceLabel = (value: string) =>
    ({
      REDEMPTION: "积分兑换",
      LOTTERY: "幸运转盘",
      VIP: "会员权益",
      GIFT: "赠礼权益",
    })[value] || value
  const locked = busy || !!intent || load.loading || !!load.error
  return (
    <div className="boss-points-page">
      <BackLink to="/mine">个人中心</BackLink>
      <header className="boss-points-header"><p>{store.name}</p><h1>我的积分</h1><p>积分、兑换与权益记录</p></header>
      <Pending
        busy={busy}
        intent={intent}
        retry={() => {
          if (intent) void run(() => redeem(intent), "").catch(() => {})
        }}
      />
      <State
        {...load}
        data={data}
        errorAction={
          claimBefore || ledgerBefore ? (
            <Button
              variant="outline"
              onClick={() => {
                setClaimBefore("")
                setLedgerBefore("")
              }}
            >
              返回最新记录
            </Button>
          ) : undefined
        }
      >
        {data && (
          <div className="boss-points-sections">
            {load.error && (claimBefore || ledgerBefore) && (
              <p className="boss-points-stale" role="status">
                当前显示上次成功读取的记录，请重试加载或返回最新记录。
              </p>
            )}
            <section className="boss-points-hero" aria-label="积分账户">
              <span className="boss-points-orbit" aria-hidden="true" />
              <Trophy
                className="boss-points-trophy"
                strokeWidth={1}
                aria-hidden="true"
              />
              <div className="boss-points-hero-content">
                <p>可用积分</p>
                <strong>
                  {Number(data.points.points).toLocaleString()}
                  <small>积分</small>
                </strong>
                <div className="boss-points-hero-actions">
                  <Button asChild>
                    <Link to="/lottery">
                      <Sparkles size={16} />
                      幸运转盘
                      <PointsArrowRight size={14} />
                    </Link>
                  </Button>
                  <Button asChild variant="outline">
                    <Link to="/club-content?kind=POINT_RULES&from=points">积分规则</Link>
                  </Button>
                </div>
              </div>
            </section>
            {data.kook && (
              <section
                className="boss-points-kook"
                aria-labelledby="points-kook-title"
              >
                <header>
                  <span className="boss-points-kook-icon">
                    <PointsHeadphones size={20} />
                  </span>
                  <div>
                    <h2 id="points-kook-title">KOOK 积分</h2>
                    <p>
                      {data.kook.enabled
                        ? "绑定本人账号后，在线与签到自动计分"
                        : "当前 KOOK 积分活动未启用"}
                    </p>
                  </div>
                  <Badge variant="secondary">
                    {data.kook.bound
                      ? "已连接"
                      : data.kook.enabled
                        ? "待绑定"
                        : "未启用"}
                  </Badge>
                </header>
                {data.kook.bound && (
                  <div className="boss-points-kook-connected">
                    <p>
                      {data.kook.nickname ||
                        data.kook.kook_user_id ||
                        "已绑定本人账号"}
                      {data.kook.online_points != null &&
                        ` · 在线获得 ${data.kook.online_points} 积分`}
                    </p>
                    {data.kook.checked_in != null && (
                      <p>
                        {data.kook.checked_in ? "今日已签到" : "今日尚未签到"}
                      </p>
                    )}
                    {data.kook.rules && <p>{data.kook.rules}</p>}
                  </div>
                )}
                {!data.kook.bound && data.kook.enabled && (
                  <Button
                    variant="outline"
                    className="boss-points-bind"
                    disabled={busy || load.loading || !!load.error}
                    onClick={() =>
                      void run(async () => {
                        const result = await api.post<any>(
                          "/club/kook-binding/",
                          {}
                        )
                        setBinding({
                          ...result,
                          expires_at: new Date(
                            Date.now() + result.expires_in * 1000
                          ).toISOString(),
                        })
                      }, "").catch(() => {})
                    }
                  >
                    <PointsLink size={16} />
                    {busy ? "正在获取…" : "获取绑定码"}
                  </Button>
                )}
                {binding && !data.kook.bound && (
                  <div className="boss-points-binding">
                    <strong>绑定 {binding.code}</strong>
                    <p>{binding.instruction}</p>
                    <p>有效至 {date(binding.expires_at)}</p>
                    <Button
                      variant="outline"
                      onClick={() =>
                        void navigator.clipboard
                          .writeText("绑定 " + binding.code)
                          .then(() => toast.success("绑定指令已复制"))
                          .catch(() =>
                            toast.error("复制失败，请手动复制绑定码")
                          )
                      }
                    >
                      <PointsCopy size={16} />
                      复制绑定指令
                    </Button>
                  </div>
                )}
              </section>
            )}
            <div
              className="boss-points-tabs"
              role="tablist"
              aria-label="积分记录视图"
            >
              {[
                ["shop", "积分好礼"],
                ["claims", "权益记录"],
                ["ledger", "积分明细"],
              ].map(([value, title]) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={tab === value}
                  onClick={() => navigate(queryUrl("/points", { tab: value }))}
                >
                  {title}
                </button>
              ))}
            </div>
            {tab === "shop" && (
              <section aria-label="积分好礼">
                <div
                  className="boss-points-filters"
                  role="group"
                  aria-label="好礼分类"
                >
                  {rewardKinds.map(([value, title]) => (
                    <button
                      key={value || "all"}
                      type="button"
                      aria-pressed={kind === value}
                      onClick={() => setKind(value)}
                    >
                      {title}
                    </button>
                  ))}
                </div>
                {visibleRewards.length ? (
                  <div className="boss-points-rewards">
                    {visibleRewards.map((item) => {
                      const Icon =
                        item.kind === "BALANCE"
                          ? PointsBalance
                          : ["TAG", "NAMING"].includes(item.kind)
                            ? Sparkles
                            : ["SERVICE", "VIRTUAL"].includes(item.kind)
                              ? PointsTicket
                              : item.kind === "POINTS"
                                ? Trophy
                                : PointsGift
                      const soldOut = item.stock === 0
                      const insufficient = data.points.points < item.point_cost
                      return (
                        <article className="boss-points-reward" key={item.id}>
                          <div className="boss-points-reward-art">
                            {item.image_url ? (
                              <Image
                                src={item.image_url}
                                alt={item.name}
                                className="boss-points-reward-image"
                              />
                            ) : (
                              <Icon
                                size={32}
                                strokeWidth={1.4}
                                aria-hidden="true"
                              />
                            )}
                          </div>
                          <p className="boss-points-reward-kind">
                            {item.kind_label ||
                              rewardKinds.find(
                                ([value]) => value === item.kind
                              )?.[1] ||
                              "积分权益"}
                          </p>
                          <h2>{item.name}</h2>
                          <p className="boss-points-reward-price">
                            {Number(item.point_cost).toLocaleString()}
                            <span>积分</span>
                          </p>
                          <p className="boss-points-reward-stock">
                            {soldOut
                              ? "已兑完"
                              : item.stock == null || item.stock < 0
                                ? "不限份数"
                                : `剩余 ${item.stock} 份`}
                          </p>
                          <Button
                            variant={soldOut ? "secondary" : "outline"}
                            disabled={locked || soldOut || insufficient}
                            onClick={() => prepare(item)}
                          >
                            {busy
                              ? "正在兑换…"
                              : soldOut
                                ? "已兑完"
                                : insufficient
                                  ? "积分不足"
                                  : "兑换"}
                          </Button>
                        </article>
                      )
                    })}
                  </div>
                ) : (
                  <EmptySurface
                    page="points"
                    title={kind ? "当前分类暂无权益" : "暂无积分好礼"}
                    description={
                      kind
                        ? "试试其他分类，继续挑选心仪权益。"
                        : "礼物上架后会展示在这里，请稍后再来。"
                    }
                    action={
                      kind ? (
                        <Button variant="outline" onClick={() => setKind("")}>
                          查看全部权益
                        </Button>
                      ) : undefined
                    }
                  />
                )}
              </section>
            )}
            {tab === "claims" && (
              <section className="boss-points-claims" aria-label="权益记录">
                {rows(data.claims.items).map((item) => {
                  const notices = [
                    ...new Set(
                      [
                        item.tag_revision_required
                          ? "请按客服意见修改后提交"
                          : "",
                        item.kind_snapshot === "TAG" &&
                        !item.tag_expired &&
                        item.tag_editable
                          ? item.tag_revision_submitted
                            ? "已提交修改，等待复核"
                            : "标签暂停展示，请修改后提交"
                          : "",
                        item.kind_snapshot === "TAG" &&
                        !item.tag_expired &&
                        !item.tag_editable &&
                        item.status === "COMPLETED" &&
                        item.tag_reviewed === false
                          ? "标签待审核"
                          : "",
                        item.tag_expired ? "标签已到期" : "",
                      ].filter(Boolean)
                    ),
                  ]
                  return (
                    <Link
                      key={item.id}
                      to={"/reward-claim?id=" + item.id}
                      className="boss-points-claim"
                    >
                      <div className="boss-points-claim-heading">
                        <div>
                          <h2>{item.name_snapshot}</h2>
                          <p>
                            {sourceLabel(item.source)} · {date(item.created_at)}
                          </p>
                        </div>
                        <Badge variant="secondary">
                          {item.status_label || label(item.status)}
                        </Badge>
                      </div>
                      {notices.length > 0 && (
                        <div className="boss-points-claim-notice">
                          {notices.map((notice) => (
                            <p key={notice}>{notice}</p>
                          ))}
                        </div>
                      )}
                      <span className="boss-points-claim-link">
                        查看交付详情
                        <ChevronRight size={14} />
                      </span>
                    </Link>
                  )
                })}
                {!rows(data.claims.items).length && (
                  <EmptySurface
                    page="points"
                    title="暂无权益记录"
                    description="兑换后的权益与交付进度会在这里显示。"
                  />
                )}
                <div className="boss-points-pagination">
                  <Button
                    variant="ghost"
                    disabled={!claimBefore || load.loading}
                    onClick={() => setClaimBefore("")}
                  >
                    <PointsHistory size={16} />
                    返回最新
                  </Button>
                  {data.claims.next ? (
                    <Button
                      variant="ghost"
                      disabled={load.loading}
                      onClick={() => setClaimBefore(String(data.claims.next))}
                    >
                      查看更多
                      <ChevronRight size={14} />
                    </Button>
                  ) : (
                    rows(data.claims.items).length > 0 && (
                      <span>
                        {claimBefore ? "没有更早的记录" : "已显示全部权益记录"}
                      </span>
                    )
                  )}
                </div>
              </section>
            )}
            {tab === "ledger" && (
              <section className="boss-points-ledger" aria-label="积分明细">
                {rows(data.points.list).map((item) => {
                  const amount = item.amount ?? item.points
                  return (
                    <div className="boss-points-ledger-row" key={item.id}>
                      <div>
                        <h2>
                          {item.reason ||
                            item.remark ||
                            item.kind_label ||
                            item.kind}
                        </h2>
                        <p>{date(item.created_at)}</p>
                      </div>
                      <strong className={amount > 0 ? "positive" : undefined}>
                        {amount > 0 ? "+" : ""}
                        {amount}
                      </strong>
                    </div>
                  )
                })}
                {!rows(data.points.list).length && (
                  <EmptySurface
                    page="points"
                    title="暂无积分变动记录"
                    description="积分获得与使用记录会在这里显示。"
                  />
                )}
                <div className="boss-points-pagination">
                  <Button
                    variant="ghost"
                    disabled={!ledgerBefore || load.loading}
                    onClick={() => setLedgerBefore("")}
                  >
                    <PointsHistory size={16} />
                    返回最新
                  </Button>
                  {data.points.next ? (
                    <Button
                      variant="ghost"
                      disabled={load.loading}
                      onClick={() => setLedgerBefore(String(data.points.next))}
                    >
                      加载更多
                      <ChevronRight size={14} />
                    </Button>
                  ) : (
                    rows(data.points.list).length > 0 && (
                      <span>没有更早的明细</span>
                    )
                  )}
                </div>
              </section>
            )}
            <div className="boss-points-account-note">
              <PointsShield size={16} />
              <p>
                积分与兑换结果以账户记录为准；网络异常时请核对原申请，避免重复兑换。
              </p>
            </div>
          </div>
        )}
      </State>
      {dialog}
    </div>
  )
}

const claimLabels: Row = {
  recipient: "收件人",
  phone: "联系电话",
  address: "完整收货地址",
  game_uid: "游戏 UID",
  tag: "希望展示的 TAG",
  provider_name: "希望冠名的陪玩",
  note: "补充说明",
}
const stableDetails = (value: Row) =>
  JSON.stringify(
    Object.keys(value || {})
      .sort()
      .map((key) => [key, value[key]])
  )
export function ClaimPage() {
  const query = useQuery(),
    id = query.get("id"),
    load = useLoad(() => {
      if (!id) throw new Error("权益编号无效，请从权益记录重新进入")
      return api.get<any>(`/club/claims/${id}/`)
    }, [id]),
    [details, setDetails] = useState<Row>({}),
    [baseline, setBaseline] = useState<Row>({}),
    [dirty, setDirty] = useState(false),
    [conflict, setConflict] = useState(false),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  useEffect(() => {
    if (!load.data) return
    const current = load.data.details || {}
    if (!dirty) {
      setDetails(current)
      setBaseline(current)
      setConflict(false)
    } else if (stableDetails(current) === stableDetails(details)) {
      setDirty(false)
      setBaseline(current)
      setConflict(false)
    } else setConflict(stableDetails(current) !== stableDetails(baseline))
  }, [load.data])
  const claim = load.data,
    editable = claim && (claim.status === "PENDING" || claim.tag_editable),
    fields =
      claim?.kind_snapshot === "PHYSICAL"
        ? ["recipient", "phone", "address", "note"]
        : ["VIRTUAL", "SERVICE"].includes(claim?.kind_snapshot)
          ? ["game_uid", "note"]
          : claim?.kind_snapshot === "TAG"
            ? ["tag", ...(!claim.tag_editable ? ["note"] : [])]
            : claim?.kind_snapshot === "NAMING"
              ? ["provider_name", "note"]
              : ["note"]
  useExpiryReload(
    claim?.kind_snapshot === "TAG" &&
      claim.status === "COMPLETED" &&
      !claim.tag_expired
      ? [claim.expires_at]
      : [],
    load.reload
  )
  async function save() {
    try {
      const result = await api.patch<any>(`/club/claims/${id}/`, {
        details,
        expected_details: baseline,
      })
      setDetails(result.details || {})
      setBaseline(result.details || {})
      setDirty(false)
      setConflict(false)
      load.setData(result)
    } catch (error) {
      load.reload()
      throw error
    }
  }
  function copyDraft() {
    void navigator.clipboard
      .writeText(
        Object.entries(details)
          .map(([key, value]) => `${claimLabels[key] || key}：${value}`)
          .join("\n")
      )
      .then(() => toast.success("资料草稿已复制"))
      .catch(() => toast.error("复制失败，请手动保留草稿"))
  }
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title
        title={claim?.name_snapshot || "权益交付详情"}
        description={
          claim
            ? `权益 #${claim.id} · ${claim.status_label || label(claim.status)}`
            : undefined
        }
        action={
          <Button
            variant="outline"
            onClick={load.reload}
            disabled={busy || load.loading}
          >
            刷新状态
          </Button>
        }
      />
      <State {...load}>
        {claim && (
          <>
            <Panel title="交付进度" className="boss-claim-progress">
              <div className={"boss-claim-current status-" + claim.status.toLowerCase()}><strong>{claim.status_label || label(claim.status)}</strong><span>{claim.kind_snapshot === "TAG" ? "专属标签交付" : claim.kind_snapshot === "NAMING" ? "冠名权益交付" : claim.kind_snapshot === "VIRTUAL" ? "虚拟权益交付" : "权益交付"}</span></div>
              <Info
                entries={[
                  [
                    "当前进度",
                    claim.delivery_note ||
                      (
                        {
                          PENDING: "补充资料后，俱乐部将为你安排",
                          FULFILLING: "俱乐部正在安排交付",
                          COMPLETED: "已完成交付",
                          CANCELLED: "本次权益已取消",
                        } as Row
                      )[claim.status],
                  ],
                  [
                    "权益期限",
                    [
                      claim.duration_months
                        ? `${claim.duration_months} 个月`
                        : "",
                      claim.duration_days ? `${claim.duration_days} 天` : "",
                    ]
                      .filter(Boolean)
                      .join(" + ") || "无固定期限",
                  ],
                  [
                    "有效至",
                    claim.expires_at ? date(claim.expires_at) : undefined,
                  ],
                  [
                    "积分退回",
                    claim.points_returned
                      ? `已退回 ${claim.points_spent} 积分`
                      : undefined,
                  ],
                  [
                    "标签复核",
                    claim.tag_revision_submitted
                      ? "已提交修改，等待复核"
                      : claim.tag_editable
                        ? "标签暂停展示，可修改后提交"
                        : claim.tag_reviewed === false
                          ? "等待复核"
                          : undefined,
                  ],
                  ["客服意见", claim.tag_suspension_reason],
                  [
                    "标签状态",
                    claim.tag_expired ? "已到期，不再展示" : undefined,
                  ],
                ]}
              />
            </Panel>
            <Panel className="boss-claim-delivery"
              title={
                editable
                  ? claim.tag_editable
                    ? "修改 TAG"
                    : "交付资料"
                  : "已提交资料"
              }
            >
              {conflict && (
                <div className="boss-feedback error" role="alert">
                  <p>
                    资料已在其他位置更新。请先复制草稿，再选择使用最新资料。
                  </p>
                </div>
              )}
              {dirty && !editable && (
                <div className="boss-feedback error">
                  权益状态已变化，草稿尚未保存。请先复制内容，再联系客服。
                </div>
              )}
              <form
                className="boss-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  void run(
                    save,
                    claim.tag_editable ? "资料已提交复核" : "交付资料已保存"
                  ).catch(() => {})
                }}
              >
                {editable || dirty ? (
                  fields.map((key) => (
                    <Field
                      key={key}
                      label={claimLabels[key]}
                      value={details[key] || ""}
                      onChange={(value) => {
                        setDirty(true)
                        setDetails((current) => ({ ...current, [key]: value }))
                      }}
                      multiline={["address", "note"].includes(key)}
                      disabled={busy || !editable}
                      maxLength={
                        key === "tag"
                          ? 30
                          : ["address", "note"].includes(key)
                            ? 255
                            : key === "game_uid"
                              ? 80
                              : 50
                      }
                    />
                  ))
                ) : (
                  <Info
                    entries={Object.entries(details)
                      .filter(([, value]) => value)
                      .map(([key, value]) => [claimLabels[key] || key, value])}
                  />
                )}
                <div className="boss-actions">
                  {editable && !conflict && (
                    <Button
                      disabled={busy || !dirty || load.loading || !!load.error}
                    >
                      {busy ? "正在保存…" : claim.tag_editable ? "提交客服复核" : "保存交付资料"}
                    </Button>
                  )}
                  {(dirty || conflict) && (
                    <Button type="button" variant="outline" onClick={copyDraft}>
                      复制草稿
                    </Button>
                  )}
                  {conflict && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() =>
                        confirm({
                          title: "使用最新资料",
                          description:
                            "未保存的草稿将被替换，可先复制草稿保留内容。",
                          confirm: "使用最新资料",
                          variant: "destructive",
                          action: async () => {
                            setDirty(false)
                            setDetails(claim.details || {})
                            setBaseline(claim.details || {})
                            setConflict(false)
                            load.reload()
                          },
                        })
                      }
                    >
                      使用最新资料
                    </Button>
                  )}
                </div>
              </form>
            </Panel>
            <div className="boss-actions">
              <Button asChild variant="outline">
                <Link to="/membership">管理展示标签</Link>
              </Button>
              <Button asChild variant="outline">
                <Link to="/support">联系俱乐部客服</Link>
              </Button>
            </div>
          </>
        )}
      </State>
      {dialog}
    </>
  )
}

function LotteryWheel({
  prizes,
  rotation,
}: {
  prizes: Row[]
  rotation: number
}) {
  if (!prizes.length) return null
  const point = (degrees: number) => {
    const angle = (degrees * Math.PI) / 180
    return `${120 + 114 * Math.cos(angle)},${120 + 114 * Math.sin(angle)}`
  }
  return (
    <div className="boss-lottery-wheel" aria-hidden="true">
      <span className="boss-lottery-pointer" />
      <svg viewBox="0 0 240 240" focusable="false">
        <g
          className="boss-lottery-rotor"
          style={{ transform: `rotate(${rotation}deg)` }}
        >
          {prizes.map((prize, index) => {
            const step = 360 / prizes.length,
              start = -90 + index * step - step / 2,
              end = start + step,
              className = `boss-lottery-sector${index % 2 ? " alternate" : ""}${prize.rarity === "SSR" ? " rare" : ""}`
            return (
              <g key={prize.id}>
                {prizes.length === 1 ? (
                  <circle cx="120" cy="120" r="114" className={className} />
                ) : (
                  <path
                    d={`M120,120 L${point(start)} A114,114 0 ${step > 180 ? 1 : 0},1 ${point(end)} Z`}
                    className={className}
                  />
                )}
                <text
                  x="120"
                  y="45"
                  textAnchor="middle"
                  transform={`rotate(${index * step},120,120)`}
                >
                  {String(prize.name || "").slice(0, 7)}
                </text>
              </g>
            )
          })}
        </g>
        <circle cx="120" cy="120" r="30" className="boss-lottery-center" />
        <text x="120" y="124" textAnchor="middle" className="boss-lottery-mark">
          抽奖
        </text>
      </svg>
    </div>
  )
}

const lotteryMotionKey = "xa:boss:web:lottery-simple-motion"

export function LotteryPage() {
  const load = useLoad(async () => {
      const [campaign, points] = await Promise.all([
        api.get<any>("/club/lottery/"),
        api.get<any>("/club/points/"),
      ])
      return { campaign, points }
    }),
    [intent, setIntent] = useState<Intent | null>(() => readIntent("lottery")),
    [result, setResult] = useState<Row | null>(null),
    [phase, setPhase] = useState<
      "idle" | "request" | "summon" | "reveal" | "done"
    >("idle"),
    [revealed, setRevealed] = useState(0),
    [rotation, setRotation] = useState(0),
    [wheelPrizes, setWheelPrizes] = useState<Row[]>([]),
    [drawError, setDrawError] = useState(""),
    [simpleMotion, setSimpleMotion] = useState(() => {
      try {
        return localStorage.getItem(lotteryMotionKey) === "true"
      } catch {
        return false
      }
    }),
    [systemMotion, setSystemMotion] = useState(
      () =>
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ),
    active = useRef(true),
    drawLock = useRef(false),
    responseRef = useRef<Row | null>(null),
    reduced = useRef(simpleMotion || systemMotion),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm()
  const campaign = load.data?.campaign,
    reduceMotion = simpleMotion || systemMotion,
    presenting = phase === "summon" || phase === "reveal",
    locked = busy || phase === "request" || presenting
  reduced.current = reduceMotion
  const finishReveal = useCallback(() => {
    const received = responseRef.current
    if (!received || !active.current) return
    setRevealed(received.draws.length)
    setPhase("done")
    drawLock.current = false
  }, [])
  useEffect(() => {
    active.current = true
    const media = window.matchMedia("(prefers-reduced-motion: reduce)"),
      change = () => setSystemMotion(media.matches),
      visibility = () => {
        if (document.hidden && responseRef.current) finishReveal()
      }
    media.addEventListener("change", change)
    document.addEventListener("visibilitychange", visibility)
    return () => {
      active.current = false
      media.removeEventListener("change", change)
      document.removeEventListener("visibilitychange", visibility)
    }
  }, [finishReveal])
  useEffect(() => {
    if (!result || !presenting) return
    if (reduceMotion || document.hidden) {
      finishReveal()
      return
    }
    const timer = window.setTimeout(
      () => {
        if (phase === "summon") {
          setPhase("reveal")
          setRevealed(1)
        } else if (revealed < result.draws.length)
          setRevealed((value) => value + 1)
        else finishReveal()
      },
      phase === "summon"
        ? 2100
        : revealed >= result.draws.length
          ? 300
          : result.draws[revealed - 1]?.rarity === "SSR"
            ? 460
            : 130
    )
    return () => window.clearTimeout(timer)
  }, [phase, revealed, result, presenting, reduceMotion, finishReveal])
  async function draw(original: Intent) {
    if (drawLock.current) throw new Error("请先完成本次奖品揭晓")
    drawLock.current = true
    responseRef.current = null
    setResult(null)
    setRevealed(0)
    setDrawError("")
    setPhase("request")
    let accepted = false
    try {
      const response = await submitIntent("lottery", original)
      accepted = true
      if (!active.current) return
      const claims = new Set<number>()
      if (
        response.count !== original.payload.count ||
        !Array.isArray(response.draws) ||
        response.draws.length !== original.payload.count ||
        !Number.isFinite(response.points_balance) ||
        response.points_balance < 0 ||
        response.draws.some((item: Row) => {
          if (
            !Number.isInteger(item.id) ||
            item.id < 1 ||
            !Number.isInteger(item.prize_id) ||
            item.prize_id < 1 ||
            !Number.isInteger(item.claim?.id) ||
            item.claim.id < 1 ||
            !item.claim.name_snapshot ||
            !["SSR", "SR", "R"].includes(item.rarity) ||
            claims.has(item.claim.id)
          )
            return true
          claims.add(item.claim.id)
          return false
        })
      )
        throw new Error(
          "本次抽奖已受理，展示结果暂不可用，请在权益记录核对奖品"
        )
      responseRef.current = response
      setResult(response)
      const prizes =
        campaign?.id === original.payload.campaign_id
          ? rows(campaign.prizes)
          : []
      setWheelPrizes(prizes)
      const focus =
          response.draws.find((item: Row) => item.rarity === "SSR") ||
          response.draws.find((item: Row) => item.rarity === "SR") ||
          response.draws[0],
        index = prizes.findIndex((item) => item.id === focus.prize_id)
      if (index >= 0) {
        const target = (360 - (index * 360) / prizes.length) % 360
        setRotation(
          (value) => value + 1440 + ((target - (value % 360) + 360) % 360)
        )
      }
      if (reduced.current || document.hidden) finishReveal()
      else if (index < 0) {
        setPhase("reveal")
        setRevealed(1)
      } else setPhase("summon")
    } catch (error: any) {
      drawLock.current = false
      if (active.current) {
        setPhase("idle")
        setDrawError(
          accepted
            ? "本次抽奖已受理，展示结果暂不可用，请到权益记录核对奖品。"
            : error.message || "抽奖结果暂未确认，请保留原请求"
        )
      }
      throw error
    } finally {
      if (active.current) {
        setIntent(readIntent("lottery"))
        load.reload()
      }
    }
  }
  function prepare(count: number) {
    if (!campaign || drawLock.current) return
    confirm({
      title: count === 10 ? "确认十连抽" : "确认抽奖",
      description: `使用 ${count === 10 ? (campaign.ten_draw_cost ?? campaign.point_cost * 10) : campaign.point_cost} 积分进行 ${count} 次抽奖，奖品与规则以当前活动为准。`,
      confirm: "确认抽奖",
      action: async () => {
        if (drawLock.current) throw new Error("请先完成本次奖品揭晓")
        if (readIntent("lottery")) throw new Error("请先核对上次抽奖")
        const original = {
          id: requestId("lottery"),
          path: "/club/lottery/",
          display: {
            name: `${campaign.name} · ${count} 次抽奖`,
            point_cost:
              count === 10
                ? (campaign.ten_draw_cost ?? campaign.point_cost * 10)
                : campaign.point_cost,
          },
          payload: {
            campaign_id: campaign.id,
            version: campaign.version,
            count,
          },
        }
        storeIntent("lottery", original)
        setIntent(original)
        await draw(original)
      },
    })
  }
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title
        title="幸运转盘"
        description="查看奖池与积分规则，再决定是否参与"
        action={
          <Button asChild variant="outline">
            <Link to="/points?tab=claims">查看中奖权益</Link>
          </Button>
        }
      />
      <Pending
        busy={busy}
        intent={intent}
        retry={() => {
          if (intent && !drawLock.current)
            void run(() => draw(intent), "").catch(() => {})
        }}
      />
      {drawError && (
        <div className="boss-feedback error" role="alert">
          <p>{drawError}</p>
          <Button asChild variant="outline">
            <Link to="/points?tab=claims">核对中奖权益记录</Link>
          </Button>
        </div>
      )}
      <State {...load} empty={!!load.data && !campaign} emptyTitle="当前暂无开放的转盘活动" emptyDescription="可稍后再来看看，积分和权益记录会继续保留。" emptyAction={<Button asChild variant="outline"><Link to="/points">返回积分与权益</Link></Button>}>
        {!load.loading && load.data && !campaign && (
          <Panel>
            <p>当前暂无开放的转盘活动，可稍后再来看看。</p>
            <Button asChild variant="outline">
              <Link to="/points">返回积分与权益</Link>
            </Button>
          </Panel>
        )}
        {campaign && (
          <>
            <Panel title={campaign.name || "当前活动"} className="boss-lottery-campaign"><div className="boss-lottery-account"><div><span>可用积分</span><strong>{load.data?.points.points}<small>积分</small></strong></div><div><span>今日剩余</span><strong>{campaign.daily_remaining == null ? "按活动规则" : campaign.daily_remaining}<small>次</small></strong></div></div>
              <div
                className={`boss-lottery-stage${phase === "summon" ? "is-spinning" : ""}${reduceMotion ? "is-reduced" : ""}`}
              >
                <LotteryWheel
                  prizes={
                    wheelPrizes.length ? wheelPrizes : rows(campaign.prizes)
                  }
                  rotation={rotation}
                />
                <p className="boss-muted">
                  转盘分区仅用于展示，基础概率以下方公示为准。
                </p>
                {locked && (
                  <p role="status" aria-live="polite">
                    {phase === "request"
                      ? "正在确认本次抽奖结果，请勿重复操作…"
                      : "结果已确认，正在揭晓奖品…"}
                  </p>
                )}
              </div>
              <Info
                entries={[
                  ["当前积分", load.data?.points.points],
                  ["单次消耗", campaign.point_cost + " 积分"],
                  [
                    "今日剩余",
                    campaign.daily_remaining == null
                      ? "按活动规则执行"
                      : campaign.daily_remaining + " 次",
                  ],
                  [
                    "保底进度",
                    campaign.pity?.enabled
                      ? `${campaign.pity.count} / ${campaign.pity.limit}`
                      : undefined,
                  ],
                  [
                    "保底奖品",
                    campaign.pity?.enabled
                      ? campaign.pity.target_name
                      : undefined,
                  ],
                  [
                    "距离保底",
                    campaign.pity?.enabled
                      ? `最多再 ${campaign.pity.remaining} 抽`
                      : undefined,
                  ],
                ]}
              />
              {campaign.ten_guarantee?.enabled && (
                <p className="boss-muted" style={{ marginTop: 16 }}>
                  每次十连至少获得 {campaign.ten_guarantee.min_non_points}{" "}
                  份非积分奖。单抽与十连共享活动保底进度。
                </p>
              )}
              <p className="boss-muted" style={{ marginTop: 16 }}>
                {campaign.description || campaign.rules}
              </p>
              <div className="boss-actions boss-lottery-draw-actions" style={{ marginTop: 20 }}>
                <Button
                  disabled={
                    locked ||
                    !!intent ||
                    !campaign.enabled ||
                    load.loading ||
                    !!load.error ||
                    load.data!.points.points < campaign.point_cost ||
                    campaign.daily_remaining === 0
                  }
                  onClick={() => prepare(1)}
                >
                  抽一次 · {campaign.point_cost} 积分
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    locked ||
                    !!intent ||
                    !campaign.enabled ||
                    !campaign.ten_guarantee?.available ||
                    load.loading ||
                    !!load.error ||
                    load.data!.points.points <
                      (campaign.ten_draw_cost ?? campaign.point_cost * 10) ||
                    (Number.isInteger(campaign.daily_remaining) &&
                      campaign.daily_remaining < 10)
                  }
                  onClick={() => prepare(10)}
                >
                  十连抽 · {campaign.ten_draw_cost ?? campaign.point_cost * 10}{" "}
                  积分
                </Button>
              </div>
              <div className="boss-actions" style={{ marginTop: 16 }}>
                <label className="boss-checkbox">
                  <input
                    name="reduce_motion"
                    autoComplete="off"
                    type="checkbox"
                    checked={reduceMotion}
                    disabled={systemMotion}
                    onChange={(event) => {
                      const value = event.target.checked
                      setSimpleMotion(value)
                      reduced.current = value || systemMotion
                      try {
                        localStorage.setItem(lotteryMotionKey, String(value))
                      } catch {
                        toast.error("本次设置已生效，浏览器未能保存偏好")
                      }
                      if (value) finishReveal()
                    }}
                  />
                  减少动态效果
                </label>
                {presenting && (
                  <Button variant="outline" onClick={finishReveal}>
                    跳过揭晓，查看全部奖品
                  </Button>
                )}
              </div>
              {systemMotion && (
                <p className="boss-muted">已跟随系统的减少动态效果设置。</p>
              )}
              {load.data!.points.points < campaign.point_cost && <div className="boss-feedback" role="status">积分不足，继续累积后再来。</div>}
              {campaign.daily_remaining === 0 && <div className="boss-feedback" role="status">今日抽奖次数已用完，请按活动规则下次再来。</div>}
              {!campaign.enabled && <p role="status">当前活动尚未开放</p>}
              {!campaign.ten_guarantee?.available && (
                <p className="boss-muted">
                  {campaign.ten_guarantee?.unavailable_reason ||
                    campaign.ten_guarantee?.reason}
                </p>
              )}
            </Panel>
            <Panel title="本期奖池 · 基础概率" className="boss-lottery-odds">{rows(campaign.prizes).map(item => <div key={item.id} className="boss-lottery-odds-row"><Badge variant="secondary">{item.rarity}</Badge><div><strong>{item.name}</strong>{item.description && <p>{item.description}</p>}</div><span>{Number(item.probability) / 100}%</span></div>)}</Panel>
          </>
        )}
      </State>
      {result && (
        <Panel title="本次抽奖结果">
          <Info
            entries={[
              ["抽奖次数", result.count],
              ["本次消耗", result.total_point_cost + " 积分"],
              ["剩余积分", result.points_balance],
            ]}
          />
          <div
            className={
              reduceMotion
                ? "boss-lottery-results is-reduced"
                : "boss-lottery-results"
            }
          >
            <p role="status" aria-live="polite" aria-atomic="true">
              已揭晓 {revealed} / {result.draws.length} 份奖品
            </p>
            <Progress
              aria-label="奖品揭晓进度"
              value={(revealed / result.draws.length) * 100}
            />
            <div className="boss-lottery-result-grid">
              {rows(result.draws).map((item, index) => (
                <article
                  className={`boss-lottery-result-card${index < revealed ? "is-revealed" : "is-concealed"}`}
                  key={item.id}
                  aria-label={
                    index < revealed
                      ? `第 ${index + 1} 份奖品`
                      : `第 ${index + 1} 份奖品等待揭晓`
                  }
                >
                  <span className="boss-muted">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  {index < revealed ? (
                    <>
                      <Badge
                        variant={
                          item.rarity === "SSR" ? "default" : "secondary"
                        }
                      >
                        {item.rarity}
                      </Badge>
                      <h3>{item.claim.name_snapshot}</h3>
                      <p className="boss-muted">
                        {item.claim.status_label || label(item.claim.status)}
                        {item.is_pity ? " · 保底奖励" : ""}
                        {item.is_ten_guarantee ? " · 十连保障" : ""}
                      </p>
                      <Button asChild variant="outline">
                        <Link to={"/reward-claim?id=" + item.claim.id}>
                          {item.claim.status === "COMPLETED"
                            ? "查看奖励"
                            : "领取奖励"}
                        </Link>
                      </Button>
                    </>
                  ) : (
                    <p>等待揭晓</p>
                  )}
                </article>
              ))}
            </div>
            <p className="boss-muted">
              本次全部结果已由服务器确认并保存；积分奖励以实际到账为准，其他奖励可在权益记录查看。
            </p>
            {presenting && (
              <Button variant="outline" onClick={finishReveal}>
                跳过揭晓，查看全部奖品
              </Button>
            )}
          </div>
        </Panel>
      )}
      {dialog}
    </>
  )
}
