<template>
  <section class="timeout-history" aria-label="待接单时限变更历史">
    <div class="timeout-history-head">
      <strong>变更历史</strong>
      <el-button :disabled="loading" @click="reload">刷新</el-button>
    </div>
    <el-alert v-if="error" title="历史记录加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="load">重新加载</el-button>
    </el-alert>
    <div v-loading="loading" class="timeout-history-mobile" role="list" aria-label="时限变更记录">
      <article v-for="row in rows" :key="row.id" class="timeout-history-card" role="listitem">
        <div class="timeout-history-card-head"><strong>{{ row.previous_minutes }} → {{ row.minutes }} 分钟</strong><span>版本 {{ row.revision }}</span></div>
        <div class="timeout-history-meta"><span>{{ row.operator_name || '未知操作人' }}</span><time>{{ formatDateTime(row.created_at) }}</time></div>
      </article>
      <el-empty v-if="!loading && !rows.length" :image-size="60" :description="error ? '暂时无法显示历史记录' : '暂无变更记录'" />
    </div>
    <el-table class="timeout-history-table" v-loading="loading" :data="rows" border :empty-text="error ? '暂时无法显示历史记录' : '暂无变更记录'">
      <el-table-column prop="revision" label="版本" width="65" />
      <el-table-column label="修改前" width="85"><template #default="{ row }">{{ row.previous_minutes }} 分钟</template></el-table-column>
      <el-table-column label="修改后" width="85"><template #default="{ row }">{{ row.minutes }} 分钟</template></el-table-column>
      <el-table-column prop="operator_name" label="操作人" min-width="100" />
      <el-table-column label="时间" min-width="150"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
    </el-table>
    <el-pagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next" small @current-change="load" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const allowed = computed(() => Boolean(auth.token) && auth.hasPerm('audit:view'))
const rows = ref<any[]>([]), total = ref(0), page = ref(1), loading = ref(false), error = ref(false)
const pageSize = 10
let sequence = 0
let disposed = false

async function load() {
  if (!allowed.value) return
  const id = ++sequence, session = auth.sessionRevision, requestedPage = page.value
  loading.value = true; error.value = false
  try {
    const result = await request({ url: '/config/order-timeout/history/', method: 'get', params: { page: requestedPage, page_size: pageSize } })
    if (disposed || id !== sequence || session !== auth.sessionRevision || !allowed.value) return
    if (!Array.isArray(result.data?.list) || !Number.isSafeInteger(result.data?.total) || result.data.total < 0) throw new Error('历史记录格式无效')
    rows.value = result.data.list; total.value = result.data.total
  } catch {
    if (disposed || id !== sequence || session !== auth.sessionRevision || !allowed.value) return
    rows.value = []; total.value = 0; error.value = true
  } finally {
    if (!disposed && id === sequence && session === auth.sessionRevision && allowed.value) loading.value = false
  }
}

function reload() { page.value = 1; void load() }
watch(() => [auth.sessionRevision, allowed.value], () => {
  ++sequence; rows.value = []; total.value = 0; page.value = 1; loading.value = error.value = false
  if (allowed.value) void load()
}, { flush: 'sync' })
onMounted(load)
onUnmounted(() => { disposed = true; ++sequence })
defineExpose({ reload })
</script>

<style scoped>
.timeout-history { margin-top: 24px; }
.timeout-history-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.timeout-history :deep(.el-pagination) { justify-content: flex-end; margin-top: 12px; }
.timeout-history-mobile { display: none; }
.timeout-history-card { padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
.timeout-history-card + .timeout-history-card { margin-top: 10px; }
.timeout-history-card-head, .timeout-history-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.timeout-history-card-head span, .timeout-history-meta { color: var(--muted-foreground); font-size: 12px; }
.timeout-history-meta { margin-top: 8px; overflow-wrap: anywhere; }
@media (max-width: 720px) { .timeout-history-mobile { display: block; } .timeout-history-table { display: none; } }
</style>
