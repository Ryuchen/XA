import { PlatformReadStatus, PlatformStoreStatus } from "@/components/PlatformStoreStatus"
import { DataValue, DataRegion } from "@/components/page-data-state"
import { useEffect, useState } from "react"
import { NavLink, Navigate, Route, Routes } from "react-router"
import {
  Building2,
  ClipboardList,
  Headphones,
  LayoutDashboard,
  LogOut,
  Moon,
  Package,
  ShieldCheck,
  Sun,
  Users,
  WalletCards,
} from "lucide-react"
import { formatDateTime, useResource, syncThemeColor } from "@xa/shared"
import { Button } from "@/components/ui/button"
import {
  clearPlatformSession,
  platformRequest,
  type PlatformProfile,
} from "@/lib/platform-session"
import { PlatformOtpSetup } from "@/components/PlatformOtpSetup"
import { PlatformAccounts } from "@/components/PlatformAccounts"
import { PlatformPassRefundShortfalls } from "@/components/PlatformPassRefundShortfalls"
import { PlatformWithdrawals } from "@/components/PlatformWithdrawals"
import { PlatformStoreManagement } from "@/components/PlatformStoreManagement"
import { Feedback, PageHeader } from "@/components/business"
import PlatformOrders from "./PlatformOrders"
import { PlatformModulesPage } from "./ModuleCenter"
import "./saas.css"

type Store = {
  slug: string
  name: string
  status: string
  currency_name: string
}
type Aggregate = {
  complete: boolean
  generated_at: string
  partial_totals: { orders: number; bosses: number; providers: number }
  stores: {
    slug: string
    name: string
    status: string
    read_at: string | null
    counts: { orders: number; bosses: number; providers: number } | null
  }[]
}

export default function PlatformConsole() {
  const [revision, setRevision] = useState(0)
  const identity = useResource(
    () => platformRequest<PlatformProfile>("profile"),
    [revision]
  )
  const directory = useResource(
    () => platformRequest<{ results: Store[] }>("stores"),
    [revision]
  )
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark")
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("xa:fronted:admin:theme", dark ? "dark" : "light")
    syncThemeColor()
  }, [dark])
  const profile =
    !identity.error && !identity.loading ? identity.data : undefined
  const stores =
    !directory.error && !directory.loading ? directory.data?.results || [] : []
  const superAdmin =
    profile?.grants.some(
      (grant) => grant.role === "SUPER_ADMIN" && grant.scope_slug === "*"
    ) === true
  const finance =
    superAdmin ||
    profile?.grants.some((grant) => grant.role === "FINANCE") === true
  const orders =
    superAdmin ||
    profile?.grants.some((grant) =>
      ["ORDER_VIEWER", "ORDER_OPERATOR"].includes(grant.role)
    ) === true
  const navigation = [
    {
      to: "/platform/overview",
      label: "平台总览",
      icon: LayoutDashboard,
      visible: true,
    },
    {
      to: "/platform/clubs",
      label: "入驻俱乐部",
      icon: Building2,
      visible: superAdmin,
    },
    {
      to: "/platform/orders",
      label: "订单与派单",
      icon: ClipboardList,
      visible: orders,
    },
    {
      to: "/platform/modules",
      label: "模块与购买",
      icon: Package,
      visible: superAdmin,
    },
    {
      to: "/platform/finance",
      label: "平台财务",
      icon: WalletCards,
      visible: finance,
    },
    {
      to: "/platform/accounts",
      label: "平台账号",
      icon: Users,
      visible: superAdmin,
    },
    {
      to: "/platform/security",
      label: "账号安全",
      icon: ShieldCheck,
      visible: true,
    },
  ].filter((item) => item.visible)
  function logout() {
    clearPlatformSession()
    location.hash = "/login"
    location.reload()
  }
  const forbidden = (
    <section className="saas-section">
      <h1 className="text-xl font-semibold">暂无访问权限</h1>
      <p className="saas-note">
        当前平台身份未获得此工作区授权，请联系平台超级管理员。
      </p>
    </section>
  )
  return (
    <div className="platform-shell">
      <aside className="platform-sidebar">
        <div className="flex items-center gap-3 px-2">
          <Building2 className="size-7 text-primary" />
          <div>
            <p className="font-semibold">电竞俱乐部 SaaS</p>
            <p className="mt-1 text-xs text-muted-foreground">平台运营中心</p>
          </div>
        </div>
        <nav aria-label="平台主导航">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to}>
              <item.icon className="size-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3 border-t pt-4">
          <p className="px-2 text-xs text-muted-foreground">
            独立平台身份
            <br />
            业务归属各俱乐部
          </p>
          <Button variant="outline" className="w-full" onClick={logout}>
            <LogOut className="size-4" />
            退出登录
          </Button>
        </div>
      </aside>
      <main id="main-content" tabIndex={-1} className="platform-main">
        <header className="platform-topbar">
          <div>
            <p className="font-semibold">
              {profile?.nickname || profile?.username || "平台管理"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {superAdmin
                ? "平台超级管理员 · 获授权俱乐部"
                : finance
                  ? "平台财务 · 按财务授权范围"
                  : "平台人员 · 按独立授权范围"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              aria-label={dark ? "切换浅色" : "切换深色"}
              onClick={() => setDark((value) => !value)}
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </Button>
            <Button
              variant="outline"
              disabled={identity.loading || directory.loading}
              onClick={() => setRevision((value) => value + 1)}
            >
              刷新平台
            </Button>
          </div>
        </header>
        <Feedback
          message={identity.error?.message || directory.error?.message}
          retry={() => setRevision((value) => value + 1)}
        />
        {identity.loading ? (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            正在核验平台身份与授权…
          </p>
        ) : (
          profile && (
            <Routes>
              <Route
                path="/platform/overview"
                element={
                  <PlatformOverview
                    superAdmin={superAdmin}
                    stores={stores}
                    revision={revision} storesLoading={directory.loading} storesKnown={!!directory.data && !directory.loading && !directory.error} storesError={directory.error?.message} storesRetry={directory.reload}
                  />
                }
              />
              <Route
                path="/platform/clubs"
                element={
                  superAdmin ? (
                    <div className="space-y-6">
                      <PageHeader title="入驻俱乐部" description="维护俱乐部资料、营业状态与独立业务配置，核对各店入驻和初始化进度。" />
                      {profile.capabilities?.includes("STORE_MANAGEMENT_V1") ? (
                        <PlatformStoreManagement
                          revision={revision}
                          canSetup={profile.capabilities.includes(
                            "PLATFORM_SETUP_V1"
                          )}
                          canEditClocks={profile.capabilities.includes(
                            "PLATFORM_CLOCKS_V1"
                          )}
                          canEditDeposit={profile.capabilities.includes(
                            "PLATFORM_DEPOSIT_V1"
                          )}
                          canEditWithdraw={profile.capabilities.includes(
                            "PLATFORM_WITHDRAW_V1"
                          )}
                          onChanged={() => setRevision((value) => value + 1)}
                        />
                      ) : (
                        <p className="saas-note">
                          俱乐部入驻管理暂未开放，请联系平台。
                        </p>
                      )}
                    </div>
                  ) : (
                    forbidden
                  )
                }
              />
              <Route
                path="/platform/orders"
                element={
                  orders ? (
                    <PlatformOrders profile={profile} stores={stores} />
                  ) : (
                    forbidden
                  )
                }
              />
              <Route
                path="/platform/modules"
                element={
                  superAdmin ? (
                    <PlatformModulesPage profile={profile} stores={stores} />
                  ) : (
                    forbidden
                  )
                }
              />
              <Route
                path="/platform/finance"
                element={
                  finance ? (
                    <div className="space-y-6">
                      <PageHeader title="平台财务" description="按授权俱乐部核对退款资金缺口与提现申请，分别追踪到账凭证和处理结果。" />
                      <PlatformPassRefundShortfalls revision={revision} />
                      <PlatformWithdrawals
                        revision={revision}
                        financeScopes={profile.grants
                          .filter((grant) => grant.role === "FINANCE")
                          .map((grant) => grant.scope_slug)}
                      />
                    </div>
                  ) : (
                    forbidden
                  )
                }
              />
              <Route
                path="/platform/accounts"
                element={
                  superAdmin ? (
                    <div className="space-y-6">
                      <PageHeader title="平台账号与授权" description="维护平台人员账号与俱乐部授权范围，平台权限独立于店内后台角色。" />
                      <PlatformAccounts stores={stores} />
                    </div>
                  ) : (
                    forbidden
                  )
                }
              />
              <Route
                path="/platform/security"
                element={
                  <div className="space-y-6">
                    <PageHeader title="账号安全" description="绑定和维护当前平台账号的动态验证码，保护跨俱乐部管理操作。" />
                    <PlatformOtpSetup />
                  </div>
                }
              />
              <Route
                path="*"
                element={<Navigate to="/platform/overview" replace />}
              />
            </Routes>
          )
        )}
      </main>
    </div>
  )
}
function PlatformOverview({
  superAdmin,
  stores,
  revision, storesLoading, storesKnown, storesError, storesRetry,
}: {
  superAdmin: boolean
  stores: Store[]
  revision: number
  storesLoading: boolean
  storesKnown: boolean
  storesError?: string
  storesRetry: () => void
}) {
  const summary = useResource(
    () =>
      superAdmin
        ? platformRequest<Aggregate>("dashboard")
        : Promise.resolve(null),
    [superAdmin, revision]
  )
  const aggregate = summary.data
  const hasReadStores = !!aggregate?.stores.some(row => row.status === "OK" && !!row.counts)
  return (
    <div className="space-y-6">
      <PageHeader title="平台总览" description="查看授权俱乐部的业务规模和读取状态；部分来源不可用时，仅汇总已读取的数据。" />
      <div className="xa-role-scope">
        <strong>平台人员</strong>
        <span>独立平台授权 · 俱乐部内管理员身份不授予跨店权限</span>
      </div>
      <Feedback message={summary.error?.message} retry={summary.reload} />
      {summary.loading && (
        <p role="status" className="text-sm text-muted-foreground">
          正在读取俱乐部汇总…
        </p>
      )}
      {superAdmin && (
        <section className="saas-section">
          <h2>俱乐部业务汇总</h2>
          {summary.error && aggregate && (
            <p role="alert" className="saas-note">
              以下为上次读取结果，当前数据可能已变化。
            </p>
          )}
          {aggregate && !aggregate.complete && (
            <p role="alert" className="saas-note">
              部分俱乐部未取得数据，以下仅为已读取俱乐部的合计。
            </p>
          )}
          <dl className="saas-definition">
            {([
              ["订单", aggregate?.partial_totals.orders, ClipboardList],
              ["老板", aggregate?.partial_totals.bosses, Users],
              ["陪玩", aggregate?.partial_totals.providers, Headphones],
            ] as const).map(([name, value, Icon]) => (
              <div key={name}>
                <dt className="flex items-center gap-2"><Icon size={16} strokeWidth={1.75} aria-hidden="true" />{name}</dt>
                <dd className="text-2xl font-semibold tabular-nums"><DataValue known={!!aggregate && (aggregate.complete || hasReadStores) && value != null} loading={summary.loading}>{value}</DataValue></dd>
              </div>
            ))}
          </dl>
          <table className="saas-table">
            <thead>
              <tr>
                {["俱乐部", "读取状态", "订单", "老板", "陪玩", "读取时间"].map(
                  (title) => (
                    <th key={title} scope="col">
                      {title}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {(aggregate?.stores || []).map((row) => (
                <tr key={row.slug}>
                  <td>{row.name}</td>
                  <td>
                    <PlatformReadStatus value={row.status} />
                  </td>
                  <td>{row.status === "OK" ? row.counts?.orders ?? "—" : "—"}</td>
                  <td>{row.status === "OK" ? row.counts?.bosses ?? "—" : "—"}</td>
                  <td>{row.status === "OK" ? row.counts?.providers ?? "—" : "—"}</td>
                  <td>{formatDateTime(row.read_at)}</td>
                </tr>
              ))}
              {!aggregate?.stores.length && <tr><td colSpan={6}><DataRegion label="俱乐部业务汇总" loading={summary.loading} hasSnapshot={!!aggregate} error={summary.error?.message} retry={summary.reload}><span>暂无俱乐部数据</span></DataRegion></td></tr>}
            </tbody>
          </table>
          <p className="saas-note">
            统计时间：{formatDateTime(aggregate?.generated_at)}
            。数量按俱乐部分列，浪花和星辉不作为同一种余额合并。
          </p>
        </section>
      )}
      {!superAdmin && (
        <section className="saas-section">
          <h2>授权俱乐部</h2>
          <ul className="mt-4 divide-y">
            {stores.map((store) => (
              <li className="py-3 text-sm" key={store.slug}>
                {store.name} · {store.currency_name} ·{" "}
                <PlatformStoreStatus value={store.status} />
              </li>
            ))}
          </ul>
          {!stores.length && <DataRegion label="授权俱乐部" loading={storesLoading} hasSnapshot={storesKnown} error={storesError} retry={storesRetry}><p className="saas-note">暂无可读取的俱乐部，请核对账号授权。</p></DataRegion>}
        </section>
      )}
    </div>
  )
}
