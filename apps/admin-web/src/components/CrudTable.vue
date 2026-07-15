<template>
  <div class="page-container">
    <div class="page-toolbar">
      <slot name="toolbar" :reload="reload" />
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      border
      stripe
      style="width: 100%"
    >
      <slot />
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
const isMobile = ref(false)
const mobileQuery = window.matchMedia('(max-width: 720px)')
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function syncViewport(event?: MediaQueryListEvent) {
  isMobile.value = event?.matches ?? mobileQuery.matches
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ...(props.query || {}) }
    const res = await props.fetcher(params)
    rows.value = res.data.list || []
    total.value = res.data.total || 0
  } finally {
    loading.value = false
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
onBeforeUnmount(() => mobileQuery.removeEventListener('change', syncViewport))

defineExpose({ reload, load })
</script>
