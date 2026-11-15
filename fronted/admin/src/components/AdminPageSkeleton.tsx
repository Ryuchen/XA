import { Skeleton } from "@/components/ui/skeleton"

// Each prototype branch is kept verbatim so page geometry remains reviewable.
const prototypeLayouts = {
  "reservation-quote": () => (
    <div
      role="status"
      data-admin-skeleton="reservation-quote"
      className="space-y-3"
    >
      <p className="text-sm text-muted-foreground">正在按当前规则重新报价…</p>
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
    </div>
  ),
  "recharge-proof": () => (
    <div
      role="status"
      data-admin-skeleton="recharge-proof"
      className="space-y-3"
    >
      <p className="text-sm text-muted-foreground">正在读取收款凭证…</p>
      <Skeleton className="aspect-[3/4] max-h-[58dvh] w-full rounded-xl" />
    </div>
  ),
  "commission-preview": () => (
    <div
      role="status"
      data-admin-skeleton="commission-preview"
      className="space-y-3 rounded-xl bg-muted/40 p-4"
      aria-label="正在试算订单分账"
    >
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  ),
  claims: () => (
    <div
      role="status"
      data-admin-skeleton="claims"
      className="space-y-4"
      aria-label="权益交付记录加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <div className="grid grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((item) => (
          <Skeleton key={item} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  orders: () => (
    <div
      role="status"
      data-admin-skeleton="orders"
      className="space-y-4"
      aria-label="订单列表加载中"
    >
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-40 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  providers: () => (
    <div
      role="status"
      data-admin-skeleton="providers"
      className="space-y-4"
      aria-label="陪玩档案加载中"
    >
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-44 rounded-xl" />
      <Skeleton className="h-[560px] rounded-xl" />
    </div>
  ),
  registrations: () => (
    <div
      role="status"
      data-admin-skeleton="registrations"
      className="space-y-4"
      aria-label="注册申请加载中"
    >
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  levels: () => (
    <div
      role="status"
      data-admin-skeleton="levels"
      className="space-y-4"
      aria-label="陪玩等级加载中"
    >
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[480px] rounded-xl" />
    </div>
  ),
  "audition-links": () => (
    <div
      role="status"
      data-admin-skeleton="audition-links"
      className="space-y-4"
      aria-label="试音活动加载中"
    >
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-20 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  "audition-signups": () => (
    <div
      role="status"
      data-admin-skeleton="audition-signups"
      className="space-y-4"
      aria-label="试音报名加载中"
    >
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-20 rounded-xl" />
      <Skeleton className="h-[500px] rounded-xl" />
    </div>
  ),
  withdrawals: () => (
    <div
      role="status"
      data-admin-skeleton="withdrawals"
      className="space-y-4"
      aria-label="提现列表加载中"
    >
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-20 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  wallets: () => (
    <div
      role="status"
      data-admin-skeleton="wallets"
      className="space-y-4"
      aria-label="钱包台账加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <div className="grid gap-4 md:grid-cols-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-[500px] rounded-xl" />
    </div>
  ),
  transactions: () => (
    <div
      role="status"
      data-admin-skeleton="transactions"
      className="space-y-4"
      aria-label="资金流水加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <div className="grid gap-4 md:grid-cols-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  recharges: () => (
    <div
      role="status"
      data-admin-skeleton="recharges"
      className="space-y-4"
      aria-label="充值记录加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <div className="grid gap-4 md:grid-cols-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  reservations: () => (
    <div
      role="status"
      data-admin-skeleton="reservations"
      className="space-y-4"
      aria-label="预约列表加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <div className="grid gap-4 md:grid-cols-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  reports: () => (
    <div
      role="status"
      data-admin-skeleton="reports"
      className="space-y-4"
      aria-label="报单审核加载中"
    >
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  services: () => (
    <div
      role="status"
      data-admin-skeleton="services"
      className="space-y-4"
      aria-label="服务商品加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  gameplays: () => (
    <div
      role="status"
      data-admin-skeleton="gameplays"
      className="space-y-4"
      aria-label="玩法配置加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  banners: () => (
    <div
      role="status"
      data-admin-skeleton="banners"
      className="space-y-4"
      aria-label="首页轮播加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  announcements: () => (
    <div
      role="status"
      data-admin-skeleton="announcements"
      className="space-y-4"
      aria-label="公告管理加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[520px] rounded-xl" />
    </div>
  ),
  contents: () => (
    <div
      role="status"
      data-admin-skeleton="contents"
      className="space-y-4"
      aria-label="运营内容加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[560px] rounded-xl" />
    </div>
  ),
  "broadcast-rules": () => (
    <div
      role="status"
      data-admin-skeleton="broadcast-rules"
      className="space-y-4"
      aria-label="播报规则加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[560px] rounded-xl" />
    </div>
  ),
  "broadcast-records": () => (
    <div
      role="status"
      data-admin-skeleton="broadcast-records"
      className="space-y-4"
      aria-label="播报记录加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[560px] rounded-xl" />
    </div>
  ),
  rewards: () => (
    <div
      role="status"
      data-admin-skeleton="rewards"
      className="space-y-4"
      aria-label="积分好礼加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <Skeleton className="h-[560px] rounded-xl" />
    </div>
  ),
  lotteries: () => (
    <div
      role="status"
      data-admin-skeleton="lotteries"
      className="space-y-4"
      aria-label="幸运转盘加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-28 rounded-xl" />
      <Skeleton className="h-[500px] rounded-xl" />
    </div>
  ),
  "operation-config": () => (
    <div
      role="status"
      data-admin-skeleton="operation-config"
      className="space-y-4"
      aria-label="运营设置加载中"
    >
      <Skeleton className="h-32 rounded-xl" />
      <Skeleton className="h-24 rounded-xl" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Skeleton className="h-[560px] rounded-xl" />
        <Skeleton className="h-[560px] rounded-xl" />
      </div>
    </div>
  ),
  "order-detail": () => (
    <div
      role="status"
      data-admin-skeleton="order-detail"
      className="space-y-4"
      aria-label="订单详情加载中"
    >
      <Skeleton className="h-36 rounded-xl" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]">
        <Skeleton className="h-[720px] rounded-xl" />
        <Skeleton className="h-[520px] rounded-xl" />
      </div>
    </div>
  ),
  "provider-detail": () => (
    <div
      role="status"
      data-admin-skeleton="provider-detail"
      className="space-y-4"
      aria-label="陪玩档案详情加载中"
    >
      <Skeleton className="h-52 rounded-xl" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]">
        <Skeleton className="h-[760px] rounded-xl" />
        <Skeleton className="h-[540px] rounded-xl" />
      </div>
    </div>
  ),
  "registration-detail": () => (
    <div
      role="status"
      data-admin-skeleton="registration-detail"
      className="space-y-4"
      aria-label="注册申请详情加载中"
    >
      <Skeleton className="h-52 rounded-xl" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_380px]">
        <Skeleton className="h-[720px] rounded-xl" />
        <Skeleton className="h-[560px] rounded-xl" />
      </div>
    </div>
  ),
  dashboard: () => (
    <div
      role="status"
      data-admin-skeleton="dashboard"
      className="space-y-4"
      aria-label="工作台数据加载中"
    >
      <div className="grid gap-3 md:grid-cols-3">
        {[0, 1, 2].map((item) => (
          <Skeleton key={item} className="h-56 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <Skeleton key={item} className="h-36 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]">
        <Skeleton className="h-80 rounded-xl" />
        <Skeleton className="h-80 rounded-xl" />
      </div>
      <Skeleton className="h-72 rounded-xl" />
    </div>
  ),
  disposes: () => (
    <div
      role="status"
      data-admin-skeleton="disposes"
      className="space-y-3 border-t p-4"
      aria-label="正在读取奖惩记录"
    >
      {[0, 1, 2, 3, 4].map((item) => (
        <Skeleton key={item} className="h-20 rounded-lg" />
      ))}
    </div>
  ),
  "pass-config": () => (
    <div
      role="status"
      data-admin-skeleton="pass-config"
      className="grid gap-3 md:grid-cols-2 xl:grid-cols-4"
      aria-label="正在读取通行证规则"
    >
      {[0, 1, 2, 3].map((item) => (
        <Skeleton key={item} className="h-52 rounded-xl" />
      ))}
    </div>
  ),
  "commission-config": () => (
    <div
      role="status"
      data-admin-skeleton="commission-config"
      className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(340px,2fr)]"
      aria-label="正在读取抽成配置"
    >
      <Skeleton className="h-[450px] rounded-xl" />
      <Skeleton className="h-[450px] rounded-xl" />
    </div>
  ),
  "payment-fee-config": () => (
    <div
      role="status"
      data-admin-skeleton="payment-fee-config"
      className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]"
      aria-label="正在读取支付费率配置"
    >
      <Skeleton className="h-[440px] rounded-xl" />
      <Skeleton className="h-[440px] rounded-xl" />
    </div>
  ),
  "payment-fee-preview": () => (
    <div
      role="status"
      data-admin-skeleton="payment-fee-preview"
      className="space-y-3 rounded-xl bg-muted/40 p-4"
      aria-label="正在试算手续费"
    >
      <Skeleton className="h-4 w-28" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  ),
  "payment-fee-history": () => (
    <div
      role="status"
      data-admin-skeleton="payment-fee-history"
      className="space-y-3 border-t p-4"
      aria-label="正在加载费率变更记录"
    >
      {[0, 1, 2].map((item) => (
        <Skeleton key={item} className="h-14 w-full" />
      ))}
    </div>
  ),
  "chat-sessions": () => (
    <div
      role="status"
      data-admin-skeleton="chat-sessions"
      className="space-y-2 p-3"
      aria-label="会话列表加载中"
    >
      {[0, 1, 2, 3, 4].map((item) => (
        <div key={item} className="flex gap-3 p-3">
          <Skeleton className="size-10 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-full" />
          </div>
        </div>
      ))}
    </div>
  ),
  "chat-messages": () => (
    <div
      role="status"
      data-admin-skeleton="chat-messages"
      className="space-y-5 p-5"
      aria-label="会话消息加载中"
    >
      <Skeleton className="h-20 w-3/5 rounded-2xl" />
      <Skeleton className="ml-auto h-16 w-1/2 rounded-2xl" />
      <Skeleton className="h-24 w-2/3 rounded-2xl" />
    </div>
  ),
  "report-detail": () => (
    <div
      role="status"
      data-admin-skeleton="report-detail"
      className="space-y-4"
      aria-label="报单详情加载中"
    >
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-28 w-full" />
    </div>
  ),
  "transaction-original": () => (
    <div
      role="status"
      data-admin-skeleton="transaction-original"
      className="space-y-3"
    >
      <p className="text-sm text-muted-foreground">正在读取原流水…</p>
      <Skeleton className="h-12" />
      <Skeleton className="h-12" />
      <Skeleton className="h-12" />
    </div>
  ),
  "level-impact": () => (
    <div
      role="status"
      data-admin-skeleton="level-impact"
      className="space-y-3"
      aria-label="关联情况读取中"
    >
      <Skeleton className="h-20 rounded-xl" />
      <Skeleton className="h-28 rounded-xl" />
    </div>
  ),
}

const extensionSources = {
  "reservation-detail": "report-detail",
  "resource-directory": "levels",
  users: "providers",
  members: "levels",
  roles: "levels",
  audit: "reports",
  stores: "levels",
  dictionary: "levels",
  "salary-plans": "services",
  "salary-assignments": "reports",
  "salary-activities": "reports",
  payroll: "operation-config",
  "operating-costs": "reports",
  "operating-roi": "dashboard",
  "player-dashboard": "dashboard",
  "deposit-rules": "operation-config",
  "checkin-rules": "operation-config",
  "checkin-progress": "reports",
  "coupon-claims": "reports",
  "vip-tiers": "levels",
  "provider-namings": "broadcast-records",
  "kook-config": "operation-config",
  "kook-status": "operation-config",
  evaluations: "reports",
  messages: "broadcast-records",
  "financial-detail": "report-detail",
  "audition-signup-detail": "report-detail",
} as const

export type AdminSkeletonKind =
  keyof typeof prototypeLayouts | keyof typeof extensionSources

export const adminSkeletonSources = {
  "reservation-quote": {
    file: "AdminReservationsPage.tsx",
    line: 232,
    scope: "region",
  },
  "recharge-proof": {
    file: "AdminRechargesPage.tsx",
    line: 128,
    scope: "region",
  },
  "commission-preview": {
    file: "AdminCommissionPage.tsx",
    line: 164,
    scope: "region",
  },
  claims: { file: "AdminClaimsPage.tsx", line: 111, scope: "page" },
  orders: { file: "AdminOrdersPage.tsx", line: 105, scope: "page" },
  providers: { file: "AdminProvidersPage.tsx", line: 128, scope: "page" },
  registrations: {
    file: "AdminRegistrationsPage.tsx",
    line: 78,
    scope: "page",
  },
  levels: { file: "AdminLevelsPage.tsx", line: 149, scope: "page" },
  "audition-links": {
    file: "AdminAuditionLinksPage.tsx",
    line: 233,
    scope: "page",
  },
  "audition-signups": {
    file: "AdminAuditionSignupsPage.tsx",
    line: 105,
    scope: "page",
  },
  withdrawals: { file: "AdminWithdrawPage.tsx", line: 173, scope: "page" },
  wallets: { file: "AdminWalletPage.tsx", line: 234, scope: "page" },
  transactions: { file: "AdminTransactionsPage.tsx", line: 105, scope: "page" },
  recharges: { file: "AdminRechargesPage.tsx", line: 89, scope: "page" },
  reservations: { file: "AdminReservationsPage.tsx", line: 191, scope: "page" },
  reports: { file: "AdminReportsPage.tsx", line: 146, scope: "page" },
  services: { file: "AdminServicesPage.tsx", line: 136, scope: "page" },
  gameplays: { file: "AdminGameplaysPage.tsx", line: 98, scope: "page" },
  banners: { file: "AdminBannersPage.tsx", line: 108, scope: "page" },
  announcements: {
    file: "AdminAnnouncementsPage.tsx",
    line: 102,
    scope: "page",
  },
  contents: { file: "AdminContentsPage.tsx", line: 112, scope: "page" },
  "broadcast-rules": {
    file: "AdminBroadcastRulesPage.tsx",
    line: 82,
    scope: "page",
  },
  "broadcast-records": {
    file: "AdminBroadcastRecordsPage.tsx",
    line: 93,
    scope: "page",
  },
  rewards: { file: "AdminRewardsPage.tsx", line: 100, scope: "page" },
  lotteries: { file: "AdminLotteriesPage.tsx", line: 88, scope: "page" },
  "operation-config": {
    file: "AdminOperationConfigPage.tsx",
    line: 127,
    scope: "page",
  },
  "order-detail": {
    file: "AdminOrderDetailPage.tsx",
    line: 103,
    scope: "page",
  },
  "provider-detail": {
    file: "AdminProviderDetailPage.tsx",
    line: 129,
    scope: "page",
  },
  "registration-detail": {
    file: "AdminRegistrationDetailPage.tsx",
    line: 134,
    scope: "page",
  },
  dashboard: { file: "AdminDashboardPage.tsx", line: 98, scope: "page" },
  disposes: { file: "AdminDisposesPage.tsx", line: 119, scope: "region" },
  "pass-config": { file: "AdminPassesPage.tsx", line: 144, scope: "region" },
  "commission-config": {
    file: "AdminCommissionPage.tsx",
    line: 145,
    scope: "region",
  },
  "payment-fee-config": {
    file: "AdminPaymentFeePage.tsx",
    line: 189,
    scope: "region",
  },
  "payment-fee-preview": {
    file: "AdminPaymentFeePage.tsx",
    line: 217,
    scope: "region",
  },
  "payment-fee-history": {
    file: "AdminPaymentFeePage.tsx",
    line: 229,
    scope: "region",
  },
  "chat-sessions": { file: "AdminChatPage.tsx", line: 260, scope: "region" },
  "chat-messages": { file: "AdminChatPage.tsx", line: 276, scope: "region" },
  "report-detail": { file: "AdminReportsPage.tsx", line: 195, scope: "region" },
  "transaction-original": {
    file: "AdminTransactionsPage.tsx",
    line: 137,
    scope: "region",
  },
  "level-impact": { file: "AdminLevelsPage.tsx", line: 187, scope: "region" },
} as const

export function skeletonScope(page: AdminSkeletonKind): "page" | "region" {
  if (page in extensionSources)
    return adminSkeletonSources[
      extensionSources[page as keyof typeof extensionSources]
    ].scope
  return adminSkeletonSources[page as keyof typeof adminSkeletonSources].scope
}

export function AdminPageSkeleton({ page }: { page: AdminSkeletonKind }) {
  const source =
    page in extensionSources
      ? extensionSources[page as keyof typeof extensionSources]
      : (page as keyof typeof prototypeLayouts)
  const Layout = prototypeLayouts[source]
  return <Layout />
}

export function resourceSkeleton(resource: string): AdminSkeletonKind {
  return (
    (
      {
        gameplays: "gameplays",
        "escort-levels": "levels",
        announcements: "announcements",
        "club-content": "contents",
        "club-rewards": "rewards",
        "broadcast-rules": "broadcast-rules",
      } as Record<string, AdminSkeletonKind>
    )[resource] || "resource-directory"
  )
}
