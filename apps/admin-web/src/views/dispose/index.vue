<template>
  <main class="dp-page">
    <Card class="dp-header"><h2>陪玩奖罚明细</h2><p>按陪玩账号、昵称或类型核对记录。金额以兴安币显示。</p></Card>
    <SimplePagedList ref="tableRef" :fetcher="disposeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <form class="dp-filters" @submit.prevent="reload">
          <div v-if="query.account_id" class="dp-scope"><span>陪玩账户 #{{ query.account_id }}</span><Button size="xs" variant="outline" @click="clearAccountFilter">清除账户筛选</Button></div>
          <label class="dp-search">查找陪玩<Input v-model="query.keyword" placeholder="账号/昵称" aria-label="按账号或昵称搜索" /></label>
          <label>奖罚类型<NativeSelect v-model="query.dispose_type" aria-label="类型" @update:model-value="reload"><NativeSelectOption value="">全部类型</NativeSelectOption><NativeSelectOption v-for="(item,key) in DISPOSE_TYPE" :key="key" :value="key">{{ item.label }}</NativeSelectOption></NativeSelect></label>
          <Button size="xs" type="submit">查询</Button>
        </form>
      </template>
      <template #default="{ rows }">
        <div class="dp-records"><Table>
          <TableHeader><TableRow><TableHead>陪玩 / 账号</TableHead><TableHead>类型 / 金额</TableHead><TableHead>原因</TableHead><TableHead>经办记录</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id" class="dp-card">
            <TableCell class="dp-person"><strong>{{ row.nickname || row.username || '陪玩待核对' }}</strong><small>账号 {{ row.username || '未记录' }}</small></TableCell>
            <TableCell class="dp-amount"><Badge variant="outline">{{ row.dispose_type_display || '类型待核对' }}</Badge><strong :class="{ 'dp-reward':row.dispose_type === 'REWARD', 'dp-penalty':row.dispose_type === 'PENALTY' }">{{ amountLabel(row) }}</strong></TableCell>
            <TableCell class="dp-reason"><span class="dp-label">奖罚原因</span><span>{{ row.reason || '未填写' }}</span></TableCell>
            <TableCell class="dp-audit"><time :datetime="row.created_at">{{ formatDateTime(row.created_at) }}</time><small>记录 #{{ row.id }} · 操作人 {{ row.operator_name || '未记录' }}</small></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>
  </main>
</template>
<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { disposeRecordApi } from '@/api/modules'
import { DISPOSE_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'

const tableRef = ref()
const route = useRoute()
const router = useRouter()
const query = reactive<Record<string, any>>({ keyword: '', dispose_type: '', account_id: '' })
watch(() => route.query.account_id, value => { query.account_id = typeof value === 'string' ? value : ''; tableRef.value?.reload() }, { immediate: true })
function amountLabel(row: {amount?:number; dispose_type?:string}) {
  if (typeof row.amount !== 'number' || !Number.isSafeInteger(row.amount) || row.amount < 0) return '金额待核对'
  const amount = amountToXaCoin(row.amount) + ' 币'
  if (row.dispose_type === 'REWARD') return '+' + amount
  if (row.dispose_type === 'PENALTY') return '-' + amount
  return amount + '（方向待核对）'
}
function clearAccountFilter() { const next = { ...route.query }; delete next.account_id; void router.replace({ query: next }) }
</script>

<style scoped>
.dp-page { min-width:0; display:grid; gap:12px; container:dispose / inline-size; }
.dp-header { padding:12px 16px; gap:4px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.dp-header h2 { font-size:16px; font-weight:600; }
.dp-header p,.dp-label,.dp-records small { font-size:12px; color:var(--muted-foreground); }
.dp-filters,.dp-scope { display:flex; align-items:end; flex-wrap:wrap; gap:8px; }
.dp-filters { padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.dp-scope { align-items:center; flex-basis:100%; padding-bottom:8px; border-bottom:1px solid var(--border); font-size:13px; overflow-wrap:anywhere; }
.dp-search { flex:1 1 180px; max-width:400px; }
.dp-filters label { display:grid; min-width:0; gap:4px; font-size:12px; }
.dp-filters :deep(input),.dp-filters :deep(select) { width:100%; min-width:0; height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.dp-filters :deep(select) { padding-right:32px; }
.dp-records { min-width:0; background:var(--card); }
.dp-records :deep(td),.dp-records :deep(th) { white-space:normal; overflow-wrap:anywhere; vertical-align:top; padding:8px; }
.dp-records :deep(th) { background:var(--muted); font-size:12px; } .dp-records :deep(td) { font-size:13px; }
.dp-records small,.dp-amount strong { display:block; margin-top:4px; }
.dp-amount strong { font-size:16px; font-weight:600; font-variant-numeric:tabular-nums; }
.dp-reward { color:var(--primary); } .dp-penalty { color:var(--destructive); }
.dp-label { display:none; }
.dp-reason>span:last-child { white-space:pre-wrap; }
@container dispose (max-width:760px) {
  .dp-page :deep(.table-scroll),.dp-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .dp-records { background:transparent; } .dp-records :deep(table),.dp-records :deep(tbody) { display:grid; }
  .dp-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .dp-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .dp-records .dp-card { display:grid; grid-template-columns:minmax(0,1fr) auto; align-content:start; gap:8px; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; }
  .dp-records :deep(td) { padding:0; } .dp-person { grid-column:1; grid-row:1; }
  .dp-amount { grid-column:2; grid-row:1; text-align:right; } .dp-reason,.dp-audit { grid-column:1/-1; }
  .dp-records .dp-reason { border-top:1px solid var(--border); padding-top:8px; } .dp-label { display:block; margin-bottom:4px; }
  .dp-page :deep(.pages) { justify-content:flex-start; }
}
@container dispose (max-width:480px) { .dp-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } .dp-search { flex-basis:100%; max-width:none; } }
@media(pointer:coarse) {
  .dp-page :deep([data-slot='button']) { min-width:44px; min-height:44px; }
  .dp-filters :deep(input),.dp-filters :deep(select) { min-height:44px; }
}
</style>
