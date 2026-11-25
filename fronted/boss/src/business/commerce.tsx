import { coinText, coinUnit, useCurrentStore, useSession } from "@xa/shared"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { Link, useNavigate } from "react-router"
import { Heart, Gamepad2, Headphones, ChevronRight, AudioLines, ShieldCheck, SlidersHorizontal, Check, Gift, MapPin, Sparkles, BadgeCheck, Star, CalendarClock, RotateCcw, ArrowDownUp, ArrowRight, ChevronDown, ChevronUp, X, Ticket, PackageCheck, WalletCards, Clock3, MessageCircleMore, AlertCircle, CircleUserRound } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { DirectoryCard, DirectoryFilter, DirectoryState } from "./companion-directory"
import { formatProviderName, ProviderName } from "./provider-name"
import { ProviderPickerCard } from "./provider-picker-card"
import {
  api,
  Choice,
  date,
  Image,
  LinkButton,
  money,
  Panel,
  PendingFeature,
  queryUrl,
  RichContent,
  rows,
  State,
  SearchField,
  Title,
  BackLink,
  useAction,
  useLoad,
  useQuery,
  type Row,
} from "./ui"
export { CheckoutPage } from "./checkout"
export { OrdersPage, OrderPage } from "./orders"
export { GiftsPage } from "./gifts"
export {
  ReservationsPage,
  ReservationPage,
  ReservationCreatePage,
} from "./reservations"

export const providerLimit = (service: Row | null | undefined) =>
  Number(service?.billing_provider_count) === 2 ? 2 : 1
export const providerIds = (value: string | null) => [
  ...new Set(
    String(value || "")
      .split(",")
      .map(Number)
      .filter((id) => Number.isSafeInteger(id) && id > 0)
  ),
]
const serviceTab = (service: Row, gameplay?: Row) =>
  /瓦罗兰特|无畏契约|英雄联盟/.test(service.game_category_name || "")
    ? "companion"
    : /跑刀|部门任务|安全箱/.test(
          [service.name, service.catalog_family].join(" ")
        )
      ? "other"
      : {
          experience: "experience",
          companion: "companion",
          fun: "fun",
          roulette: "fun",
          mission: "other",
        }[String(gameplay?.code || "").replace(/^xa-catalog-/, "")] || "regular"
const tabs = [
  { value: "", label: "全部服务" },
  { value: "experience", label: "体验单" },
  { value: "regular", label: "常规单" },
  { value: "companion", label: "陪玩单" },
  { value: "fun", label: "趣味单" },
  { value: "other", label: "其他" },
]

export function CatalogPage() {
  const query = useQuery(),
    navigate = useNavigate(),
    store = useCurrentStore(),
    [expanded, setExpanded] = useState(false),
    [keyword, setKeyword] = useState(query.get("keyword") || ""),
    [tab, setTab] = useState(query.get("tab") || ""),
    [category, setCategory] = useState(""),
    [platform, setPlatform] = useState(query.get("platform") || ""),
    [level, setLevel] = useState(query.get("level") || ""),
    [sort, setSort] = useState("price")
  const gameplayId = query.get("gameplay"),
    providerId = query.get("provider_id"),
    couponId = query.get("coupon_id")
  function clearContext(key: string) {
    const next = new URLSearchParams(query)
    next.delete(key)
    navigate("/catalog" + (next.size ? "?" + next : ""), { replace: true })
  }
  const load = useLoad(async () => {
    const [services, gameplays, categories, provider] = await Promise.all([
      api.get<any>(queryUrl("/orders/services/", { coupon_id: couponId })),
      api.get<any>("/orders/gameplays/"),
      api.get<any>("/orders/game-categories/"),
      providerId
        ? api.get<any>(`/users/escorts/${providerId}/?eligible_catalog=1`)
        : Promise.resolve(null),
    ])
    if (provider && !provider.can_order)
      throw new Error(
        provider.order_unavailable_reason || "该陪玩当前不可接单，请重新选择"
      )
    return {
      services: rows(services),
      gameplays: rows(gameplays),
      categories: rows(categories),
      provider,
    }
  }, [gameplayId, providerId, couponId])
  const visible = useMemo(() => {
    const data = load.data
    if (!data) return []
    const candidates = data.services.filter(
      (item) =>
        !item.is_gift &&
        !item.is_addon &&
        (!data.provider ||
          rows(data.provider.eligible_service_item_ids).includes(item.id) ||
          (data.provider.eligible_service_item_ids || []).includes(item.id)) &&
        (!gameplayId || String(item.gameplay) === gameplayId) &&
        (!category || String(item.game_category) === category) &&
        (!platform || item.platform === platform) &&
        (!level || item.security_level === level) &&
        (!tab ||
          serviceTab(
            item,
            data.gameplays.find((play) => play.id === item.gameplay)
          ) === tab)
    )
    const groups = new Map<string, Row[]>()
    for (const item of candidates) {
      const key = item.catalog_family
        ? JSON.stringify([
            item.game_category,
            item.gameplay,
            item.catalog_family,
          ])
        : String(item.id)
      groups.set(key, [...(groups.get(key) || []), item])
    }
    return [...groups.values()]
      .filter(
        (group) =>
          !keyword.trim() ||
          group.some((item) =>
            [
              item.name,
              item.catalog_family,
              item.description,
              item.catalog_variant,
              item.gameplay_name,
              item.game_category_name,
            ]
              .join(" ")
              .toLowerCase()
              .includes(keyword.trim().toLowerCase())
          )
      )
      .map<Row>((group) => ({
        ...group.reduce((best, item) =>
          item.price < best.price ? item : best
        ),
        variantsCount: group.length,
      }))
      .sort((a, b) =>
        sort === "price-desc" ? b.price - a.price : a.price - b.price
      )
  }, [load.data, keyword, gameplayId, category, platform, level, tab, sort])
  const unique = (field: string, name: string) => [
    ...new Map(
      (load.data?.services || [])
        .filter((item) => item[field])
        .map((item) => [
          item[field],
          { value: item[field], label: item[name] || item[field] },
        ])
    ).values(),
  ]
  function resetFilters() {
    setKeyword(""); setCategory(""); setPlatform(""); setLevel(""); setTab(""); setSort("price")
  }
  const activeFilters = [
    category ? { key: "category", label: load.data?.categories.find(item => String(item.id) === category)?.name || "已选游戏", clear: () => setCategory("") } : null,
    platform ? { key: "platform", label: unique("platform", "platform_name").find(item => item.value === platform)?.label || platform, clear: () => setPlatform("") } : null,
    level ? { key: "level", label: unique("security_level", "security_level_name").find(item => item.value === level)?.label || level, clear: () => setLevel("") } : null,
    keyword ? { key: "keyword", label: `搜索：${keyword}`, clear: () => setKeyword("") } : null,
  ].filter(item => item !== null)
  const hasLocalFilters = Boolean(activeFilters.length || tab)
  const hasScope = Boolean(gameplayId || providerId || couponId)
  return (
    <>
      <header className="boss-catalog-header boss-catalog-fidelity-header">
        <p className="boss-catalog-eyebrow">{store.name} · 服务商城</p>
        <h1>好服务，合你心意</h1><p>选好套餐，让下一局更有期待</p>
        <SearchField value={keyword} onChange={setKeyword} placeholder="搜索服务、玩法或游戏" label="搜索服务" />
      </header>
      <div className="boss-catalog-fidelity-body">
      {gameplayId && <section className="boss-catalog-context" aria-label="已选玩法"><Gamepad2 size={20} /><div><h2>{load.data?.gameplays.find(item => String(item.id) === gameplayId)?.name || "已选玩法"}</h2><p>仅展示当前玩法的可选套餐</p></div><Button variant="ghost" className="h-11" onClick={() => clearContext("gameplay")}>全部玩法</Button></section>}
      {providerId && <section className="boss-catalog-context" aria-label="已指定陪玩"><span className="boss-catalog-context-icon"><Headphones size={20} /></span><div><h2>为{load.data?.provider?.nickname ? <ProviderName value={load.data.provider.nickname} /> : "指定陪玩"}选择服务</h2><p>仅展示 TA 当前可接的套餐</p></div><Button variant="ghost" className="h-11" onClick={() => clearContext("provider_id")}>取消指定</Button></section>}
      {couponId && <section className="boss-catalog-context" aria-label="优惠券适用范围"><Ticket size={20} /><div><h2>优惠券适用套餐</h2><p>以下为本券适用套餐，结算时选择使用</p></div><Button variant="ghost" className="h-11" onClick={() => clearContext("coupon_id")}>全部服务</Button></section>}
      <section className="boss-game-filter" aria-label="游戏分类"><p>先选游戏</p><div className="boss-horizontal-options">
        {[{ value: "", label: "全部游戏" }, ...(load.data?.categories || []).map(item => ({ value: String(item.id), label: item.name }))].map(item => <button key={item.value} type="button" aria-pressed={category === item.value} onClick={() => setCategory(item.value)}>{item.value ? <Gamepad2 size={16} /> : <Sparkles size={16} />}{item.label}</button>)}
      </div></section>
      <section className="boss-catalog-filters" aria-label="服务分类与筛选">
        <div className="boss-catalog-filter-heading"><p>选择分类</p><Button variant="ghost" className="h-11" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><SlidersHorizontal size={16} />{expanded ? "收起筛选" : "更多筛选"}{expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</Button></div>
        <div className="boss-catalog-tabs">{tabs.map(item => <button key={item.value} type="button" aria-pressed={tab === item.value} onClick={() => { setTab(item.value); setKeyword(""); if (gameplayId) clearContext("gameplay") }}>{item.label}</button>)}</div>
        {expanded && <div className="boss-catalog-expanded">{[
          { key: "platform", title: "平台", value: platform, change: setPlatform, options: unique("platform", "platform_name") },
          { key: "level", title: "难度", value: level, change: setLevel, options: unique("security_level", "security_level_name") },
        ].map(group => <div key={group.key}><p>{group.title}</p><div>{[{ value: "", label: "不限" }, ...group.options].map(item => <button key={item.value} type="button" aria-pressed={group.value === item.value} onClick={() => group.change(String(item.value))}>{item.label}</button>)}</div></div>)}</div>}
      </section>
      {activeFilters.length > 0 && <section className="boss-catalog-active-filters" aria-label="当前筛选"><p>当前筛选 · 点击条件可取消</p><div>{activeFilters.map(item => <button key={item.key} type="button" onClick={item.clear} aria-label={`取消筛选：${item.label}`}>{item.label}<X size={14} /></button>)}</div></section>}
      <div className="boss-section-heading boss-catalog-results"><div><h2>{keyword ? "搜索结果" : "精选套餐"} {load.data && <small>{visible.length}</small>}</h2><p>价格与服务范围以详情为准</p></div><div className="boss-catalog-sort" role="group" aria-label="价格排序"><button type="button" aria-label="价格从低到高" aria-pressed={sort === "price"} onClick={() => setSort("price")}><ArrowDownUp size={14} />价格升序</button><button type="button" aria-label="价格从高到低" aria-pressed={sort === "price-desc"} onClick={() => setSort("price-desc")}>降序</button></div></div>
      <State {...load} empty={!visible.length}
        emptyTitle={hasLocalFilters ? "没有符合筛选条件的套餐" : hasScope ? "当前范围暂无可选套餐" : "暂无上架的服务套餐"}
        emptyDescription={hasLocalFilters ? "清除当前筛选后，可查看此范围内的其他套餐。" : hasScope ? "可以返回全部服务重新选择。" : "可以向本店客服询问可预约的服务。"}
        emptyAction={hasLocalFilters ? <Button variant="outline" className="h-11" onClick={resetFilters}>清除筛选条件</Button>
          : <Button variant="outline" className="h-11" asChild><Link to={hasScope ? "/catalog" : "/support"}>{hasScope ? "查看全部服务" : "联系本店客服"}</Link></Button>}>
        <div className="boss-catalog-list">{visible.map(item => <article className="boss-service-card" key={item.id}>
          <Link className="boss-service-card-main" aria-label={`查看${item.catalog_family || item.name}详情`} to={queryUrl("/service", { id: item.id, provider_id: providerId, coupon_id: couponId })}>
            {item.cover_url || item.gameplay_cover_url ? <Image src={item.cover_url || item.gameplay_cover_url} alt={item.catalog_family || item.name} className="boss-service-thumb" /> : <div className="boss-catalog-service-art" aria-hidden="true"><span /><span /><Gamepad2 size={32} strokeWidth={1.4} /><small>{store.name}</small></div>}
            <div className="boss-service-card-content"><div className="boss-service-card-title"><div><h3>{item.catalog_family || item.name}</h3><p>{item.description}</p></div><ArrowRight size={16} /></div><div className="boss-actions">{item.purchase_mode === "CONSULT" && providerLimit(item) !== 2 && <Badge variant="outline" className="boss-catalog-consult-badge">需客服确认</Badge>}{(item.platform_name || item.platform) && <Badge variant="secondary" title={String(item.platform_name || item.platform)}>{item.platform_name || item.platform}</Badge>}{item.game_category_name && <Badge variant="secondary" title={item.game_category_name}>{item.game_category_name}</Badge>}{item.variantsCount > 1 && <Badge variant="outline">{item.variantsCount} 个规格</Badge>}</div><span className="boss-price">{money(item.price)} <small>{coinText("浪花 / ")}{item.price_unit || "份"}{item.variantsCount > 1 ? " 起" : ""}</small></span></div>
          </Link>
        </article>)}</div>
      </State>
      </div>
    </>
  )
}

function ServiceDetailCard({ title, icon, children, wide = false, className = "" }: { title: string; icon?: ReactNode; children: ReactNode; wide?: boolean; className?: string }) {
  return <section className={`boss-service-info-card${wide ? " is-wide" : ""} ${className}`}><h2>{icon}{title}</h2>{children}</section>
}

function ServiceReviews({ serviceId }: { serviceId: number }) {
  const reviews = useLoad(() => api.get<Row>(`/orders/services/${serviceId}/evaluations/`), [serviceId])
  const items = rows(reviews.data?.list)
  const total = reviews.data?.total
  const average = reviews.data?.avg_score ?? reviews.data?.average_score
  return (
    <section className="boss-service-info-card is-wide boss-service-reviews" aria-labelledby="service-reviews-title" aria-busy={reviews.loading}>
      <div className="boss-service-card-heading">
        <div><h2 id="service-reviews-title" tabIndex={-1}>服务评价 <span>{total ?? "—"}</span></h2><p>本套餐已公开的订单评价，按时间倒序展示</p></div>
        {total > 0 && average != null && <div className="boss-service-review-average"><Star size={16} fill="currentColor" /><strong>{average}</strong><span>/ 5</span></div>}
      </div>
      {reviews.error && <button type="button" className="boss-service-review-error" onClick={reviews.reload} disabled={reviews.loading}><AlertCircle size={16} />{reviews.data ? "评价未能更新，保留上次结果。点击重试" : "评价暂未加载，点击重试"}</button>}
      {reviews.loading && !reviews.data && <div className="boss-skeleton-compact" role="status" aria-label="正在读取本套餐评价"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-4 w-2/3" /></div>}
      {items.length > 0 && <div className="boss-service-review-grid">{items.map(item => (
        <article key={item.id}>
          <div className="boss-service-review-author">{item.customer_avatar ? <Image src={item.customer_avatar} alt="" className="boss-avatar" /> : <span className="boss-service-review-avatar" aria-hidden="true"><CircleUserRound size={16} /></span>}<strong>{item.customer_name || "老板"}</strong><Badge variant="secondary">{item.score} 分</Badge></div>
          {item.provider_name && <p className="boss-service-review-provider">陪玩 · <ProviderName value={item.provider_name} /></p>}
          <p className="boss-service-review-copy">{item.content || "未填写文字评价"}</p>
          {item.reply_content && <p className="boss-service-review-reply"><strong>{item.reply_author === "PROVIDER" ? "陪玩" : "客服"}回复：</strong>{item.reply_content}</p>}
          <p className="boss-service-review-time">{date(item.created_at)}</p>
        </article>
      ))}</div>}
      {total === 0 && !reviews.error && <div className="boss-service-no-reviews"><Star size={22} /><p>该套餐暂无公开评价</p><span>下单前可查看上方的接单要求和服务规则。</span></div>}
    </section>
  )
}

export function ServicePage() {
  const session = useSession("boss")
  const query = useQuery(),
    navigate = useNavigate(),
    id = query.get("id"),
    providerId = query.get("provider_id"),
    couponId = query.get("coupon_id"),
    { busy, run } = useAction()
  const load = useLoad(async () => {
    if (!id) throw new Error("缺少服务编号，请从服务目录重新选择")
    const service = await api.get<any>(
      queryUrl(`/orders/services/${id}/`, { coupon_id: couponId })
    )
    const provider = providerId
      ? await api.get<any>(`/users/escorts/${providerId}/?service_item=${id}`)
      : null
    if (
      provider &&
      !(provider.eligible_service_item_ids || []).includes(service.id)
    )
      throw new Error(
        provider.order_unavailable_reason || "陪玩不再支持此套餐，请重新选择"
      )
    return { service, provider }
  }, [id, providerId, couponId, session.revision])
  const service = load.data?.service,
    consult =
      service?.is_addon ||
      (service?.purchase_mode === "CONSULT" && providerLimit(service) !== 2)
  const variants = rows(service?.variants).length
      ? rows(service?.variants)
      : service
        ? [service]
        : [],
    regular = variants.filter(
      (item) =>
        !item.is_addon &&
        !item.is_gift &&
        (!load.data?.provider ||
          (load.data.provider.eligible_service_item_ids || []).includes(
            item.id
          ))
    )
  function choose(value: string) {
    navigate(
      queryUrl("/service", {
        id: value,
        provider_id: providerId,
        coupon_id: couponId,
      }),
      { replace: true }
    )
  }
  function showReviews() {
    const title = document.getElementById("service-reviews-title")
    title?.focus({ preventScroll: true })
    title?.scrollIntoView({ block: "start" })
  }
  function toggleFavorite() {
    if (!session.token) {
      navigate("/login?next=" + encodeURIComponent(queryUrl("/service", { id, provider_id: providerId, coupon_id: couponId })))
      return
    }
    void run(async () => {
      const result = await api.post<{ favorited: boolean }>("/orders/favorites/toggle/", { service_id: service.id })
      if (typeof result?.favorited === "boolean") toast.success(result.favorited ? "已收藏该服务" : "已取消收藏该服务")
    }, "").catch(load.reload)
  }
  const addons = variants.filter(item => item.is_addon),
    highlights = rows(service?.highlights).map(item => String(item)),
    media = [...new Set([
      ...rows(service?.introduction_images),
      ...rows(service?.images),
      ...rows(service?.details_images),
      ...rows(service?.rules_images),
    ])],
    actionDisabled = busy || load.loading || !!load.error,
    checkoutPath = queryUrl("/checkout", { service_id: service?.id, provider_id: providerId, coupon_id: couponId }),
    pickerPath = queryUrl("/companion-picker", { service_id: service?.id, coupon_id: couponId, selected_provider_ids: providerId })
  return (
    <div className="boss-service-fidelity-page">
      <div className="boss-service-detail-back"><BackLink to="/catalog">服务目录</BackLink><Badge variant="outline">{service?.game_category_name || "服务详情"}</Badge></div>
      {couponId && <div className="boss-service-coupon-context"><Ticket size={16} /><p>当前展示优惠券适用套餐</p><Button asChild variant="ghost" className="h-11"><Link to={queryUrl("/service", { id, provider_id: providerId })}>查看完整套餐</Link></Button></div>}
      <State {...load}>
        {service && <>
          {load.data?.provider && <section className="boss-catalog-context boss-service-provider-context"><span className="boss-catalog-context-icon"><Headphones size={20} /></span><div><h2>已指定 <ProviderName value={load.data.provider.nickname} /></h2><p>确认服务时将保留这位陪玩</p></div></section>}
          <section className="boss-service-hero-grid" aria-labelledby="service-detail-title">
            <div className="boss-service-fidelity-hero">
              <span className="boss-service-hero-orbit first" aria-hidden="true" /><span className="boss-service-hero-orbit second" aria-hidden="true" />
              <div className="boss-service-hero-content">
                <div className="boss-service-hero-heading"><div><p>{service.game_category_name}</p><span>{service.gameplay_name}</span></div><span className="boss-service-hero-icon"><Gamepad2 size={24} /></span></div>
                <h1 id="service-detail-title">{service.catalog_family || service.name}</h1>
                {service.description && <p className="boss-service-hero-description">{service.description}</p>}
                <div className="boss-service-hero-price"><p>{money(service.price)}<span>{coinUnit()} / {service.price_unit || "份"}</span></p><Badge variant="outline">{[service.platform_name, service.security_level_name].filter(Boolean).join(" · ") || "以套餐要求为准"}</Badge></div>
              </div>
            </div>
            <div className="boss-service-fidelity-facts">
              <div><span className="boss-service-fact-icon"><ShieldCheck size={20} /></span><div><p>接单要求</p><strong>{service.provider_requirement?.label || service.required_level_name || "以套餐资格为准"}</strong></div></div>
              <div><span className="boss-service-fact-icon"><PackageCheck size={20} /></span><div><p>服务记录</p><strong>已完成 <span>{service.sales_count == null ? "—" : service.sales_count}</span> 单</strong><Button type="button" variant="link" className="min-h-11 px-0" onClick={showReviews}>查看本套餐评价</Button></div></div>
            </div>
          </section>
          <div className="boss-service-information-grid">
            {regular.length > 1 && <section className="boss-service-info-card is-wide boss-service-variants" aria-labelledby="service-variants-title">
              <div className="boss-service-card-heading"><div><h2 id="service-variants-title">选择套餐</h2><p>切换档位后同步核对价格</p></div><Badge variant="secondary">{regular.length} 个档位</Badge></div>
              <div className="boss-service-fidelity-variants">{regular.map(item => <button type="button" key={item.id} aria-pressed={item.id === service.id} disabled={busy} onClick={() => choose(String(item.id))}><span>{[item.platform_name, item.security_level_name, item.catalog_variant || item.name].filter(Boolean).join(" · ")}</span><strong>{money(item.price)}<small>{coinUnit()} / {item.price_unit || "份"}</small></strong></button>)}</div>
            </section>}
            <ServiceDetailCard title="服务亮点" icon={<Sparkles size={16} />}>
              {highlights.length ? <div className="boss-service-highlights">{highlights.map((item, index) => <p key={index}><Check size={16} /><span>{item}</span></p>)}</div> : <p className="boss-service-card-copy">服务亮点待补充，具体内容可查看服务规则或联系客服确认。</p>}
            </ServiceDetailCard>
            {consult && <ServiceDetailCard title="下单前确认" icon={<MessageCircleMore size={16} />} className="boss-service-consult-card"><p className="boss-service-card-copy">{service.purchase_note || "该服务需由客服确认目标、陪玩安排与最终方案，请先联系客服核对。"}</p></ServiceDetailCard>}
            <ServiceDetailCard title="附加项目" icon={<PackageCheck size={16} />}>
              <p className="boss-service-card-note">需关联主套餐，由客服确认办理</p>
              {addons.length ? <div className="boss-service-addons">{addons.map(item => <Link key={item.id} to={queryUrl("/support", { service_id: item.id })}><span>{item.catalog_variant || item.name}</span><strong>{money(item.price)} {coinUnit()} / {item.price_unit || "份"}</strong><ChevronRight size={16} /></Link>)}</div> : <div className="boss-service-addons-empty"><PackageCheck size={18} /><p>当前套餐暂无附加项目</p></div>}
            </ServiceDetailCard>
            <ServiceDetailCard title="服务规则" icon={<ShieldCheck size={16} />}>
              <p className="boss-service-card-copy">{service.rules || service.purchase_note || "下单前可联系客服核对服务内容及时间安排。"}</p>
              {providerLimit(service) === 2 && <p className="boss-service-card-note">双陪套餐 · 选择两位陪玩，套餐总价不变。</p>}
              {rows(service.roulette_pool).length > 0 && <div className="boss-service-roulette-pool">{rows(service.roulette_pool).map(item => <div key={item.name}><div><strong>{item.name}</strong>{item.description && <p>{item.description}</p>}</div><span>{item.probability}%</span></div>)}</div>}
            </ServiceDetailCard>
            {!consult && !service.is_gift && <ServiceDetailCard title="服务如何开始" wide>
              <div className="boss-service-start-steps">{[
                { number: "01", title: "填写游戏资料", detail: "选择平台派单或指定陪玩", icon: Gamepad2 },
                { number: "02", title: "核对订单", detail: `确认优惠、${coinUnit()}余额与支付信息`, icon: WalletCards },
                { number: "03", title: "等待服务开始", detail: "接单与履约进度在订单中查看", icon: Clock3 },
              ].map(item => <div key={item.number}><span><item.icon size={16} /><small>{item.number}</small></span><div><h3>{item.title}</h3><p>{item.detail}</p></div></div>)}</div>
            </ServiceDetailCard>}
            {service.details_html && <ServiceDetailCard title="玩法详情与价目" icon={<Gamepad2 size={16} />} wide><div className="boss-service-rich-content"><RichContent html={service.details_html} /></div></ServiceDetailCard>}
            {(service.cover_url || service.gameplay_cover_url || media.length > 0) && <ServiceDetailCard title="服务图文" icon={<Gamepad2 size={16} />} wide><div className="boss-service-media">{[...new Set([service.cover_url || service.gameplay_cover_url, ...media].filter(Boolean))].map((url: any) => <Image key={url} src={url} alt={service.name + "说明"} className="boss-banner" />)}</div></ServiceDetailCard>}
            <ServiceReviews key={service.id} serviceId={service.id} />
          </div>
          <div className="boss-service-fidelity-actionbar" aria-label="服务价格与下单操作"><div>
            <div className="boss-service-action-summary"><p>{service.catalog_variant || service.name}{service.platform_name ? ` · ${service.platform_name}` : ""}</p><strong>{money(service.price)}<span>{coinUnit()} / {service.price_unit || "份"}</span></strong></div>
            <div className={`boss-service-action-buttons${consult || service.is_gift ? " is-consult" : ""}`}>
              <Button variant="ghost" className="h-12 boss-service-favorite" disabled={actionDisabled} aria-label={service.is_favorited ? "取消收藏" : "收藏服务"} aria-pressed={!!service.is_favorited} onClick={toggleFavorite}>
                <Heart size={16} fill={service.is_favorited ? "currentColor" : "none"} />{service.is_favorited ? "已收藏" : "收藏"}
              </Button>
              {service.is_gift ? <Button asChild className="h-12"><Link to="/gifts">前往赠礼</Link></Button> : consult ? <Button asChild className="h-12"><Link to="/support">确认方案</Link></Button> : <>
                <Button asChild variant="outline" className="h-12" disabled={actionDisabled}><Link to={pickerPath} aria-disabled={actionDisabled || undefined} tabIndex={actionDisabled ? -1 : undefined} onClick={event => { if (actionDisabled) event.preventDefault() }}>挑选陪玩</Link></Button>
                <Button asChild className="h-12" disabled={actionDisabled}><Link to={checkoutPath} aria-disabled={actionDisabled || undefined} tabIndex={actionDisabled ? -1 : undefined} onClick={event => { if (actionDisabled) event.preventDefault() }}>确认服务</Link></Button>
              </>}
            </div>
          </div></div>
        </>}
      </State>
    </div>
  )
}

export function CompanionsPage({ picker = false }: { picker?: boolean }) {
  const store = useCurrentStore()
  const query = useQuery(),
    navigate = useNavigate(),
    serviceId = query.get("service_id"),
    couponId = query.get("coupon_id"),
    [selected, setSelected] = useState<number[]>(() =>
      providerIds(
        query.get("selected_provider_ids") ||
          query.get("provider_ids") ||
          query.get("provider_id")
      )
    ),
    [keyword, setKeyword] = useState(""),
    [gender, setGender] = useState(query.get("provider_gender") === "ANY" ? "" : query.get("provider_gender") || ""),
    [activeVoiceId, setActiveVoiceId] = useState<number | null>(null),
    [category, setCategory] = useState(""),
    [voiceOnly, setVoiceOnly] = useState(false),
    [sort, setSort] = useState(""),
    [scope, setScope] = useState<Row>({
      provider_kind: "",
      platform: "",
      security_level: "",
      grade: "",
    }),
    { busy, run } = useAction()
  const load = useLoad(async () => {
    if (picker && !serviceId)
      throw new Error("缺少服务编号，请从服务详情重新挑选陪玩")
    const [items, categories, service, meta] = await Promise.all([
      api.get<any>(
        queryUrl("/users/escorts/", {
          ...(serviceId
            ? { service_item: serviceId, ...(picker ? { picker: 1 } : {}) }
            : { directory: 1, game_category: category, ...scope }),
          gender,
        })
      ),
      api.get<any>("/orders/game-categories/"),
      serviceId
        ? api.get<any>(`/orders/services/${serviceId}/`)
        : Promise.resolve(null),
      serviceId
        ? Promise.resolve(null)
        : api.get<any>("/users/qualification-options/"),
    ])
    return { items: rows(items), categories: rows(categories), service, meta }
  }, [serviceId, picker, gender, category, JSON.stringify(scope)])
  const contextGender = query.get("provider_gender")
  useEffect(() => { setGender(contextGender === "ANY" ? "" : contextGender || "") }, [contextGender])
  const [directoryMetadata, setDirectoryMetadata] = useState<{ categories: Row[]; meta: Row | null }>({ categories: [], meta: null })
  useEffect(() => { if (load.data && !serviceId) setDirectoryMetadata({ categories: load.data.categories, meta: load.data.meta }) }, [load.data, serviceId])
  const limit = providerLimit(load.data?.service),
    visible = (load.data?.items || [])
      .filter(
        (item) =>
          (!voiceOnly || item.voice_card_url) &&
          (!keyword.trim() ||
            [item.nickname, formatProviderName(item.nickname, store.provider_name_prefix), item.rank, item.bio, item.city]
              .join(" ")
              .toLowerCase()
              .includes(keyword.trim().toLowerCase()))
      )
      .sort((a, b) =>
        sort === "asc"
          ? Number(a.pricePerHour || Infinity) -
            Number(b.pricePerHour || Infinity)
          : sort === "desc"
            ? Number(b.pricePerHour || 0) - Number(a.pricePerHour || 0)
            : 0
      )
  function toggle(item: Row) {
    if (selected.includes(item.id))
      setSelected((values) => values.filter((id) => id !== item.id))
    else if (!item.can_order)
      toast.error(item.order_unavailable_reason || "该陪玩当前不可接单")
    else if (limit === 1) setSelected([item.id])
    else if (selected.length < limit)
      setSelected((values) => [...values, item.id])
    else toast.error("请先取消一位，再选择新的陪玩")
  }
  async function confirmSelection() {
    const [service, providers] = await Promise.all([
      api.get<any>(`/orders/services/${serviceId}/`),
      Promise.all(
        selected.map((id) =>
          api.get<any>(
            queryUrl(`/users/escorts/${id}/`, {
              service_item: serviceId,
              gender: gender || "ANY",
            })
          )
        )
      ),
    ])
    if (selected.length && selected.length !== providerLimit(service))
      throw new Error(`请选齐 ${providerLimit(service)} 位陪玩，或取消全部选择`)
    if (providers.some((provider) => !provider.can_order))
      throw new Error("所选陪玩接单状态已变化，请重新选择")
    navigate(
      queryUrl("/checkout", {
        service_id: serviceId,
        provider_ids: selected.join(","),
        provider_gender: gender,
        coupon_id: couponId,
      })
    )
  }
  const loadedProviders = load.data?.items || []
  const invalidSelected = load.data ? selected.filter(id => !loadedProviders.some(item => item.id === id && item.can_order)) : []
  const meta = load.data?.meta || directoryMetadata.meta
  const Filter = picker ? Choice : DirectoryFilter
  const Results = picker ? State : DirectoryState
  const hasFilters = Boolean(keyword || gender || category || voiceOnly || sort || Object.values(scope).some(Boolean))
  function resetFilters() { setKeyword(""); setGender(""); setCategory(""); setSort(""); setVoiceOnly(false); setScope({ provider_kind: "", platform: "", security_level: "", grade: "" }) }
  const detailTarget = (item: Row) => queryUrl("/companion-detail", { id: item.id, service_id: serviceId, coupon_id: couponId, provider_gender: gender, from_picker: picker ? 1 : undefined, selected_provider_ids: picker ? selected.join(",") : undefined })
  return (
    <>
      <Title title={picker ? "挑选本次陪玩" : "陪玩"} description={picker ? "为当前服务核对接单资格与档期" : "先听听声音，再决定和谁组队"} support={!picker} />
      <div className="boss-companion-search"><SearchField value={keyword} onChange={setKeyword} placeholder="搜索昵称、段位或城市" label="搜索陪玩" /></div>
      {picker && load.data && <section className="boss-picker-service"><p>为这项服务挑选陪玩</p><h2>{load.data?.service?.name || "指定套餐"}</h2><p>可选择 {limit} 位，未指定时由平台安排</p><div className="boss-actions">
        {selected.map(id => <Button variant="outline" key={id} onClick={() => setSelected(values => values.filter(value => value !== id))}><ProviderName value={load.data?.items.find(item => item.id === id)?.nickname || `陪玩 #${id}`} /> · 移除</Button>)}

      </div></section>}
      {picker && invalidSelected.length > 0 && <div className="boss-feedback error" role="alert">部分已选陪玩的接单状态已变化，请取消失效选择后重新挑选。</div>}
      {!picker && serviceId && load.data?.service && <section className="boss-directory-service-context"><p>为这项服务挑选陪玩</p><h2>{load.data.service.name}</h2><p>{load.data.service.provider_requirement?.label || "接单资格与档期以实时状态为准"}</p><Button type="button" variant="outline" className="mt-3 h-11 w-full" asChild><Link to={queryUrl("/checkout", { service_id: serviceId, coupon_id: couponId, provider_gender: gender })}>交给平台派单</Link></Button></section>}
      {!serviceId && <section className="boss-game-filter" aria-label="游戏筛选"><div className="boss-horizontal-options">{[{ value: "", label: "全部" }, ...(load.data?.categories || directoryMetadata.categories).map(item => ({ value: String(item.id), label: item.name }))].map(item => <button key={item.value} type="button" aria-pressed={category === item.value} onClick={() => setCategory(item.value)}>{item.value ? <Gamepad2 size={16} /> : <Sparkles size={16} />}{item.label}</button>)}</div></section>}
      <section className="boss-companion-filters" aria-label="陪玩条件筛选">
        {!serviceId && <>
          <Filter disabled={!meta} label="服务方向" value={scope.provider_kind} onChange={value => setScope({ ...scope, provider_kind: value, grade: "" })} options={[{ value: "", label: "全部方向" }, ...rows(meta?.kinds)]} />
          <Filter disabled={!meta} label="平台" value={scope.platform} onChange={value => setScope({ ...scope, platform: value, grade: "" })} options={[{ value: "", label: "全部平台" }, ...rows(meta?.platforms)]} />
          <Filter disabled={!meta} label="难度" value={scope.security_level} onChange={value => setScope({ ...scope, security_level: value, grade: "" })} options={[{ value: "", label: "不限难度" }, { value: "CONFIDENTIAL", label: "机密" }, { value: "TOP_SECRET", label: "绝密" }, { value: "ANY", label: "不限" }]} />
          <Filter disabled={!meta} label="最低档位" value={scope.grade} onChange={value => setScope({ ...scope, grade: value })} options={[{ value: "", label: "不限档位" }, ...rows(meta?.scopes).filter(item => item.provider_kind === scope.provider_kind && item.platform === scope.platform && item.security_level === scope.security_level).flatMap(item => rows(item.grades))]} />
        </>}
        <Filter label="性别" value={gender} onChange={setGender} options={[{ value: "", label: "不限性别" }, { value: "MALE", label: "男陪" }, { value: "FEMALE", label: "女陪" }]} />
        <Filter label="参考时价排序" value={sort} onChange={setSort} options={[{ value: "", label: "默认排序" }, { value: "asc", label: "从低到高" }, { value: "desc", label: "从高到低" }]} />
      </section>
      <section className="boss-companion-switches" aria-label="语音与筛选重置"><Button variant="outline" aria-pressed={voiceOnly} onClick={() => setVoiceOnly(!voiceOnly)}><AudioLines size={16} />只看有语音<span className={voiceOnly ? "boss-check selected" : "boss-check"}>{voiceOnly && <Check size={12} />}</span></Button><Button variant="outline" disabled={!hasFilters} onClick={resetFilters}><RotateCcw size={16} />重置筛选</Button></section>
      <div className="boss-section-heading boss-companion-results"><div><h2>{picker && load.data ? `已选 ${selected.length} / ${limit} 位` : "陪玩列表"}</h2><p>先了解，再决定和谁组队</p></div>{load.data && <Badge variant="secondary">{visible.length} 位符合条件</Badge>}</div>
      <Results {...load} empty={!visible.length} emptyAction={<Button type="button" variant="outline" className="mt-5 h-11 w-full" onClick={resetFilters}>重置筛选</Button>}>
        <div className={picker ? "boss-companion-list boss-picker-list" : "boss-companion-list"}>{visible.map(item => picker && serviceId ? <ProviderPickerCard key={item.id} item={item} selected={selected.includes(item.id)} disabled={busy} storePrefix={store.provider_name_prefix} onToggle={() => toggle(item)} detailTo={detailTarget(item)} /> : <DirectoryCard key={item.id} item={item} to={detailTarget(item)} activeVoiceId={activeVoiceId} onVoiceChange={setActiveVoiceId} />)}</div>
      </Results>
      {picker && load.data && <div className="boss-picker-actionbar"><div><strong>已选 {selected.length} / {limit} 位</strong><p>{invalidSelected.length ? "部分选择暂不可约，请先调整" : selected.length ? "确认前重新核对接单资格" : "也可以交给平台安排"}</p></div><Button disabled={busy || load.loading || !!load.error || (selected.length > 0 && selected.length !== limit)} onClick={() => void run(confirmSelection, "").catch(() => load.reload())}>{busy ? "正在核对资格…" : selected.length ? "确认选择并下单" : "不指定陪玩，继续"}</Button></div>}
    </>
  )
}

export function CompanionPage() {
  const store = useCurrentStore()
  const query = useQuery(),
    id = query.get("id"),
    serviceId = query.get("service_id"),
    gender = query.get("provider_gender") || "ANY",
    load = useLoad(async () => {
      if (!id) throw new Error("缺少陪玩编号，请返回列表重新选择")
      const [provider, service] = await Promise.all([
        api.get<any>(
          queryUrl(`/users/escorts/${id}/`, {
            service_item: serviceId,
            gender,
          })
        ),
        serviceId
          ? api.get<any>(`/orders/services/${serviceId}/`)
          : Promise.resolve(null),
      ])
      return { provider, service }
    }, [id, serviceId, gender])
  const provider = load.data?.provider,
    service = load.data?.service,
    fromPicker = !!serviceId && query.get("from_picker") === "1",
    selected = providerIds(query.get("selected_provider_ids")),
    alreadySelected = !!provider && selected.includes(provider.id),
    full =
      fromPicker &&
      !alreadySelected &&
      selected.length >= providerLimit(service),
    pickerTarget = queryUrl("/companion-picker", {
      service_id: serviceId,
      coupon_id: query.get("coupon_id"),
      provider_gender: gender,
      selected_provider_ids: selected.join(","),
    }),
    selectionTarget = fromPicker
      ? queryUrl("/companion-picker", {
          service_id: serviceId,
          coupon_id: query.get("coupon_id"),
          provider_gender: gender,
          selected_provider_ids: (alreadySelected || !provider
            ? selected
            : providerLimit(service) === 1
              ? [provider.id]
              : [...selected, provider.id]
          ).join(","),
        })
      : serviceId
        ? queryUrl(
            providerLimit(service) === 2 ? "/companion-picker" : "/checkout",
            {
              service_id: serviceId,
              selected_provider_ids: provider?.id,
              provider_id: provider?.id,
              provider_gender: gender,
              coupon_id: query.get("coupon_id"),
            }
          )
        : "/catalog?provider_id=" + provider?.id
  return (
    <>
      <div className="boss-companion-detail-top"><BackLink to={fromPicker ? pickerTarget : "/companions"}>陪玩列表</BackLink><Badge variant="outline">陪玩详情</Badge></div>
      <State {...load}>
        {provider && <div className="boss-companion-detail">
          <Panel className="boss-companion-profile">
            <div className="boss-companion-portrait"><Image src={provider.avatar} alt={formatProviderName(provider.nickname, store.provider_name_prefix)} /><span className={provider.can_order ? "available" : ""} aria-label={provider.can_order ? "当前可约" : "暂不可约"} /></div>
            <div className="boss-companion-profile-name"><h1><ProviderName value={provider.nickname} /></h1><Badge variant="outline" className={provider.can_order ? "boss-available" : undefined}>{provider.can_order ? "当前可约" : "暂不可约"}</Badge></div>
            <p>{[provider.level_name, provider.gender === "MALE" ? "男陪" : provider.gender === "FEMALE" ? "女陪" : ""].filter(Boolean).join(" · ")}</p>
            <div className="boss-companion-profile-location">{provider.city && <span><MapPin size={14} />{provider.city}</span>}{provider.rank && <span><ShieldCheck size={14} />{provider.rank}</span>}</div>
            <div className="boss-companion-specialties"><p>擅长服务</p><div className="boss-actions">{(provider.game_category_names || []).map((name: string) => <Badge variant="secondary" key={name}><Gamepad2 size={12} />{name}</Badge>)}</div></div>
            {!provider.can_order && <div className="boss-companion-unavailable" role="status">{provider.order_unavailable_reason || "当前暂时无法接新订单，请稍后再约。"}</div>}
          </Panel>
            <Panel className="boss-companion-voice"><div className="boss-companion-section-heading"><span><AudioLines size={20} /></span><div><h2><ProviderName value={provider.nickname} />的语音房间</h2><p>试听声音，了解 TA 的风格</p></div></div>{provider.voice_card_url ? <audio controls preload="none" src={provider.voice_card_url} aria-label={`${formatProviderName(provider.nickname, store.provider_name_prefix)}的声音`} /> : <p className="boss-muted">TA 还没有上传声音名片</p>}</Panel>
          <Panel className="boss-companion-price-card">
            {service && <div className="boss-companion-current-service"><div><p>当前选择的服务</p><h2>{service.name}</h2></div><Badge variant="secondary">{providerLimit(service) === 2 ? "多人服务" : "单人服务"}</Badge></div>}
            <div className="boss-companion-reference-price"><div><p>{service ? "当前服务价格" : "参考时价"}</p><small>{service ? "下单前再次确认服务与价格" : "具体服务价格以选择为准"}</small></div><strong>{service ? <>{money(service.price)}<small>{coinText("浪花 / ")}{service.price_unit}</small></> : provider.hasReferenceHourlyPrice ? <>{money(provider.pricePerHour)}<small>{coinText("浪花 / 小时")}</small></> : <small>选择服务后确认</small>}</strong></div>
          </Panel>
          <Panel className="boss-companion-about"><div className="boss-companion-section-heading"><Sparkles size={16} /><h2>关于 TA</h2></div><p>{provider.bio || "TA 还没有填写个人介绍"}</p></Panel>
          <Panel className="boss-companion-work"><div className="boss-companion-section-heading"><BadgeCheck size={16} /><h2>接单信息</h2></div><div className="boss-companion-work-amount"><div><p>本月接单额</p><strong>{money(provider.monthly_order_amount)}<small>{coinUnit()}</small></strong></div><div><PendingFeature label="擅长位置">常用游戏位置尚未开放展示。擅长游戏与接单资格已按真实资料显示，可在下单前与客服沟通位置要求。</PendingFeature></div></div><div className="boss-companion-qualifications">{rows(provider.qualifications).filter(item => item.is_active).map(item => <p key={item.id}><Check size={14} />{item.label}</p>)}</div></Panel>
          <Panel className="boss-companion-exclusives"><div className="boss-companion-section-heading"><Star size={16} /><h2>专属标签</h2></div><div className="boss-actions">{rows(provider.display_tags).length ? rows(provider.display_tags).map(item => <Badge variant="secondary" key={item.id || item.key}>{item.title}</Badge>) : <p className="boss-muted">暂无专属标签</p>}</div><div className="boss-companion-namings"><div className="boss-companion-section-heading"><Gift size={16} /><h2>专属冠名</h2></div>{rows(provider.namings).length ? rows(provider.namings).map(item => <p key={item.id}><strong>{item.title}</strong><span>有效至 {date(item.expires_at)}</span></p>) : <p className="boss-muted">暂无专属冠名</p>}</div></Panel>
          {serviceId && providerLimit(service) === 1 && <Panel className="boss-companion-reserve"><div className="boss-companion-section-heading"><CalendarClock size={16} /><h2>先约时间</h2></div><p>提交后等待陪玩确认；成单时再确认价格。</p><LinkButton variant="outline" disabled={load.loading || !!load.error || !provider.can_order} to={queryUrl("/reservation-create", { service_id: serviceId, provider_id: provider.id })}>预约 TA<ChevronRight size={16} /></LinkButton></Panel>}
          {full && <p className="boss-companion-unavailable" role="status">已选齐 {providerLimit(service)} 位陪玩，请返回列表取消一位后更换。</p>}
          <div className="boss-companion-actionbar"><Button asChild variant="outline"><Link to={"/gifts?provider_id=" + provider.id}><Gift size={16} />送礼物</Link></Button><LinkButton variant={fromPicker && alreadySelected ? "outline" : "default"} disabled={(!(fromPicker && alreadySelected) && !provider.can_order) || full || load.loading || !!load.error} to={selectionTarget}>{fromPicker && alreadySelected ? "返回挑选列表" : fromPicker ? "选择 TA" : "选择 TA 的服务"}</LinkButton>{fromPicker && !alreadySelected && <Button asChild variant="ghost"><Link to={pickerTarget}>返回挑选列表</Link></Button>}</div>
        </div>}
      </State>
    </>
  )
}
