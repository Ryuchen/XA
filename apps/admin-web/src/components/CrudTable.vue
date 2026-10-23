<template>
  <div class="page-container">
    <div class="page-toolbar">
      <slot name="toolbar" :reload="reload" />
    </div>
    <el-alert v-if="loadFailed" title="数据加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="load">重新加载</el-button>
    </el-alert>

    <el-table
      v-loading="loading"
      :data="rows"
      show-overflow-tooltip
      border
      stripe
      style="width: 100%"
    >
      <slot />
      <template #empty>
        <el-empty v-if="!loading" :image-size="72"
          :description="loadFailed ? '数据暂时无法显示，请重新加载' : '暂无符合条件的记录'" />
        <span v-else role="status">正在加载记录…</span>
      </template>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        :layout="paginationLayout"
        :small="isMobile"
        :pager-count="isMobile ? 5 : 7"
        background
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ApiResult, PageResult } from '@/api/request'

const props = defineProps<{
  fetcher: (params: Record<string, any>) => Promise<ApiResult<PageResult>>
  query?: Record<string, any>
}>()

const rows = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const loadFailed = ref(false)
let loadSequence = 0
const isMobile = ref(false)
const mobileQuery = window.matchMedia('(max-width: 720px)')
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function syncViewport(event?: MediaQueryListEvent) {
  isMobile.value = event?.matches ?? mobileQuery.matches
}

async function load() {
  const sequence = ++loadSequence
  loading.value = true
  loadFailed.value = false
  try {
    const params = { page: page.value, page_size: pageSize.value, ...(props.query || {}) }
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
      await load()
      return
    }
    loadFailed.value = true
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

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
  syncViewport()
  mobileQuery.addEventListener('change', syncViewport)
  load()
})
onBeforeUnmount(() => {
  ++loadSequence
  mobileQuery.removeEventListener('change', syncViewport)
})

defineExpose({ reload, load })
</script>
