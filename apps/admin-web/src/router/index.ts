import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from 'vue-router'
import { useAuthStore } from '@/stores/auth'

/**
 * meta:
 *  - title: 菜单/页签标题
 *  - icon:  Element Plus 图标组件名
 *  - perm:  访问所需权限点（超管自动放行）
 *  - group: 侧栏分组名（对齐设计稿：概览/老板管理/陪玩管理/运营/权限系统）
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
      // ===== 概览 =====
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '平台概览', icon: 'Odometer', perm: 'dashboard:view', group: '概览' },
      },
      {
        path: 'player-dashboard',
        name: 'player-dashboard',
        component: () => import('@/views/player-dashboard/index.vue'),
        meta: { title: '陪玩概览', icon: 'DataAnalysis', perm: 'player_dashboard:view', group: '概览' },
      },

      // ===== 订单管理 =====
      {
        path: 'orders',
        name: 'orders',
        component: () => import('@/views/order/index.vue'),
        meta: { title: '派单管理', icon: 'Tickets', perm: 'order:view', group: '订单管理' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/report/index.vue'),
        meta: { title: '订单审核', icon: 'Document', perm: 'report:view', group: '订单管理' },
      },
      {
        path: 'withdrawals',
        name: 'withdrawals',
        component: () => import('@/views/withdraw/index.vue'),
        meta: { title: '提现审核', icon: 'Money', perm: 'withdraw:view', group: '订单管理' },
      },
      {
        path: 'evaluations',
        name: 'evaluations',
        component: () => import('@/views/evaluation/index.vue'),
        meta: { title: '订单评价', icon: 'Star', perm: 'evaluation:view', group: '订单管理' },
      },

      // ===== 老板管理 =====
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/user/index.vue'),
        meta: { title: '信息管理', icon: 'User', perm: 'user:view', group: '老板管理' },
      },
      {
        path: 'boss-types',
        name: 'boss-types',
        component: () => import('@/views/boss-type/index.vue'),
        meta: { title: '类型管理', icon: 'Medal', perm: 'boss_type:view', group: '老板管理' },
      },
      {
        path: 'wallets',
        name: 'wallets',
        component: () => import('@/views/wallet/index.vue'),
        meta: { title: '钱包管理', icon: 'Wallet', perm: 'wallet:view', group: '老板管理' },
      },
      {
        path: 'recharge-records',
        name: 'recharge-records',
        component: () => import('@/views/recharge/index.vue'),
        meta: { title: '账目记录', icon: 'CreditCard', perm: 'recharge:view', group: '老板管理' },
      },

      // ===== 陪玩管理 =====
      {
        path: 'escorts',
        name: 'escorts',
        component: () => import('@/views/escort/index.vue'),
        meta: { title: '陪玩管理', icon: 'Avatar', perm: 'escort:view', group: '陪玩管理' },
      },
      {
        path: 'escort-levels',
        name: 'escort-levels',
        component: () => import('@/views/escort-level/index.vue'),
        meta: { title: '陪玩等级', icon: 'Rank', perm: 'escort_level:view', group: '陪玩管理' },
      },
      {
        path: 'dispose-records',
        name: 'dispose-records',
        component: () => import('@/views/dispose/index.vue'),
        meta: { title: '奖惩记录', icon: 'Stamp', perm: 'dispose:view', group: '陪玩管理' },
      },
      {
        path: 'audition-links',
        name: 'audition-links',
        component: () => import('@/views/audition/index.vue'),
        meta: { title: '声卡管理', icon: 'Headset', perm: 'audition:view', group: '陪玩管理' },
      },
      {
        path: 'audition-signups',
        name: 'audition-signups',
        component: () => import('@/views/audition/signups.vue'),
        meta: { title: '试音审核', icon: 'Tickets', perm: 'audition:signup_view', group: '陪玩管理' },
      },

      // ===== 运营 =====
      {
        path: 'service-items',
        name: 'service-items',
        component: () => import('@/views/service/index.vue'),
        meta: { title: '游玩项目', icon: 'Goods', perm: 'service:view', group: '运营管理' },
      },
      {
        path: 'transactions',
        name: 'transactions',
        component: () => import('@/views/transaction/index.vue'),
        meta: { title: '流水记录', icon: 'List', perm: 'transaction:view', group: '运营管理' },
      },
      {
        path: 'promotions',
        name: 'promotions',
        component: () => import('@/views/promotion/index.vue'),
        meta: { title: '营销管理', icon: 'Sell', perm: 'promotion:view', group: '运营管理' },
      },
      {
        path: 'coupons',
        name: 'coupons',
        component: () => import('@/views/coupon/index.vue'),
        meta: { title: '优惠券', icon: 'Discount', perm: 'coupon:view', group: '运营管理' },
      },
      {
        path: 'achievements',
        name: 'achievements',
        component: () => import('@/views/achievement/index.vue'),
        meta: { title: '成就配置', icon: 'Trophy', perm: 'achievement:view', group: '运营管理' },
      },
      {
        path: 'checkin',
        name: 'checkin',
        component: () => import('@/views/checkin/index.vue'),
        meta: { title: '签到打卡', icon: 'Calendar', perm: 'checkin:view', group: '运营管理' },
      },
      {
        path: 'banners',
        name: 'banners',
        component: () => import('@/views/banner/index.vue'),
        meta: { title: '首页轮播', icon: 'Picture', perm: 'banner:view', group: '运营管理' },
      },
      {
        path: 'announcements',
        name: 'announcements',
        component: () => import('@/views/announcement/index.vue'),
        meta: { title: '公告管理', icon: 'Bell', perm: 'announcement:view', group: '运营管理' },
      },
      {
        path: 'support-cards',
        name: 'support-cards',
        component: () => import('@/views/support/index.vue'),
        meta: { title: '客服名片', icon: 'Service', perm: 'support:view', group: '运营管理' },
      },
      {
        path: 'chat',
        name: 'chat',
        component: () => import('@/views/chat/index.vue'),
        meta: { title: '消息与客服', icon: 'ChatLineRound', perm: 'chat:view', group: '运营管理' },
      },
      {
        path: 'messages',
        name: 'messages',
        component: () => import('@/views/message/index.vue'),
        meta: { title: '站内消息', icon: 'ChatDotRound', perm: 'message:view', group: '运营管理' },
      },

      // ===== 权限系统 =====
      {
        path: 'admins',
        name: 'admins',
        component: () => import('@/views/system/admin.vue'),
        meta: { title: '客服信息管理', icon: 'UserFilled', perm: 'admin:view', group: '权限系统' },
      },
      {
        path: 'roles',
        name: 'roles',
        component: () => import('@/views/system/role.vue'),
        meta: { title: '职位权限管理', icon: 'Lock', perm: 'role:view', group: '权限系统' },
      },
      {
        path: 'audit-logs',
        name: 'audit-logs',
        component: () => import('@/views/system/audit.vue'),
        meta: { title: '操作审计日志', icon: 'Document', perm: 'audit:view', group: '权限系统' },
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
    try {
      await auth.fetchProfile()
    } catch {
      auth.clear()
      return { path: '/login' }
    }
  }
  const perm = to.meta.perm as string | undefined
  if (perm && !auth.hasPerm(perm)) {
    return { path: '/403' }
  }
  return true
})

export default router
