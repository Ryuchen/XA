<template>
  <div class="rp-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">核验三类服务凭证；全体报单通过、售后窗口结束且无未结客诉后结算</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('report:audit')" type="primary" plain @click="openCommission">
          订单默认抽成：{{ commissionLabel(commissionConfig) }}
        </el-button>
      </div>
    </div>

    <crud-table v-if="canReadReports" :key="auth.sessionRevision" ref="tableRef" :fetcher="reportApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.game_name"
          :fetch-suggestions="querySuggest('game_name')"
          placeholder="游戏/项目"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 150px"
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
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in REPORT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
    <el-table-column prop="provider_name" label="陪玩" min-width="110" />
    <el-table-column prop="fulfillment" label="玩法 / 转盘目标" min-width="220" />
    <el-table-column prop="game_name" label="游戏/项目" min-width="140" />
    <el-table-column label="报单金额" width="110">
      <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
    </el-table-column>
    <el-table-column label="抽成来源" width="170">
      <template #default="{ row }">
        <span>{{ reportCommissionDesc(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="核定收益" width="180">
      <template #default="{ row }">
        <span v-if="row.status === 'APPROVED'">{{ amountToXaCoin(row.payout_amount) }} 币（{{ row.order && !row.settled_at ? '待整单结算' : '已入账' }}）</span>
        <span v-else>预计 {{ amountToXaCoin(reportEstimatedPayout(row)) }} 币</span>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="REPORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="提交时间" width="160">
      <template #default="{ row }">{{ row.submitted_at ? formatDateTime(row.submitted_at) : '—' }}</template>
    </el-table-column>
    <el-table-column label="操作" width="200" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        <template v-if="auth.hasPerm('report:audit') && row.status === 'PENDING'">
          <el-button link type="success" @click="onApprove(row)">通过</el-button>
          <el-button link type="danger" @click="onReject(row)">驳回</el-button>
        </template>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="报单详情" size="480px" class="ap-panel">
    <p v-if="detailLoading" role="status">正在同步报单详情…</p>
    <el-alert v-if="detailFailed" title="报单详情更新失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refreshCurrentReport">重新加载</el-button>
    </el-alert>
    <template v-if="current && !detailFailed">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="陪玩">{{ current.provider_name }}</el-descriptions-item>
        <el-descriptions-item label="游戏/项目">{{ current.game_name }}</el-descriptions-item>
        <el-descriptions-item v-if="current.order_no" label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="报单金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="抽成来源">{{ reportCommissionDesc(current) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'PENDING'" label="预计入账">
          {{ amountToXaCoin(reportEstimatedPayout(current)) }} 币
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="核定收益">
          {{ amountToXaCoin(current.payout_amount) }} 币（{{ current.order && !current.settled_at ? '待整单结算' : '已入账' }}）
        </el-descriptions-item>
        <el-descriptions-item label="服务内容">{{ current.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ current.submitted_at ? formatDateTime(current.submitted_at) : '—' }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>订单凭证</el-divider>
      <template v-if="current.order">
        <div class="evidence-section">
          <div class="evidence-title">入队截图</div>
          <PrivateImage v-if="current.entry_image_url" :src="current.entry_image_url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">结单截图</div>
          <PrivateImage v-if="current.completion_image_url" :src="current.completion_image_url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">战绩截图（{{ current.result_image_urls?.length || 0 }} 张）</div>
          <div v-if="current.result_image_urls?.length" class="evidence-grid">
            <PrivateImage v-for="url in current.result_image_urls" :key="url" :src="url" :preview-src-list="allEvidenceUrls(current)" fit="cover" class="evidence-image" />
          </div>
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
      </template>
      <template v-else>
        <PrivateImage v-if="current.proof_image_url" :src="current.proof_image_url" :preview-src-list="[current.proof_image_url]" fit="contain" style="width: 100%; max-height: 360px" />
        <el-empty v-else description="无凭证" :image-size="80" />
      </template>
    </template>
  </el-drawer>

  <el-dialog v-model="commissionVisible" title="订单默认抽成配置" width="600px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <CommissionEditor v-model:type="commissionType" v-model:rate="commissionInput" v-model:fixed="commissionFixed" />
      <p class="tip ap-field--full">仅设置后续普通订单的默认抽成。商品自定义抽成及订单档位、体验、陪玩自动规则优先；不影响礼物。已有订单按分账快照结算。</p>
      <p v-if="commissionType === 'FIXED'" class="tip ap-field--full">旧手工报单仍沿用默认比例 {{ commissionRate }}%，不改为固定金额。</p>
    </el-form>
    <template #footer>
      <el-button @click="commissionVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingRate" @click="saveCommission">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import PrivateImage from "@/components/PrivateImage.vue"
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { commissionError, commissionLabel, commissionPayload, readCommission, type CommissionConfig, type CommissionType } from '@/utils/commission'
import { reportApi, commissionApi } from '@/api/modules'
import { REPORT_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const canReadReports = computed(() => Boolean(auth.token) && auth.hasPerm('report:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({
  game_name: '',
  provider_username: '',
  provider_nickname: '',
  status: '',
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询报单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'game_name' | 'provider_username' | 'provider_nickname') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await reportApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const r of res.data?.list || []) {
        const value = r[field] || ''
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
let detailSequence = 0
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offReportUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null

function clearReportDetail() {
  ++detailSequence
  detailRefreshPending = false
  current.value = null
  detailLoading.value = false
  detailFailed.value = false
}

watch(detailVisible, visible => {
  if (!visible) clearReportDetail()
}, { flush: 'sync' })

function isCurrentDetail(sequence: number, revision: number, reportId: number | string) {
  return !pageDisposed && canReadReports.value && auth.sessionRevision === revision
    && sequence === detailSequence && detailVisible.value && current.value?.id === reportId
}

async function refreshCurrentReport() {
  const reportId = current.value?.id
  if (!detailVisible.value || !reportId || !canReadReports.value || pageDisposed) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  detailLoading.value = true
  detailFailed.value = false
  try {
    const res = await reportApi.retrieve(reportId)
    if (isCurrentDetail(sequence, revision, reportId)) current.value = res.data
  } catch {
    if (isCurrentDetail(sequence, revision, reportId)) detailFailed.value = true
  } finally {
    if (isCurrentDetail(sequence, revision, reportId)) detailLoading.value = false
  }
}

// Keep CrudTable's current filters and pagination; pushes only invalidate HTTP data.
function requestReportRefresh(reportId?: number | string) {
  if (pageDisposed || !canReadReports.value) return
  refreshPending = true
  if (detailVisible.value && current.value && (reportId == null || current.value.id === reportId)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleReportRefresh()
}

function scheduleReportRefresh() {
  if (pageDisposed || !canReadReports.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadReports.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([
        tableRef.value?.load(),
        ...(refreshDetail ? [refreshCurrentReport()] : []),
      ])
    } finally {
      if (generation === realtimeGeneration) {
        refreshRunning = false
        scheduleReportRefresh()
      }
    }
  }, 300)
}

function onReportStatusUpdate(data: any) {
  if (!Number.isSafeInteger(data?.report_id) || data.report_id <= 0) return
  requestReportRefresh(data.report_id)
}

function stopReportRealtime() {
  ++realtimeGeneration
  offReportUpdate?.()
  offConnected?.()
  offReportUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
}

function startReportRealtime() {
  if (pageDisposed || !canReadReports.value) return
  offReportUpdate = adminWs.on('report_status_update', onReportStatusUpdate)
  offConnected = adminWs.on('connected', () => requestReportRefresh())
  adminWs.connect()
}

watch(() => [auth.sessionRevision, canReadReports.value], () => {
  stopReportRealtime()
  detailVisible.value = false
  clearReportDetail()
  if (pageMounted) startReportRealtime()
}, { flush: 'sync' })

onMounted(() => {
  pageMounted = true
  startReportRealtime()
})

onBeforeUnmount(() => {
  pageDisposed = true
  stopReportRealtime()
  clearReportDetail()
})

const commissionRate = ref<number>(0)
const commissionConfig = ref<CommissionConfig>({ rate: 0 })
const commissionVisible = ref(false)
const commissionInput = ref<number | undefined>(0)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const savingRate = ref(false)

async function loadCommission() {
  const res = await commissionApi.get()
  commissionRate.value = res.data.rate
  commissionConfig.value = res.data
}

onMounted(loadCommission)

function onDetail(row: any) {
  if (!canReadReports.value || pageDisposed) return
  current.value = row
  detailVisible.value = true
  void refreshCurrentReport()
}

function reportRate(row: any): number {
  if (row.status === 'APPROVED') return Number(row.commission_rate) || 0
  return Number(row.suggested_commission_rate) || 0
}

function reportCommissionDesc(row: any): string {
  const label = row.commission_source_label || '全局默认'
  if (row.commission_type === 'FIXED') {
    return `固定抽取 ${amountToXaCoin(row.commission_fixed || 0)} 兴安币 · ${label}`
  }
  return `${reportRate(row)}% · ${label}`
}

function reportEstimatedPayout(row: any): number {
  if (row.expected_income != null) return Number(row.expected_income)
  return Math.floor((Number(row.amount) || 0) * (100 - reportRate(row)) / 100)
}

function allEvidenceUrls(row: any): string[] {
  return [row.entry_image_url, row.completion_image_url, ...(row.result_image_urls || [])].filter(Boolean)
}

async function onApprove(row: any) {
  const revision = auth.sessionRevision
  const isOrderReport = Boolean(row.order)
  try {
    await ElMessageBox.confirm(
      isOrderReport
        ? `确认「${row.provider_name}」的入队、结单和战绩截图完整有效？${row.settled_at || row.settlement_mode === 'COMPLETION' ? '历史订单已结算，本次仅核验凭证。' : '全体报单通过、售后窗口结束且无未结客诉后，系统按订单分账快照结算。'}`
        : `通过后将按 ${reportCommissionDesc(row)} 给「${row.provider_name}」入账，预计 ${amountToXaCoin(reportEstimatedPayout(row))} 兴安币，确认通过？`,
      '报单审核',
      { type: 'warning', confirmButtonText: '确认通过' },
    )
  } catch {
    return
  }
  if (revision !== auth.sessionRevision || pageDisposed || !canReadReports.value || !auth.hasPerm('report:audit')) return
  await reportApi.action(row.id, 'approve')
  if (revision !== auth.sessionRevision || !canReadReports.value || pageDisposed) return
  ElMessage.success(isOrderReport ? '凭证审核已通过' : '已通过并入账')
  requestReportRefresh(row.id)
}

async function onReject(row: any) {
  const revision = auth.sessionRevision
  let audit_remark = ''
  try {
    const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回报单', {
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
    })
    audit_remark = value
  } catch {
    return
  }
  if (revision !== auth.sessionRevision || pageDisposed || !canReadReports.value || !auth.hasPerm('report:audit')) return
  await reportApi.action(row.id, 'reject', { audit_remark })
  if (revision !== auth.sessionRevision || !canReadReports.value || pageDisposed) return
  ElMessage.success('已驳回')
  requestReportRefresh(row.id)
}

function openCommission() {
  const commission = readCommission(commissionConfig.value)
  commissionInput.value = commissionRate.value
  commissionType.value = commission.type
  commissionFixed.value = commission.fixed
  commissionVisible.value = true
}

async function saveCommission() {
  if (savingRate.value) return
  const error = commissionError(commissionType.value, commissionInput.value, commissionFixed.value)
  if (error) { ElMessage.warning(error); return }
  savingRate.value = true
  try {
    const payload = commissionPayload(commissionType.value, commissionInput.value, commissionFixed.value)
    const res = await commissionApi.update({ commission_type: payload.commission_type, commission_fixed: payload.commission_fixed, rate: commissionInput.value ?? commissionRate.value })
    commissionRate.value = res.data.rate
    commissionConfig.value = res.data
    ElMessage.success('订单默认抽成已更新')
    commissionVisible.value = false
  } finally {
    savingRate.value = false
  }
}
</script>

<style scoped lang="scss">
.rp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 rp-page 的间距节奏 */
.rp-page :deep(.page-container) {
  padding: 0;
}

.evidence-section { margin-bottom: 20px; }
.evidence-title { margin-bottom: 10px; color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.evidence-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.evidence-image { width: 100%; height: 120px; border-radius: 10px; }

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
