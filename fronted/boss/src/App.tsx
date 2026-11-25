import { SkipLink, storeBrandLogo, syncThemeColor, useCurrentStore } from "@xa/shared"
import { useCallback, useEffect, useRef, useState } from "react"
import { flushSync } from "react-dom"
import {
  Link,
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router"
import { Headphones, Home, MessageSquare, Moon, ClipboardList, Sun, UserRound } from "lucide-react"
import { useRealtime, useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"
import LoginPage from "./business/login"
import {
  HomePage,
  MinePage,
  ProfilePage,
  SecurityPage,
  WalletPage,
} from "./business/account"
import {
  CatalogPage,
  ServicePage,
  CompanionsPage,
  CompanionPage,
  CheckoutPage,
  OrdersPage,
  OrderPage,
  GiftsPage,
  ReservationsPage,
  ReservationPage,
  ReservationCreatePage,
} from "./business/commerce"
import {
  MembershipPage,
  CheckinPage,
  AchievementsPage,
  RankingsPage,
  CouponsPage,
  FavoritesPage,
  PointsPage,
  ClaimPage,
  LotteryPage,
} from "./business/community"
import {
  MessagesPage,
  MessagePage,
  ChatPage,
  SupportPage,
  ContentPage,
  AnnouncementsPage,
  AnnouncementPage,
  AuditionPage,
} from "./business/content"
import { ActionFeedback, GuestDataBoundary, notifyChange } from "./business/ui"
import "./App.css"
import "./prototype.css"
import "./surfaces.css"
import "./layout.css"
import "./companions-fidelity.css"
import "./order-detail-fidelity.css"
import "./service-catalog-fidelity.css"
import "./checkin-reservations-fidelity.css"
import "./business/points-gifts-fidelity.css"
import "./prototype-latest.css"
import { BossModuleNotice, H5OrderGate, useBossBrand } from "./business/h5-module"
import { StoreClosureCard } from "./business/closure-card"

const navigation = [
  { path: "/home", title: "首页", icon: Home },
  { path: "/companions", title: "陪玩", icon: Headphones },
  { path: "/orders", title: "订单", icon: ClipboardList },
  { path: "/messages", title: "消息", icon: MessageSquare },
  { path: "/mine", title: "我的", icon: UserRound },
]
const legacyRoutes: Record<string, string> = {
  gameplay: "catalog",
  category: "catalog",
  productDetail: "service",
  serviceCard: "support",
  orderList: "orders",
  messageDetail: "message-detail",
  players: "companions",
  "customer/wallet": "wallet",
  "customer/checkin": "checkin",
  announcement: "announcements",
  announcementDetail: "announcement-detail",
  coupon: "coupons",
  "coupon/mine": "coupons",
  favorite: "favorites",
  settings: "profile",
}
const canonicalPages = [
  "home",
  "catalog",
  "orders",
  "messages",
  "mine",
  "login",
  "service",
  "checkout",
  "order-detail",
  "gifts",
  "points",
  "reward-claim",
  "lottery",
  "membership",
  "club-content",
  "companions",
  "wallet",
  "profile",
  "security",
  "achievements",
  "rankings",
  "message-detail",
  "announcements",
  "announcement-detail",
  "coupons",
  "favorites",
  "checkin",
  "audition",
  "support",
  "chat",
  "companion-picker",
  "companion-detail",
  "reservations",
  "reservation-detail",
  "reservation-create",
]
function LegacyRoute({ name, target }: { name: string; target: string }) {
  const location = useLocation(),
    query = new URLSearchParams(location.search)
  const positive = (value: string | null) =>
    /^[1-9]\d*$/.test(value || "") ? value : ""
  let destination = target
  const id = positive(query.get("id") || query.get("orderId"))
  if (name === "orderList" && id) {
    destination = "order-detail"
    query.set("id", id)
  }
  if (name === "gameplay" && id) query.set("gameplay", id)
  if (
    name === "players" &&
    positive(query.get("serviceId") || query.get("id"))
  ) {
    destination = "companion-picker"
    query.set(
      "service_id",
      String(positive(query.get("serviceId") || query.get("id")))
    )
  }
  if (name === "coupon/mine") query.set("tab", "mine")
  if (name === "serviceCard") {
    const orderId = positive(query.get("orderId") || query.get("order_id")),
      cardId = positive(query.get("cardId") || query.get("card_id"))
    if (orderId) query.set("order_id", orderId)
    if (cardId) query.set("card_id", cardId)
  }
  return (
    <Navigate
      to={"/" + destination + (query.size ? "?" + query : "")}
      replace
    />
  )
}
function Private({ children }: { children: React.ReactNode }) {
  const session = useSession("boss")
  return session?.token ? children : <GuestDataBoundary>{children}</GuestDataBoundary>
}
export default function App() {
  return (
    <HashRouter>
      <BossApp />
    </HashRouter>
  )
}
function BossApp() {
  const store = useCurrentStore()
  const brand = useBossBrand()
  const session = useSession("boss"),
    location = useLocation(),
    [dark, setDark] = useState(() => {
      try {
        return localStorage.getItem("xa:boss:web:theme") === "dark"
      } catch {
        return false
      }
    })
  const changed = useCallback(() => notifyChange(), [])
  useRealtime("boss", changed, !!session?.token)
  const transitioning = useRef(false)
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    try {
      localStorage.setItem("xa:boss:web:theme", dark ? "dark" : "light")
    } catch {
      /* Theme choice remains available in this tab. */
    }
    return syncThemeColor()
  }, [dark])
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [location.pathname])
  const path = location.pathname
  const ContentTag = path === "/login" ? "div" : "main"
  const section = ["/home", "/catalog", "/service", "/rankings"].includes(path)
    ? "/home"
    : ["/companions", "/companion-picker", "/companion-detail"].includes(path)
      ? "/companions"
      : ["/orders", "/order-detail", "/checkout"].includes(path)
        ? "/orders"
        : ["/messages", "/message-detail", "/chat"].includes(path)
          ? "/messages"
          : path === "/club-content" && new URLSearchParams(location.search).get("kind") === "HOT"
            ? "/home"
            : "/mine"
  const routeMotion = (event: React.MouseEvent<HTMLDivElement>) => {
    if (transitioning.current || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const anchor = (event.target as Element).closest<HTMLAnchorElement>("a[href]")
    if (!anchor || anchor.target && anchor.target !== "_self" || anchor.hasAttribute("download") || anchor.getAttribute("aria-disabled") === "true") return
    const href = anchor.getAttribute("href") || ""
    if (!href.startsWith("#/") || href === `#${location.pathname}${location.search}` || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return
    const start = (document as Document & { startViewTransition?: (update: () => void) => void }).startViewTransition
    if (!start) return
    const targetPath = href.slice(1).split("?")[0]
    const currentIndex = navigation.findIndex(item => item.path === section)
    const targetIndex = navigation.findIndex(item => item.path === targetPath)
    const backwards = currentIndex >= 0 && targetIndex >= 0 && targetIndex < currentIndex
    document.documentElement.style.setProperty("--boss-page-shift", backwards ? "-12px" : "12px")
    event.preventDefault()
    event.stopPropagation()
    start.call(document, () => {
      transitioning.current = true
      try {
        flushSync(() => anchor.click())
      } finally {
        transitioning.current = false
      }
    })
  }
  const cardMotion = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return
    const card = (event.target as Element).closest<HTMLElement>(".boss-home-gameplay-card, .boss-service-card, .boss-companion-card, .boss-order-card")
    if (!card) return
    const rect = card.getBoundingClientRect()
    card.style.setProperty("--card-pointer-x", `${Math.min(100, Math.max(0, (event.clientX - rect.left) / rect.width * 100))}%`)
    card.style.setProperty("--card-pointer-y", `${Math.min(100, Math.max(0, (event.clientY - rect.top) / rect.height * 100))}%`)
  }
  const cardMotionOut = (event: React.PointerEvent<HTMLDivElement>) => {
    const card = (event.target as Element).closest<HTMLElement>(".boss-home-gameplay-card, .boss-service-card, .boss-companion-card, .boss-order-card")
    if (!card || card.contains(event.relatedTarget as Node | null)) return
    card.style.setProperty("--card-pointer-x", "50%")
    card.style.setProperty("--card-pointer-y", "50%")
  }
  const guarded = (page: React.ReactNode) => <Private>{page}</Private>
  return (
    <div className="boss-app" style={brand?.style} data-h5-density={brand?.density} onClickCapture={routeMotion} onPointerMove={cardMotion} onPointerOut={cardMotionOut}>
      <SkipLink />
      <div className="boss-workspace">
        <header className="boss-topbar">
          <div className="boss-header-inner">
          <Link to="/login" className="account-login-brand boss-header-brand" aria-label={`${store.name}老板端登录`}>
            <span className="account-login-logo-frame"><img src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" width={32} height={32} className="account-login-brand-mark size-8" /></span>
            <span><strong title={brand?.display_name || store.name}>{brand?.display_name || store.name}</strong><small>服务中心</small></span>
          </Link>
          {location.pathname !== "/login" && <nav className="boss-header-nav" aria-label="老板端桌面导航">
            {navigation.map(item => <Link key={item.path} to={item.path} aria-current={section === item.path ? (path === item.path ? "page" : "location") : undefined} className={section === item.path ? "active" : ""}><item.icon size={16} />{item.title}</Link>)}
          </nav>}
          <div className="boss-topbar-actions">
            {brand && <Button asChild variant="ghost" className="boss-custom-support"><Link to="/support">{brand.support_label}</Link></Button>}
            <Button
              variant="ghost"
              size="icon"
              className="account-login-theme-toggle boss-theme-toggle size-11 border-transparent bg-transparent shadow-none hover:bg-transparent active:bg-transparent dark:hover:bg-transparent"
              aria-label={dark ? "切换浅色" : "切换深色"}
              onClick={() => setDark(!dark)}
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </Button>
          </div>
          </div>
        </header>
        <ContentTag id={path === "/login" ? undefined : "main-content"} tabIndex={-1} className="boss-content" data-page={location.pathname.split("/")[1]} key={session?.revision || 0}>
          <ActionFeedback />
          <BossModuleNotice />
          <StoreClosureCard />
          <Routes>
            <Route path="/" element={<Navigate to="/home" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/home" element={<HomePage />} />
            <Route path="/support" element={<SupportPage />} />
            <Route path="/audition" element={<AuditionPage />} />
            <Route path="/club-content" element={<ContentPage />} />
            <Route path="/announcements" element={<AnnouncementsPage />} />
            <Route path="/announcement-detail" element={<AnnouncementPage />} />
            <Route path="/catalog" element={<CatalogPage />} />
            <Route path="/service" element={<ServicePage />} />
            <Route path="/companions" element={<CompanionsPage />} />
            <Route path="/companion-picker" element={<CompanionsPage picker />} />
            <Route path="/companion-detail" element={<CompanionPage />} />
            <Route path="/checkout" element={guarded(<H5OrderGate><CheckoutPage /></H5OrderGate>)} />
            <Route path="/orders" element={guarded(<OrdersPage />)} />
            <Route path="/order-detail" element={guarded(<OrderPage />)} />
            <Route path="/gifts" element={guarded(<H5OrderGate><GiftsPage /></H5OrderGate>)} />
            <Route
              path="/reservations"
              element={guarded(<ReservationsPage />)}
            />
            <Route
              path="/reservation-detail"
              element={guarded(<ReservationPage />)}
            />
            <Route
              path="/reservation-create"
              element={guarded(<H5OrderGate><ReservationCreatePage /></H5OrderGate>)}
            />
            <Route path="/mine" element={guarded(<MinePage />)} />
            <Route path="/profile" element={guarded(<ProfilePage />)} />
            <Route path="/security" element={guarded(<SecurityPage />)} />
            <Route path="/wallet" element={guarded(<WalletPage />)} />
            <Route path="/membership" element={guarded(<MembershipPage />)} />
            <Route path="/checkin" element={guarded(<CheckinPage />)} />
            <Route
              path="/achievements"
              element={guarded(<AchievementsPage />)}
            />
            <Route path="/rankings" element={guarded(<RankingsPage />)} />
            <Route path="/coupons" element={guarded(<CouponsPage />)} />
            <Route path="/favorites" element={guarded(<FavoritesPage />)} />
            <Route path="/points" element={guarded(<PointsPage />)} />
            <Route path="/reward-claim" element={guarded(<ClaimPage />)} />
            <Route path="/lottery" element={guarded(<LotteryPage />)} />
            <Route path="/messages" element={guarded(<MessagesPage />)} />
            <Route path="/message-detail" element={guarded(<MessagePage />)} />
            <Route path="/chat" element={guarded(<ChatPage />)} />
            {Object.entries(legacyRoutes).map(([old, next]) => (
              <Route
                key={old}
                path={"/" + old}
                element={<LegacyRoute name={old} target={next} />}
              />
            ))}
            {[...canonicalPages, ...Object.keys(legacyRoutes)].map((name) => (
              <Route
                key={"native-" + name}
                path={"/pages/" + name + "/index"}
                element={
                  <LegacyRoute
                    name={name}
                    target={legacyRoutes[name] || name}
                  />
                }
              />
            ))}
            <Route
              path="*"
              element={
                <div className="boss-empty">
                  <h1>未找到页面</h1>
                  <Link to="/home">返回首页</Link>
                </div>
              }
            />
          </Routes>
        </ContentTag>
        <nav className="boss-bottom-nav" aria-label="手机端导航">
          {navigation.map((item) => (
              <Link key={item.path} to={item.path} aria-current={section === item.path ? (path === item.path ? "page" : "location") : undefined} aria-label={`${item.title}${section === item.path ? "，当前栏目" : ""}`} className={section === item.path ? "active" : ""}>
                <span className="boss-bottom-nav-icon"><item.icon size={20} strokeWidth={section === item.path ? 2.3 : 1.9} /></span>
                <span>{item.title}</span>
              </Link>
            ))}
        </nav>
      </div>
      <Toaster richColors position="top-center" />
    </div>
  )
}
