<template>
  <div class="tx-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">记录平台各类资金变动流水，支持按类型与关键词筛选</p>
    </div>

    <crud-table v-if="canReadTransactions" ref="tableRef" :fetcher="transactionApi.list" :query="query" table-scroll>
      <template #toolbar="{ reload }">
        <el-tag v-if="query.wallet_id" closable @close="clearWalletFilter">钱包 #{{ query.wallet_id }}</el-tag>
        <el-tag v-if="query.order_no_exact" closable @close="clearOrderFilter">订单 {{ query.order_no_exact }}</el-tag>
        <el-input
          v-model="query.keyword"
          placeholder="流水号/账号"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.tx_type" placeholder="类型" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in TX_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <template #mobile="{ rows }">
        <div class="tx-mobile-list" role="list" aria-label="资金流水列表">
          <article v-for="row in rows" :key="row.id" class="tx-card" role="listitem">
            <div class="tx-card-head"><strong>{{ row.tx_type_display || '类型待核对' }}</strong><el-tag :type="TX_STATUS[row.status]?.type" effect="light" round>{{ row.status_display || '状态待核对' }}</el-tag></div>
            <div class="tx-amount-label">流水金额</div>
            <div class="tx-card-amount" :class="row.amount >= 0 ? 'tx-amount-up' : 'tx-amount-down'">{{ row.amount >= 0 ? '+' : '' }}{{ amountToXaCoin(row.amount) }} 币</div>
            <p v-if="isIncomeHold(row)" class="tx-hold-note">冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。</p>
            <dl class="tx-facts">
              <div><dt>账号</dt><dd>{{ row.username || '待核对' }}</dd></div>
              <div><dt>变动后余额</dt><dd>{{ amountToXaCoin(row.balance_after) }} 币</dd></div>
              <div class="tx-wide"><dt>流水号</dt><dd>{{ row.tx_no || '待核对' }}</dd></div>
              <div class="tx-wide"><dt>关联订单</dt><dd><router-link v-if="row.order_no && auth.hasPerm('order:view')" :to="{ name: 'orders', query: { find_order: row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no || '—' }}</span></dd></div>
              <div v-if="row.is_reversal || row.reversal_of" class="tx-wide"><dt>冲正来源</dt><dd><el-button v-if="row.reversal_of" link type="primary" @click="openOriginal(row.reversal_of)">查看原流水 #{{ row.reversal_of }}</el-button><span v-else>原流水待核对</span></dd></div>
              <div class="tx-wide"><dt>备注</dt><dd>{{ row.remark || '—' }}</dd></div>
              <div><dt>操作人</dt><dd>{{ row.operator_name || '系统' }}</dd></div>
              <div><dt>时间</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
            </dl>
          </article>
        </div>
      </template>

      <el-table-column prop="tx_no" label="流水号" min-width="200" />
      <el-table-column prop="username" label="账号" width="130" />
      <el-table-column label="类型" width="150">
        <template #default="{ row }">
          <el-tag :type="TX_TYPE[row.tx_type]?.type" effect="light" round>{{ row.tx_type_display }}</el-tag>
          <el-tooltip v-if="isIncomeHold(row)" content="冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。" :trigger="['hover', 'focus', 'click']"><button type="button" class="tx-help" aria-label="冻结流水说明">?</button></el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="冲正来源" min-width="140">
        <template #default="{ row }">
          <template v-if="row.is_reversal || row.reversal_of"><el-tag size="small" type="warning">冲正</el-tag> <el-button v-if="row.reversal_of" link type="primary" @click="openOriginal(row.reversal_of)">原流水 #{{ row.reversal_of }}</el-button><span v-else>原流水待核对</span></template>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="流水金额" width="120">
        <template #default="{ row }">
          <span class="tx-amount" :class="row.amount >= 0 ? 'tx-amount-up' : 'tx-amount-down'">
            {{ row.amount >= 0 ? '+' : '' }}{{ amountToXaCoin(row.amount) }} 币
          </span>
        </template>
      </el-table-column>
      <el-table-column label="变动后余额" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.balance_after) }} 币</template>
      </el-table-column>
      <el-table-column label="关联订单" min-width="225">
        <template #default="{ row }"><router-link v-if="row.order_no && auth.hasPerm('order:view')" :to="{ name: 'orders', query: { find_order: row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no || '—' }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="TX_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="160" />
      <el-table-column label="操作人" width="110">
        <template #default="{ row }">{{ row.operator_name || '系统' }}</template>
      </el-table-column>
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
    </crud-table>
  </div>
  <el-drawer v-model="originalVisible" title="原流水" size="480px" class="ap-panel">
    <p class="tx-original-intro">原流水 #{{ originalId }} · 以下为读取时的记录</p>
    <el-alert v-if="originalFailed" title="原流水读取失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="loadOriginal">重新读取</el-button>
    </el-alert>
    <p v-else-if="originalLoading" role="status">正在读取原流水…</p>
    <el-descriptions v-else-if="original" :column="1" border>
      <el-descriptions-item label="流水号">{{ original.tx_no || '待核对' }}</el-descriptions-item>
      <el-descriptions-item label="账号">{{ original.username || '待核对' }}</el-descriptions-item>
      <el-descriptions-item label="关联订单"><router-link v-if="original.order_no && auth.hasPerm('order:view')" :to="{ name: 'orders', query: { find_order: original.order_no } }">{{ original.order_no }}</router-link><span v-else>{{ original.order_no || '—' }}</span></el-descriptions-item>
      <el-descriptions-item label="类型">{{ original.tx_type_display || '待核对' }}</el-descriptions-item>
      <el-descriptions-item label="状态">{{ original.status_display || '待核对' }}</el-descriptions-item>
      <el-descriptions-item label="流水金额">{{ original.amount >= 0 ? '+' : '' }}{{ amountToXaCoin(original.amount) }} 币</el-descriptions-item>
      <el-descriptions-item label="变动前余额">{{ amountToXaCoin(original.balance_before) }} 币</el-descriptions-item>
      <el-descriptions-item label="变动后余额">{{ amountToXaCoin(original.balance_after) }} 币</el-descriptions-item>
      <el-descriptions-item label="备注">{{ original.remark || '—' }}</el-descriptions-item>
      <el-descriptions-item label="时间">{{ formatDateTime(original.created_at) }}</el-descriptions-item>
    </el-descriptions>
    <p v-if="original && isIncomeHold(original)" class="tx-hold-note">冻结将余额转入冻结资金，解冻则转回；冲正请结合关联流水核对。</p>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import CrudTable from '@/components/CrudTable.vue'
import { transactionApi } from '@/api/modules'
import { TX_TYPE, TX_STATUS } from '@/utils/dict'
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

<style scoped lang="scss">
.tx-mobile-list { display: grid; gap: 12px; }
.tx-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.tx-card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tx-amount-label { margin-top: 8px; color: var(--muted-foreground); font-size: 12px; }
.tx-card-amount { margin-top: 2px; font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.tx-hold-note { margin: 6px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
.tx-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.tx-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.tx-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin: 12px 0 0; padding-top: 12px; border-top: 1px solid var(--border); }
.tx-facts div { min-width: 0; }
.tx-facts dt { color: var(--muted-foreground); font-size: 12px; }
.tx-facts dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.tx-wide { grid-column: 1 / -1; }
.tx-original-intro { margin: 0 0 16px; color: var(--muted-foreground); }
.tx-page :deep(.el-button.is-link) { white-space: normal; text-align: left; }
.tx-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.tx-page :deep(.page-container) {
  padding: 0;
}
.tx-amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tx-amount-up {
  color: var(--success);
}
.tx-amount-down {
  color: var(--destructive);
}
</style>
