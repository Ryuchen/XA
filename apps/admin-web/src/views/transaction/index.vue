<template>
  <main class="tx-page">
    <Card class="tx-header"><h2>资金变动明细</h2><p>按流水号、账号或类型查找，结合变动后余额和关联订单核对。金额单位为兴安币。</p></Card>
    <SimplePagedList v-if="canReadTransactions" ref="tableRef" :fetcher="transactionApi.list" :query="query">
      <template #toolbar="{ reload }">
        <form class="tx-filters" @submit.prevent="reload">
          <div v-if="query.wallet_id || query.order_no_exact" class="tx-scope">
            <div v-if="query.wallet_id"><span>钱包 #{{ query.wallet_id }}</span><Button size="xs" variant="outline" @click="clearWalletFilter">清除钱包筛选</Button></div>
            <div v-if="query.order_no_exact"><span>订单 {{ query.order_no_exact }}</span><Button size="xs" variant="outline" @click="clearOrderFilter">清除订单筛选</Button></div>
          </div>
          <label class="tx-keyword">查找流水<Input v-model="query.keyword" placeholder="流水号或账号" aria-label="搜索资金流水" @update:model-value="value => { if (value === '') reload() }" /></label>
          <label>流水类型<NativeSelect v-model="query.tx_type" aria-label="流水类型" @update:model-value="reload"><NativeSelectOption value="">全部类型</NativeSelectOption><NativeSelectOption v-for="(v, k) in TX_TYPE" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect></label>
          <Button size="xs" type="submit">查询</Button>
        </form>
      </template>
      <template #default="{ rows }">
        <div class="tx-records"><Table>
          <TableHeader><TableRow><TableHead>流水 / 账户</TableHead><TableHead>金额 / 余额</TableHead><TableHead>关联订单与冲正</TableHead><TableHead>经办记录</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id" class="tx-card">
            <TableCell class="tx-identity">
              <div class="tx-heading"><strong>{{ row.tx_type_display || '类型待核对' }}</strong><Badge variant="outline">{{ row.status_display || '状态待核对' }}</Badge></div>
              <small class="tx-number">{{ row.tx_no || '流水号待核对' }}</small><small>账号 {{ row.username || '待核对' }}</small>
              <template v-if="isIncomeHold(row)">
                <div class="tx-help"><Popover><PopoverTrigger as-child><Button size="xs" variant="outline" aria-label="冻结流水说明">冻结说明</Button></PopoverTrigger><PopoverContent>冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。</PopoverContent></Popover></div>
                <p class="tx-hold-inline">冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。</p>
              </template>
            </TableCell>
            <TableCell class="tx-money"><span class="tx-caption">流水金额</span><strong class="tx-amount">{{ coinLabel(row.amount, true) }}</strong><small>变动后余额 {{ coinLabel(row.balance_after) }}</small></TableCell>
            <TableCell class="tx-references"><span class="tx-caption">关联订单</span><router-link v-if="row.order_no && auth.hasPerm('order:view')" class="tx-link" :to="{ name:'orders', query:{ find_order:row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no || '—' }}</span>
              <div v-if="row.is_reversal || row.reversal_of" class="tx-reversal"><Badge variant="secondary">冲正</Badge><Button v-if="row.reversal_of" size="xs" variant="outline" @click="openOriginal(row.reversal_of)">查看原流水 #{{ row.reversal_of }}</Button><span v-else>原流水待核对</span></div>
            </TableCell>
            <TableCell class="tx-audit"><time :datetime="row.created_at">{{ formatDateTime(row.created_at) }}</time><small>操作人 {{ row.operator_name || '系统' }}</small><small class="tx-remark">备注 {{ row.remark || '—' }}</small></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>
  </main>
  <Dialog v-model:open="originalVisible"><DialogContent class="tx-original-dialog"><DialogHeader><DialogTitle>原流水 #{{ originalId }}</DialogTitle><DialogDescription>以下为读取时的记录。</DialogDescription></DialogHeader>
    <Alert v-if="originalFailed" variant="destructive"><AlertDescription>原流水读取失败，请重试 <Button size="xs" variant="link" @click="loadOriginal">重新读取</Button></AlertDescription></Alert>
    <p v-else-if="originalLoading" role="status">正在读取原流水…</p>
    <dl v-else-if="original" class="tx-original-facts"><div><dt>流水号</dt><dd>{{ original.tx_no || '待核对' }}</dd></div><div><dt>账号</dt><dd>{{ original.username || '待核对' }}</dd></div><div><dt>关联订单</dt><dd><router-link v-if="original.order_no && auth.hasPerm('order:view')" :to="{ name: 'orders', query: { find_order: original.order_no } }">{{ original.order_no }}</router-link><span v-else>{{ original.order_no || '—' }}</span></dd></div><div><dt>类型</dt><dd>{{ original.tx_type_display || '待核对' }}</dd></div><div><dt>状态</dt><dd>{{ original.status_display || '待核对' }}</dd></div><div><dt>流水金额</dt><dd>{{ coinLabel(original.amount, true) }}</dd></div><div><dt>变动前余额</dt><dd>{{ coinLabel(original.balance_before) }}</dd></div><div><dt>变动后余额</dt><dd>{{ coinLabel(original.balance_after) }}</dd></div><div><dt>备注</dt><dd>{{ original.remark || '—' }}</dd></div><div><dt>时间</dt><dd>{{ formatDateTime(original.created_at) }}</dd></div></dl>
    <p v-if="original && isIncomeHold(original)" class="tx-hold-note">冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。</p>
  </DialogContent></Dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { transactionApi } from '@/api/modules'
import { TX_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'
import { useRoute, useRouter } from 'vue-router'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const canReadTransactions = computed(() => Boolean(auth.token) && auth.hasPerm('transaction:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', tx_type: '', wallet_id: '', order_no_exact: '' })
function coinLabel(value: number | null | undefined, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '待核对'
  return (signed && value >= 0 ? '+' : '') + amountToXaCoin(value) + ' 币'
}
function isIncomeHold(row: any) { return row?.tx_type === 'EARNINGS_FREEZE' || row?.tx_type === 'EARNINGS_RELEASE' }
watch(() => route.query.wallet_id, value => { query.wallet_id = typeof value === 'string' ? value : ''; tableRef.value?.reload() }, { immediate: true })
watch(() => route.query.order_no, value => { query.order_no_exact = typeof value === 'string' ? value : ''; tableRef.value?.reload() }, { immediate: true })
function clearWalletFilter() { const next = { ...route.query }; delete next.wallet_id; void router.replace({ query: next }) }
function clearOrderFilter() { const next = { ...route.query }; delete next.order_no; void router.replace({ query: next }) }
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let offWalletUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null
const originalVisible = ref(false), originalLoading = ref(false), originalFailed = ref(false)
const originalId = ref<number | null>(null), original = ref<any>(null)
let originalSequence = 0
function openOriginal(id: unknown) {
  if (!canReadTransactions.value || !Number.isSafeInteger(id) || (id as number) <= 0 || pageDisposed) return
  originalId.value = id as number
  originalVisible.value = true
  void loadOriginal()
}
async function loadOriginal() {
  const id = originalId.value
  if (!id || !originalVisible.value || !canReadTransactions.value || pageDisposed) return
  const sequence = ++originalSequence, revision = auth.sessionRevision
  original.value = null
  originalFailed.value = false
  originalLoading.value = true
  try {
    const response = await transactionApi.retrieve(id, true)
    if (sequence !== originalSequence || revision !== auth.sessionRevision || !originalVisible.value || !canReadTransactions.value || pageDisposed) return
    if (response.data?.id !== id) throw new Error('invalid transaction')
    original.value = response.data
  } catch {
    if (sequence === originalSequence && revision === auth.sessionRevision && originalVisible.value && canReadTransactions.value && !pageDisposed) originalFailed.value = true
  } finally {
    if (sequence === originalSequence && revision === auth.sessionRevision && !pageDisposed) originalLoading.value = false
  }
}
watch(originalVisible, visible => {
  if (!visible) { ++originalSequence; original.value = null; originalLoading.value = false; originalFailed.value = false; originalId.value = null }
}, { flush: 'sync' })

function requestTransactionRefresh() {
  if (pageDisposed || !canReadTransactions.value) return
  refreshPending = true
  scheduleTransactionRefresh()
}

function scheduleTransactionRefresh() {
  if (pageDisposed || !canReadTransactions.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadTransactions.value) return
    refreshPending = false
    refreshRunning = true
    try { await tableRef.value?.load() }
    catch { /* CrudTable owns the visible retry state. */ }
    finally {
      if (generation === realtimeGeneration) { refreshRunning = false; scheduleTransactionRefresh() }
    }
  }, 300)
}

function stopTransactionRealtime() {
  ++realtimeGeneration
  offWalletUpdate?.()
  offConnected?.()
  offWalletUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = refreshRunning = false
}

function startTransactionRealtime() {
  if (pageDisposed || !canReadTransactions.value) return
  offWalletUpdate = adminWs.on('wallet_update', data => {
    if ((Number.isSafeInteger(data?.wallet_id) && data.wallet_id > 0)
      || (Number.isSafeInteger(data?.account_id) && data.account_id > 0)) requestTransactionRefresh()
  })
  offConnected = adminWs.on('connected', requestTransactionRefresh)
  adminWs.connect()
}

watch(() => [auth.sessionRevision, canReadTransactions.value], () => {
  originalVisible.value = false
  stopTransactionRealtime()
  if (pageMounted) startTransactionRealtime()
}, { flush: 'sync' })
onMounted(() => { pageMounted = true; startTransactionRealtime() })
onBeforeUnmount(() => { pageDisposed = true; ++originalSequence; stopTransactionRealtime() })
</script>

<style scoped>
.tx-page { display:grid; gap:12px; min-width:0; container:transactions / inline-size; }
.tx-header { gap:4px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.tx-header h2 { margin:0; font-size:16px; font-weight:600; }
.tx-header p,.tx-caption,.tx-hold-note,.tx-hold-inline { font-size:12px; color:var(--muted-foreground); }
.tx-filters { display:flex; flex-wrap:wrap; align-items:end; gap:8px; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; }
.tx-filters label { display:grid; gap:4px; min-width:0; font-size:12px; }
.tx-keyword { flex:1 1 180px; max-width:400px; }
.tx-filters :deep(input),.tx-filters :deep(select) { width:100%; min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.tx-filters :deep(select) { padding-right:32px; }
.tx-scope { display:flex; flex-wrap:wrap; gap:8px 16px; padding-bottom:8px; border-bottom:1px solid var(--border); flex-basis:100%; }
.tx-scope>div,.tx-heading,.tx-reversal { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.tx-scope span { overflow-wrap:anywhere; font-size:13px; }
.tx-records { min-width:0; background:var(--card); }
.tx-records :deep(th),.tx-records :deep(td) { padding:8px; white-space:normal; vertical-align:top; overflow-wrap:anywhere; }
.tx-records :deep(th) { background:var(--muted); font-size:12px; } .tx-records :deep(td) { font-size:13px; }
.tx-records small { display:block; margin-top:4px; color:var(--muted-foreground); font-size:12px; line-height:1.45; }
.tx-records small.tx-number { font-family:var(--font-mono,monospace); }
.tx-amount,.tx-caption { display:block; } .tx-amount { font-size:16px; font-weight:600; color:var(--foreground); font-variant-numeric:tabular-nums; }
.tx-link,.tx-original-facts a { color:var(--primary); text-decoration:underline; text-underline-offset:2px; }
.tx-help,.tx-reversal,.tx-hold-inline { margin-top:8px; }
.tx-hold-inline { display:none; }
.tx-remark,.tx-original-facts dd { white-space:pre-wrap; }
.tx-original-dialog { padding:16px; max-height:calc(100dvh - 24px); overflow-y:auto; gap:12px; }
.tx-original-dialog :deep([data-slot='dialog-title']) { padding-right:32px; }
.tx-original-facts { display:grid; }
.tx-original-facts>div { display:grid; grid-template-columns:76px minmax(0,1fr); gap:8px; padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; }
.tx-original-facts dt { color:var(--muted-foreground); font-size:12px; } .tx-original-facts dd { margin:0; overflow-wrap:anywhere; }
@container transactions (max-width:760px) {
  .tx-page :deep(.table-scroll),.tx-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .tx-records { background:transparent; } .tx-records :deep(table),.tx-records :deep(tbody) { display:grid; }
  .tx-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .tx-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .tx-records .tx-card { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); align-content:start; align-items:start; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .tx-records :deep(td) { padding:0; }
  .tx-records .tx-references,.tx-records .tx-audit { grid-column:1/-1; padding-top:8px; border-top:1px solid var(--border); }
  .tx-hold-inline { display:block; } .tx-help { display:none; }
  .tx-page :deep(.pages) { justify-content:flex-start; }
}
@container transactions (max-width:480px) { .tx-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } .tx-keyword { flex-basis:100%; max-width:none; } }
@media(pointer:coarse) {
  .tx-page :deep([data-slot='button']),.tx-original-dialog :deep([data-slot='button']) { min-height:44px; min-width:44px; }
  .tx-filters :deep(input),.tx-filters :deep(select) { min-height:44px; }
  .tx-link,.tx-original-facts a { display:inline-flex; align-items:center; min-height:44px; min-width:44px; }
}
</style>
