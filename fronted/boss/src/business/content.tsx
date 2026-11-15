import { readChatPending, saveChatPending, clearChatPending } from "@xa/shared"
import {
  bossMessageDestination,
  deliverChatMessage,
  lookupChatMessage,
  mayMarkMessagesRead,
} from "@xa/shared"
import { renewPrivateMedia } from "@xa/shared"
import { chatDraftKey, readChatDraft, saveChatDraft } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { useSession } from "@xa/shared"
import { toast } from "sonner"
import { Bell, BookOpenText, CalendarDays, ChevronRight, Clock3, Crown, FileText, FileCheck2, ShieldCheck, Headphones, Megaphone, MessageSquareText, Pin, Sparkles, Trophy, WalletCards, AudioLines, ImagePlus, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  api,
  Choice,
  date,
  Field,
  Explanation,
  Image,
  Info,
  Panel,
  queryUrl,
  requestId,
  RichContent,
  rows,
  State,
  Title,
  BackLink,
  useAction,
  useConfirm,
  useGuestData,
  useLoad,
  useQuery,
  type Row,
} from "./ui"

const destination = bossMessageDestination
const noticeLabels: Row = { ORDER: "订单", SYSTEM: "系统", SUPPORT: "客服", PROMOTION: "活动" }
const contentIcon = (kind: string) => kind === "HOT" ? Trophy : kind === "RIGHTS" ? Crown : kind === "POINT_RULES" ? Trophy : kind === "ORDER_RULES" ? FileCheck2 : kind === "PROVIDER_RULES" ? Headphones : kind === "RULES" || kind === "AFTERSALE_RULES" ? ShieldCheck : BookOpenText
const contentKindLabels: Row = { ORDER_RULES: "下单规范", PROVIDER_RULES: "陪玩规范", AFTERSALE_RULES: "售后规范" }
function ContentKindIcon({ kind }: { kind: string }) { const Icon = contentIcon(kind); return <Icon size={18} /> }
function ReadingHeader({ title, category, time, kind }: { title: string; category: string; time?: string; kind?: string }) {
  return <header className="boss-reading-header" data-kind={kind}><div><FileText size={14} /><span>{category}</span></div><h1>{title}</h1>{time && <p><CalendarDays size={14} />发布于 {time}</p>}</header>
}
function MessageShortcut({ to, title, note, icon: Icon }: { to: string; title: string; note: string; icon: typeof Headphones }) {
  return <Link className="boss-message-shortcut" to={to}><Icon size={20} /><span><strong>{title}</strong><small>{note}</small></span><ChevronRight size={16} /></Link>
}

export function MessagesPage() {
  const query = useQuery()
  const [section, setSection] = useState(query.get("tab") === "feed" ? "feed" : "private"),
    [filter, setFilter] = useState(""),
    [before, setBefore] = useState(""),
    [feedKind, setFeedKind] = useState(""),
    [feedBefore, setFeedBefore] = useState(""),
    { busy, run } = useAction()
  const load = useLoad(
      () =>
        api.get<any>(
          queryUrl("/messages/", { page_size: 20, before, type: filter })
        ),
      [filter, before]
    ),
    feed = useLoad(
      () =>
        api.get<any>(
          queryUrl("/club/feed/", {
            page: 1,
            kind: feedKind,
            before: feedBefore,
          })
        ),
      [feedKind, feedBefore]
    )
  return <>
    <Title title="消息中心" description="俱乐部此刻与你的服务进度。" support action={load.data?.unread > 0 ? <Badge variant="outline" className="text-primary">{load.data.unread} 条未读</Badge> : undefined} />
    <div className="boss-messages-layout">
      <section className="boss-message-content" aria-label="消息内容">
        <div className="boss-message-filters"><div className="boss-tabs" role="group" aria-label="消息内容视图"><Button variant={section === "private" ? "secondary" : "ghost"} aria-pressed={section === "private"} onClick={() => setSection("private")}>我的通知{load.data?.unread ? ` · ${load.data.unread}` : ""}</Button><Button variant={section === "feed" ? "secondary" : "ghost"} aria-pressed={section === "feed"} onClick={() => setSection("feed")}>俱乐部动态</Button></div>
          {section === "private" ? <><div className="boss-section-heading"><p>订单、账户与服务提醒</p><Button variant="ghost" disabled={busy || !load.data?.unread} onClick={() => void run(async () => { await api.post("/messages/read-all/"); load.reload() }, "全部消息已标记为已读").catch(() => {})}>全部已读</Button></div><div className="boss-message-type-filters" role="group" aria-label="通知分类">{[{ value: "", label: "全部" }, ...Object.entries(noticeLabels).map(([value, label]) => ({ value, label }))].map(item => <Button key={item.value} variant={filter === item.value ? "secondary" : "ghost"} aria-pressed={filter === item.value} onClick={() => { setFilter(item.value); setBefore("") }}>{item.label}</Button>)}</div></> : <div className="boss-message-feed-filter"><Choice label="动态类型" value={feedKind} onChange={value => { setFeedKind(value); setFeedBefore("") }} options={[{ value: "", label: "全部动态" }, { value: "GIFT", label: "礼物" }, { value: "LOTTERY", label: "转盘" }, { value: "RECHARGE", label: "充值" }, { value: "REVIEW", label: "好评" }, { value: "NAMING", label: "冠名" }, { value: "UPGRADE", label: "VIP 升级" }, { value: "BIRTHDAY", label: "生日" }]} /></div>}
        </div>
        {section === "private" ? <State {...load} empty={!rows(load.data?.messages).length} emptyAction={before ? <Button variant="outline" onClick={() => setBefore("")}>返回最新通知</Button> : undefined} emptyTitle="还没有这类通知">
          {rows(load.data?.messages).map(item => <Link className={"boss-notice-row" + (!item.is_read ? " unread" : "")} key={item.id} to={"/message-detail?id=" + item.id}><span className="boss-notice-meta"><Badge variant="secondary">{noticeLabels[item.type] || item.type || "通知"}</Badge>{!item.is_read && <span className="boss-notice-dot" aria-label="未读" />}<time>{date(item.created_at)}</time><ChevronRight size={16} /></span><strong>{item.title}</strong><p>{item.preview}</p></Link>)}
          <div className="boss-message-pagination"><Button variant="outline" disabled={!before || load.loading} onClick={() => setBefore("")}>返回最新</Button>{load.data?.next_before && <Button variant="outline" disabled={load.loading} onClick={() => setBefore(String(load.data.next_before))}>查看更多消息</Button>}</div>
        </State> : <State {...feed} empty={!rows(feed.data?.items).length} emptyAction={feedBefore ? <Button variant="outline" onClick={() => setFeedBefore("")}>返回最新动态</Button> : undefined} emptyTitle="新的精彩即将发生" emptyDescription="礼物、冠名、好评与会员动态会在这里展示。">{rows(feed.data?.items).map(item => <article className="boss-feed-row" key={item.id}><span className="boss-feed-icon"><Sparkles size={16} /></span><div><header><h2>{item.kind_label || item.kind}</h2><time>{date(item.created_at)}</time></header><p>{item.text || item.content || item.message}</p></div></article>)}<div className="boss-message-pagination"><Button variant="outline" disabled={!feedBefore || feed.loading} onClick={() => setFeedBefore("")}>返回最新</Button>{feed.data?.next && <Button variant="outline" disabled={feed.loading} onClick={() => setFeedBefore(String(feed.data.next))}>查看更多动态</Button>}</div></State>}
      </section>
      <aside className="boss-message-shortcuts" aria-label="消息快捷入口"><div><MessageShortcut to="/support" title="联系客服" note="咨询与帮助" icon={Headphones} /><MessageShortcut to="/chat" title="平台会话" note="查看与回复" icon={MessageSquareText} /></div><MessageShortcut to="/announcements" title="平台公告" note="官方发布 · 服务安排与重要通知" icon={Bell} /><Explanation title="消息与隐私说明" returnLabel="返回消息中心">通知按订单、系统、客服与活动分类；俱乐部动态展示当前门店的公开动态。通知内容和平台会话请以当前登录账号显示的信息为准，不要将验证码、支付密码等敏感资料发送给他人。</Explanation></aside>
    </div>
  </>
}

export function MessagePage() {
  const query = useQuery(),
    id = query.get("id"),
    load = useLoad(() => {
      if (!id) throw new Error("消息链接无效")
      return api.get<any>(`/messages/${id}/`)
    }, [id]),
    item = load.data,
    target = item ? destination(item) : ""
  return <><BackLink to="/messages">消息中心</BackLink><State {...load} headingLevel={item ? 2 : 1}>{item && <div className="boss-message-reading"><article className="boss-notice-article"><header><div><Badge variant="secondary">{noticeLabels[item.type] || item.type || "通知"}</Badge>{item.is_read && <span>已读</span>}</div><h1>{item.title}</h1><p><Clock3 size={14} />发送时间 · {date(item.created_at)}</p></header><section><h2><FileText size={16} />通知内容</h2><p>{item.detail || item.content || item.preview || "暂无通知正文"}</p></section></article>{target && <section className="boss-related-content"><div><MessageSquareText size={20} /><div><h2>关联内容</h2><p>查看完整信息与最新处理状态</p></div></div>{item.related_order_id && <p>关联订单 · {item.related_order_id}</p>}<Button asChild><Link to={target}>{item.related_order_id ? "查看关联订单" : "查看相关内容"}<ChevronRight size={16} /></Link></Button></section>}<Button asChild variant="ghost"><Link to="/messages">返回消息中心</Link></Button></div>}</State></>
}

export function SupportPage() {
  const query = useQuery(),
    session = useSession("boss"),
    orderId = query.get("order_id"),
    cardId = query.get("card_id"),
    scene = query.get("scene"),
    load = useLoad(
      () =>
        api.get<any>(
          session.token
            ? queryUrl("/support/contact-card/", {
                order_id: orderId,
                card_id: cardId,
              })
            : "/support/public-contact/"
        ),
      [session.revision, orderId, cardId]
    ),
    card = load.data
  const copy = (value: string) =>
    void navigator.clipboard
      .writeText(value)
      .then(() => toast.success("已复制"))
      .catch(() => toast.error("复制失败，请手动复制内容"))
  return <><BackLink to="/mine">个人中心</BackLink><Title title={scene === "recharge" ? "充值咨询" : "客服与帮助"} description={scene === "recharge" ? "联系客服核对付款，到账后可在钱包查看流水。" : "账号绑定、服务需求与订单问题，都可以联系我们。"} />
    <State {...load}>{card ? <div className="boss-support-sections">
      {(card.customer_reference || card.order_no) && <section className="boss-support-context"><div className="boss-section-heading"><h2>本次咨询</h2><Badge variant="outline">{card.order_no ? "已关联订单" : scene === "recharge" ? "充值场景" : "老板账号"}</Badge></div><Info entries={[["关联订单", card.order_no], ["咨询编号", card.customer_reference]]} /><Button variant="outline" onClick={() => copy([card.customer_reference ? "咨询编号 " + card.customer_reference : "", card.order_no ? "订单号 " + card.order_no : ""].filter(Boolean).join("，"))}>复制咨询信息</Button></section>}
      {scene === "recharge" && <section className="boss-support-recharge"><WalletCards size={20} /><div><h2>先核对，再付款</h2><p>请勿重复付款。客服核对后，实际到账以钱包余额和充值流水为准。</p></div></section>}
      <section><div className="boss-section-heading"><div><h2>选择咨询方式</h2><p>企业微信与平台会话均可咨询</p></div></div><div className="boss-support-channels"><article><div className="boss-support-identity"><Image src={card.avatar_url} alt={card.name || "客服头像"} className="boss-avatar" /><div><h3>{card.name || card.nickname || "平台客服"}</h3><p>{card.company || card.description || card.remark}</p></div></div>{card.tips && <p className="boss-support-tips">{card.tips}</p>}{card.wecom_service_url && /^https:\/\//.test(card.wecom_service_url) ? <Button asChild><a href={card.wecom_service_url} target="_blank" rel="noopener noreferrer">打开微信客服<ChevronRight size={16} /></a></Button> : <p className="boss-support-unavailable">当前未配置企业微信客服，请使用下方平台会话。</p>}</article><article><div className="boss-support-identity"><span className="boss-support-icon"><MessageSquareText size={20} /></span><div><h3>平台客服会话</h3><p>在网页内发送文字与图片</p></div></div><p className="boss-support-tips">{orderId ? "会话会保留当前订单入口，便于继续核对问题。" : "保留历史消息，方便继续核对服务与账号问题。"}</p><Button asChild variant="outline"><Link to={queryUrl("/chat", { order_id: orderId })}>进入平台会话<ChevronRight size={16} /></Link></Button></article></div></section>
      {(card.wechat_id || card.qrcode_url || card.service_hours || card.working_hours) && <Panel title="其他联系方式" className="boss-support-other"><Info entries={[["客服微信号", card.wechat_id], ["服务时间", card.service_hours || card.working_hours]]} />{card.wechat_id && <Button variant="outline" onClick={() => copy(card.wechat_id)}>复制客服微信号</Button>}{card.qrcode_url && <Image src={card.qrcode_url} alt="平台客服二维码，请使用微信识别" className="boss-login-code" />}<p className="boss-muted">请在沟通时提供咨询编号与关联订单，方便核对。</p></Panel>}
    </div> : !load.loading && <Panel><p>客服资料暂未配置，请稍后重试。</p>{session.token && <Button asChild variant="outline"><Link to={queryUrl("/chat", { order_id: orderId })}>平台客服会话</Link></Button>}</Panel>}</State>
  </>
}

const contentOrigins: Row = {
  home: { path: "/home", label: "首页" },
  mine: { path: "/mine", label: "个人中心" },
  membership: { path: "/membership", label: "我的会员" },
  points: { path: "/points", label: "积分与兑换" },
}
export function ContentPage() {
  const query = useQuery(),
    id = query.get("id"),
    kind = query.get("kind") || "RULES",
    [page, setPage] = useState(1),
    load = useLoad(
      () =>
        api.get<any>(
          id
            ? `/club/content/${id}/`
            : kind === "HOT"
              ? `/club/content/?kind=HOT&archive=1&page=${page}`
              : "/club/content/"
        ),
      [id, kind, page]
    ),
    titles: Row = {
      RULES: "俱乐部规范",
      RIGHTS: "老板权益",
      POINT_RULES: "积分与兑换规则",
      HOT: "俱乐部活动",
    },
    visible = rows(load.data).filter((item) =>
      kind === "RULES"
        ? ["ORDER_RULES", "PROVIDER_RULES", "AFTERSALE_RULES"].includes(
            item.kind
          )
        : item.kind === kind
    )
  const item = id ? load.data : null,
    displayKind = item?.kind || kind,
    ContentIcon = contentIcon(displayKind),
    requestedOrigin = query.get("from") || "",
    originKey = contentOrigins[requestedOrigin]
      ? requestedOrigin
      : displayKind === "HOT"
        ? "home"
        : displayKind === "POINT_RULES"
          ? "points"
          : "mine",
    origin = contentOrigins[originKey],
    listKind = displayKind === "HOT" || displayKind === "RIGHTS" || displayKind === "POINT_RULES" ? displayKind : "RULES",
    listTarget = queryUrl("/club-content", { kind: listKind, from: originKey })
  return <><BackLink to={id ? listTarget : origin.path}>{id ? "内容目录" : origin.label}</BackLink>{!id && <Title title={titles[kind] || "俱乐部内容"} description={kind === "HOT" ? "新活动在最前面，往期精彩也在这里" : kind === "RIGHTS" ? "每一份支持，都有专属回应" : kind === "POINT_RULES" ? "积累热爱，也清楚每一次兑换" : "下单、服务与售后，都有清楚依据"} />}
    <State {...load} empty={!id && !visible.length}>{item ? <article className="boss-public-reading"><ReadingHeader kind={displayKind} title={item.title} category={titles[displayKind] || "俱乐部规范"} time={date(item.starts_at || item.created_at)} />{item.image_url && <a className="boss-content-poster" href={item.image_url} target="_blank" rel="noopener noreferrer" aria-label={"查看" + item.title + "大图"}><Image src={item.image_url} alt={item.title} /></a>}{displayKind === "HOT" && !item.image_url && <Panel><p className="boss-reading-empty">活动海报准备中</p></Panel>}<Panel title="完整说明" className="boss-reading-body">{item.content ? <RichContent html={item.content} /> : <p className="boss-reading-empty">正文准备中，请稍后刷新查看。</p>}</Panel></article> : <>
      <section className="boss-public-overview"><ContentIcon size={24} /><div><h2>{kind === "HOT" ? "每场相遇，都值得记录" : kind === "RIGHTS" ? "把偏爱，写成清楚的权益" : kind === "POINT_RULES" ? "每一分，都有来处与去向" : "每一步，都有明确说明"}</h2><p>{kind === "HOT" ? "近期活动与往期回顾" : "公开内容 · 无需登录"}</p></div></section>
      <section className={kind === "HOT" ? "boss-activities-list" : "boss-public-directory"}><div className="boss-public-directory-heading"><h2>{kind === "HOT" ? "活动目录" : "内容目录"}</h2><span>共 {visible.length} 篇</span></div>{visible.map(item => <Link className="boss-public-content-row" to={queryUrl("/club-content", { id: item.id, kind: item.kind || kind, from: originKey })} key={item.id}>{kind === "HOT" ? <Image src={item.image_url} alt="" className="boss-activity-thumb" /> : <span className="boss-public-content-icon"><ContentKindIcon kind={item.kind || kind} /></span>}<span>{kind === "RULES" && <em className="boss-public-kind">{contentKindLabels[item.kind] || "俱乐部规范"}</em>}<strong>{item.title}</strong>{item.summary && <p>{item.summary}</p>}<small><CalendarDays size={12} />{date(item.starts_at || item.created_at)}</small></span><ChevronRight size={16} /></Link>)}</section>
      {kind === "HOT" && <div className="boss-actions"><Button variant="outline" disabled={page <= 1 || load.loading} onClick={() => setPage(value => value - 1)}>上一页</Button><span>第 {page} 页</span><Button variant="outline" disabled={rows(load.data).length < 30 || load.loading} onClick={() => setPage(value => value + 1)}>下一页</Button></div>}
    </>}</State>
  </>
}

export function AnnouncementsPage() {
  const load = useLoad(() => api.get<any>("/announcements/?limit=20"))
  return <><BackLink to="/mine">个人中心</BackLink><Title title="平台公告" description="服务调整、活动与使用说明" /><State {...load} empty={!rows(load.data).length}>{!!rows(load.data).length && <><section className="boss-public-overview"><Megaphone size={24} /><div><h2>重要变化，清楚送达</h2><p>置顶公告优先展示</p></div></section><section className="boss-public-directory"><div className="boss-public-directory-heading"><h2>全部公告</h2><span>{rows(load.data).length} 条</span></div>{rows(load.data).map(item => <Link className="boss-public-content-row" to={"/announcement-detail?id=" + item.id} key={item.id}><span className="boss-public-content-icon">{item.is_pinned ? <Pin size={16} /> : <FileText size={16} />}</span><span><strong>{item.is_pinned && <Badge>置顶</Badge>}{item.title}</strong><small><CalendarDays size={12} />发布于 {date(item.created_at)}</small></span><ChevronRight size={16} /></Link>)}</section></>}</State></>
}

export function AnnouncementPage() {
  const query = useQuery(),
    id = query.get("id"),
    load = useLoad(() => {
      if (!id) throw new Error("公告链接无效")
      return api.get<any>(`/announcements/${id}/`)
    }, [id])
  return <><BackLink to="/announcements">平台公告</BackLink><State {...load} headingLevel={load.data ? 2 : 1}>{load.data && <article className="boss-public-reading"><ReadingHeader kind="ANNOUNCEMENT" title={load.data.title} category="官方公告" time={date(load.data.created_at)} /><Panel title="公告内容" className="boss-reading-body">{load.data.content ? <RichContent html={load.data.content} /> : <p className="boss-reading-empty">暂无公告正文</p>}</Panel></article>}</State></>
}

export function AuditionPage() {
  const query = useQuery(),
    session = useSession("boss"),
    token = query.get("token"),
    role = query.get("role"),
    { busy, run } = useAction(),
    [matched, setMatched] = useState(false),
    load = useLoad(() => {
      if (!token || role !== "boss")
        throw new Error("这不是有效的老板试音邀请，请向客服获取本人邀请")
      return api.request<any>(
        queryUrl("/audition/info", { token, role: "boss" }),
        { public: true }
      )
    }, [token, role]),
    info = load.data
  return (
    <>
      <BackLink to="/home">首页</BackLink>
      <Title
        title="老板试音邀请"
        description="请使用客服发给你本人的活动邀请"
      />
      <State {...load}>
        {info && (
          <div className="boss-audition-sections"><section className="boss-audition-event"><div className="boss-section-heading"><h2>{info.title || info.name}</h2><AudioLines size={28} /></div><p>{info.remark || info.description}</p><Info entries={[["开始时间", info.start_at ? date(info.start_at) : undefined], ["有效期至", info.expire_at ? date(info.expire_at) : "长期有效"]]} /></section><Panel title="账号确认" className="boss-audition-account">
            <Info entries={[["进入身份", "邀请绑定的老板账号"], ["当前账号", session.token ? session.user?.nickname || session.user?.username : "未登录"], ["邀请核对", matched ? "本人邀请已核对" : "待核对"]]} />
            <p className="boss-muted" style={{ marginBlock: 16 }}>
              活动账号需通过微信确认身份。客服可协助核对邀请与当前账号的关联。
            </p>
            <div className="boss-actions">
              {!session.token && (
                <Button asChild>
                  <Link
                    to={
                      "/login?next=" +
                      encodeURIComponent("/audition?" + query.toString())
                    }
                  >
                    微信登录后核对邀请
                  </Link>
                </Button>
              )}
              {session.token && !matched && (
                <Button
                  disabled={busy || load.loading || !!load.error}
                  onClick={() =>
                    void run(async () => {
                      const result = await api.get<any>(
                        queryUrl("/audition/enter", { token, role: "boss" })
                      )
                      if (!result.matched)
                        throw new Error(
                          "当前微信账号与邀请不一致，请联系客服核对"
                        )
                      setMatched(true)
                    }, "邀请身份已核对").catch(() => {})
                  }
                >
                  核对本人试音邀请
                </Button>
              )}
              {matched && (
                <Button asChild>
                  <Link to="/mine">进入老板服务中心</Link>
                </Button>
              )}
              <Button asChild variant="outline">
                <Link to="/support">联系客服核对活动</Link>
              </Button>
            </div>
          </Panel></div>
        )}
      </State>
    </>
  )
}

type PendingMessage = {
  id: string
  kind: "text" | "image"
  content: string
  draftKey: string
  restored?: boolean
  attempted?: boolean
  order_ref?: string
}
function mergeMessages(first: Row[], second: Row[]) {
  return [
    ...new Map([...first, ...second].map((item) => [item.id, item])).values(),
  ].sort(
    (a, b) =>
      String(a.created_at).localeCompare(String(b.created_at)) || a.id - b.id
  )
}
export function ChatPage() {
  const query = useQuery(),
    navigate = useNavigate(),
    orderId = query.get("order_id"),
    guest = useGuestData(),
    draftKey = guest ? "boss-guest" : chatDraftKey("boss", orderId || "")
  const [content, setContent] = useState(() =>
      guest ? "" : readChatDraft("boss", orderId || "")
    ),
    [items, setItems] = useState<Row[]>([]),
    [historyCursor, setHistoryCursor] = useState<number | null>(null),
    [hasMore, setHasMore] = useState(false),
    [historyBrowsing, setHistoryBrowsing] = useState(false),
    [pending, setPending] = useState<PendingMessage | null>(() =>
      guest ? null : readChatPending<PendingMessage>("boss")
    ),
    [file, setFile] = useState<File | null>(null),
    [attachmentError, setAttachmentError] = useState(""),
    fileRef = useRef<File | null>(null),
    initialized = useRef(false),
    { busy, run } = useAction(),
    { confirm, dialog } = useConfirm(),
    historyRef = useRef<HTMLDivElement>(null)
  const contentScope = useRef(draftKey)
  const load = useLoad(() => api.get<any>("/chat/session/")),
    context = useLoad(
      () =>
        orderId
          ? api.get<any>(`/orders/orders/${orderId}/`)
          : Promise.resolve(null),
      [orderId],
      false
    )
  useEffect(() => {
    setContent(guest ? "" : readChatDraft("boss", orderId || ""))
  }, [draftKey, guest])
  useEffect(() => {
    if (guest) return
    if (contentScope.current !== draftKey) {
      contentScope.current = draftKey
      return
    }
    const saved = saveChatDraft("boss", orderId || "", content)
    if (!saved.saved) setAttachmentError(saved.error!.message)
  }, [content, draftKey, guest])
  useEffect(() => {
    if (!load.data) return
    setItems((current) => mergeMessages(current, rows(load.data.messages)))
    if (!initialized.current) {
      initialized.current = true
      setHistoryCursor(load.data.next_before_id || null)
      setHasMore(!!load.data.has_more)
    }
  }, [load.data])
  useEffect(() => {
    if (
      !items.length ||
      !mayMarkMessagesRead({
        hidden: document.hidden,
        focused: document.hasFocus(),
        browsing: historyBrowsing,
      })
    )
      return
    historyRef.current?.scrollTo({
      top: historyRef.current.scrollHeight,
      behavior: "instant",
    })
    const ids = items
      .filter((item) => item.is_from_support && !item.is_read)
      .map((item) => item.id)
    if (ids.length)
      void (async () => {
        for (let offset = 0; offset < ids.length; offset += 200)
          await api.post("/chat/read/", {
            message_ids: ids.slice(offset, offset + 200),
          })
        setItems((current) =>
          current.map((item) =>
            ids.includes(item.id) ? { ...item, is_read: true } : item
          )
        )
      })().catch(() => {})
  }, [items, historyBrowsing])
  async function older() {
    if (!historyCursor) return
    const result = await api.get<any>(
      "/chat/session/?before_id=" + historyCursor
    )
    setHistoryBrowsing(true)
    setItems((current) => mergeMessages(rows(result.messages), current))
    setHistoryCursor(result.next_before_id || null)
    setHasMore(!!result.has_more)
  }
  function finish(original: PendingMessage, message: Row) {
    if (!message || message.client_message_id !== original.id)
      throw new Error("消息结果未确认，请继续核对")
    clearChatPending("boss")
    setPending(null)
    setItems((current) => mergeMessages(current, [message]))
    if (
      original.draftKey === draftKey &&
      content.trim() === original.content.trim()
    )
      setContent("")
    else if (
      original.draftKey === draftKey &&
      content.trim() &&
      original.content.endsWith(content.trim())
    )
      setContent("")
    sessionStorage.removeItem(original.draftKey)
    setFile(null)
    fileRef.current = null
    setHistoryBrowsing(false)
    load.reload()
  }
  async function retry(original: PendingMessage) {
    const first = !original.attempted && !original.restored
    original.attempted = true
    const storage = saveChatPending("boss", original)
    if (!storage.saved) throw storage.error
    try {
      const message = await deliverChatMessage(
        "boss",
        original,
        fileRef.current
      )
      finish(original, message)
    } catch (error: any) {
      const code = typeof error.code === "number" ? error.code : error.status
      if (
        first &&
        error.status !== 0 &&
        code >= 400 &&
        code < 500 &&
        ![409, 429].includes(code)
      ) {
        clearChatPending("boss")
        setPending(null)
      }
      load.reload()
      throw error
    }
  }
  async function lookup() {
    if (!pending) return
    const result = await lookupChatMessage("boss", pending)
    if (result.status === "FOUND" && result.message) {
      finish(pending, result.message)
      toast.success("消息已发送")
    } else throw new Error("暂未查到确定结果，可稍后继续核对原消息")
  }
  async function send() {
    if (pending) throw new Error("请先核对上次消息")
    if (orderId && (context.error || !context.data))
      throw new Error("关联订单尚未读取，请重试订单信息或先切换为普通咨询")
    const prefix = context.data?.order_no
        ? "订单号 " + context.data.order_no
        : "",
      text = [prefix, content.trim()].filter(Boolean).join("\n")
    if (!file && !text.trim()) return
    if (text.length > 1000) throw new Error("消息最多 1000 字，请精简后发送")
    const original: PendingMessage = {
      id: requestId("chat"),
      kind: file ? "image" : "text",
      content: text,
      draftKey,
      ...(context.data?.id ? { order_ref: String(context.data.id) } : {}),
    }
    if (file && file.size > 5 * 1024 * 1024) throw new Error("图片不能超过 5MB")
    const storage = saveChatPending("boss", original)
    if (!storage.saved) throw storage.error
    fileRef.current = file
    setPending(original)
    await retry(original)
  }
  async function renew(item: Row) {
    if (item.renewed) return
    setItems((current) =>
      current.map((row) =>
        row.id === item.id ? { ...row, renewed: true } : row
      )
    )
    try {
      const result = await renewPrivateMedia(
        "boss",
        String(item.image_url || "")
      )
      setItems((current) =>
        current.map((row) =>
          row.id === item.id ? { ...row, image_url: result } : row
        )
      )
    } catch {
      setAttachmentError("有图片暂不可读取，请稍后刷新会话重试")
    }
  }
  return (
    <>
      <header className="boss-chat-header"><BackLink to="/messages">消息中心</BackLink><span className="boss-chat-support-avatar"><Headphones size={20} /></span><div><h1>平台客服</h1><p>支持文字与图片 · 回复以实际会话为准</p></div></header>
      <section className="boss-chat-room">
        {orderId && (
          <div className="boss-feedback">
            <p>
              关联订单：
              {context.data?.order_no ||
                (guest ? "登录后查看" : context.loading ? "正在读取…" : "读取失败")}
            </p>
            {context.error && (
              <Button variant="outline" onClick={context.reload}>
                重试订单信息
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={!!pending || busy}
              onClick={() => navigate("/chat")}
            >
              切换普通咨询
            </Button>
          </div>
        )}
        {!guest && pending && (
          <div className="boss-feedback" role="alert">
            <strong>上次消息发送结果待确认</strong>
            <p>{pending.kind === "image" ? "图片消息" : pending.content}</p>
            <div className="boss-actions">
              <Button
                disabled={busy || !pending.id}
                onClick={() => void run(lookup, "").catch(() => {})}
              >
                核对发送结果
              </Button>
              <Button
                variant="outline"
                disabled={
                  busy ||
                  !pending.id ||
                  (pending.kind === "image" && !fileRef.current)
                }
                onClick={() =>
                  void run(() => retry(pending), "").catch(() => {})
                }
              >
                重试原消息
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() =>
                  confirm({
                    title: "放弃核对此消息",
                    description:
                      "消息可能已经送达。请先查看聊天记录，再决定是否发送新的消息。",
                    confirm: "放弃核对",
                    variant: "destructive",
                    action: async () => {
                      clearChatPending("boss")
                      setPending(null)
                      setFile(null)
                      fileRef.current = null
                    },
                  })
                }
              >
                放弃核对
              </Button>
            </div>
          </div>
        )}
        <State {...load} empty={!items.length && !rows(load.data?.messages).length}>
          {hasMore && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void run(older, "").catch(() => {})}
            >
              加载更早记录
            </Button>
          )}
          <div
            className="boss-chat-history"
            ref={historyRef}
            onScroll={(event) => {
              const element = event.currentTarget
              if (
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight >
                64
              )
                setHistoryBrowsing(true)
            }}
          >
            {items.map(item => <div key={item.id} className={"boss-chat-message-row" + (!item.is_from_support ? " mine" : "")}><span className="boss-chat-message-avatar">{item.is_from_support ? <Headphones size={16} /> : "我"}</span><div><p className="boss-chat-message-meta">{item.is_from_support ? item.sender_name || "平台客服" : "我"} · {date(item.created_at)}</p><div className={"boss-message" + (!item.is_from_support ? " mine" : "")}><p>{item.content}</p>{item.order_card && (item.order_card.available ? <Link className="block rounded border p-2 text-sm underline" to={`/order-detail?id=${item.order_card.id}`}>订单 {item.order_card.order_no} · {item.order_card.service_name}<br />发送时：{item.order_card.status_display} · 查看订单</Link> : <p>关联订单当前不可查看</p>)}{item.image_url && <a className="inline-flex min-h-11 items-center" href={item.image_url} target="_blank" rel="noopener noreferrer"><img src={item.image_url} alt="会话图片，点击查看原图" width={640} height={360} onError={() => void renew(item)} /></a>}</div></div></div>)}
          </div>
          {historyBrowsing && (
            <Button variant="outline" onClick={() => setHistoryBrowsing(false)}>
              回到最新消息
            </Button>
          )}
        </State>
        {attachmentError && <p role="alert">{attachmentError}</p>}
        <form
          className="boss-chat-composer"
          onSubmit={(event) => {
            event.preventDefault()
            void run(send, "").catch(() => {})
          }}
        >
          <Field
            label="消息内容"
            value={guest ? "" : content}
            onChange={setContent}
            multiline
            maxLength={1000}
            disabled={guest || busy || !!pending || load.loading || !!load.error}
            placeholder={guest ? "登录后可发送咨询消息" : "描述你的问题，客服会在会话中回复"}
          />
          <label className="boss-chat-attachment"><ImagePlus size={18} /><span>添加图片</span><small>JPG、PNG、WebP，不超过 5MB</small>
            <input
              className="sr-only"
              name="chat_image"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              disabled={guest || busy || !!pending || load.loading || !!load.error}
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
          </label>
          {!guest && file && (
            <p>
              {file.name}{" "}
              <Button
                type="button"
                variant="ghost"
                disabled={guest || busy || !!pending || load.loading || !!load.error}
                onClick={() => setFile(null)}
              >
                移除附件
              </Button>
            </p>
          )}
          <Button
            disabled={
              guest || busy || !!pending || load.loading || !!load.error || context.loading || (!content.trim() && !file)
            }
          >
            <Send size={16} />{busy ? "正在发送…" : "发送消息"}
          </Button>
        </form>
      </section>
      {dialog}
    </>
  )
}
