<template>
  <div class="ap-shell">
    <a class="ap-skip-link" href="#ap-main-content">跳到页面内容</a>
    <!-- 移动端遮罩 -->
    <div v-if="mobileOpen" class="ap-scrim" @click="mobileOpen = false" />


    <!-- 内容列 -->
    <div class="ap-content">
      <header class="ap-topbar">
        <div class="ap-topbar-left">
          <div class="ap-topbar-titles">
            <span class="ap-breadcrumb">{{ breadcrumb }}</span>
            <h1 class="ap-page-title">{{ pageTitle }}</h1>
          </div>
        </div>

        <div class="ap-topbar-right">
          <button class="ap-search" type="button" @click="openSearch"><el-icon><Search /></el-icon><span>搜索功能</span><kbd>Ctrl K</kbd></button>

          <button v-if="canDispatch" class="ap-staff-queue" type="button"
            title="数量在订单变化时及约每分钟更新，详情以待派列表为准" @click="openStaffQueue">
            <span role="status" aria-atomic="true">{{ staffQueueLabel }}</span>
          </button>

          <el-dropdown v-if="notificationItems.length" trigger="click" @command="onNavCommand">
            <button class="ap-icon-btn" type="button" aria-label="通知消息">
              <el-icon><Bell /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="item in notificationItems" :key="item.path" :command="item.path">{{ item.title }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-dropdown trigger="click" @command="onCommand">
            <button class="ap-admin" type="button" aria-label="管理员账户菜单">
              <span class="ap-avatar" aria-hidden="true">
                {{ (auth.profile?.nickname || '管').charAt(0) }}
              </span>
              <span class="ap-admin-text">
                <span class="ap-admin-name">{{ auth.profile?.nickname || '管理员' }}</span>
                <span class="ap-admin-role">{{ roleName }}</span>
              </span>
              <el-icon class="ap-admin-caret"><CaretBottom /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <button ref="mobileToggle" class="ap-menu-toggle" type="button" :aria-label="mobileOpen ? '关闭主导航' : '打开主导航'"
            aria-controls="ap-primary-navigation" :aria-expanded="mobileOpen" @click="mobileOpen = !mobileOpen"><el-icon><Menu /></el-icon></button>
        </div>
      </header>

      <main id="ap-main-content" class="ap-main" tabindex="-1">
        <nav v-if="activeCenter && activeCenter.features.length > 1" class="ap-workspace-nav" :aria-label="activeCenter.title + '功能'">
          <router-link v-for="feature in activeCenter.features" :key="feature.path"
            :to="'/' + feature.path" :data-active="route.path === '/' + feature.path"
            :aria-current="route.path === '/' + feature.path ? 'page' : undefined">
            {{ feature.title }}
          </router-link>
        </nav>
        <router-view v-slot="{ Component }">
          <transition name="ocean-page" mode="out-in">
            <div :key="route.path" class="ocean-route-surface"><component :is="Component" /></div>
          </transition>
        </router-view>
      </main>
    </div>
    <!-- 侧边栏 -->
    <aside id="ap-primary-navigation" ref="sidebar" class="ap-sidebar" :class="{ 'is-open': mobileOpen }" aria-label="主导航"
      :inert="isMobileNav && !mobileOpen || undefined" :aria-hidden="isMobileNav && !mobileOpen ? 'true' : undefined">
      <div class="ap-brand">
        <span class="ap-brand-logo" aria-hidden="true">
          <svg class="ap-brand-mark" viewBox="0 0 1456 816" fill="currentColor">
            <polygon points="300,200 470,200 610,315 555,360 430,320 300,320" />
            <polygon points="430,320 600,320 230,715 60,715" />
            <polygon points="760,95 835,95 560,715 485,715" />
            <polygon points="855,95 945,95 675,715 585,715" />
            <polygon points="875,95 1015,95 1345,715 1205,715 1150,595 1255,595 1075,470 1180,470 1010,340" />
          </svg>
        </span>
        <span class="ap-brand-text">
          <span class="ap-brand-name">兴安电竞</span>
          <span class="ap-brand-caption">运营管理后台</span>
        </span>
        <button class="ap-nav-close" type="button" aria-label="关闭主导航" @click="closeMobileNav">
          <el-icon><Close /></el-icon>
        </button>
      </div>

      <button ref="mobileNavSearch" class="ap-nav-search" type="button" @click="openSearch">
        <el-icon><Search /></el-icon><span>搜索功能</span><kbd>⌘ / Ctrl K</kbd>
      </button>
      <el-tooltip v-if="canDispatch" content="快捷派单" placement="left" :trigger="['hover', 'focus']" :disabled="mobileOpen">
        <button class="ap-rail-quick" type="button" aria-label="快捷派单" @click="openQuickDispatch">
          <el-icon><Plus /></el-icon><span>快捷派单</span>
        </button>
      </el-tooltip>
      <nav ref="navScroll" class="ap-nav" aria-label="功能导航">
        <section v-for="group in navGroups" :key="group.id" class="ap-nav-group"
          :aria-labelledby="'nav-group-' + group.id">
          <h2 :id="'nav-group-' + group.id" class="ap-nav-group-label">{{ group.title }}</h2>
          <div class="ap-nav-links">
            <el-tooltip v-for="item in group.centers" :key="item.id" :content="item.title" placement="left" :trigger="['hover', 'focus']" :disabled="mobileOpen">
              <router-link
                class="ap-nav-item"
                :to="activeCenter?.id === item.id ? route.fullPath : '/' + item.features[0].path"
                :data-active="activeCenter?.id === item.id"
                :aria-current="activeCenter?.id === item.id ? 'page' : undefined"
                :aria-label="item.title"
                @click="mobileOpen = false"
              >
                <el-icon><component :is="item.icon" /></el-icon>
                <span class="ap-nav-label">{{ item.title }}</span>
              </router-link>
            </el-tooltip>
          </div>
        </section>
      </nav>

      <div class="ap-sidebar-bottom">
        <el-tooltip :content="isDark ? '浅色模式' : '深色模式'" placement="left" :trigger="['hover', 'focus']" :disabled="mobileOpen"><a class="ap-nav-item" href="#" :aria-label="isDark ? '浅色模式' : '深色模式'" @click.prevent="toggleTheme">
          <el-icon><component :is="isDark ? 'Sunny' : 'Moon'" /></el-icon>
          <span class="ap-nav-label">{{ isDark ? '浅色模式' : '深色模式' }}</span>
        </a></el-tooltip>
        <el-tooltip content="退出登录" placement="left" :trigger="['hover', 'focus']" :disabled="mobileOpen"><a class="ap-nav-item" href="#" aria-label="退出登录" @click.prevent="onLogout">
          <el-icon><SwitchButton /></el-icon>
          <span class="ap-nav-label">退出登录</span>
        </a></el-tooltip>
      </div>
    </aside>
    <el-dialog v-model="searchOpen" title="查找功能" width="min(560px, calc(100vw - 24px))"
      class="ap-function-search" append-to-body @opened="searchInput?.focus()" @closed="onSearchClosed">
      <el-input ref="searchInput" v-model="searchText" placeholder="搜索功能，例如：注册、提现、声卡" :prefix-icon="Search" clearable
        aria-label="搜索后台功能" @keydown.enter.prevent="onSearchEnter" @keydown.down.prevent="firstSearchResult?.focus()" />
      <p class="ap-search-summary">{{ searchText.trim() ? '找到 ' + searchResults.length + ' 项功能' : '按业务分类浏览全部可访问功能' }}</p>
      <div class="ap-search-results" aria-label="功能搜索结果">
        <button v-for="(item, index) in searchResults" :key="item.path" :ref="element => setSearchResultRef(element, index)"
          type="button" class="ap-search-result" @click="goToFeature(item.path)">
          <strong>{{ item.title }}</strong><span>{{ item.domain }} / {{ item.center }}</span><el-icon><ArrowRight /></el-icon>
        </button>
        <el-empty v-if="!searchResults.length" :image-size="60" description="没有匹配功能，请尝试其他关键词" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, type InputInstance } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useStaffDispatchQueue } from '@/stores/staffDispatchQueue'
import { buildNavigation, flattenNavigation, searchNavigation } from '@/router/navigation'

const auth = useAuthStore()
const staffQueue = useStaffDispatchQueue()
const route = useRoute()
const router = useRouter()
const canDispatch = computed(() => Boolean(auth.token) && auth.hasPerm('order:view') && auth.hasPerm('order:dispatch'))
const staffQueueLabel = computed(() => {
  if (staffQueue.status === 'error') return '客服待派 · 查询失败'
  if (staffQueue.status === 'review') return '客服待派 · 部分待核对'
  if (staffQueue.status !== 'ready') return '客服待派 · 更新中'
  return `客服待派 ${staffQueue.count ?? 0} 单`
})
function openStaffQueue() {
  if (canDispatch.value) void router.push({ name: 'orders', query: { queue: staffQueue.status === 'review' ? 'staff_review' : 'staff' } })
}
function openQuickDispatch() {
  if (!canDispatch.value) return
  mobileOpen.value = false
  void router.push({ name: 'orders', query: { quick_dispatch: '1' } })
}

const mobileOpen = ref(false)
const logoutConfirmOpen = ref(false)
const isMobileNav = ref(window.innerWidth <= 900)
const mobileToggle = ref<HTMLButtonElement | null>(null)
const mobileNavSearch = ref<HTMLButtonElement | null>(null)
const sidebar = ref<HTMLElement | null>(null)
const navScroll = ref<HTMLElement | null>(null)
const searchText = ref('')
const searchOpen = ref(false)
const searchReturnToMobileToggle = ref(false)
const searchInput = ref<InputInstance>()
const firstSearchResult = ref<HTMLButtonElement | null>(null)

function closeMobileNav() {
  mobileOpen.value = false
}

// Search and deep links can select a center below the visible part of the rail.
// Scroll only the navigation itself so the current location stays discoverable.
function revealActiveNav() {
  void nextTick(() => {
    const nav = navScroll.value
    const active = nav?.querySelector<HTMLElement>('[data-active="true"]')
    if (!nav || !active) return
    const viewport = nav.getBoundingClientRect()
    const item = active.getBoundingClientRect()
    if (item.top < viewport.top) nav.scrollTop += item.top - viewport.top
    else if (item.bottom > viewport.bottom) nav.scrollTop += item.bottom - viewport.bottom
  })
}

function onKeydown(event: KeyboardEvent) {
  if (logoutConfirmOpen.value) return
  if (event.key === 'Escape') closeMobileNav()
  if (event.key === 'Tab' && mobileOpen.value && isMobileNav.value && sidebar.value) {
    const targets = [...sidebar.value.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]')].filter(element => element.getClientRects().length > 0)
    if (targets.length) {
      const index = targets.indexOf(document.activeElement as HTMLElement)
      if ((event.shiftKey && index <= 0) || (!event.shiftKey && (index === -1 || index === targets.length - 1))) {
        event.preventDefault()
        ;(event.shiftKey ? targets[targets.length - 1] : targets[0]).focus()
      }
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    openSearch()
  }
}

function onViewportChange() {
  isMobileNav.value = window.innerWidth <= 900
  if (!isMobileNav.value) closeMobileNav()
}

watch(() => route.fullPath, closeMobileNav)
watch(mobileOpen, (open) => {
  const restoreFocus = !open && sidebar.value?.contains(document.activeElement)
  document.body.classList.toggle('ap-nav-open', open)
  if (open && isMobileNav.value) {
    revealActiveNav()
    void nextTick(() => requestAnimationFrame(() => { if (mobileOpen.value && isMobileNav.value) mobileNavSearch.value?.focus() }))
  }
  else if (restoreFocus && isMobileNav.value && !searchOpen.value) void nextTick(() => mobileToggle.value?.focus())
}, { flush: 'sync' })

onMounted(() => {
  revealActiveNav()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onViewportChange, { passive: true })
})
onBeforeUnmount(() => {
  document.body.classList.remove('ap-nav-open')
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onViewportChange)
})

// 主题切换（对齐设计稿 light/dark）
const THEME_KEY = 'xa_admin_theme'
const isDark = ref(localStorage.getItem(THEME_KEY) === 'dark')
applyTheme()
function applyTheme() {
  document.documentElement.classList.toggle('dark', isDark.value)
}
function toggleTheme() {
  isDark.value = !isDark.value
  localStorage.setItem(THEME_KEY, isDark.value ? 'dark' : 'light')
  applyTheme()
}

const pageTitle = computed(() => (route.meta?.title as string) || activeCenter.value?.title || '')
const breadcrumb = computed(() => {
  const group = (route.meta?.group as string) || ''
  return [...new Set(['首页', group, pageTitle.value, route.meta.title as string].filter(Boolean))].join(' / ')
})
const roleName = computed(() =>
  auth.isSuperuser ? '超级管理员' : auth.profile?.role?.name || '客服',
)

const navGroups = computed(() => {
  const root = router.getRoutes().find((r) => r.path === '/')
  return buildNavigation(root?.children || [], code => auth.hasPerm(code))
})
const activeCenter = computed(() => navGroups.value.flatMap(group => group.centers)
  .find(center => center.id === route.meta.center))
watch(() => activeCenter.value?.id, revealActiveNav, { flush: 'post' })
const notificationItems = computed(() => flattenNavigation(navGroups.value).filter(item => ['messages', 'chat'].includes(item.path)))
const searchResults = computed(() => searchNavigation(navGroups.value, searchText.value))
function openSearch() {
  searchReturnToMobileToggle.value = mobileOpen.value && isMobileNav.value
  searchOpen.value = true
  mobileOpen.value = false
}
function onSearchClosed() {
  searchText.value = ''
  if (searchReturnToMobileToggle.value) {
    requestAnimationFrame(() => {
      const active = document.activeElement
      if (!mobileOpen.value && !searchOpen.value && isMobileNav.value && (active === document.body || sidebar.value?.contains(active))) {
        mobileToggle.value?.focus()
      }
    })
  }
  searchReturnToMobileToggle.value = false
}
function setSearchResultRef(element: Element | ComponentPublicInstance | null, index: number) {
  if (index === 0) firstSearchResult.value = element as HTMLButtonElement | null
}
function goToFeature(path: string) {
  searchReturnToMobileToggle.value = false
  router.push('/' + path)
  searchOpen.value = false
}
function onSearchEnter() {
  if (searchResults.value.length === 1) goToFeature(searchResults.value[0].path)
  else firstSearchResult.value?.focus()
}

function onNavCommand(command: string) {
  router.push('/' + command)
}

function onCommand(command: string) {
  if (command === 'logout') onLogout()
}

function onLogout() {
  if (logoutConfirmOpen.value) return
  logoutConfirmOpen.value = true
  ElMessageBox.confirm('确定退出登录吗？', '提示', { type: 'warning' })
    .then(() => {
      auth.clear()
      router.replace('/login')
    })
    .catch(() => {})
    // Keep the navigation suspended until the dismissing key event has finished bubbling.
    .finally(() => { requestAnimationFrame(() => { logoutConfirmOpen.value = false }) })
}
</script>

<style scoped lang="scss">
.ap-shell {
  display: flex;
  min-height: 100dvh;
  background: var(--background);
  color: var(--foreground);
}
.ap-skip-link {
  position: fixed;
  top: 8px;
  left: 8px;
  z-index: 100;
  padding: 8px 12px;
  border-radius: var(--control-radius);
  background: var(--primary);
  color: var(--primary-foreground);
  transform: translateY(-160%);
}
.ap-skip-link:focus { transform: translateY(0); }

/* ---- Sidebar ---- */
.ap-sidebar {
  position: sticky;
  top: 0;
  align-self: flex-start;
  flex: 0 0 88px;
  width: 88px;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--sidebar);
  border-left: 1px solid var(--sidebar-border);
  overflow: hidden;
}
.ap-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  flex: 0 0 auto;
  justify-content: center;
  padding: 0 8px;
}
.ap-brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  border-radius: calc(var(--radius) * 0.6);
  background: var(--primary);
  color: var(--primary-foreground);
  box-shadow: var(--shadow-sm);
  font-size: 20px;
}
.ap-brand-mark {
  width: 24px;
  height: 24px;
  display: block;
}
.ap-brand-text {
  display: none;
  flex-direction: column;
  min-width: 0;
}
.ap-brand-name {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--sidebar-foreground);
  white-space: nowrap;
}
.ap-brand-caption {
  font-size: 12px;
  line-height: 1.3;
  color: var(--muted-foreground);
  white-space: nowrap;
}
.ap-nav-close { display: none; }

/* ---- Navigation ---- */
.ap-nav-search { display: none; }
.ap-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  scrollbar-width: thin;
  scrollbar-color: var(--sidebar-border) transparent;
  padding: 8px 8px 12px;
}
.ap-nav-group + .ap-nav-group {
  margin-top: 8px;
}
.ap-nav-group-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.ap-nav-links {
  display: grid;
  gap: 2px;
}
.ap-nav-item:focus-visible,
.ap-workspace-nav a:focus-visible,
.ap-search:focus-visible,
.ap-nav-search:focus-visible,
.ap-search-result:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: -2px;
}
.ap-nav-item {
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  min-height: 40px;
  margin: 0;
  padding: 5px 4px;
  border-radius: calc(var(--radius) * 0.6);
  color: var(--sidebar-foreground);
  opacity: 0.82;
  font-size: 12px;
  line-height: 1.2;
  text-decoration: none;
  transition: background-color 0.18s ease, color 0.18s ease, opacity 0.18s ease;
}
.ap-nav-item .el-icon {
  flex: 0 0 auto;
  font-size: 18px;
}
.ap-nav-label {
  max-height: 0;
  opacity: 0;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: max-height 0.18s ease, opacity 0.18s ease;
}
.ap-nav-item[data-active='true'] .ap-nav-label { max-height: 2em; opacity: 1; }
.ap-rail-quick { display: flex; align-items: center; justify-content: center; gap: 8px; flex: 0 0 var(--control-height-sm); height: var(--control-height-sm); margin: 4px 16px; padding: 0 8px; border: 0; border-radius: var(--control-radius); background: var(--primary); color: var(--primary-foreground); cursor: pointer; font: inherit; }
.ap-rail-quick .el-icon { font-size: 18px; }
.ap-rail-quick span:not(.el-icon) { display: none; }
.ap-rail-quick:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.ap-nav-item:hover {
  background: var(--sidebar-accent);
  opacity: 1;
}
.ap-nav-item[data-active='true'] {
  background: var(--primary);
  color: var(--primary-foreground);
  font-weight: 600;
  opacity: 1;
  box-shadow: var(--shadow-sm);
}

.ap-sidebar-bottom {
  display: grid;
  gap: 4px;
  flex: 0 0 auto;
  margin-top: auto;
  padding: 8px;
  border-top: 1px solid var(--sidebar-border);
}

/* ---- Content column ---- */
.ap-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

/* ---- Topbar ---- */
.ap-topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  height: 64px;
  flex: 0 0 64px;
  padding: 0 32px;
  background: color-mix(in srgb, var(--background) 56%, transparent);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid transparent;
}
.ap-topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.ap-menu-toggle {
  display: none;
  align-items: center;
  justify-content: center;
  flex: 0 0 var(--control-height-sm);
  width: var(--control-height-sm);
  height: var(--control-height-sm);
  padding: 0;
  border: 0;
  border-radius: 10px;
  background: transparent;
  font-size: 22px;
  cursor: pointer;
  color: var(--icon);
}
.ap-menu-toggle:hover,
.ap-menu-toggle:focus-visible {
  background: var(--secondary);
}
.ap-topbar-titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ap-breadcrumb {
  font-size: 12px;
  color: var(--muted-foreground);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ap-page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--foreground);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ap-topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 0 0 auto;
}
.ap-search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 220px;
  height: var(--control-height-sm);
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--control-radius);
  color: var(--muted-foreground);
  background: var(--secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.ap-search kbd,
.ap-nav-search kbd {
  margin-left: auto;
  font: inherit;
  font-size: 10px;
  opacity: 0.8;
  white-space: nowrap;
}

.ap-staff-queue {
  display: inline-flex;
  align-items: center;
  min-height: var(--control-height-sm);
  max-width: 190px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--control-radius);
  background: var(--secondary);
  color: var(--foreground);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.ap-staff-queue span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ap-staff-queue:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

.ap-icon-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 var(--control-height-sm);
  width: var(--control-height-sm);
  height: var(--control-height-sm);
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--icon);
  cursor: pointer;
  transition: background-color 0.18s ease;
}
.ap-icon-btn .el-icon {
  font-size: 20px;
}
.ap-icon-btn:hover {
  background: var(--secondary);
}
.ap-admin {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px;
  border: 1px solid transparent;
  border-radius: var(--control-radius);
  background: transparent;
  color: var(--foreground);
  text-align: left;
  cursor: pointer;
}
.ap-admin:hover { background: var(--secondary); }
.ap-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  border-radius: 999px;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 14px;
  font-weight: 600;
}
.ap-admin-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.ap-admin-name {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--foreground);
  white-space: nowrap;
}
.ap-admin-role {
  font-size: 12px;
  line-height: 1.2;
  color: var(--muted-foreground);
  white-space: nowrap;
}
.ap-admin-caret {
  color: var(--muted-foreground);
}

/* ---- Main ---- */
.ap-main {
  flex: 1;
  min-width: 0;
  width: 100%;
  max-width: 1664px;
  margin: 0 auto;
  padding: 28px 32px;
  background: var(--background);
}
.ap-workspace-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px;
  margin-bottom: 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
}
.ap-workspace-nav a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 38px;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--muted-foreground);
  text-decoration: none;
}
.ap-workspace-nav a:hover { background: var(--secondary); color: var(--foreground); }
.ap-workspace-nav a[data-active='true'] {
  background: var(--sidebar-accent);
  color: var(--primary);
  font-weight: 600;
}
.ap-search-summary { margin: 16px 0 8px; font-size: 12px; color: var(--muted-foreground); }
.ap-search-results { max-height: min(55dvh, 450px); overflow-y: auto; }
.ap-search-result {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 12px;
  width: 100%;
  padding: 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--foreground);
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.ap-search-result:hover, .ap-search-result:focus-visible { background: var(--secondary); }
.ap-search-result strong { font-size: 14px; font-weight: 600; }
.ap-search-result span { grid-column: 1; font-size: 12px; color: var(--muted-foreground); }
.ap-search-result .el-icon { grid-column: 2; grid-row: 1 / 3; align-self: center; color: var(--muted-foreground); }

.ap-scrim {
  display: none;
}

/* ---- 响应式 ---- */
@media (max-width: 1180px) {
  .ap-topbar {
    padding: 0 20px;
  }
  .ap-main {
    padding: 24px 20px;
  }
  .ap-search {
    width: 200px;
  }
  .ap-admin-text {
    display: none;
  }
}

@media (max-width: 900px) {
  .ap-nav-item {
    flex-direction: row;
    justify-content: flex-start;
    gap: 10px;
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
  }
  .ap-nav-label { max-height: 2em; opacity: 1; }
  .ap-search { width: 156px; }
  .ap-nav-group-label { position: static; width: auto; height: auto; overflow: visible; clip-path: none; margin: 0; padding: 0 12px 6px; color: var(--muted-foreground); font-size: 12px; line-height: 18px; }
  .ap-brand { justify-content: flex-start; padding: 0 20px; }
  .ap-brand-text { display: flex; }
  .ap-nav-close { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 44px; width: 44px; height: 44px; margin-left: auto; padding: 0; border: 0; border-radius: var(--control-radius); background: transparent; color: var(--sidebar-foreground); font-size: 20px; cursor: pointer; }
  .ap-nav-close:hover { background: var(--sidebar-accent); }
  .ap-nav-close:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
  .ap-rail-quick { justify-content: flex-start; flex-basis: 44px; height: 44px; margin: 8px 12px 0; padding: 0 12px; }
  .ap-rail-quick span:not(.el-icon) { display: inline; }
  .ap-nav-search { display: flex; align-items: center; gap: 8px; height: 44px; margin: 4px 12px 0; padding: 0 10px; border: 1px solid var(--sidebar-border); border-radius: 8px; background: var(--sidebar-accent); color: var(--sidebar-foreground); text-align: left; font: inherit; font-size: 13px; cursor: pointer; }
  .ap-menu-toggle {
    display: inline-flex;
  }
  .ap-sidebar {
    position: fixed;
    top: 0;
    left: auto;
    right: 0;
    width: min(82vw, 300px);
    z-index: 40;
    transform: translateX(100%);
    transition: transform 0.24s ease;
    box-shadow: var(--shadow-xl);
  }
  .ap-sidebar.is-open {
    transform: translateX(0);
  }
  .ap-scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 30;
    background: var(--xa-color-overlay);
  }
}

@media (max-width: 720px) {
  .ap-sidebar {
    width: min(82vw, 300px);
    flex-basis: min(82vw, 300px);
    padding-bottom: env(safe-area-inset-bottom);
  }
  .ap-topbar {
    height: calc(60px + env(safe-area-inset-top));
    flex-basis: calc(60px + env(safe-area-inset-top));
    padding: env(safe-area-inset-top) 12px 0;
    gap: 8px;
  }
  .ap-topbar-left { gap: 8px; }
  .ap-page-title { font-size: 18px; }
  .ap-breadcrumb { display: none; }
  .ap-topbar-right { gap: 8px; }
  .ap-staff-queue { max-width: 132px; min-height: var(--control-height-sm); padding: 0 8px; font-size: 12px; }
  .ap-admin-caret { display: none; }
  .ap-main {
    padding: 16px 12px calc(20px + env(safe-area-inset-bottom));
  }
  .ap-search {
    display: none;
  }
  .ap-nav-search {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 4px 12px 0;
    height: 44px;
    padding: 0 10px;
    border: 1px solid var(--sidebar-border);
    border-radius: 8px;
    background: var(--sidebar-accent);
    color: var(--sidebar-foreground);
    text-align: left;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .ap-workspace-nav { gap: 4px; padding: 4px; margin-bottom: 16px; }
  .ap-workspace-nav a { padding: 6px 10px; min-height: var(--control-height-sm); }
}
@media (prefers-reduced-motion: reduce) {
  .ap-sidebar, .ap-nav-item, .ap-nav-label { transition: none; }
}
</style>
