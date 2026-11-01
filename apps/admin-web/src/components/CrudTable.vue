<template>
  <div class="page-container">
    <div class="page-toolbar">
      <slot name="toolbar" :reload="reload" />
    </div>
    <el-alert v-if="loadFailed && !externalError" title="数据加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refresh">重新加载</el-button>
    </el-alert>

    <div v-if="selectable" class="table-selection-bar">
      <span role="status" aria-live="polite">{{ selectedRows.length ? `已选 ${selectedRows.length} 条，可复制订单号核对` : '勾选本页订单，集中复制订单号' }}</span>
      <div>
        <el-checkbox v-if="isMobile && $slots.mobile" :model-value="allMobileSelected" :indeterminate="someMobileSelected" :disabled="!rows.length || loading" @change="toggleAllMobile">选择本页</el-checkbox>
        <el-tooltip content="仅复制选中的订单号，不会更改订单状态" placement="top" :trigger="['hover', 'focus']">
          <el-button :disabled="!selectedRows.length" @click="copySelection">复制订单号</el-button>
        </el-tooltip>
        <el-button text :disabled="!selectedRows.length" @click="clearSelection">清空选择</el-button>
      </div>
    </div>
    <div v-if="isMobile && $slots.mobile && !loading && !loadFailed" class="mobile-result-count" role="status" aria-live="polite">共 {{ total.toLocaleString() }} 条<span v-if="total"> · 本页 {{ rows.length }} 条</span></div>
    <div v-if="isMobile && $slots.mobile" v-loading="loading" class="mobile-records">
      <slot v-if="rows.length" name="mobile" :rows="rows" :selected="selectedRows" :toggle="toggleMobile" />
      <el-empty v-else-if="!loading" :image-size="72" :description="loadFailed ? (externalError ? '筛选范围待确认' : '数据暂时无法显示，请重新加载') : '暂无符合条件的记录'" />
      <span v-else role="status">正在加载记录…</span>
    </div>
    <div v-else ref="tableHost" class="table-host" :class="{ 'table-host--scroll': tableScroll }" :role="tableScroll ? 'region' : undefined" :aria-label="tableScroll ? '数据表格' : undefined">
    <div v-if="tableScroll && hasHorizontalOverflow && rows.length" class="table-scroll-controls">
      <span>查看更多字段</span>
      <el-button-group>
        <el-button :disabled="atScrollStart" aria-label="向左查看表格字段" @click="moveTable(-1)">←</el-button>
        <el-button :disabled="atScrollEnd" aria-label="向右查看表格字段" @click="moveTable(1)">→</el-button>
      </el-button-group>
    </div>
    <el-table
      ref="selectionTable"
      row-key="id"
      v-loading="loading"
      :data="rows"
      :row-class-name="({ row }: any) => selectedRows.some(item => item.id === row.id) ? 'selected-row' : ''"
      @selection-change="selectedRows = $event"
      @scroll="measureTableScroll"
      :scrollbar-tabindex="tableScroll ? 0 : undefined"
      :scrollbar-always-on="tableScroll"
      show-overflow-tooltip
      border
      stripe
      style="width: 100%"
    >
      <el-table-column v-if="selectable" type="selection" width="48" fixed="left" :reserve-selection="false" />
      <slot />
      <template #empty>
        <el-empty v-if="!loading" :image-size="72"
          :description="loadFailed ? (externalError ? '筛选范围待确认' : '数据暂时无法显示，请重新加载') : '暂无符合条件的记录'" />
        <span v-else role="status">正在加载记录…</span>
      </template>
    </el-table>
    </div>

    <div v-if="!loadFailed" class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        :layout="paginationLayout"
        :small="isMobile"
        :pager-count="isMobile ? 5 : 7"
        :disabled="paginationDisabled"
        background
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, type TableInstance } from 'element-plus'
import type { ApiResult, PageResult } from '@/api/request'

const props = defineProps<{
  fetcher: (params: Record<string, any>) => Promise<ApiResult<PageResult>>
  query?: Record<string, any>
  selectable?: boolean
  tableScroll?: boolean
  externalError?: boolean
  paginationDisabled?: boolean
}>()

const rows = ref<any[]>([])
const selectionTable = ref<TableInstance>()
const tableHost = ref<HTMLElement>()
const hasHorizontalOverflow = ref(false)
const atScrollStart = ref(true)
const atScrollEnd = ref(true)
let tableResizeObserver: ResizeObserver | undefined

function tableScrollElement(): HTMLElement | null {
  return tableHost.value?.querySelector<HTMLElement>('.el-table__body-wrapper .el-scrollbar__wrap') ?? null
}
function measureTableScroll() {
  if (!props.tableScroll) return
  const element = tableScrollElement()
  const max = element ? Math.max(0, element.scrollWidth - element.clientWidth) : 0
  hasHorizontalOverflow.value = max > 1
  atScrollStart.value = !element || element.scrollLeft <= 1
  atScrollEnd.value = !element || element.scrollLeft >= max - 1
}
function moveTable(direction: -1 | 1) {
  const element = tableScrollElement()
  if (!element) return
  const fixedHeaders = tableHost.value?.querySelectorAll<HTMLElement>(
    '.el-table__header-wrapper th.el-table-fixed-column--left, .el-table__header-wrapper th.el-table-fixed-column--right',
  )
  const fixedWidth = Array.from(fixedHeaders ?? []).reduce((sum, cell) => sum + cell.getBoundingClientRect().width, 0)
  const visibleWidth = Math.max(1, element.clientWidth - fixedWidth)
  selectionTable.value?.setScrollLeft(element.scrollLeft + direction * Math.max(1, visibleWidth * 0.8))
}
watch(tableHost, async (host, previous) => {
  if (previous) tableResizeObserver?.unobserve(previous)
  if (host) tableResizeObserver?.observe(host)
  await nextTick()
  measureTableScroll()
})
watch(rows, async () => { await nextTick(); measureTableScroll() })
const selectedRows = ref<any[]>([])
const allMobileSelected = computed(() => rows.value.length > 0 && rows.value.every(row => selectedRows.value.some(item => item.id === row.id)))
const someMobileSelected = computed(() => selectedRows.value.length > 0 && !allMobileSelected.value)
function toggleMobile(row: any, checked: string | number | boolean) {
  selectedRows.value = checked ? [...selectedRows.value.filter(item => item.id !== row.id), row] : selectedRows.value.filter(item => item.id !== row.id)
}
function toggleAllMobile(checked: string | number | boolean) { selectedRows.value = checked ? [...rows.value] : [] }
function clearSelection() { selectionTable.value?.clearSelection(); selectedRows.value = [] }
async function copySelection() {
  const numbers = selectedRows.value.map(row => row.order_no).filter(Boolean)
  if (!numbers.length) return
  const text = numbers.join('\n')
  try { await navigator.clipboard.writeText(text); ElMessage.success(`已复制 ${numbers.length} 个订单号`) }
  catch { ElMessage.error('复制失败，请检查浏览器剪贴板权限后重试') }
}
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const loadFailed = ref(false)
let loadSequence = 0
let lastRequestedQuery: Record<string, any> | null = null
const isMobile = ref(false)
const mobileQuery = window.matchMedia('(max-width: 720px)')
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function syncViewport(event?: MediaQueryListEvent) {
  const next = event?.matches ?? mobileQuery.matches
  if (next !== isMobile.value) clearSelection()
  isMobile.value = next
}

async function runLoad(reuseRequestedQuery: boolean) {
  const sequence = ++loadSequence
  loading.value = true
  clearSelection()
  loadFailed.value = false
  const requestedQuery = reuseRequestedQuery && lastRequestedQuery !== null
    ? { ...lastRequestedQuery }
    : { ...(props.query || {}) }
  lastRequestedQuery = requestedQuery
  try {
    const params = { page: page.value, page_size: pageSize.value, ...requestedQuery }
    const res = await props.fetcher(params)
    if (sequence !== loadSequence) return
    rows.value = res.data.list || []
    total.value = res.data.total || 0
  } catch (error: any) {
    if (sequence !== loadSequence) return
    rows.value = []
    total.value = 0
    if (error?.response?.status === 404 && page.value > 1) {
      // 最后一页的末条记录删除后，回到首屏，避免停在不存在的页码。
      page.value = 1
      await runLoad(true)
      return
    }
    loadFailed.value = true
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

function load() { return runLoad(false) }
/** 后台刷新保持最近一次请求的筛选条件，不隐式应用输入草稿。 */
function refresh() { return runLoad(true) }

function onSizeChange() {
  page.value = 1
  load()
}

/** 重置到第一页并重新加载（搜索时用）。 */
function reload() {
  page.value = 1
  load()
}

onMounted(() => {
  if (props.tableScroll && typeof ResizeObserver !== 'undefined') {
    tableResizeObserver = new ResizeObserver(measureTableScroll)
    if (tableHost.value) tableResizeObserver.observe(tableHost.value)
  }
  syncViewport()
  mobileQuery.addEventListener('change', syncViewport)
  load()
})
onBeforeUnmount(() => {
  tableResizeObserver?.disconnect()
  ++loadSequence
  mobileQuery.removeEventListener('change', syncViewport)
})

defineExpose({ reload, load, refresh })
</script>

<style scoped>
.mobile-result-count { margin: 4px 0 10px; color: var(--muted-foreground); font-size: 12px; }
.table-scroll-controls { --control-button-min-width: 40px; display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin: 0 0 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.table-host--scroll :deep(.el-table__body-wrapper .el-scrollbar__wrap:focus-visible) { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
</style>
