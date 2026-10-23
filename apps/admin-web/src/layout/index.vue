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

      <button class="ap-nav-search" type="button" @click="openSearch">
        <el-icon><Search /></el-icon><span>搜索功能</span><kbd>⌘ / Ctrl K</kbd>
      </button>
      <nav class="ap-nav" aria-label="功能导航">
        <section v-for="group in navGroups" :key="group.id" class="ap-nav-group"
          :aria-labelledby="'nav-group-' + group.id">
          <h2 :id="'nav-group-' + group.id" class="ap-nav-group-label">{{ group.title }}</h2>
          <div class="ap-nav-links">
            <router-link
              v-for="item in group.centers"
              :key="item.id"
              class="ap-nav-item"
              :to="activeCenter?.id === item.id ? route.fullPath : '/' + item.features[0].path"
              :data-active="activeCenter?.id === item.id"
              :aria-current="activeCenter?.id === item.id ? 'page' : undefined"
              @click="mobileOpen = false"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span class="ap-nav-label">{{ item.title }}</span>
            </router-link>
          </div>
        </section>
      </nav>

      <div class="ap-sidebar-bottom">
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
            :aria-label="mobileOpen ? '关闭主导航' : '打开主导航'"
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
          <button class="ap-search" type="button" @click="openSearch"><el-icon><Search /></el-icon><span>搜索功能</span><kbd>Ctrl K</kbd></button>

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
        </div>
      </header>

      <main class="ap-main">
        <nav v-if="activeCenter && activeCenter.features.length > 1" class="ap-workspace-nav" :aria-label="activeCenter.title + '功能'">
          <router-link v-for="feature in activeCenter.features" :key="feature.path"
            :to="'/' + feature.path" :data-active="route.path === '/' + feature.path"
            :aria-current="route.path === '/' + feature.path ? 'page' : undefined">
            {{ feature.title }}
          </router-link>
        </nav>
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>
    <el-dialog v-model="searchOpen" title="查找功能" width="min(560px, calc(100vw - 24px))"
      class="ap-function-search" append-to-body @opened="searchInput?.focus()" @closed="searchText = ''">
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
import { computed, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, type InputInstance } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { buildNavigation, flattenNavigation, searchNavigation } from '@/router/navigation'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const mobileOpen = ref(false)
const searchText = ref('')
const searchOpen = ref(false)
const searchInput = ref<InputInstance>()
const firstSearchResult = ref<HTMLButtonElement | null>(null)

function closeMobileNav() {
  mobileOpen.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeMobileNav()
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    openSearch()
  }
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

const pageTitle = computed(() => activeCenter.value?.title || (route.meta?.title as string) || '')
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
const notificationItems = computed(() => flattenNavigation(navGroups.value).filter(item => ['messages', 'chat'].includes(item.path)))
const searchResults = computed(() => searchNavigation(navGroups.value, searchText.value))
function openSearch() {
  mobileOpen.value = false
  searchOpen.value = true
}
function setSearchResultRef(element: Element | ComponentPublicInstance | null, index: number) {
  if (index === 0) firstSearchResult.value = element as HTMLButtonElement | null
}
function goToFeature(path: string) {
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
  min-height: 100dvh;
  background: var(--background);
  color: var(--foreground);
}

/* ---- Sidebar ---- */
.ap-sidebar {
  position: sticky;
  top: 0;
  align-self: flex-start;
  flex: 0 0 224px;
  width: 224px;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--sidebar);
  border-right: 1px solid var(--sidebar-border);
  overflow: hidden;
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
  font-size: 12px;
  line-height: 1.3;
  color: var(--muted-foreground);
  white-space: nowrap;
}

/* ---- Navigation ---- */
.ap-nav-search { display: none; }
.ap-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  scrollbar-width: thin;
  scrollbar-color: var(--sidebar-border) transparent;
  padding: 8px 12px 12px;
}
.ap-nav-group + .ap-nav-group {
  margin-top: 8px;
}
.ap-nav-group-label {
  margin: 0;
  padding: 0 12px 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  letter-spacing: 0.04em;
  color: var(--muted-foreground);
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
  gap: 10px;
  min-height: 36px;
  margin: 0;
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
  display: grid;
  gap: 4px;
  flex: 0 0 auto;
  margin-top: auto;
  padding: 12px;
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
  height: var(--control-height-md);
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
    min-height: 44px;
  }
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
    padding: 12px;
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
  .ap-workspace-nav a { padding: 8px 10px; min-height: 40px; }
}
</style>
