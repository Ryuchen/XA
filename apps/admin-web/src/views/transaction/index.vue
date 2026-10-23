<template>
  <div class="tx-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">记录平台各类资金变动流水，支持按类型与关键词筛选</p>
    </div>

    <crud-table v-if="canReadTransactions" ref="tableRef" :fetcher="transactionApi.list" :query="query">
      <template #toolbar="{ reload }">
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

      <el-table-column prop="tx_no" label="流水号" min-width="200" />
      <el-table-column prop="username" label="账号" width="130" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag :type="TX_TYPE[row.tx_type]?.type" effect="light" round>{{ row.tx_type_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="冲正来源" min-width="140">
        <template #default="{ row }">
          <template v-if="row.is_reversal || row.reversal_of"><el-tag size="small" type="warning">冲正</el-tag> 原流水 #{{ row.reversal_of }}</template>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="120">
        <template #default="{ row }">
          <span class="tx-amount" :class="row.amount >= 0 ? 'tx-amount-up' : 'tx-amount-down'">
            {{ row.amount >= 0 ? '+' : '' }}{{ amountToXaCoin(row.amount) }} 币
          </span>
        </template>
      </el-table-column>
      <el-table-column label="变更后" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.balance_after) }} 币</template>
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
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import CrudTable from '@/components/CrudTable.vue'
import { transactionApi } from '@/api/modules'
import { TX_TYPE, TX_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const canReadTransactions = computed(() => Boolean(auth.token) && auth.hasPerm('transaction:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', tx_type: '' })
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let offWalletUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null

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
  stopTransactionRealtime()
  if (pageMounted) startTransactionRealtime()
}, { flush: 'sync' })
onMounted(() => { pageMounted = true; startTransactionRealtime() })
onBeforeUnmount(() => { pageDisposed = true; stopTransactionRealtime() })
</script>

<style scoped lang="scss">
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
