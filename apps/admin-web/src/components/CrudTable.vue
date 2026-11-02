<template>
  <div class="page-container">
    <div class="page-toolbar">
      <slot name="toolbar" :reload="reload" />
    </div>
    <Alert v-if="loadFailed && !externalError" variant="destructive"><AlertDescription>数据加载失败，请重试 <Button size="xs" variant="link" @click="refresh">重新加载</Button></AlertDescription></Alert>

    <div v-if="selectable" class="table-selection-bar">
      <span role="status" aria-live="polite">{{ selectedRows.length ? `已选 ${selectedRows.length} 条，可复制订单号核对` : '勾选本页订单，集中复制订单号' }}</span>
      <div>
        <label v-if="isMobile && $slots.mobile" class="selection-control"><Checkbox :model-value="someMobileSelected ? 'indeterminate' : allMobileSelected" :disabled="!rows.length || loading" @update:model-value="value => toggleAllMobile(value === true)" />选择本页</label>
        <Button size="xs" variant="outline" :disabled="!selectedRows.length" @click="copySelection">复制订单号</Button>
        <Popover><PopoverTrigger as-child><Button size="xs" variant="link" aria-label="复制说明">说明</Button></PopoverTrigger><PopoverContent class="table-popover">仅复制选中的订单号，不会更改订单状态。</PopoverContent></Popover>
        <Button size="xs" variant="ghost" :disabled="!selectedRows.length" @click="clearSelection">清空选择</Button>
      </div>
    </div>
    <p v-if="selectionNotice" role="status" class="selection-notice">{{ selectionNotice }}</p>
    <div v-if="isMobile && $slots.mobile && !loading && !loadFailed" class="mobile-result-count" role="status" aria-live="polite">共 {{ total.toLocaleString() }} 条<span v-if="total"> · 本页 {{ rows.length }} 条</span></div>
    <div v-if="isMobile && $slots.mobile" class="mobile-records" :aria-busy="loading">
      <slot v-if="rows.length" name="mobile" :rows="rows" :selected="selectedRows" :toggle="toggleMobile" />
      <p v-else-if="!loading" class="table-empty">{{ loadFailed ? (externalError ? '筛选范围待确认' : '数据暂时无法显示，请重新加载') : '暂无符合条件的记录' }}</p>
      <span v-else role="status">正在加载记录…</span>
    </div>
    <div v-else ref="tableHost" class="table-host" :class="{ 'table-host--scroll': tableScroll }" :role="tableScroll ? 'region' : undefined" :aria-label="tableScroll ? '数据表格' : undefined">
    <div v-if="tableScroll && hasHorizontalOverflow && rows.length" class="table-scroll-controls">
      <span>查看更多字段</span>
      <div class="table-scroll-buttons"><Button size="xs" variant="outline" :disabled="atScrollStart" aria-label="向左查看表格字段" @click="moveTable(-1)">←</Button><Button size="xs" variant="outline" :disabled="atScrollEnd" aria-label="向右查看表格字段" @click="moveTable(1)">→</Button></div>
    </div>
    <template v-if="$slots.desktop">
      <p v-if="loading" role="status">正在加载记录…</p><p v-else-if="!rows.length" class="table-empty">{{ loadFailed ? (externalError ? '筛选范围待确认' : '数据暂时无法显示，请重新加载') : '暂无符合条件的记录' }}</p>
      <div v-else ref="nativeScroll" class="native-scroll" tabindex="0" @scroll="measureTableScroll"><slot name="desktop" :rows="rows" :selected="selectedRows" :toggle="toggleMobile" :toggle-all="toggleAllMobile" :all-selected="allMobileSelected" :some-selected="someMobileSelected" /></div>
    </template>
    <Alert v-else variant="destructive"><AlertDescription>列表暂时无法显示，请联系管理员检查页面配置。</AlertDescription></Alert>
    </div>

    <div v-if="!loadFailed" class="pagination-wrap">
      <div class="table-pages"><span v-if="!isMobile">共 {{ total }} 条</span><NativeSelect v-if="!isMobile" :model-value="String(pageSize)" aria-label="每页条数" :disabled="paginationDisabled || loading" @update:model-value="changePageSize"><NativeSelectOption v-for="size in [10,20,50,100]" :key="size" :value="String(size)">{{ size }} 条/页</NativeSelectOption></NativeSelect><Button size="xs" variant="outline" :disabled="paginationDisabled || loading || page <= 1" @click="changePage(page - 1)">上一页</Button><span>第 {{ page }} / {{ pageCount }} 页</span><Button size="xs" variant="outline" :disabled="paginationDisabled || loading || page >= pageCount" @click="changePage(page + 1)">下一页</Button></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import type { ApiResult, PageResult } from '@/api/request'

const props = defineProps<{
  fetcher: (params: Record<string, any>) => Promise<ApiResult<PageResult>>
  query?: Record<string, any>
  selectable?: boolean
  tableScroll?: boolean
  externalError?: boolean
  paginationDisabled?: boolean
  clearOnReload?: boolean
}>()

const rows = ref<any[]>([])
const tableHost = ref<HTMLElement>()
const nativeScroll = ref<HTMLElement>()
const hasHorizontalOverflow = ref(false)
const atScrollStart = ref(true)
const atScrollEnd = ref(true)
let tableResizeObserver: ResizeObserver | undefined

function tableScrollElement(): HTMLElement | null {
  return nativeScroll.value ?? null
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
  const visibleWidth = Math.max(1, element.clientWidth)
  element.scrollBy({ left: direction * Math.max(1, visibleWidth * 0.8), behavior: 'smooth' })
}
watch(tableHost, async (host, previous) => {
  if (previous) tableResizeObserver?.unobserve(previous)
  if (host) tableResizeObserver?.observe(host)
  await nextTick()
  measureTableScroll()
})
watch(rows, async () => { await nextTick(); measureTableScroll() })
watch(nativeScroll, async (element, previous) => { if (previous) tableResizeObserver?.unobserve(previous); if (element) tableResizeObserver?.observe(element); await nextTick(); measureTableScroll() })
const selectedRows = ref<any[]>([])
const selectionNotice = ref('')
const allMobileSelected = computed(() => rows.value.length > 0 && rows.value.every(row => selectedRows.value.some(item => item.id === row.id)))
const someMobileSelected = computed(() => selectedRows.value.length > 0 && !allMobileSelected.value)
function toggleMobile(row: any, checked: string | number | boolean) {
  selectedRows.value = checked ? [...selectedRows.value.filter(item => item.id !== row.id), row] : selectedRows.value.filter(item => item.id !== row.id)
}
function toggleAllMobile(checked: string | number | boolean) { selectedRows.value = checked ? [...rows.value] : [] }
function clearSelection() { selectedRows.value = [] }
async function copySelection() {
  const numbers = selectedRows.value.map(row => row.order_no).filter(Boolean)
  if (!numbers.length) return
  const text = numbers.join('\n')
  try { await navigator.clipboard.writeText(text); selectionNotice.value = `已复制 ${numbers.length} 个订单号` }
  catch { selectionNotice.value = '复制失败，请检查浏览器剪贴板权限后重试' }
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
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

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

function changePageSize(value:unknown) { const size = Number(value); if (![10,20,50,100].includes(size)) return; pageSize.value = size; page.value = 1; void load() }
function changePage(value:number) { if (value < 1 || value > pageCount.value || loading.value || props.paginationDisabled) return; page.value = value; void load() }

/** 重置到第一页并重新加载（搜索时用）。 */
function reload() {
  page.value = 1
  if (props.clearOnReload) { rows.value = []; total.value = 0 }
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
.table-scroll-controls { --control-button-min-width: 40px; display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin: 0 0 8px; color: var(--muted-foreground); font-size: 12px; }
.table-scroll-buttons,.table-pages { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.table-pages { justify-content:flex-end; }
.table-popover { width:240px; background:var(--card); color:var(--foreground); font-size:13px; }
.selection-control { display:inline-flex; align-items:center; gap:8px; }
.selection-notice { color:var(--primary); font-size:12px; }
.native-scroll { overflow-x: auto; }
.native-scroll:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.table-empty { color:var(--muted-foreground); text-align:center; padding:24px; }
</style>
