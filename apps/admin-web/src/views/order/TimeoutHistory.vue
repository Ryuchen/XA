<template>
  <section class="timeout-history" aria-label="待接单时限变更历史">
    <div class="timeout-history-head">
      <strong>变更历史</strong>
      <Button variant="outline" :disabled="loading" @click="reload">刷新</Button>
    </div>
    <Alert v-if="error" variant="destructive"><AlertDescription>历史记录加载失败，请重试 <Button variant="link" @click="load">重新加载</Button></AlertDescription></Alert>
    <p v-if="loading" role="status">正在加载历史记录…</p><p v-else-if="!rows.length" class="timeout-history-meta">{{ error ? '暂时无法显示历史记录' : '暂无变更记录' }}</p>
    <div v-if="!loading && rows.length" class="timeout-history-mobile" role="list" aria-label="时限变更记录">
      <article v-for="row in rows" :key="row.id" class="timeout-history-card" role="listitem">
        <div class="timeout-history-card-head"><strong>{{ row.previous_minutes }} → {{ row.minutes }} 分钟</strong><span>版本 {{ row.revision }}</span></div>
        <div class="timeout-history-meta"><span>{{ row.operator_name || '未知操作人' }}</span><time>{{ formatDateTime(row.created_at) }}</time></div>
      </article>
    </div>
    <div v-if="!loading && rows.length" class="timeout-history-table"><Table><TableHeader><TableRow><TableHead>版本</TableHead><TableHead>修改前</TableHead><TableHead>修改后</TableHead><TableHead>操作人</TableHead><TableHead>时间</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.revision }}</TableCell><TableCell>{{ row.previous_minutes }} 分钟</TableCell><TableCell>{{ row.minutes }} 分钟</TableCell><TableCell>{{ row.operator_name || '未知操作人' }}</TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell></TableRow></TableBody></Table></div>
    <div v-if="total > pageSize" class="timeout-history-pages"><Button variant="outline" :disabled="loading || page <= 1" @click="page--; load()">上一页</Button><span>第 {{ page }} / {{ Math.ceil(total / pageSize) }} 页</span><Button variant="outline" :disabled="loading || page >= Math.ceil(total / pageSize)" @click="page++; load()">下一页</Button></div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
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
.timeout-history-pages { display:flex; justify-content:flex-end; align-items:center; gap:8px; margin-top:12px; }
.timeout-history-mobile { display: none; }
.timeout-history-card { padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
.timeout-history-card + .timeout-history-card { margin-top: 10px; }
.timeout-history-card-head, .timeout-history-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.timeout-history-card-head span, .timeout-history-meta { color: var(--muted-foreground); font-size: 12px; }
.timeout-history-meta { margin-top: 8px; overflow-wrap: anywhere; }
@media (max-width: 720px) { .timeout-history-mobile { display: block; } .timeout-history-table { display: none; } }
</style>
