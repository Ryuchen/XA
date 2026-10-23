<template>
  <div class="wd-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">核对收款信息并完成人工打款后，填写打款凭证编号确认审核；1 元 = 10 兴安币。</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('withdraw:audit')" type="warning" plain @click="openConfig">
          最低提现：{{ amountToXaCoin(minAmount) }} 兴安币 · 税率 {{ taxRate }}%
        </el-button>
      </div>
    </div>

    <crud-table v-if="canReadWithdrawals" ref="tableRef" :fetcher="withdrawApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="陪玩昵称"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_name"
          :fetch-suggestions="querySuggest('payee_name')"
          placeholder="收款人"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_account"
          :fetch-suggestions="querySuggest('payee_account')"
          placeholder="收款账号"
          clearable
          value-key="value"
          style="width: 160px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in WITHDRAW_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="user_name" label="陪玩" min-width="110" />
      <el-table-column label="提现金额" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 兴安币</template>
      </el-table-column>
      <el-table-column label="应打款（元）" width="125">
        <template #default="{ row }">¥{{ formatRmb(row.actual_amount) }}</template>
      </el-table-column>
      <el-table-column label="收款方式" width="100">
        <template #default="{ row }">
          <el-tag :type="PAYEE_METHOD[row.payee_method]?.type" effect="light" round>{{ row.payee_method_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="payee_name" label="收款人" width="100" />
      <el-table-column prop="payee_account" label="收款账号" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="WITHDRAW_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'">
            <el-button link type="success" :disabled="auditBusy" @click="onApprove(row)">通过</el-button>
            <el-button link type="danger" :disabled="auditBusy" @click="onReject(row)">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="提现详情" size="480px" class="ap-panel">
    <el-skeleton v-if="detailLoading" :rows="6" animated />
    <el-alert v-else-if="detailFailed" title="提现详情加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refreshCurrentWithdrawal">重新加载</el-button>
    </el-alert>
    <template v-else-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="陪玩">{{ current.user_name }}</el-descriptions-item>
        <el-descriptions-item label="提现金额">{{ amountToXaCoin(current.amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="税率">{{ current.tax_rate }}%</el-descriptions-item>
        <el-descriptions-item label="税额">{{ amountToXaCoin(current.tax_amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="实际到账">{{ amountToXaCoin(current.actual_amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="应打款人民币">¥{{ formatRmb(current.actual_amount) }}</el-descriptions-item>
        <el-descriptions-item label="收款方式">{{ current.payee_method_display }}</el-descriptions-item>
        <el-descriptions-item label="收款人">{{ current.payee_name }}</el-descriptions-item>
        <el-descriptions-item label="收款账号">{{ current.payee_account }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="打款流水号">
          {{ current.payout_reference || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="实际打款时间">
          {{ current.paid_at ? formatDateTime(current.paid_at) : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="configVisible" title="提现配置" width="440px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="最低提现(兴安币)">
        <el-input-number v-model="configInput" :min="0" :step="10" :precision="1" style="width: 100%" />
        <div class="tip">陪玩单次提现金额不得低于该值</div>
      </el-form-item>
      <el-form-item label="提现税率(%)">
        <el-input-number v-model="taxInput" :min="0" :max="100" :step="1" :precision="0" style="width: 100%" />
        <div class="tip">按提现金额扣税，陪玩实际到账 = 提现金额 ×(1 - 税率)</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="configVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingConfig" @click="saveConfig">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { withdrawApi, withdrawConfigApi } from '@/api/modules'
import { WITHDRAW_STATUS, PAYEE_METHOD } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { formatRmb } from '@xa/money'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const canReadWithdrawals = computed(() => Boolean(auth.token) && auth.hasPerm('withdraw:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({
  provider_username: '',
  provider_nickname: '',
  payee_name: '',
  payee_account: '',
  status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询提现单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'provider_username' | 'provider_nickname' | 'payee_name' | 'payee_account') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    const revision = auth.sessionRevision
    if (!keyword || !canReadWithdrawals.value || pageDisposed) {
      cb([])
      return
    }
    try {
      const res = (await withdrawApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || !canReadWithdrawals.value || pageDisposed) { cb([]); return }
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const w of res.data?.list || []) {
        const value = w[field] || ''
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      cb(items)
    } catch {
      cb([])
    }
  }
}

const detailVisible = ref(false)
const current = ref<any>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)
const auditBusy = ref(false)
let detailSequence = 0
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offWithdrawalUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null
let auditSequence = 0
let pendingPromptId: number | null = null
const withdrawalVersions = new Map<number, number>()
const withdrawalStatuses = new Map<number, string>()

function clearWithdrawalDetail() {
  ++detailSequence
  detailRefreshPending = false
  current.value = null
  detailLoading.value = detailFailed.value = false
}

watch(detailVisible, visible => { if (!visible) clearWithdrawalDetail() }, { flush: 'sync' })

function isCurrentDetail(sequence: number, revision: number, withdrawalId: number | string) {
  return !pageDisposed && canReadWithdrawals.value && auth.sessionRevision === revision
    && sequence === detailSequence && detailVisible.value && current.value?.id === withdrawalId
}

async function refreshCurrentWithdrawal() {
  const withdrawalId = current.value?.id
  if (!detailVisible.value || !withdrawalId || !canReadWithdrawals.value || pageDisposed) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  detailLoading.value = true
  detailFailed.value = false
  try {
    const res = await withdrawApi.retrieve(withdrawalId)
    if (isCurrentDetail(sequence, revision, withdrawalId)) current.value = res.data
  } catch {
    if (isCurrentDetail(sequence, revision, withdrawalId)) detailFailed.value = true
  } finally {
    if (isCurrentDetail(sequence, revision, withdrawalId)) detailLoading.value = false
  }
}

// Pushes invalidate HTTP data without resetting the current filters or page.
function requestWithdrawalRefresh(withdrawalId?: number | string) {
  if (pageDisposed || !canReadWithdrawals.value) return
  refreshPending = true
  if (detailVisible.value && current.value && (withdrawalId == null || current.value.id === withdrawalId)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleWithdrawalRefresh()
}

function scheduleWithdrawalRefresh() {
  if (pageDisposed || !canReadWithdrawals.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadWithdrawals.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([tableRef.value?.load(), ...(refreshDetail ? [refreshCurrentWithdrawal()] : [])])
    } finally {
      if (generation === realtimeGeneration) { refreshRunning = false; scheduleWithdrawalRefresh() }
    }
  }, 300)
}

function invalidatePendingAudit() {
  ++auditSequence
  auditBusy.value = false
  if (pendingPromptId !== null) ElMessageBox.close()
  pendingPromptId = null
}

function onWithdrawalStatusUpdate(data: any) {
  if (!Number.isSafeInteger(data?.withdraw_id) || data.withdraw_id <= 0
    || !Number.isSafeInteger(data?.account_id) || data.account_id <= 0
    || !['PENDING', 'APPROVED', 'REJECTED'].includes(data?.status)) return
  withdrawalVersions.set(data.withdraw_id, (withdrawalVersions.get(data.withdraw_id) || 0) + 1)
  withdrawalStatuses.set(data.withdraw_id, data.status)
  if (pendingPromptId === data.withdraw_id) invalidatePendingAudit()
  requestWithdrawalRefresh(data.withdraw_id)
}

function stopWithdrawalRealtime() {
  ++realtimeGeneration
  offWithdrawalUpdate?.()
  offConnected?.()
  offWithdrawalUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
  withdrawalVersions.clear()
  withdrawalStatuses.clear()
  invalidatePendingAudit()
}

function startWithdrawalRealtime() {
  if (pageDisposed || !canReadWithdrawals.value) return
  offWithdrawalUpdate = adminWs.on('withdraw_status_update', onWithdrawalStatusUpdate)
  offConnected = adminWs.on('connected', () => { invalidatePendingAudit(); requestWithdrawalRefresh() })
  adminWs.connect()
}

watch(() => [auth.sessionRevision, canReadWithdrawals.value, auth.hasPerm('withdraw:audit')], () => {
  stopWithdrawalRealtime()
  detailVisible.value = configVisible.value = false
  clearWithdrawalDetail()
  if (pageMounted) startWithdrawalRealtime()
}, { flush: 'sync' })

onMounted(() => { pageMounted = true; startWithdrawalRealtime() })
onBeforeUnmount(() => { pageDisposed = true; stopWithdrawalRealtime(); clearWithdrawalDetail() })

const minAmount = ref<number>(0)
const taxRate = ref<number>(0)
const configVisible = ref(false)
const configInput = ref<number>(0)
const taxInput = ref<number>(0)
const savingConfig = ref(false)

async function loadConfig() {
  const revision = auth.sessionRevision
  if (!canReadWithdrawals.value || pageDisposed) return
  const res = await withdrawConfigApi.get()
  if (revision !== auth.sessionRevision || !canReadWithdrawals.value || pageDisposed) return
  minAmount.value = res.data.min_amount
  taxRate.value = res.data.tax_rate
}

onMounted(loadConfig)

function onDetail(row: any) {
  if (!canReadWithdrawals.value || pageDisposed) return
  current.value = row
  detailVisible.value = true
  void refreshCurrentWithdrawal()
}

async function onApprove(row: any) {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || auditBusy.value || row.status !== 'PENDING'
    || (withdrawalStatuses.has(row.id) && withdrawalStatuses.get(row.id) !== 'PENDING')) return
  const revision = auth.sessionRevision
  const sequence = ++auditSequence
  const version = withdrawalVersions.get(row.id) || 0
  const currentAudit = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence && version === (withdrawalVersions.get(row.id) || 0)
  auditBusy.value = true
  pendingPromptId = row.id
  try {
    const confirmation = await ElMessageBox.prompt(
      `请先向「${row.user_name}」人工打款 ¥${formatRmb(row.actual_amount)}（${amountToXaCoin(row.actual_amount)} 兴安币，已扣除 ${amountToXaCoin(row.tax_amount)} 兴安币税额），再填写渠道流水号或转账凭证编号。收款人：${row.payee_name}；${row.payee_method_display}：${row.payee_account}。`,
      '提现审核',
      {
        type: 'warning', confirmButtonText: '确认已打款',
        inputPlaceholder: '打款流水号/凭证编号（必填）',
        inputValidator: (v) => (v && v.trim() ? true : '请填写打款流水号/凭证编号'),
      },
    ).catch(() => null)
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null
    await withdrawApi.action(row.id, 'approve', { payout_reference: confirmation.value.trim() })
    if (!currentAudit()) return
    ElMessage.success('已通过')
    requestWithdrawalRefresh(row.id)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null }
  }
}

async function onReject(row: any) {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || auditBusy.value || row.status !== 'PENDING'
    || (withdrawalStatuses.has(row.id) && withdrawalStatuses.get(row.id) !== 'PENDING')) return
  const revision = auth.sessionRevision
  const sequence = ++auditSequence
  const version = withdrawalVersions.get(row.id) || 0
  const currentAudit = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence && version === (withdrawalVersions.get(row.id) || 0)
  auditBusy.value = true
  pendingPromptId = row.id
  try {
    const confirmation = await ElMessageBox.prompt('请填写驳回原因（驳回后冻结金额将退还陪玩余额）', '驳回提现', {
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
    }).catch(() => null)
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null
    await withdrawApi.action(row.id, 'reject', { audit_remark: confirmation.value })
    if (!currentAudit()) return
    ElMessage.success('已驳回并退还')
    requestWithdrawalRefresh(row.id)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null }
  }
}

function openConfig() {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed) return
  configInput.value = Number(amountToXaCoin(minAmount.value))
  taxInput.value = taxRate.value
  configVisible.value = true
}

async function saveConfig() {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || savingConfig.value) return
  const revision = auth.sessionRevision
  savingConfig.value = true
  try {
    const res = await withdrawConfigApi.update(xaCoinToAmount(configInput.value), taxInput.value)
    if (revision !== auth.sessionRevision || !canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed) return
    minAmount.value = res.data.min_amount
    taxRate.value = res.data.tax_rate
    ElMessage.success('提现配置已更新')
    configVisible.value = false
  } finally {
    savingConfig.value = false
  }
}
</script>

<style scoped lang="scss">
.wd-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 wd-page 的间距节奏 */
.wd-page :deep(.page-container) {
  padding: 0;
}

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
