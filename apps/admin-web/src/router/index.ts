import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { applyNavigationMeta, buildNavigation, flattenNavigation, type MenuPermission } from './navigation'

/**
 * meta:
 *  - title: 菜单/页签标题
 *  - icon:  Element Plus 图标组件名
 *  - perm:  访问所需权限点（超管自动放行）
 *  - group / center / title: 由 navigation.ts 按业务中心统一生成
 *  - hidden: 不在菜单中展示
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/index.vue'),
    meta: { hidden: true, public: true },
  },
  {
    path: '/',
    component: () => import('@/layout/index.vue'),
    redirect: '/dashboard',
    children: [
      // 页面地址保持兼容，菜单分类由 navigation.ts 统一维护。
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { perm: 'dashboard:view' },
      },
      {
        path: 'player-dashboard',
        name: 'player-dashboard',
        component: () => import('@/views/player-dashboard/index.vue'),
        meta: { perm: 'player_dashboard:view' },
      },

      {
        path: 'orders',
        name: 'orders',
        component: () => import('@/views/order/index.vue'),
        meta: { perm: 'order:view' },
      },
      {
        path: 'kook-dispatch',
        name: 'kook-dispatch',
        component: () => import('@/views/kook-dispatch/index.vue'),
        meta: { perm: ['dispatch_config:view', 'dispatch_config:edit'] },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/report/index.vue'),
        meta: { perm: 'report:view' },
      },
      {
        path: 'withdrawals',
        name: 'withdrawals',
        component: () => import('@/views/withdraw/index.vue'),
        meta: { perm: 'withdraw:view' },
      },
      {
        path: 'evaluations',
        name: 'evaluations',
        component: () => import('@/views/evaluation/index.vue'),
        meta: { perm: 'evaluation:view' },
      },

      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/user/index.vue'),
        meta: { perm: 'user:view' },
      },
      {
        path: 'boss-types',
        name: 'boss-types',
        component: () => import('@/views/boss-type/index.vue'),
        meta: { perm: 'boss_type:view' },
      },
      {
        path: 'wallets',
        name: 'wallets',
        component: () => import('@/views/wallet/index.vue'),
        meta: { perm: 'wallet:view' },
      },
      {
        path: 'recharge-records',
        name: 'recharge-records',
        component: () => import('@/views/recharge/index.vue'),
        meta: { perm: 'recharge:view' },
      },

      {
        path: 'escorts',
        name: 'escorts',
        component: () => import('@/views/escort/index.vue'),
        meta: { perm: 'escort:view' },
      },
      {
        path: 'provider-registrations',
        name: 'provider-registrations',
        component: () => import('@/views/provider-registration/index.vue'),
        meta: { perm: 'registration:view' },
      },
      {
        path: 'deposit-rules',
        name: 'deposit-rules',
        component: () => import('@/views/deposit-rules/index.vue'),
        meta: { perm: 'deposit_rule:view' },
      },
      {
        path: 'escort-levels',
        name: 'escort-levels',
        component: () => import('@/views/escort-level/index.vue'),
        meta: { perm: 'escort_level:view' },
      },
      {
        path: 'dispose-records',
        name: 'dispose-records',
        component: () => import('@/views/dispose/index.vue'),
        meta: { perm: 'dispose:view' },
      },
      {
        path: 'audition-links',
        name: 'audition-links',
        component: () => import('@/views/audition/index.vue'),
        meta: { perm: 'audition:view' },
      },
      {
        path: 'audition-signups',
        name: 'audition-signups',
        component: () => import('@/views/audition/signups.vue'),
        meta: { perm: 'audition:signup_view' },
      },

      {
        path: 'provider-passes',
        name: 'provider-passes',
        component: () => import('@/views/provider-pass/index.vue'),
        meta: { perm: 'escort:view' },
      },
      {
        path: 'gameplays',
        name: 'gameplays',
        component: () => import('@/views/gameplay/index.vue'),
        meta: { perm: 'service:view' },
      },
      {
        path: 'service-items',
        name: 'service-items',
        component: () => import('@/views/service/index.vue'),
        meta: { perm: 'service:view' },
      },
      {
        path: 'transactions',
        name: 'transactions',
        component: () => import('@/views/transaction/index.vue'),
        meta: { perm: 'transaction:view' },
      },
      {
        path: 'promotions',
        name: 'promotions',
        component: () => import('@/views/promotion/index.vue'),
        meta: { perm: 'promotion:view' },
      },
      {
        path: 'coupons',
        name: 'coupons',
        component: () => import('@/views/coupon/index.vue'),
        meta: { perm: 'coupon:view' },
      },
      {
        path: 'club-operations',
        name: 'club-operations',
        component: () => import('@/views/engagement/index.vue'),
        meta: { perm: 'engagement:view' },
      },
      {
        path: 'achievements',
        name: 'achievements',
        component: () => import('@/views/achievement/index.vue'),
        meta: { perm: 'achievement:view' },
      },
      {
        path: 'checkin',
        name: 'checkin',
        component: () => import('@/views/checkin/index.vue'),
        meta: { perm: 'checkin:view' },
      },
      {
        path: 'banners',
        name: 'banners',
        component: () => import('@/views/banner/index.vue'),
        meta: { perm: 'banner:view' },
      },
      {
        path: 'announcements',
        name: 'announcements',
        component: () => import('@/views/announcement/index.vue'),
        meta: { perm: 'announcement:view' },
      },
      {
        path: 'support-cards',
        name: 'support-cards',
        component: () => import('@/views/support/index.vue'),
        meta: { perm: 'support:view' },
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/chat/index.vue'),
        meta: { perm: 'chat:view' },
      },
      {
        path: 'messages',
        name: 'messages',
        component: () => import('@/views/message/index.vue'),
        meta: { perm: 'message:view' },
      },

      {
        path: 'admins',
        name: 'admins',
        component: () => import('@/views/system/admin.vue'),
        meta: { perm: 'admin:view' },
      },
      {
        path: 'roles',
        name: 'roles',
        component: () => import('@/views/system/role.vue'),
        meta: { perm: 'role:view' },
      },
      {
        path: 'audit-logs',
        name: 'audit-logs',
        component: () => import('@/views/system/audit.vue'),
        meta: { perm: 'audit:view' },
      },
      {
        path: '403',
        name: 'forbidden',
        component: () => import('@/views/error/403.vue'),
        meta: { hidden: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

const featureRoutes = routes.find(route => route.path === '/')!.children!
applyNavigationMeta(featureRoutes)

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    return true
  }
  if (!auth.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (!auth.profile) {
    const revision = auth.sessionRevision
    try {
      await auth.fetchProfile()
    } catch {
      // The HTTP layer clears only definitively expired credentials. A stale
      // profile request or temporary outage must not sign out a newer login.
      return auth.token ? false : { path: '/login' }
    }
    if (auth.sessionRevision !== revision) return false
  }
  const perm = to.meta.perm as MenuPermission
  if (perm && !auth.hasPerm(perm)) {
    if (to.path === '/dashboard') {
      const firstAllowed = flattenNavigation(buildNavigation(featureRoutes, code => auth.hasPerm(code)))[0]
      if (firstAllowed) return { path: `/${firstAllowed.path}` }
    }
    return { path: '/403' }
  }
  return true
})

export default router
