<template>
  <div class="ap-shell">
    <!-- 移动端遮罩 -->
    <div v-if="mobileOpen" class="ap-scrim" @click="mobileOpen = false" />

    <!-- 侧边栏 -->
    <aside class="ap-sidebar" :class="{ 'is-open': mobileOpen }" aria-label="主导航">
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
      </div>

      <nav class="ap-nav" aria-label="功能导航">
        <template v-for="group in navGroups" :key="group.name">
          <p class="ap-nav-group-label">{{ group.name }}</p>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            class="ap-nav-item"
            :to="'/' + item.path"
            :data-active="activeMenu === '/' + item.path"
            @click="mobileOpen = false"
          >
            <el-icon v-if="item.meta?.icon"><component :is="item.meta.icon" /></el-icon>
            <span class="ap-nav-label">{{ item.meta?.title }}</span>
          </router-link>
        </template>
      </nav>

      <div class="ap-sidebar-bottom">
        <div class="ap-sidebar-divider" role="presentation" />
        <a class="ap-nav-item" href="#" @click.prevent="toggleTheme">
          <el-icon><component :is="isDark ? 'Sunny' : 'Moon'" /></el-icon>
          <span class="ap-nav-label">{{ isDark ? '浅色模式' : '深色模式' }}</span>
        </a>
        <a class="ap-nav-item" href="#" @click.prevent="onLogout">
          <el-icon><SwitchButton /></el-icon>
          <span class="ap-nav-label">退出登录</span>
        </a>
      </div>
    </aside>

    <!-- 内容列 -->
    <div class="ap-content">
      <header class="ap-topbar">
        <div class="ap-topbar-left">
          <button
            class="ap-menu-toggle"
            type="button"
            aria-label="打开主导航"
            :aria-expanded="mobileOpen"
            @click="mobileOpen = !mobileOpen"
          >
            <el-icon><Fold /></el-icon>
          </button>
          <div class="ap-topbar-titles">
            <span class="ap-breadcrumb">{{ breadcrumb }}</span>
            <h1 class="ap-page-title">{{ pageTitle }}</h1>
          </div>
        </div>

        <div class="ap-topbar-right">
          <el-input
            v-model="searchText"
            class="ap-search"
            placeholder="搜索菜单 / 页面"
            :prefix-icon="Search"
            clearable
            @keyup.enter="onSearch"
          />

          <el-dropdown trigger="click" @command="onNavCommand">
            <button class="ap-icon-btn" type="button" aria-label="通知消息">
              <el-icon><Bell /></el-icon>
              <span class="ap-badge-dot" aria-hidden="true" />
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="messages">站内消息</el-dropdown-item>
                <el-dropdown-item command="chat">消息与客服</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-dropdown trigger="click" @command="onCommand">
            <div class="ap-admin">
              <span class="ap-avatar" aria-hidden="true">
                {{ (auth.profile?.nickname || '管').charAt(0) }}
              </span>
              <span class="ap-admin-text">
                <span class="ap-admin-name">{{ auth.profile?.nickname || '管理员' }}</span>
                <span class="ap-admin-role">{{ roleName }}</span>
              </span>
              <el-icon class="ap-admin-caret"><CaretBottom /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="ap-main">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const mobileOpen = ref(false)
const searchText = ref('')

function closeMobileNav() {
  mobileOpen.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeMobileNav()
}

function onViewportChange() {
  if (window.innerWidth > 900) closeMobileNav()
}

watch(() => route.fullPath, closeMobileNav)
watch(mobileOpen, (open) => {
  document.body.classList.toggle('ap-nav-open', open)
})

onMounted(() => {
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

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta?.title as string) || '')
const breadcrumb = computed(() => {
  const group = (route.meta?.group as string) || ''
  return ['首页', group, pageTitle.value].filter(Boolean).join(' / ')
})
const roleName = computed(() =>
  auth.isSuperuser ? '超级管理员' : auth.profile?.role?.name || '客服',
)

// 按 meta.group 分组，过滤隐藏项与无权限项，保留路由声明顺序
const navGroups = computed(() => {
  const root = router.getRoutes().find((r) => r.path === '/')
  const children = (root?.children || []) as any[]
  const visible = children.filter(
    (c) => !c.meta?.hidden && (!c.meta?.perm || auth.hasPerm(c.meta.perm)),
  )
  const groups: { name: string; items: any[] }[] = []
  for (const item of visible) {
    const name = item.meta?.group || '其他'
    let g = groups.find((x) => x.name === name)
    if (!g) {
      g = { name, items: [] }
      groups.push(g)
    }
    g.items.push(item)
  }
  return groups
})

function onSearch() {
  const kw = searchText.value.trim()
  if (!kw) return
  const root = router.getRoutes().find((r) => r.path === '/')
  const children = (root?.children || []) as any[]
  const hit = children.find(
    (c) =>
      !c.meta?.hidden &&
      (!c.meta?.perm || auth.hasPerm(c.meta.perm)) &&
      (c.meta?.title as string)?.includes(kw),
  )
  if (hit) {
    router.push('/' + hit.path)
    searchText.value = ''
    mobileOpen.value = false
  }
}

function onNavCommand(command: string) {
  router.push('/' + command)
}

function onCommand(command: string) {
  if (command === 'logout') onLogout()
}

function onLogout() {
  ElMessageBox.confirm('确定退出登录吗？', '提示', { type: 'warning' })
    .then(() => {
      auth.clear()
      router.replace('/login')
    })
    .catch(() => {})
}
</script>

<style scoped lang="scss">
.ap-shell {
  display: flex;
  min-height: 100vh;
  background: var(--background);
  color: var(--foreground);
}

/* ---- Sidebar ---- */
.ap-sidebar {
  position: sticky;
  top: 0;
  align-self: flex-start;
  flex: 0 0 176px;
  width: 176px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: color-mix(in srgb, var(--sidebar) 90%, transparent);
  backdrop-filter: blur(24px) saturate(160%);
  border-right: 1px solid var(--sidebar-border);
  overflow-y: auto;
}
.ap-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  flex: 0 0 auto;
  padding: 0 20px;
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
  display: flex;
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
  font-size: 11px;
  line-height: 1.3;
  color: var(--muted-foreground);
  white-space: nowrap;
}

/* ---- Navigation ---- */
.ap-nav {
  display: flex;
  flex-direction: column;
  padding-bottom: 8px;
}
.ap-nav-group-label {
  padding: 16px 22px 6px;
  margin: 0;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--muted-foreground);
}
.ap-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  margin: 2px 12px;
  padding: 0 14px;
  border-radius: calc(var(--radius) * 0.6);
  color: var(--sidebar-foreground);
  opacity: 0.82;
  font-size: 14px;
  line-height: 1;
  text-decoration: none;
  transition: background-color 0.18s ease, color 0.18s ease, opacity 0.18s ease;
}
.ap-nav-item .el-icon {
  flex: 0 0 20px;
  font-size: 18px;
}
.ap-nav-label {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
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
  margin-top: auto;
  padding-bottom: 14px;
}
.ap-sidebar-divider {
  height: 1px;
  margin: 8px 20px;
  background: var(--sidebar-border);
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
  background: var(--card);
  border-bottom: 1px solid var(--border);
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
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
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
  outline: none;
}
.ap-topbar-titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ap-breadcrumb {
  font-size: 11px;
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
  width: 260px;
}
.ap-search :deep(.el-input__wrapper) {
  border-radius: 999px;
}

.ap-icon-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
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
.ap-badge-dot {
  position: absolute;
  top: 8px;
  right: 9px;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--destructive);
  border: 2px solid var(--card);
}

.ap-admin {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  outline: none;
}
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
  font-size: 11px;
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
  padding: 28px 32px;
  background: var(--background);
}

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
  .ap-menu-toggle {
    display: inline-flex;
  }
  .ap-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    z-index: 40;
    transform: translateX(-100%);
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
    background: rgba(0, 0, 0, 0.4);
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
  .ap-admin-caret { display: none; }
  .ap-main {
    padding: 16px 12px calc(20px + env(safe-area-inset-bottom));
  }
  .ap-search {
    display: none;
  }
}
</style>
