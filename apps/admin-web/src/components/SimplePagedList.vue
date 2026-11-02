<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import type { ApiResult, PageResult } from '@/api/request'

const props = defineProps<{ fetcher: (params: Record<string, any>) => Promise<ApiResult<PageResult>>; query?: Record<string, any> }>()
const rows = ref<any[]>([]), total = ref(0), page = ref(1), size = ref('10'), loading = ref(false), failed = ref(false)
const pages = computed(() => Math.max(1, Math.ceil(total.value / Number(size.value))))
let sequence = 0, active = true
async function load() {
  const current = ++sequence; loading.value = true; failed.value = false
  try { const result = await props.fetcher({ page: page.value, page_size: Number(size.value), ...(props.query || {}) }); if (active && current === sequence) { rows.value = result.data.list || []; total.value = result.data.total || 0 } }
  catch { if (active && current === sequence) { rows.value = []; total.value = 0; failed.value = true } }
  finally { if (active && current === sequence) loading.value = false }
}
function reload() { page.value = 1; void load() }
function move(next: number) { if (!loading.value && next >= 1 && next <= pages.value) { page.value = next; void load() } }
function changeSize(value: unknown) { const next = String(value); if (['10','20','50','100'].includes(next)) { size.value = next; reload() } }
onMounted(load); onBeforeUnmount(() => { active = false; sequence++ })
defineExpose({ load, reload })
</script>
<template>
  <div class="paged-list">
    <slot name="toolbar" :reload="reload" />
    <Alert v-if="failed" variant="destructive"><AlertDescription>读取失败，请重试 <Button size="xs" variant="link" @click="load">重新加载</Button></AlertDescription></Alert>
    <p v-else-if="loading" role="status">正在加载…</p>
    <template v-else><p v-if="!rows.length" class="empty">暂无记录</p><div v-else class="table-scroll"><slot :rows="rows" /></div><div v-if="total" class="pages"><span>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</span><NativeSelect :model-value="size" aria-label="每页条数" @update:model-value="changeSize"><NativeSelectOption v-for="item in ['10','20','50','100']" :key="item" :value="item">{{ item }} 条/页</NativeSelectOption></NativeSelect><Button size="xs" variant="outline" :disabled="page <= 1" @click="move(page - 1)">上一页</Button><Button size="xs" variant="outline" :disabled="page >= pages" @click="move(page + 1)">下一页</Button></div></template>
  </div>
</template>
<style scoped>
.paged-list { display:grid; gap:8px; } .table-scroll { overflow:auto; border:1px solid var(--border); border-radius:8px; } .empty { padding:16px; border:1px solid var(--border); border-radius:8px; } .pages { display:flex; justify-content:flex-end; align-items:center; gap:8px; flex-wrap:wrap; color:var(--muted-foreground); font-size:12px; }
</style>
