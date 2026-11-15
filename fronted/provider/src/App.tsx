import { clearSession, SkipLink, storeBrandLogo, syncThemeColor, updateProfile, useCurrentStore, useResource } from "@xa/shared"
import { useEffect, useState } from "react"
import {
  HashRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router"
import { useSession } from "@xa/shared"
import {
  ClipboardCheck,
  MessageSquareText,
  FileCheck2,
  WalletCards,
  UserRound,
  Moon,
  Sun,
  Volume2,
  VolumeX,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import LoginPage from "./pages/Login"
import { RegisterPage, AuditionPage } from "./pages/Auth"
import {
  OrdersPage,
  ApplicationsPage,
  ApplicationDetailPage,
  ReservationsPage,
} from "./pages/Orders"
import { ReportsPage } from "./pages/Reports"
import { WalletPage, WithdrawalAccountsPage, PassPage } from "./pages/Wallet"
import {
  MinePage,
  ProfilePage,
  SkillsPage,
  EvaluationsPage,
} from "./pages/Account"
import { MessagesPage, SupportPage, SupportChatPage } from "./pages/Messaging"
import { PointsPage } from "./pages/Points"
import { useOrderAlerts } from "./lib/alerts"
import { client } from "./lib/provider"
import { AuditionsPage } from "./pages/Auditions"
import "./layout.css"

const links = [
  { to: "/orders", label: "接单工作台", short: "接单", icon: ClipboardCheck },
  { to: "/reports", label: "我的报单", short: "报单", icon: FileCheck2 },
  {
    to: "/messages",
    label: "消息中心",
    short: "消息",
    icon: MessageSquareText,
  },
  { to: "/wallet", label: "我的钱包", short: "钱包", icon: WalletCards },
  { to: "/mine", label: "我的个人中心", short: "我的", icon: UserRound },
]
function Guard() {
  const session = useSession("provider")
  const location = useLocation()
  const verification = useResource(async () => {
    if (!session.token) return null
    try {
      const profile = await client.get<{ id: string | number; role?: string }>("/users/me/")
      if (profile.role !== "provider" || String(profile.id) !== String(session.user?.id || "")) {
        clearSession("provider")
        throw new Error("登录账号已变化，请重新登录")
      }
      updateProfile("provider", profile)
      return true
    } catch (cause) {
      const status = (cause as { status?: number })?.status
      if (status === 401 || status === 403) clearSession("provider")
      throw cause
    }
  }, [session.sessionId])
  if (!session.token)
    return <Navigate to={`/login?redirect=${encodeURIComponent(location.pathname + location.search)}`} replace />
  if (verification.loading)
    return <main id="main-content" tabIndex={-1} className="grid min-h-dvh place-items-center bg-background p-6 text-sm text-muted-foreground" role="status">正在验证陪玩登录状态…</main>
  if (verification.error || verification.data !== true)
    return <main id="main-content" tabIndex={-1} className="grid min-h-dvh place-items-center bg-background p-6"><section className="grid w-full max-w-md justify-items-center gap-4 rounded-[12px] border bg-card p-6 text-center" role="alert"><h1 className="text-xl font-semibold">暂时无法验证登录状态</h1><p className="text-sm leading-6 text-muted-foreground">{verification.error?.message || "请重新验证后再进入陪玩工作台。"}</p><Button className="h-11 w-full" onClick={() => void verification.reload()}>重新验证</Button><Button variant="outline" className="h-11 w-full" onClick={() => clearSession("provider")}>返回登录</Button></section></main>
  return <Workspace />
}
function Workspace() {
  const store = useCurrentStore()
  const location = useLocation(),
    [dark, setDark] = useState(() => {
      try {
        const saved = localStorage.getItem("xa_fronted_provider_theme")
        if (saved) return saved === "dark"
      } catch {
        /* 使用当前主题 */
      }
      return document.documentElement.classList.contains("dark")
    }),
    alerts = useOrderAlerts()
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    return syncThemeColor()
  }, [dark])
  const section =
    location.pathname.startsWith("/applications") ||
    location.pathname === "/reservations"
      ? "/orders"
      : location.pathname === "/withdraw-accounts"
        ? "/wallet"
        : ["/support", "/support-chat"].includes(location.pathname)
          ? "/messages"
          : ["/profile", "/skills", "/evaluations", "/points", "/auditions", "/pass"].includes(
                location.pathname
              )
            ? "/mine"
            : location.pathname

  return (
    <div className="provider-app min-h-dvh bg-background text-foreground">
      <SkipLink />
      <div className="provider-workspace">
        <header className="provider-topbar sticky top-0 z-30 h-16 border-b bg-background/90 backdrop-blur-xl">
          <div className="provider-header-inner">
          <div className="provider-header-brand-group flex items-center gap-2">
            <Link to="/orders" className="provider-header-brand" aria-label={`${store.name}陪玩工作台首页`}>
              <img src={storeBrandLogo(store, import.meta.env.BASE_URL)} alt="" width={32} height={32} />
              <span><strong title={store.name}>{store.name}</strong><small>陪玩工作台</small></span>
            </Link>
          </div>
          <nav className="provider-header-nav" aria-label="陪玩端桌面导航">
            {links.map(item => <Link key={item.to} to={item.to} aria-label={item.label} aria-current={section === item.to ? "page" : undefined} className={section === item.to ? "active" : ""}><item.icon className="size-4" aria-hidden="true" />{item.short}</Link>)}
          </nav>
          <div className="provider-header-actions flex gap-1">
            <Button variant="ghost" size="icon" className="size-10" onClick={alerts.toggle} aria-label={alerts.enabled ? "关闭接单提醒音" : "开启接单提醒音"} aria-pressed={alerts.enabled} title="接单提醒音">
              {alerts.enabled ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
            </Button>
            <Button variant="ghost" size="icon" className="account-login-theme-toggle provider-theme-toggle size-11 bg-transparent hover:bg-transparent active:bg-transparent dark:hover:bg-transparent" aria-label={dark ? "切换为浅色模式" : "切换为深色模式"} onClick={() => {
              document.documentElement.classList.toggle("dark", !dark)
              setDark(!dark)
              localStorage.setItem("xa_fronted_provider_theme", !dark ? "dark" : "light")
            }}>
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="provider-content mx-auto w-full max-w-[1120px] px-4 pt-2 pb-32 md:px-6 md:pt-4 md:pb-12">
          <Outlet />
        </main>
      </div>
      <nav
        className="provider-dock fixed inset-x-3 bottom-3 z-30 grid grid-cols-5 rounded-[12px] border bg-background/75 px-2 py-1 shadow-lg backdrop-blur-xl md:hidden"
        aria-label="移动端主导航"
      >
        {links.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            aria-label={item.label}
            aria-current={section === item.to ? "page" : undefined}
            className={`relative flex min-h-12 flex-col items-center justify-center gap-1 text-xs ${section === item.to ? "font-semibold text-primary" : "text-muted-foreground"}`}
          >
            <item.icon className="size-5" aria-hidden="true" />
            {item.short}
          </Link>
        ))}
      </nav>
    </div>
  )
}
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/audition" element={<AuditionPage />} />
        <Route path="/pages/audition/index" element={<AuditionPage />} />
        <Route element={<Guard />}>
          <Route index element={<Navigate to="/orders" replace />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/auditions" element={<AuditionsPage />} />
          <Route path="/applications" element={<ApplicationsPage />} />
          <Route
            path="/applications/:orderId"
            element={<ApplicationDetailPage />}
          />
          <Route path="/reservations" element={<ReservationsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/wallet" element={<WalletPage />} />
          <Route
            path="/withdraw-accounts"
            element={<WithdrawalAccountsPage />}
          />
          <Route path="/pass" element={<PassPage />} />
          <Route path="/mine" element={<MinePage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/evaluations" element={<EvaluationsPage />} />
          <Route path="/points" element={<PointsPage />} />
          <Route path="/messages" element={<MessagesPage />} />
          <Route path="/support" element={<SupportPage />} />
          <Route path="/support-chat" element={<SupportChatPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/orders" replace />} />
      </Routes>
    </HashRouter>
  )
}
