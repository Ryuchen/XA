import { coinText, coinUnit } from "@xa/shared"
import { useBossBrand } from "./h5-module"
import { useCurrentStore, getStoreSlug } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"
import {
  CalendarDays,
  X,
  Gamepad2,
  Check,
  Megaphone,
  ArrowRight,
  AlertCircle,
  Bell,
  FileText,
  FileLock2,
  Save,
  CheckCircle2,
  RefreshCw,
  ReceiptText,
  ArrowDownLeft,
  ArrowUpRight,
  AudioLines,
  Search,
  MessageCircle,
  Smartphone,
  LockKeyhole,
  ChevronRight,
  Gift,
  Heart,
  Headphones,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
  Ticket,
  Trophy,
  UserRound,
  Wallet,
} from "lucide-react"
import {
  clearSession,
  getSession,
  persistSession,
  updateProfile,
  useSession,
} from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  api,
  Choice,
  date,
  fail,
  Field,
  Explanation,
  Image,
  Info,
  money,
  Panel,
  queryUrl,
  RichContent,
  rows,
  State,
  SearchField,
  Title,
  BackLink,
  useAction,
  useConfirm,
  useGuestData,
  useLoad,
  useQuery,
  type Row,
} from "./ui"
import { WechatBindingButton } from "./wechat-binding"
import { OtpSetupPanel } from "@xa/shared"
import { PasswordSecurityForm } from "./password-security"

export function LoginPage() {
  const store = useCurrentStore()
  const navigate = useNavigate(),
    query = useQuery(),
    session = useSession("boss"),
    { busy, run } = useAction()
  const verifyPhone = query.get("verify_phone") === "1",
    [flow, setFlow] = useState<Row | null>(null),
    [flowError, setFlowError] = useState(""),
    [status, setStatus] = useState(""),
    [remaining, setRemaining] = useState(0),
    [agreed, setAgreed] = useState(false),
    [agreementError, setAgreementError] = useState(false),
    [mobileAuthorization] = useState(() => window.matchMedia("(pointer: coarse)").matches || /MicroMessenger/i.test(navigator.userAgent)),
    generation = useRef(0)
  useEffect(() => {
    if (session?.token && !verifyPhone) {
      const next = query.get("next") || "/home"
      navigate(
        next.startsWith("/") &&
          !next.startsWith("//") &&
          !next.startsWith("/login")
          ? next
          : "/home",
        { replace: true }
      )
    }
  }, [session?.token, verifyPhone, navigate])
  async function start() {
    const revision = getSession("boss").revision,
      owner = getSession("boss").user?.id,
      currentGeneration = ++generation.current
    setFlow(null)
    setStatus("")
    setFlowError("")
    const value = await api.post<any>(
      "/users/web-login/start/",
      verifyPhone ? { purpose: "bind_phone" } : {},
      { public: !verifyPhone }
    )
    if (!value.ticket || !value.poll_token || !value.qr_image)
      throw new Error("微信登录入口暂不可用，请稍后重试或联系客服")
    if (
      getSession("boss").revision !== revision ||
      generation.current !== currentGeneration
    )
      throw new Error("登录身份已变化，请重新获取微信登录码")
    setFlow({
      ...value,
      startedAt: Date.now(),
      revision,
      owner,
      generation: currentGeneration,
    })
    setStatus("PENDING")
    setRemaining(value.expires_in)
  }
  useEffect(() => {
    if (!flow || status !== "PENDING") return
    let active = true,
      polling = false
    const poll = async () => {
      if (polling || !active) return
      if (
        getSession("boss").revision !== flow.revision ||
        generation.current !== flow.generation
      ) {
        setStatus("CHANGED")
        setFlowError("当前登录状态已变化，请重新核对账号并获取微信登录码")
        return
      }
      polling = true
      try {
        const result = await api.request<any>(
          queryUrl("/users/web-login/status/", {
            ticket: flow.ticket,
            poll_token: flow.poll_token,
          }),
          { public: true }
        )
        if (!active || generation.current !== flow.generation) return
        if (getSession("boss").revision !== flow.revision) {
          setStatus("CHANGED")
          setFlowError("当前登录状态已变化，请重新核对账号并获取微信登录码")
          return
        }
        if (result.status === "CONFIRMED") {
          if (
            verifyPhone &&
            flow.owner &&
            String(result.userInfo?.id) !== String(flow.owner)
          ) {
            setStatus("MISMATCH")
            setFlowError(
              "本次扫码账号与当前账号不一致，请使用当前账号对应的微信重新验证"
            )
            return
          }
          if (!result.token || !result.userInfo)
            throw new Error("登录结果尚未完整返回，请重新核对")
          persistSession("boss", result)
          setStatus("CONFIRMED")
          if (verifyPhone) navigate("/security", { replace: true })
          else {
            const next = query.get("next") || "/home"
            navigate(
              next.startsWith("/") &&
                !next.startsWith("//") &&
                !next.startsWith("/login")
                ? next
                : "/home",
              { replace: true }
            )
          }
        } else if (result.status === "EXPIRED") {
          setStatus("EXPIRED")
          setFlowError("二维码已失效，请重新获取微信登录码")
        } else setFlowError("")
      } catch (error: any) {
        if (active)
          setFlowError(error.message || "登录状态暂未更新，请稍后重试")
      } finally {
        polling = false
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2500),
      countdown = window.setInterval(
        () =>
          setRemaining(
            Math.max(
              0,
              Math.ceil(flow.expires_in - (Date.now() - flow.startedAt) / 1000)
            )
          ),
        1000
      )
    return () => {
      active = false
      window.clearInterval(timer)
      window.clearInterval(countdown)
    }
  }, [flow, status, verifyPhone])
  const pending = flow && status === "PENDING",
    mobileLaunchUrl = pending && mobileAuthorization && typeof flow.launch_url === "string" && /^https:\/\//.test(flow.launch_url) ? flow.launch_url : "",
    mobileLaunchUnavailable = Boolean(pending && mobileAuthorization && !mobileLaunchUrl)
  return (
    <div className="boss-login-layout">
      <section className="boss-login-welcome">
        <Badge variant="outline" className="border-primary/20 bg-primary/5 text-primary">微信安全登录</Badge>
        <Title
          title={verifyPhone ? "微信授权并验证手机号" : "每一次组队，都从安心登录开始。"}
          description={verifyPhone ? "使用当前账号对应的微信完成验证" : `微信快捷登录${store.name}老板服务中心`}
        />
        <p className="boss-muted">{coinText("使用微信快捷登录，验证手机号后即可管理订单、浪花与专属服务。")}</p>
        <div className="boss-login-benefits"><span><CalendarDays size={16} />订单进度</span><span><Wallet size={16} />钱包资产</span><span><Headphones size={16} />专属服务</span></div>
      </section>
      <Panel title={verifyPhone ? "微信授权验证" : mobileAuthorization ? "微信快捷登录" : "微信扫码登录"} className="boss-login-authorization">
        <div className="boss-form">
          {flowError && <div className="boss-feedback error" role="alert">{flowError}</div>}
          {!pending && <span className="boss-login-primary-icon"><MessageCircle size={24} /></span>}
          {pending && !mobileAuthorization && <Image src={flow.qr_image} alt="请使用微信扫描，核对后确认登录" className="boss-login-code" />}
          {pending && <p role="status" className="boss-login-status">{mobileAuthorization ? mobileLaunchUnavailable ? "微信授权入口尚未就绪" : "请在微信授权页确认登录" : "请使用微信扫码并确认"} · {remaining > 0 ? `约 ${remaining} 秒有效` : "正在核对有效状态"}</p>}
          {mobileLaunchUnavailable && <div className="boss-feedback error" role="alert">当前环境暂时无法打开微信授权，请重新获取或联系客服。</div>}
          {mobileLaunchUrl && <Button asChild><a href={mobileLaunchUrl} rel="noopener noreferrer"><MessageCircle size={18} />在微信中快捷打开授权页</a></Button>}
          {(!pending || !mobileAuthorization || mobileLaunchUnavailable) && <Button
            variant={pending ? "outline" : "default"}
            disabled={busy}
            onClick={() => {
              if (!agreed) {
                setAgreementError(true)
                document.getElementById("boss-login-privacy")?.focus()
                return
              }
              setAgreementError(false)
              void run(start, "").catch((error) => setFlowError(error.message))
            }}
          >
            {busy ? mobileAuthorization ? "正在获取微信授权…" : "正在获取微信登录码…" : pending ? mobileAuthorization ? "重新获取微信授权" : "刷新二维码" : <><MessageCircle size={18} />{mobileAuthorization ? "微信快捷登录" : "微信扫码登录"}</>}
          </Button>}
          <div className="boss-login-agreement">
            <div className="boss-login-agreement-row">
              <input id="boss-login-privacy" name="privacy_agreement" autoComplete="off" type="checkbox" checked={agreed} aria-invalid={agreementError} aria-describedby={agreementError ? "boss-login-privacy-error" : undefined} disabled={busy} onChange={event => { setAgreed(event.target.checked); if (event.target.checked) setAgreementError(false) }} />
              <span><label htmlFor="boss-login-privacy">我已阅读并同意</label><Dialog><DialogTrigger asChild><button type="button">《隐私保护指引》</button></DialogTrigger><DialogContent className="boss-explanation-dialog"><DialogHeader><span className="boss-explanation-icon"><ShieldCheck size={20} /></span><DialogTitle>隐私保护指引</DialogTitle><DialogDescription asChild><div><p>微信身份、公开头像昵称与手机号仅用于识别、创建或绑定当前门店的老板账号，以便找回原有订单、钱包与服务记录。</p><p>授权不会读取微信聊天记录、通讯录或与账号服务无关的信息。手机号仍需按微信授权页指引完成短信验证，网页仅展示脱敏信息。</p></div></DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button className="w-full">我知道了</Button></DialogClose></DialogFooter></DialogContent></Dialog><label htmlFor="boss-login-privacy">，授权用于账号识别与绑定。</label></span>
            </div>
            {agreementError && <p id="boss-login-privacy-error" className="boss-login-agreement-error" role="alert">请先阅读并同意隐私保护指引</p>}
          </div>
          <LoginSupport />
          <p className="boss-login-privacy-note"><LockKeyhole size={14} />授权仅用于账号识别与绑定，不读取微信聊天记录</p>
        </div>
      </Panel>
    </div>
  )
}

function LoginSupport() {
  const [open, setOpen] = useState(false)
  const load = useLoad(async () => open ? api.get<any>(getSession("boss").token ? "/support/contact-card/" : "/support/public-contact/") : null, [open], false)
  const card = load.data
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button type="button" variant="ghost" className="boss-login-support"><Headphones size={16} />账号有问题？联系客服</Button></DialogTrigger>
    <DialogContent className="boss-explanation-dialog">
      <DialogHeader><DialogTitle className="text-center">企业微信客服</DialogTitle><DialogDescription className="text-center">使用微信扫码，添加企业微信客服</DialogDescription></DialogHeader>
      <State {...load} compact>
        {card?.qrcode_url ? <Image src={card.qrcode_url} alt="企业微信客服二维码，请使用微信扫码" className="boss-login-support-code" /> : <p className="boss-muted text-center">客服二维码暂未配置，可通过企业微信客服入口咨询。</p>}
        {card?.wecom_service_url && /^https:\/\//.test(card.wecom_service_url) && <Button asChild className="w-full"><a href={card.wecom_service_url} target="_blank" rel="noopener noreferrer">打开微信客服<ChevronRight size={16} /></a></Button>}
      </State>
    </DialogContent>
  </Dialog>
}

function HomeGameplayIcon({ name }: { name: string }) {
  const Icon = ({ "王者荣耀": Gamepad2, "英雄联盟": AudioLines, "和平精英": Headphones, "金铲铲之战": Sparkles } as Record<string, typeof Gamepad2>)[name] || Gamepad2
  return <Icon size={36} strokeWidth={1.3} />
}

type HomeSlide = { id: string; label: string; title: string; to: string; icon: typeof Bell }
function HomeCarousel({ announcements, activities, content }: { announcements: Row[]; activities: Row[]; content: Row[] }) {
  const [index, setIndex] = useState(0), [paused, setPaused] = useState(false)
  const slides: HomeSlide[] = [
    ...announcements.map(item => ({ id: `announcement-${item.id}`, label: "公告", title: item.title, to: `/announcement-detail?id=${item.id}`, icon: Bell })),
    ...activities.map(item => ({ id: `activity-${item.id}`, label: "活动", title: item.title, to: `/club-content?id=${item.id}&kind=HOT&from=home`, icon: Trophy })),
    ...content.filter(item => item.kind === "RIGHTS").map(item => ({ id: `rights-${item.id}`, label: "权益", title: item.title, to: `/club-content?id=${item.id}&kind=RIGHTS&from=home`, icon: ShieldCheck })),
  ].filter(item => item.title)
  useEffect(() => {
    if (paused || slides.length < 2 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return
    const timer = window.setInterval(() => setIndex(current => (current + 1) % slides.length), 4500)
    return () => window.clearInterval(timer)
  }, [paused, slides.length])
  if (!slides.length) return null
  const current = slides[index % slides.length], ActiveIcon = current.icon
  return <Link
    className="boss-home-announcement"
    data-slide={current.id}
    to={current.to}
    aria-label={`${current.label}：${current.title}，第 ${index % slides.length + 1} 条，共 ${slides.length} 条`}
    onMouseEnter={() => setPaused(true)}
    onMouseLeave={() => setPaused(false)}
    onFocus={() => setPaused(true)}
    onBlur={() => setPaused(false)}
  >
    <ActiveIcon size={16} />
    <div key={current.id} className="boss-home-announcement-copy"><span>{current.label}</span><p>{current.title}</p></div>
    <span className="boss-home-announcement-count">{index % slides.length + 1} / {slides.length}</span>
    <ChevronRight size={16} />
  </Link>
}

export function HomePage() {
  const brand = useBossBrand()
  const store = useCurrentStore()
  const session = useSession("boss"),
    navigate = useNavigate(),
    [keyword, setKeyword] = useState(""),
    [entryItems, setEntryItems] = useState<Row[]>([]),
    [entryPage, setEntryPage] = useState<number | null>(null),
    entryKey = useRef("")
  const load = useLoad(async () => {
    const results = await Promise.allSettled([
      api.get<any>("/orders/gameplays/"),
      api.get<any>("/club/content/"),
      api.get<any>("/club/content/?kind=HOT"),
      ...(session?.token
        ? [
            api.get<any>("/orders/stats/"),
            api.get<any>("/banners/"),
            api.get<any>("/announcements/?limit=8"),
            api.get<any>("/club/feed/"),
            api.get<any>("/users/checkin/"),
          ]
        : [
            Promise.resolve(null),
            Promise.resolve([]),
            api.get<any>("/announcements/?limit=8"),
            Promise.resolve([]),
            Promise.resolve(null),
          ]),
    ])
    const get = (index: number, fallback: any) =>
      results[index]?.status === "fulfilled"
        ? (results[index] as PromiseFulfilledResult<any>).value
        : fallback
    if (results[0]?.status === "rejected" && results[1]?.status === "rejected")
      throw results[0].reason
    return {
      gameplays: rows(get(0, [])),
      gameplaysError: results[0]?.status === "rejected" ? (results[0].reason?.message || "服务内容读取失败，请重试") : "",
      content: rows(get(1, [])),
      contentError: results[1]?.status === "rejected" ? (results[1].reason?.message || "入场说明读取失败") : "",
      activities: rows(get(2, [])),
      stats: get(3, null),
      banners: rows(get(4, [])),
      announcements: rows(get(5, [])),
      feed: rows(get(6, [])),
      checkin: get(7, null),
      checkinError: session?.token && results[7]?.status === "rejected" ? (results[7].reason?.message || "签到进度读取失败") : "",
      activitiesError: results[2]?.status === "rejected" ? (results[2].reason?.message || "活动读取失败") : "",
      privateError: !!session?.token && results[3]?.status === "rejected",
    }
  }, [session?.revision])
  useEffect(() => {
    if (!load.data || load.data.contentError || load.data.activitiesError) return
    // Use each kind's first published item in the server's order. Text-only content is eligible.
    const items = [
      load.data.activities[0],
      load.data.content.find(item => item.kind === "ORDER_RULES"),
      load.data.content.find(item => item.kind === "RIGHTS"),
    ].filter(Boolean) as Row[]
    setEntryItems(items)
    const key = items.length
      ? `xa:store:${getStoreSlug()}:xa:entry-posters:` +
        items.map((item) => `${item.id}:${item.updated_at}`).join("|")
      : ""
    if (key === entryKey.current) return
    entryKey.current = key
    if (!key) {
      setEntryPage(null)
      return
    }
    try {
      setEntryPage(localStorage.getItem(key) ? null : 0)
    } catch {
      setEntryPage(0)
    }
  }, [load.data])
  function closeEntry() {
    if (entryKey.current) {
      try {
        // Remember only an explicit close, not showing or changing a poster.
        localStorage.setItem(entryKey.current, "true")
      } catch {
        toast.error("已关闭本次入场内容，浏览器未能保存关闭记忆")
      }
    }
    setEntryPage(null)
  }
  async function banner(item: Row) {
    try {
      const current = rows(await api.get("/banners/")).find(
        (row) => row.id === item.id
      )
      if (!current || current.link_available === false)
        throw new Error(
          current?.link_unavailable_reason === "QUALIFICATION_REQUIRED"
            ? "请先到领券中心查看套餐资格券"
            : "活动已更新，请刷新首页后重试"
        )
      if (
        current.link_type !== item.link_type ||
        current.link_value !== item.link_value
      ) {
        load.reload()
        throw new Error("活动内容已更新，请重新查看")
      }
      const value = String(current.link_value || "")
      if (current.link_type === "PRODUCT") navigate("/service?id=" + value)
      else if (current.link_type === "GAMEPLAY")
        navigate("/catalog?gameplay=" + value)
      else if (current.link_type === "CONTENT")
        navigate("/club-content?id=" + value)
      else if (current.link_type === "ANNOUNCEMENT")
        navigate("/announcement-detail?id=" + value)
      else if (
        current.link_type === "PAGE" &&
        [
          "catalog",
          "companions",
          "gifts",
          "coupons",
          "checkin",
          "points",
          "membership",
          "support",
        ].includes(value)
      )
        navigate("/" + value)
      else if (current.link_type === "URL" && value.startsWith("https://"))
        window.open(value, "_blank", "noopener,noreferrer")
    } catch (error) {
      fail(error)
    }
  }
  const data = load.data,
    entryOpen = entryPage != null && entryItems.length > 0,
    entryActivity = entryItems.find(item => item.kind === "HOT"),
    entryOrderRules = entryItems.find(item => item.kind === "ORDER_RULES"),
    entryRights = entryItems.find(item => item.kind === "RIGHTS"),
    showingRules = (entryPage ?? 0) > 0,
    initial = load.loading && !data,
    calendar = data?.checkin,
    checkinStart = calendar ? Math.floor((calendar.today - 1) / 7) * 7 + 1 : 1,
    activities = data?.activities || [],
    filteredGameplays = (data?.gameplays || []).filter((item: Row) => !keyword.trim() || [item.name, item.subtitle, item.description, item.game_category_name].some(value => String(value || "").toLocaleLowerCase().includes(keyword.trim().toLocaleLowerCase()))),
    featuredActivity = activities[0]
  const sectionHeading = (title: string, to: string, action: string, description?: string) => <div className={"boss-home-section-heading" + (description ? "" : " simple")}><div><h2>{title}</h2>{description && <p>{description}</p>}</div><Button asChild variant="ghost"><Link to={to}>{action}<ChevronRight size={14} /></Link></Button></div>
  const moduleError = (title: string, message: string) => <div className="boss-home-module-error" role="alert"><AlertCircle size={24} /><h3>{title}</h3><p>{message}</p><Button variant="outline" disabled={load.loading} onClick={load.reload}>重新加载</Button></div>
  return (
    <>
      <Title title={store.name} description="找到合拍的队友，现在开局" support />
      <form className="boss-filter boss-home-search" onSubmit={event => { event.preventDefault(); navigate("/catalog?keyword=" + encodeURIComponent(keyword)) }}><SearchField label="搜索服务" value={keyword} onChange={setKeyword} placeholder="游戏、玩法或服务名称" /><Button><Search size={16} />搜索</Button></form>
      {initial ? <section className="boss-home-hero-skeleton" aria-busy="true" aria-label="正在加载首页"><Skeleton className="h-5 w-28" /><Skeleton className="h-9 w-4/5" /><Skeleton className="h-4 w-full" /><Skeleton className="h-11 w-full rounded-lg" /></section> : <section className="boss-home-hero" aria-labelledby="boss-home-hero-title"><div className="boss-home-hero-content"><Badge>游戏陪伴</Badge><h2 id="boss-home-hero-title">{brand?.welcome || <>先听听声音，<br />再决定和谁组队</>}</h2><p>从轻松体验到默契陪伴，找到合拍的队友。</p><Button asChild><Link to="/companions">看看谁在线<ArrowRight size={16} /></Link></Button></div><span className="boss-home-hero-ring first" aria-hidden="true" /><span className="boss-home-hero-ring second" aria-hidden="true" /><AudioLines className="boss-home-hero-art" aria-hidden="true" strokeWidth={1.2} /></section>}
      {data && <HomeCarousel announcements={data.announcements} activities={data.activities} content={data.content} />}
      {!!data?.banners.length && <section className="boss-home-banners" aria-label="门店推荐">{data.banners.map((item: Row) => <button key={item.id} type="button" onClick={() => void banner(item)} aria-label={item.title || "查看活动"}><Image src={item.image_url} alt={item.title || "活动"} className="boss-banner" /></button>)}</section>}
      <div className="boss-home-body">
        {data && (load.error || data.contentError) && <section className="boss-home-refresh-error" role="alert"><AlertCircle size={20} /><div><h2>{load.error ? "首页暂时无法更新" : "入场说明暂时没有加载成功"}</h2><p>{load.error ? "当前显示上次读取的内容。" : "入场说明尚未读取完整。"}{load.error || data.contentError}</p></div><Button variant="outline" disabled={load.loading} onClick={load.reload}>重新读取</Button></section>}
        {!session?.token && <section className="boss-home-guest"><UserRound size={24} /><div><h2>登录，继续你的游戏生活</h2><p>查看钱包、服务进度与专属权益</p></div><Button asChild><Link to="/login">微信登录</Link></Button></section>}
        {session?.token && initial && <section className="boss-home-progress-skeleton" aria-busy="true" aria-label="正在加载服务进度"><Skeleton className="h-5 w-40" /><Skeleton className="h-4 w-52" /><div>{[1, 2, 3].map(key => <Skeleton className="h-14 w-full" key={key} />)}</div></section>}
        {data?.stats && <section className="boss-service-progress"><div className="boss-service-progress-heading"><div><h2>你的服务有新进度</h2><p>接单与履约状态，随时掌握</p></div><Button asChild variant="ghost"><Link to="/orders">全部<ChevronRight size={14} /></Link></Button></div><div className="boss-stats">{[["pending", "待接单"], ["grabbed", "已接单"], ["in_service", "服务中"]].map(([key, title]) => <Link key={key} to={"/orders?status=" + (key === "in_service" ? "in_progress" : key)}><strong>{data.stats[key] || 0}</strong><span>{title}</span></Link>)}</div></section>}
        <section className="boss-home-shortcuts"><h2>快捷入口</h2><div className="boss-quick-grid">{[{ title: "每日签到", path: "checkin", icon: CalendarDays }, { title: "领券中心", path: "coupons", icon: Ticket }, { title: "门店榜单", path: "rankings", icon: Trophy }, { title: "心意礼物", path: "gifts", icon: Heart }].map(item => <Link key={item.path} to={"/" + item.path}><item.icon size={20} strokeWidth={1.8} />{item.title}</Link>)}</div></section>
        <section className="boss-home-gameplays" id="home-gameplays">{sectionHeading("今天，想怎么玩", "/catalog", "全部服务", "从轻松体验到默契陪伴")}
          {initial ? <div className="boss-home-gameplay-loading" aria-busy="true" aria-label="正在加载玩法"><Skeleton className="h-40 w-full rounded-lg" /><Skeleton className="h-40 w-full rounded-lg" /></div> : data?.gameplaysError || (!data && load.error) ? moduleError("服务内容暂时没有加载成功", data?.gameplaysError || load.error) : filteredGameplays.length ? <div className="boss-home-gameplay-grid" role="region" tabIndex={0} aria-label="玩法列表，可左右滑动或使用方向键滚动">{filteredGameplays.slice(0, 4).map((item: Row) => <Link key={item.id} to={"/catalog?gameplay=" + item.id} className="boss-home-gameplay-card"><span className="boss-home-gameplay-art"><HomeGameplayIcon name={item.name} /></span><span className="boss-home-gameplay-copy"><strong>{item.name}</strong><small>{item.subtitle || item.description}</small><span>{item.starting_price != null ? <>{money(item.starting_price)}<small>{coinUnit()}{item.starting_price_unit ? " / " + item.starting_price_unit : ""}起</small></> : <>—<small>价格待公布</small></>}</span></span></Link>)}</div> : <div className="boss-home-module-empty"><Search size={24} /><h3>{keyword.trim() ? "没有找到相关玩法" : "玩法正在准备中"}</h3><p>{keyword.trim() ? "试试其他关键词，或查看全部服务。" : "暂未上架玩法，请稍后查看或联系客服。"}</p>{keyword.trim() && <Button variant="outline" onClick={() => setKeyword("")}>清空搜索</Button>}</div>}
        </section>
        {calendar && <section className="boss-home-checkin"><div className="boss-home-checkin-heading"><div><h2>每日打卡</h2><p>本月已签到 {calendar.checked_count} 天 · 连续 {calendar.continuous_days} 天</p></div><CalendarDays size={20} /></div><div className="boss-home-checkin-week">{Array.from({ length: 7 }, (_, index) => { const day = checkinStart + index, checked = (calendar.checked_days || []).includes(day), today = calendar.today === day; return day <= calendar.days_in_month ? <div key={day} className={(checked ? "checked " : "") + (today ? "today" : "")} aria-label={`${calendar.month}月${day}日，${checked ? "已签到" : today ? "今天" : "未签到"}`}><span>{today ? "今天" : day + "日"}</span><strong>{checked ? <Check size={14} /> : today ? "待签" : "·"}</strong></div> : <div key={day} aria-hidden="true">—</div> })}</div><div className="boss-home-checkin-footer"><div><h3>{calendar.full_attendance_reward?.name ? "全勤奖励 · " + calendar.full_attendance_reward.name : "查看本月签到奖励"}</h3><p>查看今日消费进度与签到条件</p></div><Button asChild variant="outline"><Link to="/checkin">{calendar.today_checked ? "查看签到" : "去打卡"}</Link></Button></div></section>}
        {session?.token && !initial && data && !calendar && <section className="boss-home-checkin"><div className="boss-home-checkin-heading"><div><h2>每日打卡</h2><p>本月已签到 — 天 · 连续 — 天</p></div><CalendarDays size={20} /></div><div className="boss-home-checkin-week">{[1, 2, 3, 4, 5, 6, 7].map(key => <div key={key}><span>—</span><strong>待核对</strong></div>)}</div><div className="boss-home-checkin-footer"><div><h3>签到奖励待核对</h3><p>{data.checkinError || "暂未获取签到资料，请刷新查看。"}</p></div><Button variant="outline" disabled={load.loading} onClick={load.reload}>重新读取</Button></div></section>}
        <section className="boss-home-activities">{sectionHeading("俱乐部活动", "/club-content?kind=HOT&from=home", "全部活动")}
          {initial ? <Skeleton className="h-24 w-full rounded-lg" /> : data?.activitiesError ? moduleError("活动暂时没有加载成功", data.activitiesError) : featuredActivity ? <Link className="boss-home-activity-feature" to={"/club-content?id=" + featuredActivity.id + "&kind=HOT&from=home"}><span><Trophy size={28} /></span><div><Badge variant="secondary">俱乐部活动</Badge><h3>{featuredActivity.title}</h3><p>了解活动与参与规则 <ChevronRight size={12} /></p></div></Link> : <div className="boss-home-activity-feature empty"><span><Trophy size={28} /></span><div><h3>新的活动正在准备中</h3><p>活动发布后，会在这里展示。</p></div></div>}
          <div className="boss-home-activity-links"><Link to="/points"><Sparkles size={16} /><strong>积分商城</strong><small>积分兑换好礼</small></Link><Link to="/club-content?kind=HOT&from=home"><Megaphone size={16} /><strong>活动回顾</strong><small>查看往期活动</small></Link></div>
        </section>
        <section className="boss-home-rights">{sectionHeading("老板专属权益", "/club-content?kind=RIGHTS&from=home", "权益说明")}<Link className="boss-home-gift-feature" to="/gifts"><div><h3>把欣赏，送给 TA</h3><p>一份礼物，让默契更进一步</p><span>进入礼物单<ArrowRight size={14} /></span></div><Gift size={64} strokeWidth={1.2} /></Link><div className="boss-home-rights-links">{[{ label: "我的好券", note: "领券享优惠", path: "/coupons?tab=mine", icon: Ticket }, { label: "积分好礼", note: "兑换心愿", path: "/points", icon: Sparkles }, { label: "专属权益", note: "了解规则", path: "/club-content?kind=RIGHTS&from=home", icon: ShieldCheck }].map(item => <Link key={item.path} to={item.path}><item.icon size={16} /><strong>{item.label}</strong><small>{item.note}</small></Link>)}</div></section>
        {!!data?.feed.length && <section className="boss-home-feed boss-home-extra">{sectionHeading("俱乐部动态", "/messages?tab=feed", "全部动态")}<div className="boss-home-extra-card">{data.feed.slice(0, 5).map((item: Row) => <div className="boss-home-feed-row" key={item.id}><span><Sparkles size={16} /></span><div><p>{item.content || item.text || item.message}</p><small>{date(item.created_at)}</small></div></div>)}</div></section>}
        {data?.privateError && <section className="boss-home-module-error" role="alert"><p>个人服务进度未能更新。</p><Button variant="outline" onClick={load.reload}>重新读取</Button></section>}
        {load.loading && data && <p className="boss-home-update" role="status">正在更新最新首页内容…</p>}
        <footer className="boss-home-footer"><p>{store.name} · 每一份陪伴，都认真对待</p><Button asChild variant="ghost"><Link to="/support"><Headphones size={16} />客服与帮助<ChevronRight size={14} /></Link></Button></footer>
      </div>
      <Dialog open={entryOpen} onOpenChange={open => { if (!open) closeEntry() }}>
        <DialogContent className="boss-entry-dialog sm:max-w-md" showCloseButton={false}>
          <DialogHeader>
            <div className="boss-entry-heading-row"><div><p>欢迎来到{store.name}</p><DialogTitle className="boss-entry-title">{showingRules ? "下单须知" : "近期活动"}</DialogTitle></div><Button type="button" variant="ghost" size="icon" className="boss-entry-close" onClick={closeEntry} aria-label="关闭入场弹窗"><X size={16} /></Button></div>
            <DialogDescription className="sr-only">查看近期活动与下单须知，关闭后返回首页。</DialogDescription>
          </DialogHeader>
          <div className="boss-entry-tabs" role="group" aria-label="切换入场内容"><Button type="button" variant={showingRules ? "ghost" : "secondary"} aria-pressed={!showingRules} onClick={() => setEntryPage(0)}>近期活动</Button><Button type="button" variant={showingRules ? "secondary" : "ghost"} aria-pressed={showingRules} onClick={() => setEntryPage(1)}>下单须知</Button></div>
          {!showingRules ? <section className="boss-entry-hot" id="boss-entry-hot" aria-label="近期活动"><Trophy size={36} /><h3>{entryActivity?.title || "近期活动待公布"}</h3>{entryActivity?.content ? <RichContent html={entryActivity.content} /> : <p className="boss-entry-pending">{entryActivity?.image_url ? "请查看下方活动内容。" : "活动内容待公布，可在俱乐部活动目录查看最新发布。"}</p>}<Badge>俱乐部活动</Badge>{entryActivity?.image_url && <a className="boss-entry-card-media" href={entryActivity.image_url} target="_blank" rel="noopener noreferrer" aria-label={"查看" + entryActivity.title + "活动原图"}><Image src={entryActivity.image_url} alt={entryActivity.title} /></a>}</section> : <section className="boss-entry-rules" id="boss-entry-rules" aria-label="下单须知"><ShieldCheck size={28} /><h3>{entryOrderRules?.title || "下单前，请确认服务内容"}</h3>{entryOrderRules?.content ? <RichContent html={entryOrderRules.content} /> : <p className="boss-entry-pending">{entryOrderRules?.image_url ? "请查看下方下单须知。" : "下单须知待公布，请在下单前查看服务详情。"}</p>}{entryOrderRules?.image_url && <a className="boss-entry-card-media" href={entryOrderRules.image_url} target="_blank" rel="noopener noreferrer" aria-label={"查看" + entryOrderRules.title + "原图"}><Image src={entryOrderRules.image_url} alt={entryOrderRules.title} /></a>}{entryRights && <details className="boss-entry-rights"><summary>{entryRights.title}<ChevronRight size={16} /></summary><div>{entryRights.content ? <RichContent html={entryRights.content} /> : <p className="boss-entry-pending">{entryRights.image_url ? "请查看下方权益说明。" : "权益说明准备中。"}</p>}{entryRights.image_url && <a className="boss-entry-card-media" href={entryRights.image_url} target="_blank" rel="noopener noreferrer" aria-label={"查看" + entryRights.title + "原图"}><Image src={entryRights.image_url} alt={entryRights.title} /></a>}</div></details>}</section>}
          <DialogFooter><Button type="button" onClick={() => showingRules ? closeEntry() : setEntryPage(1)}>{showingRules ? "知道了" : "下一页 · 下单须知"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function MinePage() {
  const store = useCurrentStore()
  const guest = useGuestData()
  const load = useLoad(async () => {
    const [profile, wallet, stats, member] = await Promise.all([
      api.get<any>("/users/me/"),
      api.get<any>("/wallet/info/"),
      api.get<any>("/orders/stats/"),
      api.get<any>("/club/me/"),
    ])
    return { profile, wallet, stats, member }
  })
  const quickLinks = [
    { path: "coupons?tab=mine", title: "优惠券", icon: Ticket },
    { path: "favorites", title: "我的收藏", icon: Heart },
    { path: "checkin", title: "每日签到", icon: CalendarDays },
    { path: "achievements", title: "我的成就", icon: Trophy },
  ]
  const memberLinks = [
    { path: "gifts", title: "礼物单", icon: Gift },
    { path: "membership", title: "我的会员", icon: Sparkles },
    { path: "points", title: "积分与幸运转盘", icon: Trophy },
    { path: "club-content?kind=RIGHTS", title: "老板权益", icon: ShieldCheck },
  ]
  const serviceLinks = [
    { path: "reservations", title: "我的预约", icon: CalendarDays },
    { path: "profile", title: "个人与游戏资料", icon: UserRound },
    { path: "security", title: "账号与微信绑定", icon: ShieldCheck },
    { path: "club-content?kind=RULES", title: "俱乐部规范", icon: FileText },
    { path: "announcements", title: "平台公告", icon: Bell },
    { path: "support", title: "客服与帮助", icon: Headphones },
  ]
  const data = guest ? {
    profile: { nickname: "游客预览", id: "—", avatar: "", isWechatBound: false },
    wallet: { balance: null },
    stats: {},
    member: { vip: null, boss_type: null, display_tags: [], points: null },
  } : load.data
  const vipLevel = Math.min(6, Math.max(1, Number(data?.member.vip?.level) || 1))
  return (
    <>
      <Title title="个人中心" description="账号、钱包与每一次服务记录" support action={guest ? <Button asChild size="sm"><Link to="/login?next=%2Fmine">登录账号</Link></Button> : undefined} />
      <State {...load} data={data}>
        {data && <div className="boss-mine-layout">
          <aside className="boss-mine-account">
            <section className={`boss-profile-card vip-level-${vipLevel}`} aria-label="本店账号资料">
              <Link to="/profile" className="boss-mine-profile-link">
                <Avatar className="boss-mine-avatar"><AvatarImage src={data.profile.avatar || undefined} alt={guest ? "游客预览头像" : "我的头像"} /><AvatarFallback>{(data.profile.nickname || "老板").slice(0, 1)}</AvatarFallback></Avatar>
                <div><h2>{data.profile.nickname || "老板"}</h2><p>{guest ? "登录后读取本店账号" : `本店账号 ID ${data.profile.id}`}</p><p>{store.name} · {guest ? "完整功能预览" : "微信账号"}</p></div>
                <ChevronRight size={16} />
              </Link>
              <div className="boss-mine-profile-details">
                <Link className={"boss-security-line" + (data.profile.isWechatBound ? "" : " is-unbound")} to="/security">{data.profile.isWechatBound ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}<span>{guest ? "登录后核对微信绑定" : data.profile.isWechatBound ? "微信已绑定" : "微信尚未绑定"}</span><span>账号安全</span><ChevronRight size={16} /></Link>
              </div>
            </section>
            <Link to="/wallet" className="boss-wallet-summary">
              <span><span><Wallet size={16} />本店可用余额</span><span>查看账单<ChevronRight size={16} /></span></span>
              <strong>{guest ? "—" : <>{money(data.wallet.balance)}<small>{coinUnit()}</small></>}</strong>
              <span className="boss-wallet-foot">{guest ? "登录后核对充值、支付与退款明细" : "充值、支付与退款明细均在本店钱包核对"}</span>
            </Link>
          </aside>
          <div className="boss-mine-main">
            <Panel className="boss-mine-orders">
              <div className="boss-section-heading"><div><h2>我的订单</h2><p>服务进度一目了然</p></div><Link to="/orders">全部订单<ChevronRight size={16} /></Link></div>
              <div className="boss-stats">
                {[["pending", "待接单"], ["grabbed", "已接单"], ["in_service", "服务中"]].map(([key, label]) => <Link key={key} to={"/orders?status=" + (key === "in_service" ? "in_progress" : key)}><strong>{guest ? "—" : data.stats[key] || 0}</strong><span>{label}</span></Link>)}
              </div>
            </Panel>
            <section className="boss-mine-shortcuts" aria-labelledby="mine-shortcuts-title"><div><h2 id="mine-shortcuts-title">常用功能</h2><span>优惠与成长记录</span></div><div className="boss-quick-grid">{quickLinks.map(item => <Link key={item.path} to={"/" + item.path}><span><item.icon size={20} /></span>{item.title}</Link>)}</div></section>
            <div className="boss-mine-menu-grid">{[{ title: "会员与权益", links: memberLinks }, { title: "账号与服务", links: serviceLinks }].map(group => <Panel key={group.title} title={group.title} className="boss-mine-menu"><div className="boss-list">{group.links.map(item => <Link className="boss-list-row" key={item.path} to={"/" + item.path}><item.icon size={16} /><span>{item.title}</span>{item.path === "points" && <small className="boss-muted">{guest ? "登录后查看" : `${data.member.points || 0} 积分`}</small>}{item.path === "membership" && <small className="boss-muted">{guest ? "登录后查看" : data.member.vip?.name || data.member.boss_type?.name || "普通会员"}</small>}{item.path === "security" && <small className="boss-muted">{guest ? "登录后核对" : data.profile.isWechatBound ? "已绑定" : "待绑定"}</small>}<ChevronRight size={16} className="right" /></Link>)}</div></Panel>)}</div>
          </div>
        </div>}
      </State>
    </>
  )
}

export function ProfilePage() {
  const load = useLoad(
      async () => {
        const [me, categories] = await Promise.all([
          api.get<any>("/users/me/"),
          api.get<any>("/orders/game-categories/"),
        ])
        return { me, categories: rows(categories) }
      },
      [],
      false
    ),
    { busy, run } = useAction()
  const [nickname, setNickname] = useState(""),
    [profiles, setProfiles] = useState<Row[]>([]),
    [dirty, setDirty] = useState(false),
    [avatarBusy, setAvatarBusy] = useState(false),
    [selected, setSelected] = useState(0)
  useEffect(() => {
    if (load.data && !dirty) {
      const loaded = load.data
      setNickname(loaded.me.nickname)
      const items = loaded.categories.map((item) => ({
        game_category: item.id,
        name: item.name,
        region: "",
        nickname: "",
        uid: "",
        is_default: false,
        ...rows(loaded.me.game_profiles).find(
          (row) => row.game_category === item.id
        ),
      }))
      setProfiles(items)
      setSelected(
        Math.max(
          0,
          items.findIndex((item) => item.is_default)
        )
      )
    }
  }, [load.data, dirty])
  function edit(index: number, key: string, value: any) {
    setDirty(true)
    setProfiles((items) =>
      items.map((item, offset) =>
        offset === index ? { ...item, [key]: value } : item
      )
    )
  }
  async function save() {
    const profile = await api.patch<any>("/users/me/", {
      nickname: nickname.trim(),
      game_profiles: profiles.map((item, index) => ({
        game_category: item.game_category,
        region: String(item.region || "").trim(),
        nickname: String(item.nickname || "").trim(),
        uid: String(item.uid || "").trim(),
        is_default: index === selected,
      })),
    })
    updateProfile("boss", profile)
    setDirty(false)
    load.reload()
  }
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title title="个人资料" description="常用游戏资料将在下单时自动回填" />
      <State {...load}>
          <form
            className="boss-profile-edit"
            onSubmit={(event) => {
              event.preventDefault()
              void run(save, "资料已保存").catch(() => {})
            }}
          >
            <Panel title="账户资料">
            <div className="boss-list-row">
              <Image
                src={load.data?.me.avatar}
                alt="头像"
                className="boss-avatar"
              />
              <label className="boss-field min-w-0 flex-1">
                <span>{avatarBusy ? "正在保存头像…" : "更换头像"}</span>
                <input
                  aria-label="更换头像"
                  name="profile_avatar"
                  type="file"
                  className="block w-full max-w-full min-w-0 text-sm"
                  accept="image/jpeg,image/png,image/webp"
                  disabled={busy}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (!file) return
                    const data = new FormData()
                    data.append("avatar", file)
                    void run(async () => {
                      setAvatarBusy(true)
                      try {
                      const result = await api.upload<any>(
                        "/users/me/avatar/",
                        data
                      )
                      updateProfile("boss", result)
                      load.reload()

                      } finally { setAvatarBusy(false) }
                    }, "头像已更新").catch(() => {})
                  }}
                />
              </label>
            </div>
            <Field disabled={busy}
              label="昵称"
              value={nickname}
              onChange={(value) => {
                setNickname(value)
                setDirty(true)
              }}
              maxLength={50}
              required
            />
            <Link className="boss-profile-phone" to="/security"><Smartphone size={16} /><span>{load.data?.me.phone ? String(load.data.me.phone).replace(/^(\d{3}).*(\d{4})$/, "$1 **** $2") : "手机号未绑定"}</span><ChevronRight size={16} /></Link>
            </Panel>
            <div><Panel title="常用游戏">
            {!profiles.length && <p className="boss-inline-empty">暂无可用游戏资料，账号信息仍可单独保存。</p>}
            <Choice disabled={busy || !profiles.length}
              label="默认游戏"
              value={selected}
              onChange={(value) => {
                setSelected(Number(value))
                setDirty(true)
              }}
              options={profiles.map((item, index) => ({
                value: index,
                label: item.name,
              }))}
            />
            {profiles[selected] && (
              <div className="boss-form-grid">
                <Field disabled={busy}
                  label="游戏大区"
                  value={profiles[selected].region}
                  onChange={(value) => edit(selected, "region", value)}
                  maxLength={50}
                />
                <Field disabled={busy}
                  label="游戏昵称"
                  value={profiles[selected].nickname}
                  onChange={(value) => edit(selected, "nickname", value)}
                  maxLength={50}
                />
                <Field disabled={busy}
                  label="游戏 UID"
                  value={profiles[selected].uid}
                  onChange={(value) => edit(selected, "uid", value)}
                  maxLength={50}
                />
              </div>
            )}
            </Panel>
            <Panel className="boss-profile-save"><div className="boss-section-heading"><div><h2>{avatarBusy ? "正在保存头像" : busy ? "正在保存资料" : dirty ? "有未保存的修改" : "资料已同步"}</h2><p>{avatarBusy ? "头像会自动保存，其他修改不受影响" : dirty ? "保存后将在下次下单时自动回填" : "头像自动保存，其他资料请手动保存"}</p></div><CheckCircle2 size={20} className={dirty ? "boss-profile-dirty" : "boss-link-color"} /></div>
            <Button
              disabled={
                busy || load.loading || !!load.error || !nickname.trim()
              }
            >
              <Save size={16} />{busy ? "正在保存…" : dirty ? "保存修改" : "保存资料"}
            </Button>
            </Panel></div>
          </form>
      </State>
    </>
  )
}

export function SecurityPage() {
  const load = useLoad(() => api.get<any>("/users/me/")),
    { confirm, dialog } = useConfirm()
  const profile = load.data, protectedAccount = profile?.isWechatBound && profile?.isPhoneVerified
  return <><BackLink to="/mine">个人中心</BackLink><Title title="账号安全" description="核对登录身份与联系方式，敏感信息均已脱敏展示。" /><State {...load}>{profile && <div className="boss-security-layout">
    <section className={"boss-security-status" + (protectedAccount ? " protected" : "")}><ShieldCheck size={24} /><div><h2>{protectedAccount ? "账号资料已验证" : "继续完善账号保护"}</h2><p>{protectedAccount ? "微信身份与手机号已确认" : "请通过微信授权页核对身份并完成手机号验证"}</p></div></section>
    <Panel className="boss-security-identity"><div className="boss-security-card-header"><MessageCircle size={20} /><div><p>登录身份</p><h2>微信绑定</h2></div><Badge variant="outline" className={profile.isWechatBound ? "boss-available" : "boss-verification-pending"}>{profile.isWechatBound ? "已绑定" : "待绑定"}</Badge></div><div className="boss-security-card-body"><Info entries={[["账号", profile.username]]} /><div className="boss-security-card-note"><strong>{profile.isWechatBound ? "当前微信已关联本账号" : "尚未关联微信身份"}</strong><p>通过微信快捷登录核对当前账号身份，账号资产与订单保持关联。</p></div>{profile.isH5WechatBound ? <p className="boss-muted">可使用微信快捷登录当前账号。</p> : <WechatBindingButton onBound={() => { updateProfile("boss", { isWechatBound: true, isH5WechatBound: true }); void load.reload() }} />}<Button asChild variant="outline"><Link to="/login?verify_phone=1">微信授权并验证手机号</Link></Button></div></Panel>
    <Panel className="boss-security-phone"><div className="boss-security-card-header"><Smartphone size={20} /><div><p>联系方式</p><h2>验证手机号</h2></div><Badge variant="outline" className={profile.isPhoneVerified ? "boss-available" : "boss-verification-pending"}>{profile.isPhoneVerified ? "已验证" : "待验证"}</Badge></div><div className="boss-security-card-body"><div className="boss-security-card-note"><strong className="boss-security-phone-number">{profile.phone ? String(profile.phone).replace(/^(\d{3}).*(\d{4})$/, "$1 **** $2") : "尚未绑定手机号"}</strong><p>在微信授权页读取微信手机号并完成短信验证，网页仅脱敏展示。</p></div><Button asChild variant={profile.isPhoneVerified ? "outline" : "default"}><Link to="/login?verify_phone=1">{profile.isPhoneVerified ? "核对手机号授权" : "微信授权并验证手机号"}</Link></Button></div></Panel>
    <Panel title="账号密码登录" icon={LockKeyhole}><p className="boss-muted">设置或更新登录账号和密码，后续使用验证器或短信完成身份验证。</p>{profile.isPhoneVerified ? <PasswordSecurityForm username={profile.username} onSaved={(username) => { updateProfile("boss", { username }); void load.reload() }} /> : <Button disabled>先完成手机号验证</Button>}</Panel>
    <OtpSetupPanel role="boss" />
    <Panel title="账号与隐私" className="boss-security-help"><Explanation title="隐私保护指引" icon={FileLock2} returnLabel="返回账号安全">微信用于确认登录身份；手机号授权后还需通过短信验证码核对归属。网页仅展示脱敏手机号，正式授权与协议请在微信授权页核对。</Explanation><Button asChild variant="ghost"><Link to="/support"><Headphones size={16} /><span>绑定有问题，联系客服</span><ChevronRight size={16} /></Link></Button></Panel>
    <Panel title="退出当前账号" icon={LockKeyhole}><p className="boss-muted">退出后可使用微信重新登录，订单与钱包记录仍会保留。</p><Button variant="outline" className="text-destructive" onClick={() => confirm({ title: "退出登录", description: "退出后可使用微信重新登录，订单与钱包记录仍会保留。", confirm: "退出登录", variant: "destructive", action: async () => clearSession("boss") })}><LockKeyhole size={16} />退出登录</Button></Panel>
  </div>}</State>{dialog}</>
}

function WalletTransactionIcon({ type, positive }: { type: string; positive: boolean }) {
  const Icon = ({ TOPUP: ArrowDownLeft, PAY: ArrowUpRight, REFUND: RefreshCw, INCOME: Gift, WITHDRAW: ArrowUpRight, DEPOSIT: LockKeyhole, REWARD: Gift, ADJUST: ReceiptText, GIFT: Gift } as Record<string, typeof Gift>)[type] || (positive ? ArrowDownLeft : ArrowUpRight)
  return <span className={"boss-transaction-icon type-" + type.toLowerCase()}><Icon size={16} /></span>
}

export function WalletPage() {
  const query = useQuery(),
    filterNav = useRef<HTMLElement | null>(null),
    [rechargeOpen, setRechargeOpen] = useState(false),
    [filter, setFilter] = useState(query.get("tx_type") || ""),
    [page, setPage] = useState(1),
    load = useLoad(
      () =>
        api.get<any>(
          queryUrl("/wallet/transactions/", {
            page,
            page_size: 20,
            tx_type: filter,
          })
        ),
      [filter, page]
    )
  useEffect(() => {
    const nav = filterNav.current
    const selected = nav?.querySelector<HTMLElement>('[aria-pressed="true"]')
    if (!nav || !selected || !window.matchMedia("(max-width: 767px)").matches) return
    const left = selected.getBoundingClientRect().left - nav.getBoundingClientRect().left + nav.scrollLeft
    if (left < nav.scrollLeft) nav.scrollLeft = left
    else if (left + selected.offsetWidth > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = left + selected.offsetWidth - nav.clientWidth
  }, [filter, !!load.data])
  const labels: Row = {
    TOPUP: "充值入账",
    PAY: "订单支付",
    REFUND: "订单退款",
    INCOME: "收入",
    WITHDRAW: "提现",
    DEPOSIT: "押金",
    REWARD: "奖励",
    ADJUST: "账务调整",
    GIFT: "赠送",
  }
  const filters = [{ value: "", label: "全部", title: "全部流水" }, ...Object.entries(labels).map(([value, title]) => ({ value, title, label: ({ TOPUP: "充值", PAY: "支付", REFUND: "退款", ADJUST: "调整" } as Record<string, string>)[value] || title }))]
  return (
    <>
      <BackLink to="/mine">个人中心</BackLink>
      <Title
        title="我的钱包"
        description="每一笔资金变动，都有记录可查"
      />
      <State {...load} errorAction={page > 1 ? <Button variant="outline" onClick={() => setPage(value => value - 1)}>返回上一页</Button> : undefined}>
        {load.data && (
          <div className="boss-wallet-layout">
            <Panel className="boss-wallet-balance">
              <div className="boss-wallet-balance-main">
                <div className="boss-wallet-available"><p>可用余额</p><strong>{money(load.data.balance)}<small>{coinUnit()}</small></strong></div>
                <div className="boss-wallet-frozen"><div><span><LockKeyhole size={16} />冻结金额</span><strong>{money(load.data.frozen_amount)}<small>{coinUnit()}</small></strong></div><p>可用与冻结金额分别展示，资金变动以最新流水为准。</p></div>
              </div>
              <div className="boss-wallet-balance-parts" aria-label="余额构成">{[
                { title: "预存", key: "prepaid_balance" },
                { title: "赠送", key: "gift_balance" },
                { title: "赔款", key: "compensation_balance" },
              ].map(item => <div key={item.key}><span>{item.title}</span><strong>{load.data[item.key] == null ? "—" : <>{money(load.data[item.key])}<small>{coinUnit()}</small></>}</strong></div>)}</div>
            </Panel>
            <Panel className="boss-wallet-transactions">
              <div className="boss-wallet-transactions-header"><div className="boss-wallet-transactions-title"><div><h2>资金流水</h2><p>变动金额、业务备注与变动后余额</p></div><span>{load.data.total} 笔</span></div>
                <nav ref={filterNav} className="boss-wallet-filters" aria-label="资金流水筛选"><div>{filters.map(item => <button type="button" key={item.value || "all"} aria-pressed={filter === item.value} aria-label={item.title} onClick={() => { setFilter(item.value); setPage(1) }}><span>{item.label}</span></button>)}</div></nav>
              </div>
              <State compact
                empty={!load.data.transactions?.length}
                loading={load.loading}
                emptyAction={filter ? <Button variant="outline" onClick={() => { setFilter(""); setPage(1) }}>查看全部流水</Button> : undefined}
              >
                {rows(load.data.transactions).map((item) => (
                  <article className="boss-transaction-row" key={item.id}>
                    <WalletTransactionIcon type={item.tx_type} positive={Number(item.amount) > 0} />
                    <div className="boss-transaction-content">
                      <div className="boss-transaction-heading"><div><h3>
                        {item.is_referral_income
                          ? item.is_reversal
                            ? "推荐收益退回"
                            : "推荐收益"
                          : item.tx_type === "GIFT"
                            ? item.amount > 0
                              ? "赠送入账"
                              : item.amount < 0
                                ? "赠送支出"
                                : "赠送"
                            : labels[item.tx_type] || item.tx_type}
                      </h3><p>{date(item.created_at)}</p></div>
                      <strong className={Number(item.amount) > 0 ? "income" : undefined}>
                        {item.amount > 0 ? "+" : ""}
                        {money(item.amount)}<small>{coinUnit()}</small>
                      </strong></div>
                      {item.remark && <p className="boss-transaction-remark">{item.remark}</p>}
                      <div className="boss-transaction-balance"><span><ReceiptText size={14} />变动后余额</span><strong>{money(item.balance_after)} {coinText(" 浪花")}</strong></div>
                      <small className="boss-transaction-number">流水编号 {item.tx_no}</small>
                    </div>
                  </article>
                ))}
              </State>
              {load.data.total > 0 && <div className="boss-wallet-pagination">
                <Button
                  variant="outline"
                  onClick={() => setPage((value) => value - 1)}
                  disabled={page <= 1 || load.loading}
                >
                  上一页
                </Button>
                <span>
                  第 {page} 页，共 {load.data.total} 笔
                </span>
                <Button
                  variant="outline"
                  onClick={() => setPage((value) => value + 1)}
                  disabled={page * 20 >= load.data.total || load.loading}
                >
                  下一页
                </Button>
              </div>}
            </Panel>
            <aside className="boss-wallet-aside"><Panel className="boss-wallet-recharge"><div className="boss-wallet-recharge-heading"><span><Wallet size={20} /></span><div><h2>钱包充值</h2><p>由客服核对付款结果</p></div></div><p>联系客服核对充值金额与付款结果，实际入账以余额和流水为准。</p><Button onClick={() => setRechargeOpen(true)}><Headphones size={16} />联系客服充值</Button></Panel><Explanation title="钱包与流水说明" returnLabel="返回钱包">可用余额与冻结金额分开显示；每笔记录包含变动金额、业务备注、发生时间和变动后余额。充值由客服核对，结果未明确时请先刷新钱包，避免重复付款。</Explanation></aside>
          </div>
        )}
      </State>
      <Dialog open={rechargeOpen} onOpenChange={setRechargeOpen}><DialogContent className="boss-explanation-dialog"><DialogHeader><span className="boss-explanation-icon"><Headphones size={20} /></span><DialogTitle>联系客服充值</DialogTitle><DialogDescription>客服将核对充值金额与付款结果。请勿重复付款，实际入账以钱包余额和充值流水为准。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setRechargeOpen(false)}>返回钱包</Button><Button asChild><Link to="/support?scene=recharge" onClick={() => setRechargeOpen(false)}>联系充值客服</Link></Button></DialogFooter></DialogContent></Dialog>
    </>
  )
}
