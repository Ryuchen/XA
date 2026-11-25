import { OtpSetupPanel, SkipLink, syncThemeColor, useCurrentStore } from "@xa/shared"
import { AdminSidebar } from "@/components/AdminSidebar"
import StoresPage from "@/pages/Stores"
import { useEffect, useLayoutEffect, useState, type ComponentType } from "react"
import {
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router"
import { clearSession, useRealtime, updateProfile } from "@xa/shared"
import { ChevronDown, ChevronRight, CircleHelp, LogOut, Moon, ShieldCheck, Sun } from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Feedback, LoadingPanel, useLoad } from "@/components/business"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { navigationDomains } from "@/lib/navigation"
import { operationPages } from "@/lib/operation-navigation"
import LoginPage from "@/pages/DualLogin"
import PlatformHome from "@/pages/PlatformConsole"
import WorkspacePage from "@/pages/Workspace"
import StoreModulesPage from "@/pages/ModuleCenter"
import { platformSession } from "@/lib/platform-session"
import DashboardPage, { PlayerDashboardPage } from "@/pages/Dashboard"
import OrdersPage from "@/pages/Orders"
import ReservationsPage from "@/pages/Reservations"
import {
  RecordsPage,
  ReportsPage,
  WalletsPage,
  WithdrawalsPage,
} from "@/pages/Financials"
import { EscortsPage, RegistrationsPage, UsersPage } from "@/pages/People"
import {
  BannersPage,
  CouponsPage,
  PromotionsPage,
  ResourcePage,
  ServicesPage,
  resourceSpecs,
} from "@/pages/Catalog"
import {
  AuditPage,
  DepositRulesPage,
  MembersPage,
  PassesPage,
  PaymentFeePage,
  RolesPage,
} from "@/pages/Settings"
import {
  AuditionsPage,
  ChatPage,
  EvaluationsPage,
  MessagesPage,
} from "@/pages/Support"
import ClubOperationsPage, { CheckinPage, OperationPage } from "@/pages/Operations"
import KookDispatchPage from "@/pages/KookDispatch"
import DictionariesPage from "@/pages/Dictionaries"
import {
  PayrollPage,
  SalaryActivitiesPage,
  SalaryAssignmentsPage,
  SalaryPlansPage,
} from "@/pages/Payroll"
import OperatingCostsPage from "@/pages/OperatingCosts"
import RoiPage from "@/pages/Roi"
import H5PagesPage from "@/pages/H5Pages"
import PendingReviews from "@/components/PendingReviews"
import Notifications from "@/components/Notifications"
import { Toaster } from "@/components/ui/sonner"
import "./App.css"
import "./admin-frame-fidelity.css"
import "./financial-fidelity.css"
import "./pages/catalog-operations-fidelity.css"
import "./orders-people-fidelity.css"
import "./admin-sidebar-motion.css"
import "./admin-design-principles.css"

const pages: Record<string, ComponentType> = {
  workspace: WorkspacePage,
  modules: StoreModulesPage,
  stores: StoresPage,
  dashboard: DashboardPage,
  "player-dashboard": PlayerDashboardPage,
  orders: OrdersPage,
  reservations: ReservationsPage,
  reports: ReportsPage,
  withdrawals: WithdrawalsPage,
  wallets: WalletsPage,
  "recharge-records": () => <RecordsPage kind="recharge-records" />,
  transactions: () => <RecordsPage kind="transactions" />,
  "dispose-records": () => <RecordsPage kind="dispose-records" />,
  users: UsersPage,
  escorts: EscortsPage,
  "provider-registrations": RegistrationsPage,
  "deposit-rules": DepositRulesPage,
  "provider-passes": PassesPage,
  admins: MembersPage,
  roles: RolesPage,
  "audit-logs": AuditPage,
  "payment-fee": PaymentFeePage,
  "service-items": ServicesPage,
  promotions: PromotionsPage,
  coupons: CouponsPage,
  banners: BannersPage,
  "h5-pages": H5PagesPage,
  evaluations: EvaluationsPage,
  chat: ChatPage,
  messages: MessagesPage,
  "audition-links": () => <AuditionsPage />,
  "audition-signups": () => <AuditionsPage signups />,
  "club-operations": ClubOperationsPage,
  checkin: CheckinPage,
  "kook-dispatch": KookDispatchPage,
  dictionaries: DictionariesPage,
  "salary-plans": SalaryPlansPage,
  "salary-assignments": SalaryAssignmentsPage,
  "salary-activities": SalaryActivitiesPage,
  payroll: PayrollPage,
  "operating-costs": OperatingCostsPage,
  "operating-roi": RoiPage,
  ...Object.fromEntries(
    Object.entries(resourceSpecs).map(([path, spec]) => [
      path,
      () => <ResourcePage spec={spec} />,
    ])
  ),
  ...Object.fromEntries(operationPages.map(page => [page.path, () => <OperationPage section={page.section} />])),
}
const permissions: Record<string, string | string[]> = {
  ...Object.fromEntries(operationPages.map(page => [page.path, page.permission])),
  modules: ["module:view", "module:manage"],
  dashboard: "dashboard:view",
  "player-dashboard": "player_dashboard:view",
  orders: "order:view",
  reservations: "order:view",
  "payment-fee": "payment_fee:view",
  "kook-dispatch": ["dispatch_config:view", "dispatch_config:edit"],
  reports: "report:view",
  withdrawals: "withdraw:view",
  evaluations: "evaluation:view",
  users: "user:view",
  "boss-types": "boss_type:view",
  wallets: "wallet:view",
  "recharge-records": "recharge:view",
  escorts: "escort:view",
  "provider-registrations": "registration:view",
  "deposit-rules": "deposit_rule:view",
  "escort-levels": "escort_level:view",
  "dispose-records": "dispose:view",
  "audition-links": "audition:view",
  "audition-signups": "audition:signup_view",
  "provider-passes": "escort:view",
  gameplays: "service:view",
  "service-items": "service:view",
  transactions: "transaction:view",
  promotions: "promotion:view",
  coupons: "coupon:view",
  "h5-pages": "engagement:view",
  "club-operations": [
    "engagement:view",
    "engagement:fulfill",
    "engagement:points",
  ],
  achievements: "achievement:view",
  checkin: "checkin:view",
  banners: "banner:view",
  announcements: "announcement:view",
  "support-cards": "support:view",
  chat: "chat:view",
  messages: "message:view",
  admins: "admin:view",
  roles: "role:view",
  "audit-logs": "audit:view",
  dictionaries: ["dictionary:view", "dictionary:edit"],
  "salary-plans": "salary:view",
  "salary-assignments": "salary:view",
  "salary-activities": "salary:view",
  payroll: "salary:view",
  "operating-costs": "cost:view",
  "operating-roi": "roi:view",
}
function AdminApplication() {
  const store = useCurrentStore()
  const auth = useAdminAuth()
  const canAccess = (path: string) =>
    store.status === "CONFIGURING" && !["dictionaries", "deposit-rules", "workspace", "modules"].includes(path)
      ? false
      : path === "stores"
      ? store.can_manage_stores === true
      : auth.hasPerm(permissions[path])
  const location = useLocation()
  const navigate = useNavigate()
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" })
  }, [location.pathname])
  const [logoutOpen, setLogoutOpen] = useState(false)
  const [otpSetupOpen, setOtpSetupOpen] = useState(false)
  const [sidebarLocked, setSidebarLocked] = useState(() => {
    try {
      return localStorage.getItem("xa:fronted:admin:sidebar:locked") === "true"
    } catch {
      return false
    }
  })
  const [sidebarExpanded, setSidebarExpanded] = useState(() => {
    if (sidebarLocked) return true
    try {
      return (
        sessionStorage.getItem("xa:fronted:admin:sidebar:expanded") === "true"
      )
    } catch {
      return false
    }
  })
  const changeSidebarExpanded = (expanded: boolean) => {
    setSidebarExpanded(expanded)
    try {
      sessionStorage.setItem(
        "xa:fronted:admin:sidebar:expanded",
        String(expanded)
      )
    } catch {}
  }
  const toggleSidebarLock = () => {
    const locked = !sidebarLocked
    setSidebarLocked(locked)
    try {
      if (locked)
        localStorage.setItem("xa:fronted:admin:sidebar:locked", "true")
      else localStorage.removeItem("xa:fronted:admin:sidebar:locked")
    } catch {}
    if (locked) changeSidebarExpanded(true)
  }
  const [dark, setDark] = useState(
    () => localStorage.getItem("xa:fronted:admin:theme") === "dark"
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("xa:fronted:admin:theme", dark ? "dark" : "light")
    return syncThemeColor()
  }, [dark])
  const profile = useLoad(async () => {
    if (!auth.token) return null
    const value = (await request({ url: "/auth/profile" })).data
    updateProfile("admin", value)
    return value
  }, [auth.token ? auth.revision : "signed-out"])
  useRealtime(
    "admin",
    (event) => {
      if (
        ["reconnected", "permissions_update", "account_update"].includes(
          event.type
        )
      )
        void profile.reload()
    },
    !!auth.token && store.status !== "CONFIGURING"
  )
  if (location.pathname === "/login")
    return auth.token ? (
      <Navigate to={store.status === "CONFIGURING" ? "/dictionaries" : "/dashboard"} replace />
    ) : (
      <LoginPage dark={dark} onThemeChange={setDark} />
    )
  if (!auth.token)
    return (
      <Navigate
        to={`/login?redirect=${encodeURIComponent(location.pathname + location.search)}`}
        replace
      />
    )
  if (profile.loading && !profile.data)
    return (
      <main id="main-content" tabIndex={-1} className="mx-auto mt-24 max-w-2xl">
        <LoadingPanel />
      </main>
    )
  if (profile.error && !profile.data)
    return (
      <main id="main-content" tabIndex={-1} className="mx-auto mt-24 max-w-2xl space-y-4">
        <h1 className="text-xl font-semibold">暂时无法读取后台权限</h1>
        <Feedback message={profile.error} retry={profile.reload} />
        <Button variant="outline" onClick={() => clearSession("admin")}>
          退出登录
        </Button>
      </main>
    )
  const domains = navigationDomains
    .map((domain) => ({
      ...domain,
      centers: domain.centers
        .map((center) => ({
          ...center,
          features: center.features.filter(
            (feature) => pages[feature.path] && canAccess(feature.path)
          ),
        }))
        .filter((center) => center.features.length),
    }))
    .filter((domain) => domain.centers.length)
  const featuresFor = (domain: (typeof navigationDomains)[number]) =>
    domain.centers.flatMap((center) => center.features)
  const allFeatures = domains.flatMap(featuresFor)
  const firstAllowed = allFeatures[0]?.path
  const current = allFeatures.find(
    (feature) => `/${feature.path}` === location.pathname
  )
  const activeDomain = domains.find((domain) =>
    featuresFor(domain).some((feature) => feature.path === current?.path)
  )
  const activeCenter = activeDomain?.centers.find((center) =>
    center.features.some((feature) => feature.path === current?.path)
  )
  const workspaceDomains = domains.filter((domain) => domain.id === "overview")
  const platformAdmin = store.can_manage_stores === true
  const brandName = platformAdmin ? "电竞管理系统" : store.name
  const openQuickDispatch = () => {
    const params = new URLSearchParams(location.search)
    params.set("dispatch", "quick")
    navigate({ pathname: "/orders", search: `?${params.toString()}` })
  }
  return (
    <div
      className={`admin-application grid min-h-screen min-w-[1366px] bg-background text-foreground ${sidebarExpanded ? "grid-cols-[296px_minmax(0,1fr)]" : "grid-cols-[80px_minmax(0,1fr)]"}`}
    >
      <AdminSidebar domains={domains} activeDomainId={activeDomain?.id} currentPath={location.pathname}
        store={store} brandName={brandName} platformAdmin={platformAdmin}
        canDispatch={canAccess("orders") && auth.hasPerm("order:dispatch")} onQuickDispatch={openQuickDispatch}
        expanded={sidebarExpanded} onExpandedChange={changeSidebarExpanded}
        locked={sidebarLocked} onToggleLock={toggleSidebarLock} />
      <div className="min-w-0">
        <header className="admin-topbar sticky top-0 z-30 flex h-16 items-center gap-4 border-b bg-background px-6">
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="shrink-0 text-muted-foreground">
              {location.pathname === "/stores" ? "平台" : store.name}
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            {activeDomain &&
              !workspaceDomains.some(
                (domain) => domain.id === activeDomain.id
              ) && (
                <>
                  <span className="shrink-0 text-muted-foreground">
                    {activeDomain.title}
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </>
              )}
            {activeCenter && activeDomain && activeDomain.centers.length > 1 && (
              <>
                <span className="shrink-0 text-muted-foreground">{activeCenter.title}</span>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              </>
            )}
            <span className="truncate font-medium">
              {current?.title || "后台"}
            </span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Notifications />
            <Button
              variant="ghost"
              size="icon"
              className="account-login-theme-toggle"
              aria-label={dark ? "切换为浅色模式" : "切换为深色模式"}
              onClick={() => setDark(!dark)}
            >
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  className="h-10 max-w-44 gap-2 px-2"
                  aria-label="打开账号菜单"
                >
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                    {(
                      auth.profile?.nickname ||
                      auth.profile?.username ||
                      "管"
                    ).slice(0, 1)}
                  </span>
                  <span className="hidden truncate text-sm 2xl:inline">
                    {auth.profile?.nickname ||
                      auth.profile?.username ||
                      "后台成员"}
                  </span>
                  <ChevronDown className="size-3.5 shrink-0" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="leading-5">
                  <span className="block text-sm font-medium text-foreground">
                    {auth.profile?.nickname ||
                      auth.profile?.username ||
                      "后台成员"}
                  </span>
                  <span className="mt-1 block truncate">
                    {auth.profile?.username || "当前账号"}
                  </span>
                  <span className="mt-1 block truncate">
                    {auth.profile?.roles
                      ?.map((role: any) => role.name)
                      .join(" / ") || "后台成员"}
                  </span>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="flex items-start gap-2 leading-5 whitespace-normal">
                  <CircleHelp className="mt-1 size-4 shrink-0" />
                  按角色权限提供操作；金额与处理结果以服务端为准。
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setOtpSetupOpen(true)}>
                  <ShieldCheck className="size-4" />
                  验证器设置
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => setLogoutOpen(true)}
                >
                  <LogOut className="size-4" />
                  退出登录
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="admin-content min-w-0 p-4 md:p-6"
          data-store-scope={store.slug}
          data-store-page={current?.path || "403"}
        >
          <PendingReviews />
          <Feedback message={profile.error} retry={profile.reload} />
          <Routes>
            <Route
              path="/"
              element={<Navigate to={`/${firstAllowed || "403"}`} replace />}
            />
            <Route
              path="/403"
              element={
                <div className="mx-auto my-16 flex min-h-80 max-w-xl flex-col items-center justify-center space-y-4 rounded-xl border bg-card p-8 text-center">
                  <ShieldCheck className="size-10 text-muted-foreground" />
                  <h1 className="text-2xl font-semibold">暂无访问权限</h1>
                  <p className="text-sm text-muted-foreground">
                    请联系管理员开通对应权限，或返回可访问的工作区。
                  </p>
                  {firstAllowed && (
                    <Button onClick={() => navigate(`/${firstAllowed}`)}>
                      返回工作区
                    </Button>
                  )}
                </div>
              }
            />
            {Object.entries(pages).map(([path, Page]) => (
              <Route
                key={path}
                path={`/${path}`}
                element={
                  canAccess(path) ? (
                    <Page />
                  ) : path === "dashboard" &&
                    firstAllowed &&
                    firstAllowed !== "dashboard" ? (
                    <Navigate to={`/${firstAllowed}`} replace />
                  ) : (
                    <Navigate to="/403" replace />
                  )
                }
              />
            ))}
            <Route
              path="*"
              element={<Navigate to={`/${firstAllowed || "403"}`} replace />}
            />
          </Routes>
        </main>
      </div>
      <Dialog open={otpSetupOpen} onOpenChange={setOtpSetupOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>验证器设置</DialogTitle><DialogDescription>管理当前账号的动态验证码。</DialogDescription></DialogHeader>
          <OtpSetupPanel role="admin" />
        </DialogContent>
      </Dialog>
      <AlertDialog open={logoutOpen} onOpenChange={setLogoutOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认退出管理后台？</AlertDialogTitle>
            <AlertDialogDescription>
              退出后需要重新完成账号密码和动态验证；未提交的表单内容不会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续工作</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                clearSession("admin")
                setLogoutOpen(false)
                navigate("/login")
              }}
            >
              确认退出
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
export default function App() {
  return (
    <HashRouter>
      <SkipLink />
      {platformSession() ? <PlatformHome /> : <AdminApplication />}
      <Toaster richColors closeButton position="bottom-right" />
    </HashRouter>
  )
}
